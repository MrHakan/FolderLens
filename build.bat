@echo off
rem FolderLens Build Script (antivirus-friendly)

echo ===================================
echo FolderLens Build Script
echo ===================================
echo.

cd /d "%~dp0"

echo [1/4] Cleaning old build output...
if exist "dist" rmdir /s /q "dist"
if exist "dist_onedir" rmdir /s /q "dist_onedir"
if exist "build" rmdir /s /q "build"

echo.
echo [2/4] Generating Windows version resource...
python make_version_info.py
if errorlevel 1 goto :error

echo.
echo [3/4] Building one-file executable (no UPX, signed metadata, manifest)...
pyinstaller --noconfirm FolderLens.spec
if errorlevel 1 goto :error

echo.
echo [4/4] Building one-directory variant (most antivirus-friendly)...
set FL_ONEDIR=1
pyinstaller --noconfirm --distpath dist_onedir FolderLens.spec
set FL_ONEDIR=
if errorlevel 1 goto :error

echo.
echo ===================================
echo BUILD SUCCESS
echo ===================================
echo.
echo   dist\FolderLens.exe                 one-file executable
echo   dist_onedir\FolderLens\FolderLens.exe   folder build (use if antivirus flags the one-file exe)
echo.
echo If Windows Defender still flags the one-file exe, use the folder build
echo or see docs\ANTIVIRUS.md for how to submit a false-positive report.
echo.
pause
exit /b 0

:error
echo.
echo [ERROR] Build failed!
pause
exit /b 1
