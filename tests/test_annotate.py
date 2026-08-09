import os
import sys

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import annotate
from annotate import AnnotationDocument, Shape


def stroke(**kw):
    kw.setdefault("kind", "pen")
    kw.setdefault("points", [(0.1, 0.1), (0.4, 0.4)])
    return Shape(**kw)


# ------------------------------------------------------------------- document

def test_add_and_state():
    doc = AnnotationDocument()
    assert doc.is_empty and not doc.can_undo and not doc.can_redo
    doc.add(stroke())
    assert not doc.is_empty and doc.can_undo


def test_undo_redo_roundtrip():
    doc = AnnotationDocument()
    a, b = doc.add(stroke()), doc.add(stroke(kind="rect"))
    assert doc.shapes == [a, b]

    assert doc.undo() is True
    assert doc.shapes == [a] and doc.can_redo
    assert doc.redo() is True
    assert doc.shapes == [a, b]


def test_undo_on_empty_is_a_noop():
    doc = AnnotationDocument()
    assert doc.undo() is False
    assert doc.redo() is False


def test_adding_clears_the_redo_stack():
    doc = AnnotationDocument()
    doc.add(stroke())
    doc.undo()
    assert doc.can_redo
    doc.add(stroke(kind="arrow"))
    assert not doc.can_redo


def test_clear_is_undoable_as_a_whole():
    doc = AnnotationDocument()
    doc.add(stroke())
    doc.add(stroke(kind="rect"))
    doc.clear()
    assert doc.is_empty
    assert doc.can_redo


def test_erase_removes_topmost_hit_only():
    doc = AnnotationDocument()
    doc.add(stroke(points=[(0.5, 0.5)]))
    top = doc.add(stroke(points=[(0.5, 0.5)], kind="pen"))

    assert doc.erase_at(0.5, 0.5) is True
    assert doc.shapes[-1] is not top
    assert len(doc.shapes) == 1


def test_erase_misses_are_reported():
    doc = AnnotationDocument()
    doc.add(stroke(points=[(0.1, 0.1)]))
    assert doc.erase_at(0.9, 0.9) is False
    assert len(doc.shapes) == 1


# ---------------------------------------------------------------------- shape

def test_shape_bounds():
    s = stroke(points=[(0.2, 0.8), (0.6, 0.1)])
    assert s.bounds() == (0.2, 0.1, 0.6, 0.8)


def test_rect_hit_uses_bounds():
    s = stroke(kind="rect", points=[(0.2, 0.2), (0.6, 0.6)])
    assert s.hit(0.4, 0.4)
    assert not s.hit(0.9, 0.9)


def test_freehand_hit_follows_the_path():
    s = stroke(kind="pen", points=[(0.1, 0.1), (0.9, 0.9)])
    assert s.hit(0.1, 0.1)
    # midpoint is far from both recorded points, so it should miss
    assert not s.hit(0.5, 0.5, tolerance=0.01)


# --------------------------------------------------------------------- helpers

def test_to_pixels_scales_normalized_points():
    assert annotate.to_pixels([(0.5, 0.25)], 200, 400) == [(100.0, 100.0)]


def test_arrow_head_is_a_triangle_at_the_tip():
    head = annotate.arrow_head((0, 0), (100, 0), 10)
    assert len(head) == 3
    assert head[0] == (100, 0)


def test_arrow_head_degenerate():
    assert annotate.arrow_head((5, 5), (5, 5), 10) == []


def test_tool_modes_are_distinct():
    assert set(annotate.BASIC_TOOLS) < set(annotate.ADVANCED_TOOLS)
    for tool in ("rect", "ellipse", "text"):
        assert tool in annotate.ADVANCED_TOOLS
        assert tool not in annotate.BASIC_TOOLS


def test_highlighter_defaults_are_translucent_and_fat():
    assert annotate.default_opacity_for("highlighter") < 1.0
    assert annotate.default_width_for("highlighter") > annotate.default_width_for("pen")
    assert annotate.default_opacity_for("pen") == 1.0


# ------------------------------------------------------------------ rendering

def test_render_leaves_a_mark_and_preserves_size():
    image = Image.new("RGB", (200, 120), (255, 255, 255))
    doc = AnnotationDocument()
    doc.add(Shape(kind="pen", points=[(0.1, 0.5), (0.9, 0.5)],
                  color="#ff0000", width=0.05))

    out = annotate.render_to_image(doc, image)
    assert out.size == (200, 120)
    assert out.getpixel((100, 60)) != (255, 255, 255)
    assert out.getpixel((5, 5)) == (255, 255, 255)      # untouched corner


def test_render_of_empty_document_is_unchanged():
    image = Image.new("RGB", (40, 40), (10, 20, 30))
    out = annotate.render_to_image(AnnotationDocument(), image)
    assert out.getpixel((20, 20)) == (10, 20, 30)


def test_render_every_shape_kind():
    image = Image.new("RGB", (300, 200), (255, 255, 255))
    doc = AnnotationDocument()
    for kind in ("pen", "highlighter", "line", "arrow", "rect", "ellipse"):
        doc.add(Shape(kind=kind, points=[(0.2, 0.2), (0.8, 0.8)], width=0.02))
    doc.add(Shape(kind="text", points=[(0.1, 0.1)], text="hello", width=0.02))

    out = annotate.render_to_image(doc, image)
    assert out.size == (300, 200)
    assert out.convert("L").getextrema()[0] < 250      # something was drawn


def test_highlighter_opacity_is_lighter_than_a_pen():
    image = Image.new("RGB", (100, 100), (255, 255, 255))
    points = [(0.1, 0.5), (0.9, 0.5)]

    pen = AnnotationDocument()
    pen.add(Shape(kind="pen", points=points, color="#000000", width=0.1, opacity=1.0))
    marker = AnnotationDocument()
    marker.add(Shape(kind="highlighter", points=points, color="#000000",
                     width=0.1, opacity=0.35))

    pen_px = annotate.render_to_image(pen, image).getpixel((50, 50))
    marker_px = annotate.render_to_image(marker, image).getpixel((50, 50))
    assert sum(marker_px) > sum(pen_px)


def test_render_scales_with_image_resolution():
    """Normalized coordinates must land in the same relative spot whatever the
    export size is."""
    doc = AnnotationDocument()
    doc.add(Shape(kind="rect", points=[(0.25, 0.25), (0.75, 0.75)], width=0.02))

    for size in [(100, 100), (800, 800)]:
        out = annotate.render_to_image(doc, Image.new("RGB", size, (255, 255, 255)))
        w, h = size
        centre_of_edge = out.getpixel((w // 2, int(h * 0.25)))
        assert centre_of_edge != (255, 255, 255), f"edge missing at {size}"
