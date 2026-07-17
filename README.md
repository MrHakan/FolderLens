# FolderLens

[![CI](https://github.com/MrHakan/FolderLens/actions/workflows/ci.yml/badge.svg)](https://github.com/MrHakan/FolderLens/actions/workflows/ci.yml)
[![Release](https://github.com/MrHakan/FolderLens/actions/workflows/release.yml/badge.svg)](https://github.com/MrHakan/FolderLens/actions/workflows/release.yml)

A lightweight folder size analyzer for Windows with Explorer context menu integration.

## Download

Grab the latest standalone `FolderLens.exe` from the [Releases page](https://github.com/MrHakan/FolderLens/releases/latest) — no Python required.

## Features

- **Tree view** — browse folders and the files inside them as an expandable tree, with sizes, usage bars, item counts, and dates at every level
- **One scan, instant navigation** — the whole directory tree is scanned once (in parallel, up to 32 workers); expanding any folder afterwards is instant, no rescanning
- Async scanning (UI doesn't freeze) with live progress
- Visual usage bars showing each item's share of its parent folder
- File type icons and color coding
- Sortable columns with ascending/descending toggle (name, size, items, type, date)
- Multi-select (Ctrl/Shift) with zip, delete, and open-in-Explorer actions
- Right-click context menu inside the tree; Delete key and F5 shortcuts
- Image preview on double-click
- Light/dark mode toggle (persisted between sessions)
- Auto-update from GitHub releases
- Windows Explorer right-click context menu integration
- Handles "access denied" errors gracefully

## Installation

### Standalone exe (recommended)

1. Download `FolderLens.exe` from [Releases](https://github.com/MrHakan/FolderLens/releases/latest)
2. Put it wherever you like (e.g. `C:\Program Files\FolderLens`)
3. Optional — add the Explorer context menu: run `FolderLens.exe --install` as administrator

### From source

```bash
pip install -r requirements.txt
python main.py --install  # run as admin for context menu
```

### Uninstall

```bash
FolderLens.exe --uninstall
```

or use `FolderLens_Uninstall.reg`.

## Usage

```bash
# open gui
python main.py

# analyze specific folder
python main.py "C:\Users\Documents"

# console mode (no gui)
python main.py --console "C:\Users\Documents"

# context menu install/uninstall
python main.py --install
python main.py --uninstall

# print version
python main.py --version
```

## UI controls

- **📂 Browse** - pick a folder to analyze
- **⬅ Up** - go to parent folder
- **🔄 Refresh / F5** - rescan the current folder
- **📦 Zip / 🗑️ Delete** - act on the selected rows (Ctrl/Shift multi-select)
- **☀️/🌙** - toggle light/dark mode
- **⬆** - check for updates
- **•••** - settings (row size, preview toggle)
- **Right-click** - open in Explorer, zip, delete
- **Column headers** - click to sort, click again to reverse

## Requirements

- Windows 10/11
- Python 3.9+ (only when running from source)

## Project structure

```
FolderLens/
├── main.py               # entry point, cli
├── app.py                # ui (customtkinter)
├── scanner.py            # async parallel folder scanner
├── file_utils.py         # file type detection, formatting
├── version.py            # version info
├── updater.py            # auto-update handler
├── registry_installer.py # windows registry operations
├── simple_installer.py   # python-based installer
├── build.bat             # local build script
├── requirements.txt
├── tests/                # pytest suite
├── .github/workflows/    # ci + release automation
└── installer/
    └── FolderLens_Setup.iss  # inno setup script
```

## Development

```bash
pip install -r requirements.txt pytest

# run tests
python -m pytest tests -v

# build exe locally
build.bat
```

## Releasing

Releases are automated. Pushing a tag like `v1.1.0` triggers the [release workflow](.github/workflows/release.yml), which runs the tests, builds `FolderLens.exe` with PyInstaller on Windows, and publishes a GitHub release with the exe attached. The in-app updater picks new releases up automatically.

## Updates

The app checks GitHub releases for updates. To point it at your own fork, edit `version.py` (`GITHUB_OWNER`, `GITHUB_REPO`).

## License

MIT
