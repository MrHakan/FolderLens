import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import trash


def test_missing_file_is_reported_not_raised(tmp_path):
    moved, message = trash.send_to_trash(str(tmp_path / "nope.txt"))
    assert moved is False
    assert "not found" in message.lower()


def test_unique_name_avoids_collisions(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "a.1.txt").write_text("x")
    name = trash._unique_name(str(tmp_path), "a.txt")
    assert name not in ("a.txt", "a.1.txt")
    assert not os.path.exists(tmp_path / name)


def test_xdg_trash_moves_the_file(tmp_path, monkeypatch):
    if sys.platform == "win32":
        return          # the shell API is exercised on Windows, not here
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()

    victim = tmp_path / "victim.txt"
    victim.write_text("delete me")

    moved, message = trash.send_to_trash(str(victim))
    assert moved is True, message
    assert not victim.exists(), "file was not moved out of the way"

    trashed = tmp_path / "data" / "Trash" / "files" / "victim.txt"
    assert trashed.exists(), "file did not land in the trash"
    assert trashed.read_text() == "delete me"

    info = tmp_path / "data" / "Trash" / "info" / "victim.txt.trashinfo"
    assert info.exists(), "no restore metadata written"
    assert "Path=" in info.read_text()


def test_second_file_with_the_same_name_does_not_overwrite(tmp_path, monkeypatch):
    if sys.platform == "win32":
        return
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()

    for content in ("first", "second"):
        folder = tmp_path / content
        folder.mkdir()
        target = folder / "same.txt"
        target.write_text(content)
        assert trash.send_to_trash(str(target))[0]

    files = os.listdir(tmp_path / "data" / "Trash" / "files")
    assert len(files) == 2, f"a file was overwritten in the trash: {files}"


def test_is_supported_reports_a_bool():
    assert isinstance(trash.is_supported(), bool)
