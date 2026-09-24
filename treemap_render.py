"""Treemap rendering.

The previous renderer already composited the map into one image, but its flat
tile treatment and unrestricted labels made dense trees look like an exploded
bar chart. This version keeps the single-image pipeline and adds:

  * cushion shading gives every tile visible volume, so nested folders read
    as groups rather than a field of same-coloured blocks (the technique
    WinDirStat/SequoiaView use),
  * image files are painted with their own thumbnail, so the map is
    browsable at a glance,
  * folder names are stamped on top after their children are drawn.

Keeping the single-image pipeline is far cheaper than thousands of live canvas
items, while the bounded labels and thumbnail requests keep big trees and
network workfolders responsive.
"""
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from file_utils import FILE_CATEGORIES, get_file_category, is_image_file

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

# 256x256 'L' bump, bright in the middle: the cushion highlight.
_CUSHION_BASE = ImageOps.invert(Image.radial_gradient("L"))
_cushion_cache: "OrderedDict[Tuple[int, int], Image.Image]" = OrderedDict()
_cushion_pixels = 0
_CUSHION_PIXEL_BUDGET = 8_000_000      # ~8 MB of masks, then evict oldest
_lut_cache: Dict[Tuple[int, int, int], Tuple[list, list, list]] = {}
_font_cache: Dict[int, object] = {}

# Below this a tile is a few pixels across: the cushion gradient cannot be
# seen, so it is filled flat. Saves the bulk of the work on dense trees
# without any visible difference.
FLAT_FILL_BELOW = 8

SHADE_MIX = 0.42        # how dark the tile edges go
LIGHT_MIX = 0.30        # how bright the centre highlight goes


@dataclass
class RenderOptions:
    dark_mode: bool = True
    show_labels: bool = True
    show_thumbnails: bool = True
    min_label_w: int = 72
    min_label_h: int = 22
    min_label_area: int = 1200
    min_thumb: int = 36
    header: int = 22                        # folder header band, matches layout
    gutter: int = 2                         # visual separation between siblings
    max_file_labels: int = 180              # dense folders stay readable
    max_thumbnails: int = 120               # cap network image reads per render
    thumbnail_mtime: Optional[Callable] = None
    highlight: Optional[object] = None      # tile to outline (hover)


def _cushion(w: int, h: int) -> Image.Image:
    """Cushion mask at a given size, cached under a pixel budget.

    Keyed by exact size, so a tree with thousands of distinct tile sizes used
    to be able to grow this without limit; it is now an LRU bounded by total
    pixels rather than entry count, since a few large masks cost far more than
    many small ones.
    """
    global _cushion_pixels
    key = (w, h)
    mask = _cushion_cache.get(key)
    if mask is not None:
        _cushion_cache.move_to_end(key)
        return mask

    mask = _CUSHION_BASE.resize((w, h), Image.Resampling.BILINEAR)
    _cushion_cache[key] = mask
    _cushion_pixels += w * h
    while _cushion_pixels > _CUSHION_PIXEL_BUDGET and len(_cushion_cache) > 1:
        (ow, oh), _ = _cushion_cache.popitem(last=False)
        _cushion_pixels -= ow * oh
    return mask


def _shade_luts(color: Tuple[int, int, int]):
    """Per-channel lookup tables mapping cushion brightness to tile colour.

    Applying three LUTs to the mask replaces allocating four temporary images
    and running two blends plus a composite for every single tile.
    """
    luts = _lut_cache.get(color)
    if luts is None:
        low = [c * (1.0 - SHADE_MIX) for c in color]
        high = [c + (255 - c) * LIGHT_MIX for c in color]
        # bytes, not list: Pillow re-rounds a list LUT in Python on every
        # call, which dominated the render on trees with thousands of tiles
        luts = tuple(
            bytes(min(255, max(0, int(low[i] + (high[i] - low[i]) * v / 255.0)))
                  for v in range(256))
            for i in range(3)
        )
        if len(_lut_cache) > 512:
            _lut_cache.clear()
        _lut_cache[color] = luts
    return luts


# Cushions are kept per colour at a few resolutions. Scaling every tile down
# from one large source meant reading the whole source for even a 20px tile;
# picking the nearest level keeps each resize close to 1:1.
_CUSHION_LEVELS = (16, 32, 64, 128, 256)
_cushion_rgb_cache: Dict[Tuple[Tuple[int, int, int], int], Image.Image] = {}


def _cushion_level(size: int) -> int:
    for level in _CUSHION_LEVELS:
        if size <= level:
            return level
    return _CUSHION_LEVELS[-1]


def _cushion_rgb(color: Tuple[int, int, int], level: int) -> Image.Image:
    """A finished cushion tile for one colour at one pyramid level.

    Only a dozen or so colours exist (one per file category), so these are
    built a handful of times per session and every tile is then one cheap
    resize. Shading each tile from scratch cost several passes over its pixels.
    """
    key = (color, level)
    tile = _cushion_rgb_cache.get(key)
    if tile is None:
        mask = _CUSHION_BASE.resize((level, level), Image.Resampling.BILINEAR)
        r, g, b = _shade_luts(color)
        tile = Image.merge("RGB", (mask.point(r), mask.point(g), mask.point(b)))
        if len(_cushion_rgb_cache) > 128:
            _cushion_rgb_cache.clear()
        _cushion_rgb_cache[key] = tile
    return tile


def _cushion_tile(color: Tuple[int, int, int], w: int, h: int) -> Image.Image:
    source = _cushion_rgb(color, _cushion_level(max(w, h)))
    if source.size == (w, h):
        return source.copy()
    return source.resize((w, h), Image.Resampling.BILINEAR)


def _font(px: int):
    px = max(7, min(px, 40))
    font = _font_cache.get(px)
    if font is None:
        for name in ("segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"):
            try:
                font = ImageFont.truetype(name, px)
                break
            except OSError:
                continue
        if font is None:
            font = ImageFont.load_default()
        _font_cache[px] = font
    return font


def _rgb(color: str) -> Tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


_color_by_ext: Dict[str, Tuple[int, int, int]] = {}


def _folder_color(dark_mode: bool, depth: int = 0) -> Tuple[int, int, int]:
    """Return a restrained depth palette for folder containers."""
    depth = max(0, min(int(depth), 6))
    if dark_mode:
        base = (38, 45, 58)
        lift = depth * 4
        return tuple(min(95, channel + lift) for channel in base)
    base = (197, 207, 220)
    lift = depth * 5
    return tuple(min(242, channel + lift) for channel in base)


def tile_color(node, dark_mode: bool, depth: int = 0) -> Tuple[int, int, int]:
    if getattr(node, "is_aggregate", False):
        category = FILE_CATEGORIES.get(getattr(node, "category_key", "other"),
                                       FILE_CATEGORIES["other"])
        return _rgb(category['color'])
    if node.is_dir:
        return _folder_color(dark_mode, depth)
    # a tile's colour depends only on its extension, and a big tree asks the
    # same question thousands of times. Using node.ext keeps the full path
    # from being built just to look at the suffix.
    ext = node.ext
    color = _color_by_ext.get(ext)
    if color is None:
        color = _rgb(get_file_category("x" + ext, is_dir=False)['color'])
        if len(_color_by_ext) > 4000:
            _color_by_ext.clear()
        _color_by_ext[ext] = color
    return color


def _draw_label(draw: ImageDraw.ImageDraw, x: float, y: float, text: str,
                px: int, fill=(255, 255, 255)):
    """Text with a 1px shadow so it stays readable on any tile colour."""
    font = _font(px)
    draw.text((x + 1, y + 1), text, font=font, fill=(0, 0, 0, 180))
    draw.text((x, y), text, font=font, fill=fill)


def _inset_rect(tile, gutter: int, canvas_width: int, canvas_height: int):
    """Return a pixel-aligned rectangle with a small visual gutter."""
    inset = max(0.0, min(float(gutter), min(tile.w, tile.h) / 3.0))
    left = max(0, int(round(tile.x + inset)))
    top = max(0, int(round(tile.y + inset)))
    right = min(canvas_width, int(round(tile.x + tile.w - inset)))
    bottom = min(canvas_height, int(round(tile.y + tile.h - inset)))
    return left, top, right, bottom


def _ellipsize(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> str:
    """Trim a label to its tile using a short binary search."""
    if max_width <= 0:
        return ""
    if draw.textlength(text, font=font) <= max_width:
        return text
    if max_width < draw.textlength("…", font=font):
        return ""

    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        candidate = text[:mid].rstrip() + "…"
        if draw.textlength(candidate, font=font) <= max_width:
            low = mid
        else:
            high = mid - 1
    return (text[:low].rstrip() + "…") if low else "…"


def render_treemap(tiles: Sequence, width: int, height: int,
                   options: Optional[RenderOptions] = None,
                   thumb_provider: Optional[Callable] = None,
                   should_cancel: Optional[Callable[[], bool]] = None) -> Optional[Image.Image]:
    """Paint ``tiles`` into one image with hierarchy-first visual grouping.

    Folder containers are painted first, leaves are separated with gutters,
    labels are capped by area, and only the largest image tiles request
    thumbnails.  The last rule is important for network workfolders: a map
    should not turn a mouse-over or window resize into hundreds of remote image
    reads.
    """
    opts = options or RenderOptions()
    width = max(1, int(width))
    height = max(1, int(height))

    background = (18, 18, 18) if opts.dark_mode else (238, 240, 243)
    canvas = Image.new("RGB", (width, height), background)
    if not tiles:
        return canvas

    border = (12, 12, 12) if opts.dark_mode else (255, 255, 255)

    # Parents form the backdrop and leaves are layered above them.  Sorting by
    # depth also makes rendering deterministic when several sibling folders
    # were discovered by different scan workers.
    draw_tiles = sorted(tiles, key=lambda tile: (tile.depth, not tile.node.is_dir))
    thumb_candidates = []
    if opts.show_thumbnails and thumb_provider is not None:
        thumb_candidates = [
            tile for tile in draw_tiles
            if (not tile.node.is_dir and not getattr(tile.node, "is_aggregate", False)
                and is_image_file(tile.node.name)
                and tile.w >= opts.min_thumb and tile.h >= opts.min_thumb)
        ]
        thumb_candidates.sort(key=lambda tile: tile.w * tile.h, reverse=True)
        thumb_candidates = {id(tile) for tile in thumb_candidates[:max(0, opts.max_thumbnails)]}
    else:
        thumb_candidates = set()

    for tile in draw_tiles:
        if should_cancel is not None and should_cancel():
            return None
        left, top, right, bottom = _inset_rect(tile, opts.gutter, width, height)
        tw, th = right - left, bottom - top
        if tw < 1 or th < 1:
            continue
        node = tile.node
        color = tile_color(node, opts.dark_mode, tile.depth)

        # Too small for cushion shading to communicate anything: a direct
        # fill avoids allocating a temporary image for the dense tail.
        if tw < FLAT_FILL_BELOW or th < FLAT_FILL_BELOW:
            canvas.paste(color, (left, top, right, bottom))
            continue

        patch = None
        if id(tile) in thumb_candidates:
            if opts.thumbnail_mtime is not None:
                thumb = thumb_provider(
                    node.path, (tw, th), opts.thumbnail_mtime(node))
            else:
                thumb = thumb_provider(node.path, (tw, th))
            if thumb is not None:
                # Fill the visible tile edge-to-edge, cropping overflow.
                patch = ImageOps.fit(thumb, (tw, th), method=Image.Resampling.BILINEAR)

        if patch is None:
            patch = _cushion_tile(color, tw, th)
        else:
            # Keep a hint of the tile's depth while leaving the picture
            # recognisable.
            shade = Image.blend(patch, Image.new("RGB", (tw, th), BLACK), 0.24)
            patch = Image.composite(patch, shade, _cushion(tw, th))

        canvas.paste(patch, (left, top))

    draw = ImageDraw.Draw(canvas, "RGBA")

    # Gutters already provide most separation.  One restrained outline around
    # each visible tile keeps very small adjacent tiles legible without the
    # heavy grid that made the old map look like an exploded bar chart.
    for tile in draw_tiles:
        if should_cancel is not None and should_cancel():
            return None
        left, top, right, bottom = _inset_rect(tile, opts.gutter, width, height)
        if right - left < 4 or bottom - top < 4:
            continue
        alpha = 205 if tile.node.is_dir else 130
        draw.rectangle([left, top, right - 1, bottom - 1],
                       outline=border + (alpha,), width=1)

    if opts.show_labels:
        # Label the largest leaves first.  The area threshold and cap keep a
        # dense directory readable while preserving the useful names of the
        # dominant files.
        label_tiles = [
            tile for tile in draw_tiles
            if (not tile.node.is_dir
                and tile.w >= opts.min_label_w
                and tile.h >= opts.min_label_h
                and tile.w * tile.h >= opts.min_label_area)
        ]
        label_tiles.sort(key=lambda tile: tile.w * tile.h, reverse=True)
        for tile in label_tiles[:max(0, opts.max_file_labels)]:
            if should_cancel is not None and should_cancel():
                return None
            left, top, right, bottom = _inset_rect(tile, opts.gutter, width, height)
            px = 11 if bottom - top >= 40 else 9
            font = _font(px)
            label = _ellipsize(draw, tile.node.name, font, right - left - 8)
            if label:
                _draw_label(draw, left + 4, top + 3, label, px)

        # Folder names sit in the reserved header band, above the leaves.  A
        # subtle depth tint makes nested folder groups readable at a glance.
        for tile in draw_tiles:
            if should_cancel is not None and should_cancel():
                return None
            node = tile.node
            if not node.is_dir or tile.w < 80 or tile.h < 30:
                continue
            left, top, right, bottom = _inset_rect(tile, opts.gutter, width, height)
            band = min(opts.header, bottom - top)
            if band < 14:
                continue
            header_fill = (8, 13, 22, 215) if opts.dark_mode else (244, 247, 251, 225)
            draw.rectangle([left, top, right - 1, top + band - 1], fill=header_fill)
            draw.line([left, top + band - 1, right - 1, top + band - 1],
                      fill=(148, 163, 184, 180), width=1)
            px = 12 if right - left >= 150 else 10
            font = _font(px)
            label = _ellipsize(draw, node.name, font, right - left - 12)
            if label:
                _draw_label(draw, left + 6, top + max(1, (band - px - 4) / 2),
                            label, px, fill=(248, 250, 252) if opts.dark_mode else (30, 41, 59))

    if opts.highlight is not None:
        t = opts.highlight
        left, top, right, bottom = _inset_rect(t, max(1, opts.gutter - 1), width, height)
        draw.rectangle([left, top, right - 1, bottom - 1],
                       outline=(255, 255, 255, 235), width=2)

    return canvas


def hit_test(tiles: Sequence, x: float, y: float):
    """Return the deepest tile containing the point, or None."""
    hit = None
    for tile in tiles:
        if tile.x <= x < tile.x + tile.w and tile.y <= y < tile.y + tile.h:
            if hit is None or tile.depth >= hit.depth:
                hit = tile
    return hit
