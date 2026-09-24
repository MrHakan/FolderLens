import heapq
import os
import queue
import stat as stat_module
import threading
from dataclasses import dataclass, field
from typing import List, Callable, Optional
import time

from file_utils import cancellable_sorted


_NO_CHILDREN: tuple = ()
_REPARSE_FLAG = getattr(stat_module, "FILE_ATTRIBUTE_REPARSE_POINT", 0)


def _has_windows_hidden_attribute(entry_stat) -> bool:
    """Detect the Windows FILE_ATTRIBUTE_HIDDEN bit when the platform exposes it."""
    hidden_flag = getattr(stat_module, "FILE_ATTRIBUTE_HIDDEN", None)
    attributes = getattr(entry_stat, "st_file_attributes", None)
    return hidden_flag is not None and attributes is not None and bool(attributes & hidden_flag)


_cluster_sizes: dict = {}
_kernel32 = None


def _windows_api():
    """kernel32 with typed signatures and reliable last-error reporting."""
    global _kernel32
    if _kernel32 is None:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCompressedFileSizeW.argtypes = [wintypes.LPCWSTR,
                                                    ctypes.POINTER(wintypes.DWORD)]
        kernel32.GetCompressedFileSizeW.restype = wintypes.DWORD
        kernel32.GetDiskFreeSpaceW.argtypes = [wintypes.LPCWSTR] + [
            ctypes.POINTER(wintypes.DWORD)] * 4
        kernel32.GetDiskFreeSpaceW.restype = wintypes.BOOL
        _kernel32 = kernel32
    return _kernel32


def _cluster_size(path: str) -> Optional[int]:
    """Allocation unit of the local volume holding *path* (cached)."""
    if path.startswith(("\\\\", "//")):
        return None   # a share's cluster size says little about its storage
    drive = os.path.splitdrive(path)[0]
    if not drive:
        return None
    root = drive + "\\"
    if root not in _cluster_sizes:
        import ctypes
        from ctypes import wintypes
        values = [wintypes.DWORD() for _ in range(4)]
        ok = _windows_api().GetDiskFreeSpaceW(root, *(ctypes.byref(v) for v in values))
        _cluster_sizes[root] = (values[0].value * values[1].value) if ok else None
    return _cluster_sizes[root]


def _complete_windows_metadata(path: str, metadata):
    """Fill on-disk size and hardlink identity that ``DirEntry.stat`` omits.

    On Windows ``DirEntry.stat`` reports neither allocated blocks nor link
    counts.  With extended metadata enabled (local, opt-in scans only), one
    ``os.stat`` supplies the link count and file id, and
    ``GetCompressedFileSizeW`` the bytes a compressed or sparse file really
    occupies, rounded up to whole clusters.  Anything that cannot be read
    stays ``None`` (unknown) rather than being estimated.
    """
    if os.name != "nt" or metadata is None or metadata[0] is not None:
        return metadata
    _allocated, identity, links, is_reparse, is_hidden = metadata
    allocated = None
    try:
        full = os.stat(path, follow_symlinks=False)
        links = full.st_nlink if full.st_nlink > 0 else None
        if links is not None and links > 1 and full.st_ino:
            identity = (full.st_dev, full.st_ino)
    except OSError:
        pass
    try:
        import ctypes
        from ctypes import wintypes
        high = wintypes.DWORD(0)
        low = _windows_api().GetCompressedFileSizeW(path, ctypes.byref(high))
        if low != 0xFFFFFFFF or ctypes.get_last_error() == 0:
            size = (high.value << 32) + low
            cluster = _cluster_size(path)
            if cluster:
                size = -(-size // cluster) * cluster
            allocated = size
    except (AttributeError, OSError, ValueError):
        pass
    return (allocated, identity, links, is_reparse, is_hidden)

class Node:
    """One file or directory in the scanned tree.

    Uses __slots__ rather than a dataclass: a scan of a large drive holds one
    of these per file, and dropping the per-instance __dict__ cuts the tree's
    memory footprint by roughly a third.
    """

    __slots__ = ("_path", "name", "is_dir", "size", "creation_date",
                 "modified_date", "item_count", "children", "parent", "error",
                 "_metadata")

    def __init__(self, path: Optional[str], name: str, is_dir: bool, size: int = 0,
                 creation_date: float = 0.0, item_count: int = 0,
                 children: Optional[List["Node"]] = None,
                 parent: Optional["Node"] = None, error: Optional[str] = None,
                 modified_date: int = 0, metadata: Optional[tuple] = None):
        # Files far outnumber directories, and a file's path is just its
        # parent's path plus its name. Storing it per file was about a third
        # of the tree's memory, so it is derived on demand instead; only
        # directories (and any node without a parent) keep their own copy.
        self._path = path if (is_dir or parent is None) else None
        self.name = name
        self.is_dir = is_dir
        self.size = size
        self.creation_date = creation_date
        # Captured during the same stat call used for size.  Thumbnail and
        # other metadata consumers can use this without stat'ing a network
        # path again.
        self.modified_date = modified_date
        self.item_count = item_count
        if children is not None:
            self.children = children
        else:
            # files can never have children, so they share one empty tuple
            # instead of each allocating a list they will never use
            self.children = [] if is_dir else _NO_CHILDREN
        self.parent = parent
        self.error = error
        # Extended metadata is opt-in on large scans.  Ordinary files share
        # None instead of allocating a tuple per entry.
        self._metadata = metadata

    @property
    def logical_size(self) -> int:
        return self.size

    @property
    def mtime_ns(self) -> int:
        return self.modified_date

    @property
    def allocated_size(self) -> Optional[int]:
        return self._metadata[0] if self._metadata is not None else None

    @property
    def file_identity(self) -> Optional[tuple]:
        return self._metadata[1] if self._metadata is not None else None

    @property
    def link_count(self) -> Optional[int]:
        return self._metadata[2] if self._metadata is not None else None

    @property
    def is_reparse_point(self) -> bool:
        return bool(self._metadata and self._metadata[3])

    @property
    def is_hidden(self) -> bool:
        # Dot-hidden names need no per-node metadata. Only nodes marked with
        # Windows' hidden attribute carry an extra field in the rare metadata tuple.
        return (self.name.startswith(".") or
                bool(self._metadata and len(self._metadata) > 4 and self._metadata[4]))

    @property
    def path(self) -> str:
        if self._path is not None:
            return self._path
        parent = self.parent
        if parent is None:                    # defensive: detached node
            return self.name
        base = parent.path
        if base.endswith(("\\", "/")):        # drive roots like "C:\"
            return base + self.name
        return base + os.sep + self.name

    @path.setter
    def path(self, value: str):
        self._path = value

    @property
    def ext(self) -> str:
        """Lower-case extension, taken from the name so the full path never
        has to be materialised just to ask what kind of file this is."""
        dot = self.name.rfind(".")
        return self.name[dot:].lower() if dot > 0 else ""

    def __repr__(self) -> str:
        kind = "dir" if self.is_dir else "file"
        return f"<Node {kind} {self.name!r} size={self.size}>"

    def sorted_children(self, key: str = "size", reverse: bool = True,
                        should_cancel: Optional[Callable[[], bool]] = None) -> List["Node"]:
        if key == "name":
            from file_utils import natural_sort_key
            return cancellable_sorted(
                self.children, key=lambda n: natural_sort_key(n.name),
                reverse=reverse, should_cancel=should_cancel)
        if key == "date":
            return cancellable_sorted(
                self.children, key=lambda n: n.creation_date,
                reverse=reverse, should_cancel=should_cancel)
        if key == "type":
            from file_utils import get_file_category
            return cancellable_sorted(
                self.children,
                key=lambda n: (not n.is_dir, "" if n.is_dir else get_file_category(n.name, is_dir=False)['label'], n.name.lower()),
                reverse=reverse, should_cancel=should_cancel,
            )
        return cancellable_sorted(
            self.children, key=lambda n: n.size, reverse=reverse,
            should_cancel=should_cancel)


@dataclass
class FileItem:
    path: str
    name: str
    size: int
    is_directory: bool
    creation_date: float
    item_count: int = 0
    error: Optional[str] = None


@dataclass
class ScanResult:
    items: List[FileItem]
    total_size: int
    total_items: int
    errors: List[str]
    scan_time: float


@dataclass(frozen=True)
class ObservedFile:
    """A bounded, immutable sample of large files encountered so far."""

    path: str
    name: str
    size: int


@dataclass(frozen=True)
class ScanSnapshot:
    """Immutable progress values; the live Node tree is never shared mid-scan.

    Bytes and files are *observed so far*, not a prediction of final totals.
    An incomplete scan remains partial even when its worker pool has stopped.
    """

    generation: int
    root_path: str
    state: str
    known_bytes: int
    known_files: int
    observed_items: int
    directories_completed: int
    pending_directories: int
    errors: int
    queue_high_water: int
    deferred_high_water: int
    elapsed_seconds: float
    partial: bool
    observed_files: tuple = ()
    # Directories whose read has been outstanding for a while, oldest first,
    # as (path, seconds) pairs.  A stuck share call cannot be interrupted, so
    # the UI offers to skip these instead of waiting on them indefinitely.
    slow_directories: tuple = ()
    skipped_directories: int = 0
    cancelling: bool = False


@dataclass
class ScanSession:
    generation: int
    root_path: str
    cancel: threading.Event = field(default_factory=threading.Event)
    lock: threading.Lock = field(default_factory=threading.Lock)
    started: float = field(default_factory=time.monotonic)
    state: str = "scanning"
    progress_count: int = 0
    known_bytes: int = 0
    known_files: int = 0
    directories_discovered: int = 1
    directories_completed: int = 0
    error_count: int = 0
    queue_high_water: int = 0
    deferred_high_water: int = 0
    observed_file_heap: list = field(default_factory=list)
    last_snapshot: float = 0.0
    # path -> (node, monotonic start, worker state) for directories being read
    in_flight: dict = field(default_factory=dict)
    skipped_paths: set = field(default_factory=set)
    skip_messages: list = field(default_factory=list)
    spawn_worker: Optional[Callable] = None
    abandoned_threads: set = field(default_factory=set)

    MAX_OBSERVED_FILES = 50
    SLOW_DIRECTORY_SECONDS = 3.0
    MAX_SLOW_DIRECTORIES = 3

    def _slow_directories(self, now: float) -> tuple:
        slow = [(now - started, path) for path, (_node, started, _state) in self.in_flight.items()
                if path != self.root_path and now - started >= self.SLOW_DIRECTORY_SECONDS]
        slow.sort(reverse=True)
        return tuple((path, round(age, 1)) for age, path in slow[:self.MAX_SLOW_DIRECTORIES])

    def snapshot(self) -> ScanSnapshot:
        with self.lock:
            now = time.monotonic()
            return ScanSnapshot(
                self.generation, self.root_path, self.state, self.known_bytes,
                self.known_files, self.progress_count, self.directories_completed,
                max(0, self.directories_discovered - self.directories_completed),
                self.error_count, self.queue_high_water, self.deferred_high_water,
                round(now - self.started, 3),
                (self.state != "complete" or self.error_count > 0
                 or bool(self.skipped_paths)),
                tuple(
                    ObservedFile(path, os.path.basename(path) or path, size)
                    for size, path in sorted(self.observed_file_heap, reverse=True)
                ),
                self._slow_directories(now) if self.state == "scanning" else (),
                len(self.skipped_paths),
                self.state == "scanning" and self.cancel.is_set(),
            )

    def is_skipped(self, node) -> bool:
        """Whether *node* or one of its ancestors was skipped by the user."""
        if not self.skipped_paths:
            return False
        current = node
        while current is not None:
            if current.is_dir and current.path in self.skipped_paths:
                return True
            current = current.parent
        return False


class _WorkerState:
    """The directories one worker owns.  Skipping a stuck directory hands the
    rest of them, and the worker's queue task, to a replacement worker."""

    __slots__ = ("lock", "stack", "abandoned", "owner")

    def __init__(self, stack=None):
        self.lock = threading.Lock()
        self.stack = list(stack or ())
        self.abandoned = False
        self.owner = threading.current_thread()


@dataclass(frozen=True)
class ScanEvent:
    generation: int
    kind: str
    path: str
    count: int = 0
    message: Optional[str] = None


def is_network_path(path: str) -> bool:
    """Return whether *path* is likely backed by a network share.

    UNC paths are portable to detect.  On Windows, mapped network drives are
    identified with ``GetDriveTypeW`` as well; this matters because a mapped
    drive does not retain the ``\\server\\share`` spelling in the UI.
    """
    if path.startswith(("\\\\", "//")):
        return True
    if os.name != "nt":
        return False

    drive, _ = os.path.splitdrive(os.path.abspath(path))
    if not drive:
        return False
    try:
        import ctypes
        # DRIVE_REMOTE == 4.  ctypes is imported only on Windows so the
        # scanner remains easy to use in headless tests and on other hosts.
        return ctypes.windll.kernel32.GetDriveTypeW(drive + "\\") == 4
    except (AttributeError, OSError, TypeError):
        return False


class TreeScanner:
    """Scans a whole directory tree once, in parallel, into a Node tree.

    Directory work is fed through a shared queue and bounded worker pool
    instead of assigning one whole subtree to one future.  That keeps a single
    large top-level folder parallel and prevents thousands of futures from
    being created.  Network shares use a smaller pool: remote metadata calls
    are latency-bound and an oversized local-style pool tends to make SMB
    workfolders slower, not faster.  Sizes are aggregated bottom-up once the
    walk is complete.
    """

    MAX_WORKERS = min(32, (os.cpu_count() or 4) * 4)
    NETWORK_WORKERS = 6
    PROGRESS_EVERY = 500
    SNAPSHOT_ITEMS_EVERY = 128
    SNAPSHOT_INTERVAL = 0.15
    QUEUE_PER_WORKER = 32

    @staticmethod
    def _entry_metadata(entry_stat, is_reparse: bool, capture_extended: bool,
                        is_hidden: bool = False):
        if not capture_extended:
            return ((None, None, None, is_reparse, is_hidden)
                    if is_reparse or is_hidden else None)
        blocks = getattr(entry_stat, "st_blocks", None)
        allocated = int(blocks * 512) if blocks is not None else None
        links = getattr(entry_stat, "st_nlink", None)
        # Some platforms report zero when DirEntry cannot provide a link
        # count. Zero is unknown, not evidence that the file has no links.
        if links is not None and links <= 0:
            links = None
        identity = None
        if links is not None and links > 1:
            dev, inode = getattr(entry_stat, "st_dev", None), getattr(entry_stat, "st_ino", None)
            if dev is not None and inode:
                identity = (dev, inode)
        return (allocated, identity, links, is_reparse, is_hidden)

    def __init__(self):
        self._current_session: Optional[ScanSession] = None
        self._current_thread: Optional[threading.Thread] = None
        self._session_lock = threading.Lock()
        self._generation = 0

    @property
    def is_scanning(self) -> bool:
        with self._session_lock:
            return self._current_thread is not None and self._current_thread.is_alive()

    def cancel(self):
        with self._session_lock:
            if self._current_session is not None:
                with self._current_session.lock:
                    if self._current_session.state == "scanning":
                        self._current_session.cancel.set()

    def current_snapshot(self) -> Optional[ScanSnapshot]:
        """Progress of the newest scan, for callers that poll while a share
        call is stuck and no progress callback is firing."""
        with self._session_lock:
            session = self._current_session
        return session.snapshot() if session is not None else None

    def skip_directory(self, path: str) -> bool:
        """Leave one folder of the running scan unread and mark it skipped.

        The folder keeps an error, so the finished scan stays partial.  If a
        worker is stuck reading it, that worker is abandoned and a new one
        takes over the rest of its work; the stuck call returns on its own
        and its result is discarded.  The scan root cannot be skipped.
        """
        with self._session_lock:
            session = self._current_session
        if session is None:
            return False
        with session.lock:
            if (session.state != "scanning" or session.cancel.is_set()
                    or path == session.root_path or path in session.skipped_paths):
                return False
            entry = session.in_flight.get(path)
            session.skipped_paths.add(path)
            session.skip_messages.append(f"Skipped by user: {path}")
            if entry is not None:
                entry[0].error = "Skipped"
            spawn = session.spawn_worker
        if entry is None:
            return True
        state = entry[2]
        with state.lock:
            if state.abandoned:
                return True
            state.abandoned = True
            inherited, state.stack = state.stack, []
        with session.lock:
            session.abandoned_threads.add(state.owner)
            session.directories_completed += 1
        if spawn is not None:
            spawn(inherited)
        return True

    @classmethod
    def worker_limit(cls, folder_path: str) -> int:
        """Choose a sensible metadata-concurrency limit for a folder."""
        if is_network_path(folder_path):
            return min(cls.MAX_WORKERS, cls.NETWORK_WORKERS)
        return cls.MAX_WORKERS

    @staticmethod
    def _event(session: ScanSession, callback, kind: str, path: str,
               count: int = 0, message: Optional[str] = None):
        if callback is not None:
            try:
                callback(ScanEvent(session.generation, kind, path, count, message))
            except Exception:
                # A closed UI must not turn a valid filesystem scan into an error.
                pass

    def _snapshot(self, session: ScanSession, callback, force: bool = False):
        if callback is None:
            return
        now = time.monotonic()
        with session.lock:
            if not force and now - session.last_snapshot < self.SNAPSHOT_INTERVAL:
                return
            session.last_snapshot = now
        try:
            callback(session.snapshot())
        except Exception:
            pass

    def _tick_progress(self, on_progress: Optional[Callable[[int], None]],
                       session: ScanSession, n: int = 1, bytes_seen: int = 0,
                       files_seen: int = 0, on_snapshot=None,
                       observed_files: Optional[List[tuple]] = None):
        with session.lock:
            before = session.progress_count
            session.progress_count += n
            session.known_bytes += bytes_seen
            session.known_files += files_seen
            for path, size in observed_files or ():
                candidate = (int(size), path)
                if len(session.observed_file_heap) < session.MAX_OBSERVED_FILES:
                    heapq.heappush(session.observed_file_heap, candidate)
                elif candidate > session.observed_file_heap[0]:
                    heapq.heapreplace(session.observed_file_heap, candidate)
            after = session.progress_count
        if (on_progress is not None and not session.cancel.is_set()
                and before // self.PROGRESS_EVERY != after // self.PROGRESS_EVERY):
            try:
                on_progress(after)
            except Exception:
                pass
        self._snapshot(session, on_snapshot)

    @staticmethod
    def _entry_modified_time(entry_stat) -> int:
        value = getattr(entry_stat, "st_mtime_ns", None)
        if value is not None:
            return int(value)
        return int(getattr(entry_stat, "st_mtime", 0.0) * 1_000_000_000)

    def _read_directory(self, node: Node, errors: List[str],
                        on_progress: Optional[Callable[[int], None]] = None,
                        work_queue: Optional[queue.Queue] = None,
                        session: Optional[ScanSession] = None,
                        on_snapshot=None, on_event=None,
                        capture_extended: bool = False) -> Optional[str]:
        """Read one directory and enqueue child directories.

        The entry type and metadata come from one ``DirEntry.stat`` call.  In
        particular, this avoids the old ``stat`` + ``is_dir`` pair of remote
        metadata requests on filesystems where neither result is cached.
        Returns a user-facing error for a directory-open failure, otherwise
        ``None``.  Entry-level failures remain non-fatal and are collected.

        Entries are collected locally and attached to *node* only at the end.
        If the user skips this directory while a share call is stuck, the
        late-returning read must not change a tree that is already complete.
        """
        if session is not None:
            if session.is_skipped(node):
                # skipped before its read began, or below a skipped folder
                with session.lock:
                    if node.path in session.skipped_paths:
                        node.error = "Skipped"
                    session.directories_completed += 1
                return None
            self._event(session, on_event, "directory-start", node.path)

        children: List[Node] = []
        failure: Optional[str] = None
        skip_seen = [-1, False]

        def skipped() -> bool:
            if session is None or not session.skipped_paths:
                return False
            marker = len(session.skipped_paths)
            if skip_seen[0] != marker:
                skip_seen[0], skip_seen[1] = marker, session.is_skipped(node)
            return skip_seen[1]

        def report_error(message: str, path: str):
            if skipped():
                return message
            errors.append(message)
            if session is not None:
                with session.lock:
                    session.error_count += 1
                self._event(session, on_event, "error", path, message=message)
                self._snapshot(session, on_snapshot)
            return message

        try:
            with os.scandir(node.path) as entries:
                batch = 0
                batch_bytes = 0
                batch_files = 0
                batch_observed = []
                next_snapshot_check = time.monotonic() + self.SNAPSHOT_INTERVAL
                for entry in entries:
                    if session is not None and (session.cancel.is_set() or skipped()):
                        break
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                        is_dir = stat_module.S_ISDIR(entry_stat.st_mode)
                        is_reparse = (stat_module.S_ISLNK(entry_stat.st_mode) or
                                      bool(getattr(entry_stat, "st_file_attributes", 0) &
                                           _REPARSE_FLAG))
                        is_hidden = _has_windows_hidden_attribute(entry_stat)
                        if skipped():
                            break
                        metadata = self._entry_metadata(
                            entry_stat, is_reparse, capture_extended, is_hidden)
                        if capture_extended and not is_dir and not is_reparse:
                            metadata = _complete_windows_metadata(entry.path, metadata)
                        child = Node(
                            path=entry.path if is_dir else None,
                            name=entry.name,
                            is_dir=is_dir,
                            size=0 if is_dir else entry_stat.st_size,
                            creation_date=entry_stat.st_ctime,
                            parent=node,
                            modified_date=self._entry_modified_time(entry_stat),
                            metadata=metadata,
                        )
                        children.append(child)
                        if is_dir and not is_reparse and work_queue is not None:
                            if session is not None:
                                with session.lock:
                                    session.directories_discovered += 1
                            work_queue.put(child)
                        batch += 1
                        if not is_dir:
                            batch_bytes += child.size
                            batch_files += 1
                            if session is not None:
                                batch_observed.append((entry.path, child.size))
                        if (batch >= self.SNAPSHOT_ITEMS_EVERY
                                or time.monotonic() >= next_snapshot_check):
                            self._tick_progress(on_progress, session, batch, batch_bytes,
                                                batch_files, on_snapshot,
                                                observed_files=batch_observed)
                            self._event(session, on_event, "batch-of-entries", node.path, batch)
                            batch = 0
                            batch_bytes = batch_files = 0
                            batch_observed = []
                            next_snapshot_check = time.monotonic() + self.SNAPSHOT_INTERVAL
                    except PermissionError:
                        report_error(f"Access denied: {entry.path}", entry.path)
                    except OSError as e:
                        report_error(f"Error: {entry.path} - {str(e)}", entry.path)
                if batch and not skipped():
                    self._tick_progress(on_progress, session, batch, batch_bytes,
                                        batch_files, on_snapshot,
                                        observed_files=batch_observed)
                    self._event(session, on_event, "batch-of-entries", node.path, batch)
            return None
        except PermissionError:
            failure = "Access denied"
            return report_error(f"Access denied: {node.path}", node.path)
        except FileNotFoundError:
            failure = "Folder not found"
            return report_error(f"Folder not found: {node.path}", node.path)
        except NotADirectoryError:
            failure = "Not a folder"
            return report_error(f"Not a folder: {node.path}", node.path)
        except OSError as e:
            failure = str(e)
            return report_error(f"Cannot read folder: {str(e)}", node.path)
        finally:
            if session is None:
                node.children.extend(children)
                if failure is not None:
                    node.error = failure
            else:
                with session.lock:
                    attach = not session.is_skipped(node)
                    if attach:
                        node.children.extend(children)
                        if failure is not None:
                            node.error = failure
                        session.directories_completed += 1
                if attach:
                    self._event(session, on_event, "directory-complete", node.path)
                    self._snapshot(session, on_snapshot)

    def _run_directory_workers(self, work_queue: queue.Queue, workers: int,
                               error_lists: List[List[str]],
                               on_progress: Optional[Callable[[int], None]] = None,
                               session: Optional[ScanSession] = None,
                               on_snapshot=None, on_event=None,
                               capture_extended: bool = False):
        """Drain a bounded queue without ever blocking workers on enqueue.

        When the shared queue fills, a worker walks the overflow itself using
        a local stack.  No worker waits to put while all consumers are busy.

        Skipping a directory whose read is stuck abandons that worker: its
        remaining stack and its queue task move to a fresh worker, so the scan
        can finish while the stuck call is left to return on its own.
        """
        threads: List[threading.Thread] = []
        threads_lock = threading.Lock()

        def drain(state: _WorkerState, errors: List[str]) -> bool:
            """Read the directories *state* owns; False once it was abandoned."""
            class Scheduler:
                def put(self, child):
                    try:
                        work_queue.put_nowait(child)
                    except queue.Full:
                        with state.lock:
                            state.stack.append(child)
                            depth = len(state.stack)
                        with session.lock:
                            session.deferred_high_water = max(
                                session.deferred_high_water, depth)
                    else:
                        with session.lock:
                            session.queue_high_water = max(session.queue_high_water,
                                                           work_queue.qsize())

            scheduler = Scheduler()
            while not session.cancel.is_set():
                with state.lock:
                    if state.abandoned or not state.stack:
                        break
                    current = state.stack.pop()
                with session.lock:
                    session.in_flight[current.path] = (current, time.monotonic(), state)
                try:
                    self._read_directory(current, errors, on_progress, scheduler,
                                         session, on_snapshot, on_event,
                                         capture_extended)
                except Exception as exc:  # one bad share must not kill the pool
                    with session.lock:
                        record = not session.is_skipped(current)
                        if record:
                            current.error = str(exc)
                            session.error_count += 1
                            session.directories_completed += 1
                    if record:
                        message = f"Error: {current.path} - {exc}"
                        errors.append(message)
                        self._event(session, on_event, "error", current.path, message=message)
                        self._snapshot(session, on_snapshot)
                finally:
                    with session.lock:
                        entry = session.in_flight.get(current.path)
                        if entry is not None and entry[2] is state:
                            del session.in_flight[current.path]
            with state.lock:
                if state.abandoned:
                    return False
                state.stack = []
                return True

        def worker(errors: List[str], inherited=None):
            if inherited is not None:
                # This worker took over an abandoned worker's queue task.
                if drain(_WorkerState(inherited), errors):
                    work_queue.task_done()
            while True:
                try:
                    node = work_queue.get(timeout=0.05)
                except queue.Empty:
                    # A worker may be reading a directory and enqueueing more
                    # work.  Keep waiting while any queued task is unfinished;
                    # exit only once the queue is genuinely drained.
                    if work_queue.unfinished_tasks == 0:
                        return
                    continue
                if not drain(_WorkerState((node,)), errors):
                    return  # abandoned: the replacement owns this task now
                work_queue.task_done()

        def start(errors: List[str], inherited=None):
            thread = threading.Thread(target=worker, args=(errors, inherited), daemon=True)
            with threads_lock:
                threads.append(thread)
            thread.start()

        def spawn_replacement(inherited):
            errors: List[str] = []
            with threads_lock:
                error_lists.append(errors)
            start(errors, inherited)

        session.spawn_worker = spawn_replacement
        for i in range(workers):
            start(error_lists[i])

        # Queue.join also drains cleanly after cancellation: workers still
        # call task_done for queued directories but skip their filesystem I/O.
        work_queue.join()
        session.spawn_worker = None
        with threads_lock:
            finished = list(threads)
        for thread in finished:
            # an abandoned worker may still be inside a stuck share call
            if thread not in session.abandoned_threads:
                thread.join(timeout=1.0)

    def _build_subtree(self, root: Node, errors: List[str],
                       on_progress: Optional[Callable[[int], None]] = None):
        """Compatibility helper that walks one subtree with the shared queue."""
        work_queue: queue.Queue = queue.Queue(maxsize=self.QUEUE_PER_WORKER)
        work_queue.put(root)
        session = ScanSession(0, root.path)
        self._run_directory_workers(work_queue, 1, [errors], on_progress, session)
        self._aggregate_sizes(root)

    @staticmethod
    def _aggregate_sizes(root: Node, capture_extended: bool = False):
        """Single post-order pass: directory sizes and item counts."""
        stack = [(root, False)]
        while stack:
            node, processed = stack.pop()
            if not node.is_dir:
                continue
            if processed:
                size = 0
                count = 0
                for child in node.children:
                    size += child.size
                    count += 1 + child.item_count
                node.size = size
                node.item_count = count
                if capture_extended and not node.is_reparse_point:
                    allocated = [child.allocated_size for child in node.children]
                    if all(value is not None for value in allocated):
                        hidden = bool(node._metadata and len(node._metadata) > 4
                                      and node._metadata[4])
                        node._metadata = (sum(allocated), None, None, False, hidden)
            else:
                stack.append((node, True))
                for child in node.children:
                    if child.is_dir:
                        stack.append((child, False))

    def scan(
        self,
        folder_path: str,
        on_progress: Optional[Callable[[int], None]] = None,
        on_complete: Optional[Callable[[Node, List[str], float], None]] = None,
        on_error: Optional[Callable[[str], None]] = None,
        on_snapshot: Optional[Callable[[ScanSnapshot], None]] = None,
        on_event: Optional[Callable[[ScanEvent], None]] = None,
        capture_extended: bool = False,
    ):
        with self._session_lock:
            if self._current_session is not None:
                with self._current_session.lock:
                    if self._current_session.state == "scanning":
                        self._current_session.cancel.set()
            self._generation += 1
            generation = self._generation
            session = ScanSession(generation, os.path.abspath(folder_path))
            self._current_session = session

        def _scan_worker():
            start_time = time.time()
            errors: List[str] = []

            try:
                self._snapshot(session, on_snapshot, force=True)
                root_path = os.path.abspath(folder_path)
                try:
                    root_stat = os.stat(root_path)
                except OSError:
                    # Let the single scandir below provide the authoritative
                    # missing/not-a-folder/access-denied result.
                    root_stat = None

                root_name = os.path.basename(root_path.rstrip("\\/")) or root_path
                root_hidden = (root_name.startswith(".") or
                               (root_stat is not None and
                                _has_windows_hidden_attribute(root_stat)))
                root_metadata = ((None, None, None, False, True) if root_hidden else None)
                root = Node(
                    path=root_path,
                    name=root_name,
                    is_dir=True,
                    creation_date=root_stat.st_ctime if root_stat else 0.0,
                    modified_date=self._entry_modified_time(root_stat) if root_stat else 0,
                    metadata=root_metadata,
                )

                workers = self.worker_limit(root.path)
                work_queue: queue.Queue = queue.Queue(maxsize=max(8, workers * self.QUEUE_PER_WORKER))
                work_queue.put(root)
                error_lists = [[] for _ in range(workers)]
                self._run_directory_workers(work_queue, workers, error_lists,
                                            on_progress, session, on_snapshot, on_event,
                                            capture_extended)
                for lst in error_lists:
                    errors.extend(lst)
                with session.lock:
                    errors.extend(session.skip_messages)
                if root.error:
                    with session.lock:
                        session.state = "failed"
                    if on_error and not session.cancel.is_set():
                        on_error(errors[0])
                    return

                self._aggregate_sizes(root, capture_extended)
                scan_time = time.time() - start_time

                if not session.cancel.is_set():
                    with session.lock:
                        session.state = "complete"
                    self._event(session, on_event, "complete", root.path)
                    self._snapshot(session, on_snapshot, force=True)
                if on_complete and not session.cancel.is_set():
                    on_complete(root, errors, scan_time)

            except Exception as e:
                with session.lock:
                    session.state = "failed"
                    session.error_count += 1
                self._event(session, on_event, "error", session.root_path, message=str(e))
                if on_error and not session.cancel.is_set():
                    on_error(f"Unexpected error: {str(e)}")
            finally:
                if session.cancel.is_set() and session.state == "scanning":
                    with session.lock:
                        session.state = "cancelled"
                    self._event(session, on_event, "cancelled", session.root_path)
                self._snapshot(session, on_snapshot, force=True)
        # Never wait for an old network syscall on the UI thread.  Each worker
        # captures its own cancellation event, so a newer scan cannot revive it.
        thread = threading.Thread(target=_scan_worker, daemon=True,
                                  name=f"folderlens-scan-{generation}")
        with self._session_lock:
            if generation != self._generation:
                return
            self._current_thread = thread
            thread.start()
        return generation


class FolderScanner:
    """Flat first-level scan built on top of TreeScanner.

    Kept for the console mode and as a stable, simple API: returns a
    ScanResult whose items are the scanned folder's direct children with
    fully aggregated directory sizes.
    """

    def __init__(self):
        self._tree_scanner = TreeScanner()

    @property
    def is_scanning(self) -> bool:
        return self._tree_scanner.is_scanning

    def cancel(self):
        self._tree_scanner.cancel()

    def scan(
        self,
        folder_path: str,
        on_progress: Optional[Callable[[str, int], None]] = None,
        on_complete: Optional[Callable[[ScanResult], None]] = None,
        on_error: Optional[Callable[[str], None]] = None
    ):
        def _tree_progress(count: int):
            if on_progress:
                on_progress("", count)

        def _tree_complete(root: Node, errors: List[str], scan_time: float):
            items = [
                FileItem(
                    path=child.path,
                    name=child.name,
                    size=child.size,
                    is_directory=child.is_dir,
                    creation_date=child.creation_date,
                    item_count=child.item_count,
                    error=child.error
                )
                for child in root.children
            ]
            result = ScanResult(
                items=items,
                total_size=root.size,
                total_items=len(items),
                errors=errors,
                scan_time=scan_time
            )
            if on_complete:
                on_complete(result)

        self._tree_scanner.scan(
            folder_path,
            on_progress=_tree_progress if on_progress else None,
            on_complete=_tree_complete,
            on_error=on_error
        )


class QuickScanner:
    """Fast, shallow first-level listing without directory sizing."""

    def scan_first_level(self, folder_path: str) -> List[FileItem]:
        items = []
        try:
            with os.scandir(folder_path) as entries:
                for entry in entries:
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                        is_dir = stat_module.S_ISDIR(entry_stat.st_mode)

                        items.append(FileItem(
                            path=entry.path,
                            name=entry.name,
                            size=0 if is_dir else entry_stat.st_size,
                            is_directory=is_dir,
                            creation_date=entry_stat.st_ctime
                        ))
                    except (PermissionError, OSError):
                        pass
        except (PermissionError, OSError):
            pass

        return items
