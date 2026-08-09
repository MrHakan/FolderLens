"""Image annotation model.

Coordinates are stored normalized to 0..1 of the image, so annotations stay
correct at any zoom level, on any window size, and when exported at full
resolution. Rendering to a PIL image lives here too, which keeps the whole
thing testable without a display.
"""
import math
from dataclasses import dataclass
from typing import List, Tuple

from PIL import Image, ImageDraw, ImageFont

Point = Tuple[float, float]

# Tools available in each mode. Basic stays deliberately small: mark something
# up and move on. Advanced adds precise shapes, text and opacity.
BASIC_TOOLS = ["pen", "highlighter", "arrow", "eraser"]
ADVANCED_TOOLS = ["pen", "highlighter", "line", "arrow", "rect", "ellipse", "text", "eraser"]

PALETTE = ["#ef4444", "#f59e0b", "#22c55e", "#3b82f6", "#a855f7", "#ffffff", "#000000"]

# Tools that are defined by a freehand path rather than two drag handles.
FREEHAND = {"pen", "highlighter"}


@dataclass
class Shape:
    kind: str
    points: List[Point]
    color: str = "#ef4444"
    width: float = 0.004          # normalized to the image's larger side
    opacity: float = 1.0
    text: str = ""

    def bounds(self) -> Tuple[float, float, float, float]:
        xs = [p[0] for p in self.points] or [0.0]
        ys = [p[1] for p in self.points] or [0.0]
        return (min(xs), min(ys), max(xs), max(ys))

    def hit(self, x: float, y: float, tolerance: float = 0.02) -> bool:
        """Rough hit test used by the eraser."""
        if self.kind in FREEHAND or self.kind in ("line", "arrow"):
            return any(math.dist((x, y), p) <= tolerance for p in self.points)
        x0, y0, x1, y1 = self.bounds()
        return (x0 - tolerance) <= x <= (x1 + tolerance) and \
               (y0 - tolerance) <= y <= (y1 + tolerance)


class AnnotationDocument:
    """Ordered list of shapes with undo/redo."""

    def __init__(self):
        self.shapes: List[Shape] = []
        self._redo: List[Shape] = []

    # ---------------------------------------------------------------- edits

    def add(self, shape: Shape) -> Shape:
        self.shapes.append(shape)
        self._redo.clear()
        return shape

    def erase_at(self, x: float, y: float, tolerance: float = 0.02) -> bool:
        """Remove the topmost shape under the point. True if something went."""
        for i in range(len(self.shapes) - 1, -1, -1):
            if self.shapes[i].hit(x, y, tolerance):
                self._redo.append(self.shapes.pop(i))
                return True
        return False

    def undo(self) -> bool:
        if not self.shapes:
            return False
        self._redo.append(self.shapes.pop())
        return True

    def redo(self) -> bool:
        if not self._redo:
            return False
        self.shapes.append(self._redo.pop())
        return True

    def clear(self):
        if self.shapes:
            self._redo.extend(reversed(self.shapes))
            self.shapes.clear()

    @property
    def can_undo(self) -> bool:
        return bool(self.shapes)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    @property
    def is_empty(self) -> bool:
        return not self.shapes


# ------------------------------------------------------------------ helpers

def _rgb(color: str) -> Tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(c * 2 for c in color)
    return tuple(int(color[i:i + 2], 16) for i in (0, 2, 4))


def to_pixels(points: List[Point], width: int, height: int) -> List[Tuple[float, float]]:
    return [(p[0] * width, p[1] * height) for p in points]


def arrow_head(start: Tuple[float, float], end: Tuple[float, float],
               size: float) -> List[Tuple[float, float]]:
    """Triangle for the tip of an arrow drawn from start to end."""
    dx, dy = end[0] - start[0], end[1] - start[1]
    length = math.hypot(dx, dy)
    if length < 1e-6:
        return []
    ux, uy = dx / length, dy / length
    size = min(size, length)
    base = (end[0] - ux * size, end[1] - uy * size)
    px, py = -uy, ux
    half = size * 0.5
    return [end, (base[0] + px * half, base[1] + py * half),
            (base[0] - px * half, base[1] - py * half)]


def _font(px: int):
    for name in ("segoeui.ttf", "DejaVuSans.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, px)
        except OSError:
            continue
    return ImageFont.load_default()


def render_to_image(doc: AnnotationDocument, image: Image.Image) -> Image.Image:
    """Burn the annotations into a copy of `image` at its full resolution."""
    base = image.convert("RGBA")
    w, h = base.size
    scale = max(w, h)

    for shape in doc.shapes:
        if not shape.points:
            continue
        # each shape gets its own layer so opacity composites correctly and
        # overlapping highlighter strokes don't darken each other
        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(layer)
        pts = to_pixels(shape.points, w, h)
        line_w = max(1, int(shape.width * scale))
        alpha = int(max(0.0, min(1.0, shape.opacity)) * 255)
        color = _rgb(shape.color) + (alpha,)

        if shape.kind in FREEHAND:
            if len(pts) == 1:
                r = line_w / 2
                draw.ellipse([pts[0][0] - r, pts[0][1] - r, pts[0][0] + r, pts[0][1] + r], fill=color)
            else:
                draw.line(pts, fill=color, width=line_w, joint="curve")
        elif shape.kind == "line":
            draw.line([pts[0], pts[-1]], fill=color, width=line_w)
        elif shape.kind == "arrow":
            draw.line([pts[0], pts[-1]], fill=color, width=line_w)
            head = arrow_head(pts[0], pts[-1], max(line_w * 4, 10))
            if head:
                draw.polygon(head, fill=color)
        elif shape.kind == "rect":
            x0, y0 = pts[0]
            x1, y1 = pts[-1]
            draw.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
                           outline=color, width=line_w)
        elif shape.kind == "ellipse":
            x0, y0 = pts[0]
            x1, y1 = pts[-1]
            draw.ellipse([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
                         outline=color, width=line_w)
        elif shape.kind == "text" and shape.text:
            size = max(10, int(shape.width * scale * 6))
            draw.text(pts[0], shape.text, fill=color, font=_font(size))

        base = Image.alpha_composite(base, layer)

    return base.convert("RGB")


def default_width_for(tool: str) -> float:
    return {"highlighter": 0.03, "text": 0.02}.get(tool, 0.005)


def default_opacity_for(tool: str) -> float:
    return 0.35 if tool == "highlighter" else 1.0
