"""Pure analysis helpers over a scanned Node tree.

Everything here is side-effect free (except CSV/JSON export) and unit tested, so
the UI layer can stay thin. Features inspired by WinDirStat / TreeSize /
SpaceSniffer: a squarified treemap layout, largest-files ranking, and a
file-type / extension breakdown.
"""
import csv
import json
from dataclasses import dataclass
from typing import Callable, Iterator, List, Dict, Tuple, Optional

from file_utils import (
    FILE_TYPE_FILTER_LABELS,
    cancellable_sorted,
    file_type_matches,
    get_file_category,
    get_file_extension,
    format_size,
)
from query import QueryEngine, QueryIndex, QuerySpec


# --------------------------------------------------------------------- walking

def iter_file_nodes(root, predicate: Optional[Callable] = None,
                    should_cancel: Optional[Callable[[], bool]] = None) -> Iterator:
    """Yield non-directory Nodes in the subtree (iterative).

    ``predicate`` is deliberately applied to the in-memory node, so filtered
    views never need to touch the filesystem again.
    """
    # Keep one iterator per depth instead of copying every sibling reference
    # into a second list. A flat million-entry directory otherwise doubles
    # pointer storage just to rank a bounded top-K result.
    stack = [iter((root,))]
    visited = 0
    while stack:
        if should_cancel is not None and visited % 256 == 0 and should_cancel():
            return
        try:
            node = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        visited += 1
        if node.is_dir:
            stack.append(reversed(node.children))
        elif predicate is None or predicate(node):
            yield node


def iter_all_nodes(root, should_cancel: Optional[Callable[[], bool]] = None) -> Iterator:
    """Yield every Node in the subtree including directories (excluding root)."""
    stack = [reversed(root.children)]
    visited = 0
    while stack:
        if should_cancel is not None and visited % 256 == 0 and should_cancel():
            return
        try:
            node = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        visited += 1
        yield node
        if node.is_dir:
            stack.append(reversed(node.children))


# ------------------------------------------------------------- largest files

def largest_files(root, limit: int = 100, filter_key: str = "all",
                  filter_index=None, name_query: str = "",
                  should_cancel: Optional[Callable[[], bool]] = None) -> List:
    """Return the `limit` largest matching files, biggest first."""
    if limit <= 0:
        return []
    predicate = None
    if filter_index is not None or filter_key != "all" or name_query:
        category_match = (filter_index.matches if filter_index is not None else
                          ((lambda node: True) if filter_key == "all" else
                           (lambda node: file_type_matches(node.name, filter_key, is_dir=False))))
        predicate = lambda node: category_match(node) and match_query(node.name, name_query)

    # A bounded heap avoids retaining every file in memory just to find the
    # top 100 on a large drive.  nlargest still returns largest-first.
    from heapq import nlargest
    return nlargest(limit, iter_file_nodes(root, predicate, should_cancel),
                    key=filter_index.size if filter_index is not None else lambda n: n.size)


# --------------------------------------------------------- type breakdown

@dataclass
class CategoryStat:
    label: str
    color: str
    size: int
    count: int
    percent: float = 0.0


def category_breakdown(root, filter_key: str = "all", filter_index=None,
                       should_cancel: Optional[Callable[[], bool]] = None) -> List[CategoryStat]:
    """Aggregate total size and file count per file category, largest first."""
    totals: Dict[str, List[int]] = {}
    predicate = None
    if filter_index is not None or filter_key != "all":
        predicate = filter_index.matches if filter_index is not None else \
            (lambda node: file_type_matches(node.name, filter_key, is_dir=False))
    for node in iter_file_nodes(root, predicate, should_cancel):
        cat = get_file_category(node.name, is_dir=False)
        label = cat['label']
        entry = totals.setdefault(label, [0, 0, cat['color']])
        entry[0] += filter_index.size(node) if filter_index is not None else node.size
        entry[1] += 1

    total_size = sum(v[0] for v in totals.values()) or 1
    stats = [
        CategoryStat(label=label, color=vals[2], size=vals[0], count=vals[1],
                     percent=vals[0] / total_size * 100)
        for label, vals in totals.items()
    ]
    stats.sort(key=lambda s: s.size, reverse=True)
    return stats


def extension_breakdown(root, limit: int = 15, filter_key: str = "all",
                        filter_index=None) -> List[Tuple[str, int, int]]:
    """Return (extension, total_size, count) tuples, largest first."""
    totals: Dict[str, List[int]] = {}
    predicate = None
    if filter_index is not None or filter_key != "all":
        predicate = filter_index.matches if filter_index is not None else \
            (lambda node: file_type_matches(node.name, filter_key, is_dir=False))
    for node in iter_file_nodes(root, predicate):
        ext = get_file_extension(node.name, is_dir=False)
        entry = totals.setdefault(ext, [0, 0])
        entry[0] += filter_index.size(node) if filter_index is not None else node.size
        entry[1] += 1
    rows = [(ext, vals[0], vals[1]) for ext, vals in totals.items()]
    rows.sort(key=lambda r: r[1], reverse=True)
    return rows[:limit]


# ---------------------------------------------------------- age breakdown

# Upper bound in days for each bucket, newest first.  Last-modified time is
# used because Windows does not reliably maintain last-access times.
AGE_BUCKETS = (
    ("Last 30 days", 30),
    ("1–6 months", 182),
    ("6–12 months", 365),
    ("1–3 years", 3 * 365),
    ("Older than 3 years", None),
)
UNKNOWN_AGE = "Unknown date"
_DAY_NS = 86_400 * 1_000_000_000


@dataclass
class AgeStat:
    label: str
    size: int
    count: int
    percent: float = 0.0


def age_breakdown(root, filter_key: str = "all", filter_index=None,
                  now_ns: Optional[int] = None,
                  should_cancel: Optional[Callable[[], bool]] = None) -> List[AgeStat]:
    """Group matching files by how long ago they were last modified.

    Buckets keep their fixed newest-to-oldest order and empty buckets are
    omitted.  Files without a usable timestamp are counted as unknown rather
    than being placed in the oldest bucket.
    """
    import time as _time
    now_ns = _time.time_ns() if now_ns is None else now_ns
    labels = [label for label, _days in AGE_BUCKETS] + [UNKNOWN_AGE]
    totals = {label: [0, 0] for label in labels}
    predicate = None
    if filter_index is not None or filter_key != "all":
        predicate = filter_index.matches if filter_index is not None else \
            (lambda node: file_type_matches(node.name, filter_key, is_dir=False))
    for node in iter_file_nodes(root, predicate, should_cancel):
        mtime = int(getattr(node, "mtime_ns", 0) or 0)
        if mtime <= 0:
            label = UNKNOWN_AGE
        else:
            age_days = max(0, now_ns - mtime) / _DAY_NS
            label = next(bucket for bucket, days in AGE_BUCKETS
                         if days is None or age_days < days)
        entry = totals[label]
        entry[0] += filter_index.size(node) if filter_index is not None else node.size
        entry[1] += 1
    total_size = sum(size for size, _count in totals.values()) or 1
    return [AgeStat(label, size, count, size / total_size * 100)
            for label in labels
            for size, count in (totals[label],) if count]


# ------------------------------------------------------- storage accounting

@dataclass
class StorageSummary:
    """Logical versus on-disk totals for one scanned tree.

    ``allocated_bytes`` sums the on-disk size each path reports and is
    ``None`` when no file reported one.  ``unique_allocated_bytes`` counts
    each hardlinked file once; it is ``None`` whenever a hardlinked file's
    identity is unknown, because a unique total would then be a guess.
    Both only cover files inside this scan.
    """
    logical_bytes: int
    files: int
    allocated_bytes: Optional[int]
    unique_allocated_bytes: Optional[int]
    unknown_allocation: int
    hardlinked_files: int
    reparse_points: int
    inaccessible: int


def storage_summary(root, should_cancel: Optional[Callable[[], bool]] = None) -> StorageSummary:
    logical = files = allocated = unique = unknown = hardlinked = reparse = inaccessible = 0
    identity_unknown = False
    seen_identities = set()
    for node in iter_all_nodes(root, should_cancel):
        if getattr(node, "is_reparse_point", False):
            reparse += 1
        if node.is_dir:
            if node.error:
                inaccessible += 1
            continue
        files += 1
        logical += node.size
        size = getattr(node, "allocated_size", None)
        links = getattr(node, "link_count", None)
        identity = getattr(node, "file_identity", None)
        if links is not None and links > 1:
            hardlinked += 1
            if identity is None:
                identity_unknown = True
        if size is None:
            unknown += 1
            continue
        allocated += size
        if identity is not None:
            if identity in seen_identities:
                continue
            seen_identities.add(identity)
        unique += size
    has_allocation = files > unknown
    return StorageSummary(
        logical_bytes=logical, files=files,
        allocated_bytes=allocated if has_allocation else None,
        unique_allocated_bytes=(unique if has_allocation and not unknown
                                and not identity_unknown else None),
        unknown_allocation=unknown, hardlinked_files=hardlinked,
        reparse_points=reparse, inaccessible=inaccessible)


# --------------------------------------------------------------- treemap

@dataclass
class Tile:
    node: object
    x: float
    y: float
    w: float
    h: float
    depth: int


@dataclass
class TreemapAggregate:
    """A lightweight tile representing children too small to show separately."""

    name: str
    size: int
    item_count: int
    parent: object
    is_dir: bool = False
    creation_date: float = 0.0
    modified_date: int = 0
    error: Optional[str] = None
    is_aggregate: bool = True
    category_key: str = "other"
    omitted_start: int = 0

    @property
    def path(self) -> str:
        # There is no single real path for an aggregate.  The parent path is
        # still useful in the tooltip and avoids manufacturing a path that
        # could accidentally be opened or deleted.
        return self.parent.path if self.parent is not None else self.name

    @property
    def ext(self) -> str:
        return ""


# Keep the public category API while the GUI migrates to the shared engine.
FilterIndex = QueryIndex


def build_filter_index(root, filter_key: str,
                       should_cancel: Optional[Callable[[], bool]] = None) -> Optional[QueryIndex]:
    """Project a single category from an already completed scan."""
    if filter_key not in FILE_TYPE_FILTER_LABELS or filter_key == "all":
        raise ValueError(f"Unsupported file filter: {filter_key}")
    return QueryEngine(root).project(QuerySpec.category(filter_key), should_cancel)


def _normalize(sizes: List[float], area: float) -> List[float]:
    total = sum(sizes)
    if total <= 0:
        return [0.0 for _ in sizes]
    return [s * area / total for s in sizes]


def _layout_row(sizes, x, y, dx, dy, horizontal):
    """Place a run of tiles either down a column (horizontal=True) or across."""
    rects = []
    covered = sum(sizes)
    if horizontal:
        width = covered / dy if dy else 0
        cy = y
        for s in sizes:
            h = s / width if width else 0
            rects.append((x, cy, width, h))
            cy += h
    else:
        height = covered / dx if dx else 0
        cx = x
        for s in sizes:
            w = s / height if height else 0
            rects.append((cx, y, w, height))
            cx += w
    return rects


def _worst(sizes, side: float) -> float:
    """Aspect-ratio score for a row, computed without constructing rectangles."""
    if not sizes or side <= 0:
        return float('inf')
    total = sum(sizes)
    smallest = min(sizes)
    largest = max(sizes)
    return _worst_metrics(total, smallest, largest, side)


def _worst_metrics(total: float, smallest: float, largest: float, side: float) -> float:
    """Aspect-ratio score from maintained row metrics (constant time)."""
    if total <= 0 or smallest <= 0:
        return float('inf')
    side_squared = side * side
    total_squared = total * total
    return max(
        side_squared * largest / total_squared,
        total_squared / (side_squared * smallest),
    )


def squarify(sizes: List[float], x: float, y: float, dx: float, dy: float) -> List[Tuple[float, float, float, float]]:
    """Squarified treemap (Bruls, Huizing & van Wijk).

    `sizes` are raw weights; they are normalized to the given rectangle's area.
    Returns rectangles (x, y, w, h) in the same order as `sizes`.
    """
    raw_sizes = [max(0.0, float(s)) for s in sizes]
    result: List[Optional[Tuple[float, float, float, float]]] = [None] * len(raw_sizes)
    if not raw_sizes or dx <= 0 or dy <= 0:
        return [(x, y, 0.0, 0.0) for _ in raw_sizes]

    normalized = _normalize(raw_sizes, dx * dy)
    order = [i for i, value in enumerate(normalized) if value > 0]
    order.sort(key=lambda i: normalized[i], reverse=True)
    if not order:
        return [(x, y, 0.0, 0.0) for _ in raw_sizes]

    position = 0
    cursor_x, cursor_y = x, y
    remaining_dx, remaining_dy = dx, dy
    while position < len(order) and remaining_dx > 0 and remaining_dy > 0:
        horizontal = remaining_dx >= remaining_dy
        side = remaining_dy if horizontal else remaining_dx
        first = order[position]
        position += 1
        row = [first]
        first_size = normalized[first]
        row_sizes = [first_size]
        row_total = first_size
        row_smallest = first_size
        row_largest = first_size
        row_worst = _worst_metrics(row_total, row_smallest, row_largest, side)

        while position < len(order):
            candidate_index = order[position]
            candidate_size = normalized[candidate_index]
            candidate_worst = _worst_metrics(
                row_total + candidate_size,
                min(row_smallest, candidate_size),
                max(row_largest, candidate_size),
                side,
            )
            if candidate_worst <= row_worst:
                position += 1
                row.append(candidate_index)
                row_sizes.append(candidate_size)
                row_total += candidate_size
                row_smallest = min(row_smallest, candidate_size)
                row_largest = max(row_largest, candidate_size)
                row_worst = candidate_worst
            else:
                break

        rects = _layout_row(row_sizes, cursor_x, cursor_y,
                            remaining_dx, remaining_dy, horizontal)
        for index, rect in zip(row, rects):
            result[index] = rect

        covered = sum(row_sizes)
        if horizontal:
            strip_width = covered / remaining_dy if remaining_dy else 0.0
            cursor_x += strip_width
            remaining_dx = max(0.0, remaining_dx - strip_width)
        else:
            strip_height = covered / remaining_dx if remaining_dx else 0.0
            cursor_y += strip_height
            remaining_dy = max(0.0, remaining_dy - strip_height)

    return [r if r is not None else (x, y, 0.0, 0.0) for r in result]


def build_treemap(root, x: float, y: float, width: float, height: float,
                  min_area: float = 90.0, max_depth: int = 6, padding: float = 1.0,
                  header: float = 0.0,
                  size_getter: Optional[Callable] = None,
                  children_getter: Optional[Callable] = None,
                  count_getter: Optional[Callable] = None,
                  max_children: int = 1200,
                  aggregate_category: Optional[str] = None,
                  should_cancel: Optional[Callable[[], bool]] = None) -> List[Tile]:
    """Build treemap tiles for a Node.

    Recurses into directories only while their tile is large enough
    (area >= min_area) and depth allows, so the tile count stays bounded and
    the canvas stays responsive on huge trees.

    `header` reserves a strip along the top of a directory's tile before its
    children are laid out, giving the folder somewhere to put its own name.
    Without it a folder's label lands on top of its first child's label.
    """
    get_size = size_getter or (lambda node: node.size)
    get_children = children_getter or (lambda node: node.children)
    get_count = count_getter or (
        lambda node: node.item_count
        if (node.is_dir or getattr(node, "is_aggregate", False)) else 1
    )

    tiles: List[Tile] = []
    if width <= 1 or height <= 1:
        return tiles

    stack = [(root, x, y, width, height, 0)]
    while stack:
        if should_cancel is not None and should_cancel():
            return []
        node, nx, ny, nw, nh, depth = stack.pop()
        children = []
        for position, child in enumerate(get_children(node)):
            if should_cancel is not None and position % 256 == 0 and should_cancel():
                return []
            if get_size(child) > 0:
                children.append(child)
        if not children:
            continue

        # A directory with tens of thousands of siblings cannot produce a
        # useful one-pixel tile for each item. Keep the largest entries and
        # preserve the remaining area as one explicit aggregate tile. This
        # bounds layout, rendering, and mouse-hit work without changing the
        # complete tree shown in the Tree view.
        if max_children and len(children) > max_children:
            keep_count = max(0, max_children - 1)
            ranked = cancellable_sorted(
                children, key=get_size, reverse=True, should_cancel=should_cancel)
            if should_cancel is not None and should_cancel():
                return []
            kept = ranked[:keep_count]
            omitted = ranked[keep_count:]
            omitted_size = omitted_count = 0
            for position, child in enumerate(omitted):
                if should_cancel is not None and position % 256 == 0 and should_cancel():
                    return []
                omitted_size += get_size(child)
                omitted_count += get_count(child)
            aggregate = TreemapAggregate(
                name=f"{omitted_count:,} smaller items",
                size=omitted_size,
                item_count=omitted_count,
                parent=node,
                category_key=aggregate_category or "other",
                omitted_start=keep_count,
            )
            children = kept + ([aggregate] if omitted_size > 0 else [])

        rects = squarify([get_size(c) for c in children], nx, ny, nw, nh)
        for child, (rx, ry, rw, rh) in zip(children, rects):
            if should_cancel is not None and should_cancel():
                return []
            if rw <= 0 or rh <= 0:
                continue
            tiles.append(Tile(node=child, x=rx, y=ry, w=rw, h=rh, depth=depth))
            if not (child.is_dir and depth + 1 < max_depth):
                continue

            # only spend a header on tiles with room to spare for one
            head = header if (header and rh >= header * 3 and rw >= 80) else 0.0
            inner_w = rw - 2 * padding
            inner_h = rh - 2 * padding - head
            if inner_w > 0 and inner_h > 0 and inner_w * inner_h >= min_area:
                stack.append((child, rx + padding, ry + padding + head,
                              inner_w, inner_h, depth + 1))
    return tiles


def aggregate_members(aggregate: TreemapAggregate, children_getter=None,
                      size_getter=None,
                      should_cancel: Optional[Callable[[], bool]] = None) -> List:
    """Recover the exact ranked siblings represented by an aggregate tile."""
    get_children = children_getter or (lambda node: node.children)
    get_size = size_getter or (lambda node: node.size)
    children = []
    for position, child in enumerate(get_children(aggregate.parent)):
        if should_cancel is not None and position % 256 == 0 and should_cancel():
            return []
        if get_size(child) > 0:
            children.append(child)
    ranked = cancellable_sorted(
        children, key=get_size, reverse=True, should_cancel=should_cancel)
    if should_cancel is not None and should_cancel():
        return []
    return ranked[aggregate.omitted_start:]


# --------------------------------------------------------------- csv export

def _query_spec_record(filter_index):
    if filter_index is None:
        return None
    spec = filter_index.spec
    return {
        "root_scope": spec.root_scope,
        "categories": list(spec.categories),
        "extensions": list(spec.extensions),
        "name": spec.name,
        "name_terms": list(spec.name_terms),
        "min_size": spec.min_size,
        "max_size": spec.max_size,
        "modified_after_ns": spec.modified_after_ns,
        "modified_before_ns": spec.modified_before_ns,
        "include_hidden": spec.include_hidden,
        "sort": spec.sort,
        "reverse": spec.reverse,
        "metric": spec.metric,
    }


def _export_scope_label(filter_index, search_query: str) -> tuple[str, str]:
    scope = filter_index.filter_key if filter_index is not None else "all"
    if scope == "query":
        scope = repr(filter_index.spec)
    search_term = search_query.casefold().strip()
    query_records_search = (filter_index is not None and search_term and
                            (search_term == filter_index.spec.name or
                             search_term in filter_index.spec.name_terms))
    if search_query and not query_records_search:
        scope += f"; name contains {search_query!r}"
    metric = filter_index.spec.metric if filter_index is not None else "logical"
    return scope, metric


def _iter_export_records(root, filter_index=None, search_query: str = ""):
    """Yield records from one shared visible/full-scan query projection."""
    for node in iter_all_nodes(root):
        if search_query and not match_query(node.name, search_query):
            continue
        if filter_index is not None and not (
                filter_index.count(node) if node.is_dir else filter_index.matches(node)):
            continue
        size = filter_index.size(node) if filter_index is not None else node.size
        yield {
            "path": node.path,
            "name": node.name,
            "type": "Folder" if node.is_dir else get_file_category(
                node.name, is_dir=False)["label"],
            "size_bytes": size,
            "size": format_size(size),
            "items": (filter_index.count(node) if filter_index is not None else node.item_count)
                     if node.is_dir else None,
            "modified_time_ns": int(getattr(node, "mtime_ns", 0)) or None,
        }


def export_tree_csv(root, path: str, filter_index=None, *,
                    search_query: str = "", partial: bool = False,
                    inaccessible_count: int = 0) -> int:
    """Write a full or visible query result with explicit scope metadata."""
    rows = 0
    scope, metric = _export_scope_label(filter_index, search_query)
    scan_status = (f"partial · {inaccessible_count} inaccessible" if partial else "complete")
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Path", "Name", "Type", "Size (bytes)", "Size", "Items",
                         "Scope", "Root", "Metric", "Scan status"])
        for record in _iter_export_records(root, filter_index, search_query):
            writer.writerow([
                record["path"], record["name"], record["type"],
                record["size_bytes"], record["size"], record["items"],
                scope, root.path, metric, scan_status,
            ])
            rows += 1
    return rows


def export_tree_json(root, path: str, filter_index=None, *,
                     search_query: str = "", partial: bool = False,
                     inaccessible_count: int = 0) -> int:
    """Stream a machine-readable report using the same scope as CSV export."""
    scope_label, metric = _export_scope_label(filter_index, search_query)
    metadata = {
        "schema_version": 1,
        "root": root.path,
        "scope": {
            "mode": ("visible_results" if filter_index is not None or search_query
                     else "full_scan"),
            "root": filter_index.scope.path if filter_index is not None else root.path,
            "label": scope_label,
            "search_query": search_query or None,
            "query": _query_spec_record(filter_index),
        },
        "metric": metric,
        "scan": {
            "status": "partial" if partial else "complete",
            "partial": bool(partial),
            "inaccessible_count": max(0, int(inaccessible_count)),
        },
    }
    rows = 0
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("{\n")
        for key, value in metadata.items():
            f.write("  " + json.dumps(key, ensure_ascii=False) + ": ")
            f.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")))
            f.write(",\n")
        f.write('  "records": [')
        for record in _iter_export_records(root, filter_index, search_query):
            f.write("\n    " if rows == 0 else ",\n    ")
            f.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            rows += 1
        if rows:
            f.write("\n")
        f.write("  ]\n}\n")
    return rows


# --------------------------------------------------------------- searching

def match_query(name: str, query: str) -> bool:
    """Case-insensitive substring match; empty query matches everything."""
    if not query:
        return True
    return query.lower() in name.lower()


def find_matches(root, query: str, limit: int = 500, filter_key: str = "all",
                 filter_index=None,
                 should_cancel: Optional[Callable[[], bool]] = None) -> List:
    """Return matching nodes, respecting an optional type projection."""
    if not query:
        return []
    if limit <= 0:
        return []

    def candidates():
        for node in iter_all_nodes(root, should_cancel):
            if not match_query(node.name, query):
                continue
            if filter_index is not None or filter_key != "all":
                visible = (filter_index.count(node) if filter_index is not None
                           else (1 if file_type_matches(node.name, filter_key, is_dir=node.is_dir) else 0))
                if filter_index is not None and not node.is_dir:
                    visible = filter_index.matches(node)
                if not visible:
                    continue
            yield node

    # Keep the UI-facing search result bounded even when millions of names
    # match. A heap preserves largest-first ranking without retaining and
    # sorting every result.
    from heapq import nlargest
    key = filter_index.size if filter_index is not None else lambda n: n.size
    return nlargest(limit, candidates(), key=key)
