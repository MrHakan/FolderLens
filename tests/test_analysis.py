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


def test_export_tree_csv(tmp_path):
    root = make_tree()
    out = tmp_path / "report.csv"
    rows = analysis.export_tree_csv(root, str(out))
    assert rows == 5  # a, b, sub, c, d

    content = out.read_text(encoding="utf-8")
    assert "Path,Name,Type,Size (bytes)" in content
    assert "b.mp4" in content
    assert "sub" in content


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
    assert analysis.find_matches(root, "") == []
