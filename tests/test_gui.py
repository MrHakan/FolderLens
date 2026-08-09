"""GUI regression tests.

These cover crashes that pure-logic tests can't reach, so they need a real Tk
display. They skip themselves cleanly when tkinter or a display is missing
(e.g. a plain CI runner without xvfb), and never block on a mainloop.
"""
import os
import sys
import threading

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
    (tmp_path / "big.mp4").write_bytes(b"v" * 60000)
    (tmp_path / "notes.txt").write_bytes(b"t" * 800)
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "pic.png").write_bytes(b"p" * 9000)
    return tmp_path


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
    """Regression: hit-testing resolves to the topmost canvas item, so tiles
    with a name label swallowed the tooltip/click exactly where the pointer
    naturally lands."""
    show(gui, "Treemap")
    gui.treemap_canvas.configure(width=820, height=520)
    gui.update_idletasks()
    gui._draw_treemap()

    labeled = [t for t in gui._tile_items.values() if t.w > 46 and t.h > 16]
    assert labeled, "expected at least one tile large enough to be labelled"

    for tile in labeled:
        hit = gui.treemap_canvas.find_closest(int(tile.x + 8), int(tile.y + 6))
        assert hit and gui._tile_items.get(hit[0]) is not None


def test_treemap_resize_is_debounced(gui):
    """A window resize fires a burst of <Configure> events; they must collapse
    into a single re-layout instead of one per pixel."""
    show(gui, "Treemap")
    gui.treemap_canvas.configure(width=800, height=500)
    gui.update_idletasks()

    calls = []
    original = gui._draw_treemap
    gui._draw_treemap = lambda: calls.append(1)
    try:
        for width in range(700, 740):
            gui.treemap_canvas.configure(width=width)
            gui.update_idletasks()
        assert len(calls) == 0, "redrew synchronously during the resize burst"
        assert gui._treemap_redraw_after is not None, "no redraw was scheduled"
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
