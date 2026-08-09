# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller build spec for FolderLens.

Tuned to minimise antivirus false positives on the produced executable:
  * upx=False           - UPX packing is the single biggest false-positive
                          trigger for PyInstaller apps.
  * version='...'       - embeds real version metadata (see make_version_info).
  * icon / manifest     - a signed-looking, well-formed binary.
  * excludes            - keep the bundle small and free of unexpected code.

Set the environment variable FL_ONEDIR=1 to build the one-directory variant
(recommended when antivirus still complains: a plain folder of files trips far
fewer heuristics than a self-extracting one-file executable).
"""
import os

from PyInstaller.utils.hooks import collect_all

ONEDIR = os.environ.get("FL_ONEDIR") == "1"

datas = [("assets/icon.ico", "assets"), ("assets/icon.png", "assets")]
binaries = []
hiddenimports = ["customtkinter", "darkdetect", "PIL", "PIL.Image", "PIL.ImageTk"]

ctk_datas, ctk_binaries, ctk_hidden = collect_all("customtkinter")
datas += ctk_datas
binaries += ctk_binaries
hiddenimports += ctk_hidden

version_file = "version_info.txt" if os.path.exists("version_info.txt") else None
manifest_file = "app.manifest" if os.path.exists("app.manifest") else None
icon_file = "assets/icon.ico" if os.path.exists("assets/icon.ico") else None

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "numpy", "pandas", "matplotlib", "scipy", "tkinter.test"],
    noarchive=False,
)

pyz = PYZ(a.pure)

if ONEDIR:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="FolderLens",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=False,
        disable_windowed_traceback=False,
        icon=icon_file,
        version=version_file,
        manifest=manifest_file,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="FolderLens",
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="FolderLens",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=False,
        disable_windowed_traceback=False,
        icon=icon_file,
        version=version_file,
        manifest=manifest_file,
    )
