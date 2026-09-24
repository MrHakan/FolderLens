"""Deterministic SHA-256 manifest for published release assets."""

import argparse
import hashlib
import os
import re


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_manifest(paths, output_path: str) -> None:
    assets = {}
    for path in paths:
        name = os.path.basename(path)
        if not name or name in assets or name == os.path.basename(output_path):
            raise ValueError(f"Duplicate or invalid release asset: {name!r}")
        assets[name] = path
    if not assets:
        raise ValueError("No release assets supplied")
    # Hash all files before opening the output, so a missing asset cannot
    # leave a truncated manifest behind.
    lines = [f"{sha256_file(assets[name])}  {name}\n" for name in sorted(assets)]
    with open(output_path, "w", encoding="ascii", newline="\n") as stream:
        stream.writelines(lines)


def expected_sha256(manifest: bytes, asset_name: str) -> str:
    """Read one unambiguous asset digest from a published SHA256SUMS file."""
    if os.path.basename(asset_name) != asset_name or not asset_name:
        raise ValueError("Invalid asset name")
    matches = []
    for line in manifest.decode("ascii").splitlines():
        digest, separator, name = line.partition("  ")
        if name == asset_name:
            if separator != "  " or not re.fullmatch(r"[0-9a-fA-F]{64}", digest):
                raise ValueError("Malformed checksum for release asset")
            matches.append(digest.lower())
    if len(matches) != 1:
        raise ValueError("Missing or ambiguous checksum for release asset")
    return matches[0]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("assets", nargs="+")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    write_manifest(args.assets, args.output)
