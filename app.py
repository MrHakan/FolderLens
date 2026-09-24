import customtkinter as ctk
from tkinter import filedialog, messagebox, simpledialog, ttk
import tkinter as tk
import tkinter.font as tkfont
from typing import Optional, List, Dict
import copy
from dataclasses import replace
from concurrent.futures import CancelledError, ThreadPoolExecutor
import json
import os
import queue
import sys
import shutil
import subprocess
import tempfile
import threading
import webbrowser

from PIL import Image, ImageOps, ImageTk

from file_utils import (
    get_file_category, format_size, format_date,
    calculate_percentage, get_file_icon, is_image_file, natural_sort_key, ICONS,
    FILE_CATEGORIES, FILE_TYPE_FILTERS, FILE_TYPE_FILTER_LABELS,
)
from scanner import TreeScanner, Node, ScanSnapshot, is_network_path
import analysis
import file_actions
from query import QueryEngine, QueryIndex, QuerySpec, query_from_form
import annotate
import duplicates
import imagenav
import locations
import treemap_render
import trash
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
        self.file_filter = "all"
        self.treemap_thumbnails = True
        self.list_thumbnails = True
        self.peek_preview = True
        self.annotation_mode = "Basic"
        self.use_recycle_bin = True
        self.load()

    def row_height(self) -> int:
        return {"small": 24, "medium": 30, "large": 38}.get(self.row_size, 30)

    def font_size(self) -> int:
        return {"small": 10, "medium": 11, "large": 13}.get(self.row_size, 11)

    def load(self):
        try:
            with open(_settings_file(), 'r', encoding='utf-8') as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return
            if data.get('row_size') in ("small", "medium", "large"):
                self.row_size = data['row_size']
            if isinstance(data.get('preview_enabled'), bool):
                self.preview_enabled = data['preview_enabled']
            if isinstance(data.get('dark_mode'), bool):
                self.dark_mode = data['dark_mode']
            if isinstance(data.get('last_folder'), str):
                self.last_folder = data['last_folder']
            if data.get('view') in ("Explore", "Tree", "Treemap", "Largest Files", "File Types", "Duplicates"):
                self.view = data['view']
            if (isinstance(data.get('file_filter'), str)
                    and data['file_filter'] in FILE_TYPE_FILTER_LABELS):
                self.file_filter = data['file_filter']
            for flag in ('treemap_thumbnails', 'list_thumbnails', 'peek_preview',
                         'use_recycle_bin'):
                if isinstance(data.get(flag), bool):
                    setattr(self, flag, data[flag])
            if data.get('annotation_mode') in ("Basic", "Advanced"):
                self.annotation_mode = data['annotation_mode']
        except (OSError, ValueError):
            pass

    def save(self):
        temporary_path = None
        try:
            path = _settings_file()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            fd, temporary_path = tempfile.mkstemp(prefix=".settings-", suffix=".tmp",
                                                   dir=os.path.dirname(path))
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                json.dump({
                    'schema_version': 1,
                    'row_size': self.row_size,
                    'preview_enabled': self.preview_enabled,
                    'dark_mode': self.dark_mode,
                    'last_folder': self.last_folder,
                    'view': self.view,
                    'file_filter': self.file_filter,
                    'treemap_thumbnails': self.treemap_thumbnails,
                    'list_thumbnails': self.list_thumbnails,
                    'peek_preview': self.peek_preview,
                    'annotation_mode': self.annotation_mode,
                    'use_recycle_bin': self.use_recycle_bin,
                }, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temporary_path, path)
            temporary_path = None
        except OSError:
            pass
        finally:
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
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


class HoverTip:
    """Plain hover help for a widget.

    The toolbar is mostly icons; without this, "⬆" and "•••" are guesses.
    """

    _shared = None

    def __init__(self, widget, text: str, delay: int = 450):
        self.widget = widget
        self.text = text
        self.delay = delay
        self._after = None
        self._window = None
        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None):
        self._cancel()
        self._after = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._after is not None:
            try:
                self.widget.after_cancel(self._after)
            except (tk.TclError, ValueError):
                pass
            self._after = None

    def _show(self):
        if self._window is not None:
            return
        try:
            x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
            y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        except tk.TclError:
            return
        self._window = tk.Toplevel(self.widget)
        self._window.withdraw()
        self._window.overrideredirect(True)
        self._window.attributes("-topmost", True)
        label = tk.Label(self._window, text=self.text, justify="left",
                         bg="#111827", fg="#f9fafb", padx=8, pady=4,
                         font=("Segoe UI", 9), bd=0)
        label.pack()
        self._window.update_idletasks()
        width = self._window.winfo_reqwidth()
        x = max(0, min(x - width // 2, self._window.winfo_screenwidth() - width))
        self._window.geometry(f"+{x}+{y}")
        self._window.deiconify()

    def _hide(self, _event=None):
        self._cancel()
        if self._window is not None:
            try:
                self._window.destroy()
            except tk.TclError:
                pass
            self._window = None


def add_hint(widget, text: str):
    HoverTip(widget, text)
    return widget


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
    MAX_PREVIEW_SIZE = 2048

    def __init__(self, master, image_path: str, settings: AppSettings, **kwargs):
        super().__init__(master, **kwargs)

        self.settings = settings
        self.nav = imagenav.ImageNavigator.deferred(image_path)
        self._nav_ready = False
        self._loading = False
        self._load_generation = 0
        self._load_results = queue.SimpleQueue()
        self._load_poll_after = None
        self._closed = False
        self._saving = False
        self._save_generation = 0
        self._edit_generation = 0
        self._image_file_size = 0
        self._image_original_size = (0, 0)
        self._subfolders = {}
        self.prev_button = None
        self.next_button = None
        self.up_button = None
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
        self._load(image_path)

        self.bind("<Right>", lambda e: self._go_next())
        self.bind("<Left>", lambda e: self._go_prev())
        self.bind("<Control-z>", lambda e: self._undo())
        self.bind("<Control-y>", lambda e: self._redo())
        self.bind("<Escape>", lambda e: self._on_close())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------ ui

    def _build_ui(self):
        nav = ctk.CTkFrame(self, fg_color=("gray93", "gray17"), corner_radius=0)
        nav.pack(fill="x")

        self.prev_button = ctk.CTkButton(nav, text="◀", width=42, height=32, command=self._go_prev,
                                         font=ctk.CTkFont(size=14))
        self.prev_button.pack(side="left", padx=(10, 4), pady=8)
        self.next_button = ctk.CTkButton(nav, text="▶", width=42, height=32, command=self._go_next,
                                         font=ctk.CTkFont(size=14))
        self.next_button.pack(side="left", padx=4, pady=8)

        self.counter = ctk.CTkLabel(nav, text="", font=ctk.CTkFont(size=12), width=64)
        self.counter.pack(side="left", padx=6)

        self.up_button = ctk.CTkButton(nav, text="⬅ Up", width=64, height=32, font=ctk.CTkFont(size=12),
                                       fg_color="transparent", border_width=1, text_color=("gray20", "gray80"),
                                       command=self._go_parent)
        self.up_button.pack(side="left", padx=(12, 4), pady=8)

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
        if self._loading:
            return False
        if self._dirty and not self._confirm_discard():
            return False
        path = os.path.abspath(path)
        folder = os.path.dirname(path)
        needs_index = (not self._nav_ready or
                       os.path.normcase(self.nav.folder) != os.path.normcase(folder))
        self._schedule_image_load(path, folder, needs_index)
        return True

    def _schedule_image_load(self, path: str, folder: str, needs_index: bool):
        self._load_generation += 1
        generation = self._load_generation
        self._loading = True
        self._set_navigation_controls()
        self.status.configure(text=f"Loading {os.path.basename(path)}…")
        cached_images = list(self.nav.images)
        cached_subfolders = dict(self._subfolders)

        def worker():
            try:
                images = imagenav.list_images(folder) if needs_index else cached_images
                subfolders = imagenav.list_subfolders(folder) if needs_index else list(cached_subfolders.values())
                normalized = os.path.normcase(os.path.abspath(path))
                if not any(os.path.normcase(os.path.abspath(item)) == normalized for item in images):
                    images.append(path)
                    images.sort(key=lambda item: natural_sort_key(os.path.basename(item)))
                preview, dimensions, file_size = self._decode_preview(path)
                payload = (generation, path, folder, preview, dimensions, file_size,
                           images, subfolders, None)
            except Exception as exc:
                payload = (generation, path, folder, None, None, None, None, None, str(exc))
            self._load_results.put(("load", payload))

        threading.Thread(target=worker, daemon=True,
                         name=f"folderlens-image-load-{generation}").start()
        self._schedule_load_poll()

    @classmethod
    def _decode_preview(cls, path: str):
        """Decode and downsample off the Tk thread; keep full resolution for Save As."""
        with Image.open(path) as source:
            oriented = ImageOps.exif_transpose(source)
            dimensions = oriented.size
            preview = oriented.convert("RGB")
            preview.thumbnail((cls.MAX_PREVIEW_SIZE, cls.MAX_PREVIEW_SIZE),
                              Image.Resampling.LANCZOS)
        try:
            size = os.path.getsize(path)
        except OSError:
            size = 0
        return preview, dimensions, size

    def _load_folder(self, folder: str):
        if self._loading:
            return False
        if self._dirty and not self._confirm_discard():
            return False
        folder = os.path.abspath(folder)
        self._load_generation += 1
        generation = self._load_generation
        self._loading = True
        self._set_navigation_controls()
        self.status.configure(text=f"Finding images in {folder}…")

        def worker():
            try:
                images = imagenav.list_images(folder)
                subfolders = imagenav.list_subfolders(folder)
                if not images:
                    payload = (generation, None, folder, None, None, None,
                               images, subfolders, "No images in this folder")
                else:
                    path = images[0]
                    preview, dimensions, file_size = self._decode_preview(path)
                    payload = (generation, path, folder, preview, dimensions,
                               file_size, images, subfolders, None)
            except Exception as exc:
                payload = (generation, None, folder, None, None, None,
                           None, None, str(exc))
            self._load_results.put(("load", payload))

        threading.Thread(target=worker, daemon=True,
                         name=f"folderlens-folder-load-{generation}").start()
        self._schedule_load_poll()
        return True

    def _schedule_load_poll(self):
        if self._load_poll_after is None and not self._closed:
            self._load_poll_after = self.after(40, self._poll_load_results)

    def _poll_load_results(self):
        self._load_poll_after = None
        while True:
            try:
                kind, payload = self._load_results.get_nowait()
            except queue.Empty:
                break
            if kind == "load":
                self._apply_loaded_image(*payload)
            elif kind == "save":
                self._apply_saved_image(*payload)
        if (self._loading or self._saving) and not self._closed:
            self._schedule_load_poll()

    def _apply_loaded_image(self, generation, path, folder, preview, dimensions,
                            file_size, images, subfolders, error):
        if self._closed or generation != self._load_generation:
            return
        self._loading = False
        if error:
            self.status.configure(text=f"Cannot load image: {error}")
            self._set_navigation_controls()
            return

        self.nav.set_images(folder, images, path)
        self._nav_ready = True
        self.image = preview
        self._image_original_size = dimensions
        self._image_file_size = file_size
        self.doc = annotate.AnnotationDocument()
        self._dirty = False
        self._edit_generation += 1
        self.title(os.path.basename(path))
        self.name_label.configure(text=os.path.basename(path))
        self.counter.configure(text=self.nav.position)
        self._subfolders = {os.path.basename(item): item for item in subfolders}
        self._set_navigation_controls()
        width, height = dimensions
        self.status.configure(text=f"{width:,} × {height:,}  ·  {format_size(file_size)}")
        self._redraw()

    def _set_navigation_controls(self):
        if self.prev_button is None:
            return
        ready = not self._loading
        has_many = len(self.nav.images) > 1
        self.prev_button.configure(state="normal" if ready and has_many else "disabled")
        self.next_button.configure(state="normal" if ready and has_many else "disabled")
        folder = self.nav.folder
        drive, tail = os.path.splitdrive(folder)
        is_root = bool(drive and tail in ("\\", "/")) or folder in (os.path.abspath(os.sep), "/")
        self.up_button.configure(state="normal" if ready and not is_root else "disabled")
        names = list(self._subfolders) or ["(no subfolders)"]
        self.folder_menu.configure(values=names,
                                    state="normal" if ready and self._subfolders else "disabled")
        self.folder_menu.set(names[0])

    def _confirm_discard(self) -> bool:
        return messagebox.askyesno("Discard annotations?",
                                   "This image has unsaved annotations.\nDiscard them?",
                                   parent=self)

    def _go_next(self):
        if self._loading:
            return
        nxt = self.nav.images[(self.nav.index + 1) % self.nav.count] if self.nav.count else None
        if nxt:
            self._load(nxt)

    def _go_prev(self):
        if self._loading:
            return
        prev = self.nav.images[(self.nav.index - 1) % self.nav.count] if self.nav.count else None
        if prev:
            self._load(prev)

    def _go_parent(self):
        if self._loading:
            return
        folder = self.nav.folder
        drive, tail = os.path.splitdrive(folder)
        if drive and tail in ("\\", "/"):
            return
        parent = os.path.dirname(folder.rstrip("\\/"))
        if parent and os.path.normcase(parent) != os.path.normcase(folder):
            self._load_folder(parent)

    def _open_subfolder(self, name: str):
        if self._loading:
            return
        folder = getattr(self, "_subfolders", {}).get(name)
        if not folder:
            return
        self._load_folder(folder)

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
        return not self._loading and not self._closed and self.mode.get() != "Off" and self.image is not None

    def _mark_document_changed(self):
        self._dirty = True
        self._edit_generation += 1

    def _on_press(self, event):
        if not self._annotating():
            return
        point = self._to_image(event.x, event.y)
        if self.tool.get() == "eraser":
            if self.doc.erase_at(*point):
                self._mark_document_changed()
                self._redraw()
            return
        if self.tool.get() == "text":
            text = simpledialog.askstring("Text", "Annotation text:", parent=self)
            if text:
                self.doc.add(annotate.Shape(kind="text", points=[point], color=self.color.get(),
                                            width=self.brush.get(), text=text))
                self._mark_document_changed()
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
        self._mark_document_changed()
        self._redraw()

    def _current_shape(self):
        tool = self.tool.get()
        return annotate.Shape(kind=tool, points=list(self._active_points),
                              color=self.color.get(), width=self.brush.get(),
                              opacity=annotate.default_opacity_for(tool))

    # ------------------------------------------------------------- actions

    def _undo(self):
        if self._loading:
            return
        if self.doc.undo():
            self._dirty = self.doc.can_undo
            self._edit_generation += 1
            self._redraw()

    def _redo(self):
        if self._loading:
            return
        if self.doc.redo():
            self._mark_document_changed()
            self._redraw()

    def _clear(self):
        if self._loading:
            return
        self.doc.clear()
        self._dirty = False
        self._edit_generation += 1
        self._redraw()

    def _save_as(self):
        if self.image is None or self._loading or self._saving:
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
        current = os.path.normcase(os.path.abspath(self.nav.current or ""))
        destination = os.path.normcase(os.path.abspath(target))
        if current and current == destination:
            messagebox.showerror("Choose another file", "Save the annotated copy under a different name.", parent=self)
            return
        overwrite = os.path.exists(target)
        if overwrite and not messagebox.askyesno(
                "Replace file?", f"Replace the existing file?\n{target}", parent=self):
            return

        self._save_generation += 1
        generation = self._save_generation
        edit_generation = self._edit_generation
        image_generation = self._load_generation
        source_path = self.nav.current
        document = copy.deepcopy(self.doc)
        self._saving = True
        self.status.configure(text="Rendering full-resolution annotated copy…")
        self._schedule_load_poll()

        def worker():
            temporary = None
            try:
                folder = os.path.dirname(os.path.abspath(target)) or os.curdir
                descriptor, temporary = tempfile.mkstemp(prefix=".folderlens-annotated-", suffix=".tmp",
                                                         dir=folder)
                os.close(descriptor)
                with Image.open(source_path) as original:
                    full_image = ImageOps.exif_transpose(original).convert("RGB")
                rendered = annotate.render_to_image(document, full_image)
                extension = os.path.splitext(target)[1].lower()
                image_format = "JPEG" if extension in (".jpg", ".jpeg") else "PNG"
                rendered.save(temporary, format=image_format)
                os.replace(temporary, target)
                temporary = None
                error = None
            except Exception as exc:
                error = str(exc)
            finally:
                if temporary:
                    try:
                        os.remove(temporary)
                    except OSError:
                        pass
            self._load_results.put(("save", (generation, target, error,
                                               edit_generation, image_generation)))

        threading.Thread(target=worker, daemon=True,
                         name=f"folderlens-image-save-{generation}").start()

    def _apply_saved_image(self, generation, target, error, edit_generation,
                           image_generation):
        if self._closed or generation != self._save_generation:
            return
        self._saving = False
        if error:
            self.status.configure(text=f"Save failed: {error}")
            messagebox.showerror("Save failed", error, parent=self)
            return
        if (image_generation == self._load_generation
                and edit_generation == self._edit_generation):
            self._dirty = False
        suffix = " · newer edits remain unsaved" if self._dirty else ""
        self.status.configure(text=f"Saved {os.path.basename(target)}{suffix}")

    def _on_close(self):
        if self._dirty and not messagebox.askyesno(
                "Discard annotations?", "You have unsaved annotations.\nClose anyway?", parent=self):
            return
        self._closed = True
        self._load_generation += 1
        self._save_generation += 1
        if self._load_poll_after is not None:
            try:
                self.after_cancel(self._load_poll_after)
            except tk.TclError:
                pass
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

        ctk.CTkLabel(main, text="Deleting", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(12, 0))
        self.recycle_var = ctk.BooleanVar(value=self.settings.use_recycle_bin)
        ctk.CTkSwitch(main, text="Send deleted files to the Recycle Bin",
                      variable=self.recycle_var).pack(anchor="w", pady=6)

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
        self.settings.use_recycle_bin = self.recycle_var.get()
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
            manual = not info.download_url
            self.status_label.configure(text=f"✅ New version available: {info.version}" +
                                        (" · manual installation" if manual else ""))
            self.status_label.pack(pady=(16, 8))
            self.notes_text.delete("1.0", "end")
            instructions = ("Download the full package from the release page. Close FolderLens "
                            "before replacing its installation.\n\n") if manual else ""
            self.notes_text.insert("1.0", instructions + info.release_notes[:500])
            self.notes_text.pack(fill="both", expand=True, padx=16, pady=(0, 16))
            if manual:
                self.action_btn.configure(text="Open release page",
                                          command=lambda: webbrowser.open(info.release_url),
                                          state="normal")
            else:
                self.action_btn.configure(text="Download & Install", command=self._download,
                                          state="normal")
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
        success, error = self.updater.apply_update(
            self.downloaded_file, self.update_info.sha256 if self.update_info else None)
        if success:
            self.status_label.configure(text="Installing update... The app will restart.")
            self.action_btn.configure(state="disabled")
            self.after(1500, lambda: self.master.destroy())
        else:
            messagebox.showerror("Update Failed", error or "Could not apply update.")


class FolderLensApp(ctk.CTk):
    """Fast, multi-view folder size explorer."""

    BAR_WIDTH = 10
    VIEWS = ["Explore", "Tree", "Treemap", "Largest Files", "File Types", "Duplicates"]

    def __init__(self, initial_path: Optional[str] = None):
        super().__init__()
        self.title(f"FolderLens {VERSION}")
        self.geometry("1280x820")
        self.minsize(940, 620)
        self._set_window_icon()

        self.settings = AppSettings()
        ctk.set_appearance_mode("dark" if self.settings.dark_mode else "light")

        self.scanner = TreeScanner()
        self._scan_generation = 0
        self._scan_completed = True
        self._scan_observed_count = 0
        self._latest_scan_snapshot: Optional[ScanSnapshot] = None
        self._scan_preview_tree = None
        self._scan_preview_status = None
        self._scan_preview_signature = None
        self.root_node: Optional[Node] = None
        self.scan_errors: List[str] = []
        self.scan_time = 0.0
        self.active_view = self.settings.view
        self.search_query = ""
        self._search_after = None
        self.file_filter = self.settings.file_filter
        self._advanced_spec: Optional[QuerySpec] = None
        self._filter_index: Optional[QueryIndex] = None
        self._filter_index_key: Optional[object] = None
        self._query_engine: Optional[QueryEngine] = None
        self._filter_generation = 0
        self._filter_building = False
        self._is_network_root = False
        self._places = None
        self._places_loading = False
        self._places_grid = None
        self._io_results = queue.SimpleQueue()
        self._action_generation = 0
        self._action_running = False
        self._action_cancel_event = None
        self._pending_scan_path = None

        # tree-view state
        self.tree: Optional[ttk.Treeview] = None
        self.iid_to_node: Dict[str, Node] = {}
        self._node_id_to_tree_iid: Dict[int, str] = {}
        self._tree_generation = 0
        self._tree_search_loading = False
        self._tree_search_matches: List[Node] = []
        self._tree_search_offset = 0
        self._tree_search_has_more = False
        self._tree_compact = False
        self._tree_sort_loading_paths = set()
        self._tree_sort_rows = {}
        self._pending_tree_expansions = set()
        self._pending_tree_expansion_pages = {}
        self._pending_explore_select_node: Optional[Node] = None
        self._cross_selection_path: Optional[str] = None
        self._cross_selection_node: Optional[Node] = None
        self._selection_sync_in_progress = False
        self._explore_tree_host = None
        self._explore_map_host = None
        self.sort_key = "size"
        self.sort_reverse = True

        # largest-files view state
        self.largest_tree: Optional[ttk.Treeview] = None
        self.largest_map: Dict[str, Node] = {}
        self._largest_generation = 0
        self._largest_loading = False
        self._types_generation = 0
        self._types_loading = False
        self._types_host = None
        self._types_status = None

        # duplicates view state
        self.dup_tree: Optional[ttk.Treeview] = None
        self.dup_map: Dict[str, Node] = {}
        self.dup_groups: List[duplicates.DuplicateGroup] = []
        self._dup_running = False
        self._dup_cancel = False
        self._duplicate_generation = 0

        # treemap state
        self.treemap_stack: List[Node] = []
        self.treemap_forward_stack: List[Node] = []
        self._tiles: List[analysis.Tile] = []
        self._hover_tile = None
        self._treemap_focus_tile = None
        self._treemap_focus_info = None
        self._treemap_workspace = None
        self._treemap_detail_panel = None
        self._treemap_detail_name = None
        self._treemap_detail_summary = None
        self._treemap_detail_location = None
        self._treemap_detail_action = None
        self._treemap_details_toggle = None
        self._treemap_details_expanded = None
        self._highlight_id = None
        self._treemap_photo = None
        self._treemap_image = None
        self._treemap_generation = 0
        self._treemap_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="treemap")
        self._treemap_future = None
        self._treemap_results = queue.SimpleQueue()
        self._treemap_poll_after = None
        self._treemap_rendering = False
        self._peek_path: Optional[str] = None
        self._peek_args = None
        self._peek_mtime = None
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
        self.after(100, self._poll_io_results)

        self.bind("<F5>", lambda e: self._refresh())
        self.bind("<F1>", lambda e: self._show_shortcuts())
        self.bind("<Control-o>", lambda e: self._browse_folder())
        self.bind("<BackSpace>", self._on_backspace)
        self.bind("<Control-f>", lambda e: self.search_entry.focus_set())
        self.bind("<Escape>", lambda e: self._clear_search())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        if initial_path:
            self.after(120, lambda: self.scan_folder(os.path.abspath(initial_path)))
        elif self.settings.last_folder:
            last_folder = self.settings.last_folder

            def check_last_folder():
                exists = os.path.isdir(last_folder)
                self._io_results.put(("restore", (last_folder, exists)))

            threading.Thread(target=check_last_folder, daemon=True).start()
            self._set_status("Checking last folder…")
        else:
            self._set_status("Select a folder to analyze")

    def _restore_last_folder(self, path: str, exists: bool):
        # A selection made while a slow mapped drive was checked wins.
        if self._scan_generation != 0 or self.root_node is not None:
            return
        if exists:
            self.scan_folder(os.path.abspath(path))
        else:
            self._set_status("Last folder unavailable; choose a place to scan")

    def _poll_io_results(self):
        """Deliver filesystem results on Tk's thread, even before mainloop starts."""
        while True:
            try:
                kind, payload = self._io_results.get_nowait()
            except queue.Empty:
                break
            if kind == "restore":
                self._restore_last_folder(*payload)
            elif kind == "disk":
                self._disk_usage_ready(*payload)
            elif kind == "places":
                self._places_ready(payload)
            elif kind == "largest":
                self._largest_results_ready(*payload)
            elif kind == "types":
                self._types_results_ready(*payload)
            elif kind == "tree-search":
                self._tree_search_results_ready(*payload)
            elif kind == "tree-sort":
                self._tree_child_sort_ready(*payload)
        self.after(100, self._poll_io_results)

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

    # Tk keeps every PhotoImage alive for as long as we reference it, so the
    # row-preview map is capped rather than left to grow while browsing.
    MAX_ROW_PHOTOS = 400

    # duplicates below this are rarely worth the read time
    DUPLICATE_MIN_SIZE = 4096

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
        up = ctk.CTkButton(r1, text="⬅", width=38, height=34, font=ctk.CTkFont(size=14),
                           fg_color="transparent", border_width=1, text_color=("gray20", "gray80"),
                           command=self._go_up)
        up.pack(side="left", padx=3, pady=9)
        add_hint(up, "Go to the parent folder")

        refresh = ctk.CTkButton(r1, text=ICONS['refresh'], width=38, height=34,
                                font=ctk.CTkFont(size=14), fg_color="transparent", border_width=1,
                                text_color=("gray20", "gray80"), command=self._refresh)
        refresh.pack(side="left", padx=3, pady=9)
        add_hint(refresh, "Rescan this folder  (F5)")
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

        settings_btn = ctk.CTkButton(self.actions, text="•••", width=40, height=34,
                                     font=ctk.CTkFont(size=14), fg_color="transparent",
                                     text_color=("gray30", "gray70"),
                                     hover_color=("gray85", "gray25"),
                                     command=self._show_settings)
        settings_btn.pack(side="right", padx=(4, 4))
        add_hint(settings_btn, "Settings")

        help_btn = ctk.CTkButton(self.actions, text="?", width=34, height=34,
                                 font=ctk.CTkFont(size=14, weight="bold"),
                                 fg_color="transparent", text_color=("gray30", "gray70"),
                                 hover_color=("gray85", "gray25"),
                                 command=self._show_shortcuts)
        help_btn.pack(side="right", padx=4)
        add_hint(help_btn, "Keyboard shortcuts  (F1)")

        update_btn = ctk.CTkButton(self.actions, text="⬆", width=40, height=34,
                                   font=ctk.CTkFont(size=14), fg_color="transparent",
                                   text_color=("gray30", "gray70"),
                                   hover_color=("gray85", "gray25"),
                                   command=lambda: UpdateDialog(self))
        update_btn.pack(side="right", padx=4)
        add_hint(update_btn, "Check for updates")
        self.theme_btn = ctk.CTkButton(self.actions, text=ICONS['sun'] if self.settings.dark_mode else ICONS['moon'],
                                       width=40, height=34, font=ctk.CTkFont(size=14), fg_color="transparent",
                                       text_color=("gray30", "gray70"), hover_color=("gray85", "gray25"),
                                       command=self._toggle_theme)
        self.theme_btn.pack(side="right", padx=4)
        add_hint(self.theme_btn, "Switch between light and dark")
        ctk.CTkButton(self.actions, text="⬇ Export", width=84, height=34,
                      font=ctk.CTkFont(size=12), fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray80"),
                      command=self._show_export_menu).pack(side="right", padx=4)

        self.search_var = ctk.StringVar()
        self.search_entry = ctk.CTkEntry(self.toolbar, textvariable=self.search_var,
                                         width=210, height=34,
                                         placeholder_text="Search files & folders…")
        add_hint(self.search_entry, "Search everything in this scan  (Ctrl+F)")
        self.search_var.trace_add("write", lambda *a: self._on_search_change())

        filter_labels = [label for _, label in FILE_TYPE_FILTERS]
        self.filter_var = ctk.StringVar(value=FILE_TYPE_FILTER_LABELS.get(self.file_filter, filter_labels[0]))
        self.filter_menu = ctk.CTkOptionMenu(
            self.toolbar,
            values=filter_labels,
            width=150,
            height=34,
            variable=self.filter_var,
            command=self._on_filter_change,
            fg_color=("gray85", "gray20"),
            button_color=("gray75", "gray28"),
            button_hover_color=("gray65", "gray35"),
            text_color=("gray20", "gray90"),
            font=ctk.CTkFont(size=11),
        )
        add_hint(self.filter_menu, "Limit every view to one file category; folders with matches stay visible")
        self.advanced_btn = ctk.CTkButton(
            self.toolbar, text="More filters", width=94, height=34,
            fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"), command=self._show_advanced_filter)
        add_hint(self.advanced_btn, "Combine categories, extensions, dates and file sizes")

        self._toolbar_narrow = None
        self.bind("<Configure>", self._on_window_configure, add="+")
        self.after(80, lambda: self._reflow_toolbar(self.winfo_width()))

    def _on_backspace(self, event):
        """Up one folder, unless the user is typing in a field."""
        if isinstance(event.widget, (tk.Entry, ctk.CTkEntry)):
            return
        try:
            if str(event.widget).startswith(str(self.search_entry)):
                return
        except tk.TclError:
            pass
        self._go_up()

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
        self.filter_menu.pack_forget()
        self.advanced_btn.pack_forget()
        self.actions.pack_forget()

        if narrow:
            self.toolbar_row2.pack(fill="x")
            self.search_entry.pack(in_=self.toolbar_row2, side="left", padx=(12, 6), pady=7)
            self.filter_menu.pack(in_=self.toolbar_row2, side="left", padx=6, pady=7)
            self.advanced_btn.pack(in_=self.toolbar_row2, side="left", padx=6, pady=7)
            self.actions.pack(in_=self.toolbar_row2, side="right", padx=(4, 8), pady=4)
        else:
            self.toolbar_row2.pack_forget()
            self.actions.pack(in_=self.toolbar_row1, side="right", padx=(4, 8), pady=6)
            self.filter_menu.pack(in_=self.toolbar_row1, side="right", padx=6, pady=9)
            self.advanced_btn.pack(in_=self.toolbar_row1, side="right", padx=6, pady=9)
            self.search_entry.pack(in_=self.toolbar_row1, side="right", padx=6, pady=9)

    def _build_body(self):
        self.pathbar = ctk.CTkFrame(self, fg_color=("gray92", "gray16"), corner_radius=0, height=32)
        self.pathbar.pack(fill="x")
        self.pathbar.pack_propagate(False)
        self.crumb_bar = tk.Frame(self.pathbar, bg=self._pathbar_bg())
        self.crumb_bar.pack(side="left", fill="both", expand=True, padx=10)
        self._set_breadcrumbs("")

        self.body = tk.Frame(self, highlightthickness=0, bd=0)
        self.body.pack(fill="both", expand=True)

        self.progress = ctk.CTkProgressBar(self, height=3, corner_radius=0)
        self.progress.set(0)

        self._render_active_view()

    def _pathbar_bg(self) -> str:
        return "#28282b" if self.settings.dark_mode else "#e9ebef"

    def _set_breadcrumbs(self, path: str):
        """Render the current path as clickable crumbs.

        A flat label told you where you were but made you walk up one level at
        a time; every crumb here jumps straight to that folder.
        """
        bar = getattr(self, "crumb_bar", None)
        if bar is None:
            return
        bg = self._pathbar_bg()
        bar.configure(bg=bg)
        for child in bar.winfo_children():
            child.destroy()

        if not path:
            tk.Label(bar, text="No folder scanned yet", bg=bg,
                     fg=self._colors()['muted_fg'], font=("Segoe UI", 10)).pack(side="left")
            return

        crumbs = locations.shorten_middle(locations.breadcrumbs(path), keep=4)
        last = len(crumbs) - 1
        for index, crumb in enumerate(crumbs):
            if crumb is None:
                tk.Label(bar, text="…", bg=bg, fg=self._colors()['muted_fg'],
                         font=("Segoe UI", 10)).pack(side="left", padx=3)
                continue

            label, target = crumb
            is_last = index == last
            item = tk.Label(bar, text=label, bg=bg,
                            fg=self._colors()['tree_fg'] if is_last else self._colors()['muted_fg'],
                            font=("Segoe UI", 10, "bold" if is_last else "normal"),
                            cursor="arrow" if is_last else "hand2")
            item.pack(side="left")
            if not is_last:
                item.bind("<Button-1>", lambda e, p=target: self.scan_folder(p))
                item.bind("<Enter>", lambda e, w=item: w.configure(fg=ACCENT))
                item.bind("<Leave>", lambda e, w=item: w.configure(fg=self._colors()['muted_fg']))
                tk.Label(bar, text="›", bg=bg, fg=self._colors()['muted_fg'],
                         font=("Segoe UI", 10)).pack(side="left", padx=5)

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

    def _filter_label(self, key: Optional[str] = None) -> str:
        key = self._projection_key() if key is None else key
        if isinstance(key, QuerySpec):
            has_search = bool(key.name_terms)
            has_custom_filter = bool(
                key.categories or key.extensions or key.name or key.min_size is not None
                or key.max_size is not None or key.modified_after_ns is not None
                or key.modified_before_ns is not None or not key.include_hidden
                or key.root_scope is not None or key.metric != "logical")
            if has_search and has_custom_filter:
                return "Custom filter + search"
            if has_search:
                return f"Search {', '.join(key.name_terms)!r}"
            return "Custom filter"
        return FILE_TYPE_FILTER_LABELS.get(key or self.file_filter, "All file types")

    def _has_base_filter(self) -> bool:
        return self._advanced_spec is not None or self.file_filter != "all"

    def _has_active_filter(self) -> bool:
        return self._has_base_filter() or bool(self.search_query)

    def _projection_key(self):
        if self._advanced_spec is not None:
            spec = self._advanced_spec
        elif self.file_filter != "all":
            spec = QuerySpec.category(self.file_filter)
        elif self.search_query:
            spec = QuerySpec()
        else:
            return "all"

        if self.search_query:
            spec = replace(spec, name_terms=(*spec.name_terms, self.search_query))
            return spec
        return spec if self._advanced_spec is not None else self.file_filter

    def _invalidate_filter_index(self):
        """Drop a projection whose underlying scanned tree has changed."""
        self._filter_generation += 1
        self._filter_index = None
        self._filter_index_key = None
        self._filter_building = False

    def _filter_ready_for_view(self) -> bool:
        if not self._has_active_filter():
            return True
        if (self._filter_index is not None
                and self._filter_index_key == self._projection_key()
                and self._filter_index.root is self.root_node):
            return True
        self._empty_hint(f"Applying {self._filter_label()} filter…")
        return False

    def _start_filter_build(self):
        root = self.root_node
        key = self._projection_key()
        if root is None or not self._has_active_filter():
            self._filter_building = False
            return

        if self._query_engine is None or self._query_engine.root is not root:
            self._query_engine = QueryEngine(root)
        engine = self._query_engine

        generation = self._filter_generation
        self._filter_building = True
        self._set_status(f"Preparing {self._filter_label(key)} view…")
        self._render_active_view()

        def worker():
            try:
                index = engine.project(
                    key if isinstance(key, QuerySpec) else QuerySpec.category(key),
                    should_cancel=lambda: (
                        generation != self._filter_generation
                        or root is not self.root_node
                        or key != self._projection_key()
                    ))
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda: self._filter_build_failed(root, key, generation, message))
                return
            if index is None:
                return
            self.after(0, lambda: self._filter_build_done(root, key, generation, index))

        threading.Thread(target=worker, daemon=True).start()

    def _filter_build_failed(self, root, key, generation: int, message: str):
        if (root is not self.root_node or key != self._projection_key()
                or generation != self._filter_generation):
            return
        self._filter_building = False
        self._set_status(f"Could not apply {self._filter_label(key)} filter: {message}")
        self._render_active_view()

    def _filter_build_done(self, root, key, generation: int,
                           index: QueryIndex):
        if (root is not self.root_node or key != self._projection_key()
                or generation != self._filter_generation):
            return
        self._filter_index = index
        self._filter_index_key = key
        self._filter_building = False
        if self.treemap_stack and index.count(self.treemap_stack[-1]) == 0:
            self.treemap_stack = []
            self.treemap_forward_stack = []
        self._set_view_total()
        self._render_active_view()

    def _set_view_total(self):
        if not self.root_node:
            return
        qualifier = "Known (partial scan)" if self.scan_errors else "Total"
        if not self._has_active_filter() or self._filter_index is None:
            self.status_right.configure(text=f"{qualifier}: {format_size(self.root_node.size)}")
            return
        visible_size = self._filter_index.size(self.root_node)
        visible_count = self._filter_index.count(self.root_node)
        self.status_right.configure(
            text=f"{self._filter_label()} · {visible_count:,} files · "
                 f"{format_size(visible_size)}{(' · partial scan' if self.scan_errors else '')}")

    def _node_size(self, node: Node) -> int:
        if self._has_active_filter() and self._filter_index is not None:
            return self._filter_index.size(node)
        return node.size

    def _node_count(self, node: Node) -> int:
        if self._has_active_filter() and self._filter_index is not None:
            return self._filter_index.count(node)
        return node.item_count if node.is_dir else 1

    def _count_label(self) -> str:
        """Use precise wording for full-tree item counts vs filtered files."""
        return "files" if self._has_active_filter() else "items"

    def _visible_children(self, node: Node) -> List[Node]:
        if self._has_active_filter() and self._filter_index is not None:
            return self._filter_index.children(node)
        return node.children

    def _sorted_children(self, node: Node) -> List[Node]:
        if self._has_active_filter() and self._filter_index is not None:
            return self._filter_index.sorted_children(node, self.sort_key, self.sort_reverse)
        return node.sorted_children(self.sort_key, self.sort_reverse)

    @staticmethod
    def _node_mtime(node: Node):
        value = getattr(node, "modified_date", 0)
        return value if value else None

    def scan_folder(self, path: str):
        if self._action_running:
            self._pending_scan_path = os.path.abspath(path)
            self._cancel_file_action()
            self._set_status("Stopping the current file action before scanning…")
            return
        self._scan_generation += 1
        generation = self._scan_generation
        self._scan_completed = False
        self._scan_observed_count = 0
        self._latest_scan_snapshot = None
        self.settings.last_folder = path
        self.settings.save()
        self._is_network_root = is_network_path(path)
        self._invalidate_filter_index()
        self._invalidate_treemap_render()
        self._query_engine = None
        self._invalidate_duplicate_scan()
        self.treemap_stack = []
        self.treemap_forward_stack = []
        self._cross_selection_path = None
        self._cross_selection_node = None
        self._set_breadcrumbs(path)
        self._set_status(f"Scanning {path} …")
        self._show_progress(True)
        self.cancel_btn.configure(text="✕ Stop", command=self._cancel_scan)
        self.cancel_btn.pack(side="left", padx=3, pady=9)
        self.root_node = None
        self.status_right.configure(text="")
        self.status_disk.configure(text="")
        self._clear_body()
        self._empty_hint("Scanning folder…")

        self.scanner.scan(
            path,
            on_complete=lambda root, errors, t: self.after(
                0, lambda: self._scan_done_if_current(generation, root, errors, t)),
            on_error=lambda msg: self.after(0, lambda: self._scan_failed_if_current(generation, msg)),
            on_snapshot=lambda snapshot: self.after(
                0, lambda: self._scan_snapshot(generation, snapshot)),
        )

    def _scan_snapshot(self, generation: int, snapshot: ScanSnapshot):
        if (generation != self._scan_generation or self._scan_completed
                or snapshot.state != "scanning"
                or snapshot.observed_items < self._scan_observed_count
                or (self._latest_scan_snapshot is not None
                    and snapshot.elapsed_seconds < self._latest_scan_snapshot.elapsed_seconds)):
            return
        self._scan_observed_count = snapshot.observed_items
        self._latest_scan_snapshot = snapshot
        self._set_status(f"Scanning… {snapshot.observed_items:,} items seen · "
                         f"at least {format_size(snapshot.known_bytes)}")
        suffix = f" · {snapshot.errors} inaccessible" if snapshot.errors else ""
        self.status_right.configure(
            text=f"Observed: {snapshot.known_files:,} files · "
                 f"at least {format_size(snapshot.known_bytes)}{suffix}")

        if self.root_node is None and snapshot.observed_files:
            self._render_scan_preview(snapshot)

    def _render_scan_preview(self, snapshot: ScanSnapshot):
        """Show a bounded provisional top-files list while the scan is running."""
        samples = snapshot.observed_files[:25]
        if not samples:
            return

        tree = self._scan_preview_tree
        try:
            ready = tree is not None and bool(tree.winfo_exists())
        except tk.TclError:
            ready = False
        if not ready:
            self._clear_body()
            colors = self._colors()
            wrap = tk.Frame(self.body, bg=colors['tree_bg'])
            wrap.pack(fill="both", expand=True, padx=18, pady=18)
            tk.Label(wrap, text="Largest files observed so far",
                     bg=colors['tree_bg'], fg=colors['tree_fg'],
                     font=("Segoe UI", 16, "bold")).pack(anchor="w")
            tk.Label(wrap,
                     text="Provisional ranking · unscanned folders may contain larger files; folder totals settle when the scan completes.",
                     bg=colors['tree_bg'], fg=colors['muted_fg'],
                     font=("Segoe UI", 10), wraplength=900,
                     justify="left").pack(anchor="w", pady=(5, 10))
            self._scan_preview_status = tk.Label(
                wrap, text="", bg=colors['tree_bg'], fg=colors['muted_fg'],
                font=("Segoe UI", 10), anchor="w")
            self._scan_preview_status.pack(fill="x", pady=(0, 8))
            table_wrap = tk.Frame(wrap, bg=colors['tree_bg'])
            table_wrap.pack(fill="both", expand=True)
            tree = ttk.Treeview(
                table_wrap, columns=("size", "location"), show="tree headings",
                style="FolderLens.Treeview", selectmode="browse")
            tree.heading("#0", text="File")
            tree.heading("size", text="Logical size")
            tree.heading("location", text="Folder")
            tree.column("#0", width=240, minwidth=150, anchor="w")
            tree.column("size", width=125, minwidth=100, anchor="e", stretch=False)
            tree.column("location", width=720, minwidth=260, anchor="w")
            scrollbar = ttk.Scrollbar(table_wrap, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scrollbar.set)
            tree.pack(side="left", fill="both", expand=True)
            scrollbar.pack(side="right", fill="y")
            self._scan_preview_tree = tree
            self._scan_preview_signature = None

        self._scan_preview_status.configure(
            text=(f"{snapshot.known_files:,} files observed · "
                  f"at least {format_size(snapshot.known_bytes)} · "
                  f"{snapshot.directories_completed:,} folders completed · "
                  f"{snapshot.errors:,} inaccessible"))
        signature = tuple((sample.path, sample.size) for sample in samples)
        if signature != self._scan_preview_signature:
            children = self._scan_preview_tree.get_children("")
            if children:
                self._scan_preview_tree.delete(*children)
            for sample in samples:
                self._scan_preview_tree.insert(
                    "", "end", text=sample.name,
                    values=(format_size(sample.size), os.path.dirname(sample.path)))
            self._scan_preview_signature = signature

    def _scan_done_if_current(self, generation: int, root: Node,
                              errors: List[str], scan_time: float):
        if generation == self._scan_generation:
            self._scan_done(root, errors, scan_time)

    def _scan_failed_if_current(self, generation: int, message: str):
        if generation == self._scan_generation:
            self._scan_failed(message)

    def _scan_done(self, root: Node, errors: List[str], scan_time: float):
        self._scan_completed = True
        self.root_node = root
        self._is_network_root = is_network_path(root.path)
        self.scan_errors = errors
        self.scan_time = scan_time
        self._show_progress(False)
        self.cancel_btn.pack_forget()

        status = f"{root.item_count:,} items · {scan_time:.1f}s"
        if errors:
            status += f"  ·  ⚠ {len(errors)} inaccessible"
        self._set_status(status)
        self._set_view_total()
        self._update_disk(root.path)
        if not self._has_active_filter():
            self._render_active_view()
        else:
            self._start_filter_build()

    def _scan_failed(self, message: str):
        self._scan_completed = True
        self._show_progress(False)
        self.cancel_btn.pack_forget()
        self._set_status("Scan failed")
        self._clear_body()
        self._empty_hint("Scan failed")
        messagebox.showerror("Error", message)

    def _cancel_scan(self):
        self._scan_generation += 1
        self._scan_completed = True
        self.scanner.cancel()
        self._set_status("Scan cancelled")
        self._show_progress(False)
        self.cancel_btn.pack_forget()
        self._clear_body()
        self._empty_hint("Scan cancelled")

    def _begin_file_action(self, status: str):
        if self._action_running:
            return None
        self._action_running = True
        self._action_generation += 1
        generation = self._action_generation
        cancel_event = threading.Event()
        self._action_cancel_event = cancel_event
        self.cancel_btn.configure(text="✕ Stop", command=self._cancel_file_action)
        if not self.cancel_btn.winfo_manager():
            self.cancel_btn.pack(side="left", padx=3, pady=9)
        self._set_status(status)
        return generation, cancel_event

    def _cancel_file_action(self):
        if self._action_cancel_event is not None:
            self._action_cancel_event.set()
            self.cancel_btn.configure(text="Stopping…")
            self._set_status("Stopping after the current file operation…")

    def _action_progress(self, generation: int, text: str):
        if self._action_running and generation == self._action_generation:
            self._set_status(text)

    def _finish_file_action(self, generation: int, status: Optional[str] = None):
        if not self._action_running or generation != self._action_generation:
            return False
        self._action_running = False
        self._action_cancel_event = None
        self.cancel_btn.configure(text="✕ Stop", command=self._cancel_scan)
        self.cancel_btn.pack_forget()
        if status:
            self._set_status(status)
        pending = self._pending_scan_path
        self._pending_scan_path = None
        if pending:
            self.after(0, lambda: self.scan_folder(pending))
        return True

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
        if parent and parent != self.root_node.path:
            self.scan_folder(parent)

    def _update_disk(self, path: str):
        generation = self._scan_generation

        def worker():
            try:
                usage = shutil.disk_usage(path)
                label = f"Disk: {format_size(usage.free)} free of {format_size(usage.total)}"
            except OSError:
                label = ""
            self._io_results.put(("disk", (generation, path, label)))

        threading.Thread(target=worker, daemon=True).start()

    def _disk_usage_ready(self, generation: int, path: str, label: str):
        if (generation == self._scan_generation and self.root_node is not None
                and self.root_node.path == path):
            self.status_disk.configure(text=label)

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

    def _tree_is_active(self):
        return self.active_view in ("Tree", "Explore")

    def _on_filter_change(self, label: str):
        """Build a background projection for the selected file category."""
        reverse = {display: key for key, display in FILE_TYPE_FILTERS}
        key = reverse.get(label, "all")
        if self._advanced_spec is None and key == self.file_filter and (
                key == "all" or self._filter_index_key == key):
            return

        self.file_filter = key
        self._advanced_spec = None
        self.settings.file_filter = key
        self.settings.save()
        self.treemap_stack = []
        self.treemap_forward_stack = []
        # Duplicate results are specific to the active type projection.
        self._invalidate_duplicate_scan()
        self._invalidate_filter_index()

        if key == "all" or self.root_node is None:
            self._set_view_total()
            self._render_active_view()
            return

        self._start_filter_build()

    def _show_advanced_filter(self):
        """Compose in-memory query conditions; the type menu remains a preset."""
        spec = self._advanced_spec or QuerySpec.category(self.file_filter)
        window = ctk.CTkToplevel(self)
        window.title("Advanced filters")
        window.geometry("510x650")
        window.transient(self)
        body = ctk.CTkScrollableFrame(window, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=20, pady=16)
        ctk.CTkLabel(body, text="Match files", font=ctk.CTkFont(size=17, weight="bold")).pack(anchor="w")
        ctk.CTkLabel(body, text="Within a field, choices use OR. Between fields, conditions use AND.",
                     text_color="gray", wraplength=450).pack(anchor="w", pady=(2, 10))
        ctk.CTkLabel(body, text="File types (none selected means all)").pack(anchor="w")
        categories_frame = ctk.CTkFrame(body, fg_color="transparent")
        categories_frame.pack(fill="x")
        category_vars = {}
        for position, (key, label) in enumerate(FILE_TYPE_FILTERS[1:]):
            selected = ctk.BooleanVar(value=key in spec.categories)
            category_vars[key] = selected
            ctk.CTkCheckBox(categories_frame, text=label, variable=selected, width=185).grid(
                row=position // 2, column=position % 2, sticky="w", padx=3, pady=4)

        def entry(label, value, hint=""):
            ctk.CTkLabel(body, text=label).pack(anchor="w", pady=(10, 2))
            field = ctk.CTkEntry(body, width=445, placeholder_text=hint)
            field.insert(0, value)
            field.pack(anchor="w")
            return field

        ext = entry("Extensions (comma separated)", ", ".join(spec.extensions), ".png, .jpg")
        name = entry("Filename contains", spec.name, "optional")
        lower = entry("Minimum size in MiB", "" if spec.min_size is None else
                      str(spec.min_size / 1048576), "optional")
        upper = entry("Maximum size in MiB", "" if spec.max_size is None else
                      str(spec.max_size / 1048576), "optional")
        from datetime import datetime
        after = entry("Modified on or after (YYYY-MM-DD)", "" if spec.modified_after_ns is None else
                      datetime.fromtimestamp(spec.modified_after_ns / 1e9).date().isoformat())
        before = entry("Modified on or before (YYYY-MM-DD)", "" if spec.modified_before_ns is None else
                       datetime.fromtimestamp(spec.modified_before_ns / 1e9).date().isoformat())
        hidden = ctk.BooleanVar(value=spec.include_hidden)
        ctk.CTkCheckBox(body, text="Include hidden files", variable=hidden).pack(anchor="w", pady=12)

        def apply():
            try:
                chosen = query_from_form(
                    categories=(key for key, variable in category_vars.items() if variable.get()),
                    extensions=ext.get(), name=name.get(), min_mib=lower.get(), max_mib=upper.get(),
                    modified_after=after.get(), modified_before=before.get(),
                    include_hidden=hidden.get())
            except (ValueError, ArithmeticError, OverflowError) as exc:
                messagebox.showerror("Invalid filter", str(exc), parent=window)
                return
            self._advanced_spec = chosen if chosen != QuerySpec() else None
            self.file_filter = "all"
            self.filter_var.set(FILE_TYPE_FILTER_LABELS["all"])
            self.settings.file_filter = "all"
            self.settings.save()
            self.treemap_stack = []
            self.treemap_forward_stack = []
            self._invalidate_duplicate_scan()
            self._invalidate_filter_index()
            window.destroy()
            if self.root_node is None or not self._has_active_filter():
                self._set_view_total()
                self._render_active_view()
            else:
                self._start_filter_build()

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.pack(fill="x", pady=(8, 0))
        ctk.CTkButton(buttons, text="Apply", command=apply).pack(side="left")
        ctk.CTkButton(buttons, text="Cancel", fg_color="transparent", border_width=1,
                      text_color=("gray20", "gray80"), command=window.destroy).pack(side="right")

    def _clear_body(self):
        self._tree_generation += 1
        self._tree_search_loading = False
        self._tree_search_matches = []
        self._tree_search_offset = 0
        self._tree_search_has_more = False
        self._tree_sort_loading_paths.clear()
        self._tree_sort_rows.clear()
        self._pending_tree_expansions.clear()
        self._pending_tree_expansion_pages.clear()
        self._pending_explore_select_node = None
        self._largest_generation += 1
        self._largest_loading = False
        self._types_generation += 1
        self._types_loading = False
        self._types_host = None
        self._types_status = None
        if self.tooltip:
            self.tooltip.hide()
        self._invalidate_treemap_render()
        self._tiles = []
        self._hover_tile = None
        self._treemap_focus_tile = None
        self._treemap_image = None
        if self._treemap_redraw_after:
            self.after_cancel(self._treemap_redraw_after)
            self._treemap_redraw_after = None
        for child in self.body.winfo_children():
            child.destroy()
        # drop references to the widgets we just destroyed so nothing
        # reaches for a stale one later
        self.tree = None
        self.iid_to_node = {}
        self._node_id_to_tree_iid = {}
        self.largest_tree = None
        self.dup_tree = None
        self._treemap_focus_info = None
        self._treemap_workspace = None
        self._treemap_detail_panel = None
        self._treemap_detail_name = None
        self._treemap_detail_summary = None
        self._treemap_detail_location = None
        self._treemap_detail_action = None
        self._treemap_details_toggle = None
        self._explore_tree_host = None
        self._explore_map_host = None
        self._tree_compact = False
        self._scan_preview_tree = None
        self._scan_preview_status = None
        self._scan_preview_signature = None
        # every row that was pointing at a thumbnail is gone with them; the
        # map would otherwise grow for the lifetime of the session
        self._row_by_path.clear()
        if len(self._row_photos) > self.MAX_ROW_PHOTOS:
            self._row_photos.clear()

    def _render_active_view(self):
        self._clear_body()
        if self.root_node is None and not self._scan_completed:
            snapshot = self._latest_scan_snapshot
            if snapshot is not None and snapshot.observed_files:
                self._render_scan_preview(snapshot)
            else:
                self._empty_hint("Scanning folder…")
            return
        if self.active_view == "Explore":
            self._render_explore()
        elif self.active_view == "Tree":
            self._render_tree()
        elif self.active_view == "Treemap":
            self._render_treemap()
        elif self.active_view == "Largest Files":
            self._render_largest()
        elif self.active_view == "File Types":
            self._render_types()
        elif self.active_view == "Duplicates":
            self._render_duplicates()

    def _render_explore(self):
        """Show the hierarchy, size map, and selected-item details together."""
        if not self.root_node:
            self._empty_hint("Select a folder to analyze")
            return
        if not self._filter_ready_for_view():
            return

        self._treemap_details_expanded = None
        colors = self._colors()
        split = tk.PanedWindow(
            self.body, orient="horizontal", sashwidth=6, sashrelief="flat",
            showhandle=False, bg=colors['head_bg'], bd=0)
        split.pack(fill="both", expand=True)
        tree_host = tk.Frame(split, bg=colors['tree_bg'])
        map_host = tk.Frame(split, bg=colors['canvas_bg'])
        split.add(tree_host, minsize=250, stretch="always")
        split.add(map_host, minsize=430, stretch="always")
        self._explore_tree_host = tree_host
        self._explore_map_host = map_host

        tk.Label(tree_host, text="FOLDER TREE", bg=colors['head_bg'],
                 fg=colors['muted_fg'], font=("Segoe UI", 9, "bold"),
                 anchor="w", padx=12, pady=7).pack(fill="x")
        self._render_tree(parent=tree_host, compact=True)
        self._render_treemap(parent=map_host)

        def set_initial_split():
            try:
                if split.winfo_exists() and split.winfo_width() >= 700:
                    split.sashpos(0, int(split.winfo_width() * 0.38))
            except tk.TclError:
                pass
        self.after_idle(set_initial_split)

    def _empty_hint(self, text: str):
        """Shown when a view has nothing to display.

        With no folder scanned yet this is the first thing anyone sees, so it
        offers the places people actually want to look at rather than just
        telling them to go and find one.
        """
        colors = self._colors()
        wrap = tk.Frame(self.body, bg=colors['tree_bg'])
        wrap.pack(fill="both", expand=True)

        if self.root_node is not None or text != "Select a folder to analyze":
            tk.Label(wrap, text=text, fg=colors['muted_fg'], bg=colors['tree_bg'],
                     font=("Segoe UI", 13)).place(relx=0.5, rely=0.45, anchor="center")
            return

        centre = tk.Frame(wrap, bg=colors['tree_bg'])
        centre.place(relx=0.5, rely=0.5, anchor="center")

        tk.Label(centre, text="Where should we look?", bg=colors['tree_bg'],
                 fg=colors['tree_fg'], font=("Segoe UI", 20, "bold")).pack()
        tk.Label(centre, text="Pick a place to scan, or browse for any folder.",
                 bg=colors['tree_bg'], fg=colors['muted_fg'],
                 font=("Segoe UI", 11)).pack(pady=(6, 22))

        grid = tk.Frame(centre, bg=colors['tree_bg'])
        grid.pack()
        self._places_grid = grid
        if self._places is None:
            tk.Label(grid, text="Loading places…", bg=colors['tree_bg'],
                     fg=colors['muted_fg']).pack()
            if not self._places_loading:
                self._places_loading = True

                def load_places():
                    try:
                        places = locations.start_places()[:12]
                    except OSError:
                        places = []
                    self._io_results.put(("places", places))

                threading.Thread(target=load_places, daemon=True).start()
        else:
            self._render_places(grid, colors)

        browse = tk.Label(centre, text="⌕  Browse for another folder…",
                          bg=colors['tree_bg'], fg=ACCENT, cursor="hand2",
                          font=("Segoe UI", 11, "underline"))
        browse.pack(pady=(20, 0))
        browse.bind("<Button-1>", lambda e: self._browse_folder())

    def _places_ready(self, places):
        self._places = places
        self._places_loading = False
        grid = self._places_grid
        if grid is not None and grid.winfo_exists():
            for child in grid.winfo_children():
                child.destroy()
            self._render_places(grid, self._colors())

    def _render_places(self, grid, colors):
        for index, place in enumerate(self._places):
            self._place_card(grid, place, colors).grid(
                row=index // 4, column=index % 4, padx=7, pady=7)

    def _place_card(self, parent, place, colors):
        """One clickable tile on the start screen, with its free space."""
        card = tk.Frame(parent, bg=colors['head_bg'], width=176, height=92,
                        highlightthickness=1, highlightbackground=colors['head_bg'],
                        cursor="hand2")
        card.pack_propagate(False)

        top = tk.Frame(card, bg=colors['head_bg'])
        top.pack(fill="x", padx=12, pady=(12, 2))
        tk.Label(top, text=place.icon, bg=colors['head_bg'], fg=colors['tree_fg'],
                 font=("Segoe UI", 16)).pack(side="left")
        tk.Label(top, text=place.label, bg=colors['head_bg'], fg=colors['tree_fg'],
                 font=("Segoe UI", 11, "bold")).pack(side="left", padx=(8, 0))

        # Only drives get the capacity bar. Every user folder lives on the
        # same disk, so repeating one figure across all of them was noise
        # that said nothing about the folder you were choosing.
        if place.is_drive and place.total:
            track = tk.Frame(card, bg=colors['tree_bg'], height=5)
            track.pack(fill="x", padx=12, pady=(8, 4))
            track.pack_propagate(False)
            fill = tk.Frame(track, bg=ACCENT if place.used_fraction < 0.9 else "#dc2626")
            fill.place(relx=0, rely=0, relwidth=max(place.used_fraction, 0.02), relheight=1)
            tk.Label(card, text=f"{format_size(place.free)} free",
                     bg=colors['head_bg'], fg=colors['muted_fg'],
                     font=("Segoe UI", 9)).pack(anchor="w", padx=12)
        else:
            home = os.path.expanduser("~")
            shown = place.path
            if shown.startswith(home):
                shown = "~" + shown[len(home):] or "~"
            tk.Label(card, text=shown, bg=colors['head_bg'], fg=colors['muted_fg'],
                     font=("Segoe UI", 9), anchor="w").pack(anchor="w", padx=12, pady=(12, 0))

        def enter(_e):
            card.configure(highlightbackground=ACCENT)

        def leave(_e):
            card.configure(highlightbackground=colors['head_bg'])

        def open_place(_e):
            self.scan_folder(place.path)

        for widget in (card, top, *top.winfo_children(), *card.winfo_children()):
            widget.bind("<Button-1>", open_place)
            widget.bind("<Enter>", enter)
            widget.bind("<Leave>", leave)
        return card

    # ---- shared treeview styling

    def _make_treeview(self, columns, headings, widths, parent=None):
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

        wrap = tk.Frame(parent or self.body, bg=colors['tree_bg'])
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

    TREE_PAGE_SIZE = 200
    TREE_ASYNC_SORT_THRESHOLD = 5000

    def _render_tree(self, parent=None, compact=False):
        if not self.root_node:
            self._empty_hint("Select a folder to analyze")
            return
        if not self._filter_ready_for_view():
            return

        arrow = " ↓" if self.sort_reverse else " ↑"
        marks = {key: (arrow if key == self.sort_key else "")
                 for key in ("name", "size", "type", "date")}
        headings = {
            "#0": ("Name" + marks["name"], lambda: self._sort_tree("name")),
            "usage": ("Usage" + marks["size"], lambda: self._sort_tree("size")),
            "size": ("Size" + marks["size"], lambda: self._sort_tree("size")),
            "items": ("Items", lambda: self._sort_tree("size")),
            "type": ("Type" + marks["type"], lambda: self._sort_tree("type")),
            "modified": ("Created" + marks["date"], lambda: self._sort_tree("date")),
        }
        widths = {
            "#0": (440, 220, "w", True),
            "usage": (170, 140, "w", False),
            "size": (100, 80, "e", False),
            "items": (78, 60, "e", False),
            "type": (100, 80, "w", False),
            "modified": (130, 110, "w", False),
        }
        columns = ("usage", "size", "items", "type", "modified")
        if compact:
            headings = {
                "#0": ("Name" + marks["name"], lambda: self._sort_tree("name")),
                "size": ("Size" + marks["size"], lambda: self._sort_tree("size")),
                "items": ("Items", lambda: self._sort_tree("size")),
            }
            widths = {
                "#0": (260, 130, "w", True),
                "size": (90, 76, "e", False),
                "items": (62, 52, "e", False),
            }
            columns = ("size", "items")
        self._tree_compact = compact
        self.tree = self._make_treeview(columns, headings, widths, parent=parent)
        self.iid_to_node = {}
        self._node_id_to_tree_iid = {}
        page_counts = getattr(self, "_tree_sort_pages", {})
        self._tree_pages = {}
        self._tree_page_data = {}

        self.tree.bind("<<TreeviewOpen>>", self._on_tree_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.tree.bind("<Double-Button-1>", self._on_tree_double)
        self.tree.bind("<Return>", self._on_tree_page_key)
        self.tree.bind("<Button-3>", self._on_tree_right)
        self.tree.bind("<Delete>", lambda e: self._delete_selected())

        self.tree_menu = tk.Menu(self, tearoff=0)
        self.tree_menu.add_command(label="Open in Explorer", command=self._open_in_explorer)
        self.tree_menu.add_command(label="Copy path", command=self._copy_path)
        self.tree_menu.add_command(label="Zip selected…", command=self._zip_selected)
        self.tree_menu.add_separator()
        self.tree_menu.add_command(label="Delete selected", command=self._delete_selected)

        if self.search_query:
            self._fill_tree_search()
        else:
            self._insert_tree_children("", self.root_node,
                                       limit=page_counts.get(self.root_node.path))

    def _tree_values(self, node: Node, parent: Node):
        node_size = self._node_size(node)
        if self._tree_compact:
            if node.is_dir:
                return (format_size(node_size), f"{self._node_count(node):,}")
            return (format_size(node_size), "")
        parent_size = self._node_size(parent) if parent else 0
        pct = calculate_percentage(node_size, parent_size) if parent_size else 0.0
        filled = round(pct / 100 * self.BAR_WIDTH)
        bar = "█" * filled + "░" * (self.BAR_WIDTH - filled)
        usage = f"{bar} {pct:4.1f}%"
        if node.is_dir:
            return (usage, format_size(node_size), f"{self._node_count(node):,}", "Folder", format_date(node.creation_date))
        return (usage, format_size(node_size), "", get_file_category(node.name, is_dir=False)['label'], format_date(node.creation_date))

    def _thumb_size(self) -> int:
        return max(16, self.settings.row_height() - 6)

    def _register_row_thumbnail(self, tree, iid: str, node: Node):
        """Ask for a small preview for an image row; it is applied when ready."""
        if node.is_dir or not self.settings.list_thumbnails or not is_image_file(node.name):
            return
        size = self._thumb_size()
        mtime = self._node_mtime(node)
        self._row_by_path.setdefault(node.path, []).append((tree, iid, mtime))
        image = self.thumbnails.request(node.path, (size, size), mtime=mtime)
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
        mtime = rows[0][2] if len(rows[0]) > 2 else None
        image = self.thumbnails.get(path, (size, size), mtime=mtime)
        if image is None:
            return
        alive = []
        for row in rows:
            tree, iid = row[:2]
            try:
                if tree.winfo_exists() and tree.exists(iid):
                    self._set_row_image(tree, iid, path, image)
                    alive.append(row)
            except tk.TclError:
                continue
        self._row_by_path[path] = alive

    def _insert_tree_children(self, parent_iid: str, parent_node: Node,
                              start: int = 0, limit: Optional[int] = None):
        children = self._tree_page_data.get(parent_node.path)
        if children is None:
            if len(parent_node.children) >= self.TREE_ASYNC_SORT_THRESHOLD:
                self._tree_pages[parent_node.path] = start + (limit or self.TREE_PAGE_SIZE)
                self._start_tree_child_sort(parent_iid, parent_node, start, limit)
                return
            children = self._sorted_children(parent_node)
            self._tree_page_data[parent_node.path] = children
        end = min(len(children), start + (limit or self.TREE_PAGE_SIZE))
        for child in children[start:end]:
            icon = ICONS['folder'] if child.is_dir else get_file_icon(child.name, is_dir=False)
            tags = []
            if child.is_dir:
                tags.append("folder")
            if child.error:
                tags.append("error")
            iid = self.tree.insert(parent_iid, "end", text=f"{icon} {child.name}",
                                   values=self._tree_values(child, parent_node), tags=tuple(tags))
            self.iid_to_node[iid] = child
            self._node_id_to_tree_iid[id(child)] = iid
            self._register_row_thumbnail(self.tree, iid, child)
            if child.is_dir and self._node_count(child) > 0:
                self.tree.insert(iid, "end", text="…", tags=("dummy",))
        self._tree_pages[parent_node.path] = end
        if end < len(children):
            remaining = len(children) - end
            self.tree.insert(parent_iid, "end",
                             text=f"Show next {min(self.TREE_PAGE_SIZE, remaining):,} of {remaining:,} remaining…",
                             tags=("page",))

    def _start_tree_child_sort(self, parent_iid: str, parent_node: Node,
                               start: int, limit: Optional[int]):
        if parent_node.path in self._tree_sort_loading_paths:
            return
        self._tree_sort_loading_paths.add(parent_node.path)
        loading_iid = self.tree.insert(parent_iid, "end", text="Preparing directory listing…",
                                       tags=("info",))
        self._tree_sort_rows[parent_node.path] = loading_iid
        root = self.root_node
        scan_generation = self._scan_generation
        filter_generation = self._filter_generation
        generation = self._tree_generation
        projection_key = self._projection_key()
        filter_index = self._filter_index if self._has_active_filter() else None
        sort_key, sort_reverse = self.sort_key, self.sort_reverse

        def cancelled():
            return (generation != self._tree_generation
                    or scan_generation != self._scan_generation
                    or root is not self.root_node
                    or filter_generation != self._filter_generation
                    or projection_key != self._projection_key())

        def worker():
            try:
                children = (filter_index.sorted_children(
                    parent_node, sort_key, sort_reverse, should_cancel=cancelled)
                    if filter_index is not None else
                    parent_node.sorted_children(
                        sort_key, sort_reverse, should_cancel=cancelled))
                if cancelled():
                    return
                payload = (generation, scan_generation, filter_generation,
                           projection_key, root, parent_iid, parent_node, start,
                           limit, loading_iid, children, None)
            except Exception as exc:
                if cancelled():
                    return
                payload = (generation, scan_generation, filter_generation,
                           projection_key, root, parent_iid, parent_node, start,
                           limit, loading_iid, [], str(exc))
            self._io_results.put(("tree-sort", payload))

        threading.Thread(target=worker, daemon=True,
                         name=f"folderlens-tree-sort-{generation}").start()

    def _tree_child_sort_ready(self, generation, scan_generation, filter_generation,
                               projection_key, root, parent_iid, parent_node, start,
                               limit, loading_iid, children, error):
        if (generation != self._tree_generation
                or scan_generation != self._scan_generation
                or filter_generation != self._filter_generation
                or projection_key != self._projection_key()
                or root is not self.root_node
                or not self._tree_is_active()
                or self.tree is None):
            return
        self._tree_sort_loading_paths.discard(parent_node.path)
        self._tree_sort_rows.pop(parent_node.path, None)
        if self.tree.exists(loading_iid):
            self.tree.delete(loading_iid)
        if error:
            self.tree.insert(parent_iid, "end", text=f"Could not sort directory: {error}",
                             tags=("info",))
            self._restore_pending_tree_expansions()
            return
        self._tree_page_data[parent_node.path] = children
        self._insert_tree_children(parent_iid, parent_node, start=start, limit=limit)
        self._restore_pending_tree_expansions()
        pending_node = self._pending_explore_select_node
        if pending_node is not None and self.active_view == "Explore":
            self._pending_explore_select_node = None
            self._sync_explore_tree_selection(pending_node)

    def _fill_tree_search(self):
        root = self.root_node
        generation = self._tree_generation
        scan_generation = self._scan_generation
        filter_generation = self._filter_generation
        projection_key = self._projection_key()
        query = self.search_query
        filter_index = self._filter_index if self._has_active_filter() else None
        filter_key = self.file_filter
        self._tree_search_loading = True
        self._set_status(f"Searching for {query!r}…")

        def cancelled():
            return (generation != self._tree_generation
                    or scan_generation != self._scan_generation
                    or root is not self.root_node
                    or filter_generation != self._filter_generation
                    or projection_key != self._projection_key())

        def worker():
            try:
                # One extra item tells the UI whether the visible result set
                # was capped; names are still ranked before the cap is applied.
                matches = analysis.find_matches(
                    root, query, limit=1001, filter_key=filter_key,
                    filter_index=filter_index, should_cancel=cancelled)
                if cancelled():
                    return
                has_more = len(matches) > 1000
                matches = matches[:1000]
                payload = (generation, scan_generation, filter_generation,
                           projection_key, root, matches, has_more, None)
            except Exception as exc:
                if cancelled():
                    return
                payload = (generation, scan_generation, filter_generation,
                           projection_key, root, [], False, str(exc))
            self._io_results.put(("tree-search", payload))

        threading.Thread(target=worker, daemon=True,
                         name=f"folderlens-tree-search-{generation}").start()

    def _tree_search_results_ready(self, generation, scan_generation, filter_generation,
                                   projection_key, root, matches, has_more, error):
        if (generation != self._tree_generation
                or scan_generation != self._scan_generation
                or filter_generation != self._filter_generation
                or projection_key != self._projection_key()
                or root is not self.root_node
                or not self._tree_is_active()
                or self.tree is None):
            return
        self._tree_search_loading = False
        if error:
            self._set_status(f"Search failed: {error}")
            return
        self._tree_search_matches = matches
        self._tree_search_has_more = has_more
        self._tree_search_offset = 0
        self._insert_tree_search_page()
        suffix = " · showing the 1,000 largest matches" if has_more else ""
        self._set_status(f"{len(matches):,} search results{suffix}")

    def _insert_tree_search_page(self, page_iid: Optional[str] = None):
        if page_iid and self.tree.exists(page_iid):
            self.tree.delete(page_iid)
        start = self._tree_search_offset
        end = min(len(self._tree_search_matches), start + self.TREE_PAGE_SIZE)
        for node in self._tree_search_matches[start:end]:
            icon = ICONS['folder'] if node.is_dir else get_file_icon(node.name, is_dir=False)
            tags = ["folder"] if node.is_dir else []
            iid = self.tree.insert("", "end", text=f"{icon} {node.name}",
                                   values=self._tree_values(node, self.root_node), tags=tuple(tags))
            self.iid_to_node[iid] = node
            self._node_id_to_tree_iid[id(node)] = iid
            self._register_row_thumbnail(self.tree, iid, node)
        self._tree_search_offset = end
        if end < len(self._tree_search_matches):
            remaining = len(self._tree_search_matches) - end
            self.tree.insert("", "end",
                             text=f"Show next {min(self.TREE_PAGE_SIZE, remaining):,} of "
                                  f"{remaining:,} remaining search results…",
                             tags=("page",))
        elif self._tree_search_has_more:
            self.tree.insert("", "end",
                             text="Showing the 1,000 largest search matches. Refine the search to see others…",
                             tags=("info",))

    def _is_dummy(self, iid: str) -> bool:
        return "dummy" in self.tree.item(iid, "tags")

    def _load_tree_page(self, iid: str) -> bool:
        if not iid or "page" not in self.tree.item(iid, "tags"):
            return False
        if self.search_query:
            self._insert_tree_search_page(iid)
            return True
        parent_iid = self.tree.parent(iid)
        parent_node = self.iid_to_node.get(parent_iid) if parent_iid else self.root_node
        if parent_node is None:
            return False
        start = self._tree_pages.get(parent_node.path, 0)
        self.tree.delete(iid)
        self._insert_tree_children(parent_iid, parent_node, start=start)
        return True

    def _on_tree_page_key(self, event):
        self._load_tree_page(self.tree.focus())

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
        page_counts = dict(getattr(self, "_tree_pages", {}))
        expanded = set()
        pending = list(self.tree.get_children())
        while pending:
            iid = pending.pop()
            node = self.iid_to_node.get(iid)
            if node and self.tree.item(iid, "open"):
                expanded.add(node.path)
                pending.extend(self.tree.get_children(iid))
        expanded.update(self._pending_tree_expansions)
        self._tree_sort_pages = page_counts
        try:
            self._render_active_view()      # rebuild headers and rows in new order
        finally:
            self._tree_sort_pages = {}

        self._pending_tree_expansions = expanded
        self._pending_tree_expansion_pages = page_counts
        self._restore_pending_tree_expansions()

    def _restore_pending_tree_expansions(self):
        """Restore open folders as their asynchronous sorted pages arrive."""
        if not self._pending_tree_expansions or self.tree is None:
            return
        pending_paths = self._pending_tree_expansions
        unresolved = set(pending_paths)
        stack = list(self.tree.get_children(""))
        while stack:
            iid = stack.pop()
            node = self.iid_to_node.get(iid)
            if node is None or node.path not in pending_paths:
                continue
            children = self.tree.get_children(iid)
            if len(children) == 1 and self._is_dummy(children[0]):
                self.tree.delete(children[0])
                self._insert_tree_children(
                    iid, node, limit=self._pending_tree_expansion_pages.get(node.path))
            self.tree.item(iid, open=True)
            if node.path in self._tree_sort_loading_paths:
                continue
            unresolved.discard(node.path)
            stack.extend(child_iid for child_iid in self.tree.get_children(iid)
                          if child_iid in self.iid_to_node)
        self._pending_tree_expansions = unresolved
        if not unresolved:
            self._pending_tree_expansion_pages.clear()

    def _on_tree_select(self, event):
        nodes = self._selected_nodes()
        if not nodes:
            if self.root_node:
                self._set_status(f"{self._node_count(self.root_node):,} {self._count_label()}")
            return
        total = sum(self._node_size(n) for n in nodes)
        self._set_status(f"{len(nodes)} selected · {format_size(total)}")
        if self._tree_is_active() and not self._selection_sync_in_progress:
            if len(nodes) == 1:
                node = nodes[0]
                self._cross_selection_path = node.path
                self._cross_selection_node = node
                if self.active_view == "Explore":
                    tile = self._find_treemap_tile_for_node(node)
                    if (tile is not None and
                            (tile.node is node or
                             (getattr(tile.node, "is_aggregate", False)
                              and tile.node.parent is node.parent))):
                        self._treemap_set_focus(tile)
                    else:
                        self._show_explore_node_in_map(node)
            else:
                self._cross_selection_path = None
                self._cross_selection_node = None

    def _on_tree_double(self, event):
        iid = self.tree.identify_row(event.y)
        if self._load_tree_page(iid):
            return
        node = self.iid_to_node.get(iid)
        if not node:
            return
        if node.is_dir:
            self.scan_folder(node.path)
        elif self.settings.preview_enabled and is_image_file(node.name):
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
        if not self._filter_ready_for_view():
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

        root = self.root_node
        scan_generation = self._scan_generation
        filter_generation = self._filter_generation
        generation = self._largest_generation
        projection_key = self._projection_key()
        filter_index = self._filter_index if self._has_active_filter() else None
        filter_key = self.file_filter
        name_query = self.search_query
        self._largest_loading = True
        self._set_status("Preparing largest files…")

        def cancelled():
            return (generation != self._largest_generation
                    or scan_generation != self._scan_generation
                    or root is not self.root_node
                    or filter_generation != self._filter_generation
                    or projection_key != self._projection_key())

        def worker():
            try:
                files = analysis.largest_files(
                    root, 100, filter_key=filter_key, filter_index=filter_index,
                    name_query=name_query, should_cancel=cancelled)
                if cancelled():
                    return
                payload = (generation, scan_generation, filter_generation,
                           projection_key, root, files, None)
            except Exception as exc:
                if cancelled():
                    return
                payload = (generation, scan_generation, filter_generation,
                           projection_key, root, [], str(exc))
            self._io_results.put(("largest", payload))

        threading.Thread(target=worker, daemon=True,
                         name=f"folderlens-largest-{generation}").start()

    def _largest_results_ready(self, generation, scan_generation, filter_generation,
                               projection_key, root, files, error):
        if (generation != self._largest_generation
                or scan_generation != self._scan_generation
                or filter_generation != self._filter_generation
                or projection_key != self._projection_key()
                or root is not self.root_node
                or self.active_view != "Largest Files"
                or self.largest_tree is None):
            return
        self._largest_loading = False
        if error:
            self._set_status(f"Could not list largest files: {error}")
            return
        if not files:
            self._set_status("No matching files")
            return
        for node in files:
            iid = self.largest_tree.insert(
                "", "end", text=f"{get_file_icon(node.name, is_dir=False)} {node.name}",
                values=(format_size(self._node_size(node)), get_file_category(node.name, is_dir=False)['label'],
                        os.path.dirname(node.path)))
            self.largest_map[iid] = node
            self._register_row_thumbnail(self.largest_tree, iid, node)

        def on_double(event):
            node = self.largest_map.get(self.largest_tree.identify_row(event.y))
            if node and self.settings.preview_enabled and is_image_file(node.name):
                self._open_image(node.path)
            elif node:
                self._reveal(node.path)
        self.largest_tree.bind("<Double-Button-1>", on_double)
        self._set_status(f"Showing {len(files):,} largest files")

    # ---- File types view

    def _render_types(self):
        if not self.root_node:
            self._empty_hint("Select a folder to analyze")
            return
        if not self._filter_ready_for_view():
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

        tk.Label(inner, text="File type breakdown", bg=colors['tree_bg'], fg=colors['tree_fg'],
                 font=("Segoe UI", 15, "bold")).pack(anchor="w", padx=24, pady=(20, 12))

        status = tk.Label(inner, text="Calculating file type totals…", bg=colors['tree_bg'],
                          fg=colors['muted_fg'], font=("Segoe UI", 11))
        status.pack(anchor="w", padx=24)
        self._types_host = inner
        self._types_status = status
        self._types_loading = True

        root = self.root_node
        scan_generation = self._scan_generation
        filter_generation = self._filter_generation
        generation = self._types_generation
        projection_key = self._projection_key()
        filter_index = self._filter_index if self._has_active_filter() else None
        filter_key = self.file_filter
        total = self._node_size(root) or 1

        def cancelled():
            return (generation != self._types_generation
                    or scan_generation != self._scan_generation
                    or root is not self.root_node
                    or filter_generation != self._filter_generation
                    or projection_key != self._projection_key())

        def worker():
            try:
                stats = analysis.category_breakdown(
                    root, filter_key=filter_key, filter_index=filter_index,
                    should_cancel=cancelled)
                if cancelled():
                    return
                payload = (generation, scan_generation, filter_generation,
                           projection_key, root, total, colors, stats, None)
            except Exception as exc:
                if cancelled():
                    return
                payload = (generation, scan_generation, filter_generation,
                           projection_key, root, total, colors, [], str(exc))
            self._io_results.put(("types", payload))

        threading.Thread(target=worker, daemon=True,
                         name=f"folderlens-types-{generation}").start()

    def _types_results_ready(self, generation, scan_generation, filter_generation,
                             projection_key, root, total, colors, stats, error):
        if (generation != self._types_generation
                or scan_generation != self._scan_generation
                or filter_generation != self._filter_generation
                or projection_key != self._projection_key()
                or root is not self.root_node
                or self.active_view != "File Types"
                or self._types_host is None):
            return
        self._types_loading = False
        if self._types_status is not None:
            self._types_status.destroy()
            self._types_status = None
        if error:
            tk.Label(self._types_host, text=f"Could not calculate file type totals: {error}",
                     bg=colors['tree_bg'], fg=colors['error_fg'],
                     font=("Segoe UI", 11)).pack(anchor="w", padx=24)
            return
        if not stats:
            tk.Label(self._types_host, text="No files found", bg=colors['tree_bg'], fg=colors['muted_fg'],
                     font=("Segoe UI", 12)).pack(anchor="w", padx=24)
            return

        for stat in stats:
            row = tk.Frame(self._types_host, bg=colors['tree_bg'])
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

    # ---- Duplicates view

    def _render_duplicates(self):
        if not self.root_node:
            self._empty_hint("Select a folder to analyze")
            return
        if not self._filter_ready_for_view():
            return

        colors = self._colors()
        bar = tk.Frame(self.body, bg=colors['head_bg'], height=38)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        self.dup_status = tk.Label(bar, text="", bg=colors['head_bg'], fg=colors['head_fg'],
                                   font=("Segoe UI", 10))
        self.dup_status.pack(side="left", padx=12)

        self.dup_button = tk.Button(bar, text="Find duplicates", bd=0, relief="flat",
                                    cursor="hand2", bg=ACCENT, fg="white",
                                    activebackground=ACCENT_HOVER, activeforeground="white",
                                    font=("Segoe UI", 10, "bold"), padx=14, pady=4,
                                    command=self._toggle_duplicate_scan)
        self.dup_button.pack(side="right", padx=10, pady=6)

        headings = {
            "#0": ("File / group", lambda: None),
            "size": ("Size", lambda: None),
            "wasted": ("Potential bytes", lambda: None),
            "path": ("Location", lambda: None),
        }
        widths = {
            "#0": (360, 220, "w", False),
            "size": (100, 80, "e", False),
            "wasted": (110, 90, "e", False),
            "path": (520, 240, "w", True),
        }
        self.dup_tree = self._make_treeview(("size", "wasted", "path"), headings, widths)
        self.dup_map = {}
        self.dup_tree.bind("<<TreeviewSelect>>", self._on_tree_select)
        self.dup_tree.bind("<Delete>", lambda e: self._delete_selected())
        self.dup_tree.bind("<Double-Button-1>", self._on_duplicate_double)

        if self.dup_groups:
            self._fill_duplicates()
        else:
            self.dup_status.configure(
                text="Find byte-identical copies; potential bytes are not guaranteed disk savings")

    def _on_duplicate_double(self, event):
        node = self.dup_map.get(self.dup_tree.identify_row(event.y))
        if node:
            self._reveal(node.path)

    def _toggle_duplicate_scan(self):
        if self._dup_running:
            self._dup_cancel = True
            return
        self._start_duplicate_scan()

    def _invalidate_duplicate_scan(self):
        self._duplicate_generation += 1
        self._dup_cancel = True
        self._dup_running = False
        self.dup_groups = []

    def _start_duplicate_scan(self):
        root = self.root_node
        if not root:
            return
        if self._is_network_root and not messagebox.askyesno(
                "Read network files?",
                "Finding duplicates reads file contents over the network. "
                "Limit this scan to 1 GiB of reads or two minutes?",
                parent=self):
            return
        filter_index = self._filter_index
        filter_key = self.file_filter
        network = self._is_network_root
        self._duplicate_generation += 1
        generation = self._duplicate_generation
        self._dup_running = True
        self._dup_cancel = False
        self.dup_button.configure(text="Stop")
        self.dup_status.configure(text="Scanning…")

        def report(stage, done, total):
            if total:
                self.after(0, lambda: self._safe_dup_status(f"{stage}… {done:,}/{total:,}")
                           if generation == self._duplicate_generation else None)

        def worker():
            try:
                found = duplicates.find_duplicates(
                    root, min_size=self.DUPLICATE_MIN_SIZE,
                    progress=report,
                    should_cancel=lambda: (self._dup_cancel or generation != self._duplicate_generation
                                           or root is not self.root_node),
                    filter_key=filter_key, filter_index=filter_index,
                    max_read_bytes=(1 << 30) if network else None,
                    max_seconds=120 if network else None)
            except Exception as exc:
                message = str(exc)
                self.after(0, lambda: self._duplicate_scan_failed(message, generation))
                return
            self.after(0, lambda: self._duplicate_scan_done(found, generation))

        threading.Thread(target=worker, daemon=True).start()

    def _safe_dup_status(self, text: str):
        if self.active_view == "Duplicates" and getattr(self, "dup_status", None) is not None:
            try:
                self.dup_status.configure(text=text)
            except tk.TclError:
                pass

    def _duplicate_scan_failed(self, message: str, generation: int):
        if generation != self._duplicate_generation:
            return
        self._dup_running = False
        self._safe_dup_status(f"Failed: {message}")
        if self.active_view == "Duplicates":
            try:
                self.dup_button.configure(text="Find duplicates")
            except tk.TclError:
                pass

    def _duplicate_scan_done(self, groups, generation: int):
        if generation != self._duplicate_generation:
            return
        self._dup_running = False
        cancelled = self._dup_cancel
        self.dup_groups = groups
        if self.active_view != "Duplicates":
            return
        try:
            self.dup_button.configure(text="Rescan")
        except tk.TclError:
            return
        if cancelled:
            self._safe_dup_status("Cancelled")
            return
        self._fill_duplicates()

    def _fill_duplicates(self):
        tree = self.dup_tree
        if tree is None:
            return
        tree.delete(*tree.get_children())
        self.dup_map = {}

        if not self.dup_groups:
            self._safe_dup_status("No duplicate files found")
            return

        potential = duplicates.total_wasted(self.dup_groups)
        self._safe_dup_status(
            f"{len(self.dup_groups):,} groups · {format_size(potential)} potential logical bytes")

        for group in self.dup_groups:
            parent = tree.insert(
                "", "end",
                text=f"{ICONS['folder_open']} {group.count} copies · {group.nodes[0].name}",
                values=(format_size(group.size), format_size(group.wasted), ""),
                tags=("folder",), open=False)
            for node in sorted(group.nodes, key=lambda n: (len(n.path), n.path)):
                iid = tree.insert(
                    parent, "end", text=f"{get_file_icon(node.name, is_dir=False)} {node.name}",
                    values=(format_size(node.size), "", os.path.dirname(node.path)))
                self.dup_map[iid] = node
                self._register_row_thumbnail(tree, iid, node)

    # ---- Treemap view

    def _render_treemap(self, parent=None):
        if not self.root_node:
            self._empty_hint("Select a folder to analyze")
            return
        if not self._filter_ready_for_view():
            return
        colors = self._colors()
        node = self.treemap_stack[-1] if self.treemap_stack else self.root_node

        wrap = tk.Frame(parent or self.body, bg=colors['canvas_bg'])
        wrap.pack(fill="both", expand=True)

        # A compact context bar makes the map self-explanatory after a drill
        # down and keeps the path/size/type summary in one stable place.
        summary = tk.Frame(wrap, bg=colors['head_bg'], height=42)
        summary.pack(fill="x")
        summary.pack_propagate(False)
        title = "Treemap  ·  " + node.name
        if self._has_active_filter():
            title += f"  ·  {self._filter_label()} only"
        tk.Label(summary, text=title, bg=colors['head_bg'], fg=colors['tree_fg'],
                 font=("Segoe UI", 11, "bold")).pack(side="left", padx=(14, 4), pady=11)
        tk.Label(summary,
                 text=f"{self._node_count(node):,} {self._count_label()} · {format_size(self._node_size(node))}",
                 bg=colors['head_bg'], fg=colors['muted_fg'],
                 font=("Segoe UI", 10)).pack(side="left", pady=11)
        if self.treemap_stack:
            back = tk.Label(summary, text="⬅ Back", bg=colors['head_bg'], fg=ACCENT,
                            cursor="hand2", font=("Segoe UI", 10, "bold"))
            back.pack(side="right", padx=14, pady=11)
            back.bind("<Button-1>", lambda e: self._treemap_back())
        if self.treemap_forward_stack:
            forward = tk.Label(summary, text="Forward ➡", bg=colors['head_bg'], fg=ACCENT,
                               cursor="hand2", font=("Segoe UI", 10, "bold"))
            forward.pack(side="right", padx=(0, 10), pady=11)
            forward.bind("<Button-1>", lambda e: self._treemap_forward())
        details_toggle = tk.Button(
            summary, text="Show details", command=self._toggle_treemap_details,
            bd=0, relief="flat", cursor="hand2", bg=colors['head_bg'], fg=ACCENT,
            activebackground=colors['head_bg'], activeforeground=ACCENT,
            font=("Segoe UI", 9, "bold"), padx=4)
        details_toggle.pack(side="right", padx=(6, 12), pady=11)
        self._treemap_details_toggle = details_toggle
        self._treemap_focus_info = tk.Label(
            summary, text="Arrows select · Enter opens · Backspace up",
            bg=colors['head_bg'], fg=colors['muted_fg'], font=("Segoe UI", 9), anchor="e")
        self._treemap_focus_info.pack(side="right", padx=(4, 12), pady=11)

        workspace = tk.Frame(wrap, bg=colors['canvas_bg'])
        workspace.pack(fill="both", expand=True)
        self._treemap_workspace = workspace
        self._treemap_detail_panel = tk.Frame(workspace, bg=colors['head_bg'], width=260)
        self._treemap_detail_panel.pack_propagate(False)
        tk.Label(self._treemap_detail_panel, text="Selection details",
                 bg=colors['head_bg'], fg=colors['tree_fg'],
                 font=("Segoe UI", 11, "bold")).pack(anchor="w", padx=14, pady=(16, 10))
        self._treemap_detail_name = tk.Label(
            self._treemap_detail_panel, text="Move over a tile or use arrow keys",
            bg=colors['head_bg'], fg=colors['tree_fg'],
            font=("Segoe UI", 10, "bold"), justify="left", anchor="w",
            wraplength=228)
        self._treemap_detail_name.pack(fill="x", padx=14, pady=(0, 8))
        self._treemap_detail_summary = tk.Label(
            self._treemap_detail_panel, text="",
            bg=colors['head_bg'], fg=colors['muted_fg'],
            font=("Segoe UI", 9), justify="left", anchor="w", wraplength=228)
        self._treemap_detail_summary.pack(fill="x", padx=14, pady=(0, 12))
        tk.Label(self._treemap_detail_panel, text="Location",
                 bg=colors['head_bg'], fg=colors['muted_fg'],
                 font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=14, pady=(4, 3))
        self._treemap_detail_location = tk.Label(
            self._treemap_detail_panel, text="",
            bg=colors['head_bg'], fg=colors['tree_fg'],
            font=("Segoe UI", 9), justify="left", anchor="nw", wraplength=228)
        self._treemap_detail_location.pack(fill="x", padx=14, pady=(0, 14))
        self._treemap_detail_action = tk.Button(
            self._treemap_detail_panel, text="Open selected", command=self._treemap_activate_focus,
            bd=0, relief="flat", cursor="hand2", bg=ACCENT, fg="white",
            activebackground=ACCENT_HOVER, activeforeground="white",
            font=("Segoe UI", 9, "bold"), padx=10, pady=7)
        self._treemap_detail_action.pack(fill="x", padx=14, pady=(4, 6))
        tk.Button(
            self._treemap_detail_panel, text="Show in Explorer",
            command=self._treemap_reveal_focus, bd=0, relief="flat", cursor="hand2",
            bg=colors['tree_bg'], fg=colors['tree_fg'], font=("Segoe UI", 9),
            padx=10, pady=6).pack(fill="x", padx=14, pady=4)
        tk.Button(
            self._treemap_detail_panel, text="Copy path", command=self._treemap_copy_focus_path,
            bd=0, relief="flat", cursor="hand2", bg=colors['tree_bg'],
            fg=colors['tree_fg'], font=("Segoe UI", 9), padx=10, pady=6
        ).pack(fill="x", padx=14, pady=4)

        self.treemap_canvas = tk.Canvas(workspace, bg=colors['canvas_bg'], highlightthickness=0,
                                       takefocus=True)
        self.treemap_canvas.pack(side="left", fill="both", expand=True)
        workspace.bind("<Configure>", lambda event: self._update_treemap_details_layout(
            event.width), add="+")
        def update_details_layout():
            try:
                if workspace.winfo_exists():
                    self._update_treemap_details_layout(workspace.winfo_width())
            except tk.TclError:
                pass
        self.after_idle(update_details_layout)
        self._build_treemap_legend(wrap, colors)
        if self.tooltip is None:
            self.tooltip = Tooltip(self)
        self.treemap_node = node
        self._hover_tile = None
        self._treemap_focus_tile = None
        self.treemap_canvas.bind("<Configure>", lambda e: self._schedule_treemap_redraw())
        self.treemap_canvas.bind("<Motion>", self._treemap_hover)
        self.treemap_canvas.bind("<Leave>", self._treemap_leave)
        self.treemap_canvas.bind("<Button-1>", self._treemap_click)
        self.treemap_canvas.bind("<Double-Button-1>", self._treemap_double_click)
        self.treemap_canvas.bind("<Button-3>", lambda e: self._treemap_back())
        self.treemap_canvas.bind("<FocusIn>", self._treemap_focus_in)
        self.treemap_canvas.bind("<Left>", lambda e: self._treemap_move_focus("left"))
        self.treemap_canvas.bind("<Right>", lambda e: self._treemap_move_focus("right"))
        self.treemap_canvas.bind("<Up>", lambda e: self._treemap_move_focus("up"))
        self.treemap_canvas.bind("<Down>", lambda e: self._treemap_move_focus("down"))
        self.treemap_canvas.bind("<Return>", self._treemap_activate_focus)
        self.treemap_canvas.bind("<Shift-BackSpace>", self._treemap_keyboard_forward)
        self.treemap_canvas.bind("<BackSpace>", self._treemap_keyboard_back)
        self._treemap_rendering = True
        self._treemap_redraw_after = self.after_idle(self._schedule_initial_treemap_draw)

    def _schedule_initial_treemap_draw(self):
        self._treemap_redraw_after = None
        if self.active_view in ("Treemap", "Explore") and self.treemap_canvas is not None:
            self._schedule_treemap_redraw()

    def _build_treemap_legend(self, parent, colors):
        """Render a compact colour key for the current map projection."""

        legend = tk.Frame(parent, bg=colors['head_bg'], height=26)
        legend.pack(fill="x")
        legend.pack_propagate(False)

        inner = tk.Frame(legend, bg=colors['head_bg'])
        inner.pack(side="left", padx=10)

        if self._advanced_spec is not None and self._advanced_spec.categories:
            shown = ("folder", *self._advanced_spec.categories)
        elif not self._has_base_filter():
            shown = tuple(FILE_CATEGORIES)
        else:
            shown = ("folder", self.file_filter) if self._advanced_spec is None else tuple(FILE_CATEGORIES)
        for key in shown:
            category = FILE_CATEGORIES.get(key)
            if not category:
                continue
            chip = tk.Frame(inner, bg=colors['head_bg'])
            chip.pack(side="left", padx=(0, 12))
            swatch = tk.Frame(chip, bg=category['color'], width=10, height=10)
            swatch.pack(side="left", pady=7)
            swatch.pack_propagate(False)
            tk.Label(chip, text=category['label'], bg=colors['head_bg'],
                     fg=colors['muted_fg'], font=("Segoe UI", 9)).pack(side="left", padx=(5, 0))

        tk.Label(legend, text="click a folder to drill in · right-click or Back button to go up",
                 bg=colors['head_bg'], fg=colors['muted_fg'],
                 font=("Segoe UI", 9)).pack(side="right", padx=12)

    def _treemap_leave(self, event):
        self.tooltip.hide()
        self._peek_path = None
        self._peek_args = None
        self._peek_mtime = None
        if self._hover_tile is not None:
            self._hover_tile = None
            self._draw_highlight(None)

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
            self._invalidate_treemap_render()
            return

        node = self.treemap_node
        if self._node_count(node) <= 0 or self._node_size(node) <= 0:
            self._invalidate_treemap_render()
            canvas.delete("all")
            self._tiles = []
            self._treemap_image = None
            canvas.create_text(w // 2, h // 2, text="Nothing to display",
                               fill=self._colors()['muted_fg'], font=("Segoe UI", 12))
            return

        self._treemap_generation += 1
        generation = self._treemap_generation
        if self._treemap_future is not None:
            self._treemap_future.cancel()
        self._treemap_rendering = True
        canvas.delete("all")
        canvas.create_text(w // 2, h // 2, text="Rendering map…",
                           fill=self._colors()['muted_fg'], font=("Segoe UI", 12))
        self._tiles = []
        self._hover_tile = None
        index = self._filter_index if self._has_active_filter() else None
        children_getter = index.children if index is not None else lambda item: item.children
        size_getter = index.size if index is not None else lambda item: item.size
        count_getter = index.count if index is not None else (
            lambda item: item.item_count
            if (item.is_dir or getattr(item, "is_aggregate", False)) else 1)
        opts = treemap_render.RenderOptions(
            dark_mode=self.settings.dark_mode,
            show_thumbnails=self.settings.treemap_thumbnails,
            # Remote image reads are the most visible source of latency after
            # the metadata scan. Keep the map useful, but deliberately small,
            # on network workfolders.
            max_thumbnails=48 if self._is_network_root else 120,
            thumbnail_mtime=self._node_mtime,
        )

        def worker():
            cancelled = lambda: (generation != self._treemap_generation
                                 or self.active_view not in ("Treemap", "Explore"))
            if index is not None:
                layout_children_getter = lambda item: index.children(
                    item, should_cancel=cancelled)
            else:
                layout_children_getter = children_getter
            tiles = analysis.build_treemap(
                node, 2, 2, w - 4, h - 4,
                min_area=110, max_depth=7, padding=3,
                header=treemap_render.RenderOptions.header,
                size_getter=size_getter,
                children_getter=layout_children_getter,
                count_getter=count_getter,
                max_children=1200,
                aggregate_category=(index.filter_key if index is not None else self.file_filter),
                should_cancel=cancelled)
            if cancelled():
                return tiles, None
            image = treemap_render.render_treemap(
                tiles, w, h, opts,
                thumb_provider=self._treemap_thumb if opts.show_thumbnails else None,
                should_cancel=cancelled)
            return tiles, image

        future = self._treemap_executor.submit(worker)
        self._treemap_future = future

        def completed(done):
            try:
                tiles, image = done.result()
                error = None
            except CancelledError:
                return
            except Exception as exc:
                tiles, image, error = [], None, str(exc)
            self._treemap_results.put((generation, tiles, image, error))

        future.add_done_callback(completed)
        self._schedule_treemap_result_poll()

    def _schedule_treemap_result_poll(self):
        if self._treemap_poll_after is None:
            self._treemap_poll_after = self.after(30, self._poll_treemap_results)

    def _poll_treemap_results(self):
        self._treemap_poll_after = None
        while True:
            try:
                generation, tiles, image, error = self._treemap_results.get_nowait()
            except queue.Empty:
                break
            if generation != self._treemap_generation:
                continue
            self._treemap_rendering = False
            if self.active_view not in ("Treemap", "Explore") or image is None:
                if error and self.active_view in ("Treemap", "Explore"):
                    self.treemap_canvas.delete("all")
                    self.treemap_canvas.create_text(
                        self.treemap_canvas.winfo_width() // 2,
                        self.treemap_canvas.winfo_height() // 2,
                        text=f"Could not render map: {error}",
                        fill=self._colors()['muted_fg'], font=("Segoe UI", 12))
                continue
            try:
                if not self.treemap_canvas.winfo_exists():
                    continue
            except tk.TclError:
                continue
            self._tiles = tiles
            self._treemap_image = image
            self._treemap_focus_tile = None
            self._treemap_photo = ImageTk.PhotoImage(image)
            self.treemap_canvas.delete("all")
            self.treemap_canvas.create_image(0, 0, anchor="nw", image=self._treemap_photo)
            self._highlight_id = None
            if self._treemap_focus_info is not None:
                self._treemap_focus_info.configure(
                    text="Map: arrows select · Enter opens · Backspace goes up")
            if self.active_view == "Explore" and self._cross_selection_path:
                node = self._cross_selection_node
                tile = (self._find_treemap_tile_for_node(node) if node is not None else
                        next((item for item in self._tiles
                              if getattr(item.node, "path", None) == self._cross_selection_path), None))
                if tile is not None:
                    self._treemap_set_focus(tile)
                    if node is not None:
                        self._sync_explore_tree_selection(node)
                    elif not getattr(tile.node, "is_aggregate", False):
                        self._sync_explore_tree_selection(tile.node)
                elif self.active_view == "Explore" and self._tiles:
                    self._treemap_set_focus(max(self._tiles,
                                                key=lambda item: (item.w * item.h, item.depth)))
            if (self._treemap_focus_tile is None
                    and self.treemap_canvas.focus_get() is self.treemap_canvas):
                self._treemap_focus_in()
        if self._treemap_rendering:
            self._schedule_treemap_result_poll()

    def _invalidate_treemap_render(self):
        self._treemap_generation += 1
        self._treemap_rendering = False
        if self._treemap_future is not None:
            self._treemap_future.cancel()
            self._treemap_future = None

    def _draw_highlight(self, tile):
        """Outline the hovered tile as a canvas item on top of the rendered
        image. Re-rendering the whole map just to move this outline cost a
        full re-composite of every tile on each mouse move."""
        canvas = self.treemap_canvas
        if self._highlight_id is not None:
            canvas.delete(self._highlight_id)
            self._highlight_id = None
        if tile is None:
            return
        self._highlight_id = canvas.create_rectangle(
            tile.x, tile.y, tile.x + tile.w - 1, tile.y + tile.h - 1,
            outline="#ffffff", width=2)

    def _announce_treemap_tile(self, tile):
        if self._treemap_focus_info is None or tile is None:
            return
        node = tile.node
        if getattr(node, "is_aggregate", False):
            kind = "Grouped items"
            location = node.parent.path
            item_count = node.item_count
            action = "Browse grouped items"
        else:
            kind = "Folder" if node.is_dir else get_file_category(node.name, is_dir=False)['label']
            location = node.path
            item_count = self._node_count(node) if node.is_dir else 1
            action = ("Zoom into folder" if node.is_dir else
                      "Open image" if is_image_file(node.name) else "Open file")
        name = node.name if len(node.name) <= 40 else node.name[:37] + "…"
        size = self._node_size(node)
        share = calculate_percentage(size, self._node_size(self.treemap_node))
        self._treemap_focus_info.configure(text=f"{kind}: {name} · {format_size(size)}")
        if self._treemap_detail_name is not None:
            metric = (self._filter_index.spec.metric
                      if self._has_active_filter() and self._filter_index is not None
                      else "logical")
            size_label = "Allocated size" if metric == "allocated" else "Logical size"
            count_label = "items" if getattr(node, "is_aggregate", False) else self._count_label()
            count_text = (f"{item_count:,} {count_label} · " if node.is_dir or
                          getattr(node, "is_aggregate", False) else "")
            self._treemap_detail_name.configure(text=node.name)
            self._treemap_detail_summary.configure(
                text=f"{kind}\n{size_label}: {format_size(size)}\n"
                     f"{count_text}Share of this map: {share:.1f}%")
            self._treemap_detail_location.configure(text=location)
            self._treemap_detail_action.configure(text=action, state="normal")

    def _update_treemap_details_layout(self, width: int):
        panel = self._treemap_detail_panel
        canvas = getattr(self, "treemap_canvas", None)
        if panel is None or canvas is None:
            return
        default_visible = self.active_view == "Explore" or width >= 1120
        visible = (default_visible if self._treemap_details_expanded is None
                   else self._treemap_details_expanded)
        is_visible = panel.winfo_manager() == "pack"
        if visible and not is_visible:
            panel.pack(side="right", fill="y", before=canvas)
        elif not visible and is_visible:
            panel.pack_forget()
        if self._treemap_details_toggle is not None:
            self._treemap_details_toggle.configure(
                text="Hide details" if visible else "Show details")

    def _toggle_treemap_details(self):
        panel = self._treemap_detail_panel
        workspace = self._treemap_workspace
        if panel is None or workspace is None:
            return
        visible = panel.winfo_manager() != "pack"
        self._treemap_details_expanded = visible
        self._update_treemap_details_layout(workspace.winfo_width())

    def _focused_treemap_path(self):
        tile = self._treemap_focus_tile
        if tile is None:
            return None
        node = tile.node
        return node.parent.path if getattr(node, "is_aggregate", False) else node.path

    def _treemap_reveal_focus(self):
        path = self._focused_treemap_path()
        if path:
            self._reveal(path)

    def _treemap_copy_focus_path(self):
        path = self._focused_treemap_path()
        if not path:
            return
        self.clipboard_clear()
        self.clipboard_append(path)
        self._set_status("Copied path to the clipboard")

    def _treemap_focus_in(self, _event=None):
        if self._tiles and self._treemap_focus_tile is None:
            tile = max(self._tiles, key=lambda item: (item.w * item.h, item.depth))
            self._treemap_set_focus(tile)

    def _treemap_set_focus(self, tile, sync_tree=False):
        self._treemap_focus_tile = tile
        self._hover_tile = tile
        self._draw_highlight(tile)
        self._announce_treemap_tile(tile)
        if sync_tree and tile is not None and self.active_view == "Explore":
            node = tile.node.parent if getattr(tile.node, "is_aggregate", False) else tile.node
            if node is not None:
                self._cross_selection_path = node.path
                self._cross_selection_node = node
                self._sync_explore_tree_selection(node)

    def _find_treemap_tile_for_node(self, node):
        if node is None:
            return None
        for tile in self._tiles:
            if tile.node is node:
                return tile

        # A tree row can be deeper than the currently visible treemap detail.
        # Prefer an aggregate for its immediate parent, then the nearest
        # visible ancestor; tree selection can zoom the map when neither is
        # the selected item itself.
        parent = node.parent
        while parent is not None:
            for tile in self._tiles:
                if (getattr(tile.node, "is_aggregate", False)
                        and tile.node.parent is parent):
                    return tile
            for tile in self._tiles:
                if tile.node is parent:
                    return tile
            parent = parent.parent
        return None

    def _show_explore_node_in_map(self, node):
        """Zoom the paired map to the selected row's containing folder."""
        if self.active_view != "Explore" or self.root_node is None or node is None:
            return
        # A folder that is already the treemap root is represented by the
        # map's contents, not by a tile. Its queued TreeviewSelect event must
        # not undo the drill-down that selected it.
        if self.treemap_stack and self.treemap_stack[-1] is node:
            return
        ancestors = []
        parent = node.parent
        while parent is not None and parent is not self.root_node:
            ancestors.append(parent)
            parent = parent.parent
        if parent is not self.root_node:
            return
        target_stack = list(reversed(ancestors))
        if target_stack == self.treemap_stack:
            return
        self.treemap_stack = target_stack
        self.treemap_forward_stack.clear()
        self._refresh_explore_map()

    def _sync_explore_tree_selection(self, node):
        """Reveal a map selection in the tree when the scanned row can be loaded."""
        tree = self.tree
        if self.active_view != "Explore" or tree is None or node is None:
            return
        if self.search_query:
            selected_iid = self._tree_iid_for_node(node)
            if selected_iid is not None:
                self._select_explore_tree_iid(selected_iid)
            return
        chain = []
        current = node
        while current is not None and current is not self.root_node:
            chain.append(current)
            current = current.parent
        if current is not self.root_node:
            return

        parent_node = self.root_node
        parent_iid = ""
        selected_iid = None
        for child_node in reversed(chain):
            child_iid = self._tree_iid_for_node(child_node)
            if child_iid is None:
                if parent_iid:
                    tree.item(parent_iid, open=True)
                children = tree.get_children(parent_iid)
                dummy = next((iid for iid in children if self._is_dummy(iid)), None)
                if dummy:
                    tree.delete(dummy)
                    self._insert_tree_children(parent_iid, parent_node)

                # The treemap is capped at 1,200 visible siblings. Load only
                # the pages needed to reach the chosen tile; very large sorts
                # continue asynchronously and resume from _tree_child_sort_ready.
                while child_iid is None:
                    if parent_node.path in self._tree_sort_loading_paths:
                        self._pending_explore_select_node = node
                        break
                    page_iid = next((iid for iid in tree.get_children(parent_iid)
                                     if "page" in tree.item(iid, "tags")), None)
                    if page_iid is None:
                        break
                    before = len(self.iid_to_node)
                    self._load_tree_page(page_iid)
                    child_iid = self._tree_iid_for_node(child_node)
                    if parent_node.path in self._tree_sort_loading_paths:
                        self._pending_explore_select_node = node
                        break
                    if child_iid is None and len(self.iid_to_node) == before:
                        break
                if child_iid is None:
                    if parent_node.path in self._tree_sort_loading_paths:
                        return
                    break

            selected_iid = child_iid
            tree.item(child_iid, open=True)
            parent_iid = child_iid
            parent_node = child_node

        if selected_iid is None or not tree.exists(selected_iid):
            return
        self._select_explore_tree_iid(selected_iid)

    def _tree_iid_for_node(self, node):
        iid = self._node_id_to_tree_iid.get(id(node))
        return iid if iid is not None and self.iid_to_node.get(iid) is node else None

    def _select_explore_tree_iid(self, iid):
        tree = self.tree
        if tree is None or not tree.exists(iid):
            return
        self._selection_sync_in_progress = True
        try:
            tree.selection_set(iid)
            tree.focus(iid)
            tree.see(iid)
        finally:
            self._selection_sync_in_progress = False

    def _treemap_move_focus(self, direction):
        if not self._tiles:
            return "break"
        current = self._treemap_focus_tile
        if current not in self._tiles:
            self._treemap_focus_in()
            return "break"
        cx, cy = current.x + current.w / 2, current.y + current.h / 2
        candidates = []
        for tile in self._tiles:
            if tile is current:
                continue
            tx, ty = tile.x + tile.w / 2, tile.y + tile.h / 2
            dx, dy = tx - cx, ty - cy
            if ((direction == "right" and dx > 0) or (direction == "left" and dx < 0)
                    or (direction == "down" and dy > 0) or (direction == "up" and dy < 0)):
                primary, secondary = ((abs(dx), abs(dy)) if direction in ("left", "right")
                                      else (abs(dy), abs(dx)))
                score = primary + secondary * 2 - tile.depth * 0.01
                candidates.append((score, tile))
        if not candidates:
            if direction in ("left", "right"):
                edge = min if direction == "right" else max
                target = edge(self._tiles, key=lambda tile: tile.x + tile.w / 2)
            else:
                edge = min if direction == "down" else max
                target = edge(self._tiles, key=lambda tile: tile.y + tile.h / 2)
        else:
            target = min(candidates, key=lambda item: item[0])[1]
        self._treemap_set_focus(target, sync_tree=True)
        return "break"

    def _treemap_activate_focus(self, _event=None):
        tile = self._treemap_focus_tile
        if tile is None and self._tiles:
            self._treemap_focus_in()
            tile = self._treemap_focus_tile
        if tile is None:
            return "break"
        self._treemap_set_focus(tile, sync_tree=True)
        if getattr(tile.node, "is_aggregate", False):
            self._show_treemap_aggregate(tile.node)
        elif tile.node.is_dir and self._node_count(tile.node) > 0:
            self._treemap_drill_to(tile.node)
        elif is_image_file(tile.node.name):
            self._open_image(tile.node.path)
        else:
            self._reveal(tile.node.path)
        return "break"

    def _treemap_thumb(self, path: str, size, mtime=None):
        return self.thumbnails.request(
            path, (min(size[0], 400), min(size[1], 400)), mtime=mtime)

    def _on_thumbnail_ready(self, path: str):
        """A worker decoded a thumbnail; fold it into the views that show one."""
        self.after(0, lambda: self._apply_thumbnail(path))

    def _apply_thumbnail(self, path: str):
        if self.active_view in ("Treemap", "Explore"):
            self._schedule_treemap_redraw()
            # the pointer may still be resting on this tile: fill the peek in
            # now rather than waiting for the next mouse move
            if self._peek_path == path and self._peek_args is not None:
                text, x, y = self._peek_args
                image = self.thumbnails.get(path, PEEK_SIZE, mtime=self._peek_mtime)
                if image is not None:
                    self.tooltip.show(text, x, y, self._colors(), image=image)
        elif self.settings.list_thumbnails:
            self._refresh_row_thumbnail(path)

    def _treemap_hover(self, event):
        tile = treemap_render.hit_test(self._tiles, event.x, event.y)
        if tile is None:
            self.tooltip.hide()
            self._peek_path = None
            self._peek_args = None
            self._peek_mtime = None
            if self._hover_tile is not None:
                self._hover_tile = None
                self._draw_highlight(None)
            return

        if tile is not self._hover_tile:
            self._treemap_set_focus(tile)

        n = tile.node
        if getattr(n, "is_aggregate", False):
            kind = f"Grouped {self._filter_label().lower()}"
        else:
            kind = "Folder" if n.is_dir else get_file_category(n.name, is_dir=False)['label']
        node_size = self._node_size(n)
        parent_size = self._node_size(self.treemap_node)
        share = calculate_percentage(node_size, parent_size)
        lines = [n.name, f"{format_size(node_size)} · {kind} · {share:.1f}%"]
        if n.is_dir:
            lines.append(f"{self._node_count(n):,} {self._count_label()} · click to zoom in")
        elif getattr(n, "is_aggregate", False):
            lines.append(f"{n.item_count:,} items represented here · click to browse")
        else:
            lines.append(os.path.dirname(n.path))

        # peek preview: show the picture itself, not just its name
        peek = None
        self._peek_path = None
        self._peek_args = None
        self._peek_mtime = None
        if (self.settings.peek_preview and not n.is_dir
                and not getattr(n, "is_aggregate", False)
                and is_image_file(n.name)):
            peek = self.thumbnails.request(n.path, PEEK_SIZE, mtime=self._node_mtime(n))
            lines.append("double-click to open")
            self._peek_path = n.path
            self._peek_mtime = self._node_mtime(n)

        text = "\n".join(lines)
        px, py = self.winfo_pointerx(), self.winfo_pointery()
        self._peek_args = (text, px, py)
        self.tooltip.show(text, px, py, self._colors(), image=peek)

    def _treemap_click(self, event):
        self.treemap_canvas.focus_set()
        tile = treemap_render.hit_test(self._tiles, event.x, event.y)
        if tile:
            self._treemap_set_focus(tile, sync_tree=True)
        if tile and getattr(tile.node, "is_aggregate", False):
            self._show_treemap_aggregate(tile.node)
            return
        if tile and tile.node.is_dir and self._node_count(tile.node) > 0:
            self._treemap_drill_to(tile.node)

    def _show_treemap_aggregate(self, aggregate):
        """Browse the omitted siblings without inserting them all into Tk."""
        colors = self._colors()
        window = tk.Toplevel(self)
        window.title(f"Smaller items · {aggregate.parent.name}")
        window.geometry("720x490")
        window.configure(bg=colors['tree_bg'])
        generation = self._scan_generation
        index = self._filter_index if self._has_active_filter() else None
        filter_generation = self._filter_generation
        projection_key = self._projection_key()
        get_size = index.size if index else lambda node: node.size
        cancelled_event = threading.Event()

        def cancelled():
            return (cancelled_event.is_set()
                    or generation != self._scan_generation
                    or filter_generation != self._filter_generation
                    or projection_key != self._projection_key())

        def on_destroy(event):
            if event.widget is window:
                cancelled_event.set()

        window.bind("<Destroy>", on_destroy, add="+")
        status = tk.Label(window, text="Preparing grouped items…", anchor="w",
                          bg=colors['tree_bg'], fg=colors['tree_fg'])
        status.pack(fill="x", padx=12, pady=(12, 4))
        frame = tk.Frame(window, bg=colors['tree_bg'])
        frame.pack(fill="both", expand=True, padx=12)
        rows = ttk.Treeview(frame, columns=("size", "type"),
                            style="FolderLens.Treeview", selectmode="browse")
        rows.heading("#0", text="Name")
        rows.heading("size", text="Logical size")
        rows.heading("type", text="Type")
        rows.column("#0", width=390)
        rows.column("size", width=110, anchor="e")
        rows.column("type", width=120)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=rows.yview)
        rows.configure(yscrollcommand=scroll.set)
        rows.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        button = ttk.Button(window, text="Load next 200", state="disabled")
        button.pack(pady=9)
        state = {"members": [], "offset": 0, "row_nodes": {}}

        def load_page():
            if cancelled():
                status.configure(text="The scan or view changed. Reopen this list from the current map.")
                button.configure(state="disabled")
                return
            members = state["members"]
            end = min(len(members), state["offset"] + 200)
            for node in members[state["offset"]:end]:
                kind = "Folder" if node.is_dir else get_file_category(node.name, is_dir=False)['label']
                iid = rows.insert("", "end", text=node.name,
                                  values=(format_size(get_size(node)), kind))
                state["row_nodes"][iid] = node
            state["offset"] = end
            status.configure(text=f"Showing {end:,} of {len(members):,} grouped entries · "
                                  "double-click a folder to zoom")
            button.configure(state="normal" if end < len(members) else "disabled")

        def open_folder(event):
            if cancelled():
                return
            node = state["row_nodes"].get(rows.focus())
            if node is not None and node.is_dir:
                window.destroy()
                self._treemap_drill_to(node)

        rows.bind("<Double-Button-1>", open_folder)
        rows.bind("<Return>", open_folder)
        button.configure(command=load_page)
        results = queue.SimpleQueue()

        def present():
            if not window.winfo_exists():
                return
            if cancelled():
                status.configure(text="The scan or view changed. Reopen this list from the current map.")
                button.configure(state="disabled")
                return
            try:
                members, error = results.get_nowait()
            except queue.Empty:
                window.after(25, present)
                return
            if error:
                status.configure(text=f"Could not list grouped items: {error}")
            else:
                state["members"] = members
                load_page()

        window.after(25, present)

        def worker():
            try:
                if index is not None:
                    get_children = lambda parent: index.children(
                        parent, should_cancel=cancelled)
                else:
                    get_children = lambda parent: parent.children
                members = analysis.aggregate_members(
                    aggregate, get_children, get_size, should_cancel=cancelled)
                error = None
            except Exception as exc:
                members, error = [], str(exc)
            if cancelled():
                return
            results.put((members, error))

        threading.Thread(target=worker, daemon=True).start()
        return window

    def _treemap_double_click(self, event):
        tile = treemap_render.hit_test(self._tiles, event.x, event.y)
        if (tile and not tile.node.is_dir
                and not getattr(tile.node, "is_aggregate", False)
                and is_image_file(tile.node.name)):
            self._open_image(tile.node.path)

    def _treemap_back(self):
        if self.treemap_stack:
            self.treemap_forward_stack.append(self.treemap_stack.pop())
            self._refresh_explore_map()

    def _treemap_forward(self):
        if not self.treemap_forward_stack:
            return
        node = self.treemap_forward_stack.pop()
        if self._has_active_filter() and self._node_count(node) == 0:
            self.treemap_forward_stack.clear()
            return
        self.treemap_stack.append(node)
        self._refresh_explore_map()

    def _treemap_drill_to(self, node: Node):
        self.treemap_forward_stack.clear()
        self.treemap_stack.append(node)
        self._hover_tile = None
        self._refresh_explore_map()

    def _refresh_explore_map(self):
        """Redraw only the map pane so its paired tree keeps its open rows."""
        host = self._explore_map_host
        if self.active_view != "Explore" or host is None:
            self._render_active_view()
            return
        self._invalidate_treemap_render()
        if self._treemap_redraw_after:
            self.after_cancel(self._treemap_redraw_after)
            self._treemap_redraw_after = None
        for child in host.winfo_children():
            child.destroy()
        self._treemap_focus_info = None
        self._treemap_workspace = None
        self._treemap_detail_panel = None
        self._treemap_detail_name = None
        self._treemap_detail_summary = None
        self._treemap_detail_location = None
        self._treemap_detail_action = None
        self._treemap_details_toggle = None
        self.treemap_canvas = None
        self._treemap_focus_tile = None
        self._tiles = []
        self._treemap_image = None
        self._render_treemap(parent=host)

    def _treemap_keyboard_back(self, _event=None):
        self._treemap_back()
        return "break"

    def _treemap_keyboard_forward(self, _event=None):
        self._treemap_forward()
        return "break"

    # --------------------------------------------------------------- search

    def _on_search_change(self):
        if self._search_after:
            self.after_cancel(self._search_after)
        self._search_after = self.after(250, self._apply_search)

    def _apply_search(self):
        self._search_after = None
        query = self.search_var.get().strip()
        if query == self.search_query:
            return
        self.search_query = query
        self._invalidate_duplicate_scan()
        self._invalidate_filter_index()
        if self.root_node is None:
            return
        if self._has_active_filter():
            self._start_filter_build()
        else:
            self._set_view_total()
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
        if self._tree_is_active() and self.tree is not None:
            return self.tree, self.iid_to_node
        if self.active_view == "Largest Files" and self.largest_tree is not None:
            return self.largest_tree, self.largest_map
        if self.active_view == "Duplicates" and self.dup_tree is not None:
            return self.dup_tree, self.dup_map
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

    def _copy_path(self):
        nodes = self._selected_nodes()
        if not nodes:
            return
        self.clipboard_clear()
        self.clipboard_append("\n".join(n.path for n in nodes))
        self._set_status(f"Copied {len(nodes)} path(s) to the clipboard")

    def _open_in_explorer(self):
        nodes = self._selected_nodes()
        if nodes:
            self._reveal(nodes[0].path)

    def _show_export_menu(self):
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Report as CSV…", command=self._export_csv)
        menu.add_command(label="Treemap as PNG…", command=self._export_treemap)
        try:
            x = self.winfo_pointerx()
            y = self.winfo_pointery()
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def _export_treemap(self):
        image = getattr(self, "_treemap_image", None)
        if image is None:
            messagebox.showinfo("Nothing to export",
                                "Open the Treemap view first.", parent=self)
            return
        target = filedialog.asksaveasfilename(
            defaultextension=".png", filetypes=[("PNG image", "*.png")],
            initialfile="folderlens-treemap.png", title="Save treemap image as")
        if not target:
            return
        try:
            image.save(target)
            self._set_status(f"Saved {os.path.basename(target)}")
        except Exception as exc:
            messagebox.showerror("Export failed", str(exc))

    def _export_csv(self):
        if not self.root_node:
            messagebox.showwarning("No data", "Scan a folder first.")
            return
        index = None
        search_query = ""
        if self._has_active_filter() or self.search_query:
            if not self._filter_ready_for_view():
                return
            visible_scope = []
            if self._has_active_filter():
                visible_scope.append(self._filter_label())
            indexed_search = (self._filter_index is not None and self.search_query and
                              (self.search_query.casefold().strip() == self._filter_index.spec.name or
                               self.search_query.casefold().strip() in self._filter_index.spec.name_terms))
            if self.search_query and not indexed_search:
                visible_scope.append(f"name contains {self.search_query!r}")
            description = " and ".join(visible_scope)
            choice = messagebox.askyesnocancel(
                "CSV scope", f"Export the current visible results ({description}) or the full scan?\n\n"
                "Yes: current visible results. No: all scanned files. Cancel: stop export.", parent=self)
            if choice is None:
                return
            index = self._filter_index if choice else None
            search_query = self.search_query if choice else ""
        save_path = filedialog.asksaveasfilename(defaultextension=".csv",
                                                 filetypes=[("CSV files", "*.csv")], title="Export report as")
        if not save_path:
            return
        root = self.root_node
        partial = bool(self.scan_errors)
        inaccessible_count = len(self.scan_errors)
        self._set_status("Exporting CSV…")

        def worker():
            try:
                rows = analysis.export_tree_csv(
                    root, save_path, filter_index=index, search_query=search_query, partial=partial,
                    inaccessible_count=inaccessible_count)
                self.after(0, lambda: (self._set_status(f"Exported {rows:,} rows"),
                                       messagebox.showinfo("Export complete", f"Wrote {rows:,} rows to:\n{save_path}")))
            except Exception as exc:
                msg = str(exc)
                self.after(0, lambda: messagebox.showerror("Export failed", msg))
        threading.Thread(target=worker, daemon=True).start()

    def _zip_selected(self):
        if self._action_running:
            self._set_status("Wait for the current file action to finish or stop.")
            return
        selection = self._top_level_selection()
        if not selection:
            messagebox.showwarning("No selection", self._no_selection_hint())
            return
        selected_nodes = [node for _, node in selection]
        has_folder = any(node.is_dir for node in selected_nodes)
        has_view_filter = self._has_active_filter() or bool(self.search_query)
        matching_only = False
        prompt = self._action_scope_prompt(selection, "ZIP")
        if has_folder and has_view_filter:
            if self._has_active_filter() and not self._filter_ready_for_view():
                return
            choice = messagebox.askyesnocancel(
                "Confirm ZIP scope",
                prompt + "\n\nYes: include every file in the selected folders.\n"
                "No: include only files matching the current view.\nCancel: do not create a ZIP.",
                parent=self)
            if choice is None:
                return
            matching_only = not choice
        elif not messagebox.askyesno("Confirm ZIP scope", prompt, parent=self):
            return
        save_path = filedialog.asksaveasfilename(defaultextension=".zip",
                                                 filetypes=[("ZIP files", "*.zip")], title="Save ZIP as")
        if not save_path:
            return
        zip_path = os.path.normcase(os.path.abspath(save_path))
        for node in selected_nodes:
            selected_path = os.path.normcase(os.path.abspath(node.path))
            try:
                inside = (selected_path == zip_path or node.is_dir and
                          os.path.commonpath([selected_path, zip_path]) == selected_path)
            except ValueError:  # different Windows drives
                inside = False
            if inside:
                messagebox.showerror("ZIP location", "Save the ZIP outside the selected files and folders.", parent=self)
                return
        overwrite = os.path.exists(save_path)
        if overwrite and not messagebox.askyesno(
                "Replace ZIP?", f"Replace the existing file?\n{save_path}", parent=self):
            return
        started = self._begin_file_action("Checking selected files before creating ZIP…")
        if started is None:
            return
        generation, cancel_event = started
        filter_index = self._filter_index if self._has_active_filter() else None
        search_query = self.search_query

        def post(callback):
            try:
                self.after(0, callback)
            except tk.TclError:
                pass

        def worker():
            try:
                zip_nodes = selected_nodes
                if matching_only:
                    check = file_actions.validate_selection(
                        selected_nodes, cancel_event=cancel_event,
                        on_progress=lambda done, total: post(lambda text=(
                            f"Verifying selection… {done:,} entries checked"): self._action_progress(
                                generation, text)))
                    if not check.valid:
                        raise ValueError("The selection changed since the scan. Rescan it before creating a ZIP.\n"
                                         + "\n".join(check.issues[:5]))
                    zip_nodes = []
                    for selected_node in selected_nodes:
                        stack = [selected_node]
                        while stack:
                            if cancel_event.is_set():
                                raise file_actions.ActionCancelled()
                            node = stack.pop()
                            if node.is_dir:
                                stack.extend(reversed(node.children))
                            elif ((filter_index is None or filter_index.matches(node))
                                  and analysis.match_query(node.name, search_query)):
                                zip_nodes.append(node)
                    if not zip_nodes:
                        raise ValueError("No files match the current view inside the selected folders.")
                result = file_actions.create_zip(
                    zip_nodes, save_path, cancel_event=cancel_event,
                    overwrite=overwrite,
                    on_progress=lambda done, total: post(lambda text=(
                        f"Creating ZIP… {done:,}/{total:,} files"): self._action_progress(
                            generation, text)))
                post(lambda: self._zip_action_done(generation, result, None))
            except file_actions.ActionCancelled:
                post(lambda: self._zip_action_done(
                    generation, file_actions.ZipResult(save_path, 0, cancelled=True), None))
            except Exception as exc:
                message = str(exc)
                post(lambda: self._zip_action_done(generation, None, message))
        threading.Thread(target=worker, daemon=True).start()

    def _zip_action_done(self, generation, result, error):
        if generation != self._action_generation:
            return
        if error:
            self._finish_file_action(generation, "ZIP failed")
            messagebox.showwarning("ZIP stopped", error, parent=self)
        elif result.cancelled:
            self._finish_file_action(generation, "ZIP cancelled")
            messagebox.showinfo(
                "ZIP cancelled",
                f"Stopped after {result.files_written:,} file(s). The incomplete ZIP was discarded; "
                "an existing destination was left unchanged.", parent=self)
        elif result.errors:
            self._finish_file_action(generation, "ZIP incomplete")
            details = "\n".join(result.errors[:5])
            messagebox.showwarning(
                "Incomplete ZIP",
                f"Created: {result.path}\nIncluded {result.files_written:,} file(s); "
                f"{len(result.errors):,} item(s) were skipped.\n\n{details}", parent=self)
        else:
            self._finish_file_action(generation, "ZIP created")
            messagebox.showinfo("ZIP created", f"Created: {result.path}\nIncluded {result.files_written:,} file(s).",
                                parent=self)

    def _no_selection_hint(self) -> str:
        if self.active_view in ("Explore", "Tree", "Largest Files", "Duplicates"):
            return "Select files or folders first."
        return "Switch to Explore, Tree, Largest Files or Duplicates to select items."

    def _action_scope_prompt(self, selection: List[tuple], action: str) -> str:
        """A filtered folder row represents all files on disk for ZIP/delete."""
        total = sum(node.size for _, node in selection)
        items = sum(1 + node.item_count for _, node in selection)
        folders = any(node.is_dir for _, node in selection)
        if self.scan_errors:
            scope = (f"{len(selection)} selected item(s); {items:,} known scanned items; "
                     f"{format_size(total)} known scanned size (partial scan).")
        else:
            scope = (f"{len(selection)} selected item(s); {items:,} scanned items; "
                     f"{format_size(total)} total scanned size.")
        if folders and self._has_active_filter():
            scope += (f"\n\nThe current {self._filter_label()} view only changes which rows appear. "
                      f"{action} will include ALL file types inside selected folders, "
                      "including files hidden by this view.")
        if self.scan_errors:
            scope += ("\n\nThe scan reported inaccessible items. The selected files and folders "
                      "will be checked again on disk; the action stops if the selection cannot be verified.")
        scope += "\n\nThese are last-scan totals. The selection is rechecked before the action starts."
        if action == "ZIP":
            return f"Create a ZIP from the entire selected files and folders?\n{scope}"
        return scope

    def _delete_selected(self):
        if self._action_running:
            self._set_status("Wait for the current file action to finish or stop.")
            return
        selection = self._top_level_selection()
        if not selection:
            messagebox.showwarning("No selection", self._no_selection_hint())
            return
        scope = self._action_scope_prompt(selection, "Delete")
        recycle = self.settings.use_recycle_bin and trash.is_supported()
        if recycle:
            prompt = f"Move the selected files and folders to the Recycle Bin?\n{scope}\nYou can restore them from there."
        else:
            prompt = f"Delete the selected files and folders?\n{scope}\nThis cannot be undone."
        if not messagebox.askyesno("Confirm delete", prompt):
            return
        started = self._begin_file_action("Verifying selected files before deleting…")
        if started is None:
            return
        generation, cancel_event = started

        # remember which view started this so the async result never touches a
        # widget the user has since navigated away from
        tree, mapping = self._selection_context()
        action_root = self.root_node

        def post(callback):
            try:
                self.after(0, callback)
            except tk.TclError:
                pass

        def worker():
            deleted, errors = [], []
            cancelled = False
            nodes = [node for _, node in selection]
            try:
                checked = file_actions.validate_selection(
                    nodes, cancel_event=cancel_event, reject_reparse=True,
                    on_progress=lambda done, total: post(lambda text=(
                        f"Verifying selection… {done:,}/{total:,} entries"): self._action_progress(
                            generation, text)))
            except file_actions.ActionCancelled:
                checked = None
                cancelled = True
            if checked is not None and not checked.valid:
                errors.append("The selection changed or includes an unsafe reparse point. "
                              "Rescan before deleting.\n" + "\n".join(checked.issues[:5]))
            for position, (iid, node) in enumerate(selection, 1):
                if cancelled or checked is None or not checked.valid:
                    break
                if cancel_event.is_set():
                    cancelled = True
                    break
                try:
                    action_label = "Recycling" if recycle else "Deleting"
                    progress_text = f"{action_label} {position:,}/{len(selection):,}: {node.name}"
                    post(lambda text=progress_text: self._action_progress(generation, text))
                    file_actions.remove_selected(
                        node, recycle=recycle, cancel_event=cancel_event,
                        prevalidated=True)
                    deleted.append((iid, node))
                except file_actions.ActionCancelled:
                    cancelled = True
                    break
                except Exception as e:
                    errors.append(f"{node.name}: {e}")
                if not cancel_event.is_set():
                    progress_text = f"Processed {position:,}/{len(selection):,} selected items"
                    post(lambda text=progress_text: self._action_progress(generation, text))
            if cancel_event.is_set():
                cancelled = True
            post(lambda: self._delete_action_done(
                generation, deleted, errors, tree, mapping, action_root,
                cancelled, len(selection)))
        threading.Thread(target=worker, daemon=True).start()

    def _delete_action_done(self, generation, deleted, errors, tree, mapping,
                            action_root, cancelled, requested):
        if generation != self._action_generation:
            return
        self._apply_deletions(deleted, errors, tree, mapping, action_root)
        status = None
        if cancelled:
            status = f"Delete stopped · removed {len(deleted):,} of {requested:,} selected item(s)"
        self._finish_file_action(generation, status)

    def _apply_deletions(self, deleted, errors, tree=None, mapping=None, action_root=None):
        if action_root is not None and action_root is not self.root_node:
            return
        if deleted:
            self._invalidate_treemap_render()
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
            if deleted:
                self.treemap_stack = []
                self.treemap_forward_stack = []
                self._invalidate_filter_index()
                self._invalidate_duplicate_scan()
                # Delete mutates the completed tree in place. Projections
                # cached for this root no longer describe its children.
                self._query_engine = None
            self._set_view_total()
            status = f"{self._node_count(self.root_node):,} {self._count_label()}"
            if deleted:
                status = f"Deleted {len(deleted)} item(s) · " + status
            self._set_status(status)
            if deleted:
                if self._has_active_filter():
                    self._start_filter_build()
                else:
                    self._render_active_view()
        if errors:
            messagebox.showerror("Errors", "\n".join(errors[:5]))

    def _on_close(self):
        """Stop background work and release thumbnail workers on exit."""
        self.scanner.cancel()
        self._dup_cancel = True
        if self._action_cancel_event is not None:
            self._action_cancel_event.set()
        self._invalidate_treemap_render()
        self._treemap_executor.shutdown(wait=False, cancel_futures=True)
        self.thumbnails.close()
        self.destroy()

    # --------------------------------------------------------------- misc

    def _toggle_theme(self):
        self.settings.dark_mode = not self.settings.dark_mode
        ctk.set_appearance_mode("dark" if self.settings.dark_mode else "light")
        self.theme_btn.configure(text=ICONS['sun'] if self.settings.dark_mode else ICONS['moon'])
        self.settings.save()
        self._render_active_view()

    SHORTCUTS = (
        ("Getting around", (
            ("F5", "Rescan the current folder"),
            ("Backspace", "Go to the parent folder"),
            ("Ctrl+O", "Browse for a folder"),
            ("Double-click", "Open a folder, or an image in the viewer"),
        )),
        ("Finding things", (
            ("Ctrl+F", "Jump to search"),
            ("Esc", "Clear the search"),
            ("Click a column", "Sort by it; click again to reverse"),
        )),
        ("Acting on files", (
            ("Ctrl/Shift+click", "Select several items"),
            ("Delete", "Send the selection to the Recycle Bin"),
            ("Right-click", "Open in Explorer, copy path, zip, delete"),
        )),
        ("Treemap", (
            ("Hover", "Peek at a file, with a preview for images"),
            ("Click", "Zoom into a folder"),
            ("Right-click", "Zoom back out"),
            ("Shift+Backspace", "Move forward in zoom history"),
        )),
    )

    def _show_shortcuts(self):
        window = ctk.CTkToplevel(self)
        window.title("Keyboard shortcuts")
        window.geometry("460x520")
        window.transient(self)
        window.grab_set()
        self.update_idletasks()
        window.geometry(f"+{self.winfo_x() + 160}+{max(self.winfo_y() + 60, 0)}")

        ctk.CTkButton(window, text="Got it", height=34,
                      command=window.destroy).pack(side="bottom", pady=(0, 14))

        body = ctk.CTkScrollableFrame(window, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=16)

        for section, rows in self.SHORTCUTS:
            ctk.CTkLabel(body, text=section, font=ctk.CTkFont(size=13, weight="bold"),
                         anchor="w").pack(fill="x", pady=(10, 4))
            for keys, description in rows:
                row = ctk.CTkFrame(body, fg_color="transparent")
                row.pack(fill="x", pady=1)
                ctk.CTkLabel(row, text=keys, width=118, anchor="w",
                             font=ctk.CTkFont(size=11, weight="bold"),
                             text_color=ACCENT).pack(side="left")
                ctk.CTkLabel(row, text=description, anchor="w",
                             font=ctk.CTkFont(size=11),
                             text_color=("gray25", "gray75")).pack(side="left", fill="x", expand=True)

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
