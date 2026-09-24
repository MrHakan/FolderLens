import pytest

from benchmarks.create_dataset import create_dataset


def test_dataset_generator_creates_deterministic_nested_files(tmp_path):
    root = tmp_path / "corpus"
    result = create_dataset(root, files=7, files_per_folder=2,
                            folders_per_bucket=2, max_bytes=3)

    files = sorted(root.rglob("*.bin"))
    expected_sizes = [(index * 37) % 4 for index in range(7)]
    assert len(files) == 7
    assert [path.stat().st_size for path in files] == expected_sizes
    assert result["files"] == 7
    assert result["logical_bytes"] == sum(expected_sizes)
    assert (root / "bucket-000" / "folder-000").is_dir()
    assert (root / "bucket-000" / "folder-001").is_dir()
    assert (root / "bucket-001" / "folder-000").is_dir()


def test_dataset_generator_refuses_to_touch_a_nonempty_directory(tmp_path):
    root = tmp_path / "existing"
    root.mkdir()
    keep = root / "keep.txt"
    keep.write_text("unchanged", encoding="utf-8")

    with pytest.raises(ValueError, match="must be empty"):
        create_dataset(root, files=1)

    assert keep.read_text(encoding="utf-8") == "unchanged"
    assert sorted(path.name for path in root.iterdir()) == ["keep.txt"]


@pytest.mark.parametrize("kwargs", [
    {"files": 0}, {"files": 1, "files_per_folder": 0},
    {"files": 1, "folders_per_bucket": 0}, {"files": 1, "max_bytes": -1},
])
def test_dataset_generator_rejects_invalid_limits(tmp_path, kwargs):
    with pytest.raises(ValueError):
        create_dataset(tmp_path / "corpus", **kwargs)
