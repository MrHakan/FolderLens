import os
import sys
import json
import hashlib

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from updater import UpdateInfo, Updater
import updater as updater_module


def test_compare_equal():
    assert Updater.compare_versions("1.0.0", "1.0.0") == 0


def test_compare_greater():
    assert Updater.compare_versions("1.1.0", "1.0.0") == 1
    assert Updater.compare_versions("2.0.0", "1.9.9") == 1
    assert Updater.compare_versions("1.0.10", "1.0.9") == 1


def test_compare_less():
    assert Updater.compare_versions("1.0.0", "1.0.1") == -1


def test_compare_v_prefix():
    assert Updater.compare_versions("v1.1.0", "1.0.0") == 1
    assert Updater.compare_versions("V1.0.0", "v1.0.0") == 0


def test_compare_different_lengths():
    assert Updater.compare_versions("1.0", "1.0.0") == 0
    assert Updater.compare_versions("1.0.0.1", "1.0.0") == 1


def test_compare_suffixed_parts():
    assert Updater.compare_versions("1.0.1rc1", "1.0.0") == 1


def test_update_selects_exact_onefile_asset_and_never_source_archive(monkeypatch):
    release = {
        "tag_name": "v4.0.0",
        "assets": [
            {"name": "FolderLens-4.0.0-win64.zip", "browser_download_url": "https://example/dir.zip"},
            {"name": "FolderLens.exe", "browser_download_url": "https://example/FolderLens.exe"},
            {"name": "SHA256SUMS", "browser_download_url": "https://example/SHA256SUMS"},
        ],
        "zipball_url": "https://example/source.zip",
    }
    digest = hashlib.sha256(b"exe").hexdigest()

    class Response:
        def __init__(self, data):
            self.data = data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size=None):
            return self.data

    def fake_urlopen(request, timeout):
        if request.full_url.endswith("SHA256SUMS"):
            return Response(f"{digest}  FolderLens.exe\n".encode())
        return Response(json.dumps(release).encode())

    monkeypatch.setattr(updater_module, "urlopen", fake_urlopen)
    service = Updater()
    monkeypatch.setattr(service, "installation_type", lambda: "onefile")
    available, info, error = service.check_for_updates()
    assert available and error is None
    assert info.download_url == "https://example/FolderLens.exe"
    assert info.sha256 == digest
    assert info.release_url.endswith("/releases/tag/v4.0.0")

    monkeypatch.setattr(service, "installation_type", lambda: "onedir")
    available, info, error = service.check_for_updates()
    assert available and error is None and info.download_url is None

    release["assets"] = []
    monkeypatch.setattr(service, "installation_type", lambda: "onefile")
    available, info, error = service.check_for_updates()
    assert available and error is None and info.download_url is None


def test_updater_refuses_partial_onedir_or_archive_replacement(monkeypatch):
    service = Updater()
    monkeypatch.setattr(service, "installation_type", lambda: "onedir")
    assert service.apply_update("release.zip")[0] is False
    monkeypatch.setattr(service, "installation_type", lambda: "onefile")
    assert service.apply_update("release.zip")[0] is False


def test_installed_layout_detects_onedir_runtime_bundle(monkeypatch, tmp_path):
    monkeypatch.setattr(updater_module.sys, "frozen", True, raising=False)
    monkeypatch.setattr(updater_module.sys, "executable", str(tmp_path / "FolderLens.exe"))
    assert Updater.installation_type() == "onefile"
    (tmp_path / "_internal").mkdir()
    assert Updater.installation_type() == "onedir"


def test_update_download_checks_hash_and_removes_corrupt_file(monkeypatch, tmp_path):
    payload = b"downloaded exe"

    class Response:
        headers = {"content-length": str(len(payload))}

        def __init__(self):
            self.reads = 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self, size):
            self.reads += 1
            return payload if self.reads == 1 else b""

    monkeypatch.setattr(updater_module, "urlopen", lambda *a, **kw: Response())
    directories = iter([tmp_path / "bad", tmp_path / "good"])

    def temporary_dir(prefix):
        folder = next(directories)
        folder.mkdir()
        return str(folder)

    monkeypatch.setattr(updater_module.tempfile, "mkdtemp", temporary_dir)
    info = UpdateInfo("4.0.0", "https://example/FolderLens.exe", "", "", sha256="0" * 64)
    assert Updater().download_update(info)[0] is False
    assert not (tmp_path / "bad").exists()

    info.sha256 = hashlib.sha256(payload).hexdigest()
    success, path, error = Updater().download_update(info)
    assert success and error is None
    assert path == str(tmp_path / "good" / "FolderLens.exe")
    assert (tmp_path / "good" / "FolderLens.exe").read_bytes() == payload


def test_apply_refuses_executable_changed_after_download(monkeypatch, tmp_path):
    downloaded = tmp_path / "FolderLens.exe"
    downloaded.write_bytes(b"modified after download")
    service = Updater()
    monkeypatch.setattr(service, "installation_type", lambda: "onefile")
    monkeypatch.setattr(updater_module.sys, "frozen", True, raising=False)
    success, error = service.apply_update(str(downloaded), "0" * 64)
    assert success is False
    assert "changed" in error
