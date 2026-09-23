import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import TreeScanner, FolderScanner, QuickScanner, Node, is_network_path


@pytest.fixture
def sample_tree(tmp_path):
    (tmp_path / "file_a.txt").write_bytes(b"x" * 100)
    (tmp_path / "file_b.bin").write_bytes(b"y" * 250)

    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "nested.txt").write_bytes(b"z" * 50)

    deep = sub / "deeper"
    deep.mkdir()
    (deep / "deep.txt").write_bytes(b"w" * 25)

    return tmp_path


def run_tree_scan(path):
    scanner = TreeScanner()
    holder = {"root": None, "errors": None, "error": None}
    done = threading.Event()

    def on_complete(root, errors, scan_time):
        holder["root"] = root
        holder["errors"] = errors
        done.set()

    def on_error(error):
        holder["error"] = error
        done.set()

    scanner.scan(str(path), on_complete=on_complete, on_error=on_error)
    assert done.wait(timeout=90), "scan did not finish in time (machine under load?)"
    return holder


def run_flat_scan(path):
    scanner = FolderScanner()
    holder = {"result": None, "error": None}
    done = threading.Event()

    def on_complete(result):
        holder["result"] = result
        done.set()

    def on_error(error):
        holder["error"] = error
        done.set()

    scanner.scan(str(path), on_complete=on_complete, on_error=on_error)
    assert done.wait(timeout=90), "scan did not finish in time (machine under load?)"
    return holder["result"], holder["error"]


def test_tree_scan_builds_full_tree(sample_tree):
    holder = run_tree_scan(sample_tree)
    assert holder["error"] is None
    root = holder["root"]
    assert root is not None
    assert root.is_dir

    by_name = {c.name: c for c in root.children}
    assert set(by_name) == {"file_a.txt", "file_b.bin", "subdir"}
    assert by_name["file_a.txt"].size == 100
    assert by_name["file_b.bin"].size == 250

    subdir = by_name["subdir"]
    assert subdir.is_dir
    assert subdir.size == 75
    # nested.txt + deeper dir + deep.txt
    assert subdir.item_count == 3

    sub_children = {c.name: c for c in subdir.children}
    assert set(sub_children) == {"nested.txt", "deeper"}
    assert sub_children["deeper"].size == 25
    assert sub_children["deeper"].children[0].name == "deep.txt"
    assert sub_children["deeper"].children[0].parent is sub_children["deeper"]

    assert root.size == 425
    # file_a + file_b + subdir + nested.txt + deeper + deep.txt
    assert root.item_count == 6
    assert holder["errors"] == []
    assert by_name["file_a.txt"].modified_date > 0


def test_tree_scan_sorted_children(sample_tree):
    root = run_tree_scan(sample_tree)["root"]
    by_size = root.sorted_children("size", reverse=True)
    assert [c.name for c in by_size] == ["file_b.bin", "file_a.txt", "subdir"]

    by_name = root.sorted_children("name", reverse=False)
    assert [c.name for c in by_name] == ["file_a.txt", "file_b.bin", "subdir"]


def test_tree_scan_missing_folder(tmp_path):
    holder = run_tree_scan(tmp_path / "does_not_exist")
    assert holder["root"] is None
    assert "not found" in holder["error"].lower()


def test_tree_scan_file_not_folder(tmp_path):
    target = tmp_path / "plain.txt"
    target.write_text("hello")
    holder = run_tree_scan(target)
    assert holder["root"] is None
    assert "not a folder" in holder["error"].lower()


def test_tree_scan_empty_folder(tmp_path):
    holder = run_tree_scan(tmp_path)
    root = holder["root"]
    assert holder["error"] is None
    assert root.children == []
    assert root.size == 0
    assert root.item_count == 0


def test_flat_scan_sizes(sample_tree):
    result, error = run_flat_scan(sample_tree)
    assert error is None

    by_name = {item.name: item for item in result.items}
    assert by_name["file_a.txt"].size == 100
    assert by_name["subdir"].size == 75
    assert by_name["subdir"].is_directory
    assert by_name["subdir"].item_count == 3

    assert result.total_size == 425
    assert result.total_items == 3
    assert result.errors == []


def test_flat_scan_missing_folder(tmp_path):
    result, error = run_flat_scan(tmp_path / "does_not_exist")
    assert result is None
    assert "not found" in error.lower()


def test_quick_scanner(sample_tree):
    items = QuickScanner().scan_first_level(str(sample_tree))
    names = {item.name for item in items}
    assert names == {"file_a.txt", "file_b.bin", "subdir"}

    dirs = [i for i in items if i.is_directory]
    assert len(dirs) == 1
    assert dirs[0].size == 0


def test_node_uses_slots_to_keep_the_tree_small():
    """One Node exists per file on disk, so the per-instance __dict__ matters."""
    node = Node(path="/x/a.txt", name="a.txt", is_dir=False, size=1)
    assert not hasattr(node, "__dict__"), "Node grew a __dict__ again"
    assert hasattr(Node, "__slots__")


def test_files_share_one_empty_children_container():
    """Files can never have children; giving each its own list wasted memory."""
    a = Node(path="/x/a.txt", name="a.txt", is_dir=False)
    b = Node(path="/x/b.txt", name="b.txt", is_dir=False)
    assert a.children is b.children
    assert len(a.children) == 0

    # directories still get their own mutable list
    d1 = Node(path="/x/d1", name="d1", is_dir=True)
    d2 = Node(path="/x/d2", name="d2", is_dir=True)
    assert d1.children is not d2.children
    d1.children.append(a)
    assert d2.children == []


def test_network_paths_use_a_smaller_worker_pool(tmp_path):
    assert is_network_path(r"\\server\share\work")
    assert not is_network_path(str(tmp_path))
    assert TreeScanner.worker_limit(r"\\server\share\work") == min(
        TreeScanner.MAX_WORKERS, TreeScanner.NETWORK_WORKERS)
    assert TreeScanner.worker_limit(str(tmp_path)) == TreeScanner.MAX_WORKERS


def test_stalled_old_scan_cannot_complete_or_block_new_scan(tmp_path, monkeypatch):
    """A stuck share call may outlive cancellation; its event remains private."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "new.txt").write_text("new")
    scanner = TreeScanner()
    entered = threading.Event()
    release = threading.Event()
    done = threading.Event()
    results = []
    original = scanner._read_directory

    def stalled(node, errors, on_progress=None, work_queue=None, session=None):
        if node.path == str(first):
            entered.set()
            assert release.wait(timeout=10)
        return original(node, errors, on_progress, work_queue, session)

    monkeypatch.setattr(scanner, "_read_directory", stalled)
    scanner.scan(str(first), on_complete=lambda *args: results.append("old"))
    old_thread = scanner._current_thread
    assert entered.wait(timeout=5)
    scanner.scan(str(second), on_complete=lambda *args: (results.append("new"), done.set()))
    assert done.wait(timeout=5), "a blocked old scan delayed the new one"
    release.set()
    old_thread.join(timeout=5)
    scanner._current_thread.join(timeout=5)
    assert not old_thread.is_alive()
    assert results == ["new"]
    assert not scanner.is_scanning
