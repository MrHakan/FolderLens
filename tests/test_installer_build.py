from pathlib import Path

import pytest

from installer import build_installer
from version import VERSION


def test_installer_build_uses_the_application_version_and_script(tmp_path):
    compiler = tmp_path / "ISCC.exe"
    compiler.write_bytes(b"compiler placeholder")
    output_dir = tmp_path / "out"
    captured = {}

    def fake_run(command, cwd, check):
        captured.update(command=command, cwd=cwd, check=check)
        target = Path(command[2][2:]) / f"FolderLens_Setup_{VERSION}.exe"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"setup")

    result = build_installer.build(output_dir, compiler, run=fake_run)
    assert result.name == f"FolderLens_Setup_{VERSION}.exe"
    assert captured["check"] is True
    assert f"/DMyAppVersion={VERSION}" in captured["command"]
    assert str(build_installer.ROOT / "installer" / "FolderLens_Setup.iss") in captured["command"]


def test_installer_build_fails_clearly_when_iscc_is_unavailable(tmp_path, monkeypatch):
    monkeypatch.setenv("ISCC_PATH", str(tmp_path / "missing.exe"))
    monkeypatch.setenv("PATH", "")
    monkeypatch.setenv("ProgramFiles(x86)", str(tmp_path / "missing-x86"))
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "missing-programfiles"))
    monkeypatch.setattr(build_installer.shutil, "which", lambda *_args: None)
    with pytest.raises(FileNotFoundError, match="Inno Setup 6 compiler"):
        build_installer.find_compiler()
