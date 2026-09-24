"""Shared, filesystem-free query projection over one completed scan.

Categories and extensions use OR within each field. Distinct fields use AND.
Directories remain visible only when they contain matching files. A query
never mutates its scan; callers must discard projections after a rescan.
"""

import os
import threading
from collections import OrderedDict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Callable, Optional

from file_utils import FILE_TYPE_FILTER_LABELS, get_file_category, get_file_category_key, natural_sort_key


@dataclass(frozen=True)
class QuerySpec:
    root_scope: Optional[str] = None
    categories: tuple[str, ...] = ()
    extensions: tuple[str, ...] = ()
    name: str = ""
    min_size: Optional[int] = None
    max_size: Optional[int] = None
    modified_after_ns: Optional[int] = None
    modified_before_ns: Optional[int] = None
    include_hidden: bool = True
    sort: str = "size"
    reverse: bool = True
    metric: str = "logical"
    name_terms: tuple[str, ...] = ()

    def __post_init__(self):
        categories = tuple(sorted(set(self.categories)))
        if any(c not in FILE_TYPE_FILTER_LABELS or c == "all" for c in categories):
            raise ValueError("Unsupported file category")
        extensions = tuple(sorted({"." + ext.lower().lstrip(".") for ext in self.extensions}))
        if any(ext == "." for ext in extensions):
            raise ValueError("Empty file extension")
        for lower, upper in ((self.min_size, self.max_size),
                             (self.modified_after_ns, self.modified_before_ns)):
            if lower is not None and lower < 0 or upper is not None and upper < 0:
                raise ValueError("Size and date bounds must be non-negative")
            if lower is not None and upper is not None and lower > upper:
                raise ValueError("Lower bound exceeds upper bound")
        if self.sort not in ("size", "name", "type", "date"):
            raise ValueError("Unsupported sort order")
        if self.metric not in ("logical", "allocated"):
            raise ValueError("Unsupported size metric")
        object.__setattr__(self, "categories", categories)
        object.__setattr__(self, "extensions", extensions)
        object.__setattr__(self, "name", self.name.casefold().strip())
        object.__setattr__(self, "name_terms", tuple(dict.fromkeys(
            term.casefold().strip() for term in self.name_terms if term.strip())))
        if self.root_scope is not None:
            object.__setattr__(self, "root_scope", os.path.normcase(os.path.normpath(self.root_scope)))

    @classmethod
    def category(cls, key: str) -> "QuerySpec":
        return cls(categories=() if key == "all" else (key,))


def query_from_form(categories=(), extensions="", name="", min_mib="", max_mib="",
                    modified_after="", modified_before="", include_hidden=True) -> QuerySpec:
    """Parse the advanced filter form without involving Tk or the filesystem."""
    def mib(value):
        return int(Decimal(value) * 1048576) if value.strip() else None

    def day(value, end=False):
        if not value.strip():
            return None
        parsed = date.fromisoformat(value.strip())
        if end:
            parsed += timedelta(days=1)
        boundary = int(datetime.combine(parsed, time.min).timestamp() * 1_000_000_000)
        return boundary - 1 if end else boundary

    return QuerySpec(categories=tuple(categories),
                     extensions=tuple(ext.strip() for ext in extensions.replace(";", ",").split(",")
                                      if ext.strip()),
                     name=name, min_size=mib(min_mib), max_size=mib(max_mib),
                     modified_after_ns=day(modified_after),
                     modified_before_ns=day(modified_before, end=True),
                     include_hidden=include_hidden)


class QueryIndex:
    __slots__ = ("root", "scope", "spec", "size_by_node", "count_by_node")

    def __init__(self, root, scope, spec, sizes, counts):
        self.root, self.scope, self.spec = root, scope, spec
        self.size_by_node, self.count_by_node = sizes, counts

    @property
    def filter_key(self) -> str:
        """Compatibility label for existing category-only exports."""
        if (len(self.spec.categories) == 1 and not self.spec.extensions
                and not self.spec.name and self.spec.min_size is None
                and not self.spec.name_terms
                and self.spec.max_size is None and self.spec.modified_after_ns is None
                and self.spec.modified_before_ns is None and self.spec.include_hidden
                and self.spec.root_scope is None and self.spec.metric == "logical"):
            return self.spec.categories[0]
        return "query"

    def _in_scope(self, node) -> bool:
        if self.scope is self.root:
            return True
        while node is not None:
            if node is self.scope:
                return True
            node = node.parent
        return False

    def matches(self, node) -> bool:
        if node.is_dir or not self._in_scope(node):
            return False
        spec = self.spec
        if spec.categories and get_file_category_key(node.name, is_dir=False) not in spec.categories:
            return False
        if spec.extensions and node.ext not in spec.extensions:
            return False
        name = node.name.casefold()
        if spec.name and spec.name not in name:
            return False
        if any(term not in name for term in spec.name_terms):
            return False
        if not spec.include_hidden:
            current = node
            while current is not None and current is not self.scope.parent:
                if current.name.startswith("."):
                    return False
                current = current.parent
        size = node.size if spec.metric == "logical" else node.allocated_size
        if size is None:
            raise ValueError("Allocated size unavailable; rescan with extended metadata")
        if spec.min_size is not None and size < spec.min_size:
            return False
        if spec.max_size is not None and size > spec.max_size:
            return False
        mtime = node.mtime_ns
        if spec.modified_after_ns is not None and mtime < spec.modified_after_ns:
            return False
        if spec.modified_before_ns is not None and (not mtime or mtime > spec.modified_before_ns):
            return False
        return True

    def size(self, node) -> int:
        if getattr(node, "is_aggregate", False):
            return node.size
        if node.is_dir:
            return self.size_by_node.get(node, 0)
        if not self.matches(node):
            return 0
        return node.size if self.spec.metric == "logical" else node.allocated_size

    def count(self, node) -> int:
        if getattr(node, "is_aggregate", False):
            return node.item_count
        if node.is_dir:
            return self.count_by_node.get(node, 0)
        return 1 if self.matches(node) else 0

    def children(self, node, should_cancel: Optional[Callable[[], bool]] = None) -> list:
        if not node.is_dir or not self._in_scope(node):
            return []
        visible = []
        for position, child in enumerate(node.children):
            if should_cancel is not None and position % 256 == 0 and should_cancel():
                return []
            if self.count(child) > 0:
                visible.append(child)
        return visible

    def sorted_children(self, node, key: Optional[str] = None,
                        reverse: Optional[bool] = None,
                        should_cancel: Optional[Callable[[], bool]] = None) -> list:
        key = key or self.spec.sort
        reverse = self.spec.reverse if reverse is None else reverse
        children = self.children(node, should_cancel)
        if key == "name":
            value = lambda n: natural_sort_key(n.name)
        elif key == "date":
            value = lambda n: n.creation_date
        elif key == "type":
            value = lambda n: (not n.is_dir, "" if n.is_dir else
                               get_file_category(n.name, is_dir=False)["label"], n.name.casefold())
        else:
            value = self.size
        return sorted(children, key=value, reverse=reverse)


class QueryEngine:
    """A bounded projection cache tied to exactly one immutable scan tree."""

    def __init__(self, root, max_cache_entries: int = 3,
                 max_cached_directories: int = 250_000):
        self.root = root
        self.max_cache_entries = max_cache_entries
        self.max_cached_directories = max_cached_directories
        self._cache: OrderedDict[QuerySpec, QueryIndex] = OrderedDict()
        self._lock = threading.Lock()

    def _scope(self, spec: QuerySpec):
        if spec.root_scope is None:
            return self.root
        stack = [self.root]
        while stack:
            node = stack.pop()
            if os.path.normcase(os.path.normpath(node.path)) == spec.root_scope:
                return node
            stack.extend(child for child in node.children if child.is_dir)
        raise ValueError("Scope is outside this completed scan")

    def project(self, spec: QuerySpec,
                should_cancel: Optional[Callable[[], bool]] = None) -> Optional[QueryIndex]:
        if should_cancel and should_cancel():
            return None
        with self._lock:
            if spec in self._cache:
                self._cache.move_to_end(spec)
                return self._cache[spec]
        scope = self._scope(spec)
        sizes, counts = {}, {}
        result = QueryIndex(self.root, scope, spec, sizes, counts)
        stack = [(scope, False)]
        while stack:
            if should_cancel and should_cancel():
                return None
            node, processed = stack.pop()
            if not node.is_dir:
                continue
            if not processed:
                stack.append((node, True))
                stack.extend((child, False) for child in node.children if child.is_dir)
                continue
            total_size = total_count = 0
            for index, child in enumerate(node.children):
                if should_cancel and index % 256 == 0 and should_cancel():
                    return None
                if child.is_dir:
                    total_size += sizes.get(child, 0)
                    total_count += counts.get(child, 0)
                elif result.matches(child):
                    total_size += child.size if spec.metric == "logical" else child.allocated_size
                    total_count += 1
            sizes[node], counts[node] = total_size, total_count
        if should_cancel and should_cancel():
            return None
        if self.max_cache_entries > 0 and len(sizes) <= self.max_cached_directories:
            with self._lock:
                while self._cache and (len(self._cache) >= self.max_cache_entries or
                                       sum(len(item.size_by_node) for item in self._cache.values()) +
                                       len(sizes) > self.max_cached_directories):
                    self._cache.popitem(last=False)
                self._cache[spec] = result
        return result
