import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from updater import Updater


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
