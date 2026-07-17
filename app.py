import customtkinter as ctk
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
import tkinter.font as tkfont
from typing import Optional, List, Callable, Dict, Set
import json
import os
import sys
import subprocess
import threading
import shutil
import zipfile
from PIL import Image, ImageTk

from file_utils import (
    get_file_category, format_size, format_date,
    calculate_percentage, get_file_icon, is_image_file, ICONS
)
from scanner import TreeScanner, Node
from version import VERSION
from updater import get_updater, UpdateInfo


ctk.set_default_color_theme("blue")


def _settings_file() -> str:
    base = os.environ.get('APPDATA') or os.path.join(os.path.expanduser('~'), '.config')
    return os.path.join(base, 'FolderLens', 'settings.json')


class AppSettings:
    def __init__(self):
        self.icon_size = "medium"
        self.preview_enabled = True
        self.dark_mode = True
        self.load()

    def get_row_height(self) -> int:
        heights = {"small": 24, "medium": 30, "large": 38}
        return heights.get(self.icon_size, 30)

    def get_font_size(self) -> int:
        sizes = {"small": 10, "medium": 11, "large": 13}
        return sizes.get(self.icon_size, 11)

    def load(self):
        try:
            with open(_settings_file(), 'r', encoding='utf-8') as f:
                data = json.load(f)
            if data.get('icon_size') in ("small", "medium", "large"):
                self.icon_size = data['icon_size']
            if isinstance(data.get('preview_enabled'), bool):
                self.preview_enabled = data['preview_enabled']
            if isinstance(data.get('dark_mode'), bool):
                self.dark_mode = data['dark_mode']
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            path = _settings_file()
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({
                    'icon_size': self.icon_size,
                    'preview_enabled': self.preview_enabled,
                    'dark_mode': self.dark_mode,
                }, f, indent=2)
        except OSError:
            pass


DARK = {
    'tree_bg': '#1b1b1b',
    'tree_fg': '#e6e6e6',
    'tree_sel_bg': '#1d4ed8',
    'tree_sel_fg': '#ffffff',
    'heading_bg': '#262626',
    'heading_fg': '#bdbdbd',
    'folder_fg': '#93c5fd',
    'error_fg': '#f87171',
    'muted_fg': '#8a8a8a',
}

LIGHT = {
    'tree_bg': '#ffffff',
    'tree_fg': '#1f2937',
    'tree_sel_bg': '#bfdbfe',
    'tree_sel_fg': '#111827',
    'heading_bg': '#f3f4f6',
    'heading_fg': '#4b5563',
    'folder_fg': '#1d4ed8',
    'error_fg': '#dc2626',
    'muted_fg': '#9ca3af',
}


class SettingsMenu(ctk.CTkToplevel):
    """Settings popup"""

    def __init__(self, master, settings: AppSettings, on_apply: Callable, **kwargs):
        super().__init__(master, **kwargs)

        self.settings = settings
        self.on_apply = on_apply

        self.title("Settings")
        self.geometry("300x250")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.update_idletasks()
        x = master.winfo_x() + (master.winfo_width() - 300) // 2
        y = master.winfo_y() + (master.winfo_height() - 250) // 2
        self.geometry(f"+{x}+{y}")

        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=20, pady=20)

        ctk.CTkLabel(main, text="Row Size", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")

        self.size_var = ctk.StringVar(value=self.settings.icon_size)
        size_frame = ctk.CTkFrame(main, fg_color="transparent")
        size_frame.pack(fill="x", pady=(5, 15))

        for size in ["small", "medium", "large"]:
            ctk.CTkRadioButton(
                size_frame,
                text=size.capitalize(),
                variable=self.size_var,
                value=size
            ).pack(side="left", padx=10)

        ctk.CTkLabel(main, text="Image Preview", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w", pady=(10, 0))

        self.preview_var = ctk.BooleanVar(value=self.settings.preview_enabled)
        ctk.CTkSwitch(
            main,
            text="Enable image preview on double-click",
            variable=self.preview_var
        ).pack(anchor="w", pady=10)

        ctk.CTkButton(main, text="Apply", command=self._apply).pack(pady=20)

    def _apply(self):
        self.settings.icon_size = self.size_var.get()
        self.settings.preview_enabled = self.preview_var.get()
        self.on_apply()
        self.destroy()


class UpdateDialog(ctk.CTkToplevel):
    """Dialog for checking and applying updates"""

    def __init__(self, master, **kwargs):
        super().__init__(master, **kwargs)

        self.updater = get_updater()
        self.update_info = None
        self.downloaded_file = None

        self.title("Updates")
        self.geometry("450x350")
        self.resizable(False, False)
        self.transient(master)
        self.grab_set()

        self.update_idletasks()
        x = master.winfo_x() + (master.winfo_width() - 450) // 2
        y = master.winfo_y() + (master.winfo_height() - 350) // 2
        self.geometry(f"+{x}+{y}")

        self._create_ui()
        self._check_for_updates()

    def _create_ui(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=24, pady=24)

        header = ctk.CTkFrame(main, fg_color="transparent")
        header.pack(fill="x")

        ctk.CTkLabel(
            header,
            text="🔄 Check for Updates",
            font=ctk.CTkFont(size=18, weight="bold")
        ).pack(anchor="w")

        ctk.CTkLabel(
            header,
            text=f"Current version: {VERSION}",
            font=ctk.CTkFont(size=12),
            text_color="gray"
        ).pack(anchor="w", pady=(4, 0))

        self.status_frame = ctk.CTkFrame(main, fg_color=("gray90", "gray20"), corner_radius=8)
        self.status_frame.pack(fill="both", expand=True, pady=20)

        self.status_label = ctk.CTkLabel(
            self.status_frame,
            text="Checking for updates...",
            font=ctk.CTkFont(size=14)
        )
        self.status_label.pack(expand=True)

        self.progress = ctk.CTkProgressBar(self.status_frame, width=300)
        self.progress.set(0)

        self.notes_text = ctk.CTkTextbox(
            self.status_frame,
            height=120,
            font=ctk.CTkFont(size=11)
        )

        self.button_frame = ctk.CTkFrame(main, fg_color="transparent")
        self.button_frame.pack(fill="x")

        self.action_btn = ctk.CTkButton(
            self.button_frame,
            text="Check Again",
            command=self._check_for_updates,
            state="disabled"
        )
        self.action_btn.pack(side="left")

        ctk.CTkButton(
            self.button_frame,
            text="Close",
            fg_color="transparent",
            border_width=1,
            text_color=("gray20", "gray80"),
            command=self.destroy
        ).pack(side="right")

    def _check_for_updates(self):
        self.status_label.configure(text="Checking for updates...")
        self.action_btn.configure(state="disabled")
        self.progress.pack_forget()
        self.notes_text.pack_forget()

        self.updater.check_for_updates_async(self._on_check_complete)

    def _on_check_complete(self, available: bool, info: UpdateInfo, error: str):
        self.after(0, lambda: self._handle_check_result(available, info, error))

    def _handle_check_result(self, available: bool, info: UpdateInfo, error: str):
        if error:
            self.status_label.configure(text=f"❌ {error}")
            self.action_btn.configure(text="Check Again", command=self._check_for_updates, state="normal")
        elif available and info:
            self.update_info = info
            self.status_label.configure(text=f"✅ New version available: {info.version}")
            self.status_label.pack(pady=(16, 8))

            self.notes_text.delete("1.0", "end")
            self.notes_text.insert("1.0", info.release_notes[:500])
            self.notes_text.pack(fill="both", expand=True, padx=16, pady=(0, 16))

            self.action_btn.configure(text="Download & Install", command=self._download_update, state="normal")
        else:
            self.status_label.configure(text="✅ You're running the latest version!")
            self.action_btn.configure(text="Check Again", command=self._check_for_updates, state="normal")

    def _download_update(self):
        if not self.update_info:
            return

        self.status_label.configure(text="Downloading update...")
        self.notes_text.pack_forget()
        self.progress.set(0)
        self.progress.pack(pady=16)
        self.action_btn.configure(state="disabled")

        self.updater.download_update_async(
            self.update_info,
            progress_callback=self._on_progress,
            complete_callback=self._on_download_complete
        )

    def _on_progress(self, downloaded: int, total: int):
        if total > 0:
            progress = downloaded / total
            self.after(0, lambda: self.progress.set(progress))

    def _on_download_complete(self, success: bool, file_path: str, error: str):
        self.after(0, lambda: self._handle_download_result(success, file_path, error))

    def _handle_download_result(self, success: bool, file_path: str, error: str):
        if success and file_path:
            self.downloaded_file = file_path
            self.status_label.configure(text="Download complete! Ready to install.")
            self.progress.set(1)
            self.action_btn.configure(text="Install & Restart", command=self._apply_update, state="normal")
        else:
            self.status_label.configure(text=f"❌ Download failed: {error}")
            self.progress.pack_forget()
            self.action_btn.configure(text="Try Again", command=self._download_update, state="normal")

    def _apply_update(self):
        if not self.downloaded_file:
            return

        success, error = self.updater.apply_update(self.downloaded_file)

        if success:
            self.status_label.configure(text="Installing update... The app will restart.")
            self.action_btn.configure(state="disabled")
            self.after(1500, lambda: self.master.destroy())
        else:
            messagebox.showerror("Update Failed", error or "Could not apply update.")


class ImagePreview(ctk.CTkToplevel):
    """Image preview window"""

    def __init__(self, master, image_path: str, **kwargs):
        super().__init__(master, **kwargs)

        self.title(os.path.basename(image_path))
        self.geometry("800x600")
        self.transient(master)

        try:
            img = Image.open(image_path)
            img.thumbnail((780, 560), Image.Resampling.LANCZOS)
            self.photo = ImageTk.PhotoImage(img)

            label = ctk.CTkLabel(self, image=self.photo, text="")
            label.pack(expand=True, fill="both", padx=10, pady=10)
        except Exception as e:
            ctk.CTkLabel(self, text=f"Cannot load image: {e}").pack(expand=True)


class FolderLensApp(ctk.CTk):
    """Main application window: a fast, tree-based folder size explorer."""

    BAR_WIDTH = 10

    SORT_COLUMNS = {
        "#0": "name",
        "size": "size",
        "usage": "size",
        "items": "size",
        "type": "type",
        "modified": "date",
    }

    def __init__(self, initial_path: Optional[str] = None):
        super().__init__()

        self.title(f"FolderLens {VERSION}")
        self.geometry("1200x800")
        self.minsize(900, 600)

        self.settings = AppSettings()
        ctk.set_appearance_mode("dark" if self.settings.dark_mode else "light")

        self.scanner = TreeScanner()
        self.root_node: Optional[Node] = None
        self.scan_errors: List[str] = []
        self.iid_to_node: Dict[str, Node] = {}
        self.sort_key = "size"
        self.sort_reverse = True

        self._create_toolbar()
        self._create_tree()
        self._create_status_bar()
        self._apply_tree_style()

        self.bind("<F5>", lambda e: self._refresh())
        self.tree.bind("<Delete>", lambda e: self._delete_selected())

        if initial_path and os.path.isdir(initial_path):
            self.after(100, lambda: self.scan_folder(os.path.abspath(initial_path)))
        else:
            self._set_status("Select a folder to analyze")

    # ------------------------------------------------------------------ UI

    def _create_toolbar(self):
        bar = ctk.CTkFrame(self, fg_color=("gray95", "gray14"), corner_radius=0, height=48)
        bar.pack(fill="x")
        bar.pack_propagate(False)

        ctk.CTkButton(
            bar, text=f"{ICONS['folder_open']} Browse", width=100, height=32,
            font=ctk.CTkFont(size=12), command=self._browse_folder
        ).pack(side="left", padx=(12, 4), pady=8)

        ctk.CTkButton(
            bar, text="⬅ Up", width=64, height=32,
            font=ctk.CTkFont(size=12),
            fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"),
            command=self._go_up
        ).pack(side="left", padx=4, pady=8)

        ctk.CTkButton(
            bar, text=f"{ICONS['refresh']} Refresh", width=90, height=32,
            font=ctk.CTkFont(size=12),
            fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"),
            command=self._refresh
        ).pack(side="left", padx=4, pady=8)

        self.path_label = ctk.CTkLabel(
            bar, text="", font=ctk.CTkFont(size=12),
            text_color=("gray30", "gray70"), anchor="w"
        )
        self.path_label.pack(side="left", padx=16, fill="x", expand=True)

        ctk.CTkButton(
            bar, text="•••", width=40, height=32,
            font=ctk.CTkFont(size=14), fg_color="transparent",
            text_color=("gray30", "gray70"),
            hover_color=("gray85", "gray25"),
            command=self._show_settings
        ).pack(side="right", padx=(4, 12), pady=8)

        ctk.CTkButton(
            bar, text="⬆", width=40, height=32,
            font=ctk.CTkFont(size=14), fg_color="transparent",
            text_color=("gray30", "gray70"),
            hover_color=("gray85", "gray25"),
            command=lambda: UpdateDialog(self)
        ).pack(side="right", padx=4, pady=8)

        self.theme_btn = ctk.CTkButton(
            bar, text=ICONS['sun'] if self.settings.dark_mode else ICONS['moon'],
            width=40, height=32,
            font=ctk.CTkFont(size=14), fg_color="transparent",
            text_color=("gray30", "gray70"),
            hover_color=("gray85", "gray25"),
            command=self._toggle_theme
        )
        self.theme_btn.pack(side="right", padx=4, pady=8)

        ctk.CTkButton(
            bar, text=f"{ICONS['delete']} Delete", width=90, height=32,
            font=ctk.CTkFont(size=12),
            fg_color="#dc2626", hover_color="#b91c1c",
            command=self._delete_selected
        ).pack(side="right", padx=4, pady=8)

        ctk.CTkButton(
            bar, text=f"{ICONS['zip']} Zip", width=76, height=32,
            font=ctk.CTkFont(size=12),
            fg_color="transparent", border_width=1,
            text_color=("gray20", "gray80"),
            command=self._zip_selected
        ).pack(side="right", padx=4, pady=8)

    def _create_tree(self):
        container = tk.Frame(self, highlightthickness=0, bd=0)
        container.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(
            container,
            columns=("usage", "size", "items", "type", "modified"),
            selectmode="extended",
            style="FolderLens.Treeview"
        )

        self.tree.heading("#0", text="Name", anchor="w",
                          command=lambda: self._on_heading_click("#0"))
        self.tree.heading("usage", text="Usage",
                          command=lambda: self._on_heading_click("usage"))
        self.tree.heading("size", text="Size",
                          command=lambda: self._on_heading_click("size"))
        self.tree.heading("items", text="Items",
                          command=lambda: self._on_heading_click("items"))
        self.tree.heading("type", text="Type",
                          command=lambda: self._on_heading_click("type"))
        self.tree.heading("modified", text="Created",
                          command=lambda: self._on_heading_click("modified"))

        self.tree.column("#0", width=420, minwidth=220, stretch=True)
        self.tree.column("usage", width=170, minwidth=140, stretch=False, anchor="w")
        self.tree.column("size", width=100, minwidth=80, stretch=False, anchor="e")
        self.tree.column("items", width=80, minwidth=60, stretch=False, anchor="e")
        self.tree.column("type", width=100, minwidth=80, stretch=False, anchor="w")
        self.tree.column("modified", width=130, minwidth=110, stretch=False, anchor="w")

        vsb = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)

        vsb.pack(side="right", fill="y")
        self.tree.pack(side="left", fill="both", expand=True)

        self.tree.bind("<<TreeviewOpen>>", self._on_open)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Double-Button-1>", self._on_double_click)
        self.tree.bind("<Button-3>", self._on_right_click)

        self.menu = tk.Menu(self, tearoff=0)
        self.menu.add_command(label="Open in Explorer", command=self._open_in_explorer)
        self.menu.add_command(label="Zip selected", command=self._zip_selected)
        self.menu.add_separator()
        self.menu.add_command(label="Delete selected", command=self._delete_selected)

    def _create_status_bar(self):
        bar = ctk.CTkFrame(self, fg_color=("gray95", "gray14"), corner_radius=0, height=28)
        bar.pack(fill="x", side="bottom")
        bar.pack_propagate(False)

        self.status_left = ctk.CTkLabel(
            bar, text="Ready", font=ctk.CTkFont(size=11), text_color="gray"
        )
        self.status_left.pack(side="left", padx=12)

        self.status_right = ctk.CTkLabel(
            bar, text="", font=ctk.CTkFont(size=11), text_color="gray"
        )
        self.status_right.pack(side="right", padx=12)

    def _apply_tree_style(self):
        colors = DARK if self.settings.dark_mode else LIGHT
        font_size = self.settings.get_font_size()

        style = ttk.Style(self)
        style.theme_use("clam")

        style.configure(
            "FolderLens.Treeview",
            background=colors['tree_bg'],
            fieldbackground=colors['tree_bg'],
            foreground=colors['tree_fg'],
            rowheight=self.settings.get_row_height(),
            borderwidth=0,
            font=("Segoe UI", font_size)
        )
        style.map(
            "FolderLens.Treeview",
            background=[("selected", colors['tree_sel_bg'])],
            foreground=[("selected", colors['tree_sel_fg'])]
        )
        style.configure(
            "FolderLens.Treeview.Heading",
            background=colors['heading_bg'],
            foreground=colors['heading_fg'],
            borderwidth=0,
            font=("Segoe UI", font_size - 1, "bold")
        )
        style.map("FolderLens.Treeview.Heading",
                  background=[("active", colors['heading_bg'])])

        bold = tkfont.Font(family="Segoe UI", size=font_size, weight="bold")
        self.tree.tag_configure("folder", foreground=colors['folder_fg'], font=bold)
        self.tree.tag_configure("error", foreground=colors['error_fg'])
        self.tree.tag_configure("muted", foreground=colors['muted_fg'])

    # ------------------------------------------------------------- scanning

    def scan_folder(self, path: str):
        self.path_label.configure(text=path)
        self._set_status(f"Scanning {path} ...")
        self.tree.delete(*self.tree.get_children())
        self.iid_to_node.clear()

        self.scanner.scan(
            path,
            on_progress=lambda n: self.after(0, lambda: self._set_status(f"Scanning... {n:,} items")),
            on_complete=lambda root, errors, t: self.after(0, lambda: self._on_scan_complete(root, errors, t)),
            on_error=lambda msg: self.after(0, lambda: self._on_scan_error(msg))
        )

    def _on_scan_complete(self, root: Node, errors: List[str], scan_time: float):
        self.root_node = root
        self.scan_errors = errors

        self.tree.delete(*self.tree.get_children())
        self.iid_to_node.clear()
        self._insert_children("", root)

        status = f"{root.item_count:,} items in {scan_time:.1f}s"
        if errors:
            status += f"  |  ⚠ {len(errors)} inaccessible"
        self._set_status(status)
        self.status_right.configure(text=f"Total: {format_size(root.size)}")

    def _on_scan_error(self, message: str):
        self._set_status("Scan failed")
        messagebox.showerror("Error", message)

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

    # ------------------------------------------------------------ tree fill

    def _values_for(self, node: Node, parent: Node) -> tuple:
        pct = calculate_percentage(node.size, parent.size) if parent else 0.0
        filled = round(pct / 100 * self.BAR_WIDTH)
        bar = "█" * filled + "░" * (self.BAR_WIDTH - filled)
        usage = f"{bar} {pct:4.1f}%"

        if node.is_dir:
            type_label = "Folder"
            items = f"{node.item_count:,}"
        else:
            type_label = get_file_category(node.path)['label']
            items = ""

        return (usage, format_size(node.size), items, type_label, format_date(node.creation_date))

    def _insert_children(self, parent_iid: str, parent_node: Node):
        children = parent_node.sorted_children(self.sort_key, self.sort_reverse)
        for child in children:
            icon = ICONS['folder'] if child.is_dir else get_file_icon(child.path)
            tags = []
            if child.is_dir:
                tags.append("folder")
            if child.error:
                tags.append("error")

            iid = self.tree.insert(
                parent_iid, "end",
                text=f"{icon} {child.name}",
                values=self._values_for(child, parent_node),
                tags=tuple(tags)
            )
            self.iid_to_node[iid] = child

            if child.is_dir and child.children:
                # lazy: real children are inserted when the node is expanded
                self.tree.insert(iid, "end", text="…", tags=("dummy", "muted"))

    def _is_dummy(self, iid: str) -> bool:
        return "dummy" in self.tree.item(iid, "tags")

    def _on_open(self, event):
        iid = self.tree.focus()
        if not iid:
            return
        children = self.tree.get_children(iid)
        if len(children) == 1 and self._is_dummy(children[0]):
            self.tree.delete(children[0])
            node = self.iid_to_node.get(iid)
            if node:
                self._insert_children(iid, node)

    # -------------------------------------------------------------- sorting

    def _on_heading_click(self, column: str):
        key = self.SORT_COLUMNS.get(column, "size")
        if key == self.sort_key:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_key = key
            self.sort_reverse = key != "name"
        self._rebuild_tree()

    def _rebuild_tree(self):
        if not self.root_node:
            return

        expanded: Set[str] = set()

        def collect(iid):
            for child in self.tree.get_children(iid):
                node = self.iid_to_node.get(child)
                if node and self.tree.item(child, "open"):
                    expanded.add(node.path)
                collect(child)

        collect("")

        self.tree.delete(*self.tree.get_children())
        self.iid_to_node.clear()
        self._insert_children("", self.root_node)

        def reexpand(iid):
            for child in self.tree.get_children(iid):
                node = self.iid_to_node.get(child)
                if node and node.path in expanded:
                    dummies = self.tree.get_children(child)
                    if len(dummies) == 1 and self._is_dummy(dummies[0]):
                        self.tree.delete(dummies[0])
                        self._insert_children(child, node)
                    self.tree.item(child, open=True)
                    reexpand(child)

        reexpand("")

    # ------------------------------------------------------------ selection

    def _selected_nodes(self) -> List[Node]:
        return [self.iid_to_node[iid] for iid in self.tree.selection() if iid in self.iid_to_node]

    def _top_level_selection(self) -> List[str]:
        """Selected iids with any nested-under-another-selected iids removed."""
        selection = set(self.tree.selection())
        result = []
        for iid in self.tree.selection():
            parent = self.tree.parent(iid)
            nested = False
            while parent:
                if parent in selection:
                    nested = True
                    break
                parent = self.tree.parent(parent)
            if not nested:
                result.append(iid)
        return result

    def _on_select(self, event):
        nodes = self._selected_nodes()
        if not nodes:
            if self.root_node:
                self._set_status(f"{self.root_node.item_count:,} items")
            return
        total = sum(n.size for n in nodes)
        self._set_status(f"{len(nodes)} selected  |  {format_size(total)}")

    def _on_double_click(self, event):
        iid = self.tree.identify_row(event.y)
        node = self.iid_to_node.get(iid)
        if node and not node.is_dir and self.settings.preview_enabled and is_image_file(node.path):
            ImagePreview(self, node.path)

    def _on_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if iid and iid not in self.tree.selection():
            self.tree.selection_set(iid)
        if self.tree.selection():
            self.menu.tk_popup(event.x_root, event.y_root)

    # -------------------------------------------------------------- actions

    def _open_in_explorer(self):
        nodes = self._selected_nodes()
        if not nodes:
            return
        target = nodes[0].path
        try:
            if sys.platform == "win32":
                if os.path.isdir(target):
                    os.startfile(target)
                else:
                    subprocess.Popen(["explorer", "/select,", target])
            else:
                subprocess.Popen(["xdg-open", target if os.path.isdir(target) else os.path.dirname(target)])
        except OSError as e:
            messagebox.showerror("Error", f"Could not open: {e}")

    def _zip_selected(self):
        iids = self._top_level_selection()
        if not iids:
            messagebox.showwarning("No Selection", "Select files or folders to zip.")
            return

        save_path = filedialog.asksaveasfilename(
            defaultextension=".zip",
            filetypes=[("ZIP files", "*.zip")],
            title="Save ZIP as"
        )
        if not save_path:
            return

        paths = [self.iid_to_node[iid].path for iid in iids if iid in self.iid_to_node]
        self._set_status("Creating ZIP...")

        def worker():
            try:
                with zipfile.ZipFile(save_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for path in paths:
                        if os.path.isfile(path):
                            zf.write(path, os.path.basename(path))
                        elif os.path.isdir(path):
                            base = os.path.dirname(path)
                            for root, dirs, files in os.walk(path):
                                for file in files:
                                    file_path = os.path.join(root, file)
                                    try:
                                        zf.write(file_path, os.path.relpath(file_path, base))
                                    except OSError:
                                        pass
                self.after(0, lambda: (
                    self._set_status("ZIP created"),
                    messagebox.showinfo("Success", f"Created: {save_path}")
                ))
            except Exception as e:
                self.after(0, lambda: (
                    self._set_status("ZIP failed"),
                    messagebox.showerror("Error", f"Failed to create ZIP: {e}")
                ))

        threading.Thread(target=worker, daemon=True).start()

    def _delete_selected(self):
        iids = self._top_level_selection()
        if not iids:
            messagebox.showwarning("No Selection", "Select files or folders to delete.")
            return

        nodes = [self.iid_to_node[iid] for iid in iids if iid in self.iid_to_node]
        total = sum(n.size for n in nodes)
        if not messagebox.askyesno(
            "Confirm Delete",
            f"Delete {len(nodes)} item(s) ({format_size(total)})?\nThis cannot be undone."
        ):
            return

        self._set_status("Deleting...")

        def worker():
            deleted = []
            errors = []
            for iid, node in zip(iids, nodes):
                try:
                    if os.path.isdir(node.path):
                        shutil.rmtree(node.path)
                    else:
                        os.remove(node.path)
                    deleted.append((iid, node))
                except Exception as e:
                    errors.append(f"{node.name}: {e}")

            self.after(0, lambda: self._apply_deletions(deleted, errors))

        threading.Thread(target=worker, daemon=True).start()

    def _apply_deletions(self, deleted, errors):
        for iid, node in deleted:
            # update ancestor sizes in the model without rescanning
            removed_items = (1 + node.item_count) if node.is_dir else 1
            parent = node.parent
            if parent and node in parent.children:
                parent.children.remove(node)
            while parent:
                parent.size -= node.size
                parent.item_count -= removed_items
                parent = parent.parent

            parent_iid = self.tree.parent(iid)
            if self.tree.exists(iid):
                self.tree.delete(iid)
            self.iid_to_node.pop(iid, None)
            self._refresh_row_and_children(parent_iid)

        if self.root_node:
            status = f"{self.root_node.item_count:,} items"
            if deleted:
                status = f"Deleted {len(deleted)} item(s)  |  " + status
            self._set_status(status)
            self.status_right.configure(text=f"Total: {format_size(self.root_node.size)}")

        if errors:
            messagebox.showerror("Errors", "\n".join(errors[:5]))

    def _refresh_row_and_children(self, iid: str):
        """Recompute displayed values for a row and its visible children
        (sizes and percentages change after a deletion)."""
        while True:
            node = self.iid_to_node.get(iid) if iid else self.root_node
            if node is None:
                break
            if iid:
                grand = self.iid_to_node.get(self.tree.parent(iid)) or self.root_node
                self.tree.item(iid, values=self._values_for(node, grand))
            for child_iid in self.tree.get_children(iid):
                child = self.iid_to_node.get(child_iid)
                if child:
                    self.tree.item(child_iid, values=self._values_for(child, node))
            if not iid:
                break
            iid = self.tree.parent(iid)

    # ---------------------------------------------------------------- misc

    def _toggle_theme(self):
        self.settings.dark_mode = not self.settings.dark_mode
        ctk.set_appearance_mode("dark" if self.settings.dark_mode else "light")
        self.theme_btn.configure(text=ICONS['sun'] if self.settings.dark_mode else ICONS['moon'])
        self._apply_tree_style()
        self.settings.save()

    def _show_settings(self):
        SettingsMenu(self, self.settings, self._on_settings_apply)

    def _on_settings_apply(self):
        self.settings.save()
        self._apply_tree_style()

    def _set_status(self, text: str):
        self.status_left.configure(text=text)


def run_app(folder_path: Optional[str] = None):
    """Start application"""
    app = FolderLensApp(initial_path=folder_path)
    app.mainloop()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else None
    run_app(path)
