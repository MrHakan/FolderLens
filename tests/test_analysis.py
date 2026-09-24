import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import analysis
from scanner import Node


def make_tree():
    """Build a small aggregated Node tree by hand.

    root/
      a.txt      100  (document)
      b.mp4      500  (video)
      sub/            (folder)
        c.png    300  (image)
        d.txt     50  (document)
    """
    root = Node(path="/root", name="root", is_dir=True)
    a = Node(path="/root/a.txt", name="a.txt", is_dir=False, size=100, parent=root)
    b = Node(path="/root/b.mp4", name="b.mp4", is_dir=False, size=500, parent=root)
    sub = Node(path="/root/sub", name="sub", is_dir=True, parent=root)
    c = Node(path="/root/sub/c.png", name="c.png", is_dir=False, size=300, parent=sub)
    d = Node(path="/root/sub/d.txt", name="d.txt", is_dir=False, size=50, parent=sub)
    sub.children = [c, d]
    sub.size = 350
    sub.item_count = 2
    root.children = [a, b, sub]
    root.size = 950
    root.item_count = 4
    return root


def test_iter_file_nodes():
    root = make_tree()
    names = sorted(n.name for n in analysis.iter_file_nodes(root))
    assert names == ["a.txt", "b.mp4", "c.png", "d.txt"]


def test_iter_all_nodes_excludes_root():
    root = make_tree()
    names = sorted(n.name for n in analysis.iter_all_nodes(root))
    assert names == ["a.txt", "b.mp4", "c.png", "d.txt", "sub"]


def test_largest_files_order_and_limit():
    root = make_tree()
    files = analysis.largest_files(root, limit=2)
    assert [f.name for f in files] == ["b.mp4", "c.png"]

    all_files = analysis.largest_files(root, limit=100)
    assert [f.size for f in all_files] == [500, 300, 100, 50]


def test_largest_search_before_top_k():
    root = make_tree()
    assert [n.name for n in analysis.largest_files(root, limit=1, name_query="txt")] == ["a.txt"]
    assert [n.name for n in analysis.largest_files(
        root, limit=1, filter_key="document", name_query="d")] == ["d.txt"]


def test_largest_files_stops_walking_when_cancelled():
    root = make_tree()
    assert analysis.largest_files(root, should_cancel=lambda: True) == []


def test_category_breakdown():
    root = make_tree()
    stats = {s.label: s for s in analysis.category_breakdown(root)}
    assert stats["Video"].size == 500
    assert stats["Video"].count == 1
    assert stats["Image"].size == 300
    assert stats["Document"].size == 150
    assert stats["Document"].count == 2
    # sorted largest first
    labels = [s.label for s in analysis.category_breakdown(root)]
    assert labels[0] == "Video"
    # percentages sum to ~100
    total_pct = sum(s.percent for s in analysis.category_breakdown(root))
    assert abs(total_pct - 100.0) < 0.01


def test_extension_breakdown():
    root = make_tree()
    rows = dict((ext, (size, count)) for ext, size, count in analysis.extension_breakdown(root))
    assert rows["TXT"] == (150, 2)
    assert rows["MP4"] == (500, 1)
    assert rows["PNG"] == (300, 1)


def test_squarify_areas_and_bounds():
    sizes = [4, 3, 2, 1]
    rects = analysis.squarify(sizes, 0, 0, 100, 100)
    assert len(rects) == 4

    total = sum(sizes)
    for size, (x, y, w, h) in zip(sizes, rects):
        assert w >= 0 and h >= 0
        assert -0.01 <= x and x + w <= 100.01
        assert -0.01 <= y and y + h <= 100.01
        expected_area = size / total * 10000
        assert abs(w * h - expected_area) < 1.0

    covered = sum(w * h for (_, _, w, h) in rects)
    assert abs(covered - 10000) < 1.0


def test_squarify_empty_and_single():
    assert analysis.squarify([], 0, 0, 10, 10) == []
    single = analysis.squarify([5], 0, 0, 10, 20)
    assert single == [(0, 0, 10, 20)]


def test_squarify_handles_zero_sizes():
    rects = analysis.squarify([0, 0], 0, 0, 10, 10)
    assert len(rects) == 2


def test_build_treemap_within_bounds():
    root = make_tree()
    tiles = analysis.build_treemap(root, 0, 0, 200, 200, min_area=1, max_depth=6)
    assert tiles
    names = {t.node.name for t in tiles}
    # top-level items and (since min_area is tiny) nested items appear
    assert {"a.txt", "b.mp4", "sub"} <= names
    assert {"c.png", "d.txt"} <= names
    for t in tiles:
        assert t.w > 0 and t.h > 0
        assert -0.5 <= t.x and t.x + t.w <= 200.5
        assert -0.5 <= t.y and t.y + t.h <= 200.5


def test_build_treemap_tiny_canvas():
    root = make_tree()
    assert analysis.build_treemap(root, 0, 0, 1, 1) == []


def test_build_treemap_stops_for_a_stale_render_generation():
    root = make_tree()
    assert analysis.build_treemap(root, 0, 0, 200, 120,
                                  should_cancel=lambda: True) == []


def test_build_treemap_cancels_while_collecting_a_wide_directory():
    from scanner import Node

    root = Node("/wide", "wide", True)
    root.children = [Node(None, f"f{index}.bin", False,
                          size=index + 1, parent=root)
                     for index in range(10_000)]
    checks = 0

    def cancel_after_some_children():
        nonlocal checks
        checks += 1
        return checks >= 4

    assert analysis.build_treemap(
        root, 0, 0, 400, 300, should_cancel=cancel_after_some_children) == []
    assert checks == 4


def test_export_tree_csv(tmp_path):
    root = make_tree()
    out = tmp_path / "report.csv"
    rows = analysis.export_tree_csv(root, str(out))
    assert rows == 5  # a, b, sub, c, d

    content = out.read_text(encoding="utf-8")
    assert "Path,Name,Type,Size (bytes)" in content
    assert "b.mp4" in content
    assert "sub" in content


def test_filtered_csv_has_matching_rows_and_projected_folder_size(tmp_path):
    import csv
    root = make_tree()
    index = analysis.build_filter_index(root, "image")
    out = tmp_path / "images.csv"
    assert analysis.export_tree_csv(root, str(out), filter_index=index) == 2
    with out.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert [(r["Name"], r["Size (bytes)"], r["Scope"]) for r in rows] == [
        ("sub", "300", "image"), ("c.png", "300", "image")]
    assert all(r["Root"] == "/root" and r["Metric"] == "logical"
               and r["Scan status"] == "complete" for r in rows)


def test_search_csv_records_visible_scope_and_partial_status(tmp_path):
    import csv
    root = make_tree()
    out = tmp_path / "search.csv"
    assert analysis.export_tree_csv(root, str(out), search_query="txt",
                                    partial=True, inaccessible_count=2) == 2
    with out.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    assert {row["Name"] for row in rows} == {"a.txt", "d.txt"}
    assert all(row["Scope"] == "all; name contains 'txt'" for row in rows)
    assert all(row["Root"] == "/root" and row["Scan status"] == "partial · 2 inaccessible"
               for row in rows)


def test_csv_does_not_repeat_search_already_in_query_projection(tmp_path):
    from query import QueryEngine, QuerySpec
    root = make_tree()
    index = QueryEngine(root).project(QuerySpec(name_terms=("txt",)))
    out = tmp_path / "query-search.csv"

    assert analysis.export_tree_csv(root, str(out), filter_index=index,
                                    search_query="TXT") == 2
    content = out.read_text(encoding="utf-8")
    assert "name_terms=('txt',)" in content
    assert "name contains 'TXT'" not in content


def test_match_query():
    assert analysis.match_query("Report.txt", "report")
    assert analysis.match_query("anything", "")
    assert not analysis.match_query("photo.png", "video")


def test_find_matches():
    root = make_tree()
    matches = analysis.find_matches(root, "txt")
    names = [n.name for n in matches]
    assert set(names) == {"a.txt", "d.txt"}
    # sorted largest first
    assert names[0] == "a.txt"
    assert [node.name for node in analysis.find_matches(root, "txt", limit=1)] == ["a.txt"]
    assert analysis.find_matches(root, "txt", should_cancel=lambda: True) == []
    assert analysis.find_matches(root, "") == []


def test_filter_index_projects_sizes_and_children_without_touching_disk():
    root = make_tree()
    index = analysis.build_filter_index(root, "image")
    sub = next(child for child in root.children if child.name == "sub")
    image = next(child for child in sub.children if child.name == "c.png")

    assert index.size(root) == 300
    assert index.count(root) == 1
    assert index.children(root) == [sub]
    assert index.size(sub) == 300
    assert index.children(sub) == [image]
    assert analysis.largest_files(root, filter_key="image", filter_index=index) == [image]
    assert [stat.label for stat in analysis.category_breakdown(
        root, filter_key="image", filter_index=index)] == ["Image"]
    assert analysis.find_matches(root, "c", filter_key="image", filter_index=index) == [image]

    tiles = analysis.build_treemap(
        root, 0, 0, 400, 300, min_area=1, max_depth=6,
        size_getter=index.size, children_getter=index.children,
        count_getter=index.count)
    assert {tile.node.name for tile in tiles if not tile.node.is_dir} == {"c.png"}
    assert any(tile.node is sub for tile in tiles)


def test_treemap_collapses_large_sibling_tail_into_an_aggregate():
    root = Node(path="/root", name="root", is_dir=True)
    root.children = [
        Node(path=f"/root/file{i}.bin", name=f"file{i}.bin", is_dir=False,
             size=i + 1, parent=root)
        for i in range(20)
    ]
    root.size = sum(child.size for child in root.children)
    root.item_count = len(root.children)

    tiles = analysis.build_treemap(
        root, 0, 0, 400, 300, min_area=1, max_depth=1, max_children=6)
    top = [tile for tile in tiles if tile.depth == 0]
    aggregate = next(tile.node for tile in top if getattr(tile.node, "is_aggregate", False))
    assert len(top) == 6
    assert aggregate.item_count == 15
    assert aggregate.size == sum(range(1, 16))
    assert [node.size for node in analysis.aggregate_members(aggregate)] == list(range(15, 0, -1))

    only_aggregate = analysis.build_treemap(
        root, 0, 0, 100, 100, min_area=1, max_depth=1, max_children=1)
    assert len(only_aggregate) == 1
    assert getattr(only_aggregate[0].node, "is_aggregate", False)


def test_json_and_csv_exports_share_query_scope_and_partial_metadata(tmp_path):
    from query import QueryEngine, QuerySpec

    root = make_tree()
    index = QueryEngine(root).project(QuerySpec(categories=("image",)))
    csv_path = tmp_path / "visible.csv"
    json_path = tmp_path / "visible.json"

    csv_count = analysis.export_tree_csv(
        root, str(csv_path), filter_index=index, partial=True, inaccessible_count=2)
    json_count = analysis.export_tree_json(
        root, str(json_path), filter_index=index, partial=True, inaccessible_count=2)

    with csv_path.open(newline="", encoding="utf-8") as source:
        csv_rows = list(csv.DictReader(source))
    report = json.loads(json_path.read_text(encoding="utf-8"))

    assert json_count == csv_count == len(report["records"]) == 2
    assert [(row["Path"], int(row["Size (bytes)"])) for row in csv_rows] == [
        (row["path"], row["size_bytes"]) for row in report["records"]]
    assert report["schema_version"] == 1
    assert report["root"] == root.path
    assert report["scope"]["mode"] == "visible_results"
    assert report["scope"]["query"]["categories"] == ["image"]
    assert report["metric"] == "logical"
    assert report["scan"] == {
        "status": "partial", "partial": True, "inaccessible_count": 2}


def test_age_breakdown_uses_modified_time_buckets_and_unknown():
    day = 86_400 * 1_000_000_000
    now = 10_000 * day
    root = Node(path="/r", name="r", is_dir=True)
    files = [
        Node(path=None, name="new.txt", is_dir=False, size=10, parent=root,
             modified_date=now - 2 * day),
        Node(path=None, name="year.jpg", is_dir=False, size=20, parent=root,
             modified_date=now - 400 * day),
        Node(path=None, name="ancient.jpg", is_dir=False, size=30, parent=root,
             modified_date=now - 2000 * day),
        Node(path=None, name="nodate.jpg", is_dir=False, size=40, parent=root),
    ]
    root.children = files
    stats = analysis.age_breakdown(root, now_ns=now)
    assert [(s.label, s.size, s.count) for s in stats] == [
        ("Last 30 days", 10, 1), ("1–3 years", 20, 1),
        ("Older than 3 years", 30, 1), ("Unknown date", 40, 1)]
    assert abs(sum(s.percent for s in stats) - 100) < 1e-9
    images = analysis.age_breakdown(root, filter_key="image", now_ns=now)
    assert [s.label for s in images] == ["1–3 years", "Older than 3 years", "Unknown date"]


def test_storage_summary_counts_hardlinks_once_only_when_identity_is_known():
    root = Node(path="/r", name="r", is_dir=True)
    a = Node(path=None, name="a", is_dir=False, size=100, parent=root,
             metadata=(4096, (1, 7), 2, False, False))
    b = Node(path=None, name="b", is_dir=False, size=100, parent=root,
             metadata=(4096, (1, 7), 2, False, False))
    c = Node(path=None, name="c", is_dir=False, size=10, parent=root,
             metadata=(4096, None, 1, False, False))
    root.children = [a, b, c]
    summary = analysis.storage_summary(root)
    assert summary.logical_bytes == 210
    assert summary.allocated_bytes == 3 * 4096
    assert summary.unique_allocated_bytes == 2 * 4096
    assert summary.hardlinked_files == 2 and summary.unknown_allocation == 0

    b._metadata = (4096, None, 2, False, False)   # identity unavailable
    assert analysis.storage_summary(root).unique_allocated_bytes is None


def test_storage_summary_reports_unknown_allocation_without_guessing():
    root = make_tree()
    summary = analysis.storage_summary(root)
    assert summary.allocated_bytes is None
    assert summary.unique_allocated_bytes is None
    assert summary.unknown_allocation == summary.files == 4
    assert summary.logical_bytes == 950
