"""Move files to the Recycle Bin instead of deleting them outright.

A disk cleaner that can only delete permanently is a sharp tool to hand
someone who is skimming a list of files. On Windows this uses the shell's own
undo-able delete; elsewhere it follows the XDG trash spec well enough for the
desktop to show the files in its bin.

`send_to_trash` never raises: it reports whether the file made it to the bin,
and the caller decides whether to fall back to a permanent delete.
"""
import os
import shutil
import sys
import time
from typing import Tuple
from urllib.parse import quote


def is_supported() -> bool:
    if sys.platform == "win32":
        return True
    return bool(_xdg_trash_dir())


def send_to_trash(path: str) -> Tuple[bool, str]:
    """Try to move `path` to the recycle bin.

    Returns (moved, message). A False result is not an error the caller must
    surface: it means the bin was unavailable and a permanent delete is the
    only remaining option.
    """
    if not os.path.exists(path):
        return False, "File not found"

    if sys.platform == "win32":
        return _windows_recycle(os.path.abspath(path))
    return _xdg_trash(os.path.abspath(path))


# --------------------------------------------------------------------- windows

def _windows_recycle(path: str) -> Tuple[bool, str]:
    import ctypes
    from ctypes import wintypes

    FO_DELETE = 0x0003
    FOF_ALLOWUNDO = 0x0040
    FOF_NOCONFIRMATION = 0x0010
    FOF_NOERRORUI = 0x0400
    FOF_SILENT = 0x0004

    class SHFILEOPSTRUCTW(ctypes.Structure):
        _fields_ = [
            ("hwnd", wintypes.HWND),
            ("wFunc", wintypes.UINT),
            ("pFrom", wintypes.LPCWSTR),
            ("pTo", wintypes.LPCWSTR),
            ("fFlags", ctypes.c_uint16),
            ("fAnyOperationsAborted", wintypes.BOOL),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", wintypes.LPCWSTR),
        ]

    # the path list is double-NUL terminated
    op = SHFILEOPSTRUCTW(
        hwnd=None,
        wFunc=FO_DELETE,
        pFrom=path + "\0\0",
        pTo=None,
        fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_NOERRORUI | FOF_SILENT,
        fAnyOperationsAborted=False,
        hNameMappings=None,
        lpszProgressTitle=None,
    )
    try:
        result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    except Exception as exc:                       # pragma: no cover - windows only
        return False, f"Recycle Bin unavailable: {exc}"

    if result != 0:
        return False, f"Recycle Bin refused the file (code {result})"
    if op.fAnyOperationsAborted:
        return False, "Cancelled"
    return True, "Moved to Recycle Bin"


# ------------------------------------------------------------------------ xdg

def _xdg_trash_dir() -> str:
    home = os.path.expanduser("~")
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(home, ".local", "share")
    trash = os.path.join(base, "Trash")
    return trash if os.path.isdir(os.path.dirname(trash)) else ""


def _unique_name(folder: str, name: str) -> str:
    candidate = name
    stem, ext = os.path.splitext(name)
    counter = 1
    while os.path.exists(os.path.join(folder, candidate)):
        candidate = f"{stem}.{counter}{ext}"
        counter += 1
    return candidate


def _xdg_trash(path: str) -> Tuple[bool, str]:
    trash = _xdg_trash_dir()
    if not trash:
        return False, "No trash directory available"

    files_dir = os.path.join(trash, "files")
    info_dir = os.path.join(trash, "info")
    try:
        os.makedirs(files_dir, exist_ok=True)
        os.makedirs(info_dir, exist_ok=True)

        name = _unique_name(files_dir, os.path.basename(path))
        with open(os.path.join(info_dir, name + ".trashinfo"), "w", encoding="utf-8") as f:
            f.write("[Trash Info]\n")
            f.write(f"Path={quote(path)}\n")
            f.write(f"DeletionDate={time.strftime('%Y-%m-%dT%H:%M:%S')}\n")

        shutil.move(path, os.path.join(files_dir, name))
        return True, "Moved to Trash"
    except OSError as exc:
        return False, f"Trash unavailable: {exc}"
