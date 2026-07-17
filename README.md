# FolderLens

[![CI](https://github.com/MrHakan/FolderLens/actions/workflows/ci.yml/badge.svg)](https://github.com/MrHakan/FolderLens/actions/workflows/ci.yml)
[![Release](https://github.com/MrHakan/FolderLens/actions/workflows/release.yml/badge.svg)](https://github.com/MrHakan/FolderLens/actions/workflows/release.yml)

A lightweight folder size analyzer for Windows with Explorer context menu integration.

## Download

Grab the latest standalone `FolderLens.exe` from the [Releases page](https://github.com/MrHakan/FolderLens/releases/latest) — no Python required.

## Features

- Recursive scanning of folders and subfolders
- Parallel directory sizing — first-level folders are sized concurrently
- Async scanning (UI doesn't freeze)
- Visual usage bars showing relative size
- File type icons and color coding
- Category filter (folders, files, video, image, code, ...)
- Sortable columns with ascending/descending toggle (size, name, type, date)
- Folder and file selection across directories
- Analyze panel with zip and delete options
- Image preview on double-click
- Light/dark mode toggle (persisted between sessions)
- Auto-update from GitHub releases
- Right-click context menu integration
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

- **☀️/🌙** - toggle light/dark mode
- **⬆** - check for updates
- **📊** - open analyze panel for selected items
- **•••** - settings (icon size, preview toggle)
- **○/●** - select/deselect files and folders
- **📂 Browse** - pick a folder to analyze
- **⬅** - go to parent folder

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
