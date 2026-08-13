"""Well-known places to scan, and path breadcrumbs.

The app used to open on an empty pane with nothing but a "select a folder"
line, which asks the user to go hunting through a file dialog before they can
see anything. These helpers back a start screen that offers the folders people
actually want to look at, with the space each one has left.

Filesystem lookups only, so it can be tested without a display.
"""
import os
import shutil
import string
import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple


@dataclass
class Place:
    label: str
    path: str
    icon: str
    is_drive: bool = False
    total: int = 0
    free: int = 0

    @property
    def used(self) -> int:
        return max(0, self.total - self.free)

    @property
    def used_fraction(self) -> float:
        return (self.used / self.total) if self.total else 0.0


# (label, icon, environment-independent subfolder of the home directory)
HOME_FOLDERS: Tuple[Tuple[str, str, Optional[str]], ...] = (
    ("Home", "🏠", None),
    ("Desktop", "🖥️", "Desktop"),
    ("Downloads", "⬇️", "Downloads"),
    ("Documents", "📄", "Documents"),
    ("Pictures", "🖼️", "Pictures"),
    ("Videos", "🎬", "Videos"),
    ("Music", "🎵", "Music"),
)


def _usage(path: str) -> Tuple[int, int]:
    try:
        usage = shutil.disk_usage(path)
        return usage.total, usage.free
    except OSError:
        return 0, 0


def home_places(include_usage: bool = False) -> List[Place]:
    """Standard user folders that actually exist on this machine."""
    home = os.path.expanduser("~")
    places: List[Place] = []
    for label, icon, sub in HOME_FOLDERS:
        path = home if sub is None else os.path.join(home, sub)
        if not os.path.isdir(path):
            continue
        total, free = _usage(path) if include_usage else (0, 0)
        places.append(Place(label=label, path=path, icon=icon, total=total, free=free))
    return places


def drives() -> List[Place]:
    """Mounted drives (Windows) or filesystem roots (elsewhere)."""
    found: List[Place] = []
    if sys.platform == "win32":
        for letter in string.ascii_uppercase:
            root = f"{letter}:\\"
            if not os.path.exists(root):
                continue
            total, free = _usage(root)
            found.append(Place(label=f"{letter}:", path=root, icon="💽",
                               is_drive=True, total=total, free=free))
    else:
        for root in ("/",):
            if os.path.isdir(root):
                total, free = _usage(root)
                found.append(Place(label="System", path=root, icon="💽",
                                   is_drive=True, total=total, free=free))
    return found


def start_places() -> List[Place]:
    """Everything worth offering on the start screen, drives last."""
    return home_places(include_usage=True) + drives()


def breadcrumbs(path: str) -> List[Tuple[str, str]]:
    """Split a path into (label, path) pairs, outermost first.

    Used for a clickable path bar: every crumb is somewhere you can jump to,
    which beats retyping or walking up one level at a time.
    """
    if not path:
        return []

    path = os.path.abspath(path)
    drive, remainder = os.path.splitdrive(path)
    parts = [p for p in remainder.replace("\\", "/").split("/") if p]

    crumbs: List[Tuple[str, str]] = []
    if drive:                                   # "C:" -> "C:\"
        root = drive + os.sep
        crumbs.append((drive, root))
        current = root
    else:
        crumbs.append((os.sep, os.sep))
        current = os.sep

    for part in parts:
        current = os.path.join(current, part)
        crumbs.append((part, current))
    return crumbs


def shorten_middle(crumbs: List[Tuple[str, str]], keep: int = 4) -> List[Optional[Tuple[str, str]]]:
    """Collapse a deep path, keeping the root and the last few segments.

    A None in the returned list marks where the elision happened.
    """
    if len(crumbs) <= keep + 1:
        return list(crumbs)
    return [crumbs[0], None] + list(crumbs[-keep:])
