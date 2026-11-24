"""Persistent, catalog-keyed Watch Later list."""

import json
import tempfile
import threading
from pathlib import Path

from .config import CONFIG_DIR
from .models import canonical_content_url, content_identity


class WatchLaterStore:
    def __init__(self, path=None):
        self.path = Path(path or CONFIG_DIR / 'watch_later.json')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._items = {}
        self.load()

    def load(self):
        with self._lock:
            try:
                raw = json.loads(self.path.read_text(encoding='utf-8'))
                self._items = raw if isinstance(raw, dict) else {}
            except (FileNotFoundError, json.JSONDecodeError, OSError):
                if self.path.exists():
                    backup = self.path.with_name(self.path.name + '.corrupt.bak')
                    backup.write_bytes(self.path.read_bytes())
                self._items = {}

    def _write(self):
        from .atomic import atomic_write_json
        atomic_write_json(self.path, self._items)

    @staticmethod
    def identity(item):
        show_url = item.get('show_url') or item.get('url')
        if not show_url:
            return None
        return content_identity(show_url,
                                item.get('season_number'),
                                item.get('episode_number'),
                                item.get('season_part'),
                                item.get('content_type') or 'movie')

    def add(self, item):
        identity = self.identity(item)
        if not identity:
            return None
        record = dict(item)
        record['catalog_url'] = canonical_content_url(record.get('url') or record.get('catalog_url') or '')
        record['show_url'] = canonical_content_url(record.get('show_url') or record.get('url') or '')
        with self._lock:
            self._items = dict(self._items)
            self._items[identity] = record
            self._write()
        return identity

    def remove(self, item):
        identity = self.identity(item)
        if not identity:
            return False
        with self._lock:
            removed = self._items.pop(identity, None) is not None
            if removed:
                self._write()
        return removed

    def contains(self, item):
        identity = self.identity(item)
        return bool(identity and identity in self._items)

    def list(self):
        with self._lock:
            return [dict(value) for value in self._items.values()]
