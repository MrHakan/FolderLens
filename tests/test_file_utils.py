import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from file_utils import (
    format_size, calculate_percentage, natural_sort_key,
    get_file_extension, get_file_category, is_image_file, FILE_CATEGORIES
)


def test_format_size_bytes():
    assert format_size(0) == "0 B"
    assert format_size(512) == "512 B"


def test_format_size_units():
    assert format_size(1024) == "1.00 KB"
    assert format_size(1024 * 1024) == "1.00 MB"
    assert format_size(1024 ** 3) == "1.00 GB"
    assert format_size(int(1.5 * 1024 ** 2)) == "1.50 MB"


def test_format_size_negative():
    assert format_size(-100) == "0 B"


def test_calculate_percentage():
    assert calculate_percentage(50, 100) == 50.0
    assert calculate_percentage(0, 100) == 0.0
    assert calculate_percentage(100, 0) == 0.0
    assert calculate_percentage(200, 100) == 100.0


def test_natural_sort_key():
    names = ["file10.txt", "file2.txt", "file1.txt"]
    names.sort(key=natural_sort_key)
    assert names == ["file1.txt", "file2.txt", "file10.txt"]


def test_natural_sort_case_insensitive():
    names = ["Beta", "alpha", "Gamma"]
    names.sort(key=natural_sort_key)
    assert names == ["alpha", "Beta", "Gamma"]


def test_get_file_extension():
    assert get_file_extension("photo.JPG") == "JPG"
    assert get_file_extension("noext") == "File"


def test_get_file_category_video():
    assert get_file_category("movie.mp4") is FILE_CATEGORIES['video']


def test_get_file_category_unknown():
    assert get_file_category("mystery.xyz") is FILE_CATEGORIES['other']


def test_is_image_file():
    assert is_image_file("pic.png")
    assert is_image_file("pic.JPEG")
    assert not is_image_file("doc.pdf")
