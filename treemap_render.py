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
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFont, ImageOps

from file_utils import get_file_category, is_image_file
from thumbnails import cover_box

WHITE = (255, 255, 255)
BLACK = (0, 0, 0)

# 256x256 'L' bump, bright in the middle: the cushion highlight.
_CUSHION_BASE = ImageOps.invert(Image.radial_gradient("L"))
_cushion_cache: Dict[Tuple[int, int], Image.Image] = {}
_font_cache: Dict[int, object] = {}


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
    key = (w, h)
    mask = _cushion_cache.get(key)
    if mask is None:
        mask = _CUSHION_BASE.resize((w, h), Image.Resampling.BILINEAR)
        if len(_cushion_cache) > 4000:
            _cushion_cache.clear()
        _cushion_cache[key] = mask
    return mask


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


def tile_color(node, dark_mode: bool) -> Tuple[int, int, int]:
    if node.is_dir:
        return (63, 63, 70) if dark_mode else (203, 213, 225)
    return _rgb(get_file_category(node.path)['color'])


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

        patch = None
        if (opts.show_thumbnails and thumb_provider is not None
                and not node.is_dir and is_image_file(node.path)
                and tw >= opts.min_thumb and th >= opts.min_thumb):
            thumb = thumb_provider(node.path, (max(tw, 64), max(th, 64)))
            if thumb is not None:
                # fill the tile edge-to-edge, then centre-crop the overflow
                cw, ch = cover_box(thumb.width, thumb.height, tw, th)
                scaled = thumb.resize((cw, ch), Image.Resampling.BILINEAR)
                left = max(0, (cw - tw) // 2)
                top = max(0, (ch - th) // 2)
                patch = scaled.crop((left, top, left + tw, top + th))

        if patch is None:
            base = Image.new("RGB", (tw, th), tile_color(node, opts.dark_mode))
            mask = _cushion(tw, th)
            lit = Image.blend(base, Image.new("RGB", (tw, th), WHITE), 0.30)
            shade = Image.blend(base, Image.new("RGB", (tw, th), BLACK), 0.42)
            patch = Image.composite(lit, shade, mask)
        else:
            # keep a hint of the cushion so thumbnails still read as tiles,
            # but stay light enough that the picture is what you notice
            mask = _cushion(tw, th)
            shade = Image.blend(patch, Image.new("RGB", (tw, th), BLACK), 0.28)
            patch = Image.composite(patch, shade, mask)

        canvas.paste(patch, (x, y))

    draw = ImageDraw.Draw(canvas, "RGBA")

    # thin separators
    for tile in tiles:
        tw, th = int(tile.w), int(tile.h)
        if tw < 3 or th < 3:
            continue
        draw.rectangle([int(tile.x), int(tile.y), int(tile.x) + tw - 1, int(tile.y) + th - 1],
                       outline=border + (140,), width=1)

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
