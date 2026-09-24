import os
import sys
import json
import hashlib
import re
import shutil
import tempfile
import threading
import subprocess
import uuid
from typing import Optional, Tuple, Callable
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError
from urllib.parse import quote

from version import VERSION, GITHUB_OWNER, GITHUB_REPO
from release_manifest import expected_sha256, sha256_file

GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
APP_NAME = "FolderLens"


def create_swap_script() -> str:
    """Create a unique external updater with a rollback path after app exit."""
    script = r'''@echo off
setlocal DisableDelayedExpansion
if "%FL_UPDATE_TEST_DELAY%"=="0" goto ready
timeout /t 2 /nobreak >nul
:ready
if not exist "%FL_UPDATE_NEW%" goto failed
for /l %%I in (1,1,30) do (
    move /y "%FL_UPDATE_CURRENT%" "%FL_UPDATE_BACKUP%" >nul 2>&1
    if not errorlevel 1 goto backed_up
    timeout /t 1 /nobreak >nul
)
goto failed
:backed_up
move /y "%FL_UPDATE_NEW%" "%FL_UPDATE_CURRENT%" >nul 2>&1
if errorlevel 1 goto restore
"%FL_UPDATE_CURRENT%" --version >nul 2>&1
if errorlevel 1 goto remove_new
if "%FL_UPDATE_NO_RESTART%"=="1" goto success
start "" "%FL_UPDATE_CURRENT%"
if errorlevel 1 goto remove_new
:success
exit /b 0
:remove_new
del /f /q "%FL_UPDATE_CURRENT%" >nul 2>&1
:restore
move /y "%FL_UPDATE_BACKUP%" "%FL_UPDATE_CURRENT%" >nul 2>&1
if errorlevel 1 echo Rollback failed. Restore "%FL_UPDATE_BACKUP%" manually. > "%FL_UPDATE_LOG%"
:failed
del /f /q "%FL_UPDATE_NEW%" >nul 2>&1
exit /b 1
'''
    fd, path = tempfile.mkstemp(prefix='folderlens_update_', suffix='.cmd')
    with os.fdopen(fd, 'w', encoding='ascii', newline='\r\n') as stream:
        stream.write(script)
    return path


class UpdateInfo:
    """Information about an available update"""
    def __init__(self, version: str, download_url: Optional[str], release_notes: str,
                 published_at: str, release_url: str = "", sha256: Optional[str] = None):
        self.version = version
        self.download_url = download_url
        self.release_notes = release_notes
        self.published_at = published_at
        self.release_url = release_url
        self.sha256 = sha256


class Updater:
    """Handles checking and applying updates"""
    
    def __init__(self):
        self.current_version = VERSION
        self._checking = False
        self._downloading = False

    @staticmethod
    def installation_type() -> str:
        if not getattr(sys, 'frozen', False):
            return "source"
        if os.path.isdir(os.path.join(os.path.dirname(sys.executable), "_internal")):
            return "onedir"
        return "onefile"
    
    @staticmethod
    def compare_versions(v1: str, v2: str) -> int:
        def parse_version(v: str) -> list:
            v = v.lstrip('v').lstrip('V')
            parts = []
            for part in v.split('.'):
                try:
                    parts.append(int(part))
                except ValueError:
                    num_part = ''.join(c for c in part if c.isdigit())
                    parts.append(int(num_part) if num_part else 0)
            return parts
        
        v1_parts = parse_version(v1)
        v2_parts = parse_version(v2)
        
        max_len = max(len(v1_parts), len(v2_parts))
        v1_parts.extend([0] * (max_len - len(v1_parts)))
        v2_parts.extend([0] * (max_len - len(v2_parts)))
        
        for p1, p2 in zip(v1_parts, v2_parts):
            if p1 > p2:
                return 1
            elif p1 < p2:
                return -1
        return 0
    
    def check_for_updates(self) -> Tuple[bool, Optional[UpdateInfo], Optional[str]]:
        if self._checking:
            return False, None, "Already checking for updates"
        
        self._checking = True
        
        try:
            request = Request(
                GITHUB_API_URL,
                headers={'User-Agent': f'{APP_NAME}/{VERSION}'}
            )
            
            with urlopen(request, timeout=10) as response:
                data = json.loads(response.read().decode('utf-8'))
            
            latest_version = data.get('tag_name', '').lstrip('v')
            
            if not latest_version:
                return False, None, "Could not determine latest version"
            
            if self.compare_versions(latest_version, self.current_version) > 0:
                download_url = None
                checksum = None
                assets = data.get('assets', [])
                
                # The ZIP is a complete onedir installation. Replacing only
                # its EXE would leave an incompatible _internal directory.
                # Source runs likewise cannot install a Windows EXE in place.
                if self.installation_type() == "onefile":
                    download_url = next(
                        (asset.get('browser_download_url') for asset in assets
                         if asset.get('name', '').lower() == 'folderlens.exe'), None)
                    manifest_url = next(
                        (asset.get('browser_download_url') for asset in assets
                         if asset.get('name') == 'SHA256SUMS'), None)
                    if download_url and manifest_url:
                        try:
                            manifest_request = Request(
                                manifest_url, headers={'User-Agent': f'{APP_NAME}/{VERSION}'})
                            with urlopen(manifest_request, timeout=10) as response:
                                manifest = response.read(1024 * 1024 + 1)
                            if len(manifest) > 1024 * 1024:
                                raise ValueError("Checksum manifest is too large")
                            checksum = expected_sha256(manifest, "FolderLens.exe")
                        except (URLError, OSError, ValueError):
                            download_url = None  # manual path when integrity cannot be checked
                    else:
                        download_url = None

                release_url = (f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/tag/"
                               f"{quote(data['tag_name'], safe='')}")
                
                update_info = UpdateInfo(
                    version=latest_version,
                    download_url=download_url,
                    release_notes=data.get('body', 'No release notes available.'),
                    published_at=data.get('published_at', ''),
                    release_url=release_url,
                    sha256=checksum,
                )
                
                return True, update_info, None
            else:
                return False, None, None
                
        except HTTPError as e:
            if e.code == 404:
                return False, None, "Repository not found. Please configure GitHub settings."
            return False, None, f"HTTP Error: {e.code}"
        except URLError as e:
            return False, None, f"Network error: {e.reason}"
        except json.JSONDecodeError:
            return False, None, "Invalid response from GitHub"
        except Exception as e:
            return False, None, f"Error checking for updates: {str(e)}"
        finally:
            self._checking = False
    
    def check_for_updates_async(self, callback: Callable[[bool, Optional[UpdateInfo], Optional[str]], None]):
        def worker():
            result = self.check_for_updates()
            callback(*result)
        
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
    
    def download_update(
        self, 
        update_info: UpdateInfo, 
        progress_callback: Optional[Callable[[int, int], None]] = None
    ) -> Tuple[bool, Optional[str], Optional[str]]:
        if self._downloading:
            return False, None, "Already downloading"
        
        if not update_info.download_url:
            return False, None, "No download URL available"
        if not update_info.sha256:
            return False, None, "No verified checksum for this update; use the release page"
        
        self._downloading = True
        
        temp_dir = None
        success = False
        try:
            temp_dir = tempfile.mkdtemp(prefix='folderlens_update_')
            
            file_path = os.path.join(temp_dir, 'FolderLens.exe')
            
            request = Request(
                update_info.download_url,
                headers={'User-Agent': f'{APP_NAME}/{VERSION}'}
            )
            
            with urlopen(request, timeout=60) as response:
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                chunk_size = 8192
                digest = hashlib.sha256()
                
                with open(file_path, 'wb') as f:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        digest.update(chunk)
                        downloaded += len(chunk)
                        
                        if progress_callback and total_size > 0:
                            progress_callback(downloaded, total_size)
            
            if digest.hexdigest() != update_info.sha256.lower():
                return False, None, "Downloaded update did not match its published SHA-256 checksum"
            success = True
            return True, file_path, None
            
        except Exception as e:
            return False, None, f"Download failed: {str(e)}"
        finally:
            self._downloading = False
            if temp_dir and not success:
                shutil.rmtree(temp_dir, ignore_errors=True)
    
    def download_update_async(
        self,
        update_info: UpdateInfo,
        progress_callback: Optional[Callable[[int, int], None]] = None,
        complete_callback: Optional[Callable[[bool, Optional[str], Optional[str]], None]] = None
    ):

        def worker():
            result = self.download_update(update_info, progress_callback)
            if complete_callback:
                complete_callback(*result)
        
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
    
    def apply_update(self, downloaded_file: str,
                     expected_digest: Optional[str] = None) -> Tuple[bool, Optional[str]]:
        staged = None
        script_path = None
        launched = False
        try:
            if self.installation_type() != "onefile":
                return False, ("Automatic installation is available only for the one-file EXE. "
                               "Download the full package from the release page and update manually.")
            if not downloaded_file.lower().endswith('.exe'):
                return False, "The one-file updater requires a FolderLens EXE, not an archive."
            if not getattr(sys, 'frozen', False):
                return False, "Auto-update not supported for Python scripts. Please download manually."
            if not expected_digest or not re.fullmatch(r'[0-9a-fA-F]{64}', expected_digest):
                return False, "A verified SHA-256 checksum is required before installation."
            if sha256_file(downloaded_file) != expected_digest.lower():
                return False, "The downloaded executable changed after verification."

            current_exe = sys.executable
            token = uuid.uuid4().hex
            staged = f"{current_exe}.new-{token}.exe"
            backup_exe = f"{current_exe}.backup-{token}"
            # Stage next to the installed EXE, so the later moves stay on
            # one filesystem. Failure here leaves the running app untouched.
            shutil.copy2(downloaded_file, staged)
            if sha256_file(staged) != expected_digest.lower():
                return False, "The staged executable failed checksum verification."

            script_path = create_swap_script()
            env = os.environ.copy()
            env.update({
                'FL_UPDATE_CURRENT': current_exe,
                'FL_UPDATE_NEW': staged,
                'FL_UPDATE_BACKUP': backup_exe,
                'FL_UPDATE_LOG': script_path + '.log',
            })
            subprocess.Popen(['cmd', '/c', script_path], env=env,
                             creationflags=subprocess.CREATE_NO_WINDOW)
            launched = True
            return True, None
        except Exception as e:
            return False, f"Failed to apply update: {str(e)}"
        finally:
            if not launched:
                for path in (staged, script_path):
                    if path:
                        try:
                            os.remove(path)
                        except FileNotFoundError:
                            pass
                        except OSError:
                            pass
    
    def get_current_version(self) -> str:
        return self.current_version


_updater_instance = None

def get_updater() -> Updater:
    global _updater_instance
    if _updater_instance is None:
        _updater_instance = Updater()
    return _updater_instance


if __name__ == "__main__":
    updater = get_updater()
    print(f"Current version: {updater.get_current_version()}")
    print("Checking for updates...")
    
    available, info, error = updater.check_for_updates()
    
    if error:
        print(f"Error: {error}")
    elif available and info:
        print(f"Update available: {info.version}")
        print(f"Download URL: {info.download_url}")
        print(f"Release notes: {info.release_notes[:200]}...")
    else:
        print("You're running the latest version!")
