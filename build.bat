@echo off
rem FolderLens Build Script

echo ===================================
echo FolderLens Build Script
echo ===================================
echo.

cd /d "%~dp0"

echo [1/3] Cleaning old files...
if exist "dist" rmdir /s /q "dist"
if exist "build" rmdir /s /q "build"
if exist "FolderLens.spec" del /q "FolderLens.spec" 2>nul

echo.
echo [2/3] Building executable with PyInstaller...
echo.

pyinstaller --onefile --windowed --name FolderLens ^
    --add-data "version.py;." ^
    --add-data "file_utils.py;." ^
    --add-data "scanner.py;." ^
    --add-data "registry_installer.py;." ^
    --add-data "updater.py;." ^
    --hidden-import customtkinter ^
    --hidden-import darkdetect ^
    --hidden-import PIL ^
    --hidden-import PIL.Image ^
    --hidden-import PIL.ImageTk ^
    --collect-all customtkinter ^
    main.py

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller failed!
    pause
    exit /b 1
)

echo.
echo [3/3] Build complete!
echo.
echo ===================================
echo BUILD SUCCESS
echo ===================================
echo.
echo Output: dist\FolderLens.exe
echo.
echo To install:
echo   1. Run: python simple_installer.py
echo   2. Or just copy dist\FolderLens.exe where you want
echo.

pause
