"""Repeatable metadata scan baseline for local folders and mounted/UNC shares.

Run the same command on the same folder for each version.  Report Python
allocation peak (not process RSS), first progress after 500 entries, and
completion time.  Avoid altering the target while a run is in progress.
"""

import argparse
import json
import os
import statistics
import sys
import threading
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import TreeScanner, is_network_path
from version import VERSION


def measure(path: str) -> dict:
    done = threading.Event()
    outcome = {}
    start = time.perf_counter()
    tracemalloc.start()

    def progress(count):
        outcome.setdefault("first_progress_seconds", round(time.perf_counter() - start, 3))

    def complete(root, errors, duration):
        outcome.update(items=root.item_count, logical_bytes=root.size,
                       inaccessible=len(errors), scan_seconds=round(duration, 3))
        done.set()

    def failed(message):
        outcome["error"] = message
        done.set()

    scanner = TreeScanner()
    try:
        scanner.scan(path, on_progress=progress, on_complete=complete, on_error=failed)
        done.wait()
        _, peak = tracemalloc.get_traced_memory()
        outcome["python_alloc_peak_bytes"] = peak
        scanner._current_thread.join()
    finally:
        tracemalloc.stop()
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Existing directory to scan; do not modify during measurements")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", help="Optional JSON report path")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    path = os.path.abspath(args.path)
    results = [measure(path) for _ in range(args.runs)]
    timings = [r["scan_seconds"] for r in results if "scan_seconds" in r]
    report = {"version": VERSION, "path": path, "network_path": is_network_path(path),
              "runs": results, "median_scan_seconds": statistics.median(timings) if timings else None}
    output = json.dumps(report, indent=2)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as stream:
            stream.write(output + "\n")
    print(output)
    return 0 if len(timings) == args.runs else 1


if __name__ == "__main__":
    raise SystemExit(main())
