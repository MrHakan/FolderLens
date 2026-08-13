import os
import sys
import threading

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duplicates
from scanner import TreeScanner


def scan(path):
    holder, done = {}, threading.Event()
    TreeScanner().scan(str(path),
                       on_complete=lambda r, e, t: (holder.update(root=r), done.set()),
                       on_error=lambda m: done.set())
    assert done.wait(90)
    return holder["root"]


@pytest.fixture
def tree(tmp_path):
    """
    twins: a.bin == b.bin == nested/c.bin  (identical)
    same size but different content: x.bin vs y.bin
    unique: solo.bin
    """
    payload = b"D" * 9000
    (tmp_path / "a.bin").write_bytes(payload)
    (tmp_path / "b.bin").write_bytes(payload)
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "c.bin").write_bytes(payload)

    (tmp_path / "x.bin").write_bytes(b"X" * 7000)
    (tmp_path / "y.bin").write_bytes(b"Y" * 7000)      # same size, different bytes
    (tmp_path / "solo.bin").write_bytes(b"S" * 5000)
    return tmp_path


def test_finds_identical_files(tree):
    groups = duplicates.find_duplicates(scan(tree), min_size=1)
    assert len(groups) == 1

    group = groups[0]
    assert group.count == 3
    assert {n.name for n in group.nodes} == {"a.bin", "b.bin", "c.bin"}
    assert group.size == 9000
    assert group.wasted == 18000            # two redundant copies


def test_same_size_different_content_is_not_a_duplicate(tree):
    groups = duplicates.find_duplicates(scan(tree), min_size=1)
    names = {n.name for g in groups for n in g.nodes}
    assert "x.bin" not in names and "y.bin" not in names


def test_unique_files_are_ignored(tree):
    groups = duplicates.find_duplicates(scan(tree), min_size=1)
    assert "solo.bin" not in {n.name for g in groups for n in g.nodes}


def test_min_size_skips_small_files(tree):
    assert duplicates.find_duplicates(scan(tree), min_size=100_000) == []


def test_groups_sorted_by_reclaimable_space(tmp_path):
    (tmp_path / "small1.bin").write_bytes(b"a" * 1000)
    (tmp_path / "small2.bin").write_bytes(b"a" * 1000)
    (tmp_path / "big1.bin").write_bytes(b"b" * 50000)
    (tmp_path / "big2.bin").write_bytes(b"b" * 50000)

    groups = duplicates.find_duplicates(scan(tmp_path), min_size=1)
    assert [g.size for g in groups] == [50000, 1000]
    assert duplicates.total_wasted(groups) == 51000


def test_cancellation_returns_nothing(tree):
    groups = duplicates.find_duplicates(scan(tree), min_size=1,
                                        should_cancel=lambda: True)
    assert groups == []


def test_progress_is_reported(tree):
    seen = []
    duplicates.find_duplicates(scan(tree), min_size=1,
                               progress=lambda stage, done, total: seen.append(stage))
    assert seen


def test_empty_tree(tmp_path):
    assert duplicates.find_duplicates(scan(tmp_path), min_size=1) == []


def test_keep_first_delete_rest_keeps_the_shortest_path(tree):
    group = duplicates.find_duplicates(scan(tree), min_size=1)[0]
    doomed = duplicates.keep_first_delete_rest(group)
    assert len(doomed) == group.count - 1
    kept = set(group.nodes) - set(doomed)
    keeper = kept.pop()
    assert all(len(keeper.path) <= len(n.path) for n in doomed)


def test_digest_helpers(tmp_path):
    a = tmp_path / "a.bin"
    b = tmp_path / "b.bin"
    a.write_bytes(b"hello world" * 100)
    b.write_bytes(b"hello world" * 100)

    assert duplicates.full_digest(str(a)) == duplicates.full_digest(str(b))
    assert duplicates.sample_digest(str(a)) == duplicates.sample_digest(str(b))

    b.write_bytes(b"different" * 100)
    assert duplicates.full_digest(str(a)) != duplicates.full_digest(str(b))


def test_digests_of_unreadable_file_are_none(tmp_path):
    missing = str(tmp_path / "nope.bin")
    assert duplicates.full_digest(missing) is None
    assert duplicates.sample_digest(missing) is None


def test_large_files_differing_only_in_the_middle(tmp_path):
    """The cheap sample pass must not be trusted on its own."""
    head_tail_same = b"H" * 70000 + b"M" * 10 + b"T" * 70000
    other = b"H" * 70000 + b"N" * 10 + b"T" * 70000
    (tmp_path / "one.bin").write_bytes(head_tail_same)
    (tmp_path / "two.bin").write_bytes(other)

    assert duplicates.sample_digest(str(tmp_path / "one.bin")) == \
           duplicates.sample_digest(str(tmp_path / "two.bin"))
    assert duplicates.find_duplicates(scan(tmp_path), min_size=1) == []
