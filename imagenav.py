"""Navigation model for the image viewer.

Knows which images sit next to the current one, and lets the viewer walk into
subfolders or back up to the parent without going back to the main window.
Pure filesystem logic so it can be tested without a display.
"""
import os
from typing import List, Optional

from file_utils import is_image_file, natural_sort_key


def list_images(folder: str) -> List[str]:
    """Image files directly inside `folder`, in natural name order."""
    try:
        with os.scandir(folder) as entries:
            names = [e.name for e in entries
                     if e.is_file(follow_symlinks=False) and is_image_file(e.name)]
    except (OSError, PermissionError):
        return []
    names.sort(key=natural_sort_key)
    return [os.path.join(folder, n) for n in names]


def list_subfolders(folder: str) -> List[str]:
    """Immediate subdirectories, in natural name order."""
    try:
        with os.scandir(folder) as entries:
            names = [e.name for e in entries if e.is_dir(follow_symlinks=False)]
    except (OSError, PermissionError):
        return []
    names.sort(key=natural_sort_key)
    return [os.path.join(folder, n) for n in names]


def folder_has_images(folder: str) -> bool:
    try:
        with os.scandir(folder) as entries:
            return any(e.is_file(follow_symlinks=False) and is_image_file(e.name)
                       for e in entries)
    except (OSError, PermissionError):
        return False


class ImageNavigator:
    """Cursor over the images in one folder, with folder switching."""

    def __init__(self, path: str):
        if os.path.isdir(path):
            self.folder = os.path.abspath(path)
            self.images = list_images(self.folder)
            self.index = 0 if self.images else -1
        else:
            self.folder = os.path.dirname(os.path.abspath(path))
            self.images = list_images(self.folder)
            target = os.path.abspath(path)
            self.index = next((i for i, p in enumerate(self.images)
                               if os.path.normcase(p) == os.path.normcase(target)), -1)
            if self.index == -1 and self.images:
                self.index = 0

    # ------------------------------------------------------------- current

    @property
    def current(self) -> Optional[str]:
        if 0 <= self.index < len(self.images):
            return self.images[self.index]
        return None

    @property
    def count(self) -> int:
        return len(self.images)

    @property
    def position(self) -> str:
        """Human-readable '3 / 17' (or '0 / 0' for an empty folder)."""
        return f"{self.index + 1 if self.images else 0} / {len(self.images)}"

    # ---------------------------------------------------------- navigation

    def next(self) -> Optional[str]:
        if not self.images:
            return None
        self.index = (self.index + 1) % len(self.images)
        return self.current

    def previous(self) -> Optional[str]:
        if not self.images:
            return None
        self.index = (self.index - 1) % len(self.images)
        return self.current

    def go_to(self, path: str) -> Optional[str]:
        target = os.path.normcase(os.path.abspath(path))
        for i, p in enumerate(self.images):
            if os.path.normcase(p) == target:
                self.index = i
                return self.current
        return None

    # ------------------------------------------------------------- folders

    def subfolders(self) -> List[str]:
        return list_subfolders(self.folder)

    def parent(self) -> Optional[str]:
        parent = os.path.dirname(self.folder.rstrip("\\/"))
        if parent and parent != self.folder and os.path.isdir(parent):
            return parent
        return None

    def open_folder(self, folder: str) -> Optional[str]:
        """Switch to another folder. Returns its first image, or None."""
        if not os.path.isdir(folder):
            return None
        self.folder = os.path.abspath(folder)
        self.images = list_images(self.folder)
        self.index = 0 if self.images else -1
        return self.current
