import sys
import os
import argparse
import ctypes

if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

sys.path.insert(0, BASE_DIR)


def require_admin():
    """Restart with admin privileges if needed"""
    if ctypes.windll.shell32.IsUserAnAdmin():
        return True
    
    try:
        if getattr(sys, 'frozen', False):
            executable = sys.executable
        else:
            executable = sys.executable
            
        params = ' '.join([f'"{arg}"' for arg in sys.argv])
        
        ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, params, None, 1
        )
        sys.exit(0)
    except Exception as e:
        print(f"Failed to get admin privileges: {e}")
        return False


def main():
    """Main function"""
    parser = argparse.ArgumentParser(
        description="FolderLens - Folder Size Analyzer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py                         Open application
  python main.py "C:\\Users\\Documents"  Analyze folder
  python main.py --install               Install context menu (admin required)
  python main.py --uninstall             Remove context menu
  python main.py --generate-reg          Generate .reg file for manual install
        """
    )
    
    parser.add_argument(
        "folder",
        nargs="?",
        default=None,
        help="Folder path to analyze"
    )
    
    parser.add_argument(
        "--install",
        action="store_true",
        help="Add FolderLens to Windows context menu"
    )
    
    parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Remove FolderLens from Windows context menu"
    )
    
    parser.add_argument(
        "--generate-reg",
        action="store_true",
        help="Generate .reg file for manual install"
    )
    
    parser.add_argument(
        "--console",
        action="store_true",
        help="Run in console mode (CLI)"
    )
    
    args = parser.parse_args()
    
    if args.install:
        from registry_installer import install_context_menu, is_admin
        
        if not is_admin():
            print("Admin privileges required. Restarting...")
            require_admin()
            return
        
        success, message = install_context_menu()
        print(("[OK] " if success else "[ERROR] ") + message)
        return
    
    if args.uninstall:
        from registry_installer import uninstall_context_menu, is_admin
        
        if not is_admin():
            print("Admin privileges required. Restarting...")
            require_admin()
            return
        
        success, message = uninstall_context_menu()
        print(("[OK] " if success else "[ERROR] ") + message)
        return
    
    if args.generate_reg:
        from registry_installer import generate_reg_file, generate_uninstall_reg_file, get_executable_path
        
        app_path = get_executable_path()
        
        install_reg = os.path.join(BASE_DIR, "FolderLens_Install.reg")
        success1, msg1 = generate_reg_file(app_path, install_reg)
        print(("[OK] " if success1 else "[ERROR] ") + msg1)
        
        uninstall_reg = os.path.join(BASE_DIR, "FolderLens_Uninstall.reg")
        success2, msg2 = generate_uninstall_reg_file(uninstall_reg)
        print(("[OK] " if success2 else "[ERROR] ") + msg2)
        
        print("\nDouble-click these files to install/uninstall context menu.")
        return
    
    if args.console:
        from scanner import FolderScanner
        from file_utils import format_size
        
        folder = args.folder or os.getcwd()
        
        if not os.path.isdir(folder):
            print(f"[ERROR] Folder not found: {folder}")
            return
        
        print(f"Scanning: {folder}")
        print("-" * 60)
        
        scanner = FolderScanner()
        result_holder = [None]
        
        def on_complete(result):
            result_holder[0] = result
        
        def on_error(error):
            print(f"[ERROR] {error}")
        
        scanner.scan(folder, on_complete=on_complete, on_error=on_error)
        
        import time
        while scanner.is_scanning:
            time.sleep(0.1)
        
        result = result_holder[0]
        if result:
            result.items.sort(key=lambda x: x.size, reverse=True)
            
            print(f"{'Name':<40} {'Size':>15} {'Type':<15}")
            print("-" * 70)
            
            for item in result.items:
                name = item.name[:38] + ".." if len(item.name) > 40 else item.name
                size = format_size(item.size)
                item_type = "Folder" if item.is_directory else "File"
                print(f"{name:<40} {size:>15} {item_type:<15}")
            
            print("-" * 70)
            print(f"Total: {result.total_items} items, {format_size(result.total_size)}")
            print(f"Scan time: {result.scan_time:.2f}s")
            
            if result.errors:
                print(f"\n{len(result.errors)} errors occurred")
        
        return
    
    folder = args.folder
    
    if folder:
        folder = os.path.abspath(folder)
        if not os.path.isdir(folder):
            print(f"Warning: '{folder}' is not a valid folder.")
            folder = None
    
    try:
        from app import run_app
        run_app(folder)
    except ImportError as e:
        print(f"[ERROR] Module not found: {e}")
        print("Install dependencies: pip install -r requirements.txt")
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Failed to start: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
