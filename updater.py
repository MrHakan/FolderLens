import os
import sys
import json
import shutil
import tempfile
import threading
import subprocess
from typing import Optional, Tuple, Callable
from urllib.request import urlopen, Request
from urllib.error import URLError, HTTPError

from version import VERSION, GITHUB_OWNER, GITHUB_REPO

GITHUB_API_URL = f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/releases/latest"
APP_NAME = "FolderLens"


class UpdateInfo:
    """Information about an available update"""
    def __init__(self, version: str, download_url: str, release_notes: str, published_at: str):
        self.version = version
        self.download_url = download_url
        self.release_notes = release_notes
        self.published_at = published_at


class Updater:
    """Handles checking and applying updates"""
    
    def __init__(self):
        self.current_version = VERSION
        self._checking = False
        self._downloading = False
    
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
                assets = data.get('assets', [])
                
                for asset in assets:
                    name = asset.get('name', '').lower()
                    if name.endswith('.exe') or name.endswith('.zip'):
                        download_url = asset.get('browser_download_url')
                        break
                
                if not download_url:
                    download_url = data.get('zipball_url')
                
                update_info = UpdateInfo(
                    version=latest_version,
                    download_url=download_url,
                    release_notes=data.get('body', 'No release notes available.'),
                    published_at=data.get('published_at', '')
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
        
        self._downloading = True
        
        try:
            temp_dir = tempfile.mkdtemp(prefix='folderlens_update_')
            
            url_path = update_info.download_url.split('/')[-1]
            if '?' in url_path:
                url_path = url_path.split('?')[0]
            
            filename = url_path if url_path else f'FolderLens_{update_info.version}.zip'
            file_path = os.path.join(temp_dir, filename)
            
            request = Request(
                update_info.download_url,
                headers={'User-Agent': f'{APP_NAME}/{VERSION}'}
            )
            
            with urlopen(request, timeout=60) as response:
                total_size = int(response.headers.get('content-length', 0))
                downloaded = 0
                chunk_size = 8192
                
                with open(file_path, 'wb') as f:
                    while True:
                        chunk = response.read(chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        downloaded += len(chunk)
                        
                        if progress_callback and total_size > 0:
                            progress_callback(downloaded, total_size)
            
            return True, file_path, None
            
        except Exception as e:
            return False, None, f"Download failed: {str(e)}"
        finally:
            self._downloading = False
    
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
    
    def apply_update(self, downloaded_file: str) -> Tuple[bool, Optional[str]]:
        try:
            if getattr(sys, 'frozen', False):
                current_exe = sys.executable
                backup_exe = current_exe + '.backup'
                
                if downloaded_file.endswith('.exe'):
                    batch_content = f'''@echo off
timeout /t 2 /nobreak > nul
move /y "{current_exe}" "{backup_exe}"
move /y "{downloaded_file}" "{current_exe}"
start "" "{current_exe}"
del "%~f0"
'''
                    batch_path = os.path.join(tempfile.gettempdir(), 'folderlens_update.bat')
                    with open(batch_path, 'w') as f:
                        f.write(batch_content)
                    
                    subprocess.Popen(['cmd', '/c', batch_path], 
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                    return True, None
                    
                elif downloaded_file.endswith('.zip'):
                    import zipfile
                    
                    extract_dir = tempfile.mkdtemp(prefix='folderlens_extract_')
                    
                    with zipfile.ZipFile(downloaded_file, 'r') as zf:
                        zf.extractall(extract_dir)
                    
                    new_exe = None
                    for root, dirs, files in os.walk(extract_dir):
                        for file in files:
                            if file.lower() == 'folderlens.exe':
                                new_exe = os.path.join(root, file)
                                break
                    
                    if new_exe:
                        batch_content = f'''@echo off
timeout /t 2 /nobreak > nul
move /y "{current_exe}" "{backup_exe}"
move /y "{new_exe}" "{current_exe}"
start "" "{current_exe}"
rmdir /s /q "{extract_dir}"
del "%~f0"
'''
                        batch_path = os.path.join(tempfile.gettempdir(), 'folderlens_update.bat')
                        with open(batch_path, 'w') as f:
                            f.write(batch_content)
                        
                        subprocess.Popen(['cmd', '/c', batch_path],
                                       creationflags=subprocess.CREATE_NO_WINDOW)
                        return True, None
                    else:
                        return False, "Could not find executable in update package"
            else:
                return False, "Auto-update not supported for Python scripts. Please download manually."
                
        except Exception as e:
            return False, f"Failed to apply update: {str(e)}"
    
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
