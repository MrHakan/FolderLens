"""Headless reports share the application's scanner and query semantics."""
import io
import json
import os
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import cli
import main as main_module


@pytest.fixture
def folder(tmp_path):
    (tmp_path / "photo.png").write_bytes(b"p" * 3000)
    (tmp_path / "notes.txt").write_bytes(b"t" * 100)
    sub = tmp_path / "albums"
    sub.mkdir()
    (sub / "trip.jpg").write_bytes(b"j" * 2000)
    return tmp_path


def parse(*argv):
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", nargs="?")
    parser.add_argument("--console", action="store_true")
    cli.add_arguments(parser)
    return parser.parse_args(list(argv))


def test_filtered_json_report_matches_the_query_scope(folder, tmp_path_factory):
    report = tmp_path_factory.mktemp("out") / "images.json"
    out = io.StringIO()
    code = cli.run(parse(str(folder), "--category", "image", "--json", str(report)), out)
    assert code == cli.EXIT_COMPLETE
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["scope"]["mode"] == "visible_results"
    assert data["scope"]["query"]["categories"] == ["image"]
    assert data["scan"] == {"status": "complete", "partial": False, "inaccessible_count": 0}
    names = {record["name"] for record in data["records"]}
    assert names == {"photo.png", "albums", "trip.jpg"}
    assert "Total (matching files): 4.88 KB in 2 items" in out.getvalue()


def test_csv_report_and_full_scan_summary(folder, tmp_path_factory):
    report = tmp_path_factory.mktemp("out") / "all.csv"
    out = io.StringIO()
    assert cli.run(parse(str(folder), "--csv", str(report)), out) == cli.EXIT_COMPLETE
    lines = report.read_text(encoding="utf-8").splitlines()
    assert lines[0].startswith("Path,Name,Type")
    assert len(lines) == 1 + 4
    assert "Total (all files)" in out.getvalue()


def test_partial_scan_exits_with_two(folder, monkeypatch):
    def fake_scan(path, capture_extended=False):
        root, errors, seconds, failure = original(path, capture_extended)
        return root, errors + ["Access denied: somewhere"], seconds, failure

    original = cli.scan_tree
    monkeypatch.setattr(cli, "scan_tree", fake_scan)
    out = io.StringIO()
    assert cli.run(parse(str(folder), "--console"), out) == cli.EXIT_PARTIAL
    assert "[PARTIAL]" in out.getvalue()


def test_invalid_filter_and_missing_folder_fail_without_scanning(folder, tmp_path):
    out = io.StringIO()
    assert cli.run(parse(str(folder), "--min-mib", "lots"), out) == cli.EXIT_FAILED
    assert "Invalid filter" in out.getvalue()
    assert cli.run(parse(str(tmp_path / "missing"), "--console"), io.StringIO()) == cli.EXIT_FAILED


def test_main_entry_point_returns_the_report_exit_code(folder, tmp_path_factory):
    report = tmp_path_factory.mktemp("out") / "r.json"
    completed = subprocess.run(
        [sys.executable, os.path.join(ROOT, "main.py"), str(folder),
         "--ext", "txt", "--json", str(report)],
        capture_output=True, text=True, timeout=120)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert [record["name"] for record in data["records"]] == ["notes.txt"]


def test_filter_options_alone_run_headless(folder):
    assert cli.wants_report(parse(str(folder), "--category", "image"))
    assert not cli.wants_report(parse(str(folder)))


def test_on_disk_summary_is_reported_without_guessing(folder):
    out = io.StringIO()
    assert cli.run(parse(str(folder), "--on-disk"), out) == cli.EXIT_COMPLETE
    assert "On disk (whole scan):" in out.getvalue()
