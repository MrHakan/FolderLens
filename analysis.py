"""Pure analysis helpers over a scanned Node tree.

Everything here is side-effect free (except CSV export) and unit tested, so
the UI layer can stay thin. Features inspired by WinDirStat / TreeSize /
SpaceSniffer: a squarified treemap layout, largest-files ranking, and a
file-type / extension breakdown.
"""
import csv
from dataclasses import dataclass
from typing import Callable, Iterator, List, Dict, Tuple, Optional

from file_utils import (
    FILE_TYPE_FILTER_LABELS,
    file_type_matches,
    get_file_category,
    get_file_extension,
    format_size,
)
from query import QueryEngine, QueryIndex, QuerySpec


# --------------------------------------------------------------------- walking

def iter_file_nodes(root, predicate: Optional[Callable] = None) -> Iterator:
    """Yield non-directory Nodes in the subtree (iterative).

    ``predicate`` is deliberately applied to the in-memory node, so filtered
    views never need to touch the filesystem again.
    """
    stack = [root]
    while stack:
        node = stack.pop()
        if node.is_dir:
            stack.extend(node.children)
        elif predicate is None or predicate(node):
            yield node


def iter_all_nodes(root) -> Iterator:
    """Yield every Node in the subtree including directories (excluding root)."""
    stack = list(root.children)
    while stack:
        node = stack.pop()
        yield node
        if node.is_dir:
            stack.extend(node.children)


# ------------------------------------------------------------- largest files

def largest_files(root, limit: int = 100, filter_key: str = "all",
                  filter_index=None, name_query: str = "") -> List:
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
    return nlargest(limit, iter_file_nodes(root, predicate),
                    key=filter_index.size if filter_index is not None else lambda n: n.size)


# --------------------------------------------------------- type breakdown

@dataclass
class CategoryStat:
    label: str
    color: str
    size: int
    count: int
    percent: float = 0.0


def category_breakdown(root, filter_key: str = "all", filter_index=None) -> List[CategoryStat]:
    """Aggregate total size and file count per file category, largest first."""
    totals: Dict[str, List[int]] = {}
    predicate = None
    if filter_index is not None or filter_key != "all":
        predicate = filter_index.matches if filter_index is not None else \
            (lambda node: file_type_matches(node.name, filter_key, is_dir=False))
    for node in iter_file_nodes(root, predicate):
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
        children = [c for c in get_children(node) if get_size(c) > 0]
        if not children:
            continue

        # A directory with tens of thousands of siblings cannot produce a
        # useful one-pixel tile for each item. Keep the largest entries and
        # preserve the remaining area as one explicit aggregate tile. This
        # bounds layout, rendering, and mouse-hit work without changing the
        # complete tree shown in the Tree view.
        if max_children and len(children) > max_children:
            keep_count = max(0, max_children - 1)
            ranked = sorted(children, key=get_size, reverse=True)
            kept = ranked[:keep_count]
            omitted = ranked[keep_count:]
            omitted_size = sum(get_size(child) for child in omitted)
            omitted_count = sum(get_count(child) for child in omitted)
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
                      size_getter=None) -> List:
    """Recover the exact ranked siblings represented by an aggregate tile."""
    get_children = children_getter or (lambda node: node.children)
    get_size = size_getter or (lambda node: node.size)
    ranked = sorted((child for child in get_children(aggregate.parent)
                     if get_size(child) > 0), key=get_size, reverse=True)
    return ranked[aggregate.omitted_start:]


# --------------------------------------------------------------- csv export

def export_tree_csv(root, path: str, filter_index=None, *,
                    search_query: str = "", partial: bool = False,
                    inaccessible_count: int = 0) -> int:
    """Write a full or visible query result with explicit scope metadata."""
    rows = 0
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
    scan_status = (f"partial · {inaccessible_count} inaccessible" if partial else "complete")
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Path", "Name", "Type", "Size (bytes)", "Size", "Items",
                         "Scope", "Root", "Metric", "Scan status"])
        for node in iter_all_nodes(root):
            if search_query and not match_query(node.name, search_query):
                continue
            if filter_index is not None and not (filter_index.count(node) if node.is_dir
                                                  else filter_index.matches(node)):
                continue
            size = filter_index.size(node) if filter_index is not None else node.size
            kind = "Folder" if node.is_dir else get_file_category(node.name, is_dir=False)['label']
            writer.writerow([
                node.path, node.name, kind, size,
                format_size(size),
                (filter_index.count(node) if filter_index is not None else node.item_count)
                if node.is_dir else "", scope, root.path, metric, scan_status,
            ])
            rows += 1
    return rows


# --------------------------------------------------------------- searching

def match_query(name: str, query: str) -> bool:
    """Case-insensitive substring match; empty query matches everything."""
    if not query:
        return True
    return query.lower() in name.lower()


def find_matches(root, query: str, limit: int = 500, filter_key: str = "all",
                 filter_index=None) -> List:
    """Return matching nodes, respecting an optional type projection."""
    if not query:
        return []
    matches = []
    for node in iter_all_nodes(root):
        if not match_query(node.name, query):
            continue
        if filter_index is not None or filter_key != "all":
            visible = (filter_index.count(node) if filter_index is not None
                       else (1 if file_type_matches(node.name, filter_key, is_dir=node.is_dir) else 0))
            if filter_index is not None and not node.is_dir:
                visible = filter_index.matches(node)
            if not visible:
                continue
        matches.append(node)
    if filter_index is None:
        matches.sort(key=lambda n: n.size, reverse=True)
    else:
        matches.sort(key=filter_index.size, reverse=True)
    return matches[:limit]
