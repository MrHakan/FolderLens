"""The settings file must survive an interrupted or failed replacement."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app


def test_legacy_settings_migrate_to_versioned_atomic_save(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    legacy = {
        "row_size": "large",
        "preview_enabled": False,
        "dark_mode": False,
        "last_folder": "C:/old",
        "view": "Treemap",
        "file_filter": "image",
        "treemap_thumbnails": False,
        "list_thumbnails": False,
        "peek_preview": False,
        "annotation_mode": "Advanced",
        "use_recycle_bin": False,
    }
    target.write_text(json.dumps(legacy), encoding="utf-8")
    monkeypatch.setattr(app, "_settings_file", lambda: str(target))
    settings = app.AppSettings()
    assert settings.row_size == "large"
    assert settings.preview_enabled is False
    assert settings.dark_mode is False
    assert settings.last_folder == "C:/old"
    assert settings.view == "Treemap"
    assert settings.file_filter == "image"
    assert settings.treemap_thumbnails is False
    assert settings.list_thumbnails is False
    assert settings.peek_preview is False
    assert settings.annotation_mode == "Advanced"
    assert settings.use_recycle_bin is False
    settings.save()
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["schema_version"] == 2
    # Every legacy value survives; 4.0 adds its own keys with safe defaults.
    assert {key: saved[key] for key in legacy} == legacy
    assert saved["measure_on_disk"] is False
    assert saved["filter_presets"] == {}


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


def test_explore_view_setting_loads_and_round_trips(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    target.write_text('{"view": "Explore"}', encoding="utf-8")
    monkeypatch.setattr(app, "_settings_file", lambda: str(target))

    settings = app.AppSettings()
    assert settings.view == "Explore"
    settings.save()
    assert json.loads(target.read_text(encoding="utf-8"))["view"] == "Explore"


def test_filter_presets_round_trip_and_invalid_entries_are_dropped(tmp_path, monkeypatch):
    target = tmp_path / "settings.json"
    target.write_text(json.dumps({
        "schema_version": 2,
        "measure_on_disk": True,
        "filter_presets": {
            "Big photos": {"categories": ["image"], "min_mib": "20"},
            "Broken": {"categories": ["not-a-type"]},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(app, "_settings_file", lambda: str(target))
    settings = app.AppSettings()
    assert settings.measure_on_disk is True
    assert list(settings.filter_presets) == ["Big photos"]
    settings.save()
    saved = json.loads(target.read_text(encoding="utf-8"))
    assert saved["filter_presets"]["Big photos"]["min_mib"] == "20"
    assert "Broken" not in saved["filter_presets"]
