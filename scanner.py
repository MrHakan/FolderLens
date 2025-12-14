import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import List, Callable, Optional
from queue import Queue
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

    def __init__(self):
        self._cancel_requested = threading.Event()
        self._is_scanning = threading.Event()
        self._lock = threading.Lock()
        self._current_thread: Optional[threading.Thread] = None
        
    @property
    def is_scanning(self) -> bool:
        return self._is_scanning.is_set()
    
    def cancel(self):
        self._cancel_requested.set()
        
    def _get_directory_size(self, path: str) -> tuple[int, int, list]:
        total_size = 0
        item_count = 0
        errors = []
        
        if self._cancel_requested.is_set():
            return total_size, item_count, errors
        
        try:
            with os.scandir(path) as entries:
                for entry in entries:
                    if self._cancel_requested.is_set():
                        break
                    
                    try:
                        if entry.is_file(follow_symlinks=False):
                            total_size += entry.stat(follow_symlinks=False).st_size
                            item_count += 1
                        elif entry.is_dir(follow_symlinks=False):
                            item_count += 1
                            sub_size, sub_count, sub_errors = self._get_directory_size(entry.path)
                            total_size += sub_size
                            item_count += sub_count
                            errors.extend(sub_errors)
                    except PermissionError:
                        errors.append(f"Access denied: {entry.path}")
                    except OSError as e:
                        errors.append(f"Error: {entry.path} - {str(e)}")
                        
        except PermissionError:
            errors.append(f"Access denied: {path}")
        except OSError as e:
            errors.append(f"Error: {path} - {str(e)}")
            
        return total_size, item_count, errors
    
    def scan(
        self,
        folder_path: str,
        on_progress: Optional[Callable[[str, int], None]] = None,
        on_complete: Optional[Callable[[ScanResult], None]] = None,
        on_error: Optional[Callable[[str], None]] = None
    ):
"
        def _scan_worker():
            self._cancel_requested.clear()
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
                
                for i, entry in enumerate(entries):
                    if self._cancel_requested.is_set():
                        break
                    
                    try:
                        if on_progress:
                            on_progress(entry.name, i + 1)
                        
                        stat = entry.stat(follow_symlinks=False)
                        is_dir = entry.is_dir(follow_symlinks=False)
                        
                        if is_dir:
                            dir_size, dir_count, dir_errors = self._get_directory_size(entry.path)
                            errors.extend(dir_errors)
                            
                            item = FileItem(
                                path=entry.path,
                                name=entry.name,
                                size=dir_size,
                                is_directory=True,
                                creation_date=stat.st_ctime,
                                item_count=dir_count
                            )
                        else:
                            item = FileItem(
                                path=entry.path,
                                name=entry.name,
                                size=stat.st_size,
                                is_directory=False,
                                creation_date=stat.st_ctime
                            )
                        
                        items.append(item)
                        total_size += item.size
                        total_items += 1
                        
                    except PermissionError:
                        error_msg = f"Access denied: {entry.path}"
                        errors.append(error_msg)
                        items.append(FileItem(
                            path=entry.path,
                            name=entry.name,
                            size=0,
                            is_directory=entry.is_dir(follow_symlinks=False) if hasattr(entry, 'is_dir') else False,
                            creation_date=0,
                            error=error_msg
                        ))
                    except OSError as e:
                        error_msg = f"Error: {entry.path} - {str(e)}"
                        errors.append(error_msg)
                
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
                self._current_thread.join(timeout=2.0)
        
        self._current_thread = threading.Thread(target=_scan_worker, daemon=True)
        self._current_thread.start()


class QuickScanner:
    def __init__(self):
        self._executor = ThreadPoolExecutor(max_workers=4)
        
    def scan_first_level(self, folder_path: str) -> List[FileItem]:
        try:
            with os.scandir(folder_path) as entries:
                for entry in entries:
                    try:
                        stat = entry.stat(follow_symlinks=False)
                        is_dir = entry.is_dir(follow_symlinks=False)
                        
                        item = FileItem(
                            path=entry.path,
                            name=entry.name,
                            size=0 if is_dir else stat.st_size,
                            is_directory=is_dir,
                            creation_date=stat.st_ctime
                        )
                        items.append(item)
                    except (PermissionError, OSError):
                        pass
        except (PermissionError, OSError):
            pass
            
        return items
