"""Exercise the external updater with a packaged EXE on a Windows CI runner."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from release_manifest import sha256_file
from updater import create_swap_script


def run_swap(current: Path, replacement: Path, backup: Path, log: Path):
    script = create_swap_script()
    env = os.environ.copy()
    env.update({
        "FL_UPDATE_CURRENT": str(current),
        "FL_UPDATE_NEW": str(replacement),
        "FL_UPDATE_BACKUP": str(backup),
        "FL_UPDATE_LOG": str(log),
        "FL_UPDATE_TEST_DELAY": "0",
        "FL_UPDATE_NO_RESTART": "1",
    })
    try:
        return subprocess.run(["cmd", "/c", script], env=env, timeout=60,
                              capture_output=True, text=True)
    finally:
        os.unlink(script)


def smoke_version(exe: Path):
    result = subprocess.run([str(exe), "--version"], timeout=60)
    assert result.returncode == 0, f"{exe} --version exited with {result.returncode}"


def verify_legacy_upgrade(exe: str, legacy_exe: str):
    expected_hash = sha256_file(exe)
    with tempfile.TemporaryDirectory(prefix="folderlens-legacy-upgrade-") as folder:
        root = Path(folder)
        current = root / "FolderLens.exe"
        replacement = root / "staged.exe"
        backup = root / "backup.exe"
        log = root / "swap.log"
        shutil.copy2(legacy_exe, current)
        legacy_hash = sha256_file(current)
        smoke_version(current)
        shutil.copy2(exe, replacement)

        result = run_swap(current, replacement, backup, log)
        assert result.returncode == 0, result.stdout + result.stderr
        assert sha256_file(current) == expected_hash
        assert sha256_file(backup) == legacy_hash
        smoke_version(current)


def main(exe: str, legacy_exe: str = None):
    expected_hash = sha256_file(exe)
    with tempfile.TemporaryDirectory(prefix="folderlens-swap-smoke-") as folder:
        root = Path(folder)
        current, replacement = root / "FolderLens.exe", root / "staged.exe"
        backup, log = root / "backup.exe", root / "swap.log"

        current.write_bytes(b"previous installation")
        shutil.copy2(exe, replacement)
        result = run_swap(current, replacement, backup, log)
        assert result.returncode == 0, result.stdout + result.stderr
        assert sha256_file(current) == expected_hash
        assert backup.read_bytes() == b"previous installation"

        current.write_bytes(b"previous installation again")
        replacement.write_bytes(b"invalid executable")
        backup.unlink()
        result = run_swap(current, replacement, backup, log)
        assert result.returncode != 0, "invalid replacement was accepted"
        assert current.read_bytes() == b"previous installation again", result.stdout + result.stderr
        assert not backup.exists(), "old executable was not restored"

    if legacy_exe:
        verify_legacy_upgrade(exe, legacy_exe)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
