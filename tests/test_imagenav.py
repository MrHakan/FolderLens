import os
import sys

import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import imagenav
from imagenav import ImageNavigator


def make_image(path, color=(120, 60, 200), size=(20, 16)):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


@pytest.fixture
def gallery(tmp_path):
    """
    root/
      a.png  b.png  c.png  notes.txt
      sub/     -> d.png
      empty/   -> (no images)
    """
    for name in ("a.png", "b.png", "c.png"):
        make_image(str(tmp_path / name))
    (tmp_path / "notes.txt").write_text("not an image")
    make_image(str(tmp_path / "sub" / "d.png"))
    (tmp_path / "empty").mkdir()
    return tmp_path


def test_lists_only_images_in_order(gallery):
    names = [os.path.basename(p) for p in imagenav.list_images(str(gallery))]
    assert names == ["a.png", "b.png", "c.png"]


def test_natural_ordering(tmp_path):
    for name in ("img10.png", "img2.png", "img1.png"):
        make_image(str(tmp_path / name))
    names = [os.path.basename(p) for p in imagenav.list_images(str(tmp_path))]
    assert names == ["img1.png", "img2.png", "img10.png"]


def test_missing_folder_is_empty_not_an_error(tmp_path):
    assert imagenav.list_images(str(tmp_path / "nope")) == []
    assert imagenav.list_subfolders(str(tmp_path / "nope")) == []


def test_starts_on_the_opened_image(gallery):
    nav = ImageNavigator(str(gallery / "b.png"))
    assert os.path.basename(nav.current) == "b.png"
    assert nav.position == "2 / 3"


def test_next_and_previous_wrap(gallery):
    nav = ImageNavigator(str(gallery / "c.png"))
    assert os.path.basename(nav.next()) == "a.png"          # wraps forward
    assert os.path.basename(nav.previous()) == "c.png"      # and back


def test_walks_the_whole_folder(gallery):
    nav = ImageNavigator(str(gallery / "a.png"))
    seen = [os.path.basename(nav.current)]
    for _ in range(2):
        seen.append(os.path.basename(nav.next()))
    assert seen == ["a.png", "b.png", "c.png"]


def test_opening_a_folder_starts_at_its_first_image(gallery):
    nav = ImageNavigator(str(gallery))
    assert os.path.basename(nav.current) == "a.png"


def test_go_to_specific_image(gallery):
    nav = ImageNavigator(str(gallery / "a.png"))
    assert nav.go_to(str(gallery / "c.png")) is not None
    assert os.path.basename(nav.current) == "c.png"
    assert nav.go_to(str(gallery / "missing.png")) is None


def test_subfolders_listed(gallery):
    names = [os.path.basename(p) for p in ImageNavigator(str(gallery / "a.png")).subfolders()]
    assert names == ["empty", "sub"]


def test_open_subfolder_switches_context(gallery):
    nav = ImageNavigator(str(gallery / "a.png"))
    first = nav.open_folder(str(gallery / "sub"))
    assert os.path.basename(first) == "d.png"
    assert nav.count == 1
    assert nav.position == "1 / 1"


def test_open_folder_without_images(gallery):
    nav = ImageNavigator(str(gallery / "a.png"))
    assert nav.open_folder(str(gallery / "empty")) is None
    assert nav.current is None
    assert nav.position == "0 / 0"
    # and navigation stays safe rather than raising
    assert nav.next() is None
    assert nav.previous() is None


def test_parent_navigation(gallery):
    nav = ImageNavigator(str(gallery / "sub" / "d.png"))
    parent = nav.parent()
    assert parent == str(gallery)
    assert os.path.basename(nav.open_folder(parent)) == "a.png"


def test_folder_has_images(gallery):
    assert imagenav.folder_has_images(str(gallery))
    assert not imagenav.folder_has_images(str(gallery / "empty"))
