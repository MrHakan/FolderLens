import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import TreeScanner, FolderScanner, QuickScanner


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
    assert done.wait(timeout=30), "scan did not finish in time"
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
    assert done.wait(timeout=30), "scan did not finish in time"
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
