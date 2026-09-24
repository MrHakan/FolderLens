"""Build the Inno Setup package from the current application version."""

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from version import VERSION


def find_compiler(explicit=None):
    candidates = [explicit, os.environ.get("ISCC_PATH"), shutil.which("ISCC.exe"),
                  shutil.which("ISCC")]
    for base in (os.environ.get("ProgramFiles(x86)"), os.environ.get("ProgramFiles")):
        if base:
            candidates.append(str(Path(base) / "Inno Setup 6" / "ISCC.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return str(Path(candidate).resolve())
    raise FileNotFoundError(
        "Inno Setup 6 compiler (ISCC.exe) was not found. Install Inno Setup 6 "
        "or set ISCC_PATH to its compiler path.")


def build(output_dir=None, compiler=None, run=subprocess.run):
    """Compile the installer; fail if its filename drifts from version.py."""
    compiler = find_compiler(compiler)
    output_dir = Path(output_dir or ROOT / "installer_output").resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    script = ROOT / "installer" / "FolderLens_Setup.iss"
    command = [compiler, f"/DMyAppVersion={VERSION}", f"/O{output_dir}", str(script)]
    run(command, cwd=str(ROOT), check=True)
    output = output_dir / f"FolderLens_Setup_{VERSION}.exe"
    if not output.is_file():
        raise FileNotFoundError(f"Inno Setup did not produce {output}")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iscc", help="Path to ISCC.exe; defaults to PATH and Inno Setup 6 locations")
    parser.add_argument("--output-dir", help="Installer output directory")
    args = parser.parse_args()
    result = build(args.output_dir, args.iscc)
    print(f"Built {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
