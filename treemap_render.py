"""Treemap rendering.

The old view drew one flat canvas rectangle per tile, which looked like a bar
chart exploded across the window and told you nothing about what a file
actually was. This renders the whole map into a single image instead:

  * cushion shading gives every tile visible volume, so nested folders read
    as groups rather than a field of same-coloured blocks (the technique
    WinDirStat/SequoiaView use),
  * image files are painted with their own thumbnail, so the map is
    browsable at a glance,
  * folder names are stamped on top after their children are drawn.

Compositing into one image is also far cheaper than thousands of live canvas
items, which is what made the old view stutter on big trees.
"""
from collections import OrderedDict
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from file_utils import get_file_category, is_image_file

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
    min_label_w: int = 54
    min_label_h: int = 18
    min_thumb: int = 36
    header: int = 18                        # folder header band, matches layout
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


def tile_color(node, dark_mode: bool) -> Tuple[int, int, int]:
    if node.is_dir:
        return (63, 63, 70) if dark_mode else (203, 213, 225)
    # a tile's colour depends only on its extension, and a big tree asks the
    # same question thousands of times. Using node.ext keeps the full path
    # from being built just to look at the suffix.
    ext = node.ext
    color = _color_by_ext.get(ext)
    if color is None:
        color = _rgb(get_file_category("x" + ext)['color'])
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


def render_treemap(tiles: Sequence, width: int, height: int,
                   options: Optional[RenderOptions] = None,
                   thumb_provider: Optional[Callable] = None) -> Image.Image:
    """Paint `tiles` (from analysis.build_treemap) into one RGB image."""
    opts = options or RenderOptions()
    width = max(1, int(width))
    height = max(1, int(height))

    background = (18, 18, 18) if opts.dark_mode else (238, 240, 243)
    canvas = Image.new("RGB", (width, height), background)
    if not tiles:
        return canvas

    border = (12, 12, 12) if opts.dark_mode else (255, 255, 255)

    for tile in tiles:
        tw, th = int(tile.w), int(tile.h)
        if tw < 1 or th < 1:
            continue
        x, y = int(tile.x), int(tile.y)
        node = tile.node
        color = tile_color(node, opts.dark_mode)

        # too small for the gradient to be visible: fill flat, no allocation
        if tw < FLAT_FILL_BELOW or th < FLAT_FILL_BELOW:
            canvas.paste(color, (x, y, x + tw, y + th))
            continue

        patch = None
        if (opts.show_thumbnails and thumb_provider is not None
                and not node.is_dir and is_image_file(node.name)
                and tw >= opts.min_thumb and th >= opts.min_thumb):
            thumb = thumb_provider(node.path, (tw, th))
            if thumb is not None:
                # fill the tile edge-to-edge, cropping the overflow
                patch = ImageOps.fit(thumb, (tw, th), method=Image.Resampling.BILINEAR)

        if patch is None:
            patch = _cushion_tile(color, tw, th)
        else:
            # keep a hint of the cushion so thumbnails still read as tiles,
            # but stay light enough that the picture is what you notice
            shade = Image.blend(patch, Image.new("RGB", (tw, th), BLACK), 0.28)
            patch = Image.composite(patch, shade, _cushion(tw, th))

        canvas.paste(patch, (x, y))

    draw = ImageDraw.Draw(canvas, "RGBA")

    # thin separators; skip tiles too small for an outline to read
    separator = border + (140,)
    for tile in tiles:
        tw, th = int(tile.w), int(tile.h)
        if tw < 5 or th < 5:
            continue
        draw.rectangle([int(tile.x), int(tile.y), int(tile.x) + tw - 1, int(tile.y) + th - 1],
                       outline=separator, width=1)

    if opts.show_labels:
        # files first...
        for tile in tiles:
            node = tile.node
            if node.is_dir:
                continue
            if tile.w >= opts.min_label_w and tile.h >= opts.min_label_h:
                px = 11 if tile.h >= 34 else 9
                _draw_label(draw, tile.x + 4, tile.y + 3, node.name[:38], px)

        # ...then folder names on top. The layout reserves a header strip on
        # each folder, so these sit in their own band instead of landing on a
        # child's label.
        for tile in tiles:
            node = tile.node
            if not node.is_dir or tile.w < 80 or tile.h < 26:
                continue
            px = 12 if tile.w >= 130 else 10
            band = min(opts.header, tile.h)
            draw.rectangle([int(tile.x), int(tile.y),
                            int(tile.x + tile.w) - 1, int(tile.y + band) - 1],
                           fill=(0, 0, 0, 120))
            _draw_label(draw, tile.x + 6, tile.y + max(1, (band - px - 4) / 2),
                        node.name[:36], px, fill=(248, 250, 252))

    if opts.highlight is not None:
        t = opts.highlight
        draw.rectangle([int(t.x), int(t.y), int(t.x + t.w) - 1, int(t.y + t.h) - 1],
                       outline=(255, 255, 255, 235), width=2)

    return canvas


def hit_test(tiles: Sequence, x: float, y: float):
    """Topmost tile containing the point, or None.

    Tiles arrive in draw order (parents before their children), so scanning
    backwards finds the deepest one under the cursor.
    """
    for tile in reversed(tiles):
        if tile.x <= x < tile.x + tile.w and tile.y <= y < tile.y + tile.h:
            return tile
    return None
