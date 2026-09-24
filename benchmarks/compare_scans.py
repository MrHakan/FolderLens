"""Compare two JSON reports produced by ``scan_baseline.py``.

This reports ratios; it does not turn a single run into a release verdict.
Run both versions on the same host, dataset, cache state, and Defender setup.
"""
import argparse
import json
import os


METRICS = (
    "median_scan_seconds",
    "p95_scan_seconds",
    "median_first_500_items_seconds",
    "median_first_partial_seconds",
    "median_first_useful_result_seconds",
    "median_peak_rss_bytes",
    "median_peak_rss_delta_bytes",
    "median_python_alloc_peak_bytes",
    "median_inaccessible",
)


def _same_target(baseline, candidate):
    baseline_label = baseline.get("dataset_label")
    candidate_label = candidate.get("dataset_label")
    if baseline_label or candidate_label:
        return bool(baseline_label) and baseline_label == candidate_label
    return os.path.normcase(os.path.normpath(baseline.get("path", ""))) == \
        os.path.normcase(os.path.normpath(candidate.get("path", "")))


def compare_reports(baseline, candidate):
    base_summary = baseline.get("summary", {})
    new_summary = candidate.get("summary", {})
    base_total = base_summary.get("total_runs", 0)
    new_total = new_summary.get("total_runs", 0)
    base_success = base_summary.get("successful_runs", 0)
    new_success = new_summary.get("successful_runs", 0)

    metrics = {}
    for name in METRICS:
        before = base_summary.get(name)
        after = new_summary.get(name)
        ratio = (after / before if isinstance(before, (int, float)) and before
                 and isinstance(after, (int, float)) else None)
        metrics[name] = {
            "baseline": before,
            "candidate": after,
            "change_percent": round((ratio - 1) * 100, 2) if ratio is not None else None,
        }

    same_network = baseline.get("network_path") == candidate.get("network_path")
    base_host = baseline.get("host", {})
    new_host = candidate.get("host", {})
    same_host = all(base_host.get(key) == new_host.get(key)
                    for key in ("platform", "machine", "processor", "logical_cpus"))
    same_python = base_host.get("python") == new_host.get("python")
    same_conditions = baseline.get("conditions") == candidate.get("conditions")
    base_items = base_summary.get("median_items")
    new_items = new_summary.get("median_items")
    same_item_count = base_items is not None and base_items == new_items
    base_errors = base_summary.get("median_inaccessible")
    new_errors = new_summary.get("median_inaccessible")
    same_inaccessible_count = base_errors is not None and base_errors == new_errors
    same_coverage = same_item_count and same_inaccessible_count
    comparable = (_same_target(baseline, candidate) and same_network and same_host
                  and same_python and same_conditions and same_coverage)
    comparison = {
        "baseline_version": baseline.get("version"),
        "candidate_version": candidate.get("version"),
        "target": candidate.get("dataset_label") or candidate.get("path"),
        "network_path": candidate.get("network_path"),
        "comparable": comparable,
        "comparability_checks": {
            "same_dataset": _same_target(baseline, candidate),
            "same_local_or_network_kind": same_network,
            "same_host_platform": same_host,
            "same_python_version": same_python,
            "same_cache_and_defender_conditions": same_conditions,
            "same_item_count": same_item_count,
            "same_inaccessible_count": same_inaccessible_count,
        },
        "runs_complete": (base_total > 0 and new_total > 0
                          and base_success == base_total and new_success == new_total),
        "candidate_partial_runs": new_summary.get("partial_runs"),
        "metrics": metrics,
        "release_guidance": "Use repeated Windows measurements and review scan correctness separately.",
    }

    if comparable and not candidate.get("network_path"):
        items = new_items
        scan_ratio = metrics["median_scan_seconds"]["candidate"]
        baseline_scan = metrics["median_scan_seconds"]["baseline"]
        if (isinstance(items, (int, float)) and items >= 1_000_000
                and isinstance(scan_ratio, (int, float))
                and isinstance(baseline_scan, (int, float)) and baseline_scan > 0):
            ratio = scan_ratio / baseline_scan
            before_rss = metrics["median_peak_rss_bytes"]["baseline"]
            after_rss = metrics["median_peak_rss_bytes"]["candidate"]
            rss_ratio = (after_rss / before_rss
                         if isinstance(before_rss, (int, float)) and before_rss > 0
                         and isinstance(after_rss, (int, float)) else None)
            comparison["local_million_item_target"] = {
                "scan_time_at_or_below_75_percent_of_baseline": ratio <= 0.75,
                "scan_time_ratio": round(ratio, 3),
                "peak_rss_at_or_below_75_percent_of_baseline": (
                    rss_ratio <= 0.75 if rss_ratio is not None else None),
                "peak_rss_ratio": round(rss_ratio, 3) if rss_ratio is not None else None,
            }
    elif comparable and candidate.get("network_path"):
        before = metrics["median_scan_seconds"]["baseline"]
        after = metrics["median_scan_seconds"]["candidate"]
        comparison["network_scan_regression"] = (
            after > before if isinstance(before, (int, float))
            and isinstance(after, (int, float)) else None)
    return comparison


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("baseline", help="3.4.x baseline JSON report")
    parser.add_argument("candidate", help="4.0 candidate JSON report")
    parser.add_argument("--output", help="Optional JSON comparison output path")
    args = parser.parse_args()
    try:
        with open(args.baseline, encoding="utf-8") as stream:
            baseline = json.load(stream)
        with open(args.candidate, encoding="utf-8") as stream:
            candidate = json.load(stream)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))

    result = compare_reports(baseline, candidate)
    output = json.dumps(result, indent=2)
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as stream:
            stream.write(output + "\n")
    print(output)
    return 0 if result["runs_complete"] and result["comparable"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
