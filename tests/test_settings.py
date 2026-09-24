"""The settings file must survive an interrupted or failed replacement."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app


def test_legacy_settings_migrate_to_versioned_atomic_save(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    target.write_text('{"last_folder": "C:/old", "file_filter": "image"}', encoding="utf-8")
    monkeypatch.setattr(app, "_settings_file", lambda: str(target))
    settings = app.AppSettings()
    assert settings.last_folder == "C:/old"
    assert settings.file_filter == "image"
    settings.save()
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 1
    assert saved["last_folder"] == "C:/old"


def test_failed_settings_replacement_preserves_old_file(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    target.write_text('{"file_filter": "all"}', encoding="utf-8")
    monkeypatch.setattr(app, "_settings_file", lambda: str(target))
    settings = app.AppSettings()
    settings.file_filter = "image"
    monkeypatch.setattr(app.os, "replace", lambda source, destination: (_ for _ in ()).throw(
        OSError("simulated failure")))
    settings.save()
    assert target.read_text(encoding="utf-8") == '{"file_filter": "all"}'
    assert list(tmp_path.iterdir()) == [target]
