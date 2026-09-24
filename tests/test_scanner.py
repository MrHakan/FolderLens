import gc
import os
import stat as stat_module
import sys
import threading
import time
import traceback
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scanner as scanner_module
from scanner import TreeScanner, FolderScanner, QuickScanner, Node, ScanSession, is_network_path


@pytest.fixture
def sample_tree(tmp_path):
    (tmp_path / "file_a.txt").write_bytes(b"x" * 100)
    (tmp_path / "file_b.bin").write_bytes(b"y" * 250)

    sub = tmp_path / "subdir"
    sub.mkdir()
    (sub / "nested.txt").write_bytes(b"z" * 50)

    deep = sub / "deeper"
    deep.mkdir()
    (deep / "deep.txt").write_bytes(b"w" * 25)

    return tmp_path


def run_tree_scan(path):
    scanner = TreeScanner()
    holder = {"root": None, "errors": None, "error": None}
    done = threading.Event()

    def on_complete(root, errors, scan_time):
        holder["root"] = root
        holder["errors"] = errors
        done.set()

    def on_error(error):
        holder["error"] = error
        done.set()

    scanner.scan(str(path), on_complete=on_complete, on_error=on_error)
    assert done.wait(timeout=90), "scan did not finish in time (machine under load?)"
    return holder


def run_flat_scan(path):
    scanner = FolderScanner()
    holder = {"result": None, "error": None}
    done = threading.Event()

    def on_complete(result):
        holder["result"] = result
        done.set()

    def on_error(error):
        holder["error"] = error
        done.set()

    scanner.scan(str(path), on_complete=on_complete, on_error=on_error)
    assert done.wait(timeout=90), "scan did not finish in time (machine under load?)"
    return holder["result"], holder["error"]


def test_tree_scan_builds_full_tree(sample_tree):
    holder = run_tree_scan(sample_tree)
    assert holder["error"] is None
    root = holder["root"]
    assert root is not None
    assert root.is_dir

    by_name = {c.name: c for c in root.children}
    assert set(by_name) == {"file_a.txt", "file_b.bin", "subdir"}
    assert by_name["file_a.txt"].size == 100
    assert by_name["file_b.bin"].size == 250

    subdir = by_name["subdir"]
    assert subdir.is_dir
    assert subdir.size == 75
    # nested.txt + deeper dir + deep.txt
    assert subdir.item_count == 3

    sub_children = {c.name: c for c in subdir.children}
    assert set(sub_children) == {"nested.txt", "deeper"}
    assert sub_children["deeper"].size == 25
    assert sub_children["deeper"].children[0].name == "deep.txt"
    assert sub_children["deeper"].children[0].parent is sub_children["deeper"]

    assert root.size == 425
    # file_a + file_b + subdir + nested.txt + deeper + deep.txt
    assert root.item_count == 6
    assert holder["errors"] == []
    assert by_name["file_a.txt"].modified_date > 0


def test_tree_scan_sorted_children(sample_tree):
    root = run_tree_scan(sample_tree)["root"]
    by_size = root.sorted_children("size", reverse=True)
    assert [c.name for c in by_size] == ["file_b.bin", "file_a.txt", "subdir"]

    by_name = root.sorted_children("name", reverse=False)
    assert [c.name for c in by_name] == ["file_a.txt", "file_b.bin", "subdir"]


def test_wide_child_sort_is_correct_and_cancellable():
    root = Node("/synthetic", "synthetic", True)
    root.children = [Node(None, f"item{number:05}.txt", False,
                          size=number, parent=root)
                     for number in reversed(range(20_000))]

    ordered = root.sorted_children("size", reverse=False,
                                   should_cancel=lambda: False)
    assert len(ordered) == 20_000
    assert [ordered[0].size, ordered[-1].size] == [0, 19_999]

    checks = 0

    def cancel_during_merge():
        nonlocal checks
        checks += 1
        return checks >= 7

    assert root.sorted_children("size", should_cancel=cancel_during_merge) == []
    assert checks == 7


def test_tree_scan_missing_folder(tmp_path):
    holder = run_tree_scan(tmp_path / "does_not_exist")
    assert holder["root"] is None
    assert "not found" in holder["error"].lower()


def test_tree_scan_file_not_folder(tmp_path):
    target = tmp_path / "plain.txt"
    target.write_text("hello")
    holder = run_tree_scan(target)
    assert holder["root"] is None
    assert "not a folder" in holder["error"].lower()


def test_tree_scan_empty_folder(tmp_path):
    holder = run_tree_scan(tmp_path)
    root = holder["root"]
    assert holder["error"] is None
    assert root.children == []
    assert root.size == 0
    assert root.item_count == 0


def test_flat_scan_sizes(sample_tree):
    result, error = run_flat_scan(sample_tree)
    assert error is None

    by_name = {item.name: item for item in result.items}
    assert by_name["file_a.txt"].size == 100
    assert by_name["subdir"].size == 75
    assert by_name["subdir"].is_directory
    assert by_name["subdir"].item_count == 3

    assert result.total_size == 425
    assert result.total_items == 3
    assert result.errors == []


def test_flat_scan_missing_folder(tmp_path):
    result, error = run_flat_scan(tmp_path / "does_not_exist")
    assert result is None
    assert "not found" in error.lower()


def test_quick_scanner(sample_tree):
    items = QuickScanner().scan_first_level(str(sample_tree))
    names = {item.name for item in items}
    assert names == {"file_a.txt", "file_b.bin", "subdir"}

    dirs = [i for i in items if i.is_directory]
    assert len(dirs) == 1
    assert dirs[0].size == 0


def test_node_uses_slots_to_keep_the_tree_small():
    """One Node exists per file on disk, so the per-instance __dict__ matters."""
    node = Node(path="/x/a.txt", name="a.txt", is_dir=False, size=1)
    assert not hasattr(node, "__dict__"), "Node grew a __dict__ again"
    assert hasattr(Node, "__slots__")


def test_files_share_one_empty_children_container():
    """Files can never have children; giving each its own list wasted memory."""
    a = Node(path="/x/a.txt", name="a.txt", is_dir=False)
    b = Node(path="/x/b.txt", name="b.txt", is_dir=False)
    assert a.children is b.children
    assert len(a.children) == 0

    # directories still get their own mutable list
    d1 = Node(path="/x/d1", name="d1", is_dir=True)
    d2 = Node(path="/x/d2", name="d2", is_dir=True)
    assert d1.children is not d2.children
    d1.children.append(a)
    assert d2.children == []


def test_network_paths_use_a_smaller_worker_pool(tmp_path):
    assert is_network_path(r"\\server\share\work")
    assert not is_network_path(str(tmp_path))
    assert TreeScanner.worker_limit(r"\\server\share\work") == min(
        TreeScanner.MAX_WORKERS, TreeScanner.NETWORK_WORKERS)
    assert TreeScanner.worker_limit(str(tmp_path)) == TreeScanner.MAX_WORKERS


def test_stalled_old_scan_cannot_complete_or_block_new_scan(tmp_path, monkeypatch):
    """A stuck share call may outlive cancellation; its event remains private."""
    # Earlier GUI tests can leave cyclic Tk Font objects behind.  Collect on
    # the Tk/main thread so their Tcl destructors cannot run in a scan worker.
    gc.collect()
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (second / "new.txt").write_text("new")
    scanner = TreeScanner()
    entered = threading.Event()
    release = threading.Event()
    done = threading.Event()
    results = []
    failures = []
    observations = []
    original = scanner._read_directory

    def stalled(node, errors, on_progress=None, work_queue=None, session=None,
                on_snapshot=None, on_event=None, capture_extended=False):
        if node.path == str(first):
            entered.set()
            assert release.wait(timeout=30)
        return original(node, errors, on_progress, work_queue, session,
                        on_snapshot, on_event, capture_extended)

    monkeypatch.setattr(scanner, "_read_directory", stalled)
    scanner.scan(str(first), on_complete=lambda *args: results.append("old"))
    old_thread = scanner._current_thread
    assert entered.wait(timeout=5)
    scanner.scan(str(second), on_complete=lambda *args: (results.append("new"), done.set()),
                 on_error=lambda message: (failures.append(message), done.set()),
                 on_snapshot=observations.append)
    def scan_stacks():
        frames = sys._current_frames()
        return "\n".join(
            f"{thread.name}:\n{''.join(traceback.format_stack(frames[thread.ident]))}"
            for thread in threading.enumerate()
            if thread.name.startswith("folderlens-scan-") and thread.ident in frames
        )

    try:
        assert done.wait(timeout=15), ("a blocked old scan delayed the new one; "
                                       f"observations={observations[-2:]!r}; "
                                       f"threads={[t.name for t in threading.enumerate()]!r}; "
                                       f"scan stacks={scan_stacks()}")
        assert not failures, failures
    finally:
        release.set()
    old_thread.join(timeout=5)
    scanner._current_thread.join(timeout=5)
    assert not old_thread.is_alive()
    assert results == ["new"]
    assert not scanner.is_scanning


def test_bounded_queue_wide_tree_completes_with_small_worker_pool(tmp_path, monkeypatch):
    """Producers never all block while the queue is full of child folders."""
    for number in range(80):
        folder = tmp_path / f"folder{number}"
        folder.mkdir()
        (folder / "file.txt").write_bytes(b"a" * 12)
    monkeypatch.setattr(TreeScanner, "QUEUE_PER_WORKER", 1)
    monkeypatch.setattr(TreeScanner, "worker_limit", lambda cls, path: 2)
    scanner = TreeScanner()
    snapshots, events, done = [], [], threading.Event()
    result = {}
    scanner.scan(str(tmp_path), on_snapshot=snapshots.append, on_event=events.append,
                 on_complete=lambda root, errors, elapsed: (result.update(root=root, errors=errors), done.set()),
                 on_error=lambda message: (result.update(error=message), done.set()))
    assert done.wait(10), "workers deadlocked on a full queue"
    scanner._current_thread.join(timeout=5)
    assert "error" not in result
    assert result["root"].item_count == 160
    assert result["root"].size == 960
    assert snapshots[-1].state == "complete"
    assert snapshots[-1].partial is False
    assert snapshots[-1].known_files == 80
    assert snapshots[-1].known_bytes == 960
    assert snapshots[-1].queue_high_water <= 8
    assert snapshots[-1].deferred_high_water > 0
    assert {e.kind for e in events} >= {
        "directory-start", "directory-complete", "batch-of-entries", "complete"}


def test_partial_snapshot_is_available_while_child_share_is_blocked(tmp_path, monkeypatch):
    (tmp_path / "known.txt").write_bytes(b"12345")
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    entered, release, done = threading.Event(), threading.Event(), threading.Event()
    snapshot_ready = threading.Event()
    scanner = TreeScanner()
    scanner.SNAPSHOT_INTERVAL = 0
    original = scanner._read_directory
    snapshots = []

    def slow(node, errors, on_progress=None, work_queue=None, session=None,
             on_snapshot=None, on_event=None, capture_extended=False):
        if node.path == str(blocked):
            entered.set()
            assert release.wait(30)
        return original(node, errors, on_progress, work_queue, session,
                        on_snapshot, on_event, capture_extended)

    def observe(snapshot):
        snapshots.append(snapshot)
        if snapshot.state == "scanning" and snapshot.known_bytes == 5 and snapshot.partial:
            snapshot_ready.set()

    monkeypatch.setattr(scanner, "_read_directory", slow)
    scanner.scan(str(tmp_path), on_snapshot=observe,
                 on_complete=lambda *args: done.set())
    try:
        assert entered.wait(20), f"scan did not reach child: {snapshots[-1:]!r}"
        assert snapshot_ready.wait(20)
        assert not done.is_set()
        assert any(s.state == "scanning" and s.known_bytes == 5 and s.partial
                   for s in snapshots)
        assert any(s.state == "scanning" and
                   any(sample.name == "known.txt" and sample.size == 5
                       for sample in s.observed_files)
                   for s in snapshots)
    finally:
        release.set()
    assert done.wait(20)


def test_inaccessible_subtree_keeps_observed_bytes_partial(tmp_path, monkeypatch):
    good, denied = tmp_path / "good", tmp_path / "denied"
    good.mkdir()
    denied.mkdir()
    (good / "known.txt").write_bytes(b"known")
    scanner = TreeScanner()
    original = os.scandir

    def denied_scan(path):
        if path == str(denied):
            raise PermissionError("permission denied")
        return original(path)

    monkeypatch.setattr(os, "scandir", denied_scan)
    snapshots, done, result = [], threading.Event(), {}
    scanner.scan(str(tmp_path), on_snapshot=snapshots.append,
                 on_complete=lambda root, errors, elapsed: (result.update(root=root, errors=errors), done.set()))
    assert done.wait(5)
    scanner._current_thread.join(timeout=5)
    assert result["root"].size == 5
    assert len(result["errors"]) == 1
    assert snapshots[-1].state == "complete"
    assert snapshots[-1].partial is True
    assert snapshots[-1].known_bytes == 5


def test_scan_snapshot_keeps_only_the_largest_fifty_observed_files(tmp_path):
    for number in range(80):
        (tmp_path / f"file-{number:03}.bin").write_bytes(b"x" * (number + 1))

    scanner = TreeScanner()
    scanner.SNAPSHOT_INTERVAL = 0
    done = threading.Event()
    snapshots = []
    scanner.scan(str(tmp_path), on_snapshot=snapshots.append,
                 on_complete=lambda *args: done.set())
    assert done.wait(10)
    scanner._current_thread.join(timeout=5)

    final = snapshots[-1]
    assert len(final.observed_files) == 50
    assert [sample.size for sample in final.observed_files] == list(range(80, 30, -1))
    assert final.observed_files[0].name == "file-079.bin"


def test_slow_metadata_call_flushes_first_file_sample_before_next_entry(tmp_path, monkeypatch):
    (tmp_path / "first.txt").write_bytes(b"first")
    (tmp_path / "second.txt").write_bytes(b"second")
    entered_second, release_second, sample_seen, done = (
        threading.Event(), threading.Event(), threading.Event(), threading.Event())
    snapshots, events, result = [], [], {}
    original_scandir = os.scandir

    class DelayedEntry:
        def __init__(self, entry, position):
            self._entry = entry
            self._position = position

        def __getattr__(self, name):
            return getattr(self._entry, name)

        def stat(self, *args, **kwargs):
            if self._position == 0:
                time.sleep(0.2)
            elif self._position == 1:
                entered_second.set()
                assert release_second.wait(10)
            return self._entry.stat(*args, **kwargs)

    class DelayedScandir:
        def __enter__(self):
            self._iterator = original_scandir(tmp_path)
            self._entries = sorted(list(self._iterator), key=lambda entry: entry.name)
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            self._iterator.close()
            return False

        def __iter__(self):
            return (DelayedEntry(entry, position)
                    for position, entry in enumerate(self._entries))

    def slow_scandir(path):
        if os.path.abspath(path) == os.path.abspath(tmp_path):
            return DelayedScandir()
        return original_scandir(path)

    monkeypatch.setattr(os, "scandir", slow_scandir)
    scanner = TreeScanner()

    def observe(snapshot):
        snapshots.append(snapshot)
        if (snapshot.state == "scanning"
                and any(sample.name == "first.txt" for sample in snapshot.observed_files)):
            sample_seen.set()

    scanner.scan(str(tmp_path), on_snapshot=observe, on_event=events.append,
                 on_error=lambda message: (result.update(error=message), done.set()),
                 on_complete=lambda root, errors, elapsed: (result.update(root=root), done.set()))
    try:
        assert entered_second.wait(5), f"scan={result!r}, snapshots={snapshots!r}, events={events!r}"
        assert sample_seen.is_set()
        assert any(sample.name == "first.txt" and sample.size == 5
                   for snapshot in snapshots if snapshot.state == "scanning"
                   for sample in snapshot.observed_files)
    finally:
        release_second.set()
    assert done.wait(10)
    scanner._current_thread.join(timeout=5)
    assert result["root"].size == 11


def test_closed_progress_consumer_does_not_break_scan(tmp_path):
    (tmp_path / "file.txt").write_bytes(b"content")
    scanner = TreeScanner()
    done, result = threading.Event(), {}

    def closed_ui(*args):
        raise RuntimeError("window was closed")

    scanner.scan(str(tmp_path), on_snapshot=closed_ui, on_event=closed_ui,
                 on_complete=lambda root, errors, elapsed: (result.update(root=root), done.set()))
    assert done.wait(5)
    assert result["root"].size == 7


def test_reparse_links_are_recorded_without_following_them(tmp_path):
    folder = tmp_path / "folder"
    folder.mkdir()
    (folder / "one.txt").write_bytes(b"hi")
    link = folder / "back"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("directory symlinks unavailable")
    root = run_tree_scan(tmp_path)["root"]
    scanned = root.children[0]
    by_name = {child.name: child for child in scanned.children}
    assert by_name["back"].is_reparse_point
    assert not by_name["back"].is_dir
    assert root.item_count == 3


def test_extended_metadata_reports_hardlinks_without_claiming_unique_bytes(tmp_path):
    first, second = tmp_path / "first.dat", tmp_path / "second.dat"
    first.write_bytes(b"a" * 4096)
    try:
        os.link(first, second)
    except (OSError, NotImplementedError):
        pytest.skip("hardlinks unavailable")
    done, result = threading.Event(), {}
    scanner = TreeScanner()
    scanner.scan(str(tmp_path), capture_extended=True,
                 on_complete=lambda root, errors, elapsed: (result.update(root=root), done.set()))
    assert done.wait(5)
    children = result["root"].children
    if children[0].link_count is None:
        # DirEntry.stat can omit this field on Windows. Do not infer identity.
        assert all(child.link_count is None and child.file_identity is None
                   for child in children)
    else:
        assert children[0].link_count >= 2
        assert children[0].file_identity == children[1].file_identity
    assert result["root"].logical_size == 8192  # logical paths, not unique blocks
    assert children[0].mtime_ns > 0
    if children[0].allocated_size is not None:
        assert result["root"].allocated_size == sum(child.allocated_size for child in children)


def test_windows_hidden_attribute_is_retained_without_extended_metadata(monkeypatch, tmp_path):
    monkeypatch.setattr(scanner_module.stat_module, "FILE_ATTRIBUTE_HIDDEN", 0x2,
                        raising=False)
    entry_stat = SimpleNamespace(
        st_mode=stat_module.S_IFREG | 0o644,
        st_size=5,
        st_ctime=1.0,
        st_mtime_ns=2,
        st_file_attributes=0x2,
    )

    class Entry:
        name = "system-hidden.jpg"
        path = str(tmp_path / name)

        def stat(self, follow_symlinks=False):
            return entry_stat

    class Entries:
        def __enter__(self):
            return iter((Entry(),))

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(scanner_module.os, "scandir", lambda _path: Entries())
    root = Node(path=str(tmp_path), name=tmp_path.name, is_dir=True)

    errors = TreeScanner()._read_directory(root, [], session=ScanSession(1, str(tmp_path)))

    assert errors is None
    assert root.children[0].name == "system-hidden.jpg"
    assert root.children[0].is_hidden
    assert not root.children[0].is_reparse_point


def _stall_first_child_read(monkeypatch, scanner, root_path):
    """Block the first directory read below *root_path* until released."""
    entered, release = threading.Event(), threading.Event()
    original = scanner._read_directory
    stalled = []

    def stall(node, errors, on_progress=None, work_queue=None, session=None,
              on_snapshot=None, on_event=None, capture_extended=False):
        if node.path != str(root_path) and not stalled:
            stalled.append(node.path)
            entered.set()
            assert release.wait(timeout=30)
        return original(node, errors, on_progress, work_queue, session,
                        on_snapshot, on_event, capture_extended)

    monkeypatch.setattr(scanner, "_read_directory", stall)
    return entered, release, stalled


def test_skipping_a_stuck_folder_finishes_the_scan_as_partial(tmp_path, monkeypatch):
    gc.collect()
    for number in range(20):
        folder = tmp_path / f"folder{number:02d}"
        folder.mkdir()
        (folder / "file.bin").write_bytes(b"a" * 10)
    # One worker whose overflow stack holds most folders: the replacement
    # worker must inherit that stack, or those folders would silently vanish.
    monkeypatch.setattr(TreeScanner, "worker_limit", lambda cls, path: 1)
    monkeypatch.setattr(ScanSession, "SLOW_DIRECTORY_SECONDS", 0.0)
    scanner = TreeScanner()
    entered, release, stalled = _stall_first_child_read(monkeypatch, scanner, tmp_path)
    done, result = threading.Event(), {}
    snapshots = []
    scanner.scan(str(tmp_path), on_snapshot=snapshots.append,
                 on_complete=lambda root, errors, elapsed: (
                     result.update(root=root, errors=errors), done.set()),
                 on_error=lambda message: (result.update(error=message), done.set()))
    try:
        assert entered.wait(10)
        live = scanner.current_snapshot()
        assert live.state == "scanning"
        assert [path for path, _age in live.slow_directories] == stalled
        assert not scanner.skip_directory(str(tmp_path)), "the scan root cannot be skipped"
        assert scanner.skip_directory(stalled[0])
        assert not scanner.skip_directory(stalled[0]), "a folder is skipped only once"
        assert done.wait(10), "the scan kept waiting on a skipped folder"
    finally:
        release.set()
    assert "error" not in result
    root = result["root"]
    by_name = {child.name: child for child in root.children}
    skipped = by_name[os.path.basename(stalled[0])]
    assert skipped.error == "Skipped"
    assert skipped.children == [] and skipped.size == 0
    others = [child for child in root.children if child is not skipped]
    assert len(others) == 19
    assert all(child.error is None and child.size == 10 for child in others)
    assert root.size == 190
    assert any(message.startswith("Skipped by user:") for message in result["errors"])
    final = snapshots[-1]
    assert final.state == "complete" and final.partial
    assert final.skipped_directories == 1
    assert final.slow_directories == ()
    # The stuck read returns late; it must not change the finished tree.
    for thread in threading.enumerate():
        if thread is not threading.current_thread() and thread.daemon:
            thread.join(timeout=0.5)
    assert skipped.children == [] and skipped.error == "Skipped"
    assert root.size == 190


def test_stop_reports_cancelling_while_a_folder_read_is_stuck(tmp_path, monkeypatch):
    gc.collect()
    (tmp_path / "child").mkdir()
    scanner = TreeScanner()
    entered, release, _stalled = _stall_first_child_read(monkeypatch, scanner, tmp_path)
    finished = []
    scanner.scan(str(tmp_path), on_complete=lambda *args: finished.append(True))
    try:
        assert entered.wait(10)
        scanner.cancel()
        snapshot = scanner.current_snapshot()
        assert snapshot.state == "scanning" and snapshot.cancelling
        assert scanner.is_scanning
        assert not scanner.skip_directory(str(tmp_path / "child"))
    finally:
        release.set()
    scanner._current_thread.join(timeout=10)
    assert scanner.current_snapshot().state == "cancelled"
    assert not scanner.current_snapshot().cancelling
    assert not finished


@pytest.mark.skipif(os.name != "nt", reason="Windows on-disk metadata backend")
def test_windows_extended_scan_reports_on_disk_size_and_hardlink_identity(tmp_path):
    first, second = tmp_path / "first.dat", tmp_path / "second.dat"
    first.write_bytes(b"a" * 5000)
    os.link(first, second)
    (tmp_path / "tiny.txt").write_bytes(b"x")
    done, result = threading.Event(), {}
    TreeScanner().scan(str(tmp_path), capture_extended=True,
                       on_complete=lambda root, errors, elapsed: (
                           result.update(root=root), done.set()))
    assert done.wait(10)
    children = {child.name: child for child in result["root"].children}
    for child in children.values():
        assert child.allocated_size is not None
        assert child.allocated_size >= 0
    assert children["first.dat"].allocated_size >= 5000
    assert children["first.dat"].link_count == 2
    assert children["first.dat"].file_identity == children["second.dat"].file_identity
    assert children["tiny.txt"].link_count == 1
    import analysis
    summary = analysis.storage_summary(result["root"])
    assert summary.hardlinked_files == 2
    assert summary.unique_allocated_bytes == (summary.allocated_bytes
                                              - children["second.dat"].allocated_size)
