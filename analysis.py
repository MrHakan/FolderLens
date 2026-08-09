"""Pure analysis helpers over a scanned Node tree.

Everything here is side-effect free (except CSV export) and unit tested, so
the UI layer can stay thin. Features inspired by WinDirStat / TreeSize /
SpaceSniffer: a squarified treemap layout, largest-files ranking, and a
file-type / extension breakdown.
"""
import csv
from dataclasses import dataclass
from typing import Iterator, List, Dict, Tuple, Optional

from file_utils import get_file_category, get_file_extension, format_size


# --------------------------------------------------------------------- walking

def iter_file_nodes(root) -> Iterator:
    """Yield every non-directory Node in the subtree (iterative)."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.is_dir:
            stack.extend(node.children)
        else:
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

def largest_files(root, limit: int = 100) -> List:
    """Return the `limit` largest files anywhere in the tree, biggest first."""
    files = list(iter_file_nodes(root))
    files.sort(key=lambda n: n.size, reverse=True)
    return files[:limit]


# --------------------------------------------------------- type breakdown

@dataclass
class CategoryStat:
    label: str
    color: str
    size: int
    count: int
    percent: float = 0.0


def category_breakdown(root) -> List[CategoryStat]:
    """Aggregate total size and file count per file category, largest first."""
    totals: Dict[str, List[int]] = {}
    for node in iter_file_nodes(root):
        cat = get_file_category(node.path)
        label = cat['label']
        entry = totals.setdefault(label, [0, 0, cat['color']])
        entry[0] += node.size
        entry[1] += 1

    total_size = sum(v[0] for v in totals.values()) or 1
    stats = [
        CategoryStat(label=label, color=vals[2], size=vals[0], count=vals[1],
                     percent=vals[0] / total_size * 100)
        for label, vals in totals.items()
    ]
    stats.sort(key=lambda s: s.size, reverse=True)
    return stats


def extension_breakdown(root, limit: int = 15) -> List[Tuple[str, int, int]]:
    """Return (extension, total_size, count) tuples, largest first."""
    totals: Dict[str, List[int]] = {}
    for node in iter_file_nodes(root):
        ext = get_file_extension(node.path)
        entry = totals.setdefault(ext, [0, 0])
        entry[0] += node.size
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


def _worst(sizes, x, y, dx, dy, horizontal):
    rects = _layout_row(sizes, x, y, dx, dy, horizontal)
    worst = 0.0
    for (_, _, w, h) in rects:
        if w <= 0 or h <= 0:
            return float('inf')
        worst = max(worst, w / h, h / w)
    return worst


def squarify(sizes: List[float], x: float, y: float, dx: float, dy: float) -> List[Tuple[float, float, float, float]]:
    """Squarified treemap (Bruls, Huizing & van Wijk).

    `sizes` are raw weights; they are normalized to the given rectangle's area.
    Returns rectangles (x, y, w, h) in the same order as `sizes`.
    """
    sizes = _normalize([float(s) for s in sizes], dx * dy)
    result: List[Optional[Tuple[float, float, float, float]]] = [None] * len(sizes)

    order = list(range(len(sizes)))
    order.sort(key=lambda i: sizes[i], reverse=True)

    def place(indices, x, y, dx, dy):
        if not indices:
            return
        if len(indices) == 1:
            i = indices[0]
            result[i] = (x, y, dx, dy)
            return

        horizontal = dx >= dy
        vals = [sizes[i] for i in indices]

        split = 1
        while split < len(vals) and _worst(vals[:split], x, y, dx, dy, horizontal) >= \
                _worst(vals[:split + 1], x, y, dx, dy, horizontal):
            split += 1

        current = indices[:split]
        rest = indices[split:]
        rects = _layout_row([sizes[i] for i in current], x, y, dx, dy, horizontal)
        for idx, rect in zip(current, rects):
            result[idx] = rect

        covered = sum(sizes[i] for i in current)
        if horizontal:
            width = covered / dy if dy else 0
            place(rest, x + width, y, dx - width, dy)
        else:
            height = covered / dx if dx else 0
            place(rest, x, y + height, dx, dy - height)

    place(order, x, y, dx, dy)
    return [r if r is not None else (x, y, 0.0, 0.0) for r in result]


def build_treemap(root, x: float, y: float, width: float, height: float,
                  min_area: float = 90.0, max_depth: int = 6, padding: float = 1.0) -> List[Tile]:
    """Build treemap tiles for a Node.

    Recurses into directories only while their tile is large enough
    (area >= min_area) and depth allows, so the tile count stays bounded and
    the canvas stays responsive on huge trees.
    """
    tiles: List[Tile] = []
    if width <= 1 or height <= 1:
        return tiles

    stack = [(root, x, y, width, height, 0)]
    while stack:
        node, nx, ny, nw, nh, depth = stack.pop()
        children = [c for c in node.children if c.size > 0]
        if not children:
            continue

        rects = squarify([c.size for c in children], nx, ny, nw, nh)
        for child, (rx, ry, rw, rh) in zip(children, rects):
            if rw <= 0 or rh <= 0:
                continue
            tiles.append(Tile(node=child, x=rx, y=ry, w=rw, h=rh, depth=depth))
            if (child.is_dir and depth + 1 < max_depth
                    and (rw - 2 * padding) * (rh - 2 * padding) >= min_area):
                stack.append((child, rx + padding, ry + padding,
                              rw - 2 * padding, rh - 2 * padding, depth + 1))
    return tiles


# --------------------------------------------------------------- csv export

def export_tree_csv(root, path: str) -> int:
    """Write every node (folders and files) to a CSV. Returns rows written."""
    rows = 0
    with open(path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(["Path", "Name", "Type", "Size (bytes)", "Size", "Items"])
        for node in iter_all_nodes(root):
            kind = "Folder" if node.is_dir else get_file_category(node.path)['label']
            writer.writerow([
                node.path, node.name, kind, node.size,
                format_size(node.size),
                node.item_count if node.is_dir else "",
            ])
            rows += 1
    return rows


# --------------------------------------------------------------- searching

def match_query(name: str, query: str) -> bool:
    """Case-insensitive substring match; empty query matches everything."""
    if not query:
        return True
    return query.lower() in name.lower()


def find_matches(root, query: str, limit: int = 500) -> List:
    """Return nodes whose name matches `query`, largest first (bounded)."""
    if not query:
        return []
    matches = [n for n in iter_all_nodes(root) if match_query(n.name, query)]
    matches.sort(key=lambda n: n.size, reverse=True)
    return matches[:limit]
