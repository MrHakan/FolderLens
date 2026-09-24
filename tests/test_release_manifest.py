import hashlib

import pytest

from release_manifest import write_manifest


def test_manifest_hashes_all_release_assets_and_rejects_missing_input(tmp_path):
    exe = tmp_path / "FolderLens.exe"
    zip_asset = tmp_path / "FolderLens-4.0.0-win64.zip"
    manifest = tmp_path / "SHA256SUMS"
    exe.write_bytes(b"onefile")
    zip_asset.write_bytes(b"onedir")

    write_manifest([str(zip_asset), str(exe)], str(manifest))
    assert manifest.read_text().splitlines() == [
        f"{hashlib.sha256(b'onedir').hexdigest()}  {zip_asset.name}",
        f"{hashlib.sha256(b'onefile').hexdigest()}  {exe.name}",
    ]
    previous = manifest.read_bytes()
    zip_asset.unlink()
    with pytest.raises(FileNotFoundError):
        write_manifest([str(exe), str(zip_asset)], str(manifest))
    assert manifest.read_bytes() == previous


def test_manifest_rejects_ambiguous_duplicate_asset_names(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    (first / "same.exe").write_bytes(b"a")
    (second / "same.exe").write_bytes(b"b")
    with pytest.raises(ValueError, match="Duplicate"):
        write_manifest([str(first / "same.exe"), str(second / "same.exe")],
                       str(tmp_path / "SHA256SUMS"))
