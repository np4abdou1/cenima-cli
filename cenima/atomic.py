"""Atomic JSON and SQLite-adjacent state helpers."""

import json
import os
import tempfile
from pathlib import Path


def atomic_write_json(path, value, *, backup=False):
    path = Path(path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        backup_path = path.with_name(path.name + '.bak')
        backup_path.write_bytes(path.read_bytes())
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8',
                                         dir=path.parent, delete=False) as handle:
            temporary = Path(handle.name)
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        try:
            directory_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except (AttributeError, OSError):
            pass
        return path
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def load_json_state(path, default):
    path = Path(path)
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(value, type(default)):
            return default
        return value
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default
