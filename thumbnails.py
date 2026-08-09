"""Background thumbnail loading with an LRU cache.

Decoding images must never happen on the UI thread, and the treemap can ask
for hundreds of thumbnails at once, so requests are queued onto a small pool
of workers and results are cached. Callers poll `get()` (cheap, non-blocking)
and are told when new thumbnails land so they can redraw.
"""
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Optional, Tuple

from PIL import Image, ImageOps

from file_utils import is_image_file

# Pillow raises DecompressionBombWarning / errors on absurd images; keep a
# generous but finite ceiling so a malformed file can't exhaust memory.
Image.MAX_IMAGE_PIXELS = 200_000_000

CacheKey = Tuple[str, int, int, float]


class ThumbnailCache:
    def __init__(self, max_items: int = 600, workers: int = 3):
        self.max_items = max_items
        self._cache: "OrderedDict[CacheKey, Optional[Image.Image]]" = OrderedDict()
        self._pending: set = set()
        self._lock = threading.Lock()
        self._executor = ThreadPoolExecutor(max_workers=workers,
                                            thread_name_prefix="thumb")
        self._on_ready: Optional[Callable[[str], None]] = None
        self._closed = False

    def set_ready_callback(self, callback: Optional[Callable[[str], None]]):
        """Called (on a worker thread) whenever a new thumbnail is available."""
        self._on_ready = callback

    @staticmethod
    def _key(path: str, size: Tuple[int, int], mtime: float) -> CacheKey:
        return (path, size[0], size[1], mtime)

    @staticmethod
    def _mtime(path: str) -> float:
        try:
            return os.path.getmtime(path)
        except OSError:
            return 0.0

    def get(self, path: str, size: Tuple[int, int]) -> Optional[Image.Image]:
        """Return a cached thumbnail, or None. Never blocks, never decodes."""
        key = self._key(path, size, self._mtime(path))
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
        return None

    def request(self, path: str, size: Tuple[int, int]) -> Optional[Image.Image]:
        """Return the thumbnail if ready, otherwise schedule a load and return
        None. Safe to call repeatedly: duplicate work is suppressed."""
        if self._closed or not is_image_file(path):
            return None

        cached = self.get(path, size)
        if cached is not None:
            return cached

        key = self._key(path, size, self._mtime(path))
        with self._lock:
            if key in self._cache or key in self._pending:
                return None
            self._pending.add(key)

        try:
            self._executor.submit(self._load, key, path, size)
        except RuntimeError:      # executor already shut down
            with self._lock:
                self._pending.discard(key)
        return None

    def _load(self, key: CacheKey, path: str, size: Tuple[int, int]):
        image = None
        try:
            with Image.open(path) as src:
                src = ImageOps.exif_transpose(src)
                src.thumbnail(size, Image.Resampling.LANCZOS)
                image = src.convert("RGB")
        except Exception:
            image = None          # unreadable/corrupt: cache the miss
        finally:
            with self._lock:
                self._pending.discard(key)
                self._cache[key] = image
                self._cache.move_to_end(key)
                while len(self._cache) > self.max_items:
                    self._cache.popitem(last=False)

        if image is not None and self._on_ready and not self._closed:
            try:
                self._on_ready(path)
            except Exception:
                pass

    def clear(self):
        with self._lock:
            self._cache.clear()

    def close(self):
        self._closed = True
        self._on_ready = None
        self._executor.shutdown(wait=False)


def fit_box(src_w: int, src_h: int, box_w: int, box_h: int) -> Tuple[int, int]:
    """Largest size fitting inside the box while preserving aspect ratio."""
    if src_w <= 0 or src_h <= 0 or box_w <= 0 or box_h <= 0:
        return (0, 0)
    scale = min(box_w / src_w, box_h / src_h)
    return (max(1, int(src_w * scale)), max(1, int(src_h * scale)))


def cover_box(src_w: int, src_h: int, box_w: int, box_h: int) -> Tuple[int, int]:
    """Smallest size that fully covers the box, preserving aspect ratio."""
    if src_w <= 0 or src_h <= 0 or box_w <= 0 or box_h <= 0:
        return (0, 0)
    scale = max(box_w / src_w, box_h / src_h)
    return (max(1, int(src_w * scale)), max(1, int(src_h * scale)))
