"""Repeatable metadata scan baseline for local folders and mounted/UNC shares.

Run the same command on the same folder for each version.  Report Python
allocation peak, sampled process RSS, time to first progress, and completion
time. Avoid altering the target while a run is in progress.
"""

import argparse
import json
import os
import platform
import statistics
import sys
import threading
import time
import tracemalloc

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scanner import TreeScanner, is_network_path
from version import VERSION


def process_rss_bytes():
    """Return current process RSS using the standard library where possible."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        class ProcessMemoryCounters(ctypes.Structure):
            _fields_ = [
                ("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = ProcessMemoryCounters()
        counters.cb = ctypes.sizeof(counters)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        psapi = ctypes.WinDLL("psapi", use_last_error=True)
        get_current_process = kernel32.GetCurrentProcess
        get_current_process.restype = wintypes.HANDLE
        get_memory = psapi.GetProcessMemoryInfo
        get_memory.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCounters), wintypes.DWORD)
        get_memory.restype = wintypes.BOOL
        if get_memory(get_current_process(), ctypes.byref(counters), counters.cb):
            return int(counters.WorkingSetSize)
        return None

    try:
        with open("/proc/self/status", encoding="ascii") as stream:
            for line in stream:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError, IndexError):
        pass

    try:
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return int(value if sys.platform == "darwin" else value * 1024)
    except (ImportError, AttributeError, OSError):
        return None


def _percentile(values, percentile):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1,
                       int((percentile * len(ordered) + 99) // 100) - 1))
    return ordered[index]


def measure(path: str, timeout_seconds: float = 14_400) -> dict:
    done = threading.Event()
    sampling_stopped = threading.Event()
    outcome = {}
    start = time.perf_counter()
    baseline_rss = process_rss_bytes()
    memory = {"peak": baseline_rss}
    tracemalloc.start()

    def progress(count):
        outcome.setdefault("first_progress_seconds", round(time.perf_counter() - start, 3))
        if count >= 500:
            outcome.setdefault("first_500_items_seconds",
                               round(time.perf_counter() - start, 3))

    def snapshot(value):
        outcome["scan_state"] = value.state
        outcome["partial"] = value.partial
        outcome["known_files"] = value.known_files
        outcome["observed_items"] = value.observed_items
        outcome["inaccessible"] = value.errors
        outcome["queue_high_water"] = value.queue_high_water
        outcome["deferred_high_water"] = value.deferred_high_water
        if value.state == "scanning" and (value.known_files or value.directories_completed):
            outcome.setdefault("first_partial_seconds", round(time.perf_counter() - start, 3))

    def complete(root, errors, duration):
        outcome.update(scan_state="complete", items=root.item_count,
                       logical_bytes=root.size, inaccessible=len(errors),
                       scan_seconds=round(duration, 3))
        done.set()

    def failed(message):
        outcome.update(scan_state="failed", error=message)
        done.set()

    scanner = TreeScanner()

    def sample_rss():
        while not sampling_stopped.wait(0.1):
            current = process_rss_bytes()
            if current is not None and (memory["peak"] is None or current > memory["peak"]):
                memory["peak"] = current

    sampler = threading.Thread(target=sample_rss, daemon=True, name="folderlens-rss-sampler")
    sampler.start()
    try:
        scanner.scan(path, on_progress=progress, on_snapshot=snapshot,
                     on_complete=complete, on_error=failed)
        timed_out = not done.wait(timeout_seconds)
        if timed_out:
            scanner.cancel()
            done.wait(5)
            outcome.update(scan_state="timeout", timeout_seconds=timeout_seconds)
        _, peak = tracemalloc.get_traced_memory()
        outcome["python_alloc_peak_bytes"] = peak
        worker = scanner._current_thread
        if worker is not None:
            worker.join(30 if done.is_set() else 5)
            outcome["worker_stopped"] = not worker.is_alive()
        outcome["scan_generation"] = scanner._generation
    finally:
        sampling_stopped.set()
        sampler.join(timeout=1)
        final_rss = process_rss_bytes()
        if final_rss is not None and (memory["peak"] is None or final_rss > memory["peak"]):
            memory["peak"] = final_rss
        outcome["rss_before_bytes"] = baseline_rss
        outcome["peak_rss_bytes"] = memory["peak"]
        outcome["peak_rss_delta_bytes"] = (
            max(0, memory["peak"] - baseline_rss)
            if memory["peak"] is not None and baseline_rss is not None else None)
        tracemalloc.stop()
    return outcome


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="Existing directory to scan; do not modify during measurements")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--timeout-seconds", type=float, default=14_400,
                        help="Maximum time per run before cancellation (default: 4 hours)")
    parser.add_argument("--output", help="Optional JSON report path")
    parser.add_argument("--dataset-label", help="Short label for the controlled test dataset")
    parser.add_argument("--cache-state", choices=("unknown", "cold", "warm"), default="unknown")
    parser.add_argument("--defender", choices=("unknown", "enabled", "disabled"), default="unknown",
                        help="Windows Defender state during the run")
    parser.add_argument("--storage-label", help="For example: SATA SSD, NVMe SSD, or HDD")
    parser.add_argument("--network-rtt-ms", type=float,
                        help="Measured share RTT in milliseconds, if applicable")
    parser.add_argument("--network-bandwidth-mbps", type=float,
                        help="Measured share bandwidth in Mbit/s, if applicable")
    args = parser.parse_args()
    if args.runs < 1:
        parser.error("--runs must be positive")
    if args.timeout_seconds <= 0:
        parser.error("--timeout-seconds must be positive")
    path = os.path.abspath(args.path)
    scanner = TreeScanner()
    results = [measure(path, args.timeout_seconds) for _ in range(args.runs)]
    completed = [result for result in results if result.get("scan_state") == "complete"]

    def median(key):
        values = [result[key] for result in completed if isinstance(result.get(key), (int, float))]
        if not values:
            return None
        value = statistics.median(values)
        return round(value, 3) if key.endswith("_seconds") else value

    report = {
        "version": VERSION,
        "path": path,
        "dataset_label": args.dataset_label,
        "network_path": is_network_path(path),
        "worker_limit": scanner.worker_limit(path),
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
            "python": sys.version.split()[0],
        },
        "conditions": {
            "cache_state": args.cache_state,
            "windows_defender": args.defender,
            "storage": args.storage_label,
            "network_rtt_ms": args.network_rtt_ms,
            "network_bandwidth_mbps": args.network_bandwidth_mbps,
        },
        "timeout_seconds": args.timeout_seconds,
        "runs": results,
        "summary": {
            "successful_runs": len(completed),
            "total_runs": len(results),
            "median_scan_seconds": median("scan_seconds"),
            "p95_scan_seconds": _percentile(
                [r["scan_seconds"] for r in completed if "scan_seconds" in r], 95),
            "median_first_progress_seconds": median("first_progress_seconds"),
            "median_first_500_items_seconds": median("first_500_items_seconds"),
            "median_first_partial_seconds": median("first_partial_seconds"),
            "median_peak_rss_bytes": median("peak_rss_bytes"),
            "median_peak_rss_delta_bytes": median("peak_rss_delta_bytes"),
            "median_python_alloc_peak_bytes": median("python_alloc_peak_bytes"),
            "median_items": median("items"),
            "median_inaccessible": median("inaccessible"),
            "partial_runs": sum(result.get("partial") is True for result in results),
        },
    }
    output = json.dumps(report, indent=2)
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as stream:
            stream.write(output + "\n")
    print(output)
    return 0 if len(completed) == args.runs else 1


if __name__ == "__main__":
    raise SystemExit(main())
