"""GUI regression tests.

These cover crashes that pure-logic tests can't reach, so they need a real Tk
display. They skip themselves cleanly when tkinter or a display is missing
(e.g. a plain CI runner without xvfb), and never block on a mainloop.
"""
import os
import sys
import threading
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

tk = pytest.importorskip("tkinter", reason="tkinter not available")
pytest.importorskip("customtkinter", reason="customtkinter not installed")

if sys.platform != "win32" and not os.environ.get("DISPLAY"):
    pytest.skip("no display available", allow_module_level=True)

try:
    _probe = tk.Tk()
    _probe.destroy()
except Exception as exc:  # pragma: no cover - environment dependent
    pytest.skip(f"cannot open a Tk display: {exc}", allow_module_level=True)

import treemap_render
from scanner import TreeScanner


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
    assert done.wait(timeout=30), "scan did not finish"
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


@pytest.fixture
def gui(tmp_path, sample_tree, monkeypatch):
    """A FolderLensApp with an already-scanned tree and isolated settings."""
    # keep the test off the developer's real settings file
    monkeypatch.setenv("APPDATA", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))

    import app as appmod

    root = scan_sync(sample_tree)
    assert root is not None

    win = appmod.FolderLensApp(initial_path=None)   # no scan kicked off
    win.geometry("1000x700+0+0")
    win.root_node = root
    try:
        yield win
    finally:
        try:
            win.destroy()
        except tk.TclError:
            pass


def show(win, view):
    win.active_view = view
    win.view_switch.set(view)
    win._render_active_view()
    win.update_idletasks()


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


def test_treemap_labels_are_hit_testable(gui):
    """Regression: hovering a tile where its name label is drawn used to lose
    the tooltip. Hit-testing is geometric now, so the label area belongs to its
    own tile like any other pixel."""
    show(gui, "Treemap")
    gui.treemap_canvas.configure(width=820, height=520)
    gui.update_idletasks()
    gui._draw_treemap()

    labeled = [t for t in gui._tiles if t.w > 54 and t.h > 18]
    assert labeled, "expected at least one tile large enough to be labelled"

    for tile in labeled:
        hit = treemap_render.hit_test(gui._tiles, tile.x + 8, tile.y + 6)
        assert hit is not None, "lost the hit over a tile label"


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

    assert gui._tiles, "no tiles laid out"
    assert gui._treemap_photo is not None, "treemap image was not produced"


def test_treemap_hit_testing_uses_geometry(gui):
    show(gui, "Treemap")
    gui.treemap_canvas.configure(width=640, height=440)
    gui.update_idletasks()
    gui._draw_treemap()

    tile = gui._tiles[-1]
    hit = treemap_render.hit_test(gui._tiles, tile.x + tile.w / 2, tile.y + tile.h / 2)
    assert hit is tile
    assert treemap_render.hit_test(gui._tiles, -50, -50) is None


def test_treemap_hover_populates_the_tooltip(gui):
    show(gui, "Treemap")
    gui.treemap_canvas.configure(width=640, height=440)
    gui.update_idletasks()
    gui._draw_treemap()

    tile = gui._tiles[0]
    event = type("E", (), {"x": int(tile.x + tile.w / 2), "y": int(tile.y + tile.h / 2)})()
    gui._treemap_hover(event)
    assert gui.tooltip.label.cget("text"), "tooltip had no text"
    assert gui._hover_tile is not None


# -------------------------------------------------------------- image viewer

def test_viewer_navigates_images_and_folders(gui, gallery):
    import app as appmod
    viewer = appmod.ImageViewer(gui, str(gallery / "shot0.png"), gui.settings)
    try:
        viewer.update_idletasks()
        assert viewer.image is not None
        assert viewer.nav.count == 3

        first = viewer.nav.current
        viewer._go_next()
        assert viewer.nav.current != first
        viewer._go_prev()
        assert viewer.nav.current == first

        # walk into the subfolder and back out
        assert viewer._subfolders
        viewer._open_subfolder(list(viewer._subfolders)[0])
        assert viewer.nav.folder.endswith("more")
        viewer._go_parent()
        assert viewer.nav.folder.endswith("gallery")
    finally:
        viewer.destroy()


def test_viewer_modes_expose_different_tools(gui, gallery):
    import app as appmod
    import annotate
    viewer = appmod.ImageViewer(gui, str(gallery / "shot0.png"), gui.settings)
    try:
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

    before = gui._treemap_photo
    assert gui._tiles
    for tile in gui._tiles[:10]:
        event = type("E", (), {"x": int(tile.x + tile.w / 2),
                               "y": int(tile.y + tile.h / 2)})()
        gui._treemap_hover(event)

    assert gui._treemap_photo is before, "the treemap image was rebuilt on hover"
    assert gui._highlight_id is not None, "no highlight outline was drawn"
