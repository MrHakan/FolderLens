import os
from datetime import datetime
from typing import Tuple

ICONS = {
    'folder': '📁',
    'folder_open': '📂',
    'video': '🎬',
    'audio': '🎵',
    'image': '🖼️',
    'document': '📄',
    'pdf': '📕',
    'spreadsheet': '📊',
    'presentation': '📽️',
    'archive': '📦',
    'code': '💻',
    'python': '🐍',
    'javascript': '📜',
    'html': '🌐',
    'css': '🎨',
    'json': '📋',
    'executable': '⚙️',
    'text': '📝',
    'font': '🔤',
    'database': '🗄️',
    'config': '⚡',
    'git': '🔀',
    'markdown': '📑',
    'other': '📎',
    'refresh': '🔄',
    'settings': '⚙️',
    'sun': '☀️',
    'moon': '🌙',
    'check': '✓',
    'check_empty': '○',
    'check_filled': '●',
    'delete': '🗑️',
    'zip': '📦',
    'analyze': '📊',
    'eye': '👁️',
    'eye_off': '👁️‍🗨️',
}

FILE_CATEGORIES = {
    'folder': {
        'extensions': [],
        'color': '#F59E0B',
        'icon': ICONS['folder'],
        'label': 'Folder'
    },
    'video': {
        'extensions': ['.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.mpeg', '.mpg', '.3gp'],
        'color': '#8B5CF6',
        'icon': ICONS['video'],
        'label': 'Video'
    },
    'audio': {
        'extensions': ['.mp3', '.wav', '.flac', '.aac', '.ogg', '.wma', '.m4a', '.opus', '.mid', '.midi'],
        'color': '#EC4899',
        'icon': ICONS['audio'],
        'label': 'Audio'
    },
    'image': {
        'extensions': ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.svg', '.webp', '.ico', '.tiff', '.psd', '.raw', '.heic'],
        'color': '#F97316',
        'icon': ICONS['image'],
        'label': 'Image'
    },
    'document': {
        'extensions': ['.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt', '.rtf', '.odt', '.ods', '.odp'],
        'color': '#3B82F6',
        'icon': ICONS['document'],
        'label': 'Document'
    },
    'archive': {
        'extensions': ['.zip', '.rar', '.7z', '.tar', '.gz', '.bz2', '.xz', '.iso', '.dmg'],
        'color': '#EF4444',
        'icon': ICONS['archive'],
        'label': 'Archive'
    },
    'code': {
        'extensions': ['.py', '.js', '.ts', '.html', '.css', '.json', '.xml', '.java', '.cpp', '.c', '.h', '.cs', '.go', '.rs', '.php', '.rb', '.swift', '.kt', '.jsx', '.tsx', '.vue', '.scss', '.sass', '.less'],
        'color': '#10B981',
        'icon': ICONS['code'],
        'label': 'Code'
    },
    'executable': {
        'extensions': ['.exe', '.msi', '.bat', '.cmd', '.ps1', '.sh', '.app', '.dll', '.so', '.bin'],
        'color': '#6366F1',
        'icon': ICONS['executable'],
        'label': 'Executable'
    },
    'font': {
        'extensions': ['.ttf', '.otf', '.woff', '.woff2', '.eot'],
        'color': '#14B8A6',
        'icon': ICONS['font'],
        'label': 'Font'
    },
    'database': {
        'extensions': ['.db', '.sqlite', '.sql', '.mdb', '.accdb'],
        'color': '#F472B6',
        'icon': ICONS['database'],
        'label': 'Database'
    },
    'other': {
        'extensions': [],
        'color': '#64748B',
        'icon': ICONS['other'],
        'label': 'Other'
    }
}

SPECIAL_ICONS = {
    '.py': ICONS['python'],
    '.js': ICONS['javascript'],
    '.jsx': ICONS['javascript'],
    '.ts': ICONS['javascript'],
    '.tsx': ICONS['javascript'],
    '.html': ICONS['html'],
    '.htm': ICONS['html'],
    '.css': ICONS['css'],
    '.scss': ICONS['css'],
    '.sass': ICONS['css'],
    '.json': ICONS['json'],
    '.md': ICONS['markdown'],
    '.markdown': ICONS['markdown'],
    '.pdf': ICONS['pdf'],
    '.xls': ICONS['spreadsheet'],
    '.xlsx': ICONS['spreadsheet'],
    '.csv': ICONS['spreadsheet'],
    '.ppt': ICONS['presentation'],
    '.pptx': ICONS['presentation'],
    '.gitignore': ICONS['git'],
    '.gitattributes': ICONS['git'],
    '.env': ICONS['config'],
    '.ini': ICONS['config'],
    '.cfg': ICONS['config'],
    '.conf': ICONS['config'],
    '.yaml': ICONS['config'],
    '.yml': ICONS['config'],
    '.toml': ICONS['config'],
}

IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.ico'}


def get_file_icon(path: str) -> str:
    if os.path.isdir(path):
        return ICONS['folder']
    
    name = os.path.basename(path).lower()
    _, ext = os.path.splitext(name)
    
    if name in SPECIAL_ICONS:
        return SPECIAL_ICONS[name]
    if ext in SPECIAL_ICONS:
        return SPECIAL_ICONS[ext]
    
    category = get_file_category(path)
    return category['icon']


def get_file_category(path: str) -> dict:
    if os.path.isdir(path):
        return FILE_CATEGORIES['folder']
    
    _, ext = os.path.splitext(path)
    ext = ext.lower()
    
    for category_name, category_info in FILE_CATEGORIES.items():
        if ext in category_info['extensions']:
            return category_info
    
    return FILE_CATEGORIES['other']


def is_image_file(path: str) -> bool:
    _, ext = os.path.splitext(path)
    return ext.lower() in IMAGE_EXTENSIONS


def format_size(size_bytes: int) -> str:
    if size_bytes < 0:
        return "0 B"
    
    units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
    unit_index = 0
    size = float(size_bytes)
    
    while size >= 1024 and unit_index < len(units) - 1:
        size /= 1024
        unit_index += 1
    
    if unit_index == 0:
        return f"{int(size)} {units[unit_index]}"
    else:
        return f"{size:.2f} {units[unit_index]}"


def format_date(timestamp: float) -> str:
    """Format unix timestamp to readable date"""
    try:
        dt = datetime.fromtimestamp(timestamp)
        return dt.strftime("%Y-%m-%d %H:%M")
    except (OSError, ValueError, OverflowError):
        return "-"


def get_file_extension(path: str) -> str:
    if os.path.isdir(path):
        return "Folder"
    
    _, ext = os.path.splitext(path)
    if ext:
        return ext[1:].upper()
    return "File"


def get_file_info(path: str) -> Tuple[str, int, str, str, str]:
    try:
        name = os.path.basename(path)
        is_dir = os.path.isdir(path)
        
        if is_dir:
            size = 0
        else:
            size = os.path.getsize(path)
        
        category = get_file_category(path)
        type_label = category['label']
        
        stat = os.stat(path)
        date = format_date(stat.st_ctime)
        
        extension = get_file_extension(path)
        
        return (name, size, type_label, date, extension)
    except (OSError, PermissionError) as e:
        name = os.path.basename(path)
        return (name, 0, "Unknown", "-", "-")


def calculate_percentage(size: int, total_size: int) -> float:
    if total_size <= 0:
        return 0.0
    return min((size / total_size) * 100, 100.0)


def natural_sort_key(s: str) -> list:
    import re
    return [int(text) if text.isdigit() else text.lower() 
            for text in re.split(r'(\d+)', s)]
