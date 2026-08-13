import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import locations
from locations import Place


# ------------------------------------------------------------------- places

def test_home_places_only_lists_folders_that_exist(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Downloads").mkdir(parents=True)
    (home / "Pictures").mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    labels = [p.label for p in locations.home_places()]
    assert "Home" in labels
    assert "Downloads" in labels and "Pictures" in labels
    assert "Desktop" not in labels, "offered a folder that does not exist"


def test_home_places_point_at_real_paths(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "Documents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))

    for place in locations.home_places():
        assert os.path.isdir(place.path)
        assert place.icon and place.label


def test_usage_is_only_gathered_when_asked(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    assert all(p.total == 0 for p in locations.home_places(include_usage=False))
    assert any(p.total > 0 for p in locations.home_places(include_usage=True))


def test_drives_are_marked_and_measured():
    found = locations.drives()
    assert found, "expected at least one drive/root"
    for place in found:
        assert place.is_drive
        assert os.path.isdir(place.path)
        assert place.total > 0


def test_start_places_puts_drives_last():
    places = locations.start_places()
    assert places
    drive_positions = [i for i, p in enumerate(places) if p.is_drive]
    folder_positions = [i for i, p in enumerate(places) if not p.is_drive]
    if drive_positions and folder_positions:
        assert min(drive_positions) > max(folder_positions)


def test_place_usage_maths():
    place = Place(label="C:", path="C:\\", icon="💽", is_drive=True,
                  total=1000, free=250)
    assert place.used == 750
    assert abs(place.used_fraction - 0.75) < 1e-9


def test_place_usage_handles_unknown_capacity():
    place = Place(label="x", path="/x", icon="?")
    assert place.used == 0
    assert place.used_fraction == 0.0


# -------------------------------------------------------------- breadcrumbs

def test_breadcrumbs_are_navigable_prefixes():
    # built from os.sep: on Windows a leading "/" lands on the current drive,
    # so a hardcoded posix path would not survive the round trip
    folder = os.path.join(os.sep, "home", "user", "Pictures", "Trip")
    full = os.path.abspath(folder)
    crumbs = locations.breadcrumbs(folder)

    assert [label for label, _ in crumbs][-3:] == ["user", "Pictures", "Trip"]
    assert crumbs[-1][1] == full, "last crumb is not the folder itself"

    # every crumb is an ancestor you can jump straight to, and they only
    # ever get deeper
    for _, target in crumbs:
        assert full == target or full.startswith(target.rstrip("\\/") + os.sep)
    depths = [len(target) for _, target in crumbs]
    assert depths == sorted(depths)
    assert len(set(depths)) == len(depths), "two crumbs point at the same place"


def test_breadcrumbs_start_at_the_root():
    crumbs = locations.breadcrumbs(os.path.join(os.sep, "var", "log"))
    root = crumbs[0][1]
    assert os.path.dirname(root) == root, f"{root!r} is not a filesystem root"


def test_breadcrumbs_of_empty_path():
    assert locations.breadcrumbs("") == []


def test_breadcrumbs_of_the_root_itself():
    crumbs = locations.breadcrumbs("/")
    assert len(crumbs) == 1


def test_shorten_middle_keeps_root_and_tail():
    crumbs = [(f"p{i}", f"/p{i}") for i in range(10)]
    shortened = locations.shorten_middle(crumbs, keep=3)

    assert shortened[0] == crumbs[0], "root crumb was dropped"
    assert None in shortened, "no elision marker"
    assert shortened[-3:] == crumbs[-3:], "tail crumbs were dropped"
    assert len(shortened) < len(crumbs)


def test_shorten_middle_leaves_short_paths_alone():
    crumbs = [(f"p{i}", f"/p{i}") for i in range(4)]
    assert locations.shorten_middle(crumbs, keep=4) == crumbs
    assert None not in locations.shorten_middle(crumbs, keep=4)
