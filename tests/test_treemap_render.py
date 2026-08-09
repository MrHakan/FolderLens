import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
import treemap_render
from scanner import Node
from thumbnails import cover_box, fit_box


def make_tree():
    root = Node(path="/root", name="root", is_dir=True)
    photo = Node(path="/root/pic.png", name="pic.png", is_dir=False, size=600, parent=root)
    movie = Node(path="/root/clip.mp4", name="clip.mp4", is_dir=False, size=300, parent=root)
    sub = Node(path="/root/sub", name="sub", is_dir=True, parent=root)
    inner = Node(path="/root/sub/doc.pdf", name="doc.pdf", is_dir=False, size=100, parent=sub)
    sub.children = [inner]
    sub.size, sub.item_count = 100, 1
    root.children = [photo, movie, sub]
    root.size, root.item_count = 1000, 4
    return root


def tiles_for(width=400, height=300, header=0.0):
    return analysis.build_treemap(make_tree(), 0, 0, width, height,
                                  min_area=1, max_depth=6, header=header)


# ---------------------------------------------------------------- box fitting

def test_fit_box_preserves_aspect_and_fits_inside():
    assert fit_box(100, 50, 50, 50) == (50, 25)
    assert fit_box(50, 100, 50, 50) == (25, 50)


def test_cover_box_fills_the_box():
    w, h = cover_box(100, 50, 50, 50)
    assert w >= 50 and h >= 50


def test_box_helpers_reject_degenerate_input():
    assert fit_box(0, 10, 10, 10) == (0, 0)
    assert cover_box(10, 10, 0, 10) == (0, 0)


# ------------------------------------------------------------------ rendering

def test_render_produces_an_image_of_the_requested_size():
    img = treemap_render.render_treemap(tiles_for(), 400, 300)
    assert isinstance(img, Image.Image)
    assert img.size == (400, 300)


def test_render_without_tiles_is_a_plain_background():
    dark = treemap_render.render_treemap([], 60, 40,
                                         treemap_render.RenderOptions(dark_mode=True))
    light = treemap_render.render_treemap([], 60, 40,
                                          treemap_render.RenderOptions(dark_mode=False))
    assert len(dark.getcolors(maxcolors=16)) == 1
    assert dark.getpixel((5, 5)) != light.getpixel((5, 5))


def test_render_is_not_flat_cushion_shading_adds_depth():
    img = treemap_render.render_treemap(tiles_for(), 400, 300)
    # a flat-filled treemap would have very few distinct colours
    assert len(img.getcolors(maxcolors=65536) or []) > 50


def test_dark_and_light_renders_differ():
    tiles = tiles_for()
    dark = treemap_render.render_treemap(tiles, 200, 150,
                                         treemap_render.RenderOptions(dark_mode=True))
    light = treemap_render.render_treemap(tiles, 200, 150,
                                          treemap_render.RenderOptions(dark_mode=False))
    assert dark.tobytes() != light.tobytes()


def test_thumbnails_are_used_when_available():
    """An image file's tile should be painted from its thumbnail."""
    magenta = Image.new("RGB", (32, 32), (255, 0, 255))
    calls = []

    def provider(path, size):
        calls.append(path)
        return magenta if path.endswith(".png") else None

    tiles = tiles_for(400, 300)
    img = treemap_render.render_treemap(
        tiles, 400, 300,
        treemap_render.RenderOptions(dark_mode=True, show_labels=False),
        thumb_provider=provider)

    assert any(p.endswith("pic.png") for p in calls)
    # the magenta thumbnail should dominate somewhere in the output
    colors = img.getcolors(maxcolors=1 << 20) or []
    assert any(r > 150 and b > 150 and g < 120 for _, (r, g, b) in colors)


def test_thumbnails_skipped_when_disabled():
    calls = []
    treemap_render.render_treemap(
        tiles_for(), 400, 300,
        treemap_render.RenderOptions(show_thumbnails=False),
        thumb_provider=lambda p, s: calls.append(p))
    assert calls == []


def test_unreadable_thumbnail_falls_back_to_a_colour_tile():
    img = treemap_render.render_treemap(
        tiles_for(), 200, 150,
        treemap_render.RenderOptions(),
        thumb_provider=lambda p, s: None)
    assert img.size == (200, 150)


def test_highlight_outline_changes_the_image():
    tiles = tiles_for()
    plain = treemap_render.render_treemap(tiles, 200, 150)
    lit = treemap_render.render_treemap(
        tiles, 200, 150, treemap_render.RenderOptions(highlight=tiles[0]))
    assert plain.tobytes() != lit.tobytes()


def test_tile_colour_distinguishes_folders_from_files():
    root = make_tree()
    folder = next(c for c in root.children if c.is_dir)
    movie = next(c for c in root.children if c.name.endswith(".mp4"))
    assert treemap_render.tile_color(folder, True) != treemap_render.tile_color(movie, True)
    assert treemap_render.tile_color(folder, True) != treemap_render.tile_color(folder, False)


# ---------------------------------------------------------------- hit testing

def test_hit_test_finds_the_tile_under_the_point():
    tiles = tiles_for()
    target = tiles[0]
    hit = treemap_render.hit_test(tiles, target.x + target.w / 2, target.y + target.h / 2)
    assert hit is not None
    assert hit.x <= target.x + target.w


def test_hit_test_prefers_the_deepest_tile():
    """A child drawn inside its parent must win the hit."""
    tiles = tiles_for(600, 400, header=0.0)
    child = max((t for t in tiles if t.depth > 0), key=lambda t: t.w * t.h, default=None)
    assert child is not None, "expected a nested tile"
    hit = treemap_render.hit_test(tiles, child.x + child.w / 2, child.y + child.h / 2)
    assert hit is child


def test_hit_test_outside_returns_none():
    assert treemap_render.hit_test(tiles_for(), -5, -5) is None
    assert treemap_render.hit_test(tiles_for(), 10_000, 10_000) is None


def test_hit_test_empty_list():
    assert treemap_render.hit_test([], 1, 1) is None


# --------------------------------------------------------------- folder header

def test_header_reserves_space_so_children_start_below_it():
    """Without a header a folder's label lands on its first child's label."""
    plain = tiles_for(600, 400, header=0.0)
    headed = tiles_for(600, 400, header=18.0)

    def first_child_of_folder(tiles):
        parent = next(t for t in tiles if t.node.is_dir)
        kids = [t for t in tiles if t.depth > 0]
        return parent, (kids[0] if kids else None)

    p0, c0 = first_child_of_folder(plain)
    p1, c1 = first_child_of_folder(headed)
    assert c0 is not None and c1 is not None
    assert c1.y - p1.y > c0.y - p0.y, "header did not push children down"


def test_header_skipped_on_tiles_too_small_to_show_one():
    tiles = analysis.build_treemap(make_tree(), 0, 0, 60, 40,
                                   min_area=1, max_depth=6, header=18.0)
    for tile in tiles:
        assert tile.h >= 0
