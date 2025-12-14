import customtkinter as ctk
from tkinter import filedialog, messagebox
import tkinter as tk
from typing import Optional, List, Callable, Set
import os
import threading
import shutil
import zipfile
from PIL import Image, ImageTk

from file_utils import (
    get_file_category, format_size, format_date, 
    get_file_extension, calculate_percentage, natural_sort_key,
    get_file_icon, is_image_file, ICONS, FILE_CATEGORIES
)
from scanner import FolderScanner, FileItem, ScanResult
from version import VERSION
from updater import get_updater, UpdateInfo


ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


class AppSettings:
    def __init__(self):
        self.icon_size = "medium"
        self.preview_enabled = True
        self.dark_mode = True
    
    def get_icon_font_size(self) -> int:
        sizes = {"small": 14, "medium": 20, "large": 28}
        return sizes.get(self.icon_size, 20)
    
    def get_row_height(self) -> int:
        heights = {"small": 40, "medium": 56, "large": 72}
        return heights.get(self.icon_size, 56)


class SelectionManager:
    def __init__(self):
        self.selected_items: Set[str] = set()
        self.on_change_callbacks: List[Callable] = []
    
    def toggle(self, path: str) -> bool:
        if path in self.selected_items:
            self.selected_items.remove(path)
            selected = False
        else:
            self.selected_items.add(path)
            selected = True
        self._notify()
        return selected
    
    def select(self, path: str):
        self.selected_items.add(path)
        self._notify()
    
    def deselect(self, path: str):
        self.selected_items.discard(path)
        self._notify()
    
    def clear(self):
        self.selected_items.clear()
        self._notify()
    
    def is_selected(self, path: str) -> bool:
        return path in self.selected_items
    
    def count(self) -> int:
        return len(self.selected_items)
    
    def get_total_size(self) -> int:
        total = 0
        for path in self.selected_items:
            try:
                if os.path.isfile(path):
                    total += os.path.getsize(path)
                elif os.path.isdir(path):
                    for root, dirs, files in os.walk(path):
                        for f in files:
                            try:
                                total += os.path.getsize(os.path.join(root, f))
                            except:
                                pass
            except:
                pass
        return total
    
    def on_change(self, callback: Callable):
        self.on_change_callbacks.append(callback)
    
    def _notify(self):
        for cb in self.on_change_callbacks:
            try:
                cb()
            except:
                pass


class SortState:
    """Sort state"""
    ASCENDING = "asc"
    DESCENDING = "desc"
    NONE = None


class FileListItem(ctk.CTkFrame):
    
    def __init__(
        self, 
        master, 
        file_item: FileItem, 
        total_size: int,
        settings: AppSettings,
        selection_manager: SelectionManager,
        on_double_click: Optional[Callable] = None,
        on_preview: Optional[Callable] = None,
        **kwargs
    ):
        super().__init__(master, **kwargs)
        
        self.file_item = file_item
        self.on_double_click = on_double_click
        self.on_preview = on_preview
        self.settings = settings
        self.selection_manager = selection_manager
        self.is_selected = selection_manager.is_selected(file_item.path)
        
        row_height = settings.get_row_height()
        icon_size = settings.get_icon_font_size()
        
        self.configure(
            fg_color="transparent",
            corner_radius=8,
            height=row_height
        )
        
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)
        self.bind("<Double-Button-1>", self._on_double_click)
        
        self.grid_columnconfigure(0, weight=0, minsize=40)  # Checkbox
        self.grid_columnconfigure(1, weight=4)  # Name
        self.grid_columnconfigure(2, weight=4)  # Usage bar
        self.grid_columnconfigure(3, weight=2)  # Size
        
        category = get_file_category(file_item.path)
        file_icon = get_file_icon(file_item.path)
        
        self.checkbox = ctk.CTkButton(
            self,
            text=ICONS['check_filled'] if self.is_selected else ICONS['check_empty'],
            font=ctk.CTkFont(size=16),
            fg_color="transparent",
            hover_color=("gray90", "gray25"),
            text_color="#3B82F6" if self.is_selected else "gray",
            width=32,
            height=32,
            corner_radius=16,
            command=self._toggle_selection
        )
        self.checkbox.grid(row=0, column=0, padx=(8, 0), pady=4)
        
        name_frame = ctk.CTkFrame(self, fg_color="transparent")
        name_frame.grid(row=0, column=1, sticky="w", padx=(8, 8), pady=4)
        
        icon_label = ctk.CTkLabel(
            name_frame,
            text=file_icon,
            font=ctk.CTkFont(size=icon_size),
            width=36
        )
        icon_label.grid(row=0, column=0, rowspan=2, padx=(0, 8))
        
        name_label = ctk.CTkLabel(
            name_frame,
            text=file_item.name,
            font=ctk.CTkFont(size=13, weight="bold"),
            anchor="w"
        )
        name_label.grid(row=0, column=1, sticky="w")
        
        if file_item.is_directory:
            sub_text = f"{file_item.item_count} items"
        else:
            sub_text = category['label']
        
        sub_label = ctk.CTkLabel(
            name_frame,
            text=sub_text,
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        sub_label.grid(row=1, column=1, sticky="w")
        
        percentage = calculate_percentage(file_item.size, total_size)
        
        bar_frame = ctk.CTkFrame(self, fg_color="transparent")
        bar_frame.grid(row=0, column=2, sticky="ew", padx=8, pady=4)
        
        bar_bg = ctk.CTkFrame(
            bar_frame,
            height=8,
            corner_radius=4,
            fg_color=("gray85", "gray25")
        )
        bar_bg.pack(fill="x", expand=True, pady=16)
        
        if percentage > 0:
            bar_fill = ctk.CTkFrame(
                bar_bg,
                height=8,
                corner_radius=4,
                fg_color=category['color']
            )
            bar_fill.place(relx=0, rely=0, relwidth=min(percentage/100, 1.0), relheight=1.0)
        
        size_label = ctk.CTkLabel(
            self,
            text=format_size(file_item.size),
            font=ctk.CTkFont(size=13, weight="bold" if file_item.is_directory else "normal"),
            anchor="e"
        )
        size_label.grid(row=0, column=3, sticky="e", padx=(8, 16))
        
        for child in self.winfo_children():
            if child != self.checkbox:
                child.bind("<Enter>", self._on_enter)
                child.bind("<Leave>", self._on_leave)
                child.bind("<Double-Button-1>", self._on_double_click)
                self._bind_children(child)
    
    def _bind_children(self, widget):
        for child in widget.winfo_children():
            child.bind("<Enter>", self._on_enter)
            child.bind("<Leave>", self._on_leave)
            child.bind("<Double-Button-1>", self._on_double_click)
            self._bind_children(child)
    
    def _toggle_selection(self):
        self.is_selected = self.selection_manager.toggle(self.file_item.path)
        self.checkbox.configure(
            text=ICONS['check_filled'] if self.is_selected else ICONS['check_empty'],
            text_color="#3B82F6" if self.is_selected else "gray"
        )
    
    def _on_enter(self, event):
        self.configure(fg_color=("gray90", "gray20"))
    
    def _on_leave(self, event):
        self.configure(fg_color="transparent")
    
    def _on_double_click(self, event):
        if self.file_item.is_directory and self.on_double_click:
            self.on_double_click(self.file_item.path)
        elif is_image_file(self.file_item.path) and self.settings.preview_enabled and self.on_preview:
            self.on_preview(self.file_item.path)


class SettingsMenu(ctk.CTkToplevel):
    """Settings popup menu"""
    
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
        
        self._create_ui()
    
    def _create_ui(self):
        main = ctk.CTkFrame(self, fg_color="transparent")
        main.pack(fill="both", expand=True, padx=20, pady=20)
        
        ctk.CTkLabel(main, text="Icon Size", font=ctk.CTkFont(size=14, weight="bold")).pack(anchor="w")
        
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
        
        ctk.CTkButton(
            main,
            text="Apply",
            command=self._apply
        ).pack(pady=20)
    
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


class AnalyzePanel(ctk.CTkFrame):
    """Panel for analyzing selected items"""
    
    def __init__(self, master, selection_manager: SelectionManager, on_close: Callable, **kwargs):
        super().__init__(master, **kwargs)
        
        self.selection_manager = selection_manager
        self.on_close = on_close
        
        self.configure(fg_color=("white", "gray15"), corner_radius=12)
        
        self._create_ui()
        selection_manager.on_change(self._update_info)
    
    def _create_ui(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=12)
        
        ctk.CTkLabel(
            header,
            text=f"{ICONS['analyze']} Analyze Selection",
            font=ctk.CTkFont(size=16, weight="bold")
        ).pack(side="left")
        
        ctk.CTkButton(
            header,
            text="✕",
            width=30,
            height=30,
            fg_color="transparent",
            hover_color=("gray90", "gray25"),
            command=self.on_close
        ).pack(side="right")
        
        self.info_frame = ctk.CTkFrame(self, fg_color="transparent")
        self.info_frame.pack(fill="x", padx=16, pady=8)
        
        self.count_label = ctk.CTkLabel(
            self.info_frame,
            text="0 items selected",
            font=ctk.CTkFont(size=13)
        )
        self.count_label.pack(anchor="w")
        
        self.size_label = ctk.CTkLabel(
            self.info_frame,
            text="Total: 0 B",
            font=ctk.CTkFont(size=13),
            text_color="gray"
        )
        self.size_label.pack(anchor="w")
        
        actions = ctk.CTkFrame(self, fg_color="transparent")
        actions.pack(fill="x", padx=16, pady=12)
        
        ctk.CTkButton(
            actions,
            text=f"{ICONS['zip']} Zip Selected",
            command=self._zip_selected,
            height=36
        ).pack(fill="x", pady=4)
        
        ctk.CTkButton(
            actions,
            text=f"{ICONS['delete']} Delete Selected",
            command=self._delete_selected,
            fg_color="#EF4444",
            hover_color="#DC2626",
            height=36
        ).pack(fill="x", pady=4)
        
        ctk.CTkButton(
            actions,
            text="Clear Selection",
            command=self._clear_selection,
            fg_color="transparent",
            border_width=1,
            text_color=("gray20", "gray80"),
            height=36
        ).pack(fill="x", pady=4)
    
    def _update_info(self):
        count = self.selection_manager.count()
        self.count_label.configure(text=f"{count} item{'s' if count != 1 else ''} selected")
        
        total_size = self.selection_manager.get_total_size()
        self.size_label.configure(text=f"Total: {format_size(total_size)}")
    
    def _zip_selected(self):
        if self.selection_manager.count() == 0:
            messagebox.showwarning("No Selection", "Please select items to zip.")
            return
        
        save_path = filedialog.asksaveasfilename(
            defaultextension=".zip",
            filetypes=[("ZIP files", "*.zip")],
            title="Save ZIP as"
        )
        
        if save_path:
            try:
                with zipfile.ZipFile(save_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                    for path in self.selection_manager.selected_items:
                        if os.path.isfile(path):
                            zf.write(path, os.path.basename(path))
                        elif os.path.isdir(path):
                            for root, dirs, files in os.walk(path):
                                for file in files:
                                    file_path = os.path.join(root, file)
                                    arc_name = os.path.relpath(file_path, os.path.dirname(path))
                                    zf.write(file_path, arc_name)
                
                messagebox.showinfo("Success", f"Created: {save_path}")
                self.selection_manager.clear()
            except Exception as e:
                messagebox.showerror("Error", f"Failed to create ZIP: {e}")
    
    def _delete_selected(self):
        count = self.selection_manager.count()
        if count == 0:
            messagebox.showwarning("No Selection", "Please select items to delete.")
            return
        
        if messagebox.askyesno("Confirm Delete", f"Delete {count} item(s)? This cannot be undone."):
            errors = []
            for path in list(self.selection_manager.selected_items):
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                    elif os.path.isdir(path):
                        shutil.rmtree(path)
                except Exception as e:
                    errors.append(f"{path}: {e}")
            
            self.selection_manager.clear()
            
            if errors:
                messagebox.showerror("Errors", "\n".join(errors[:5]))
            else:
                messagebox.showinfo("Success", "Items deleted successfully.")
    
    def _clear_selection(self):
        self.selection_manager.clear()


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


class FilterBar(ctk.CTkFrame):
    """Filter and sort buttons"""
    
    def __init__(self, master, on_filter: Callable, on_refresh: Callable, on_settings: Callable, **kwargs):
        super().__init__(master, fg_color="transparent", **kwargs)
        
        self.on_filter = on_filter
        self.on_refresh = on_refresh
        self.on_settings = on_settings
        self.active_sort = "size"
        
        left_frame = ctk.CTkFrame(self, fg_color="transparent")
        left_frame.pack(side="left", fill="y")
        
        filter_btn = ctk.CTkButton(
            left_frame,
            text="Filter",
            font=ctk.CTkFont(size=12),
            fg_color=("white", "gray20"),
            hover_color=("gray90", "gray25"),
            text_color=("gray20", "gray80"),
            corner_radius=20,
            height=32,
            width=80,
            border_width=1,
            border_color=("gray80", "gray40")
        )
        filter_btn.pack(side="left", padx=(0, 8))
        
        separator = ctk.CTkFrame(left_frame, width=1, height=20, fg_color="gray")
        separator.pack(side="left", padx=8)
        
        sort_buttons = [
            ("Size", "size", True),
            ("Type", "type", False),
            ("Date", "date", False),
            ("Name", "name", False),
        ]
        
        self.sort_btns = {}
        for text, key, active in sort_buttons:
            btn = ctk.CTkButton(
                left_frame,
                text=f"● {text} ↓" if active else text,
                font=ctk.CTkFont(size=12, weight="bold" if active else "normal"),
                fg_color="#135bec" if active else ("white", "gray20"),
                hover_color="#1048c4" if active else ("gray90", "gray25"),
                text_color="white" if active else ("gray40", "gray70"),
                corner_radius=20,
                height=32,
                width=90 if active else 70,
                command=lambda k=key: self._on_sort_click(k)
            )
            btn.pack(side="left", padx=2)
            self.sort_btns[key] = btn
        
        right_frame = ctk.CTkFrame(self, fg_color="transparent")
        right_frame.pack(side="right", fill="y")
        
        refresh_btn = ctk.CTkButton(
            right_frame,
            text=ICONS['refresh'],
            font=ctk.CTkFont(size=16),
            fg_color="transparent",
            hover_color=("gray90", "gray25"),
            text_color=("gray40", "gray70"),
            width=32,
            height=32,
            command=on_refresh
        )
        refresh_btn.pack(side="left", padx=2)
        
        settings_btn = ctk.CTkButton(
            right_frame,
            text="•••",
            font=ctk.CTkFont(size=16),
            fg_color="transparent",
            hover_color=("gray90", "gray25"),
            text_color=("gray40", "gray70"),
            width=32,
            height=32,
            command=on_settings
        )
        settings_btn.pack(side="left", padx=2)
    
    def _on_sort_click(self, key: str):
        if self.active_sort == key:
            return
        
        old_btn = self.sort_btns[self.active_sort]
        old_text = old_btn.cget("text")
        clean_text = old_text.replace("● ", "").replace(" ↓", "").replace(" ↑", "")
        old_btn.configure(
            text=clean_text,
            fg_color=("white", "gray20"),
            text_color=("gray40", "gray70"),
            font=ctk.CTkFont(size=12),
            width=70
        )
        
        new_btn = self.sort_btns[key]
        new_text = new_btn.cget("text")
        clean_text = new_text.replace("● ", "").replace(" ↓", "").replace(" ↑", "")
        new_btn.configure(
            text=f"● {clean_text} ↓",
            fg_color="#135bec",
            text_color="white",
            font=ctk.CTkFont(size=12, weight="bold"),
            width=90
        )
        
        self.active_sort = key
        self.on_filter(key, "desc")


class BreadcrumbBar(ctk.CTkFrame):
    """Breadcrumb navigation bar"""
    
    def __init__(self, master, on_navigate: Callable, **kwargs):
        super().__init__(master, fg_color="transparent", height=32, **kwargs)
        
        self.on_navigate = on_navigate
        self.current_path = ""
        
    def set_path(self, path: str):
        self.current_path = path
        
        for widget in self.winfo_children():
            widget.destroy()
        
        parts = []
        current = path
        while current:
            parent, name = os.path.split(current)
            if name:
                parts.append((current, name))
            elif parent:
                parts.append((parent, parent))
                break
            current = parent
        
        parts.reverse()
        
        pc_btn = ctk.CTkButton(
            self,
            text="This PC",
            font=ctk.CTkFont(size=12),
            fg_color="transparent",
            hover_color=("gray90", "gray25"),
            text_color=("gray40", "gray70"),
            height=28,
            width=70,
            anchor="w"
        )
        pc_btn.pack(side="left")
        
        for i, (full_path, name) in enumerate(parts):
            sep = ctk.CTkLabel(self, text="›", font=ctk.CTkFont(size=14), text_color="gray")
            sep.pack(side="left", padx=4)
            
            is_last = (i == len(parts) - 1)
            
            btn = ctk.CTkButton(
                self,
                text=name,
                font=ctk.CTkFont(size=12, weight="bold" if is_last else "normal"),
                fg_color=("white", "gray20") if is_last else "transparent",
                hover_color=("gray90", "gray25"),
                text_color=("gray20", "gray80") if is_last else ("gray40", "gray70"),
                height=28,
                corner_radius=6 if is_last else 0,
                command=lambda p=full_path: self.on_navigate(p)
            )
            btn.pack(side="left")


class StatusBar(ctk.CTkFrame):
    """Status bar"""
    
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=("gray95", "gray15"), height=32, **kwargs)
        
        self.left_label = ctk.CTkLabel(
            self,
            text="Ready",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        self.left_label.pack(side="left", padx=16)
        
        self.right_label = ctk.CTkLabel(
            self,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        self.right_label.pack(side="right", padx=16)
    
    def update_status(self, item_count: int = 0, selected: int = 0, total_size: int = 0):
        left_text = f"{item_count} items"
        if selected > 0:
            left_text += f"  |  {selected} selected"
        
        self.left_label.configure(text=left_text)
        self.right_label.configure(text=f"Total: {format_size(total_size)}")


class LoadingOverlay(ctk.CTkFrame):
    """Loading overlay"""
    
    def __init__(self, master, **kwargs):
        super().__init__(master, fg_color=("white", "gray20"), **kwargs)
        
        self.configure(corner_radius=12)
        
        content = ctk.CTkFrame(self, fg_color="transparent")
        content.place(relx=0.5, rely=0.5, anchor="center")
        
        self.spinner_label = ctk.CTkLabel(
            content,
            text="...",
            font=ctk.CTkFont(size=32)
        )
        self.spinner_label.pack(pady=(0, 16))
        
        self.message_label = ctk.CTkLabel(
            content,
            text="Scanning...",
            font=ctk.CTkFont(size=14)
        )
        self.message_label.pack()
        
        self.sub_label = ctk.CTkLabel(
            content,
            text="",
            font=ctk.CTkFont(size=11),
            text_color="gray"
        )
        self.sub_label.pack(pady=(8, 0))
        
        self._animation_running = False
        self._spinner_chars = [".", "..", "..."]
        self._spinner_index = 0
    
    def start_animation(self):
        self._animation_running = True
        self._animate()
    
    def stop_animation(self):
        self._animation_running = False
    
    def _animate(self):
        if not self._animation_running:
            return
        
        self._spinner_index = (self._spinner_index + 1) % len(self._spinner_chars)
        self.spinner_label.configure(text=self._spinner_chars[self._spinner_index])
        self.after(500, self._animate)
    
    def update_message(self, message: str, sub_message: str = ""):
        self.message_label.configure(text=message)
        self.sub_label.configure(text=sub_message)


class FolderLensApp(ctk.CTk):
    """Main application window"""
    
    def __init__(self, initial_path: Optional[str] = None):
        super().__init__()
        
        self.title("FolderLens")
        self.geometry("1200x800")
        self.minsize(900, 600)
        
        self.overrideredirect(True)
        
        self._drag_start_x = 0
        self._drag_start_y = 0
        self._is_maximized = False
        self._normal_geometry = None
        
        self.update_idletasks()
        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        x = (screen_width - 1200) // 2
        y = (screen_height - 800) // 2
        self.geometry(f"1200x800+{x}+{y}")
        
        self.current_path = initial_path or ""
        self.scanner = FolderScanner()
        self.current_items: List[FileItem] = []
        self.total_size = 0
        self.sort_key = "size"
        self.sort_order = SortState.DESCENDING
        
        self.settings = AppSettings()
        self.selection_manager = SelectionManager()
        self.selection_manager.on_change(self._on_selection_change)
        
        self.analyze_visible = False
        
        self._create_ui()
        
        if initial_path and os.path.isdir(initial_path):
            self.after(100, lambda: self.scan_folder(initial_path))
    
    def _create_ui(self):
        self.main_container = ctk.CTkFrame(self, fg_color=("gray98", "gray10"), corner_radius=0)
        self.main_container.pack(fill="both", expand=True)
        
        self._create_title_bar()
        self._create_navigation()
        
        self.content_area = ctk.CTkFrame(self.main_container, fg_color="transparent")
        self.content_area.pack(fill="both", expand=True, padx=24)
        
        self.main_content = ctk.CTkFrame(self.content_area, fg_color="transparent")
        self.main_content.pack(side="left", fill="both", expand=True)
        
        self.filter_bar = FilterBar(
            self.main_content,
            on_filter=self._on_filter_change,
            on_refresh=self._on_refresh,
            on_settings=self._show_settings
        )
        self.filter_bar.pack(fill="x", pady=(0, 16))
        
        header_frame = ctk.CTkFrame(self.main_content, fg_color="transparent", height=30)
        header_frame.pack(fill="x")
        
        ctk.CTkLabel(header_frame, text="NAME", font=ctk.CTkFont(size=11, weight="bold"), text_color="gray").pack(side="left", padx=(48, 0))
        ctk.CTkLabel(header_frame, text="SIZE ↓", font=ctk.CTkFont(size=11, weight="bold"), text_color="gray").pack(side="right", padx=16)
        
        separator = ctk.CTkFrame(self.main_content, height=1, fg_color=("gray80", "gray30"))
        separator.pack(fill="x", pady=(0, 8))
        
        self.file_list = ctk.CTkScrollableFrame(self.main_content, fg_color="transparent")
        self.file_list.pack(fill="both", expand=True)
        
        self.analyze_panel = None
        
        self.status_bar = StatusBar(self.main_container)
        self.status_bar.pack(fill="x", side="bottom")
        
        self.loading_overlay = LoadingOverlay(self.main_container)
    
    def _create_title_bar(self):
        self.title_bar = ctk.CTkFrame(
            self.main_container,
            fg_color=("white", "gray15"),
            height=40,
            corner_radius=0
        )
        self.title_bar.pack(fill="x")
        self.title_bar.pack_propagate(False)
        
        self.title_bar.bind("<Button-1>", self._start_drag)
        self.title_bar.bind("<B1-Motion>", self._on_drag)
        self.title_bar.bind("<Double-Button-1>", self._on_title_double_click)
        
        left_frame = ctk.CTkFrame(self.title_bar, fg_color="transparent")
        left_frame.pack(side="left", padx=12)
        left_frame.bind("<Button-1>", self._start_drag)
        left_frame.bind("<B1-Motion>", self._on_drag)
        
        logo_frame = ctk.CTkFrame(left_frame, width=24, height=24, corner_radius=4, fg_color="#135bec")
        logo_frame.pack(side="left", padx=(0, 8))
        logo_frame.pack_propagate(False)
        
        ctk.CTkLabel(logo_frame, text="F", font=ctk.CTkFont(size=12, weight="bold"), text_color="white").place(relx=0.5, rely=0.5, anchor="center")
        
        title_label = ctk.CTkLabel(left_frame, text="FolderLens", font=ctk.CTkFont(size=12, weight="bold"), text_color=("gray20", "gray80"))
        title_label.pack(side="left")
        title_label.bind("<Button-1>", self._start_drag)
        title_label.bind("<B1-Motion>", self._on_drag)
        
        controls_frame = ctk.CTkFrame(self.title_bar, fg_color="transparent")
        controls_frame.pack(side="right")
        
        self.theme_btn = ctk.CTkButton(
            controls_frame,
            text=ICONS['sun'] if self.settings.dark_mode else ICONS['moon'],
            font=ctk.CTkFont(size=14),
            fg_color="transparent",
            hover_color=("gray90", "gray25"),
            text_color="gray",
            width=40,
            height=40,
            corner_radius=0,
            command=self._toggle_theme
        )
        self.theme_btn.pack(side="left")
        
        self.update_btn = ctk.CTkButton(
            controls_frame,
            text="⬆",
            font=ctk.CTkFont(size=14),
            fg_color="transparent",
            hover_color=("gray90", "gray25"),
            text_color="gray",
            width=40,
            height=40,
            corner_radius=0,
            command=self._show_updates
        )
        self.update_btn.pack(side="left")
        
        self.analyze_btn = ctk.CTkButton(
            controls_frame,
            text=ICONS['analyze'],
            font=ctk.CTkFont(size=14),
            fg_color="transparent",
            hover_color=("gray90", "gray25"),
            text_color="gray",
            width=40,
            height=40,
            corner_radius=0,
            command=self._toggle_analyze
        )
        self.analyze_btn.pack(side="left")
        
        ctk.CTkButton(
            controls_frame, text="─", font=ctk.CTkFont(size=12),
            fg_color="transparent", hover_color=("gray90", "gray25"),
            text_color="gray", width=46, height=40, corner_radius=0,
            command=self._minimize_window
        ).pack(side="left")
        
        self.max_btn = ctk.CTkButton(
            controls_frame, text="□", font=ctk.CTkFont(size=12),
            fg_color="transparent", hover_color=("gray90", "gray25"),
            text_color="gray", width=46, height=40, corner_radius=0,
            command=self._toggle_maximize
        )
        self.max_btn.pack(side="left")
        
        ctk.CTkButton(
            controls_frame, text="✕", font=ctk.CTkFont(size=12),
            fg_color="transparent", hover_color="#e81123",
            text_color="gray", width=46, height=40, corner_radius=0,
            command=self.destroy
        ).pack(side="left")
    
    def _create_navigation(self):
        nav_frame = ctk.CTkFrame(self.main_container, fg_color="transparent")
        nav_frame.pack(fill="x", padx=24, pady=16)
        
        self.breadcrumb = BreadcrumbBar(nav_frame, on_navigate=self.scan_folder)
        self.breadcrumb.pack(fill="x")
    
    def _start_drag(self, event):
        self._drag_start_x = event.x
        self._drag_start_y = event.y
    
    def _on_drag(self, event):
        if self._is_maximized:
            self._toggle_maximize()
        
        x = self.winfo_x() + event.x - self._drag_start_x
        y = self.winfo_y() + event.y - self._drag_start_y
        self.geometry(f"+{x}+{y}")
    
    def _on_title_double_click(self, event):
        self._toggle_maximize()
    
    def _minimize_window(self):
        self.overrideredirect(False)
        self.iconify()
        self.after(100, lambda: self.overrideredirect(True))
    
    def _toggle_maximize(self):
        if self._is_maximized:
            if self._normal_geometry:
                self.geometry(self._normal_geometry)
            self._is_maximized = False
            self.max_btn.configure(text="□")
        else:
            self._normal_geometry = self.geometry()
            screen_width = self.winfo_screenwidth()
            screen_height = self.winfo_screenheight()
            self.geometry(f"{screen_width}x{screen_height - 40}+0+0")
            self._is_maximized = True
            self.max_btn.configure(text="❐")
    
    def _toggle_theme(self):
        self.settings.dark_mode = not self.settings.dark_mode
        mode = "dark" if self.settings.dark_mode else "light"
        ctk.set_appearance_mode(mode)
        self.theme_btn.configure(text=ICONS['sun'] if self.settings.dark_mode else ICONS['moon'])
    
    def _toggle_analyze(self):
        if self.analyze_visible:
            if self.analyze_panel:
                self.analyze_panel.destroy()
                self.analyze_panel = None
            self.analyze_visible = False
        else:
            self.analyze_panel = AnalyzePanel(
                self.content_area,
                self.selection_manager,
                on_close=self._toggle_analyze
            )
            self.analyze_panel.pack(side="right", fill="y", padx=(16, 0))
            self.analyze_visible = True
    
    def _show_updates(self):
        """Show update dialog"""
        UpdateDialog(self)
    
    def _show_settings(self):
        SettingsMenu(self, self.settings, self._on_settings_apply)
    
    def _on_settings_apply(self):
        if self.current_path:
            self._refresh_list()
    
    def _on_selection_change(self):
        self.status_bar.update_status(
            item_count=len(self.current_items),
            selected=self.selection_manager.count(),
            total_size=self.total_size
        )
    
    def scan_folder(self, path: str):
        self.current_path = path
        self.breadcrumb.set_path(path)
        self._show_loading()
        
        self.scanner.scan(
            path,
            on_progress=self._on_scan_progress,
            on_complete=self._on_scan_complete,
            on_error=self._on_scan_error
        )
    
    def _show_loading(self):
        self.loading_overlay.place(relx=0.5, rely=0.5, anchor="center", relwidth=0.4, relheight=0.2)
        self.loading_overlay.start_animation()
        self.loading_overlay.lift()
    
    def _hide_loading(self):
        self.loading_overlay.stop_animation()
        self.loading_overlay.place_forget()
    
    def _on_scan_progress(self, current_file: str, items_scanned: int):
        self.after(0, lambda: self.loading_overlay.update_message(
            "Scanning...",
            f"{items_scanned} items: {current_file[:40]}..."
        ))
    
    def _on_scan_complete(self, result: ScanResult):
        self.after(0, lambda: self._display_results(result))
    
    def _on_scan_error(self, error: str):
        self.after(0, lambda: self._show_error(error))
    
    def _display_results(self, result: ScanResult):
        self._hide_loading()
        
        self.current_items = result.items
        self.total_size = result.total_size
        
        self._sort_items()
        self._refresh_list()
        
        self.status_bar.update_status(
            item_count=result.total_items,
            selected=self.selection_manager.count(),
            total_size=result.total_size
        )
    
    def _refresh_list(self):
        for widget in self.file_list.winfo_children():
            widget.destroy()
        
        for item in self.current_items:
            file_widget = FileListItem(
                self.file_list,
                item,
                self.total_size,
                self.settings,
                self.selection_manager,
                on_double_click=self.scan_folder,
                on_preview=self._show_image_preview
            )
            file_widget.pack(fill="x", pady=2)
    
    def _show_image_preview(self, path: str):
        ImagePreview(self, path)
    
    def _sort_items(self):
        reverse = (self.sort_order == SortState.DESCENDING)
        
        if self.sort_key == "size":
            self.current_items.sort(key=lambda x: x.size, reverse=reverse)
        elif self.sort_key == "name":
            self.current_items.sort(key=lambda x: natural_sort_key(x.name), reverse=reverse)
        elif self.sort_key == "date":
            self.current_items.sort(key=lambda x: x.creation_date, reverse=reverse)
        elif self.sort_key == "type":
            self.current_items.sort(
                key=lambda x: (not x.is_directory, get_file_category(x.path)['label']),
                reverse=reverse
            )
    
    def _on_filter_change(self, sort_key: str, sort_order: str):
        self.sort_key = sort_key
        self.sort_order = sort_order
        self._sort_items()
        self._refresh_list()
    
    def _on_refresh(self):
        if self.current_path:
            self.scan_folder(self.current_path)
    
    def _show_error(self, message: str):
        self._hide_loading()
        messagebox.showerror("Error", message)


def run_app(folder_path: Optional[str] = None):
    """Start application"""
    app = FolderLensApp(initial_path=folder_path)
    app.mainloop()


if __name__ == "__main__":
    import sys
    path = sys.argv[1] if len(sys.argv) > 1 else None
    run_app(path)
