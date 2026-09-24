"""GUI regression tests.

These cover crashes that pure-logic tests can't reach, so they need a real Tk
display. They skip themselves cleanly when tkinter or a display is missing
(e.g. a plain CI runner without xvfb), and never block on a mainloop.
"""
import os
import gc
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tk = pytest.importorskip("tkinter", reason="tkinter not available")
pytest.importorskip("customtkinter", reason="customtkinter not installed")

if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    pytest.skip("no display available", allow_module_level=True)

import locations
import analysis
import treemap_render
from scanner import TreeScanner, ScanSnapshot


def scan_sync(path):
    """Scan without needing a mainloop for the completion callback."""
    scanner = TreeScanner()
    holder = {}
    done = threading.Event()
    scanner.scan(
        str(path),
        on_complete=lambda root, errors, t: (holder.update(root=root), done.set()),
        on_error=lambda msg: (holder.update(error=msg), done.set()),
    )
    assert done.wait(timeout=90), "scan did not finish"
    return holder.get("root")


@pytest.fixture
def sample_tree(tmp_path):
    from PIL import Image
    (tmp_path / "big.mp4").write_bytes(b"v" * 60000)
    (tmp_path / "notes.txt").write_bytes(b"t" * 800)
    sub = tmp_path / "sub"
    sub.mkdir()
    Image.new("RGB", (120, 90), (200, 60, 60)).save(sub / "pic.png")
    return tmp_path


@pytest.fixture
def gallery(tmp_path):
    """A folder of real images plus a subfolder, for the viewer tests."""
    from PIL import Image
    root = tmp_path / "gallery"
    (root / "more").mkdir(parents=True)
    for i, color in enumerate([(220, 80, 60), (60, 140, 220), (80, 190, 120)]):
        Image.new("RGB", (160, 120), color).save(root / f"shot{i}.png")
    Image.new("RGB", (80, 60), (10, 10, 10)).save(root / "more" / "deep.png")
    return root


@pytest.fixture(scope="session")
def app_window(tmp_path_factory):
    """One application window for the whole session.

    Every test used to build its own root window. Creating and tearing down
    that many Tcl interpreters in a single process is flaky on CI runners —
    it intermittently fails with "Can't find a usable init.tcl" partway
    through the suite — so the window is made once and reset between tests.
    """
    cfg = tmp_path_factory.mktemp("folderlens-cfg")
    os.environ["APPDATA"] = str(cfg)          # keep off the real settings file
    os.environ["XDG_CONFIG_HOME"] = str(cfg)

    import app as appmod

    try:
        win = appmod.FolderLensApp(initial_path=None)   # no scan kicked off
    except tk.TclError as exc:                # pragma: no cover - environment
        pytest.skip(f"cannot open a Tk display: {exc}")

    win.geometry("1000x700+0+0")
    try:
        yield win
    finally:
        try:
            win.destroy()
        except tk.TclError:
            pass


@pytest.fixture
def gui(app_window, sample_tree):
    """The shared window, reset and pointed at a freshly scanned tree."""
    win = app_window

    # Tk font/widgets from the previous view can form cycles. Finalize them
    # on the Tk thread before the scanner launches worker threads.
    gc.collect()
    root = scan_sync(sample_tree)
    assert root is not None
    win.root_node = root

    # clear anything a previous test left behind
    win.search_var.set("")
    win.search_query = ""
    win.file_filter = "all"
    win._advanced_spec = None
    win.settings.file_filter = "all"
    win.filter_var.set("All file types")
    win._invalidate_filter_index()
    win.treemap_stack = []
    win.dup_groups = []
    win._hover_tile = None
    win._treemap_image = None

    # tests may unbind the resize handler to drive the reflow directly
    win.unbind("<Configure>")
    win.bind("<Configure>", win._on_window_configure, add="+")
    win._toolbar_narrow = None
    win._reflow_toolbar(win.NARROW_WIDTH + 300)

    show(win, "Tree")
    try:
        yield win
    finally:
        win._clear_body()
        gc.collect()


def show(win, view):
    win.active_view = view
    win.view_switch.set(view)
    win._render_active_view()
    win.update_idletasks()


def wait_viewer(viewer, attribute="_loading", timeout=10):
    deadline = time.monotonic() + timeout
    while getattr(viewer, attribute) and time.monotonic() < deadline:
        viewer.update()
        time.sleep(0.01)
    assert not getattr(viewer, attribute), f"viewer did not finish {attribute}"


def wait_treemap(win, timeout=10):
    deadline = time.monotonic() + timeout
    while win._treemap_rendering and time.monotonic() < deadline:
        win.update()
        time.sleep(0.01)
    assert not win._treemap_rendering, "treemap did not finish rendering"


def wait_largest(win, timeout=10):
    deadline = time.monotonic() + timeout
    while win._largest_loading and time.monotonic() < deadline:
        win.update()
        time.sleep(0.01)
    assert not win._largest_loading, "largest-files query did not finish"


def wait_types(win, timeout=10):
    deadline = time.monotonic() + timeout
    while win._types_loading and time.monotonic() < deadline:
        win.update()
        time.sleep(0.01)
    assert not win._types_loading, "file-type totals did not finish"


def test_toolbar_actions_safe_in_view_without_selection(gui):
    """Regression: the app remembers the last view, so it can start in Treemap.
    The always-visible Zip/Delete buttons used to raise AttributeError there."""
    show(gui, "Treemap")

    assert gui._top_level_selection() == []
    assert gui._selected_nodes() == []
    # and the user gets a hint pointing at a view that does have a selection
    assert "Tree" in gui._no_selection_hint()


def test_selection_helpers_survive_view_switch(gui):
    """Regression: switching views destroys the treeview widget; the stale
    reference used to raise TclError on the next toolbar action."""
    show(gui, "Tree")
    assert gui.tree is not None
    gui.tree.selection_set(gui.tree.get_children()[0])
    assert len(gui._top_level_selection()) == 1

    show(gui, "Treemap")
    assert gui.tree is None
    assert gui._top_level_selection() == []      # must not raise

    show(gui, "Tree")                             # and it comes back
    assert gui.tree is not None
    assert gui._top_level_selection() == []


def test_selection_works_in_largest_files_view(gui):
    """Largest Files is where you find space hogs, so Zip/Delete must work
    against that view's selection too."""
    show(gui, "Largest Files")
    wait_largest(gui)
    assert gui.largest_tree is not None

    rows = gui.largest_tree.get_children()
    assert rows
    gui.largest_tree.selection_set(rows[0])

    selection = gui._top_level_selection()
    assert len(selection) == 1
    iid, node = selection[0]
    assert node.name == "big.mp4"       # largest file first
    assert gui._selected_nodes() == [node]


def test_top_level_selection_drops_nested_rows(gui):
    """A folder and something inside it must not both be acted on."""
    show(gui, "Tree")
    folder_iid = next(i for i, n in gui.iid_to_node.items() if n.is_dir)
    gui.tree.item(folder_iid, open=True)

    # expand: replace the lazy placeholder with the real rows
    kids = gui.tree.get_children(folder_iid)
    if len(kids) == 1 and gui._is_dummy(kids[0]):
        gui.tree.delete(kids[0])
        gui._insert_tree_children(folder_iid, gui.iid_to_node[folder_iid])

    child_iid = gui.tree.get_children(folder_iid)[0]
    gui.tree.selection_set(folder_iid, child_iid)

    selection = gui._top_level_selection()
    assert [n.name for _, n in selection] == [gui.iid_to_node[folder_iid].name]


def test_sort_preserves_expanded_folders(gui):
    folder_iid = next(i for i, n in gui.iid_to_node.items() if n.is_dir)
    folder = gui.iid_to_node[folder_iid]
    gui.tree.item(folder_iid, open=True)
    gui.tree.focus(folder_iid)
    gui._on_tree_open(None)
    gui._sort_tree("name")
    new_iid = next(i for i, n in gui.iid_to_node.items() if n is folder)
    assert gui.tree.item(new_iid, "open")
    assert any(n.name == "pic.png" for n in gui.iid_to_node.values())


def test_wide_tree_loads_rows_in_pages_and_keeps_page_on_sort(gui, tmp_path, monkeypatch):
    wide = tmp_path / "wide"
    wide.mkdir()
    for number in range(205):
        (wide / f"item{number:03}.txt").write_text("x")
    gui.root_node = scan_sync(wide)
    sort_calls = []
    original = gui._sorted_children

    def counted_sort(node):
        sort_calls.append(node)
        return original(node)

    monkeypatch.setattr(gui, "_sorted_children", counted_sort)
    show(gui, "Tree")

    rows = gui.tree.get_children("")
    assert len(rows) == 201
    assert len(gui.iid_to_node) == 200
    assert "page" in gui.tree.item(rows[-1], "tags")

    gui.tree.focus(rows[-1])
    gui._on_tree_page_key(None)
    assert len(gui.tree.get_children("")) == 205
    assert len(gui.iid_to_node) == 205
    assert sort_calls == [gui.root_node]

    gui._sort_tree("name")
    assert len(gui.tree.get_children("")) == 205
    assert len(gui.iid_to_node) == 205


def test_stale_scan_callback_cannot_replace_current_tree(gui):
    old_root = gui.root_node
    gui._scan_generation += 1
    gui._scan_done_if_current(gui._scan_generation - 1, old_root, [], 1.0)
    gui._scan_failed_if_current(gui._scan_generation - 1, "stale error")
    assert gui.root_node is old_root


def test_partial_progress_is_labeled_observed_and_cannot_replace_completion(gui):
    gui._scan_generation += 1
    gui._scan_completed = False
    gui._scan_observed_count = 0
    generation = gui._scan_generation
    snapshot = ScanSnapshot(generation, gui.root_node.path, "scanning", 4096, 2, 3,
                            1, 4, 1, 2, 0, 0.5, True)
    gui._scan_snapshot(generation, snapshot)
    assert "at least" in gui.status_left.cget("text")
    assert "inaccessible" in gui.status_right.cget("text")
    gui._scan_completed = True
    gui._set_status("finished")
    gui._scan_snapshot(generation, snapshot)
    assert gui.status_left.cget("text") == "finished"


def test_filtered_folder_action_uses_real_size_and_all_types(gui, monkeypatch):
    from app import messagebox
    from scanner import Node
    folder = next(node for node in gui.root_node.children if node.is_dir)
    hidden = Node(path=None, name="hidden.txt", is_dir=False, size=4096, parent=folder)
    folder.children.append(hidden)
    folder.size += hidden.size
    folder.item_count += 1
    gui.root_node.size += hidden.size
    gui.root_node.item_count += 1
    gui.file_filter = "image"
    gui._filter_index = analysis.build_filter_index(gui.root_node, "image")
    gui._filter_index_key = "image"
    show(gui, "Tree")
    iid = next(i for i, n in gui.iid_to_node.items() if n.is_dir)
    gui.tree.selection_set(iid)
    node = gui.iid_to_node[iid]
    assert gui._node_size(node) < node.size
    delete_prompts = []
    zip_prompts = []
    monkeypatch.setattr(messagebox, "askyesno",
                        lambda *a, **kw: (delete_prompts.append(a[1]), False)[1])
    monkeypatch.setattr(messagebox, "askyesnocancel",
                        lambda *a, **kw: (zip_prompts.append(a[1]), None)[1])
    gui._delete_selected()
    gui._zip_selected()
    assert "ALL file types" in delete_prompts[0]
    assert "ALL file types" in zip_prompts[0]
    assert "only files matching the current view" in zip_prompts[0]
    assert f"{node.item_count + 1:,} scanned items" in delete_prompts[0]
    assert f"{node.item_count + 1:,} scanned items" in zip_prompts[0]
    from file_utils import format_size
    assert format_size(node.size) in delete_prompts[0]
    assert format_size(node.size) in zip_prompts[0]


def test_treemap_labels_are_hit_testable(gui):
    """Regression: hovering a tile where its name label is drawn used to lose
    the tooltip. Hit-testing is geometric now, so the label area belongs to its
    own tile like any other pixel."""
    show(gui, "Treemap")
    gui.treemap_canvas.configure(width=820, height=520)
    gui.update_idletasks()
    gui._draw_treemap()
    wait_treemap(gui)

    labeled = [t for t in gui._tiles if t.w > 54 and t.h > 18]
    assert labeled, "expected at least one tile large enough to be labelled"

    for tile in labeled:
        hit = treemap_render.hit_test(gui._tiles, tile.x + 8, tile.y + 6)
        assert hit is not None, "lost the hit over a tile label"


def test_treemap_aggregate_lists_omitted_siblings_in_pages(gui):
    from tkinter import ttk
    from scanner import Node
    root = Node(path="/virtual", name="virtual", is_dir=True)
    root.children = [Node(path=f"/virtual/f{i}.txt", name=f"f{i}.txt",
                          is_dir=False, size=i + 1, parent=root) for i in range(207)]
    root.size = sum(child.size for child in root.children)
    root.item_count = len(root.children)
    tiles = analysis.build_treemap(root, 0, 0, 400, 300, max_depth=1, max_children=3)
    aggregate = next(tile.node for tile in tiles if getattr(tile.node, "is_aggregate", False))
    window = gui._show_treemap_aggregate(aggregate)
    try:
        frame = next(child for child in window.winfo_children() if isinstance(child, tk.Frame))
        rows = next(child for child in frame.winfo_children() if isinstance(child, ttk.Treeview))
        button = next(child for child in window.winfo_children() if isinstance(child, ttk.Button))
        deadline = time.time() + 5
        while len(rows.get_children()) < 200 and time.time() < deadline:
            gui.update()
        assert len(rows.get_children()) == 200
        assert rows.item(rows.get_children()[0], "text") == "f204.txt"
        button.invoke()
        assert len(rows.get_children()) == 205
    finally:
        window.destroy()


def test_treemap_redraw_is_wired_to_resize(gui):
    """The canvas must actually ask for a redraw when it is resized."""
    show(gui, "Treemap")
    assert gui.treemap_canvas.bind("<Configure>"), "no <Configure> handler bound"


def test_treemap_resize_is_debounced(gui):
    """A window resize fires a burst of <Configure> events; they must collapse
    into exactly one re-layout instead of one per event.

    Driven through the scheduler directly rather than by resizing a widget:
    whether a toolkit emits <Configure> for a given geometry change differs
    between platforms, but the debouncing itself must not.
    """
    show(gui, "Treemap")

    calls = []
    original = gui._draw_treemap
    gui._draw_treemap = lambda: calls.append(1)
    try:
        for _ in range(40):
            gui._schedule_treemap_redraw()

        assert calls == [], "redrew synchronously during the burst"
        assert gui._treemap_redraw_after is not None, "no redraw was scheduled"

        deadline = time.time() + 5
        while not calls and time.time() < deadline:
            gui.update()
            time.sleep(0.02)

        assert len(calls) == 1, f"expected exactly 1 redraw for 40 events, got {len(calls)}"
        assert gui._treemap_redraw_after is None, "pending redraw was not cleared"
    finally:
        gui._draw_treemap = original


def test_deletion_updates_model_without_the_original_widget(gui, sample_tree):
    """Deleting is async; the user may switch views before it lands. The model
    must still update and nothing may touch the destroyed widget."""
    show(gui, "Tree")
    iid = next(i for i, n in gui.iid_to_node.items() if n.name == "notes.txt")
    node = gui.iid_to_node[iid]
    tree, mapping = gui._selection_context()

    before = gui.root_node.size
    os.remove(node.path)                       # stand in for the worker thread
    show(gui, "Treemap")                       # navigate away -> widget destroyed

    gui._apply_deletions([(iid, node)], [], tree, mapping)

    assert gui.root_node.size == before - node.size
    assert node not in gui.root_node.children


def test_deletion_refreshes_async_file_type_totals(gui):
    show(gui, "File Types")
    wait_types(gui)
    node = next(child for child in gui.root_node.children if child.name == "notes.txt")
    os.remove(node.path)

    gui._apply_deletions([(None, node)], [], action_root=gui.root_node)
    wait_types(gui)

    labels = []
    def collect(widget):
        for child in widget.winfo_children():
            if isinstance(child, tk.Label):
                labels.append(child.cget("text"))
            collect(child)
    collect(gui.body)
    from file_utils import get_file_category
    assert get_file_category("notes.txt", is_dir=False)["label"] not in labels


# --------------------------------------------------------------- toolbar reflow

def packed_in(widget):
    """Which container a widget is currently packed into, as a path string.

    Layout-manager state rather than realized pixels: a headless runner may
    have no window manager, so nothing is ever truly mapped and windows
    cannot grow past the virtual screen.
    """
    try:
        return str(widget.pack_info().get("in", ""))
    except (tk.TclError, KeyError):
        return ""


def is_packed(widget) -> bool:
    return widget.winfo_manager() == "pack"


def test_toolbar_keeps_everything_visible_when_narrow(gui):
    """Regression: packing the whole toolbar into one fixed row silently
    clipped whatever didn't fit, so buttons disappeared on smaller windows."""
    # drive the reflow directly: the real <Configure> handler would otherwise
    # immediately re-apply the runner's own window size
    gui.unbind("<Configure>")

    gui._reflow_toolbar(gui.NARROW_WIDTH + 300)
    gui.update_idletasks()
    assert gui._toolbar_narrow is False
    assert packed_in(gui.actions) == str(gui.toolbar_row1)
    assert packed_in(gui.search_entry) == str(gui.toolbar_row1)
    assert not is_packed(gui.toolbar_row2)

    gui._reflow_toolbar(gui.NARROW_WIDTH - 300)
    gui.update_idletasks()
    assert gui._toolbar_narrow is True
    assert is_packed(gui.toolbar_row2), "second row never appeared"
    # nothing was dropped: both moved to the second row
    assert packed_in(gui.actions) == str(gui.toolbar_row2)
    assert packed_in(gui.search_entry) == str(gui.toolbar_row2)

    gui._reflow_toolbar(gui.NARROW_WIDTH + 300)          # and back again
    gui.update_idletasks()
    assert gui._toolbar_narrow is False
    assert packed_in(gui.actions) == str(gui.toolbar_row1)
    assert not is_packed(gui.toolbar_row2)


# ------------------------------------------------------------------- treemap

def test_treemap_renders_an_image_not_flat_rectangles(gui):
    show(gui, "Treemap")
    gui.treemap_canvas.configure(width=640, height=440)
    gui.update_idletasks()
    gui._draw_treemap()
    wait_treemap(gui)

    assert gui._tiles, "no tiles laid out"
    assert gui._treemap_photo is not None, "treemap image was not produced"


def test_image_filter_projects_all_views(gui):
    """The selected type must change rows, rankings, and treemap leaves."""
    gui.file_filter = "image"
    gui.settings.file_filter = "image"
    gui._filter_index = analysis.build_filter_index(gui.root_node, "image")
    gui._filter_index_key = "image"

    show(gui, "Tree")
    direct = list(gui.iid_to_node.values())
    assert [node.name for node in direct] == ["sub"]
    folder_iid = next(iter(gui.iid_to_node))
    gui._insert_tree_children(folder_iid, gui.iid_to_node[folder_iid])
    assert {node.name for node in gui.iid_to_node.values()} == {"sub", "pic.png"}

    show(gui, "Largest Files")
    wait_largest(gui)
    assert [node.name for node in gui.largest_map.values()] == ["pic.png"]

    show(gui, "Treemap")
    gui.treemap_canvas.configure(width=640, height=440)
    gui.update_idletasks()
    gui._draw_treemap()
    wait_treemap(gui)
    leaves = [tile.node.name for tile in gui._tiles
              if not tile.node.is_dir and not getattr(tile.node, "is_aggregate", False)]
    assert leaves == ["pic.png"]


def test_global_search_projects_all_views(gui):
    """One live search scope must drive the tree, ranking, map, and type totals."""
    result = {"ready": False}
    deadline = time.monotonic() + 10

    def poll_projection():
        if not gui._filter_building and gui._filter_index_key == gui._projection_key():
            result["ready"] = True
            gui.quit()
        elif time.monotonic() >= deadline:
            gui.quit()
        else:
            gui.after(10, poll_projection)

    def start_search():
        gui.search_var.set("pic")
        if gui._search_after:
            gui.after_cancel(gui._search_after)
        gui._search_after = None
        gui._apply_search()
        poll_projection()

    gui.after(0, start_search)
    gui.mainloop()
    gui.update_idletasks()

    assert result["ready"], "search projection did not finish"
    assert not gui._filter_building, "search projection did not finish"
    assert gui._filter_index is not None
    assert gui._filter_index_key == gui._projection_key()
    assert gui._filter_index.count(gui.root_node) == 1

    show(gui, "Tree")
    assert [node.name for node in gui.iid_to_node.values()] == ["pic.png"]

    show(gui, "Largest Files")
    wait_largest(gui)
    assert [node.name for node in gui.largest_map.values()] == ["pic.png"]

    show(gui, "Treemap")
    gui.treemap_canvas.configure(width=640, height=440)
    gui.update_idletasks()
    gui._draw_treemap()
    wait_treemap(gui)
    leaves = [tile.node.name for tile in gui._tiles
              if not tile.node.is_dir and not getattr(tile.node, "is_aggregate", False)]
    assert leaves == ["pic.png"]

    show(gui, "File Types")
    wait_types(gui)
    def label_texts(widget):
        found = []
        for child in widget.winfo_children():
            if isinstance(child, tk.Label):
                found.append(child.cget("text"))
            found.extend(label_texts(child))
        return found

    labels = label_texts(gui.body)
    from file_utils import get_file_category
    image_label = get_file_category("pic.png", is_dir=False)["label"]
    video_label = get_file_category("big.mp4", is_dir=False)["label"]
    document_label = get_file_category("notes.txt", is_dir=False)["label"]
    assert image_label in labels
    assert video_label not in labels
    assert document_label not in labels

    folder = next(node for node in gui.root_node.children if node.is_dir)
    prompt = gui._action_scope_prompt([("row", folder)], "ZIP")
    assert "Search 'pic'" in prompt
    assert prompt.count("only changes which rows appear") == 1
    assert "ALL file types" in prompt


def test_advanced_filter_projection_and_action_scope(gui):
    from query import QueryEngine, QuerySpec
    gui._advanced_spec = QuerySpec(categories=("image", "document"),
                                   extensions=(".png",), min_size=1)
    gui._filter_index = QueryEngine(gui.root_node).project(gui._advanced_spec)
    gui._filter_index_key = gui._advanced_spec
    assert gui.file_filter == "all"
    assert gui._has_active_filter()
    show(gui, "Tree")
    assert [node.name for node in gui.iid_to_node.values()] == ["sub"]
    folder = next(node for node in gui.root_node.children if node.is_dir)
    prompt = gui._action_scope_prompt([("row", folder)], "Delete")
    assert "ALL file types" in prompt
    show(gui, "Largest Files")
    wait_largest(gui)
    assert [node.name for node in gui.largest_map.values()] == ["pic.png"]


def test_stale_largest_files_result_is_ignored_after_view_switch(gui, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original = analysis.largest_files

    def slow_query(*args, **kwargs):
        entered.set()
        release.wait(timeout=5)
        try:
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(analysis, "largest_files", slow_query)
    show(gui, "Largest Files")
    assert entered.wait(timeout=2)
    show(gui, "Tree")
    release.set()
    assert finished.wait(timeout=2)

    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        gui.update()
        time.sleep(0.01)
    assert gui.active_view == "Tree"
    assert gui.largest_tree is None


def test_stale_file_type_totals_are_ignored_after_view_switch(gui, monkeypatch):
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original = analysis.category_breakdown

    def slow_breakdown(*args, **kwargs):
        entered.set()
        release.wait(timeout=5)
        try:
            return original(*args, **kwargs)
        finally:
            finished.set()

    monkeypatch.setattr(analysis, "category_breakdown", slow_breakdown)
    show(gui, "File Types")
    assert entered.wait(timeout=2)
    show(gui, "Tree")
    release.set()
    assert finished.wait(timeout=2)

    deadline = time.monotonic() + 0.5
    while time.monotonic() < deadline:
        gui.update()
        time.sleep(0.01)
    assert gui.active_view == "Tree"
    assert gui._types_host is None


def test_slow_disk_usage_cannot_overwrite_new_scan_status(gui):
    gui.status_disk.configure(text="current")
    gui._disk_usage_ready(gui._scan_generation - 1, gui.root_node.path, "stale")
    assert gui.status_disk.cget("text") == "current"
    gui._disk_usage_ready(gui._scan_generation, gui.root_node.path, "fresh")
    assert gui.status_disk.cget("text") == "fresh"


def test_old_duplicate_result_cannot_replace_new_scan(gui):
    old_generation = gui._duplicate_generation
    gui._invalidate_duplicate_scan()
    gui._duplicate_scan_done([object()], old_generation)
    assert gui.dup_groups == []


def test_network_duplicate_read_requires_explicit_choice(gui, monkeypatch):
    import app as appmod
    gui._is_network_root = True
    prompts = []
    monkeypatch.setattr(appmod.messagebox, "askyesno",
                        lambda *args, **kwargs: (prompts.append(args[1]), False)[1])
    gui._start_duplicate_scan()
    assert prompts and "reads file contents over the network" in prompts[0]
    assert not gui._dup_running


def test_treemap_hit_testing_uses_geometry(gui):
    show(gui, "Treemap")
    gui.treemap_canvas.configure(width=640, height=440)
    gui.update_idletasks()
    gui._draw_treemap()
    wait_treemap(gui)

    tile = gui._tiles[-1]
    hit = treemap_render.hit_test(gui._tiles, tile.x + tile.w / 2, tile.y + tile.h / 2)
    assert hit is tile
    assert treemap_render.hit_test(gui._tiles, -50, -50) is None


def test_treemap_hover_populates_the_tooltip(gui):
    show(gui, "Treemap")
    gui.treemap_canvas.configure(width=640, height=440)
    gui.update_idletasks()
    gui._draw_treemap()
    wait_treemap(gui)

    tile = gui._tiles[0]
    event = type("E", (), {"x": int(tile.x + tile.w / 2), "y": int(tile.y + tile.h / 2)})()
    gui._treemap_hover(event)
    assert gui.tooltip.label.cget("text"), "tooltip had no text"
    assert gui._hover_tile is not None


def test_treemap_supports_keyboard_focus_and_item_announcement(gui):
    show(gui, "Treemap")
    gui.treemap_canvas.configure(width=640, height=440)
    gui.update_idletasks()
    gui._draw_treemap()
    wait_treemap(gui)

    # focus_set() is ignored for withdrawn/headless roots on some window
    # managers. Exercise the same FocusIn handler directly after asserting
    # that the canvas is wired to receive the event.
    assert gui.treemap_canvas.bind("<FocusIn>")
    gui._treemap_focus_in()
    assert gui._treemap_focus_tile in gui._tiles
    assert ":" in gui._treemap_focus_info.cget("text")
    assert "Map:" not in gui._treemap_focus_info.cget("text")
    assert gui.treemap_canvas.bind("<Left>") and gui.treemap_canvas.bind("<Return>")
    gui._treemap_move_focus("right")
    assert gui._treemap_focus_tile in gui._tiles


# -------------------------------------------------------------- image viewer

def test_viewer_navigates_images_and_folders(gui, gallery):
    import app as appmod
    viewer = appmod.ImageViewer(gui, str(gallery / "shot0.png"), gui.settings)
    try:
        wait_viewer(viewer)
        assert viewer.image is not None
        assert viewer.nav.count == 3

        first = viewer.nav.current
        viewer._go_next()
        assert viewer.nav.current == first  # navigation commits only after decoding succeeds
        wait_viewer(viewer)
        assert viewer.nav.current != first
        viewer._go_prev()
        wait_viewer(viewer)
        assert viewer.nav.current == first

        # walk into the subfolder and back out
        assert viewer._subfolders
        viewer._open_subfolder(list(viewer._subfolders)[0])
        wait_viewer(viewer)
        assert viewer.nav.folder.endswith("more")
        viewer._go_parent()
        wait_viewer(viewer)
        assert viewer.nav.folder.endswith("gallery")
    finally:
        viewer.destroy()


def test_viewer_rejects_navigation_and_escape_when_annotations_are_dirty(gui, gallery, monkeypatch):
    import app as appmod
    viewer = appmod.ImageViewer(gui, str(gallery / "shot0.png"), gui.settings)
    try:
        wait_viewer(viewer)
        current = viewer.nav.current
        viewer._dirty = True
        monkeypatch.setattr(viewer, "_confirm_discard", lambda: False)
        monkeypatch.setattr(appmod.messagebox, "askyesno", lambda *a, **kw: False)
        viewer._go_next()
        assert viewer.nav.current == current
        assert viewer.bind("<Escape>")
        viewer._on_close()
        assert viewer.winfo_exists()
    finally:
        viewer.destroy()


def test_viewer_modes_expose_different_tools(gui, gallery):
    import app as appmod
    import annotate
    viewer = appmod.ImageViewer(gui, str(gallery / "shot0.png"), gui.settings)
    try:
        wait_viewer(viewer)
        viewer._on_mode_change("Basic")
        viewer.update_idletasks()
        assert set(viewer.tool_buttons) == set(annotate.BASIC_TOOLS)
        assert is_packed(viewer.tools_bar), "tool bar hidden in Basic mode"

        viewer._on_mode_change("Advanced")
        viewer.update_idletasks()
        assert set(viewer.tool_buttons) == set(annotate.ADVANCED_TOOLS)
        assert "rect" in viewer.tool_buttons and "text" in viewer.tool_buttons

        viewer._on_mode_change("Off")
        viewer.update_idletasks()
        assert not is_packed(viewer.tools_bar), "tool bar left visible when Off"
    finally:
        viewer.destroy()


def test_viewer_draws_and_undoes_a_stroke(gui, gallery):
    import app as appmod
    viewer = appmod.ImageViewer(gui, str(gallery / "shot0.png"), gui.settings)
    try:
        wait_viewer(viewer)
        viewer.geometry("900x700")
        viewer.update_idletasks()
        viewer._on_mode_change("Basic")
        viewer._select_tool("pen")
        viewer.update_idletasks()

        x, y, w, h = viewer._draw_geometry
        E = lambda px, py: type("E", (), {"x": px, "y": py})()
        viewer._on_press(E(x + 10, y + 10))
        for i in range(5):
            viewer._on_drag(E(x + 10 + i * 9, y + 12 + i * 7))
        viewer._on_release(E(x + 60, y + 50))

        assert len(viewer.doc.shapes) == 1
        assert len(viewer.doc.shapes[0].points) > 1
        # coordinates are normalized, so they stay inside 0..1
        assert all(0.0 <= px <= 1.0 and 0.0 <= py <= 1.0
                   for px, py in viewer.doc.shapes[0].points)

        viewer._undo()
        assert viewer.doc.is_empty
    finally:
        viewer.destroy()


def test_viewer_annotation_actions_stay_visible_when_narrow(gui, gallery):
    """Regression: the tools filled the row first, so 'Save as…' got clipped."""
    import app as appmod
    viewer = appmod.ImageViewer(gui, str(gallery / "shot0.png"), gui.settings)
    try:
        wait_viewer(viewer)
        viewer.unbind("<Configure>")
        viewer._on_mode_change("Advanced")

        viewer._reflow_tools(viewer.TOOLS_NARROW_WIDTH + 200)
        viewer.update_idletasks()
        assert viewer._tools_narrow is False
        # both groups share the first row, actions packed first so they keep
        # their width instead of being squeezed off the edge
        assert packed_in(viewer.tools_right) == str(viewer.tools_row_a)
        assert packed_in(viewer.tools_left) == str(viewer.tools_row_a)
        assert not is_packed(viewer.tools_row_b)

        viewer._reflow_tools(viewer.TOOLS_NARROW_WIDTH - 300)
        viewer.update_idletasks()
        assert viewer._tools_narrow is True
        assert is_packed(viewer.tools_row_b), "actions row never appeared"
        assert packed_in(viewer.tools_right) == str(viewer.tools_row_b)
        assert packed_in(viewer.tools_left) == str(viewer.tools_row_a)
    finally:
        viewer.destroy()


def test_viewer_failed_decode_does_not_advance_navigation(gui, gallery, monkeypatch):
    import app as appmod
    viewer = appmod.ImageViewer(gui, str(gallery / "shot0.png"), gui.settings)
    try:
        wait_viewer(viewer)
        current = viewer.nav.current
        displayed = viewer.image
        monkeypatch.setattr(viewer, "_decode_preview", lambda path: (_ for _ in ()).throw(OSError("offline")))
        viewer._go_next()
        wait_viewer(viewer)
        assert viewer.nav.current == current
        assert viewer.image is displayed
        assert "offline" in viewer.status.cget("text")
    finally:
        viewer.destroy()


def test_viewer_save_as_uses_full_resolution_and_atomic_target(gui, tmp_path, monkeypatch):
    from PIL import Image
    import annotate
    import app as appmod

    source = tmp_path / "large.png"
    Image.new("RGB", (2600, 1000), (240, 240, 240)).save(source)
    target = tmp_path / "large_annotated.png"
    viewer = appmod.ImageViewer(gui, str(source), gui.settings)
    try:
        wait_viewer(viewer)
        assert viewer.image.width <= viewer.MAX_PREVIEW_SIZE
        viewer.doc.add(annotate.Shape(kind="line", points=[(0.1, 0.1), (0.9, 0.9)],
                                      color="#ff0000", width=0.01))
        viewer._mark_document_changed()
        monkeypatch.setattr(appmod.filedialog, "asksaveasfilename", lambda **kwargs: str(target))
        viewer._save_as()
        wait_viewer(viewer, "_saving")
        with Image.open(target) as saved:
            assert saved.size == (2600, 1000)
        assert not viewer._dirty
        assert "Saved large_annotated.png" in viewer.status.cget("text")
        assert not list(tmp_path.glob(".folderlens-annotated-*.tmp"))
    finally:
        viewer.destroy()


# ------------------------------------------------------------- duplicates view

def test_duplicates_view_renders_and_takes_selection(gui, tmp_path):
    """Duplicates is a selection view, so Zip/Delete must reach it."""
    import duplicates as dup

    show(gui, "Duplicates")
    assert gui.dup_tree is not None

    # inject a finished result rather than hashing during the test
    files = [n for n in gui.root_node.children if not n.is_dir]
    assert len(files) >= 2
    gui.dup_groups = [dup.DuplicateGroup(size=files[0].size, nodes=files[:2])]
    gui._fill_duplicates()
    gui.update_idletasks()

    groups = gui.dup_tree.get_children()
    assert len(groups) == 1
    rows = gui.dup_tree.get_children(groups[0])
    assert len(rows) == 2

    gui.dup_tree.selection_set(rows[0])
    selection = gui._top_level_selection()
    assert len(selection) == 1
    assert selection[0][1] in files


def test_duplicates_is_offered_as_a_view(gui):
    assert "Duplicates" in gui.VIEWS


def test_treemap_keeps_an_exportable_image(gui):
    show(gui, "Treemap")
    gui.treemap_canvas.configure(width=520, height=380)
    gui.update_idletasks()
    gui._draw_treemap()
    wait_treemap(gui)
    assert gui._treemap_image is not None
    assert gui._treemap_image.size == (gui.treemap_canvas.winfo_width(),
                                       gui.treemap_canvas.winfo_height())


def test_hover_does_not_rerender_the_treemap(gui):
    """Regression: the highlight used to be baked into the image, so every
    mouse move across a tile boundary re-composited the whole map."""
    show(gui, "Treemap")
    gui.treemap_canvas.configure(width=640, height=440)
    gui.update_idletasks()
    gui._draw_treemap()
    wait_treemap(gui)

    before = gui._treemap_photo
    assert gui._tiles
    for tile in gui._tiles[:10]:
        event = type("E", (), {"x": int(tile.x + tile.w / 2),
                               "y": int(tile.y + tile.h / 2)})()
        gui._treemap_hover(event)

    assert gui._treemap_photo is before, "the treemap image was rebuilt on hover"
    assert gui._highlight_id is not None, "no highlight outline was drawn"


# ------------------------------------------------------------ start screen

def test_start_screen_offers_places_before_any_scan(gui):
    """With nothing scanned the app used to show a bare line of text; it now
    offers somewhere to start."""
    gui.root_node = None
    show(gui, "Tree")

    def collect(widget):
        texts = []
        def visit(widget):
            for child in widget.winfo_children():
                try:
                    text = child.cget("text")
                except tk.TclError:
                    text = ""
                if text:
                    texts.append(str(text))
                visit(child)
        visit(widget)
        return " ".join(texts)

    joined = collect(gui.body)
    assert "Where should we look" in joined
    assert "Browse" in joined
    # Cards arrive after filesystem checks on a worker; Browse stays usable.
    expected = {place.label for place in locations.start_places()}
    deadline = time.monotonic() + 15
    while not any(label in joined for label in expected) and time.monotonic() < deadline:
        gui.update()
        time.sleep(0.02)
        joined = collect(gui.body)
    assert any(label in joined for label in expected)


def test_breadcrumbs_are_clickable_prefixes(gui, tmp_path):
    deep = tmp_path / "one" / "two" / "three"
    deep.mkdir(parents=True)
    gui._set_breadcrumbs(str(deep))
    gui.update_idletasks()

    labels = []
    for child in gui.crumb_bar.winfo_children():
        try:
            labels.append(str(child.cget("text")))
        except tk.TclError:
            pass
    assert "three" in labels
    assert "two" in labels and "one" in labels


def test_breadcrumbs_empty_state(gui):
    gui._set_breadcrumbs("")
    gui.update_idletasks()
    texts = [str(c.cget("text")) for c in gui.crumb_bar.winfo_children()]
    assert any("No folder" in t for t in texts)


def test_sort_header_shows_the_direction(gui):
    show(gui, "Tree")
    gui.sort_key, gui.sort_reverse = "size", True
    show(gui, "Tree")
    assert "↓" in gui.tree.heading("size")["text"]

    gui.sort_reverse = False
    show(gui, "Tree")
    assert "↑" in gui.tree.heading("size")["text"]


def test_copy_path_puts_the_selection_on_the_clipboard(gui):
    show(gui, "Tree")
    first = gui.tree.get_children()[0]
    gui.tree.selection_set(first)
    node = gui.iid_to_node[first]

    gui._copy_path()
    gui.update_idletasks()
    assert gui.clipboard_get() == node.path


def test_shortcuts_are_documented():
    import app as appmod
    sections = dict(appmod.FolderLensApp.SHORTCUTS)
    assert sections, "no shortcut help defined"
    keys = [k for rows in sections.values() for k, _ in rows]
    for expected in ("F5", "Ctrl+F", "Delete"):
        assert expected in keys, f"{expected} is bound but undocumented"
