import customtkinter as ctk
from tkinter import filedialog, messagebox, simpledialog, ttk
import tkinter as tk
import tkinter.font as tkfont
from typing import Optional, List, Dict
import json
import os
import sys
import shutil
import subprocess
import threading
import zipfile

from PIL import Image, ImageOps, ImageTk

from file_utils import (
    get_file_category, format_size, format_date,
    calculate_percentage, get_file_icon, is_image_file, ICONS,
)
from scanner import TreeScanner, Node
import analysis
import annotate
import imagenav
import treemap_render
from thumbnails import ThumbnailCache, fit_box
from version import VERSION
from updater import get_updater


ctk.set_default_color_theme("blue")

ACCENT = "#2563eb"
ACCENT_HOVER = "#1d4ed8"

# Size requested for the treemap hover preview.
PEEK_SIZE = (240, 240)


def _settings_file() -> str:
    base = os.environ.get('APPDATA') or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'FolderLens', 'settings.json')


def _asset(name: str) -> Optional[str]:
    if getattr(sys, 'frozen', False):
        base = os.path.join(sys._MEIPASS, 'assets')
    else:
        base = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'assets')
    path = os.path.join(base, name)
    return path if os.path.exists(path) else None


class AppSettings:
    def __init__(self):
        self.row_size = "medium"
        self.preview_enabled = True
        self.dark_mode = True
        self.last_folder = ""
        self.view = "Tree"
        self.treemap_thumbnails = True
        self.list_thumbnails = True
        self.peek_preview = True
        self.annotation_mode = "Basic"
        self.load()

    def row_height(self) -> int:
        return {"small": 24, "medium": 30, "large": 38}.get(self.row_size, 30)

    def font_size(self) -> int:
        return {"small": 10, "medium": 11, "large": 13}.get(self.row_size, 11)

    def load(self):
        try:
            with open(_settings_file(), 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('row_size') in ("small", "medium", "large"):
                self.row_size = data['row_size']
            if isinstance(data.get('preview_enabled'), bool):
                self.preview_enabled = data['preview_enabled']
            if isinstance(data.get('dark_mode'), bool):
                self.dark_mode = data['dark_mode']
            if isinstance(data.get('last_folder'), str):
                self.last_folder = data['last_folder']
            if data.get('view') in ("Tree", "Treemap", "Largest Files", "File Types"):
                self.view = data['view']
            for flag in ('treemap_thumbnails', 'list_thumbnails', 'peek_preview'):
                if isinstance(data.get(flag), bool):
                    setattr(self, flag, data[flag])
            if data.get('annotation_mode') in ("Basic", "Advanced"):
                self.annotation_mode = data['annotation_mode']
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            path = _settings_file()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({
                    'row_size': self.row_size,
                    'preview_enabled': self.preview_enabled,
                    'dark_mode': self.dark_mode,
                    'last_folder': self.last_folder,
                    'view': self.view,
                    'treemap_thumbnails': self.treemap_thumbnails,
                    'list_thumbnails': self.list_thumbnails,
                    'peek_preview': self.peek_preview,
                    'annotation_mode': self.annotation_mode,
                }, f, indent=2)
        except OSError:
            pass


DARK = {
    'tree_bg': '#1b1b1b', 'tree_fg': '#e6e6e6',
    'sel_bg': '#1d4ed8', 'sel_fg': '#ffffff',
    'head_bg': '#262626', 'head_fg': '#bdbdbd',
    'folder_fg': '#93c5fd', 'error_fg': '#f87171', 'muted_fg': '#8a8a8a',
    'canvas_bg': '#141414', 'tile_border': '#0f0f0f', 'tip_bg': '#000000', 'tip_fg': '#ffffff',
}
LIGHT = {
    'tree_bg': '#ffffff', 'tree_fg': '#1f2937',
    'sel_bg': '#bfdbfe', 'sel_fg': '#111827',
    'head_bg': '#f3f4f6', 'head_fg': '#4b5563',
    'folder_fg': '#1d4ed8', 'error_fg': '#dc2626', 'muted_fg': '#9ca3af',
    'canvas_bg': '#eef0f3', 'tile_border': '#ffffff', 'tip_bg': '#1f2937', 'tip_fg': '#ffffff',
}


class Tooltip:
    """Floating tooltip for the treemap, with an optional peek thumbnail."""

    def __init__(self, master):
        self.tip = tk.Toplevel(master)
        self.tip.withdraw()
        self.tip.overrideredirect(True)
        self.tip.attributes("-topmost", True)
        self.frame = tk.Frame(self.tip, bd=0)
        self.frame.pack()
        self.image_label = tk.Label(self.frame, bd=0)
        self.label = tk.Label(self.frame, justify="left", padx=8, pady=5,
                              font=("Segoe UI", 9), bd=0)
        self.label.pack(fill="x")
        self._photo = None

    def show(self, text: str, x: int, y: int, colors: dict, image=None):
        self.frame.configure(bg=colors['tip_bg'])
        self.label.configure(text=text, bg=colors['tip_bg'], fg=colors['tip_fg'])

        if image is not None:
            self._photo = ImageTk.PhotoImage(image)
            self.image_label.configure(image=self._photo, bg=colors['tip_bg'])
            self.image_label.pack(before=self.label, padx=6, pady=(6, 0))
        else:
            self._photo = None
            self.image_label.pack_forget()

        # keep the tooltip on screen instead of running off the right/bottom
        self.tip.update_idletasks()
        w = self.tip.winfo_reqwidth()
        h = self.tip.winfo_reqheight()
        screen_w = self.tip.winfo_screenwidth()
        screen_h = self.tip.winfo_screenheight()
        px = x + 18 if x + 18 + w < screen_w else max(0, x - w - 18)
        py = y + 18 if y + 18 + h < screen_h else max(0, y - h - 18)
        self.tip.geometry(f"+{px}+{py}")
        self.tip.deiconify()

    def hide(self):
        self.tip.withdraw()


class ImageViewer(ctk.CTkToplevel):
    """Image viewer with folder navigation and annotation.

    Annotations are held in normalized coordinates, so they follow the image
    through zooming and window resizing and can be exported at full
    resolution.
    """

    # below this width the annotation actions move to their own row
    TOOLS_NARROW_WIDTH = 1180

    def __init__(self, master, image_path: str, settings: AppSettings, **kwargs):
        super().__init__(master, **kwargs)

        self.settings = settings
        self.nav = imagenav.ImageNavigator(image_path)
        self.doc = annotate.AnnotationDocument()
        self.image: Optional[Image.Image] = None
        self.photo = None
        self._draw_geometry = (0, 0, 1, 1)      # x, y, w, h of the drawn image
        self._active_points: List[tuple] = []
        self._preview_ids: List[int] = []
        self._dirty = False

        self.mode = ctk.StringVar(value=settings.annotation_mode)
        self.tool = ctk.StringVar(value="pen")
        self.color = ctk.StringVar(value=annotate.PALETTE[0])
        self.brush = ctk.DoubleVar(value=annotate.default_width_for("pen"))

        self.title(os.path.basename(image_path))
        self.geometry("1100x780")
        self.minsize(720, 520)
        self.transient(master)

        self._build_ui()
        self._load(self.nav.current or image_path)

        self.bind("<Right>", lambda e: self._go_next())
        self.bind("<Left>", lambda e: self._go_prev())
        self.bind("<Control-z>", lambda e: self._undo())
        self.bind("<Control-y>", lambda e: self._redo())
        self.bind("<Escape>", lambda e: self.destroy())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ ui

    def _build_ui(self):
        nav = ctk.CTkFrame(self, fg_color=("gray93", "gray17"), corner_radius=0)
        nav.pack(fill="x")

        ctk.CTkButton(nav, text="◀", width=42, height=32, command=self._go_prev,
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=(10, 4), pady=8)
        ctk.CTkButton(nav, text="▶", width=42, height=32, command=self._go_next,
                      font=ctk.CTkFont(size=14)).pack(side="left", padx=4, pady=8)

        self.counter = ctk.CTkLabel(nav, text="", font=ctk.CTkFont(size=12), width=64)
        self.counter.pack(side="left", padx=6)

        ctk.CTkButton(nav, text="⬅ Up", width=64, height=32, font=ctk.CTkFont(size=12),
                      fg_color="transparent", border_width=1, text_color=("gray20", "gray80"),
                      command=self._go_parent).pack(side="left", padx=(12, 4), pady=8)

        self.folder_menu = ctk.CTkOptionMenu(nav, values=["(no subfolders)"], width=170, height=32,
                                             font=ctk.CTkFont(size=12), command=self._open_subfolder)
        self.folder_menu.pack(side="left", padx=4, pady=8)

        # the mode switch is packed before the flexible label so it always
        # keeps its space instead of being pushed off the edge
        ctk.CTkSegmentedButton(nav, values=["Off", "Basic", "Advanced"], variable=self.mode,
                               command=self._on_mode_change, height=32,
                               font=ctk.CTkFont(size=12), selected_color=ACCENT,
                               selected_hover_color=ACCENT_HOVER).pack(side="right", padx=(0, 10), pady=8)
        ctk.CTkLabel(nav, text="Annotate", font=ctk.CTkFont(size=12)).pack(side="right", padx=(4, 4))

        self.name_label = ctk.CTkLabel(nav, text="", font=ctk.CTkFont(size=12),
                                       text_color=("gray30", "gray70"), anchor="w")
        self.name_label.pack(side="left", padx=12, fill="x", expand=True)

        # --- annotation toolbar (only shown when annotating).
        # Two rows: the action group drops below the tools when the window is
        # too narrow to hold both, so "Save as…" can never be clipped off the
        # right edge.
        self.tools_bar = ctk.CTkFrame(self, fg_color=("gray96", "gray14"), corner_radius=0)
        self.tool_buttons: Dict[str, ctk.CTkButton] = {}
        self.tools_row_a = ctk.CTkFrame(self.tools_bar, fg_color="transparent")
        self.tools_row_a.pack(fill="x")
        self.tools_row_b = ctk.CTkFrame(self.tools_bar, fg_color="transparent")
        self.tools_left = ctk.CTkFrame(self.tools_bar, fg_color="transparent")
        self.tools_right = ctk.CTkFrame(self.tools_bar, fg_color="transparent")
        self._tools_narrow = None
        self.bind("<Configure>", self._on_viewer_configure, add="+")

        self.canvas = tk.Canvas(self, highlightthickness=0, bd=0,
                                bg="#141414" if self.settings.dark_mode else "#e9ecf1",
                                cursor="arrow")
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        status = ctk.CTkFrame(self, fg_color=("gray93", "gray17"), corner_radius=0, height=26)
        status.pack(fill="x", side="bottom")
        status.pack_propagate(False)
        self.status = ctk.CTkLabel(status, text="", font=ctk.CTkFont(size=11), text_color="gray")
        self.status.pack(side="left", padx=10)

        self._build_tools()
        self._on_mode_change(self.mode.get())

    def _build_tools(self):
        for group in (self.tools_left, self.tools_right):
            for child in group.winfo_children():
                child.destroy()
        self.tool_buttons.clear()

        tools = (annotate.ADVANCED_TOOLS if self.mode.get() == "Advanced"
                 else annotate.BASIC_TOOLS)
        labels = {"pen": "✏ Pen", "highlighter": "🖍 Marker", "line": "╱ Line",
                  "arrow": "➔ Arrow", "rect": "▭ Rect", "ellipse": "◯ Ellipse",
                  "text": "T Text", "eraser": "🧽 Erase"}

        if self.tool.get() not in tools:
            self.tool.set(tools[0])

        for name in tools:
            btn = ctk.CTkButton(self.tools_left, text=labels.get(name, name), width=76, height=30,
                                font=ctk.CTkFont(size=12), command=lambda n=name: self._select_tool(n))
            btn.pack(side="left", padx=2)
            self.tool_buttons[name] = btn

        for hexcolor in annotate.PALETTE:
            ctk.CTkButton(self.tools_left, text="", width=22, height=22, corner_radius=11,
                          fg_color=hexcolor, hover_color=hexcolor, border_width=1,
                          border_color=("gray60", "gray40"),
                          command=lambda c=hexcolor: self.color.set(c)).pack(side="left", padx=2)

        # right-hand group: packed as its own unit so it always has room
        ctk.CTkButton(self.tools_right, text="💾 Save as…", width=106, height=30,
                      font=ctk.CTkFont(size=12), fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._save_as).pack(side="right", padx=(2, 6))
        ctk.CTkButton(self.tools_right, text="Clear", width=58, height=30, font=ctk.CTkFont(size=12),
                      fg_color="transparent", border_width=1, text_color=("gray20", "gray80"),
                      command=self._clear).pack(side="right", padx=2)
        if self.mode.get() == "Advanced":
            ctk.CTkButton(self.tools_right, text="↷", width=34, height=30, font=ctk.CTkFont(size=14),
                          command=self._redo).pack(side="right", padx=2)
        ctk.CTkButton(self.tools_right, text="↶", width=34, height=30, font=ctk.CTkFont(size=14),
                      command=self._undo).pack(side="right", padx=2)
        ctk.CTkSlider(self.tools_right, from_=0.002, to=0.05, variable=self.brush,
                      width=110, height=16).pack(side="right", padx=(2, 8))
        ctk.CTkLabel(self.tools_right, text="Size",
                     font=ctk.CTkFont(size=11)).pack(side="right", padx=(8, 2))

        self._tools_narrow = None          # force a layout pass
        self._reflow_tools(self.winfo_width() or 1100)
        self._select_tool(self.tool.get())

    def _on_viewer_configure(self, event):
        if event.widget is self:
            self._reflow_tools(event.width)

    def _reflow_tools(self, width: int):
        """Give the action buttons their own row when the window is too narrow
        to fit them beside the tools."""
        narrow = width < self.TOOLS_NARROW_WIDTH
        if narrow == self._tools_narrow:
            return
        self._tools_narrow = narrow

        self.tools_left.pack_forget()
        self.tools_right.pack_forget()

        if narrow:
            self.tools_row_b.pack(fill="x")
            self.tools_left.pack(in_=self.tools_row_a, side="left", padx=8, pady=(6, 2))
            self.tools_right.pack(in_=self.tools_row_b, side="right", padx=8, pady=(0, 6))
        else:
            self.tools_row_b.pack_forget()
            # right group first: it reserves its width before the tools claim it
            self.tools_right.pack(in_=self.tools_row_a, side="right", padx=8, pady=6)
            self.tools_left.pack(in_=self.tools_row_a, side="left", padx=8, pady=6)

    def _select_tool(self, name: str):
        self.tool.set(name)
        self.brush.set(annotate.default_width_for(name))
        for tool_name, btn in self.tool_buttons.items():
            active = tool_name == name
            btn.configure(fg_color=ACCENT if active else "transparent",
                          border_width=0 if active else 1,
                          text_color="white" if active else ("gray20", "gray80"))

    def _on_mode_change(self, value: str):
        # the value is authoritative: this is also called directly, not only
        # by the segmented button that owns the variable
        if self.mode.get() != value:
            self.mode.set(value)
        self.settings.annotation_mode = value if value != "Off" else self.settings.annotation_mode
        self.settings.save()
        if value == "Off":
            self.tools_bar.pack_forget()
            self.canvas.configure(cursor="arrow")
        else:
            self.tools_bar.pack(fill="x", before=self.canvas)
            self.canvas.configure(cursor="crosshair")
            self._build_tools()
        self._redraw()

    # -------------------------------------------------------------- loading

    def _load(self, path: str):
        if self._dirty and not self._confirm_discard():
            return
        try:
            with Image.open(path) as src:
                self.image = ImageOps.exif_transpose(src).convert("RGB")
        except Exception as exc:
            self.image = None
            self.status.configure(text=f"Cannot load image: {exc}")

        self.doc = annotate.AnnotationDocument()
        self._dirty = False
        self.nav.go_to(path)
        self.title(os.path.basename(path))
        self.name_label.configure(text=os.path.basename(path))
        self.counter.configure(text=self.nav.position)

        subfolders = self.nav.subfolders()
        names = [os.path.basename(p) for p in subfolders] or ["(no subfolders)"]
        self.folder_menu.configure(values=names)
        self.folder_menu.set(names[0])
        self._subfolders = {os.path.basename(p): p for p in subfolders}

        if self.image is not None:
            self.status.configure(
                text=f"{self.image.width} × {self.image.height}  ·  {format_size(os.path.getsize(path))}"
                if os.path.exists(path) else "")
        self._redraw()

    def _confirm_discard(self) -> bool:
        return messagebox.askyesno("Discard annotations?",
                                   "This image has unsaved annotations.\nDiscard them?",
                                   parent=self)

    def _go_next(self):
        nxt = self.nav.next()
        if nxt:
            self._load(nxt)

    def _go_prev(self):
        prev = self.nav.previous()
        if prev:
            self._load(prev)

    def _go_parent(self):
        parent = self.nav.parent()
        if not parent:
            return
        first = self.nav.open_folder(parent)
        if first:
            self._load(first)
        else:
            self.status.configure(text="No images in that folder")
            self.counter.configure(text=self.nav.position)

    def _open_subfolder(self, name: str):
        folder = getattr(self, "_subfolders", {}).get(name)
        if not folder:
            return
        first = self.nav.open_folder(folder)
        if first:
            self._load(first)
        else:
            self.status.configure(text=f"No images in {name}")
            self.counter.configure(text=self.nav.position)

    # ------------------------------------------------------------- drawing

    def _redraw(self):
        self.canvas.delete("all")
        self._preview_ids.clear()
        if self.image is None:
            return
        cw = max(self.canvas.winfo_width(), 1)
        ch = max(self.canvas.winfo_height(), 1)
        if cw < 10 or ch < 10:
            return

        w, h = fit_box(self.image.width, self.image.height, cw - 20, ch - 20)
        if w < 1 or h < 1:
            return
        resized = self.image.resize((w, h), Image.Resampling.LANCZOS)
        self.photo = ImageTk.PhotoImage(resized)
        x, y = (cw - w) // 2, (ch - h) // 2
        self._draw_geometry = (x, y, w, h)
        self.canvas.create_image(x, y, anchor="nw", image=self.photo)

        for shape in self.doc.shapes:
            self._draw_shape(shape)

    def _to_canvas(self, point) -> tuple:
        x, y, w, h = self._draw_geometry
        return (x + point[0] * w, y + point[1] * h)

    def _to_image(self, cx: float, cy: float) -> tuple:
        x, y, w, h = self._draw_geometry
        return ((cx - x) / w if w else 0.0, (cy - y) / h if h else 0.0)

    def _draw_shape(self, shape, preview: bool = False):
        pts = [self._to_canvas(p) for p in shape.points]
        if not pts:
            return
        _, _, w, h = self._draw_geometry
        width = max(1, int(shape.width * max(w, h)))
        color = shape.color
        # Tk has no per-item alpha, so approximate the highlighter by blending
        # its colour toward the page instead
        if shape.opacity < 1.0:
            color = _blend_hex(shape.color, "#ffffff" if not self.settings.dark_mode else "#202020",
                               1 - shape.opacity)

        ids = []
        kind = shape.kind
        if kind in annotate.FREEHAND and len(pts) > 1:
            ids.append(self.canvas.create_line(*[c for p in pts for c in p],
                                               fill=color, width=width,
                                               capstyle="round", joinstyle="round", smooth=True))
        elif kind in annotate.FREEHAND:
            r = width / 2
            ids.append(self.canvas.create_oval(pts[0][0] - r, pts[0][1] - r,
                                               pts[0][0] + r, pts[0][1] + r, fill=color, outline=color))
        elif kind == "line":
            ids.append(self.canvas.create_line(*pts[0], *pts[-1], fill=color, width=width, capstyle="round"))
        elif kind == "arrow":
            ids.append(self.canvas.create_line(*pts[0], *pts[-1], fill=color, width=width,
                                               capstyle="round", arrow="last",
                                               arrowshape=(width * 4, width * 5, width * 2)))
        elif kind == "rect":
            ids.append(self.canvas.create_rectangle(*pts[0], *pts[-1], outline=color, width=width))
        elif kind == "ellipse":
            ids.append(self.canvas.create_oval(*pts[0], *pts[-1], outline=color, width=width))
        elif kind == "text" and shape.text:
            size = max(8, int(shape.width * max(w, h) * 6))
            ids.append(self.canvas.create_text(*pts[0], text=shape.text, fill=color,
                                               anchor="nw", font=("Segoe UI", size)))
        if preview:
            self._preview_ids.extend(ids)

    # -------------------------------------------------------------- events

    def _annotating(self) -> bool:
        return self.mode.get() != "Off" and self.image is not None

    def _on_press(self, event):
        if not self._annotating():
            return
        point = self._to_image(event.x, event.y)
        if self.tool.get() == "eraser":
            if self.doc.erase_at(*point):
                self._dirty = True
                self._redraw()
            return
        if self.tool.get() == "text":
            text = simpledialog.askstring("Text", "Annotation text:", parent=self)
            if text:
                self.doc.add(annotate.Shape(kind="text", points=[point], color=self.color.get(),
                                            width=self.brush.get(), text=text))
                self._dirty = True
                self._redraw()
            return
        self._active_points = [point]

    def _on_drag(self, event):
        if not self._annotating() or not self._active_points:
            return
        point = self._to_image(event.x, event.y)
        tool = self.tool.get()
        if tool in annotate.FREEHAND:
            self._active_points.append(point)
        else:
            self._active_points = [self._active_points[0], point]

        for item in self._preview_ids:
            self.canvas.delete(item)
        self._preview_ids.clear()
        self._draw_shape(self._current_shape(), preview=True)

    def _on_release(self, event):
        if not self._annotating() or not self._active_points:
            return
        shape = self._current_shape()
        for item in self._preview_ids:
            self.canvas.delete(item)
        self._preview_ids.clear()
        self._active_points = []

        if len(shape.points) == 1 and shape.kind not in annotate.FREEHAND:
            return          # a click with no drag: nothing to draw
        self.doc.add(shape)
        self._dirty = True
        self._redraw()

    def _current_shape(self):
        tool = self.tool.get()
        return annotate.Shape(kind=tool, points=list(self._active_points),
                              color=self.color.get(), width=self.brush.get(),
                              opacity=annotate.default_opacity_for(tool))

    # ------------------------------------------------------------- actions

    def _undo(self):
        if self.doc.undo():
            self._dirty = self.doc.can_undo
            self._redraw()

    def _redo(self):
        if self.doc.redo():
            self._dirty = True
            self._redraw()

    def _clear(self):
        self.doc.clear()
        self._dirty = False
        self._redraw()

    def _save_as(self):
        if self.image is None:
            return
        if self.doc.is_empty:
            messagebox.showinfo("Nothing to save", "Draw something first.", parent=self)
            return
        current = self.nav.current or ""
        stem, ext = os.path.splitext(os.path.basename(current))
        target = filedialog.asksaveasfilename(
            parent=self, defaultextension=ext or ".png",
            initialfile=f"{stem}_annotated{ext or '.png'}",
            filetypes=[("PNG", "*.png"), ("JPEG", "*.jpg"), ("All files", "*.*")],
            title="Save annotated image as")
        if not target:
            return
        try:
            rendered = annotate.render_to_image(self.doc, self.image)
            rendered.save(target)
            self._dirty = False
            self.status.configure(text=f"Saved {os.path.basename(target)}")
        except Exception as exc:
            messagebox.showerror("Save failed", str(exc), parent=self)

    def _on_close(self):
        if self._dirty and not messagebox.askyesno(
                "Discard annotations?", "You have unsaved annotations.\nClose anyway?", parent=self):
            return
        self.destroy()


def _blend_hex(color: str, towards: str, amount: float) -> str:
    """Mix two #rrggbb colours; used to fake alpha on the Tk canvas."""
    def parse(value):
        value = value.lstrip("#")
        if len(value) == 3:
            value = "".join(c * 2 for c in value)
        return [int(value[i:i + 2], 16) for i in (0, 2, 4)]
    a, b = parse(color), parse(towards)
    amount = max(0.0, min(1.0, amount))
    return "#%02x%02x%02x" % tuple(int(a[i] * (1 - amount) + b[i] * amount) for i in range(3))


class SettingsMenu(ctk.CTkToplevel):
    def __init__(self, master, settings: AppSettings, on_apply, **kwargs):
        super().__init__(master, **kwargs)
        self.settings = settings
        self.on_apply = on_apply
        self.title("Settings")
        # resizable, and the content scrolls: at the old fixed size the Apply
        # button could end up off the bottom of the dialog
        self.geometry("400x460")
        self.minsize(340, 300)
        self.transient(master)
        self.grab_set()
        self.update_idletasks()
        x = master.winfo_x() + (master.winfo_width() - 400) // 2
        y = master.winfo_y() + (master.winfo_height() - 460) // 2
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")

        # buttons first and packed to the bottom so they can never be pushed
        # out of view by the content above them
        buttons = ctk.CTkFrame(self, fg_color="transparent")
        buttons.pack(side="bottom", fill="x", padx=20, pady=(0, 16))
        ctk.CTkButton(buttons, text="Apply", height=34, command=self._apply).pack(side="right")
        ctk.CTkButton(buttons, text="Cancel", height=34, fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray80"), command=self.destroy).pack(side="right", padx=8)

        main = ctk.CTkScrollableFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(main, text="Row size", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
        self.size_var = ctk.StringVar(value=self.settings.row_size)
        row = ctk.CTkFrame(main, fg_color="transparent")
        row.pack(fill="x", pady=(5, 12))
        for size in ["small", "medium", "large"]:
            ctk.CTkRadioButton(row, text=size.capitalize(), variable=self.size_var,
                               value=size).pack(side="left", padx=8)

        ctk.CTkLabel(main, text="Previews", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(8, 0))

        self.preview_var = ctk.BooleanVar(value=self.settings.preview_enabled)
        ctk.CTkSwitch(main, text="Open images on double-click",
                      variable=self.preview_var).pack(anchor="w", pady=6)

        self.peek_var = ctk.BooleanVar(value=self.settings.peek_preview)
        ctk.CTkSwitch(main, text="Peek preview when hovering the treemap",
                      variable=self.peek_var).pack(anchor="w", pady=6)

        self.treemap_thumbs_var = ctk.BooleanVar(value=self.settings.treemap_thumbnails)
        ctk.CTkSwitch(main, text="Show image thumbnails in the treemap",
                      variable=self.treemap_thumbs_var).pack(anchor="w", pady=6)

        self.list_thumbs_var = ctk.BooleanVar(value=self.settings.list_thumbnails)
        ctk.CTkSwitch(main, text="Show small previews in lists",
                      variable=self.list_thumbs_var).pack(anchor="w", pady=6)

        ctk.CTkLabel(main, text="Annotation", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(12, 0))
        ctk.CTkLabel(main, text="Basic: pen, marker, arrow, eraser.\nAdvanced: adds shapes, text, redo.",
                     font=ctk.CTkFont(size=11), text_color="gray",
                     justify="left").pack(anchor="w", pady=(2, 6))
        self.annotation_var = ctk.StringVar(value=self.settings.annotation_mode)
        ctk.CTkSegmentedButton(main, values=["Basic", "Advanced"], variable=self.annotation_var,
                               height=32).pack(anchor="w", pady=4)

    def _apply(self):
        self.settings.row_size = self.size_var.get()
        self.settings.preview_enabled = self.preview_var.get()
        self.settings.peek_preview = self.peek_var.get()
        self.settings.treemap_thumbnails = self.treemap_thumbs_var.get()
        self.settings.list_thumbnails = self.list_thumbs_var.get()
        self.settings.annotation_mode = self.annotation_var.get()
        self.on_apply()
        self.destroy()


class UpdateDialog(ctk.CTkToplevel):
    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)
        self.updater = get_updater()
        self.update_info = None
        self.downloaded_file = None
        self.title("Updates")
        self.geometry("460x360")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()
        self.update_idletasks()
        x = master.winfo_x() + (master.winfo_width() - 460) // 2
        y = master.winfo_y() + (master.winfo_height() - 360) // 2
        self.geometry(f"+{x}+{y}")

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=24, pady=24)
        ctk.CTkLabel(main, text="🔄 Check for Updates", font=ctk.CTkFont(size=18, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(main, text=f"Current version: {VERSION}", font=ctk.CTkFont(size=12), text_color="gray").pack(anchor="w", pady=(4, 0))

        self.status_frame = ctk.CTkFrame(main, fg_color=("gray90", "gray20"), corner_radius=8)
        self.status_frame.pack(fill="both", expand=True, pady=20)
        self.status_label = ctk.CTkLabel(self.status_frame, text="Checking for updates...", font=ctk.CTkFont(size=14))
        self.status_label.pack(expand=True)
        self.progress = ctk.CTkProgressBar(self.status_frame, width=320)
        self.progress.set(0)
        self.notes_text = ctk.CTkTextbox(self.status_frame, height=120, font=ctk.CTkFont(size=11))

        btns = ctk.CTkFrame(main, fg_color="transparent")
        btns.pack(fill="x")
        self.action_btn = ctk.CTkButton(btns, text="Check Again", command=self._check, state="disabled")
        self.action_btn.pack(side="left")
        ctk.CTkButton(btns, text="Close", fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray80"), command=self.destroy).pack(side="right")
        self._check()

    def _check(self):
        self.status_label.configure(text="Checking for updates...")
        self.action_btn.configure(state="disabled")
        self.progress.pack_forget()
        self.notes_text.pack_forget()
        self.updater.check_for_updates_async(lambda a, i, e: self.after(0, lambda: self._checked(a, i, e)))

    def _checked(self, available, info, error):
        if error:
            self.status_label.configure(text=f"❌ {error}")
            self.action_btn.configure(text="Check Again", command=self._check, state="normal")
        elif available and info:
            self.update_info = info
            self.status_label.configure(text=f"✅ New version available: {info.version}")
            self.status_label.pack(pady=(16, 8))
            self.notes_text.delete("1.0", "end")
            self.notes_text.insert("1.0", info.release_notes[:500])
            self.notes_text.pack(fill="both", expand=True, padx=16, pady=(0, 16))
            self.action_btn.configure(text="Download & Install", command=self._download, state="normal")
        else:
            self.status_label.configure(text="✅ You're running the latest version!")
            self.action_btn.configure(text="Check Again", command=self._check, state="normal")

    def _download(self):
        if not self.update_info:
            return
        self.status_label.configure(text="Downloading update...")
        self.notes_text.pack_forget()
        self.progress.set(0)
        self.progress.pack(pady=16)
        self.action_btn.configure(state="disabled")
        self.updater.download_update_async(
            self.update_info,
            progress_callback=lambda d, t: self.after(0, lambda: self.progress.set(d / t if t else 0)),
            complete_callback=lambda s, f, e: self.after(0, lambda: self._downloaded(s, f, e)),
        )

    def _downloaded(self, success, file_path, error):
        if success and file_path:
            self.downloaded_file = file_path
            self.status_label.configure(text="Download complete! Ready to install.")
            self.progress.set(1)
            self.action_btn.configure(text="Install & Restart", command=self._apply, state="normal")
        else:
            self.status_label.configure(text=f"❌ Download failed: {error}")
            self.progress.pack_forget()
            self.action_btn.configure(text="Try Again", command=self._download, state="normal")

    def _apply(self):
        if not self.downloaded_file:
            return
        success, error = self.updater.apply_update(self.downloaded_file)
        if success:
            self.status_label.configure(text="Installing update... The app will restart.")
            self.action_btn.configure(state="disabled")
            self.after(1500, lambda: self.master.destroy())
        else:
            messagebox.showerror("Update Failed", error or "Could not apply update.")


class FolderLensApp(ctk.CTk):
    """Fast, multi-view folder size explorer."""

    BAR_WIDTH = 10
    VIEWS = ["Tree", "Treemap", "Largest Files", "File Types"]

    def __init__(self, initial_path: Optional[str] = None):
        super().__init__()
        self.title(f"FolderLens {VERSION}")
        self.geometry("1280x820")
        self.minsize(940, 620)
        self._set_window_icon()

        self.settings = AppSettings()
        ctk.set_appearance_mode("dark" if self.settings.dark_mode else "light")

        self.scanner = TreeScanner()
        self.root_node: Optional[Node] = None
        self.scan_errors: List[str] = []
        self.scan_time = 0.0
        self.active_view = self.settings.view
        self.search_query = ""
        self._search_after = None

        # tree-view state
        self.tree: Optional[ttk.Treeview] = None
        self.iid_to_node: Dict[str, Node] = {}
        self.sort_key = "size"
        self.sort_reverse = True

        # largest-files view state
        self.largest_tree: Optional[ttk.Treeview] = None
        self.largest_map: Dict[str, Node] = {}

        # treemap state
        self.treemap_stack: List[Node] = []
        self._tiles: List[analysis.Tile] = []
        self._hover_tile = None
        self._treemap_photo = None
        self._peek_path: Optional[str] = None
        self._peek_args = None
        self.tooltip: Optional[Tooltip] = None
        self._treemap_redraw_after = None

        # thumbnails (decoded off the UI thread, shared by every view)
        self.thumbnails = ThumbnailCache()
        self.thumbnails.set_ready_callback(self._on_thumbnail_ready)
        self._row_photos: Dict[str, ImageTk.PhotoImage] = {}
        self._row_by_path: Dict[str, List[tuple]] = {}

        self._build_toolbar()
        self._build_body()
        self._build_status_bar()

        self.bind("<F5>", lambda e: self._refresh())
        self.bind("<Control-f>", lambda e: self.search_entry.focus_set())
        self.bind("<Escape>", lambda e: self._clear_search())

        start = initial_path or (self.settings.last_folder if os.path.isdir(self.settings.last_folder or "") else None)
        if start and os.path.isdir(start):
            self.after(120, lambda: self.scan_folder(os.path.abspath(start)))
        else:
            self._set_status("Select a folder to analyze")

    # -------------------------------------------------------------- chrome

    def _set_window_icon(self):
        try:
            ico = _asset('icon.ico')
            if ico and sys.platform == "win32":
                self.iconbitmap(ico)
            png = _asset('icon.png')
            if png:
                self._icon_img = ImageTk.PhotoImage(Image.open(png))
                self.iconphoto(True, self._icon_img)
        except Exception:
            pass

    def _colors(self) -> dict:
        return DARK if self.settings.dark_mode else LIGHT

    # Below this window width the toolbar splits onto two rows. Packing
    # everything into one fixed row silently clipped whatever didn't fit,
    # which is why buttons went missing on smaller windows.
    NARROW_WIDTH = 1120

    def _build_toolbar(self):
        self.toolbar = ctk.CTkFrame(self, fg_color=("gray95", "gray14"), corner_radius=0)
        self.toolbar.pack(fill="x")

        # two rows; the second is only packed when the window is narrow
        self.toolbar_row1 = ctk.CTkFrame(self.toolbar, fg_color="transparent", height=52)
        self.toolbar_row1.pack(fill="x")
        self.toolbar_row1.pack_propagate(False)
        self.toolbar_row2 = ctk.CTkFrame(self.toolbar, fg_color="transparent", height=48)
        self.toolbar_row2.pack_propagate(False)

        r1 = self.toolbar_row1

        ctk.CTkButton(r1, text=f"{ICONS['folder_open']}  Browse", width=104, height=34,
                      font=ctk.CTkFont(size=12, weight="bold"), fg_color=ACCENT, hover_color=ACCENT_HOVER,
                      command=self._browse_folder).pack(side="left", padx=(12, 6), pady=9)
        ctk.CTkButton(r1, text="⬅", width=38, height=34, font=ctk.CTkFont(size=14),
                      fg_color="transparent", border_width=1, text_color=("gray20", "gray80"),
                      command=self._go_up).pack(side="left", padx=3, pady=9)
        ctk.CTkButton(r1, text=ICONS['refresh'], width=38, height=34, font=ctk.CTkFont(size=14),
                      fg_color="transparent", border_width=1, text_color=("gray20", "gray80"),
                      command=self._refresh).pack(side="left", padx=3, pady=9)
        self.cancel_btn = ctk.CTkButton(r1, text="✕ Stop", width=68, height=34, font=ctk.CTkFont(size=12),
                                        fg_color="#b91c1c", hover_color="#991b1b", command=self._cancel_scan)

        self.view_switch = ctk.CTkSegmentedButton(
            r1, values=self.VIEWS, command=self._on_view_change,
            font=ctk.CTkFont(size=12), height=34,
            selected_color=ACCENT, selected_hover_color=ACCENT_HOVER,
        )
        self.view_switch.set(self.active_view)
        self.view_switch.pack(side="left", padx=12, pady=9)

        # --- widgets that move between rows depending on the window width.
        # Their parent is the toolbar itself, not a row: Tk only allows
        # packing a widget into its parent or a descendant of it, so a
        # row-parented widget could never move to the sibling row.
        self.actions = ctk.CTkFrame(self.toolbar, fg_color="transparent")

        ctk.CTkButton(self.actions, text="•••", width=40, height=34, font=ctk.CTkFont(size=14),
                      fg_color="transparent", text_color=("gray30", "gray70"),
                      hover_color=("gray85", "gray25"),
                      command=self._show_settings).pack(side="right", padx=(4, 4))
        ctk.CTkButton(self.actions, text="⬆", width=40, height=34, font=ctk.CTkFont(size=14),
                      fg_color="transparent", text_color=("gray30", "gray70"),
                      hover_color=("gray85", "gray25"),
                      command=lambda: UpdateDialog(self)).pack(side="right", padx=4)
        self.theme_btn = ctk.CTkButton(self.actions, text=ICONS['sun'] if self.settings.dark_mode else ICONS['moon'],
                                       width=40, height=34, font=ctk.CTkFont(size=14), fg_color="transparent",
                                       text_color=("gray30", "gray70"), hover_color=("gray85", "gray25"),
                                       command=self._toggle_theme)
        self.theme_btn.pack(side="right", padx=4)
        ctk.CTkButton(self.actions, text="⬇ CSV", width=70, height=34, font=ctk.CTkFont(size=12),
                      fg_color="transparent", border_width=1, text_color=("gray20", "gray80"),
                      command=self._export_csv).pack(side="right", padx=4)

        self.search_var = ctk.StringVar()
        self.search_entry = ctk.CTkEntry(self.toolbar, textvariable=self.search_var,
                                         width=210, height=34,
                                         placeholder_text="Search files & folders…")
        self.search_var.trace_add("write", lambda *a: self._on_search_change())

        self._toolbar_narrow = None
        self.bind("<Configure>", self._on_window_configure, add="+")
        self.after(80, lambda: self._reflow_toolbar(self.winfo_width()))

    def _on_window_configure(self, event):
        if event.widget is self:
            self._reflow_toolbar(event.width)

    def _reflow_toolbar(self, width: int):
        """Move the search box and action buttons onto their own row when the
        window is too narrow to show everything side by side."""
        narrow = width < self.NARROW_WIDTH
        if narrow == self._toolbar_narrow:
            return
        self._toolbar_narrow = narrow

        self.search_entry.pack_forget()
        self.actions.pack_forget()

        if narrow:
            self.toolbar_row2.pack(fill="x")
            self.search_entry.pack(in_=self.toolbar_row2, side="left", padx=(12, 6), pady=7)
            self.actions.pack(in_=self.toolbar_row2, side="right", padx=(4, 8), pady=4)
        else:
            self.toolbar_row2.pack_forget()
            self.actions.pack(in_=self.toolbar_row1, side="right", padx=(4, 8), pady=6)
            self.search_entry.pack(in_=self.toolbar_row1, side="right", padx=6, pady=9)

    def _build_body(self):
        self.pathbar = ctk.CTkFrame(self, fg_color=("gray92", "gray16"), corner_radius=0, height=30)
        self.pathbar.pack(fill="x")
        self.pathbar.pack_propagate(False)
        self.path_label = ctk.CTkLabel(self.pathbar, text="", font=ctk.CTkFont(size=12),
                                       text_color=("gray30", "gray70"), anchor="w")
        self.path_label.pack(side="left", padx=14)

        self.body = tk.Frame(self, highlightthickness=0, bd=0)
        self.body.pack(fill="both", expand=True)

        self.progress = ctk.CTkProgressBar(self, height=3, corner_radius=0)
        self.progress.set(0)

        self._render_active_view()

    def _build_status_bar(self):
        bar = ctk.CTkFrame(self, fg_color=("gray95", "gray14"), corner_radius=0, height=28)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)
        self.status_left = ctk.CTkLabel(bar, text="Ready", font=ctk.CTkFont(size=11), text_color="gray")
        self.status_left.pack(side="left", padx=12)
        self.status_disk = ctk.CTkLabel(bar, text="", font=ctk.CTkFont(size=11), text_color="gray")
        self.status_disk.pack(side="left", padx=12)
        self.status_right = ctk.CTkLabel(bar, text="", font=ctk.CTkFont(size=11), text_color="gray")
        self.status_right.pack(side="right", padx=12)

    # --------------------------------------------------------------- scan

    def scan_folder(self, path: str):
        self.settings.last_folder = path
        self.settings.save()
        self.treemap_stack = []
        self.path_label.configure(text=path)
        self._set_status(f"Scanning {path} …")
        self._show_progress(True)
        self.cancel_btn.pack(side="left", padx=3, pady=9)

        self.scanner.scan(
            path,
            on_progress=lambda n: self.after(0, lambda: self._set_status(f"Scanning… {n:,} items")),
            on_complete=lambda root, errors, t: self.after(0, lambda: self._scan_done(root, errors, t)),
            on_error=lambda msg: self.after(0, lambda: self._scan_failed(msg)),
        )

    def _scan_done(self, root: Node, errors: List[str], scan_time: float):
        self.root_node = root
        self.scan_errors = errors
        self.scan_time = scan_time
        self._show_progress(False)
        self.cancel_btn.pack_forget()

        status = f"{root.item_count:,} items · {scan_time:.1f}s"
        if errors:
            status += f"  ·  ⚠ {len(errors)} inaccessible"
        self._set_status(status)
        self.status_right.configure(text=f"Total: {format_size(root.size)}")
        self._update_disk(root.path)
        self._render_active_view()

    def _scan_failed(self, message: str):
        self._show_progress(False)
        self.cancel_btn.pack_forget()
        self._set_status("Scan failed")
        messagebox.showerror("Error", message)

    def _cancel_scan(self):
        self.scanner.cancel()
        self._set_status("Scan cancelled")
        self._show_progress(False)
        self.cancel_btn.pack_forget()

    def _refresh(self):
        if self.root_node:
            self.scan_folder(self.root_node.path)

    def _browse_folder(self):
        folder = filedialog.askdirectory(title="Select folder to analyze")
        if folder:
            self.scan_folder(os.path.normpath(folder))

    def _go_up(self):
        if not self.root_node:
            return
        parent = os.path.dirname(self.root_node.path.rstrip("\\/"))
        if parent and parent != self.root_node.path and os.path.isdir(parent):
            self.scan_folder(parent)

    def _update_disk(self, path: str):
        try:
            usage = shutil.disk_usage(path)
            self.status_disk.configure(
                text=f"Disk: {format_size(usage.free)} free of {format_size(usage.total)}")
        except OSError:
            self.status_disk.configure(text="")

    def _show_progress(self, active: bool):
        if active:
            self.progress.pack(fill="x", before=self.body)
            self.progress.configure(mode="indeterminate")
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.pack_forget()

    # --------------------------------------------------------------- views

    def _on_view_change(self, value):
        self.active_view = value
        self.settings.view = value
        self.settings.save()
        self._render_active_view()

    def _clear_body(self):
        if self.tooltip:
            self.tooltip.hide()
        if self._treemap_redraw_after:
            self.after_cancel(self._treemap_redraw_after)
            self._treemap_redraw_after = None
        for child in self.body.winfo_children():
            child.destroy()
        # drop references to the widgets we just destroyed so nothing
        # reaches for a stale one later
        self.tree = None
        self.largest_tree = None

    def _render_active_view(self):
        self._clear_body()
        if self.active_view == "Tree":
            self._render_tree()
        elif self.active_view == "Treemap":
            self._render_treemap()
        elif self.active_view == "Largest Files":
            self._render_largest()
        elif self.active_view == "File Types":
            self._render_types()

    def _empty_hint(self, text: str):
        wrap = tk.Frame(self.body, bg=self._colors()['tree_bg'])
        wrap.pack(fill="both", expand=True)
        tk.Label(wrap, text=text, fg=self._colors()['muted_fg'], bg=self._colors()['tree_bg'],
                 font=("Segoe UI", 13)).place(relx=0.5, rely=0.45, anchor="center")

    # ---- shared treeview styling

    def _make_treeview(self, columns, headings, widths):
        colors = self._colors()
        fs = self.settings.font_size()
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("FolderLens.Treeview", background=colors['tree_bg'], fieldbackground=colors['tree_bg'],
                        foreground=colors['tree_fg'], rowheight=self.settings.row_height(),
                        borderwidth=0, font=("Segoe UI", fs))
        style.map("FolderLens.Treeview", background=[("selected", colors['sel_bg'])],
                  foreground=[("selected", colors['sel_fg'])])
        style.configure("FolderLens.Treeview.Heading", background=colors['head_bg'], foreground=colors['head_fg'],
                        borderwidth=0, font=("Segoe UI", fs - 1, "bold"))
        style.map("FolderLens.Treeview.Heading", background=[("active", colors['head_bg'])])

        wrap = tk.Frame(self.body, bg=colors['tree_bg'])
        wrap.pack(fill="both", expand=True)
        tree = ttk.Treeview(wrap, columns=columns, selectmode="extended", style="FolderLens.Treeview")
        for col, (text, cmd) in headings.items():
            tree.heading(col, text=text, anchor="w" if col == "#0" else "e", command=cmd)
        for col, (w, mn, anchor, stretch) in widths.items():
            tree.column(col, width=w, minwidth=mn, anchor=anchor, stretch=stretch)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        tree.pack(side="left", fill="both", expand=True)

        bold = tkfont.Font(family="Segoe UI", size=fs, weight="bold")
        tree.tag_configure("folder", foreground=colors['folder_fg'], font=bold)
        tree.tag_configure("error", foreground=colors['error_fg'])
        tree.tag_configure("dummy", foreground=colors['muted_fg'])
        return tree

    # ---- Tree view

    def _render_tree(self):
        if not self.root_node:
            self._empty_hint("Select a folder to analyze")
            return

        headings = {
            "#0": ("Name", lambda: self._sort_tree("name")),
            "usage": ("Usage", lambda: self._sort_tree("size")),
            "size": ("Size", lambda: self._sort_tree("size")),
            "items": ("Items", lambda: self._sort_tree("size")),
            "type": ("Type", lambda: self._sort_tree("type")),
            "modified": ("Created", lambda: self._sort_tree("date")),
        }
        widths = {
            "#0": (440, 220, "w", True),
            "usage": (170, 140, "w", False),
            "size": (100, 80, "e", False),
            "items": (78, 60, "e", False),
            "type": (100, 80, "w", False),
            "modified": (130, 110, "w", False),
        }
        self.tree = self._make_treeview(("usage", "size", "items", "type", "modified"), headings, widths)
        self.iid_to_node = {}

        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-Button-1>", self._on_tree_double)
        self.tree.bind("<Button-3>", self._on_tree_right)
        self.tree.bind("<Delete>", lambda e: self._delete_selected())

        self.tree_menu = tk.Menu(self, tearoff=0)
        self.tree_menu.add_command(label="Open in Explorer", command=self._open_in_explorer)
        self.tree_menu.add_command(label="Zip selected", command=self._zip_selected)
        self.tree_menu.add_separator()
        self.tree_menu.add_command(label="Delete selected", command=self._delete_selected)

        if self.search_query:
            self._fill_tree_search()
        else:
            self._insert_tree_children("", self.root_node)

    def _tree_values(self, node: Node, parent: Node):
        pct = calculate_percentage(node.size, parent.size) if parent and parent.size else 0.0
        filled = round(pct / 100 * self.BAR_WIDTH)
        bar = "█" * filled + "░" * (self.BAR_WIDTH - filled)
        usage = f"{bar} {pct:4.1f}%"
        if node.is_dir:
            return (usage, format_size(node.size), f"{node.item_count:,}", "Folder", format_date(node.creation_date))
        return (usage, format_size(node.size), "", get_file_category(node.path)['label'], format_date(node.creation_date))

    def _thumb_size(self) -> int:
        return max(16, self.settings.row_height() - 6)

    def _register_row_thumbnail(self, tree, iid: str, node: Node):
        """Ask for a small preview for an image row; it is applied when ready."""
        if node.is_dir or not self.settings.list_thumbnails or not is_image_file(node.path):
            return
        size = self._thumb_size()
        self._row_by_path.setdefault(node.path, []).append((tree, iid))
        image = self.thumbnails.request(node.path, (size, size))
        if image is not None:
            self._set_row_image(tree, iid, node.path, image)

    def _set_row_image(self, tree, iid: str, path: str, image):
        photo = self._row_photos.get(path)
        if photo is None:
            photo = ImageTk.PhotoImage(image)
            self._row_photos[path] = photo       # Tk needs a live reference
        try:
            tree.item(iid, image=photo)
        except tk.TclError:
            pass

    def _refresh_row_thumbnail(self, path: str):
        rows = self._row_by_path.get(path)
        if not rows:
            return
        size = self._thumb_size()
        image = self.thumbnails.get(path, (size, size))
        if image is None:
            return
        alive = []
        for tree, iid in rows:
            try:
                if tree.winfo_exists() and tree.exists(iid):
                    self._set_row_image(tree, iid, path, image)
                    alive.append((tree, iid))
            except tk.TclError:
                continue
        self._row_by_path[path] = alive

    def _insert_tree_children(self, parent_iid: str, parent_node: Node):
        for child in parent_node.sorted_children(self.sort_key, self.sort_reverse):
            icon = ICONS['folder'] if child.is_dir else get_file_icon(child.path)
            tags = []
            if child.is_dir:
                tags.append("folder")
            if child.error:
                tags.append("error")
            iid = self.tree.insert(parent_iid, "end", text=f"{icon} {child.name}",
                                   values=self._tree_values(child, parent_node), tags=tuple(tags))
            self.iid_to_node[iid] = child
            self._register_row_thumbnail(self.tree, iid, child)
            if child.is_dir and child.children:
                self.tree.insert(iid, "end", text="…", tags=("dummy",))

    def _fill_tree_search(self):
        matches = analysis.find_matches(self.root_node, self.search_query, limit=1000)
        if not matches:
            return
        for node in matches:
            icon = ICONS['folder'] if node.is_dir else get_file_icon(node.path)
            tags = ["folder"] if node.is_dir else []
            iid = self.tree.insert("", "end", text=f"{icon} {node.name}",
                                   values=self._tree_values(node, self.root_node), tags=tuple(tags))
            self.iid_to_node[iid] = node

    def _is_dummy(self, iid: str) -> bool:
        return "dummy" in self.tree.item(iid, "tags")

    def _on_tree_open(self, event):
        iid = self.tree.focus()
        if not iid:
            return
        kids = self.tree.get_children(iid)
        if len(kids) == 1 and self._is_dummy(kids[0]):
            self.tree.delete(kids[0])
            node = self.iid_to_node.get(iid)
            if node:
                self._insert_tree_children(iid, node)

    def _sort_tree(self, key: str):
        if key == self.sort_key:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key = key
            self.sort_reverse = key != "name"
        if self.search_query:
            return
        expanded = set()

        def collect(iid):
            for c in self.tree.get_children(iid):
                node = self.iid_to_node.get(c)
                if node and self.tree.item(c, "open"):
                    expanded.add(node.path)
                collect(c)
        collect("")
        self.tree.delete(*self.tree.get_children())
        self.iid_to_node = {}
        self._insert_tree_children("", self.root_node)

        def reexpand(iid):
            for c in self.tree.get_children(iid):
                node = self.iid_to_node.get(c)
                if node and node.path in expanded:
                    dummies = self.tree.get_children(c)
                    if len(dummies) == 1 and self._is_dummy(dummies[0]):
                        self.tree.delete(dummies[0])
                        self._insert_tree_children(c, node)
                    self.tree.item(c, open=True)
                    reexpand(c)
        reexpand("")

    def _on_tree_select(self, event):
        nodes = self._selected_nodes()
        if not nodes:
            if self.root_node:
                self._set_status(f"{self.root_node.item_count:,} items")
            return
        total = sum(n.size for n in nodes)
        self._set_status(f"{len(nodes)} selected · {format_size(total)}")

    def _on_tree_double(self, event):
        iid = self.tree.identify_row(event.y)
        node = self.iid_to_node.get(iid)
        if not node:
            return
        if node.is_dir:
            self.scan_folder(node.path)
        elif self.settings.preview_enabled and is_image_file(node.path):
            self._open_image(node.path)

    def _open_image(self, path: str):
        ImageViewer(self, path, self.settings)

    def _on_tree_right(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        if self.tree.selection():
            self.tree_menu.tk_popup(event.x_root, event.y_root)

    # ---- Largest files view

    def _render_largest(self):
        if not self.root_node:
            self._empty_hint("Select a folder to analyze")
            return
        headings = {
            "#0": ("File", lambda: None),
            "size": ("Size", lambda: None),
            "type": ("Type", lambda: None),
            "path": ("Location", lambda: None),
        }
        widths = {
            "#0": (320, 200, "w", False),
            "size": (100, 80, "e", False),
            "type": (110, 80, "w", False),
            "path": (560, 240, "w", True),
        }
        self.largest_tree = self._make_treeview(("size", "type", "path"), headings, widths)
        self.largest_map = {}
        self.largest_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.largest_tree.bind("<Delete>", lambda e: self._delete_selected())

        files = analysis.largest_files(self.root_node, 100)
        if self.search_query:
            files = [n for n in files if analysis.match_query(n.name, self.search_query)]
        if not files:
            return
        for node in files:
            iid = self.largest_tree.insert(
                "", "end", text=f"{get_file_icon(node.path)} {node.name}",
                values=(format_size(node.size), get_file_category(node.path)['label'],
                        os.path.dirname(node.path)))
            self.largest_map[iid] = node
            self._register_row_thumbnail(self.largest_tree, iid, node)

        def on_double(event):
            node = self.largest_map.get(self.largest_tree.identify_row(event.y))
            if node and self.settings.preview_enabled and is_image_file(node.path):
                self._open_image(node.path)
            elif node:
                self._reveal(node.path)
        self.largest_tree.bind("<Double-Button-1>", on_double)

    # ---- File types view

    def _render_types(self):
        if not self.root_node:
            self._empty_hint("Select a folder to analyze")
            return
        colors = self._colors()
        outer = tk.Frame(self.body, bg=colors['tree_bg'])
        outer.pack(fill="both", expand=True)

        canvas = tk.Canvas(outer, bg=colors['tree_bg'], highlightthickness=0)
        vsb = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        inner = tk.Frame(canvas, bg=colors['tree_bg'])
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
        window_id = canvas.create_window((0, 0), window=inner, anchor="nw")
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window_id, width=e.width))
        canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        stats = analysis.category_breakdown(self.root_node)
        total = self.root_node.size or 1

        tk.Label(inner, text="File type breakdown", bg=colors['tree_bg'], fg=colors['tree_fg'],
                 font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=24, pady=(20, 12))

        if not stats:
            tk.Label(inner, text="No files found", bg=colors['tree_bg'], fg=colors['muted_fg'],
                     font=("Segoe UI", 12)).pack(anchor="w", padx=24)
            return

        for stat in stats:
            row = tk.Frame(inner, bg=colors['tree_bg'])
            row.pack(fill="x", padx=24, pady=5)

            head = tk.Frame(row, bg=colors['tree_bg'])
            head.pack(fill="x")
            tk.Label(head, text=stat.label, bg=colors['tree_bg'], fg=colors['tree_fg'],
                     font=("Segoe UI", 11, "bold")).pack(side="left")
            tk.Label(head, text=f"{format_size(stat.size)}  ·  {stat.count:,} files  ·  {stat.percent:.1f}%",
                     bg=colors['tree_bg'], fg=colors['muted_fg'], font=("Segoe UI", 10)).pack(side="right")

            track = tk.Frame(row, bg=colors['head_bg'], height=14)
            track.pack(fill="x", pady=(4, 0))
            track.pack_propagate(False)
            fill = tk.Frame(track, bg=stat.color, height=14)
            fill.place(relx=0, rely=0, relwidth=max(stat.size / total, 0.004), relheight=1)

    # ---- Treemap view

    def _render_treemap(self):
        if not self.root_node:
            self._empty_hint("Select a folder to analyze")
            return
        colors = self._colors()
        node = self.treemap_stack[-1] if self.treemap_stack else self.root_node

        wrap = tk.Frame(self.body, bg=colors['canvas_bg'])
        wrap.pack(fill="both", expand=True)

        crumb = tk.Frame(wrap, bg=colors['head_bg'], height=28)
        crumb.pack(fill="x")
        crumb.pack_propagate(False)
        label = "  ›  ".join([self.root_node.name] + [n.name for n in self.treemap_stack]) or node.name
        tk.Label(crumb, text=f"🗺  {label}", bg=colors['head_bg'], fg=colors['head_fg'],
                 font=("Segoe UI", 10)).pack(side="left", padx=12)
        if self.treemap_stack:
            tk.Button(crumb, text="⬅ Back", bd=0, relief="flat", cursor="hand2",
                      bg=colors['head_bg'], fg=colors['head_fg'], font=("Segoe UI", 9),
                      command=self._treemap_back).pack(side="right", padx=8)

        self.treemap_canvas = tk.Canvas(wrap, bg=colors['canvas_bg'], highlightthickness=0)
        self.treemap_canvas.pack(fill="both", expand=True)
        if self.tooltip is None:
            self.tooltip = Tooltip(self)
        self.treemap_node = node
        self._hover_tile = None
        self.treemap_canvas.bind("<Configure>", lambda e: self._schedule_treemap_redraw())
        self.treemap_canvas.bind("<Motion>", self._treemap_hover)
        self.treemap_canvas.bind("<Leave>", self._treemap_leave)
        self.treemap_canvas.bind("<Button-1>", self._treemap_click)
        self.treemap_canvas.bind("<Double-Button-1>", self._treemap_double_click)
        self.treemap_canvas.bind("<Button-3>", lambda e: self._treemap_back())

    def _treemap_leave(self, event):
        self.tooltip.hide()
        if self._hover_tile is not None:
            self._hover_tile = None
            self._schedule_treemap_redraw()

    def _schedule_treemap_redraw(self):
        """Coalesce the burst of <Configure> events a window resize produces
        into a single re-layout."""
        if self._treemap_redraw_after:
            self.after_cancel(self._treemap_redraw_after)
        self._treemap_redraw_after = self.after(80, self._do_treemap_redraw)

    def _do_treemap_redraw(self):
        self._treemap_redraw_after = None
        if getattr(self, "treemap_canvas", None) is None:
            return
        try:
            if not self.treemap_canvas.winfo_exists():
                return
        except tk.TclError:
            return
        self._draw_treemap()

    def _draw_treemap(self, highlight=None):
        canvas = self.treemap_canvas
        w = canvas.winfo_width()
        h = canvas.winfo_height()
        if w < 20 or h < 20:
            return

        node = self.treemap_node
        if not node.children or node.size <= 0:
            canvas.delete("all")
            self._tiles = []
            canvas.create_text(w // 2, h // 2, text="Nothing to display",
                               fill=self._colors()['muted_fg'], font=("Segoe UI", 12))
            return

        self._tiles = analysis.build_treemap(
            node, 2, 2, w - 4, h - 4,
            min_area=110, max_depth=6, header=treemap_render.RenderOptions.header)

        opts = treemap_render.RenderOptions(
            dark_mode=self.settings.dark_mode,
            show_thumbnails=self.settings.treemap_thumbnails,
            highlight=highlight,
        )
        image = treemap_render.render_treemap(
            self._tiles, w, h, opts,
            thumb_provider=self._treemap_thumb if self.settings.treemap_thumbnails else None)

        # one canvas image instead of thousands of items: far less work for Tk
        self._treemap_photo = ImageTk.PhotoImage(image)
        canvas.delete("all")
        canvas.create_image(0, 0, anchor="nw", image=self._treemap_photo)

    def _treemap_thumb(self, path: str, size):
        return self.thumbnails.request(path, (min(size[0], 400), min(size[1], 400)))

    def _on_thumbnail_ready(self, path: str):
        """A worker decoded a thumbnail; fold it into the views that show one."""
        self.after(0, lambda: self._apply_thumbnail(path))

    def _apply_thumbnail(self, path: str):
        if self.active_view == "Treemap":
            self._schedule_treemap_redraw()
            # the pointer may still be resting on this tile: fill the peek in
            # now rather than waiting for the next mouse move
            if self._peek_path == path and self._peek_args is not None:
                text, x, y = self._peek_args
                image = self.thumbnails.get(path, PEEK_SIZE)
                if image is not None:
                    self.tooltip.show(text, x, y, self._colors(), image=image)
        elif self.settings.list_thumbnails:
            self._refresh_row_thumbnail(path)

    def _treemap_hover(self, event):
        tile = treemap_render.hit_test(self._tiles, event.x, event.y)
        if tile is None:
            self.tooltip.hide()
            if self._hover_tile is not None:
                self._hover_tile = None
                self._schedule_treemap_redraw()
            return

        if tile is not self._hover_tile:
            self._hover_tile = tile
            self._draw_treemap(highlight=tile)

        n = tile.node
        kind = "Folder" if n.is_dir else get_file_category(n.path)['label']
        share = calculate_percentage(n.size, self.treemap_node.size)
        lines = [n.name, f"{format_size(n.size)} · {kind} · {share:.1f}%"]
        if n.is_dir:
            lines.append(f"{n.item_count:,} items · click to zoom in")
        else:
            lines.append(os.path.dirname(n.path))

        # peek preview: show the picture itself, not just its name
        peek = None
        self._peek_path = None
        self._peek_args = None
        if self.settings.peek_preview and not n.is_dir and is_image_file(n.path):
            peek = self.thumbnails.request(n.path, PEEK_SIZE)
            lines.append("double-click to open")
            self._peek_path = n.path

        text = "\n".join(lines)
        px, py = self.winfo_pointerx(), self.winfo_pointery()
        self._peek_args = (text, px, py)
        self.tooltip.show(text, px, py, self._colors(), image=peek)

    def _treemap_click(self, event):
        tile = treemap_render.hit_test(self._tiles, event.x, event.y)
        if tile and tile.node.is_dir and tile.node.children:
            self.treemap_stack.append(tile.node)
            self._hover_tile = None
            self._render_active_view()

    def _treemap_double_click(self, event):
        tile = treemap_render.hit_test(self._tiles, event.x, event.y)
        if tile and not tile.node.is_dir and is_image_file(tile.node.path):
            self._open_image(tile.node.path)

    def _treemap_back(self):
        if self.treemap_stack:
            self.treemap_stack.pop()
            self._render_active_view()

    # --------------------------------------------------------------- search

    def _on_search_change(self):
        if self._search_after:
            self.after_cancel(self._search_after)
        self._search_after = self.after(250, self._apply_search)

    def _apply_search(self):
        self.search_query = self.search_var.get().strip()
        if self.active_view in ("Tree", "Largest Files"):
            self._render_active_view()

    def _clear_search(self):
        self.search_var.set("")

    # --------------------------------------------------------------- actions

    def _selection_context(self):
        """Return (tree_widget, iid->Node map) for the view that currently owns
        a selection, or (None, {}) when the active view has none.

        Views are rebuilt on every switch, so the widget references are only
        valid for the view that is on screen right now.
        """
        if self.active_view == "Tree" and self.tree is not None:
            return self.tree, self.iid_to_node
        if self.active_view == "Largest Files" and self.largest_tree is not None:
            return self.largest_tree, self.largest_map
        return None, {}

    def _selected_nodes(self) -> List[Node]:
        tree, mapping = self._selection_context()
        if tree is None:
            return []
        return [mapping[iid] for iid in tree.selection() if iid in mapping]

    def _top_level_selection(self) -> List[tuple]:
        """Selected (iid, node) pairs, with anything nested under another
        selected row removed so we never act on the same bytes twice."""
        tree, mapping = self._selection_context()
        if tree is None:
            return []

        selection = set(tree.selection())
        result = []
        for iid in tree.selection():
            if iid not in mapping:
                continue
            parent = tree.parent(iid)
            nested = False
            while parent:
                if parent in selection:
                    nested = True
                    break
                parent = tree.parent(parent)
            if not nested:
                result.append((iid, mapping[iid]))
        return result

    def _reveal(self, path: str):
        try:
            if sys.platform == "win32":
                if os.path.isdir(path):
                    os.startfile(path)
                else:
                    subprocess.Popen(["explorer", "/select,", path])
            else:
                subprocess.Popen(["xdg-open", path if os.path.isdir(path) else os.path.dirname(path)])
        except OSError as e:
            messagebox.showerror("Error", f"Could not open: {e}")

    def _open_in_explorer(self):
        nodes = self._selected_nodes()
        if nodes:
            self._reveal(nodes[0].path)

    def _export_csv(self):
        if not self.root_node:
            messagebox.showwarning("No data", "Scan a folder first.")
            return
        save_path = filedialog.asksaveasfilename(defaultextension=".csv",
                                                 filetypes=[("CSV files", "*.csv")], title="Export report as")
        if not save_path:
            return
        self._set_status("Exporting CSV…")

        def worker():
            try:
                rows = analysis.export_tree_csv(self.root_node, save_path)
                self.after(0, lambda: (self._set_status(f"Exported {rows:,} rows"),
                                       messagebox.showinfo("Export complete", f"Wrote {rows:,} rows to:\n{save_path}")))
            except Exception as exc:
                msg = str(exc)
                self.after(0, lambda: messagebox.showerror("Export failed", msg))
        threading.Thread(target=worker, daemon=True).start()

    def _zip_selected(self):
        selection = self._top_level_selection()
        if not selection:
            messagebox.showwarning("No selection", self._no_selection_hint())
            return
        save_path = filedialog.asksaveasfilename(defaultextension=".zip",
                                                 filetypes=[("ZIP files", "*.zip")], title="Save ZIP as")
        if not save_path:
            return
        paths = [node.path for _, node in selection]
        self._set_status("Creating ZIP…")

        def worker():
            try:
                with zipfile.ZipFile(save_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for path in paths:
                        if os.path.isfile(path):
                            zf.write(path, os.path.basename(path))
                        elif os.path.isdir(path):
                            base = os.path.dirname(path)
                            for r, _, files in os.walk(path):
                                for file in files:
                                    fp = os.path.join(r, file)
                                    try:
                                        zf.write(fp, os.path.relpath(fp, base))
                                    except OSError:
                                        pass
                self.after(0, lambda: (self._set_status("ZIP created"),
                                       messagebox.showinfo("Success", f"Created: {save_path}")))
            except Exception as exc:
                msg = str(exc)
                self.after(0, lambda: (self._set_status("ZIP failed"),
                                       messagebox.showerror("Error", f"Failed to create ZIP: {msg}")))
        threading.Thread(target=worker, daemon=True).start()

    def _no_selection_hint(self) -> str:
        if self.active_view in ("Tree", "Largest Files"):
            return "Select files or folders first."
        return "Switch to the Tree or Largest Files view to select items."

    def _delete_selected(self):
        selection = self._top_level_selection()
        if not selection:
            messagebox.showwarning("No selection", self._no_selection_hint())
            return
        total = sum(node.size for _, node in selection)
        if not messagebox.askyesno("Confirm delete",
                                   f"Delete {len(selection)} item(s) ({format_size(total)})?\nThis cannot be undone."):
            return
        self._set_status("Deleting…")

        # remember which view started this so the async result never touches a
        # widget the user has since navigated away from
        tree, mapping = self._selection_context()

        def worker():
            deleted, errors = [], []
            for iid, node in selection:
                try:
                    if os.path.isdir(node.path):
                        shutil.rmtree(node.path)
                    else:
                        os.remove(node.path)
                    deleted.append((iid, node))
                except Exception as e:
                    errors.append(f"{node.name}: {e}")
            self.after(0, lambda: self._apply_deletions(deleted, errors, tree, mapping))
        threading.Thread(target=worker, daemon=True).start()

    def _apply_deletions(self, deleted, errors, tree=None, mapping=None):
        rows_alive = False
        if tree is not None:
            try:
                rows_alive = bool(tree.winfo_exists())
            except tk.TclError:
                rows_alive = False

        for iid, node in deleted:
            removed_items = (1 + node.item_count) if node.is_dir else 1
            parent = node.parent
            if parent and node in parent.children:
                parent.children.remove(node)
            walk = parent
            while walk:
                walk.size -= node.size
                walk.item_count -= removed_items
                walk = walk.parent
            if rows_alive:
                try:
                    if tree.exists(iid):
                        tree.delete(iid)
                except tk.TclError:
                    rows_alive = False
            if mapping is not None:
                mapping.pop(iid, None)

        if self.root_node:
            self.status_right.configure(text=f"Total: {format_size(self.root_node.size)}")
            status = f"{self.root_node.item_count:,} items"
            if deleted:
                status = f"Deleted {len(deleted)} item(s) · " + status
            self._set_status(status)
        if errors:
            messagebox.showerror("Errors", "\n".join(errors[:5]))

    # --------------------------------------------------------------- misc

    def _toggle_theme(self):
        self.settings.dark_mode = not self.settings.dark_mode
        ctk.set_appearance_mode("dark" if self.settings.dark_mode else "light")
        self.theme_btn.configure(text=ICONS['sun'] if self.settings.dark_mode else ICONS['moon'])
        self.settings.save()
        self._render_active_view()

    def _show_settings(self):
        SettingsMenu(self, self.settings, self._on_settings_apply)

    def _on_settings_apply(self):
        self.settings.save()
        self._render_active_view()

    def _set_status(self, text: str):
        self.status_left.configure(text=text)


def run_app(folder_path: Optional[str] = None):
    app = FolderLensApp(initial_path=folder_path)
    app.mainloop()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    run_app(path)
