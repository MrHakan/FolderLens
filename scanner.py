import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import List, Callable, Optional
import time


@dataclass
class Node:
    """One file or directory in the scanned tree."""
    path: str
    name: str
    is_dir: bool
    size: int = 0
    creation_date: float = 0.0
    item_count: int = 0
    children: List["Node"] = field(default_factory=list)
    parent: Optional["Node"] = None
    error: Optional[str] = None

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
                key=lambda n: (not n.is_dir, "" if n.is_dir else get_file_category(n.path)['label'], n.name.lower()),
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


class TreeScanner:
    """Scans a whole directory tree once, in parallel, into a Node tree.

    Top-level directories are walked concurrently; each subtree is walked
    iteratively (no recursion limit) and sizes are aggregated bottom-up in a
    single post-order pass. The resulting tree lets a UI expand any folder
    instantly without touching the disk again.
    """

    MAX_WORKERS = min(32, (os.cpu_count() or 4) * 4)
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

    def _tick_progress(self, on_progress: Optional[Callable[[int], None]], n: int = 1):
        if on_progress is None:
            return
        with self._progress_lock:
            before = self._progress_count
            self._progress_count += n
            after = self._progress_count
        if before // self.PROGRESS_EVERY != after // self.PROGRESS_EVERY:
            on_progress(after)

    def _build_subtree(self, root: Node, errors: List[str],
                       on_progress: Optional[Callable[[int], None]] = None):
        """Fill in root's descendants iteratively, then aggregate sizes bottom-up."""
        stack = [root]
        while stack:
            if self._cancel_requested.is_set():
                return
            node = stack.pop()
            try:
                with os.scandir(node.path) as entries:
                    batch = 0
                    for entry in entries:
                        if self._cancel_requested.is_set():
                            break
                        try:
                            stat = entry.stat(follow_symlinks=False)
                            if entry.is_dir(follow_symlinks=False):
                                child = Node(
                                    path=entry.path, name=entry.name, is_dir=True,
                                    creation_date=stat.st_ctime, parent=node
                                )
                                stack.append(child)
                            else:
                                child = Node(
                                    path=entry.path, name=entry.name, is_dir=False,
                                    size=stat.st_size, creation_date=stat.st_ctime, parent=node
                                )
                            node.children.append(child)
                            batch += 1
                        except PermissionError:
                            errors.append(f"Access denied: {entry.path}")
                        except OSError as e:
                            errors.append(f"Error: {entry.path} - {str(e)}")
                    if batch:
                        self._tick_progress(on_progress, batch)
            except PermissionError:
                node.error = "Access denied"
                errors.append(f"Access denied: {node.path}")
            except OSError as e:
                node.error = str(e)
                errors.append(f"Error: {node.path} - {str(e)}")

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
                if not os.path.exists(folder_path):
                    if on_error:
                        on_error(f"Folder not found: {folder_path}")
                    return
                if not os.path.isdir(folder_path):
                    if on_error:
                        on_error(f"Not a folder: {folder_path}")
                    return

                try:
                    root_stat = os.stat(folder_path)
                except OSError:
                    root_stat = None

                root = Node(
                    path=os.path.abspath(folder_path),
                    name=os.path.basename(folder_path.rstrip("\\/")) or folder_path,
                    is_dir=True,
                    creation_date=root_stat.st_ctime if root_stat else 0.0
                )

                try:
                    entries = list(os.scandir(root.path))
                except PermissionError:
                    if on_error:
                        on_error(f"Access denied: {folder_path}")
                    return
                except OSError as e:
                    if on_error:
                        on_error(f"Cannot read folder: {str(e)}")
                    return

                dir_children = []
                for entry in entries:
                    if self._cancel_requested.is_set():
                        break
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        if entry.is_dir(follow_symlinks=False):
                            child = Node(
                                path=entry.path, name=entry.name, is_dir=True,
                                creation_date=stat.st_ctime, parent=root
                            )
                            dir_children.append(child)
                        else:
                            child = Node(
                                path=entry.path, name=entry.name, is_dir=False,
                                size=stat.st_size, creation_date=stat.st_ctime, parent=root
                            )
                        root.children.append(child)
                    except PermissionError:
                        errors.append(f"Access denied: {entry.path}")
                    except OSError as e:
                        errors.append(f"Error: {entry.path} - {str(e)}")

                self._tick_progress(on_progress, len(root.children))

                if dir_children and not self._cancel_requested.is_set():
                    # each worker collects errors into its own list to avoid locking
                    error_lists = [[] for _ in dir_children]
                    workers = min(self.MAX_WORKERS, len(dir_children))
                    with ThreadPoolExecutor(max_workers=workers) as executor:
                        futures = {
                            executor.submit(self._build_subtree, child, error_lists[i], on_progress): i
                            for i, child in enumerate(dir_children)
                        }
                        for future in as_completed(futures):
                            try:
                                future.result()
                            except Exception as e:
                                errors.append(f"Error: {dir_children[futures[future]].path} - {str(e)}")
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
