import os
import threading
import zipfile

import pytest

from file_actions import ActionCancelled, create_zip, remove_selected, validate_selection
from scanner import TreeScanner


def scan(path):
    scanner = TreeScanner()
    finished = threading.Event()
    result = {}
    scanner.scan(
        str(path),
        on_complete=lambda root, errors, duration: (result.update(root=root, errors=errors), finished.set()),
        on_error=lambda message: (result.update(error=message), finished.set()),
    )
    assert finished.wait(20), "scan did not finish"
    assert "error" not in result, result.get("error")
    return result["root"]


def test_validate_selection_compares_contents_and_current_totals(tmp_path):
    folder = tmp_path / "picked"
    folder.mkdir()
    (folder / "one.txt").write_bytes(b"123")
    sub = folder / "sub"
    sub.mkdir()
    (sub / "two.txt").write_bytes(b"4567")

    node = scan(tmp_path).children[0]
    result = validate_selection([node])
    assert result.valid
    assert result.items == 4
    assert result.files == 2
    assert result.logical_bytes == 7

    (sub / "two.txt").write_bytes(b"different size")
    result = validate_selection([node])
    assert not result.valid
    assert any("Size changed" in issue for issue in result.issues)


def test_validate_selection_detects_added_entries_even_when_sizes_match(tmp_path):
    folder = tmp_path / "picked"
    folder.mkdir()
    (folder / "one.txt").write_bytes(b"123")
    node = scan(tmp_path).children[0]
    (folder / "new.txt").write_bytes(b"123")

    result = validate_selection([node])
    assert not result.valid
    assert any("Added since scan" in issue for issue in result.issues)


def test_create_zip_preserves_archive_paths_and_checks_freshness(tmp_path):
    folder = tmp_path / "picked"
    folder.mkdir()
    (folder / "one.txt").write_text("hello", encoding="utf-8")
    sub = folder / "sub"
    sub.mkdir()
    (sub / "two.txt").write_text("world", encoding="utf-8")
    node = scan(tmp_path).children[0]
    destination = tmp_path / "selected.zip"

    result = create_zip([node], str(destination))
    assert result.files_written == 2
    assert result.errors == []
    with zipfile.ZipFile(destination) as archive:
        assert archive.namelist() == ["picked/one.txt", "picked/sub/two.txt"]
        assert archive.read("picked/one.txt") == b"hello"

    destination.unlink()
    (folder / "new.txt").write_text("not scanned", encoding="utf-8")
    with pytest.raises(ValueError, match="changed since the scan"):
        create_zip([node], str(destination))
    assert not destination.exists()


def test_create_zip_clamps_unrepresentable_pre_1980_timestamp(tmp_path):
    folder = tmp_path / "picked"
    folder.mkdir()
    source = folder / "old.txt"
    source.write_text("old file", encoding="utf-8")
    old_timestamp = 1  # DOS/ZIP timestamps start at 1980.
    os.utime(source, (old_timestamp, old_timestamp))
    node = scan(tmp_path).children[0]
    destination = tmp_path / "old.zip"

    result = create_zip([node], str(destination))

    assert result.files_written == 1
    with zipfile.ZipFile(destination) as archive:
        assert archive.getinfo("picked/old.txt").date_time == (1980, 1, 1, 0, 0, 0)


def test_cancelled_zip_leaves_existing_destination_untouched(tmp_path):
    folder = tmp_path / "picked"
    folder.mkdir()
    (folder / "one.txt").write_text("hello", encoding="utf-8")
    node = scan(tmp_path).children[0]
    destination = tmp_path / "selected.zip"
    destination.write_bytes(b"previous archive")
    cancel = threading.Event()
    cancel.set()

    result = create_zip([node], str(destination), cancel_event=cancel)
    assert result.cancelled
    assert destination.read_bytes() == b"previous archive"


def test_delete_rejects_changed_or_reparse_selection(tmp_path):
    folder = tmp_path / "picked"
    folder.mkdir()
    victim = folder / "victim.txt"
    victim.write_text("keep", encoding="utf-8")
    node = scan(tmp_path).children[0]
    victim.write_text("changed", encoding="utf-8")

    with pytest.raises(ValueError, match="changed or contains"):
        remove_selected(node, recycle=False)
    assert victim.exists()


def test_delete_blocks_reparse_points(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "secret.txt").write_text("keep", encoding="utf-8")
    link = tmp_path / "link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlink creation is unavailable")

    root = scan(tmp_path)
    node = next(child for child in root.children if child.name == "link")
    with pytest.raises(ValueError, match="reparse point"):
        remove_selected(node, recycle=False)
    assert (target / "secret.txt").exists()
