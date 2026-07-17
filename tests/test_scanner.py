import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import FolderScanner, QuickScanner


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


def run_scan(path):
    scanner = FolderScanner()
    result_holder = [None]
    error_holder = [None]
    done = threading.Event()

    def on_complete(result):
        result_holder[0] = result
        done.set()

    def on_error(error):
        error_holder[0] = error
        done.set()

    scanner.scan(str(path), on_complete=on_complete, on_error=on_error)
    assert done.wait(timeout=30), "scan did not finish in time"
    return result_holder[0], error_holder[0]


def test_scan_sizes(sample_tree):
    result, error = run_scan(sample_tree)
    assert error is None
    assert result is not None

    by_name = {item.name: item for item in result.items}
    assert by_name["file_a.txt"].size == 100
    assert by_name["file_b.bin"].size == 250
    assert by_name["subdir"].size == 75
    assert by_name["subdir"].is_directory
    # nested.txt + deep.txt + the "deeper" dir itself
    assert by_name["subdir"].item_count == 3

    assert result.total_size == 425
    assert result.total_items == 3
    assert result.errors == []


def test_scan_missing_folder(tmp_path):
    result, error = run_scan(tmp_path / "does_not_exist")
    assert result is None
    assert "not found" in error.lower()


def test_scan_file_not_folder(tmp_path):
    target = tmp_path / "plain.txt"
    target.write_text("hello")
    result, error = run_scan(target)
    assert result is None
    assert "not a folder" in error.lower()


def test_scan_empty_folder(tmp_path):
    result, error = run_scan(tmp_path)
    assert error is None
    assert result.total_items == 0
    assert result.total_size == 0


def test_quick_scanner(sample_tree):
    items = QuickScanner().scan_first_level(str(sample_tree))
    names = {item.name for item in items}
    assert names == {"file_a.txt", "file_b.bin", "subdir"}

    dirs = [i for i in items if i.is_directory]
    assert len(dirs) == 1
    assert dirs[0].size == 0
