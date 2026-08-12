"""Duplicate file detection over an already-scanned tree.

Hashing every file would be far too slow on a real drive, so this narrows the
candidates in three stages, each cheaper than the one after it:

  1. group by exact size   - no I/O at all, and most files are unique by size
  2. hash a small head/tail sample - one short read, splits nearly all the rest
  3. hash the full contents - only for files that still look identical

Pure logic with injectable progress/cancel, so it is testable without a UI.
"""
import hashlib
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

SAMPLE_BYTES = 65536          # head+tail sample used for the cheap pass
CHUNK = 1 << 20               # 1 MiB streaming reads for the full hash


@dataclass
class DuplicateGroup:
    size: int
    nodes: List[object] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.nodes)

    @property
    def wasted(self) -> int:
        """Space that would be freed by keeping a single copy."""
        return self.size * max(0, self.count - 1)


def sample_digest(path: str, sample: int = SAMPLE_BYTES) -> Optional[str]:
    """Hash of the first and last `sample` bytes, plus the size.

    Cheap enough to run on every same-size candidate, and files that differ
    almost always differ near one end.
    """
    try:
        size = os.path.getsize(path)
        h = hashlib.blake2b(digest_size=16)
        h.update(str(size).encode())
        with open(path, "rb") as f:
            h.update(f.read(sample))
            if size > sample * 2:
                f.seek(-sample, os.SEEK_END)
                h.update(f.read(sample))
        return h.hexdigest()
    except OSError:
        return None


def full_digest(path: str) -> Optional[str]:
    try:
        h = hashlib.blake2b(digest_size=16)
        with open(path, "rb") as f:
            for block in iter(lambda: f.read(CHUNK), b""):
                h.update(block)
        return h.hexdigest()
    except OSError:
        return None


def _group_by(items, key_func, should_cancel) -> List[List]:
    """Bucket items by a key, keeping only buckets with more than one member."""
    buckets: Dict[object, List] = {}
    for item in items:
        if should_cancel and should_cancel():
            return []
        key = key_func(item)
        if key is None:          # unreadable: cannot be proven a duplicate
            continue
        buckets.setdefault(key, []).append(item)
    return [group for group in buckets.values() if len(group) > 1]


def find_duplicates(root, min_size: int = 1,
                    progress: Optional[Callable[[str, int, int], None]] = None,
                    should_cancel: Optional[Callable[[], bool]] = None
                    ) -> List[DuplicateGroup]:
    """Find groups of byte-identical files, biggest waste first.

    `min_size` skips small files, where duplicates are common and reclaiming
    them is not worth the read.
    """
    from analysis import iter_file_nodes

    files = [n for n in iter_file_nodes(root) if n.size >= min_size]
    if progress:
        progress("Grouping by size", 0, len(files))

    by_size = _group_by(files, lambda n: n.size, should_cancel)
    if should_cancel and should_cancel():
        return []

    candidates = [n for group in by_size for n in group]
    if progress:
        progress("Sampling candidates", 0, len(candidates))

    groups: List[DuplicateGroup] = []
    done = 0
    for same_size in by_size:
        if should_cancel and should_cancel():
            return []

        for sampled in _group_by(same_size, lambda n: sample_digest(n.path), should_cancel):
            if should_cancel and should_cancel():
                return []
            # a full hash is only needed when the sample already matched
            for identical in _group_by(sampled, lambda n: full_digest(n.path), should_cancel):
                groups.append(DuplicateGroup(size=identical[0].size, nodes=identical))

        done += len(same_size)
        if progress:
            progress("Hashing", done, len(candidates))

    groups.sort(key=lambda g: g.wasted, reverse=True)
    return groups


def total_wasted(groups: List[DuplicateGroup]) -> int:
    return sum(g.wasted for g in groups)


def keep_first_delete_rest(group: DuplicateGroup) -> List[object]:
    """The copies that can go: everything but the shortest path.

    The shortest path is usually the original rather than a 'copy (2)' of it.
    """
    ordered = sorted(group.nodes, key=lambda n: (len(n.path), n.path))
    return ordered[1:]
