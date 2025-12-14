@echo off
rem FolderLens Build Script
rem Requirements: Python 3.9+, PyInstaller, Inno Setup 6 (optional)

echo ===================================
echo FolderLens Build Script
echo ===================================
echo.

cd /d "%~dp0"

echo [1/4] Cleaning old files...
if exist "dist" rmdir /s /q "dist"
if exist "build" rmdir /s /q "build"
if exist "installer_output" rmdir /s /q "installer_output"
if exist "*.spec" del /q "*.spec" 2>nul

echo.
echo [2/4] Building executable with PyInstaller...
echo.

pyinstaller --onefile --windowed --name FolderLens ^
    --add-data "file_utils.py;." ^
    --add-data "scanner.py;." ^
    --add-data "registry_installer.py;." ^
    --hidden-import customtkinter ^
    --hidden-import darkdetect ^
    --hidden-import PIL ^
    --collect-all customtkinter ^
    main.py

if errorlevel 1 (
    echo.
    echo [ERROR] PyInstaller failed!
    pause
    exit /b 1
)

echo.
echo [3/4] Executable created: dist\FolderLens.exe
echo.

if not exist "installer_output" mkdir "installer_output"

echo [4/4] Building installer with Inno Setup...

set ISCC=""
if exist "%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe" (
    set ISCC="%ProgramFiles(x86)%\Inno Setup 6\ISCC.exe"
) else if exist "%ProgramFiles%\Inno Setup 6\ISCC.exe" (
    set ISCC="%ProgramFiles%\Inno Setup 6\ISCC.exe"
)

if %ISCC%=="" (
    echo.
    echo [WARNING] Inno Setup not found!
    echo To create installer, install Inno Setup 6:
    echo https://jrsoftware.org/isinfo.php
    echo.
    echo Executable ready at: dist\FolderLens.exe
    echo.
) else (
    echo Inno Setup found: %ISCC%
    %ISCC% "installer\FolderLens_Setup.iss"
    
    if errorlevel 1 (
        echo.
        echo [ERROR] Inno Setup failed!
    ) else (
        echo.
        echo ===================================
        echo BUILD COMPLETE
        echo ===================================
        echo.
        echo Output:
        echo   - EXE: dist\FolderLens.exe
        echo   - Installer: installer_output\FolderLens_Setup_1.0.0.exe
        echo.
    )
)

pause
