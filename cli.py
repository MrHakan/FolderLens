"""Headless scans and reports for automation.

The command line uses the same scanner, query projection, and CSV/JSON
exporters as the application, so a report written here matches what the
Export menu writes for the same filter.  The command line never deletes,
moves, or zips anything.

Exit codes: 0 for a complete scan, 2 when the scan is partial (some folders
could not be read), 1 when the scan or the report failed.
"""
import os
import sys
import threading
from typing import Optional, TextIO

import analysis
from file_utils import FILE_TYPE_FILTERS, format_size
from query import QueryEngine, QuerySpec, query_from_form
from scanner import TreeScanner, is_network_path

EXIT_COMPLETE = 0
EXIT_FAILED = 1
EXIT_PARTIAL = 2

CATEGORY_KEYS = tuple(key for key, _label in FILE_TYPE_FILTERS if key != "all")


def add_arguments(parser):
    """Register report and filter options on the application's parser."""
    group = parser.add_argument_group(
        "reports (no window; nothing is ever deleted or changed)")
    group.add_argument("--json", metavar="FILE", help="Write a JSON report to FILE")
    group.add_argument("--csv", metavar="FILE", help="Write a CSV report to FILE")
    group.add_argument("--category", action="append", choices=CATEGORY_KEYS, default=[],
                       help="Only files of this type (repeat for several)")
    group.add_argument("--ext", action="append", default=[], metavar="EXT",
                       help="Only these extensions, e.g. --ext .png --ext jpg")
    group.add_argument("--name", default="", help="Only file names containing this text")
    group.add_argument("--min-mib", default="", metavar="MIB", help="Minimum file size in MiB")
    group.add_argument("--max-mib", default="", metavar="MIB", help="Maximum file size in MiB")
    group.add_argument("--modified-after", default="", metavar="YYYY-MM-DD",
                       help="Only files modified on or after this day")
    group.add_argument("--modified-before", default="", metavar="YYYY-MM-DD",
                       help="Only files modified on or before this day")
    group.add_argument("--exclude-hidden", action="store_true", help="Leave out hidden files")
    group.add_argument("--on-disk", action="store_true",
                       help="Measure on-disk size and hardlinks (local folders only, slower)")


def wants_report(args) -> bool:
    """Any report or filter option runs headless instead of opening a window."""
    return bool(args.console or args.json or args.csv or args.category or args.ext
                or args.name or args.min_mib or args.max_mib or args.modified_after
                or args.modified_before or args.exclude_hidden or args.on_disk)


def spec_from_args(args) -> QuerySpec:
    return query_from_form(
        categories=args.category, extensions=",".join(args.ext), name=args.name,
        min_mib=args.min_mib, max_mib=args.max_mib,
        modified_after=args.modified_after, modified_before=args.modified_before,
        include_hidden=not args.exclude_hidden)


def scan_tree(folder: str, capture_extended: bool = False):
    """Run one blocking scan; returns (root, errors, seconds, failure)."""
    scanner = TreeScanner()
    done = threading.Event()
    result = {"root": None, "errors": [], "seconds": 0.0, "failure": None}

    def complete(root, errors, seconds):
        result.update(root=root, errors=list(errors), seconds=seconds)
        done.set()

    def failed(message):
        result["failure"] = message
        done.set()

    scanner.scan(folder, on_complete=complete, on_error=failed,
                 capture_extended=capture_extended)
    done.wait()
    return result["root"], result["errors"], result["seconds"], result["failure"]


def run(args, out: Optional[TextIO] = None) -> int:
    out = out or sys.stdout

    def say(text=""):
        if out is not None:
            print(text, file=out)

    folder = os.path.abspath(args.folder or os.getcwd())
    if not os.path.isdir(folder):
        say(f"[ERROR] Folder not found: {folder}")
        return EXIT_FAILED
    try:
        spec = spec_from_args(args)
    except (ValueError, ArithmeticError, OverflowError) as exc:
        say(f"[ERROR] Invalid filter: {exc}")
        return EXIT_FAILED

    on_disk = bool(args.on_disk)
    if on_disk and is_network_path(folder):
        say("[WARN] On-disk size is not measured on network folders; using logical size.")
        on_disk = False

    say(f"Scanning: {folder}")
    root, errors, seconds, failure = scan_tree(folder, capture_extended=on_disk)
    if failure is not None or root is None:
        say(f"[ERROR] {failure or 'Scan failed'}")
        return EXIT_FAILED

    filtered = spec != QuerySpec()
    index = QueryEngine(root).project(spec) if filtered else None
    partial = bool(errors)

    try:
        if args.json:
            rows = analysis.export_tree_json(root, args.json, index, partial=partial,
                                             inaccessible_count=len(errors))
            say(f"[OK] Wrote {rows:,} records to {args.json}")
        if args.csv:
            rows = analysis.export_tree_csv(root, args.csv, index, partial=partial,
                                            inaccessible_count=len(errors))
            say(f"[OK] Wrote {rows:,} records to {args.csv}")
    except OSError as exc:
        say(f"[ERROR] Could not write the report: {exc}")
        return EXIT_FAILED

    size_of = index.size if index is not None else (lambda node: node.size)
    count_of = index.count if index is not None else (lambda node: node.item_count)
    children = (index.sorted_children(root) if index is not None
                else root.sorted_children("size"))
    say("-" * 72)
    say(f"{'Name':<42} {'Size':>14} {'Type':<12}")
    say("-" * 72)
    for node in children:
        name = node.name[:40] + ".." if len(node.name) > 42 else node.name
        kind = "Folder" if node.is_dir else "File"
        say(f"{name:<42} {format_size(size_of(node)):>14} {kind:<12}")
    say("-" * 72)
    scope = "matching files" if filtered else "all files"
    items = count_of(root) if index is not None else root.item_count
    say(f"Total ({scope}): {format_size(size_of(root))} in {items:,} items")
    say(f"Scan time: {seconds:.2f}s")
    if on_disk:
        storage = analysis.storage_summary(root)
        unknown = "unknown"
        say(f"On disk (whole scan): {format_size(storage.allocated_bytes) if storage.allocated_bytes is not None else unknown}"
            f" · unique: {format_size(storage.unique_allocated_bytes) if storage.unique_allocated_bytes is not None else unknown}"
            f" · hardlinked files: {storage.hardlinked_files:,}")
    if partial:
        say(f"[PARTIAL] {len(errors):,} item(s) could not be read; totals are incomplete.")
        return EXIT_PARTIAL
    return EXIT_COMPLETE
