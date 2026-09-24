import os
import heapq
from datetime import datetime
from typing import Optional, Tuple

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


def cancellable_sorted(items, key, reverse=False, should_cancel=None,
                      chunk_size=8192):
    """Sort a large sequence while periodically yielding to cancellation.

    Python's built-in sort is efficient but cannot be interrupted once it
    starts. Sorting bounded runs and merging them lets background UI work stop
    promptly when the view, query, or scan changes.
    """
    if should_cancel is None:
        return sorted(items, key=key, reverse=reverse)
    if should_cancel():
        return []

    if len(items) <= chunk_size:
        result = sorted(items, key=key, reverse=reverse)
        return [] if should_cancel() else result

    runs = []
    for start in range(0, len(items), chunk_size):
        if should_cancel():
            return []
        runs.append(sorted(items[start:start + chunk_size],
                           key=key, reverse=reverse))

    merged = heapq.merge(*runs, key=key, reverse=reverse)
    result = []
    for index, item in enumerate(merged):
        if index % 256 == 0 and should_cancel():
            return []
        result.append(item)
    return result

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
        'extensions': [
            '.jpg', '.jpeg', '.jpe', '.jfif', '.png', '.apng', '.gif', '.bmp',
            '.svg', '.webp', '.ico', '.tif', '.tiff', '.psd', '.raw', '.heic',
            '.heif', '.avif', '.jxl',
        ],
        'color': '#F97316',
        'icon': ICONS['image'],
        'label': 'Image'
    },
    'document': {
        'extensions': [
            '.pdf', '.doc', '.docx', '.xls', '.xlsx', '.ppt', '.pptx', '.txt',
            '.rtf', '.odt', '.ods', '.odp', '.csv', '.tsv', '.md', '.markdown',
            '.log', '.tex', '.pages', '.numbers', '.key',
        ],
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

# Classification is on the hot path for every file in the tree and in each
# filtered view.  Keep the public category descriptions readable, but resolve
# extensions in O(1) instead of walking every category for every node.
_CATEGORY_BY_EXTENSION = {
    extension: category_name
    for category_name, category_info in FILE_CATEGORIES.items()
    if category_name not in ('folder', 'other')
    for extension in category_info['extensions']
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

IMAGE_EXTENSIONS = frozenset(FILE_CATEGORIES['image']['extensions'])

# The first value is stable storage/API identity; the second is the label shown
# in the type picker.  Directories are deliberately not a filter option: they
# remain visible as containers whenever they contain a matching file.
FILE_TYPE_FILTERS = (
    ('all', 'All file types'),
    ('image', 'Images'),
    ('video', 'Videos'),
    ('audio', 'Audio'),
    ('document', 'Documents'),
    ('archive', 'Archives'),
    ('code', 'Code'),
    ('executable', 'Executables'),
    ('font', 'Fonts'),
    ('database', 'Databases'),
    ('other', 'Other'),
)
FILE_TYPE_FILTER_LABELS = dict(FILE_TYPE_FILTERS)


def get_file_category_key(path: str, is_dir: Optional[bool] = None) -> str:
    """Return the stable category key for *path*.

    ``is_dir`` is optional for backwards compatibility.  Callers that already
    have directory metadata (the scanner and UI do) should pass it explicitly;
    otherwise this helper has to ask the filesystem, which is particularly
    expensive on a network share.
    """
    if is_dir is None:
        is_dir = os.path.isdir(path)
    if is_dir:
        return 'folder'

    name = os.path.basename(path).lower()
    _, ext = os.path.splitext(name)
    return _CATEGORY_BY_EXTENSION.get(ext, 'other')


def file_type_matches(path: str, filter_key: str, is_dir: Optional[bool] = None) -> bool:
    """Return whether a file belongs to a type-filter key.

    Folders are containers rather than file types, so they never match a
    category directly; filtered tree projections add them back when they
    contain a matching descendant.
    """
    if is_dir is None:
        is_dir = os.path.isdir(path)
    if filter_key == 'all':
        return not is_dir
    if is_dir:
        return False
    return get_file_category_key(path, is_dir=False) == filter_key


def get_file_icon(path: str, is_dir: Optional[bool] = None) -> str:
    if is_dir is None:
        is_dir = os.path.isdir(path)
    if is_dir:
        return ICONS['folder']
    
    name = os.path.basename(path).lower()
    _, ext = os.path.splitext(name)
    
    if name in SPECIAL_ICONS:
        return SPECIAL_ICONS[name]
    if ext in SPECIAL_ICONS:
        return SPECIAL_ICONS[ext]
    
    category = get_file_category(path, is_dir=False)
    return category['icon']


def get_file_category(path: str, is_dir: Optional[bool] = None) -> dict:
    return FILE_CATEGORIES[get_file_category_key(path, is_dir=is_dir)]


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


def get_file_extension(path: str, is_dir: Optional[bool] = None) -> str:
    if is_dir is None:
        is_dir = os.path.isdir(path)
    if is_dir:
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
        
        category = get_file_category(path, is_dir=is_dir)
        type_label = category['label']
        
        stat = os.stat(path)
        date = format_date(stat.st_ctime)
        
        extension = get_file_extension(path, is_dir=is_dir)
        
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
