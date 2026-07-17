import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Callable, Optional
import time


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


class FolderScanner:
    """Asynchronous folder scanner.

    Sizes first-level directories in parallel with a thread pool and walks
    each subtree iteratively, so deep folder structures can't hit Python's
    recursion limit.
    """

    MAX_WORKERS = 8

    def __init__(self):
        self._cancel_requested = threading.Event()
        self._is_scanning = threading.Event()
        self._current_thread: Optional[threading.Thread] = None

    @property
    def is_scanning(self) -> bool:
        return self._is_scanning.is_set()

    def cancel(self):
        self._cancel_requested.set()

    def _get_directory_size(self, path: str) -> tuple[int, int, list]:
        """Iteratively compute total size, item count and errors for a subtree."""
        total_size = 0
        item_count = 0
        errors = []
        stack = [path]

        while stack:
            if self._cancel_requested.is_set():
                break

            current = stack.pop()
            try:
                with os.scandir(current) as entries:
                    for entry in entries:
                        if self._cancel_requested.is_set():
                            break

                        try:
                            if entry.is_file(follow_symlinks=False):
                                total_size += entry.stat(follow_symlinks=False).st_size
                                item_count += 1
                            elif entry.is_dir(follow_symlinks=False):
                                item_count += 1
                                stack.append(entry.path)
                        except PermissionError:
                            errors.append(f"Access denied: {entry.path}")
                        except OSError as e:
                            errors.append(f"Error: {entry.path} - {str(e)}")

            except PermissionError:
                errors.append(f"Access denied: {current}")
            except OSError as e:
                errors.append(f"Error: {current} - {str(e)}")

        return total_size, item_count, errors

    def _size_directory_entry(self, entry_path: str, entry_name: str, ctime: float) -> tuple[FileItem, list]:
        dir_size, dir_count, dir_errors = self._get_directory_size(entry_path)
        item = FileItem(
            path=entry_path,
            name=entry_name,
            size=dir_size,
            is_directory=True,
            creation_date=ctime,
            item_count=dir_count
        )
        return item, dir_errors

    def scan(
        self,
        folder_path: str,
        on_progress: Optional[Callable[[str, int], None]] = None,
        on_complete: Optional[Callable[[ScanResult], None]] = None,
        on_error: Optional[Callable[[str], None]] = None
    ):
        def _scan_worker():
            self._is_scanning.set()

            start_time = time.time()
            items: List[FileItem] = []
            errors: List[str] = []
            total_size = 0
            total_items = 0

            try:
                if not os.path.exists(folder_path):
                    if on_error:
                        on_error(f"Folder not found: {folder_path}")
                    return

                if not os.path.isdir(folder_path):
                    if on_error:
                        on_error(f"Not a folder: {folder_path}")
                    return

                try:
                    entries = list(os.scandir(folder_path))
                except PermissionError:
                    if on_error:
                        on_error(f"Access denied: {folder_path}")
                    return
                except OSError as e:
                    if on_error:
                        on_error(f"Cannot read folder: {str(e)}")
                    return

                directories = []
                progress_count = 0

                for entry in entries:
                    if self._cancel_requested.is_set():
                        break

                    try:
                        stat = entry.stat(follow_symlinks=False)
                        is_dir = entry.is_dir(follow_symlinks=False)

                        if is_dir:
                            directories.append((entry.path, entry.name, stat.st_ctime))
                        else:
                            items.append(FileItem(
                                path=entry.path,
                                name=entry.name,
                                size=stat.st_size,
                                is_directory=False,
                                creation_date=stat.st_ctime
                            ))
                            progress_count += 1
                            if on_progress:
                                on_progress(entry.name, progress_count)

                    except PermissionError:
                        error_msg = f"Access denied: {entry.path}"
                        errors.append(error_msg)
                        items.append(FileItem(
                            path=entry.path,
                            name=entry.name,
                            size=0,
                            is_directory=False,
                            creation_date=0,
                            error=error_msg
                        ))
                    except OSError as e:
                        errors.append(f"Error: {entry.path} - {str(e)}")

                if directories and not self._cancel_requested.is_set():
                    workers = min(self.MAX_WORKERS, max(1, len(directories)))
                    with ThreadPoolExecutor(max_workers=workers) as executor:
                        futures = {
                            executor.submit(self._size_directory_entry, path, name, ctime): name
                            for path, name, ctime in directories
                        }

                        for future in as_completed(futures):
                            if self._cancel_requested.is_set():
                                break

                            try:
                                item, dir_errors = future.result()
                                items.append(item)
                                errors.extend(dir_errors)
                            except Exception as e:
                                errors.append(f"Error: {futures[future]} - {str(e)}")
                                continue

                            progress_count += 1
                            if on_progress:
                                on_progress(futures[future], progress_count)

                total_size = sum(item.size for item in items)
                total_items = len(items)
                scan_time = time.time() - start_time

                result = ScanResult(
                    items=items,
                    total_size=total_size,
                    total_items=total_items,
                    errors=errors,
                    scan_time=scan_time
                )

                if on_complete and not self._cancel_requested.is_set():
                    on_complete(result)

            except Exception as e:
                if on_error:
                    on_error(f"Unexpected error: {str(e)}")
            finally:
                self._is_scanning.clear()

        if self.is_scanning:
            self.cancel()
            if self._current_thread and self._current_thread.is_alive():
                self._current_thread.join(timeout=5.0)

        self._cancel_requested.clear()
        self._current_thread = threading.Thread(target=_scan_worker, daemon=True)
        self._current_thread.start()


class QuickScanner:
    """Fast, shallow first-level listing without directory sizing."""

    def scan_first_level(self, folder_path: str) -> List[FileItem]:
        items = []
        try:
            with os.scandir(folder_path) as entries:
                for entry in entries:
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        is_dir = entry.is_dir(follow_symlinks=False)

                        items.append(FileItem(
                            path=entry.path,
                            name=entry.name,
                            size=0 if is_dir else stat.st_size,
                            is_directory=is_dir,
                            creation_date=stat.st_ctime
                        ))
                    except (PermissionError, OSError):
                        pass
        except (PermissionError, OSError):
            pass

        return items
