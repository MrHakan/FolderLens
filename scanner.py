import os
import queue
import stat as stat_module
import threading
from dataclasses import dataclass
from typing import List, Callable, Optional
import time


_NO_CHILDREN: tuple = ()


class Node:
    """One file or directory in the scanned tree.

    Uses __slots__ rather than a dataclass: a scan of a large drive holds one
    of these per file, and dropping the per-instance __dict__ cuts the tree's
    memory footprint by roughly a third.
    """

    __slots__ = ("_path", "name", "is_dir", "size", "creation_date",
                 "modified_date", "item_count", "children", "parent", "error")

    def __init__(self, path: Optional[str], name: str, is_dir: bool, size: int = 0,
                 creation_date: float = 0.0, item_count: int = 0,
                 children: Optional[List["Node"]] = None,
                 parent: Optional["Node"] = None, error: Optional[str] = None,
                 modified_date: int = 0):
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

    def sorted_children(self, key: str = "size", reverse: bool = True) -> List["Node"]:
        if key == "name":
            from file_utils import natural_sort_key
            return sorted(self.children, key=lambda n: natural_sort_key(n.name), reverse=reverse)
        if key == "date":
            return sorted(self.children, key=lambda n: n.creation_date, reverse=reverse)
        if key == "type":
            from file_utils import get_file_category
            return sorted(
                self.children,
                key=lambda n: (not n.is_dir, "" if n.is_dir else get_file_category(n.name, is_dir=False)['label'], n.name.lower()),
                reverse=reverse
            )
        return sorted(self.children, key=lambda n: n.size, reverse=reverse)


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

    def __init__(self):
        self._cancel_requested = threading.Event()
        self._is_scanning = threading.Event()
        self._current_thread: Optional[threading.Thread] = None
        self._progress_lock = threading.Lock()
        self._progress_count = 0

    @property
    def is_scanning(self) -> bool:
        return self._is_scanning.is_set()

    def cancel(self):
        self._cancel_requested.set()

    @classmethod
    def worker_limit(cls, folder_path: str) -> int:
        """Choose a sensible metadata-concurrency limit for a folder."""
        if is_network_path(folder_path):
            return min(cls.MAX_WORKERS, cls.NETWORK_WORKERS)
        return cls.MAX_WORKERS

    def _tick_progress(self, on_progress: Optional[Callable[[int], None]], n: int = 1):
        if on_progress is None:
            return
        with self._progress_lock:
            before = self._progress_count
            self._progress_count += n
            after = self._progress_count
        if before // self.PROGRESS_EVERY != after // self.PROGRESS_EVERY:
            on_progress(after)

    @staticmethod
    def _entry_modified_time(entry_stat) -> int:
        value = getattr(entry_stat, "st_mtime_ns", None)
        if value is not None:
            return int(value)
        return int(getattr(entry_stat, "st_mtime", 0.0) * 1_000_000_000)

    def _read_directory(self, node: Node, errors: List[str],
                        on_progress: Optional[Callable[[int], None]] = None,
                        work_queue: Optional[queue.Queue] = None) -> Optional[str]:
        """Read one directory and enqueue child directories.

        The entry type and metadata come from one ``DirEntry.stat`` call.  In
        particular, this avoids the old ``stat`` + ``is_dir`` pair of remote
        metadata requests on filesystems where neither result is cached.
        Returns a user-facing error for a directory-open failure, otherwise
        ``None``.  Entry-level failures remain non-fatal and are collected.
        """
        try:
            with os.scandir(node.path) as entries:
                batch = 0
                for entry in entries:
                    if self._cancel_requested.is_set():
                        break
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                        is_dir = stat_module.S_ISDIR(entry_stat.st_mode)
                        child = Node(
                            path=entry.path,
                            name=entry.name,
                            is_dir=is_dir,
                            size=0 if is_dir else entry_stat.st_size,
                            creation_date=entry_stat.st_ctime,
                            parent=node,
                            modified_date=self._entry_modified_time(entry_stat),
                        )
                        node.children.append(child)
                        if is_dir and work_queue is not None:
                            work_queue.put(child)
                        batch += 1
                        if batch >= self.PROGRESS_EVERY:
                            self._tick_progress(on_progress, batch)
                            batch = 0
                    except PermissionError:
                        errors.append(f"Access denied: {entry.path}")
                    except OSError as e:
                        errors.append(f"Error: {entry.path} - {str(e)}")
                if batch:
                    self._tick_progress(on_progress, batch)
            return None
        except PermissionError:
            node.error = "Access denied"
            message = f"Access denied: {node.path}"
            errors.append(message)
            return message
        except FileNotFoundError:
            node.error = "Folder not found"
            message = f"Folder not found: {node.path}"
            errors.append(message)
            return message
        except NotADirectoryError:
            node.error = "Not a folder"
            message = f"Not a folder: {node.path}"
            errors.append(message)
            return message
        except OSError as e:
            node.error = str(e)
            message = f"Cannot read folder: {str(e)}"
            errors.append(message)
            return message

    def _run_directory_workers(self, work_queue: queue.Queue, workers: int,
                               error_lists: List[List[str]],
                               on_progress: Optional[Callable[[int], None]] = None):
        """Drain a directory queue with a fixed, bounded worker pool."""
        def worker(errors: List[str]):
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
                try:
                    if not self._cancel_requested.is_set():
                        self._read_directory(node, errors, on_progress, work_queue)
                except Exception as e:  # defensive: one bad share must not kill the pool
                    errors.append(f"Error: {node.path} - {str(e)}")
                finally:
                    work_queue.task_done()

        threads = [
            threading.Thread(target=worker, args=(error_lists[i],), daemon=True)
            for i in range(workers)
        ]
        for thread in threads:
            thread.start()

        # Queue.join also drains cleanly after cancellation: workers still
        # call task_done for queued directories but skip their filesystem I/O.
        work_queue.join()
        for thread in threads:
            thread.join(timeout=1.0)

    def _build_subtree(self, root: Node, errors: List[str],
                       on_progress: Optional[Callable[[int], None]] = None):
        """Compatibility helper that walks one subtree with the shared queue."""
        work_queue: queue.Queue = queue.Queue()
        work_queue.put(root)
        self._run_directory_workers(work_queue, 1, [errors], on_progress)
        self._aggregate_sizes(root)

    @staticmethod
    def _aggregate_sizes(root: Node):
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
        on_error: Optional[Callable[[str], None]] = None
    ):
        def _scan_worker():
            self._is_scanning.set()
            start_time = time.time()
            errors: List[str] = []

            try:
                root_path = os.path.abspath(folder_path)
                try:
                    root_stat = os.stat(root_path)
                except OSError:
                    # Let the single scandir below provide the authoritative
                    # missing/not-a-folder/access-denied result.
                    root_stat = None

                root = Node(
                    path=root_path,
                    name=os.path.basename(root_path.rstrip("\\/")) or root_path,
                    is_dir=True,
                    creation_date=root_stat.st_ctime if root_stat else 0.0,
                    modified_date=self._entry_modified_time(root_stat) if root_stat else 0,
                )

                work_queue: queue.Queue = queue.Queue()
                root_error = self._read_directory(root, errors, on_progress, work_queue)
                if root_error:
                    if on_error:
                        on_error(root_error)
                    return

                if work_queue.qsize() and not self._cancel_requested.is_set():
                    workers = self.worker_limit(root.path)
                    error_lists = [[] for _ in range(workers)]
                    self._run_directory_workers(work_queue, workers, error_lists, on_progress)
                    for lst in error_lists:
                        errors.extend(lst)

                self._aggregate_sizes(root)
                scan_time = time.time() - start_time

                if on_complete and not self._cancel_requested.is_set():
                    on_complete(root, errors, scan_time)

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
        self._progress_count = 0
        self._current_thread = threading.Thread(target=_scan_worker, daemon=True)
        self._current_thread.start()


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
