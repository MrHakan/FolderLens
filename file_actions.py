"""Safety helpers for actions based on a scanned FolderLens tree.

The scan tree is a point-in-time view. Before copying or removing selected
items, compare its names and cheap metadata with the live filesystem so a
stale tree cannot silently broaden or misstate an action.
"""

from __future__ import annotations

import os
import shutil
import stat
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional


class ActionCancelled(Exception):
    """Raised when a user stops a long-running file action."""


@dataclass
class ValidationResult:
    valid: bool
    items: int = 0
    files: int = 0
    logical_bytes: int = 0
    reparse_paths: list[str] = field(default_factory=list)
    issues: list[str] = field(default_factory=list)


@dataclass
class ZipResult:
    path: str
    files_written: int
    errors: list[str] = field(default_factory=list)
    cancelled: bool = False


def _mtime_ns(value) -> int:
    raw = getattr(value, "st_mtime_ns", None)
    return int(raw if raw is not None else getattr(value, "st_mtime", 0.0) * 1_000_000_000)


def _is_reparse(value) -> bool:
    flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    return stat.S_ISLNK(value.st_mode) or bool(getattr(value, "st_file_attributes", 0) & flag)


def validate_selection(nodes: Iterable, *, cancel_event=None,
                       on_progress: Optional[Callable[[int, int], None]] = None,
                       reject_reparse: bool = False) -> ValidationResult:
    """Compare selected scanned subtrees with current names and metadata.

    The check never follows symlinks or Windows reparse points. It compares
    file size and nanosecond mtime, directory mtime and exact child names, and
    a captured file identity when the scan opted into extended metadata.
    """
    result = ValidationResult(valid=True)
    selected = list(nodes)
    stack = [(node, True) for node in selected]
    checked = 0
    total = sum(1 + max(0, int(getattr(node, "item_count", 0))) for node in selected)

    def problem(message: str):
        result.valid = False
        if len(result.issues) < 10:
            result.issues.append(message)

    def check_node(node, current, count=True):
        nonlocal checked
        path = node.path
        current_is_dir = stat.S_ISDIR(current.st_mode)
        current_reparse = _is_reparse(current)
        if current_is_dir != bool(node.is_dir):
            problem(f"Type changed: {path}")
        if bool(getattr(node, "modified_date", 0)) and _mtime_ns(current) != int(node.modified_date):
            problem(f"Modified since scan: {path}")
        if not current_is_dir and int(current.st_size) != int(node.size):
            problem(f"Size changed: {path}")
        if current_reparse != bool(getattr(node, "is_reparse_point", False)):
            problem(f"Reparse status changed: {path}")
        if current_reparse:
            result.reparse_paths.append(path)

        identity = getattr(node, "file_identity", None)
        if identity is not None:
            live_identity = (getattr(current, "st_dev", None), getattr(current, "st_ino", None))
            if tuple(identity) != live_identity:
                problem(f"File identity changed: {path}")

        if count:
            checked += 1
            result.items += 1
            if not current_is_dir:
                result.files += 1
                result.logical_bytes += max(0, int(current.st_size))
            if on_progress is not None and (checked % 500 == 0 or checked == total):
                on_progress(checked, total)
        return current_is_dir, current_reparse

    while stack:
        if cancel_event is not None and cancel_event.is_set():
            raise ActionCancelled()
        node, count = stack.pop()
        path = node.path
        try:
            current = os.stat(path, follow_symlinks=False)
        except OSError as exc:
            problem(f"Cannot verify {path}: {exc}")
            continue
        current_is_dir, current_reparse = check_node(node, current, count)
        if current_reparse:
            continue
        if not current_is_dir:
            continue

        expected = {child.name: child for child in node.children}
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if cancel_event is not None and cancel_event.is_set():
                        raise ActionCancelled()
                    child = expected.pop(entry.name, None)
                    if child is None:
                        problem(f"Added since scan: {entry.path}")
                        continue
                    try:
                        child_stat = entry.stat(follow_symlinks=False)
                    except OSError as exc:
                        problem(f"Cannot verify {entry.path}: {exc}")
                        continue
                    child_is_dir, child_reparse = check_node(child, child_stat)
                    if child_is_dir and not child_reparse:
                        stack.append((child, False))
        except OSError as exc:
            problem(f"Cannot list {path}: {exc}")
            continue
        if expected:
            missing = sorted(expected)
            problem(f"Missing since scan: {os.path.join(path, missing[0])}")

    if on_progress is not None and checked and checked % 500:
        on_progress(checked, total)
    if reject_reparse and result.reparse_paths:
        result.valid = False
        if len(result.issues) < 10:
            result.issues.append(
                f"Selection contains {len(result.reparse_paths)} reparse point(s); delete was stopped for safety.")
    return result


def _matches_snapshot(node, current) -> bool:
    if bool(node.is_dir) != stat.S_ISDIR(current.st_mode):
        return False
    if bool(getattr(node, "is_reparse_point", False)) != _is_reparse(current):
        return False
    if bool(node.modified_date) and _mtime_ns(current) != int(node.modified_date):
        return False
    if not node.is_dir and int(node.size) != int(current.st_size):
        return False
    identity = getattr(node, "file_identity", None)
    if identity is not None and tuple(identity) != (
            getattr(current, "st_dev", None), getattr(current, "st_ino", None)):
        return False
    return True


def _iter_scanned_files(node, errors: list[str], reparse_paths: list[str], cancel_event):
    """Yield files from the captured tree, never broadening to new disk entries."""
    stack = [node]
    while stack:
        if cancel_event is not None and cancel_event.is_set():
            raise ActionCancelled()
        current_node = stack.pop()
        try:
            current = os.stat(current_node.path, follow_symlinks=False)
        except OSError as exc:
            errors.append(f"{current_node.path}: {exc}")
            continue
        if not _matches_snapshot(current_node, current):
            raise ValueError(f"Changed while creating ZIP: {current_node.path}")
        if _is_reparse(current):
            reparse_paths.append(current_node.path)
        elif stat.S_ISDIR(current.st_mode):
            stack.extend(reversed(current_node.children))
        else:
            yield current_node


def _archive_names(nodes: list) -> dict[str, str]:
    bases = [os.path.dirname(node.path) for node in nodes]
    try:
        common = os.path.commonpath(bases) if bases else os.curdir
    except ValueError:
        common = ""
    result = {}
    used = set()
    for index, node in enumerate(nodes):
        if common:
            prefix = os.path.relpath(node.path, common)
        else:
            prefix = os.path.join(f"Selection {index + 1}", os.path.basename(node.path))
        prefix = os.path.normpath(prefix)
        result[node.path] = prefix
        if prefix.lower() in used:
            stem, ext = os.path.splitext(prefix)
            suffix = 2
            candidate = f"{stem} ({suffix}){ext}"
            while candidate.lower() in used:
                suffix += 1
                candidate = f"{stem} ({suffix}){ext}"
            result[node.path] = candidate
            prefix = candidate
        used.add(prefix.lower())
    return result


def create_zip(nodes: Iterable, destination: str, *, cancel_event=None,
               on_progress: Optional[Callable[[int, int], None]] = None,
               overwrite: bool = False) -> ZipResult:
    """Validate and create a ZIP, preserving the old destination on cancel.

    Reparse points are omitted and listed as incomplete entries. Any metadata
    drift found before writing aborts the operation without creating a ZIP.
    """
    selected = list(nodes)
    try:
        check = validate_selection(selected, cancel_event=cancel_event)
    except ActionCancelled:
        return ZipResult(os.path.abspath(destination), 0, cancelled=True)
    if not check.valid:
        raise ValueError("The selection changed since the scan. Rescan it before creating a ZIP.\n"
                         + "\n".join(check.issues[:5]))
    errors: list[str] = []
    reparses: list[str] = []
    written = 0
    destination = os.path.abspath(destination)
    if os.path.exists(destination) and not overwrite:
        raise FileExistsError(f"The destination already exists: {destination}")
    parent = os.path.dirname(destination) or os.curdir
    fd, temporary = tempfile.mkstemp(prefix=".folderlens-", suffix=".zip.tmp", dir=parent)
    os.close(fd)
    names = _archive_names(selected)
    total_files = check.files
    try:
        with zipfile.ZipFile(temporary, "w", zipfile.ZIP_DEFLATED) as archive:
            for node in selected:
                if cancel_event is not None and cancel_event.is_set():
                    raise ActionCancelled()
                root_name = names.get(node.path, os.path.basename(node.path))
                for source_node in _iter_scanned_files(node, errors, reparses, cancel_event):
                    if cancel_event is not None and cancel_event.is_set():
                        raise ActionCancelled()
                    source = source_node.path
                    if source == node.path:
                        arcname = root_name
                    else:
                        relative = os.path.relpath(source, node.path)
                        arcname = os.path.join(root_name, relative)
                    try:
                        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
                        descriptor = os.open(source, flags)
                        with os.fdopen(descriptor, "rb") as source_stream:
                            before = os.fstat(source_stream.fileno())
                            path_stat = os.stat(source, follow_symlinks=False)
                            if (_is_reparse(path_stat) or not _matches_snapshot(source_node, before)
                                    or (getattr(before, "st_ino", None), getattr(before, "st_dev", None))
                                    != (getattr(path_stat, "st_ino", None), getattr(path_stat, "st_dev", None))):
                                raise ValueError(f"Changed while creating ZIP: {source}")
                            date_time = time.localtime(before.st_mtime)[:6]
                            # ZIP's DOS timestamp cannot represent years before
                            # 1980 or after 2107. Keep valid file timestamps
                            # intact and clamp only values outside that range.
                            if date_time[0] < 1980:
                                date_time = (1980, 1, 1, 0, 0, 0)
                            elif date_time[0] > 2107:
                                date_time = (2107, 12, 31, 23, 59, 58)
                            info = zipfile.ZipInfo(arcname, date_time=date_time)
                            info.compress_type = zipfile.ZIP_DEFLATED
                            info.file_size = before.st_size
                            info.external_attr = (before.st_mode & 0xFFFF) << 16
                            with archive.open(info, "w") as archive_stream:
                                while True:
                                    if cancel_event is not None and cancel_event.is_set():
                                        raise ActionCancelled()
                                    chunk = source_stream.read(1024 * 1024)
                                    if not chunk:
                                        break
                                    archive_stream.write(chunk)
                            after = os.fstat(source_stream.fileno())
                            if not _matches_snapshot(source_node, after):
                                raise ValueError(f"Changed while creating ZIP: {source}")
                        written += 1
                    except ActionCancelled:
                        raise
                    except ValueError:
                        raise
                    except OSError as exc:
                        errors.append(f"{source}: {exc}")
                        raise RuntimeError(f"Could not finish writing {source}: {exc}") from exc
                    if on_progress is not None:
                        on_progress(written, total_files)
        if cancel_event is not None and cancel_event.is_set():
            raise ActionCancelled()
        if overwrite:
            os.replace(temporary, destination)
        else:
            try:
                os.link(temporary, destination)
            except FileExistsError:
                raise FileExistsError(f"The destination was created while making the ZIP: {destination}")
            except OSError:
                # Some network filesystems disallow hard links. Reserve the
                # name exclusively before replacing the placeholder.
                descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(descriptor)
                try:
                    os.replace(temporary, destination)
                except Exception:
                    try:
                        os.remove(destination)
                    except OSError:
                        pass
                    raise
            else:
                os.remove(temporary)
    except ActionCancelled:
        try:
            os.remove(temporary)
        except OSError:
            pass
        return ZipResult(destination, written, errors, cancelled=True)
    except Exception:
        try:
            os.remove(temporary)
        except OSError:
            pass
        raise

    errors.extend(f"Skipped reparse point: {path}" for path in reparses)
    return ZipResult(destination, written, errors)


def remove_selected(node, *, recycle: bool, cancel_event=None,
                   on_progress: Optional[Callable[[int, int], None]] = None,
                   prevalidated: bool = False):
    """Remove a single validated selection without following reparse paths."""
    if not prevalidated:
        check = validate_selection([node], cancel_event=cancel_event,
                                   on_progress=on_progress, reject_reparse=True)
        if not check.valid:
            raise ValueError("The selection changed or contains an unsafe reparse point. Rescan before deleting.\n"
                             + "\n".join(check.issues[:5]))
    if cancel_event is not None and cancel_event.is_set():
        raise ActionCancelled()
    current = os.stat(node.path, follow_symlinks=False)
    if not _matches_snapshot(node, current):
        raise ValueError(f"Changed after verification: {node.path}")
    if _is_reparse(current):
        raise ValueError(f"Refusing to follow a reparse point: {node.path}")
    if recycle:
        import trash
        moved, message = trash.send_to_trash(node.path)
        if not moved:
            raise OSError(message)
        return
    if stat.S_ISDIR(current.st_mode):
        shutil.rmtree(node.path)
    else:
        os.remove(node.path)
