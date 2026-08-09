<div align="center">

<img src="assets/icon.png" width="120" alt="FolderLens">

# FolderLens

**A fast, modern folder size analyzer for Windows.**
See what's eating your disk — as a tree, a treemap, a top-files list, or a file-type breakdown.

[![CI](https://github.com/MrHakan/FolderLens/actions/workflows/ci.yml/badge.svg)](https://github.com/MrHakan/FolderLens/actions/workflows/ci.yml)
[![Release](https://github.com/MrHakan/FolderLens/actions/workflows/release.yml/badge.svg)](https://github.com/MrHakan/FolderLens/actions/workflows/release.yml)

</div>

## Download

Grab the latest build from the [Releases page](https://github.com/MrHakan/FolderLens/releases/latest):

- **`FolderLens.exe`** — standalone, no Python required. Just run it.
- **`FolderLens-<version>-win64.zip`** — folder build; use this if your antivirus flags the single exe (see [Antivirus notes](docs/ANTIVIRUS.md)).

## Features

FolderLens scans a whole directory tree **once** — in parallel, across up to 32
worker threads — then lets you explore it four different ways with zero
rescanning:

- 🌳 **Tree view** — expandable folder tree with a usage bar, size, item count, type, and date at every level. Expanding a folder is instant.
- 🗺️ **Treemap** — WinDirStat-style colored map where every rectangle's area is its size. Hover for details, click a folder to zoom in, and drill back out.
- 🏆 **Largest files** — the top 100 biggest files anywhere in the tree, with their locations. Double-click to reveal in Explorer.
- 🧩 **File types** — size and count broken down by category (video, image, code, …) with proportional bars.

Plus:

- 🔎 **Instant search** across the whole tree (Ctrl+F)
- 🧵 **Fully responsive** — scanning, zipping, deleting, and exporting all run off the UI thread, with live progress and a **Stop** button
- 🗑️ **Manage** — multi-select to zip, delete, or open in Explorer (right-click, toolbar, or Delete key); sizes update without rescanning
- 📤 **Export** the full report to CSV
- 💽 **Disk usage** shown in the status bar (free / total)
- 🌗 **Light / dark** theme, remembered between sessions, along with your last folder and view
- ⬆️ **Auto-update** from GitHub releases
- 🖱️ **Explorer context menu** integration
- 🛡️ Handles "access denied" gracefully and flags how many items it couldn't read

## Usage

```bash
# open the app
python main.py

# analyze a specific folder
python main.py "C:\Users\Documents"

# console mode (no gui)
python main.py --console "C:\Users\Documents"

# Explorer context menu (run as admin)
python main.py --install
python main.py --uninstall

# print version
python main.py --version
```

### Keyboard shortcuts

| Key | Action |
| --- | --- |
| `F5` | Rescan current folder |
| `Ctrl+F` | Focus search |
| `Esc` | Clear search |
| `Delete` | Delete selected (tree view) |
| Double-click | Open folder / preview image |

## Requirements

- Windows 10/11
- Python 3.9+ (only when running from source)

## Antivirus false positives

PyInstaller executables are commonly false-flagged by antivirus engines. The
build is tuned to minimize this (no UPX, embedded metadata, manifest, icon, and
a folder-build alternative). If your machine still quarantines the download,
see **[docs/ANTIVIRUS.md](docs/ANTIVIRUS.md)** — the short version is: use the
`.zip` folder build, and/or report the false positive to Microsoft (they delist
confirmed ones quickly).

## Development

```bash
pip install -r requirements.txt pytest

# run the test suite
python -m pytest tests -v

# build both executables locally (Windows)
build.bat
```

### Project structure

```
FolderLens/
├── main.py               # entry point, CLI
├── app.py                # UI (customtkinter + ttk): tree, treemap, largest, types
├── scanner.py            # single-pass parallel tree scanner
├── analysis.py           # treemap layout, largest-files, type breakdown, CSV (pure, tested)
├── file_utils.py         # file type detection, formatting
├── updater.py            # auto-update handler
├── version.py            # version info
├── registry_installer.py # Windows Explorer context menu
├── FolderLens.spec       # antivirus-friendly PyInstaller build
├── app.manifest          # asInvoker + DPI + supported-OS manifest
├── make_version_info.py  # generates the embedded Windows version resource
├── assets/               # app icon (+ generator)
├── tests/                # pytest suite
├── docs/ANTIVIRUS.md     # false-positive guidance
└── .github/workflows/    # CI (tests) + Release (builds signed-metadata exe + zip)
```

## Releasing

Push a tag like `v3.0.0` (or run the **Release** workflow with a `tag` input).
It runs the tests, generates the version resource, builds the AV-friendly
one-file exe **and** the one-directory zip on Windows, smoke-tests the exe, and
publishes a GitHub release with both assets attached. The in-app updater picks
new releases up automatically.

## License

MIT
