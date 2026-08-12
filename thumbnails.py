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


def bucket_size(size: Tuple[int, int]) -> Tuple[int, int]:
    """Round a requested size up to the next cache bucket.

    Tiles are all slightly different sizes and every window resize changes
    them again. Keying the cache on the exact request meant re-decoding every
    image on every resize; snapping to a handful of buckets means a resize
    almost always reuses what has already been decoded, and the extra pixels
    are thrown away by the downscale that follows.
    """
    want = max(size[0], size[1], 1)
    for bucket in SIZE_BUCKETS:
        if want <= bucket:
            return (bucket, bucket)
    return (SIZE_BUCKETS[-1], SIZE_BUCKETS[-1])


SIZE_BUCKETS = (32, 64, 128, 256, 512)


class ThumbnailCache:
    """Decoded thumbnails, bounded by total pixels rather than entry count:
    a handful of 512px thumbnails cost far more than many 32px ones."""

    def __init__(self, pixel_budget: int = 24_000_000, workers: int = 3):
        self.pixel_budget = pixel_budget
        self._cache: "OrderedDict[CacheKey, Optional[Image.Image]]" = OrderedDict()
        self._pixels = 0
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
        key = self._key(path, bucket_size(size), self._mtime(path))
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

        bucket = bucket_size(size)
        cached = self.get(path, bucket)
        if cached is not None:
            return cached

        key = self._key(path, bucket, self._mtime(path))
        with self._lock:
            if key in self._cache or key in self._pending:
                return None
            self._pending.add(key)

        try:
            self._executor.submit(self._load, key, path, bucket)
        except RuntimeError:      # executor already shut down
            with self._lock:
                self._pending.discard(key)
        return None

    def _load(self, key: CacheKey, path: str, size: Tuple[int, int]):
        image = None
        try:
            with Image.open(path) as src:
                # draft() lets the JPEG decoder skip most of the work when we
                # only need a small thumbnail: decode at 1/2, 1/4 or 1/8 scale
                # instead of full resolution and then shrinking.
                try:
                    src.draft("RGB", size)
                except Exception:
                    pass
                src = ImageOps.exif_transpose(src)
                src.thumbnail(size, Image.Resampling.LANCZOS)
                image = src.convert("RGB")
        except Exception:
            image = None          # unreadable/corrupt: cache the miss
        finally:
            with self._lock:
                self._pending.discard(key)
                self._store(key, image)

        if image is not None and self._on_ready and not self._closed:
            try:
                self._on_ready(path)
            except Exception:
                pass

    def _store(self, key: CacheKey, image: Optional[Image.Image]):
        """Insert and evict down to the pixel budget. Caller holds the lock."""
        old = self._cache.pop(key, None)
        if old is not None:
            self._pixels -= old.width * old.height
        self._cache[key] = image
        if image is not None:
            self._pixels += image.width * image.height

        while self._pixels > self.pixel_budget and len(self._cache) > 1:
            _, evicted = self._cache.popitem(last=False)
            if evicted is not None:
                self._pixels -= evicted.width * evicted.height

    @property
    def pixels(self) -> int:
        return self._pixels

    def clear(self):
        with self._lock:
            self._cache.clear()
            self._pixels = 0

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
