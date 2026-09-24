import os
import sys

import pytest
import csv

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from query import QueryEngine, QuerySpec, query_from_form
import analysis
from scanner import Node


def tree():
    root = Node("/root", "root", True)
    images = Node("/root/images", "images", True, parent=root)
    docs = Node("/root/docs", "docs", True, parent=root)
    image = Node(None, "picture.png", False, size=300, modified_date=100,
                 parent=images, metadata=(256, None, 1, False))
    hidden = Node(None, ".hidden.jpg", False, size=50, modified_date=200,
                  parent=images, metadata=(128, None, 1, False))
    video = Node(None, "clip.mp4", False, size=500, modified_date=300,
                 parent=root, metadata=(512, None, 1, False))
    note = Node(None, "notes.txt", False, size=100, modified_date=400,
                parent=docs, metadata=(128, None, 1, False))
    images.children.extend([image, hidden])
    images.size, images.item_count = 350, 2
    docs.children.append(note)
    docs.size, docs.item_count = 100, 1
    root.children.extend([images, docs, video])
    root.size, root.item_count = 950, 6
    return root, images, docs, image, hidden, video, note


def test_query_fields_combine_and_project_folders():
    root, images, docs, image, hidden, video, note = tree()
    spec = QuerySpec(categories=("image", "video", "image"),
                     extensions=("PNG", ".mp4"), min_size=250,
                     modified_after_ns=90, modified_before_ns=350)
    assert spec.categories == ("image", "video")
    index = QueryEngine(root).project(spec)
    assert index.count(root) == 2
    assert index.size(root) == 800
    assert index.children(root) == [images, video]
    assert index.children(images) == [image]
    assert index.count(docs) == 0
    assert not index.matches(hidden)


def test_scope_hidden_and_name_filter():
    root, images, docs, image, hidden, video, note = tree()
    spec = QuerySpec(root_scope="/root/images", categories=("image",),
                     include_hidden=False, name="PIC")
    index = QueryEngine(root).project(spec)
    assert index.scope is images
    assert index.size(images) == 300
    assert index.size(root) == 0
    assert index.matches(image)
    assert not index.matches(hidden)
    assert not index.matches(video)


def test_allocated_metric_never_substitutes_logical_bytes():
    root, images, docs, image, hidden, video, note = tree()
    index = QueryEngine(root).project(QuerySpec(categories=("image",), metric="allocated"))
    assert index.size(root) == 384
    image._metadata = None
    with pytest.raises(ValueError, match="Allocated size unavailable"):
        QueryEngine(root).project(QuerySpec(categories=("image",), metric="allocated"))


def test_cache_is_bounded_and_cancelled_projection_is_not_cached():
    root, *_ = tree()
    engine = QueryEngine(root, max_cache_entries=1, max_cached_directories=3)
    images = QuerySpec.category("image")
    videos = QuerySpec.category("video")
    first = engine.project(images)
    assert engine.project(images) is first
    assert engine.project(videos).count(root) == 1
    assert engine.project(images) is not first
    assert engine.project(QuerySpec.category("document"), should_cancel=lambda: True) is None
    assert len(engine._cache) == 1


def test_invalid_bounds_and_outside_scope_are_rejected():
    root, *_ = tree()
    with pytest.raises(ValueError):
        QuerySpec(min_size=20, max_size=10)
    with pytest.raises(ValueError):
        QuerySpec(categories=("imaginary",))
    with pytest.raises(ValueError, match="outside"):
        QueryEngine(root).project(QuerySpec(root_scope="/elsewhere"))


def test_search_rank_breakdowns_and_csv_share_query_scope(tmp_path):
    root, images, docs, image, hidden, video, note = tree()
    index = QueryEngine(root).project(QuerySpec(root_scope=images.path,
                                                extensions=("PNG",), min_size=200))
    assert analysis.largest_files(root, filter_index=index) == [image]
    assert analysis.find_matches(root, "picture", filter_index=index) == [image]
    assert analysis.find_matches(root, "notes", filter_index=index) == []
    assert [(row.label, row.size, row.count) for row in
            analysis.category_breakdown(root, filter_index=index)] == [("Image", 300, 1)]
    assert analysis.extension_breakdown(root, filter_index=index) == [("PNG", 300, 1)]
    destination = tmp_path / "report.csv"
    assert analysis.export_tree_csv(root, str(destination), filter_index=index) == 2
    with destination.open(newline="", encoding="utf-8") as output:
        rows = list(csv.DictReader(output))
    assert [row["Name"] for row in rows] == ["images", "picture.png"]
    assert all("extensions=('.png',)" in row["Scope"] for row in rows)


def test_query_can_rank_by_allocated_bytes_without_changing_logical_size():
    root, images, docs, image, hidden, video, note = tree()
    index = QueryEngine(root).project(QuerySpec(metric="allocated"))
    assert [n.name for n in analysis.largest_files(root, limit=2, filter_index=index)] == [
        "clip.mp4", "picture.png"]
    assert index.size(root) == 1024
    assert root.size == 950


def test_advanced_form_combines_fields_and_rejects_invalid_ranges():
    root, images, docs, image, hidden, video, note = tree()
    spec = query_from_form(categories=("image", "video"), extensions="png; mp4",
                           min_mib="0.0002", max_mib="0.001", include_hidden=False)
    index = QueryEngine(root).project(spec)
    assert index.children(root) == [images, video]
    assert index.children(images) == [image]
    assert index.count(root) == 2
    with pytest.raises(ValueError):
        query_from_form(min_mib="20", max_mib="10")
    with pytest.raises(ValueError):
        query_from_form(modified_after="2026-99-99")
