<div align="center">

<img src="assets/icon.png" width="120" alt="FolderLens">

# FolderLens

**A fast, modern folder size analyzer for Windows.**
See what's eating your disk — as a tree, a treemap, a top-files list, a file-type breakdown, or duplicate copies.

[![CI](https://github.com/MrHakan/FolderLens/actions/workflows/ci.yml/badge.svg)](https://github.com/MrHakan/FolderLens/actions/workflows/ci.yml)
[![Release](https://github.com/MrHakan/FolderLens/actions/workflows/release.yml/badge.svg)](https://github.com/MrHakan/FolderLens/actions/workflows/release.yml)

</div>

## Download

Grab the latest build from the [Releases page](https://github.com/MrHakan/FolderLens/releases/latest):

- **`FolderLens.exe`** — standalone, no Python required. Just run it.
- **`FolderLens-<version>-win64.zip`** — folder build; use this if your antivirus flags the single exe (see [Antivirus notes](docs/ANTIVIRUS.md)).
- **`FolderLens_Setup_<version>.exe`** — versioned installer with Start Menu and optional Explorer context-menu integration.

## Features

Opens on a **start screen** offering Home, Desktop, Downloads, Documents,
Pictures, Videos, Music and your drives — pick one and it scans, no file
dialog required.

FolderLens scans a whole directory tree **once** — with a shared work queue and
a bounded worker pool that keeps one large folder parallel, avoids creating
one task per subtree, and automatically uses gentler metadata concurrency for
UNC and mapped network drives. The complete in-memory tree can then be
explored five different ways with zero rescanning:

- 🌳 **Tree view** — expandable folder tree with a usage bar, size, item count, type, and date at every level. Expanding a folder is instant.
- 🗺️ **Treemap** — a hierarchy-first cushion-shaded map where every rectangle's area is its size, folders get a reserved header band, labels are capped to the useful large tiles, and dense tails are grouped into a single “smaller items” tile. **Image files are painted with their own thumbnail** so you can recognise them at a glance. Hover for a **peek preview** of the picture, click a folder to zoom in, right-click to go back.
- 🏆 **Largest files** — the top 100 biggest files anywhere in the tree, with their locations and small inline previews.
- 🧩 **File types** — size and count broken down by category (video, image, code, …) with proportional bars.
- 🎛️ **Type filter** — switch every view to Images, Videos, Audio, Documents, Archives, Code, Executables, Fonts, Databases, or Other. Matching folders remain as context, while all displayed sizes, counts, rankings, treemap areas, search results, and duplicate checks use only the selected file type.
- 👯 **Duplicates** — finds byte-identical copies and shows exactly how much space keeping one of each would free. Narrowed by size, then a head/tail sample, then a full hash, so almost nothing is read twice.

### Image viewer & annotation

Double-click any image (in any view) to open it:

- ◀ ▶ **step through every image in the folder**, with a position counter
- ⬅ **Up** and a **subfolder picker** to move around without leaving the viewer
- ✏️ **Annotate** in two modes:
  - **Basic** — pen, marker, arrow, eraser
  - **Advanced** — adds line, rectangle, ellipse, text, and redo
- colour palette, brush size, undo/redo, clear, and **Save as…** to export the annotated copy at full resolution

Annotations are stored relative to the image, so they stay put when you resize or zoom, and export sharp at the original resolution.

Plus:

- 🧭 **Clickable breadcrumbs** — jump straight to any folder in the path
- 🎨 **Colour legend** under the treemap, so the colours actually mean something
- ⌨️ **Keyboard treemap navigation** — arrow keys move through tiles, Enter opens the focused item, and Backspace goes up. The item name and size are also shown as text; Tree view provides the full hierarchical table.
- ⌨️ **Shortcut help** built in (F1), and hover hints on every icon button
- 🔎 **Instant search** across the whole tree (Ctrl+F)
- 🧵 **Fully responsive** — scanning, zipping, deleting, and exporting all run off the UI thread, with live progress and a **Stop** button
- 🗑️ **Manage** — multi-select to zip, delete, or open in Explorer (right-click, toolbar, or Delete key); sizes update without rescanning
- 📤 **Export** the full report to CSV, or the treemap itself as a PNG
- ♻️ **Recycle Bin** — deletions are undoable by default (permanent delete is a setting)
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
| `Delete` | Delete selected (tree / largest files / duplicates) |
| `F1` | Keyboard shortcut help |
| `Ctrl+O` | Browse for a folder |
| `Backspace` | Go to the parent folder |
| Double-click | Open folder / open image viewer |

In the image viewer:

| Key | Action |
| --- | --- |
| `←` `→` | Previous / next image |
| `Ctrl+Z` / `Ctrl+Y` | Undo / redo annotation |
| `Esc` | Close |

In the Treemap, Tab to the map, use the arrow keys to focus a tile, press
Enter to zoom into a folder or open an image, and press Backspace to move up.
The Tree view provides the full hierarchy as a keyboard accessible table.

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

# build the versioned installer (Windows, Inno Setup 6 required)
python installer/build_installer.py
```

### Scan baseline for 4.0 development

Run `python benchmarks/scan_baseline.py PATH --runs 3 --output scan-results.json`
against the same unchanged local folder or network share on each version.
The report includes scan duration, first partial result, first 500-entry
progress event, queue high-water marks, Python allocation peak, scanned item
count, and inaccessible path count. Snapshot bytes are observed values until
the scan finishes; inaccessible paths keep a completed result partial.
Logical size counts each hardlink path separately. Extended allocated size
uses optional filesystem block metadata and is unavailable on some shares;
reparse points and symlinks are listed but never traversed by default.
Python allocation peak is not process RSS; use an external process monitor to
compare total memory and record share latency and cache state separately.

### Project structure

```
FolderLens/
├── main.py               # entry point, CLI
├── app.py                # UI (customtkinter + ttk): views, image viewer, toolbars
├── scanner.py            # single-pass parallel tree scanner
├── analysis.py           # query projections, treemap layout, largest-files, CSV
├── file_actions.py       # stale-scan validation and safe ZIP/delete operations
├── duplicates.py         # size -> sample -> full hash duplicate detection (pure, tested)
├── locations.py          # start-screen places and path breadcrumbs (pure, tested)
├── trash.py              # Recycle Bin / XDG trash, with a permanent-delete fallback
├── treemap_render.py     # cushion shading, thumbnails, labels, hit-testing
├── thumbnails.py         # background thumbnail decoding + LRU cache
├── annotate.py           # annotation model, tools, undo/redo, export (pure, tested)
├── imagenav.py           # image/folder navigation for the viewer (pure, tested)
├── file_utils.py         # file type detection, formatting
├── updater.py            # auto-update handler
├── version.py            # version info
├── registry_installer.py # Windows Explorer context menu
├── FolderLens.spec       # antivirus-friendly PyInstaller build
├── app.manifest          # asInvoker + DPI + supported-OS manifest
├── make_version_info.py  # generates the embedded Windows version resource
├── installer/            # versioned Inno Setup installer and build script
├── assets/               # app icon (+ generator)
├── tests/                # pytest suite
├── docs/ANTIVIRUS.md     # false-positive guidance
└── .github/workflows/    # CI (tests) + Release (exe, folder zip, installer)
```

## Releasing

Push a tag like `v3.0.0` (or run the **Release** workflow with a `tag` input).
It runs the tests, generates the version resource, builds the AV-friendly
one-file exe, one-directory zip, and versioned installer on Windows, smoke-tests
the executable and installer install/uninstall flow, and publishes a GitHub
release with all three assets and their SHA-256 manifest. The in-app updater
uses the one-file exe or folder zip; installer builds are for fresh installs.

## License

MIT
