# folderlens

a lightweight folder size analyzer for windows with explorer context menu integration.

## features

- recursive scanning of folders and subfolders
- async scanning (ui doesn't freeze)
- visual usage bars showing relative size
- file type icons and color coding
- folder and file selection across directories
- analyze panel with zip and delete options
- image preview on double-click
- sortable columns (size, name, type, date)
- light/dark mode toggle
- auto-update from github releases
- right-click context menu integration
- handles "access denied" errors gracefully

## installation

### quick install

```bash
cd FolderLens
pip install -r requirements.txt
build.bat
python simple_installer.py  # run as admin
```

this installs to `C:\Program Files\FolderLens` and adds context menu entry.

### manual install

```bash
pip install -r requirements.txt
python main.py --install  # run as admin for context menu
```

### uninstall

```bash
python simple_installer.py --uninstall
```

or use `FolderLens_Uninstall.reg`.

## usage

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
```

## ui controls

- **☀️/🌙** - toggle light/dark mode
- **⬆** - check for updates
- **📊** - open analyze panel for selected items
- **•••** - settings (icon size, preview toggle)
- **○/●** - select/deselect files and folders

## requirements

- windows 10/11
- python 3.9+

## project structure

```
FolderLens/
├── main.py               # entry point, cli
├── app.py                # ui (customtkinter)
├── scanner.py            # async folder scanner
├── file_utils.py         # file type detection, formatting
├── version.py            # version info
├── updater.py            # auto-update handler
├── registry_installer.py # windows registry operations
├── simple_installer.py   # python-based installer
├── build.bat             # build script
├── requirements.txt
├── installer/
│   └── FolderLens_Setup.iss  # inno setup script
└── dist/
    └── FolderLens.exe    # compiled executable
```

## updates

the app checks github releases for updates. to configure:

1. edit `version.py` 
2. set `GITHUB_OWNER` to your github username
3. set `GITHUB_REPO` to your repository name
4. create releases with `.exe` or `.zip` assets

## building

```bash
# build exe
build.bat

# or manually
pyinstaller --onefile --windowed --name FolderLens --collect-all customtkinter main.py
```

## license

mit
