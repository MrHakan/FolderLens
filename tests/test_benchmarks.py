from types import SimpleNamespace

from benchmarks import compare_scans, scan_baseline


def test_scan_benchmark_records_first_result_memory_and_final_counts(tmp_path, monkeypatch):
    class FakeThread:
        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False

    class FakeScanner:
        def __init__(self):
            self._generation = 7
            self._current_thread = FakeThread()

        def scan(self, _path, on_progress, on_snapshot, on_complete, on_error):
            on_progress(500)
            on_snapshot(SimpleNamespace(
                state="scanning", partial=True, known_files=40, observed_items=50,
                errors=1, queue_high_water=8, deferred_high_water=2,
                directories_completed=1, observed_files=(object(),)))
            on_snapshot(SimpleNamespace(
                state="complete", partial=True, known_files=40, observed_items=55,
                errors=1, queue_high_water=8, deferred_high_water=2,
                directories_completed=3))
            on_complete(SimpleNamespace(item_count=55, size=1024), ["denied"], 0.25)

        def cancel(self):
            raise AssertionError("a completed fake scan must not be cancelled")

    monkeypatch.setattr(scan_baseline, "TreeScanner", FakeScanner)
    result = scan_baseline.measure(str(tmp_path), timeout_seconds=1)

    assert result["scan_state"] == "complete"
    assert result["items"] == 55
    assert result["logical_bytes"] == 1024
    assert result["first_500_items_seconds"] >= 0
    assert result["first_useful_result_seconds"] >= 0
    assert result["queue_high_water"] == 8
    assert "peak_rss_bytes" in result
    assert "python_alloc_peak_bytes" in result
    assert result["worker_stopped"] is True


def test_benchmark_p95_uses_nearest_rank():
    assert scan_baseline._percentile([1, 2, 3, 4, 5], 95) == 5


def test_scan_benchmark_cancels_and_marks_a_timed_out_scan(tmp_path, monkeypatch):
    class FakeThread:
        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False

    class FakeScanner:
        def __init__(self):
            self._generation = 2
            self._current_thread = FakeThread()
            self.error_callback = None
            self.cancel_called = False

        def scan(self, _path, **callbacks):
            self.error_callback = callbacks["on_error"]

        def cancel(self):
            self.cancel_called = True
            self.error_callback("cancelled")

    scanner = FakeScanner()
    monkeypatch.setattr(scan_baseline, "TreeScanner", lambda: scanner)
    result = scan_baseline.measure(str(tmp_path), timeout_seconds=0.01)

    assert scanner.cancel_called
    assert result["scan_state"] == "timeout"
    assert result["timeout_seconds"] == 0.01


def test_scan_benchmark_supports_legacy_scanner_callbacks(tmp_path, monkeypatch):
    class FakeThread:
        def join(self, timeout=None):
            pass

        def is_alive(self):
            return False

    class FakeScanner:
        def __init__(self):
            self._generation = 1
            self._current_thread = FakeThread()

        def scan(self, _path, on_progress=None, on_complete=None, on_error=None):
            on_progress(500)
            on_complete(SimpleNamespace(item_count=3, size=2048), [], 0.5)

        def cancel(self):
            raise AssertionError("the legacy scan completed")

    monkeypatch.setattr(scan_baseline, "TreeScanner", FakeScanner)
    result = scan_baseline.measure(str(tmp_path), timeout_seconds=1)

    assert result["scan_state"] == "complete"
    assert result["items"] == 3
    assert result["first_500_items_seconds"] >= 0
    assert "first_useful_result_seconds" not in result


def test_scan_comparison_reports_ratios_and_comparability():
    base = {
        "version": "3.4.1", "path": "C:/benchmark", "network_path": False,
        "dataset_label": "million files", "host": {"platform": "Windows"},
        "conditions": {"cache_state": "warm", "windows_defender": "enabled"},
        "summary": {"total_runs": 3, "successful_runs": 3, "median_items": 1_000_001,
                    "median_inaccessible": 0, "partial_runs": 0,
                    "median_scan_seconds": 100, "p95_scan_seconds": 110,
                    "median_first_500_items_seconds": 2,
                    "median_first_partial_seconds": 3,
                    "median_first_useful_result_seconds": 1,
                    "median_peak_rss_bytes": 400,
                    "median_peak_rss_delta_bytes": 100,
                    "median_python_alloc_peak_bytes": 300},
    }
    current = {
        **base,
        "version": "4.0.0-rc1",
        "summary": {**base["summary"], "median_scan_seconds": 70,
                    "p95_scan_seconds": 75, "median_peak_rss_bytes": 280},
    }

    result = compare_scans.compare_reports(base, current)

    assert result["comparable"] is True
    assert result["metrics"]["median_scan_seconds"]["change_percent"] == -30.0
    assert result["local_million_item_target"]["scan_time_at_or_below_75_percent_of_baseline"]
