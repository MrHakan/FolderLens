import os
import sys
import json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from updater import Updater
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
        ],
        "zipball_url": "https://example/source.zip",
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def read(self):
            return json.dumps(release).encode()

    monkeypatch.setattr(updater_module, "urlopen", lambda *a, **kw: Response())
    service = Updater()
    monkeypatch.setattr(service, "installation_type", lambda: "onefile")
    available, info, error = service.check_for_updates()
    assert available and error is None
    assert info.download_url == "https://example/FolderLens.exe"
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
