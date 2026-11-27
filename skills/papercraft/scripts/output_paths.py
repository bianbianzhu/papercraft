"""Path checks shared by the local artwork exporters."""
import os
import stat
import sys
import unicodedata
from pathlib import Path


def filename_stem(name):
    device = name.split('.')[0].rstrip(' ').upper()
    reserved = {'CON', 'PRN', 'AUX', 'NUL', 'CONIN$', 'CONOUT$'}
    reserved.update(prefix + n for prefix in ('COM', 'LPT') for n in '123456789¹²³')
    if (not name or name.startswith('.') or name.endswith(('.', ' ')) or device in reserved
            or len(name.encode('utf-8')) > 160 or any(c in '<>:"/\\|?*' for c in name)
            or any(unicodedata.category(c).startswith('C') for c in name)):
        raise ValueError('use a portable file name up to 160 UTF-8 bytes, without path separators or controls')
    return name


def linked_path(path):
    try:
        info = path.lstat()
    except (FileNotFoundError, NotADirectoryError):
        return False
    return (stat.S_ISLNK(info.st_mode) or
            bool(getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT))


def output_directory(path):
    path = Path(os.path.abspath(path))
    # macOS ships these top-level aliases. Normalize them before inspecting the
    # caller-selected components, including existing parents of a new output.
    if sys.platform == 'darwin' and len(path.parts) > 1 and path.parts[1] in ('tmp', 'var'):
        alias = Path('/') / path.parts[1]
        if alias.resolve() == Path('/private') / path.parts[1]:
            path = alias.resolve().joinpath(*path.parts[2:])
    if any(linked_path(part) for part in (path, *path.parents)):
        raise ValueError('output directory path contains a symbolic link')
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve(strict=True)


def output_file(directory, filename):
    path = directory / filename
    if path.parent != directory or linked_path(path) or (path.exists() and not path.is_file()):
        raise ValueError('invalid output file: ' + repr(filename))
    return path
