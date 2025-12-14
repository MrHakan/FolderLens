import os
import sys
import shutil
import winreg
import ctypes
import subprocess
from pathlib import Path


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def run_as_admin():
    if is_admin():
        return True
    
    try:
        if getattr(sys, 'frozen', False):
            executable = sys.executable
            params = ' '.join([f'"{arg}"' for arg in sys.argv[1:]])
        else:
            executable = sys.executable
            params = f'"{os.path.abspath(__file__)}"'
            if len(sys.argv) > 1:
                params += ' ' + ' '.join([f'"{arg}"' for arg in sys.argv[1:]])
        
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, params, None, 1
        )
        if result > 32:
            sys.exit(0)
        else:
            print(f"Failed to elevate privileges. Error code: {result}")
            return False
    except Exception as e:
        print(f"Failed to get admin privileges: {e}")
        return False


def get_install_path():
    program_files = os.environ.get('ProgramFiles', 'C:\\Program Files')
    return os.path.join(program_files, 'FolderLens')


def create_shortcut(target, shortcut_path, description="", icon_path=None):
    ps_script = f'''
$WshShell = New-Object -comObject WScript.Shell
$Shortcut = $WshShell.CreateShortcut("{shortcut_path}")
$Shortcut.TargetPath = "{target}"
$Shortcut.Description = "{description}"
'''
    if icon_path:
        ps_script += f'$Shortcut.IconLocation = "{icon_path}"\n'
    ps_script += '$Shortcut.Save()\n'
    
    try:
        subprocess.run(
            ['powershell', '-ExecutionPolicy', 'Bypass', '-Command', ps_script],
            capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW,
            timeout=30
        )
        return True
    except Exception as e:
        print(f"   Error creating shortcut: {e}")
        return False


def add_context_menu(exe_path):
    try:
        key_path = r"Directory\Background\shell\FolderLens"
        with winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, key_path) as key:
            winreg.SetValue(key, "", winreg.REG_SZ, "Analyze with FolderLens")
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, exe_path)
        
        with winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, key_path + r"\command") as key:
            winreg.SetValue(key, "", winreg.REG_SZ, f'"{exe_path}" "%V"')
        
        key_path = r"Directory\shell\FolderLens"
        with winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, key_path) as key:
            winreg.SetValue(key, "", winreg.REG_SZ, "Analyze with FolderLens")
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, exe_path)
        
        with winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, key_path + r"\command") as key:
            winreg.SetValue(key, "", winreg.REG_SZ, f'"{exe_path}" "%1"')
        
        return True, "Context menu added successfully"
    except PermissionError:
        return False, "Permission denied. Run as administrator."
    except Exception as e:
        return False, str(e)


def remove_context_menu():
    keys_to_delete = [
        (winreg.HKEY_CLASSES_ROOT, r"Directory\Background\shell\FolderLens\command"),
        (winreg.HKEY_CLASSES_ROOT, r"Directory\Background\shell\FolderLens"),
        (winreg.HKEY_CLASSES_ROOT, r"Directory\shell\FolderLens\command"),
        (winreg.HKEY_CLASSES_ROOT, r"Directory\shell\FolderLens"),
    ]
    
    errors = []
    for root, key_path in keys_to_delete:
        try:
            winreg.DeleteKey(root, key_path)
        except FileNotFoundError:
            pass
        except Exception as e:
            errors.append(f"{key_path}: {e}")
    
    if errors:
        return False, "\n".join(errors)
    return True, "Context menu removed"


def register_app(install_path, exe_path):
    try:
        uninstall_key = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\FolderLens"
        
        with winreg.CreateKey(winreg.HKEY_LOCAL_MACHINE, uninstall_key) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, "FolderLens")
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, "1.0.0")
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, "FolderLens")
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, install_path)
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, exe_path)
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{exe_path}" --uninstall')
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
        
        return True, "Application registered"
    except Exception as e:
        return False, str(e)


def unregister_app():
    try:
        winreg.DeleteKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\FolderLens"
        )
        return True, "Registration removed"
    except FileNotFoundError:
        return True, "Not registered"
    except Exception as e:
        return False, str(e)


def install():
    print()
    print("=" * 60)
    print("         FolderLens - Installation Wizard")
    print("=" * 60)
    print()
    
    if not is_admin():
        print("[!] Administrator privileges required.")
        print("    Requesting elevation...")
        run_as_admin()
        return
    
    print("[*] Running with administrator privileges")
    print()
    
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    source_exe = os.path.join(script_dir, 'dist', 'FolderLens', 'FolderLens.exe')
    source_folder = os.path.join(script_dir, 'dist', 'FolderLens')
    
    if not os.path.exists(source_exe):
        source_exe_single = os.path.join(script_dir, 'dist', 'FolderLens.exe')
        if os.path.exists(source_exe_single):
            source_exe = source_exe_single
            source_folder = None
        else:
            print("[ERROR] FolderLens.exe not found!")
            print()
            print(f"   Checked: {source_exe}")
            print(f"   Checked: {source_exe_single}")
            print()
            print("   Please build the application first by running:")
            print("   > build.bat")
            print()
            input("Press Enter to exit...")
            return
    
    install_path = get_install_path()
    dest_exe = os.path.join(install_path, 'FolderLens.exe')
    
    print(f"   Source: {source_exe}")
    print(f"   Destination: {install_path}")
    print()
    
    response = input("Continue with installation? [Y/N]: ").strip().upper()
    if response != 'Y':
        print("\nInstallation cancelled.")
        input("Press Enter to exit...")
        return
    
    print()
    
    print("[1/5] Creating installation directory...")
    try:
        os.makedirs(install_path, exist_ok=True)
        print("      [OK] Directory created")
    except Exception as e:
        print(f"      [FAIL] {e}")
        input("\nPress Enter to exit...")
        return
    
    print("[2/5] Copying application files...")
    try:
        if source_folder and os.path.isdir(source_folder):
            internal_src = os.path.join(source_folder, '_internal')
            internal_dst = os.path.join(install_path, '_internal')
            if os.path.exists(internal_src):
                if os.path.exists(internal_dst):
                    shutil.rmtree(internal_dst)
                shutil.copytree(internal_src, internal_dst)
                print(f"      [OK] Copied _internal folder")
            shutil.copy2(source_exe, dest_exe)
            print(f"      [OK] Copied FolderLens.exe")
        else:
            shutil.copy2(source_exe, dest_exe)
            print(f"      [OK] Copied to {dest_exe}")
    except Exception as e:
        print(f"      [FAIL] {e}")
        input("\nPress Enter to exit...")
        return
    
    print("[3/5] Adding Windows context menu...")
    success, msg = add_context_menu(dest_exe)
    if success:
        print(f"      [OK] {msg}")
    else:
        print(f"      [WARN] {msg}")
    
    print("[4/5] Creating Start Menu shortcut...")
    try:
        start_menu = os.path.join(
            os.environ.get('ProgramData', 'C:\\ProgramData'),
            'Microsoft', 'Windows', 'Start Menu', 'Programs'
        )
        shortcut_path = os.path.join(start_menu, 'FolderLens.lnk')
        if create_shortcut(dest_exe, shortcut_path, "Folder Size Analyzer", dest_exe):
            print(f"      [OK] Created shortcut")
        else:
            print(f"      [WARN] Could not create shortcut")
    except Exception as e:
        print(f"      [WARN] {e}")
    
    print("[5/5] Registering application...")
    success, msg = register_app(install_path, dest_exe)
    if success:
        print(f"      [OK] {msg}")
    else:
        print(f"      [WARN] {msg}")
    
    print()
    print("=" * 60)
    print("         Installation Complete!")
    print("=" * 60)
    print()
    print("   You can now:")
    print("   - Right-click any folder and select 'Analyze with FolderLens'")
    print("   - Launch from Start Menu")
    print()
    
    response = input("Launch FolderLens now? [Y/N]: ").strip().upper()
    if response == 'Y':
        try:
            subprocess.Popen([dest_exe], creationflags=subprocess.DETACHED_PROCESS)
            print("\n   Launching FolderLens...")
        except Exception as e:
            print(f"\n   Failed to launch: {e}")
    
    print()
    input("Press Enter to exit...")


def uninstall():
    print()
    print("=" * 60)
    print("         FolderLens - Uninstaller")
    print("=" * 60)
    print()
    
    if not is_admin():
        print("[!] Administrator privileges required.")
        print("    Requesting elevation...")
        run_as_admin()
        return
    
    print("[*] Running with administrator privileges")
    print()
    
    install_path = get_install_path()
    
    if not os.path.exists(install_path):
        print("[!] FolderLens is not installed.")
        input("\nPress Enter to exit...")
        return
    
    print(f"   Installation path: {install_path}")
    print()
    
    response = input("Remove FolderLens? [Y/N]: ").strip().upper()
    if response != 'Y':
        print("\nUninstall cancelled.")
        input("Press Enter to exit...")
        return
    
    print()
    
    print("[1/4] Removing context menu...")
    success, msg = remove_context_menu()
    if success:
        print(f"      [OK] {msg}")
    else:
        print(f"      [WARN] {msg}")
    
    print("[2/4] Removing Start Menu shortcut...")
    try:
        start_menu = os.path.join(
            os.environ.get('ProgramData', 'C:\\ProgramData'),
            'Microsoft', 'Windows', 'Start Menu', 'Programs'
        )
        shortcut_path = os.path.join(start_menu, 'FolderLens.lnk')
        if os.path.exists(shortcut_path):
            os.remove(shortcut_path)
            print("      [OK] Shortcut removed")
        else:
            print("      [OK] No shortcut found")
    except Exception as e:
        print(f"      [WARN] {e}")
    
    print("[3/4] Removing registration...")
    success, msg = unregister_app()
    if success:
        print(f"      [OK] {msg}")
    else:
        print(f"      [WARN] {msg}")
    
    print("[4/4] Removing application files...")
    try:
        if os.path.exists(install_path):
            shutil.rmtree(install_path)
            print("      [OK] Files removed")
        else:
            print("      [OK] Already removed")
    except Exception as e:
        print(f"      [WARN] {e}")
    
    print()
    print("=" * 60)
    print("         Uninstall Complete!")
    print("=" * 60)
    print()
    
    input("Press Enter to exit...")


def main():
    if '--uninstall' in sys.argv or '-u' in sys.argv:
        uninstall()
    elif '--help' in sys.argv or '-h' in sys.argv:
        print("FolderLens Installer")
        print()
        print("Usage:")
        print("  simple_installer.py              Install FolderLens")
        print("  simple_installer.py --uninstall  Remove FolderLens")
        print("  simple_installer.py --help       Show this help")
    else:
        install()


if __name__ == "__main__":
    main()
