import os
import sys
import winreg
import ctypes
from typing import Optional


def is_admin() -> bool:
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def get_executable_path() -> str:
    if getattr(sys, 'frozen', False):
        return sys.executable
    else:
        return os.path.abspath(sys.argv[0])


def install_context_menu(app_path: Optional[str] = None) -> tuple[bool, str]:
    if not is_admin():
        return False, "Admin privileges required. Run as administrator."
    
    if app_path is None:
        app_path = get_executable_path()
    
    if app_path.endswith('.py'):
        python_exe = sys.executable
        command = f'"{python_exe}" "{app_path}" "%V"'
    else:
        command = f'"{app_path}" "%V"'
    
    try:
        key_path = r"Directory\Background\shell\FolderLens"
        
        with winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, key_path) as key:
            winreg.SetValue(key, "", winreg.REG_SZ, "Analyze with FolderLens")
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, app_path)
        
        with winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, key_path + r"\command") as key:
            winreg.SetValue(key, "", winreg.REG_SZ, command)
        
        key_path = r"Directory\shell\FolderLens"
        
        with winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, key_path) as key:
            winreg.SetValue(key, "", winreg.REG_SZ, "Analyze with FolderLens")
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, app_path)
        
        with winreg.CreateKey(winreg.HKEY_CLASSES_ROOT, key_path + r"\command") as key:
            if app_path.endswith('.py'):
                folder_command = f'"{python_exe}" "{app_path}" "%1"'
            else:
                folder_command = f'"{app_path}" "%1"'
            winreg.SetValue(key, "", winreg.REG_SZ, folder_command)
        
        return True, "Context menu installed. Right-click folders to use FolderLens."
        
    except PermissionError:
        return False, "No registry write permission. Run as administrator."
    except Exception as e:
        return False, f"Error: {str(e)}"


def uninstall_context_menu() -> tuple[bool, str]:
    if not is_admin():
        return False, "Admin privileges required. Run as administrator."
    
    errors = []
    
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
        except PermissionError:
            errors.append(f"Cannot delete: {key_path}")
        except Exception as e:
            errors.append(f"Error ({key_path}): {str(e)}")
    
    if errors:
        return False, "Some keys could not be deleted:\n" + "\n".join(errors)
    
    return True, "Context menu removed."


def is_context_menu_installed() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"Directory\shell\FolderLens"):
            return True
    except FileNotFoundError:
        return False
    except Exception:
        return False


def generate_reg_file(app_path: str, output_path: str) -> tuple[bool, str]:
    escaped_path = app_path.replace("\\", "\\\\")
    
    reg_content = f'''Windows Registry Editor Version 5.00

; FolderLens - Folder Size Analyzer
; Double-click to add context menu

; Right-click inside folder (Background)
[HKEY_CLASSES_ROOT\\Directory\\Background\\shell\\FolderLens]
@="Analyze with FolderLens"
"Icon"="{escaped_path}"

[HKEY_CLASSES_ROOT\\Directory\\Background\\shell\\FolderLens\\command]
@="\\"{escaped_path}\\" \\"%V\\""

; Right-click on folder
[HKEY_CLASSES_ROOT\\Directory\\shell\\FolderLens]
@="Analyze with FolderLens"
"Icon"="{escaped_path}"

[HKEY_CLASSES_ROOT\\Directory\\shell\\FolderLens\\command]
@="\\"{escaped_path}\\" \\"%1\\""
'''
    
    try:
        with open(output_path, 'w', encoding='utf-16') as f:
            f.write(reg_content)
        return True, f"Registry file created: {output_path}"
    except Exception as e:
        return False, f"Failed to create file: {str(e)}"


def generate_uninstall_reg_file(output_path: str) -> tuple[bool, str]:
    reg_content = '''Windows Registry Editor Version 5.00

; FolderLens - Remove Context Menu
; Double-click to remove context menu

[-HKEY_CLASSES_ROOT\\Directory\\Background\\shell\\FolderLens]

[-HKEY_CLASSES_ROOT\\Directory\\shell\\FolderLens]
'''
    
    try:
        with open(output_path, 'w', encoding='utf-16') as f:
            f.write(reg_content)
        return True, f"Uninstall file created: {output_path}"
    except Exception as e:
        return False, f"Failed to create file: {str(e)}"


if __name__ == "__main__":
    print("FolderLens Registry Installer")
    print("-" * 40)
    print(f"Admin: {is_admin()}")
    print(f"Installed: {is_context_menu_installed()}")
    print(f"App Path: {get_executable_path()}")
