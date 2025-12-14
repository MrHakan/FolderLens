import os
import sys
import shutil
import winreg
import ctypes
from pathlib import Path


def is_admin():
    """Check admin privileges"""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def run_as_admin():
    """Restart with admin privileges"""
    if is_admin():
        return True
    
    try:
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", sys.executable,
            ' '.join([f'"{arg}"' for arg in sys.argv]),
            None, 1
        )
        sys.exit(0)
    except Exception as e:
        print(f"Failed to get admin privileges: {e}")
        return False


def get_install_path():
    """Default install path"""
    program_files = os.environ.get('ProgramFiles', 'C:\\Program Files')
    return os.path.join(program_files, 'FolderLens')


def create_shortcut(target, shortcut_path, description=""):
    """Create Windows shortcut (via PowerShell)"""
    ps_script = f'''
$WshShell = New-Object -comObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
$Shortcut.TargetPath = "{target}"
$Shortcut.Description = "{description}"
$Shortcut.Save()
'''
    import subprocess
    subprocess.run(['powershell', '-Command', ps_script], 
                   capture_output=True, 
                   creationflags=subprocess.CREATE_NO_WINDOW)


def install():
    """Install"""
    print("=" * 60)
    print("FolderLens - Installer")
    print("=" * 60)
    print()
    
    if not is_admin():
        print("Admin privileges required. Restarting...")
        run_as_admin()
        return
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    source_exe = os.path.join(script_dir, 'dist', 'FolderLens.exe')
    
    if not os.path.exists(source_exe):
        print(f"[ERROR] {source_exe} not found!")
        print("Run build.bat first to create the executable.")
        input("\nPress Enter to continue...")
        return
    
    install_path = get_install_path()
    print(f"Install path: {install_path}")
    
    response = input("\nContinue with installation? (Y/N): ").strip().upper()
    if response != 'Y':
        print("Installation cancelled.")
        return
    
    print()
    
    print("[1/5] Creating install folder...")
    try:
        os.makedirs(install_path, exist_ok=True)
    except Exception as e:
        print(f"[ERROR] Cannot create folder: {e}")
        input("\nPress Enter to continue...")
        return
    
    print("[2/5] Copying files...")
    try:
        dest_exe = os.path.join(install_path, 'FolderLens.exe')
        shutil.copy2(source_exe, dest_exe)
        print(f"   OK: {dest_exe}")
    except Exception as e:
        print(f"[ERROR] Cannot copy file: {e}")
        input("\nPress Enter to continue...")
        return
    
    print("[3/5] Adding context menu...")
    try:
        key_path = r"Directory\Background\shell\FolderLens"
        with winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, key_path) as key:
            winreg.SetValue(key, "", winreg.REG_SZ, "Analyze with FolderLens")
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, dest_exe)
        
        with winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, key_path + r"\command") as key:
            winreg.SetValue(key, "", winreg.REG_SZ, f'"{dest_exe}" "%V"')
        
        key_path = r"Directory\shell\FolderLens"
        with winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, key_path) as key:
            winreg.SetValue(key, "", winreg.REG_SZ, "Analyze with FolderLens")
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, dest_exe)
        
        with winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, key_path + r"\command") as key:
            winreg.SetValue(key, "", winreg.REG_SZ, f'"{dest_exe}" "%1"')
        
        print("   OK: Context menu added")
    except Exception as e:
        print(f"   Warning: Context menu failed: {e}")
    
    print("[4/5] Creating Start Menu shortcut...")
    try:
        start_menu = os.path.join(
            os.environ.get('ProgramData', 'C:\\ProgramData'),
            'Microsoft', 'Windows', 'Start Menu', 'Programs'
        )
        shortcut_path = os.path.join(start_menu, 'FolderLens.lnk')
        create_shortcut(dest_exe, shortcut_path, "Folder Size Analyzer")
        print(f"   OK: {shortcut_path}")
    except Exception as e:
        print(f"   Warning: Shortcut failed: {e}")
    
    print("[5/5] Registering application...")
    try:
        uninstall_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\FolderLens"
        uninstall_script = os.path.join(script_dir, 'simple_installer.py')
        
        with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, uninstall_key) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "FolderLens")
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, "1.0.0")
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "FolderLens")
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, install_path)
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, dest_exe)
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, 
                            f'"{sys.executable}" "{uninstall_script}" --uninstall')
        print("   OK: Application registered")
    except Exception as e:
        print(f"   Warning: Registration failed: {e}")
    
    print()
    print("=" * 60)
    print("INSTALLATION COMPLETE")
    print("=" * 60)
    print()
    print(f"Installed to: {install_path}")
    print()
    print("Usage:")
    print("  - Right-click any folder")
    print("  - Select 'Analyze with FolderLens'")
    print()
    
    response = input("Launch FolderLens now? (Y/N): ").strip().upper()
    if response == 'Y':
        import subprocess
        subprocess.Popen([dest_exe])
    
    input("\nPress Enter to continue...")


def uninstall():
    """Uninstall"""
    print("=" * 60)
    print("FolderLens - Uninstaller")
    print("=" * 60)
    print()
    
    if not is_admin():
        print("Admin privileges required. Restarting...")
        run_as_admin()
        return
    
    install_path = get_install_path()
    
    print(f"Removing: {install_path}")
    response = input("\nContinue with uninstall? (Y/N): ").strip().upper()
    if response != 'Y':
        print("Uninstall cancelled.")
        return
    
    print()
    
    print("[1/4] Removing context menu...")
    try:
        keys_to_delete = [
            (winreg.HKEY_CLASSES_ROOT, r"Directory\Background\shell\FolderLens\command"),
            (winreg.HKEY_CLASSES_ROOT, r"Directory\Background\shell\FolderLens"),
            (winreg.HKEY_CLASSES_ROOT, r"Directory\shell\FolderLens\command"),
            (winreg.HKEY_CLASSES_ROOT, r"Directory\shell\FolderLens"),
        ]
        for root, key_path in keys_to_delete:
            try:
                winreg.DeleteKey(root, key_path)
            except FileNotFoundError:
                pass
        print("   OK: Context menu removed")
    except Exception as e:
        print(f"   Warning: Context menu removal failed: {e}")
    
    print("[2/4] Removing shortcuts...")
    try:
        start_menu = os.path.join(
            os.environ.get('ProgramData', 'C:\\ProgramData'),
            'Microsoft', 'Windows', 'Start Menu', 'Programs'
        )
        shortcut_path = os.path.join(start_menu, 'FolderLens.lnk')
        if os.path.exists(shortcut_path):
            os.remove(shortcut_path)
        print("   OK: Shortcuts removed")
    except Exception as e:
        print(f"   Warning: Shortcut removal failed: {e}")
    
    print("[3/4] Removing registration...")
    try:
        winreg.DeleteKey(winreg.HKEY_LOCAL_MACHINE, 
                        r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\FolderLens")
        print("   OK: Registration removed")
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"   Warning: Registration removal failed: {e}")
    
    print("[4/4] Removing files...")
    try:
        if os.path.exists(install_path):
            shutil.rmtree(install_path)
        print("   OK: Files removed")
    except Exception as e:
        print(f"   Warning: File removal failed: {e}")
    
    print()
    print("=" * 60)
    print("UNINSTALL COMPLETE")
    print("=" * 60)
    print()
    
    input("Press Enter to continue...")


def main():
    if '--uninstall' in sys.argv:
        uninstall()
    else:
        install()


if __name__ == "__main__":
    main()
