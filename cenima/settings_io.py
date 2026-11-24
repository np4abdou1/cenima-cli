"""Validated, redacted settings import/export."""

import json
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


SCHEMA_VERSION = 1
SENSITIVE_KEYS = ('token', 'password', 'secret', 'cookie', 'authorization', 'credential')
SAFE_KEYS = ('theme', 'simple_mode', 'send_headers', 'referer', 'user_agent',
             'fullscreen', 'hwdec', 'cache_pause', 'download_dir', 'download_quality',
             'download_speed', 'subtitle_language', 'notifications', 'subtitle_preference')


def _redact(value):
    if isinstance(value, dict):
        return {key: '[REDACTED]' if any(word in key.lower() for word in SENSITIVE_KEYS)
                else _redact(child) for key, child in value.items()}
    if isinstance(value, list):
        return [_redact(child) for child in value]
    return value


def export_settings(config):
    data = config.to_dict() if hasattr(config, 'to_dict') else dict(config)
    safe = {key: data[key] for key in SAFE_KEYS if key in data}
    return {'schema': SCHEMA_VERSION, 'settings': _redact(safe)}


def write_settings_export(config, path):
    path = Path(path).expanduser()
    payload = export_settings(config)
    from .atomic import atomic_write_json
    atomic_write_json(path, payload)
    return path


def validate_settings_payload(payload):
    if not isinstance(payload, dict) or payload.get('schema') != SCHEMA_VERSION:
        raise ValueError('Unsupported settings file; expected schema version 1.')
    settings = payload.get('settings')
    if not isinstance(settings, dict):
        raise ValueError('Settings file has no valid settings object.')
    unknown = set(settings) - set(SAFE_KEYS)
    if unknown:
        raise ValueError('Settings file contains unsupported keys: ' + ', '.join(sorted(unknown)))
    for key in ('download_dir', 'download_quality', 'download_speed',
                'subtitle_language', 'subtitle_preference'):
        if key in settings and not isinstance(settings[key], str):
            raise ValueError(f'Setting {key} must be text.')
    for key in ('notifications', 'simple_mode', 'send_headers', 'fullscreen', 'hwdec', 'cache_pause'):
        if key in settings and not isinstance(settings[key], bool):
            raise ValueError(f'Setting {key} must be true or false.')
    return settings


def read_settings_import(path):
    path = Path(path).expanduser()
    payload = json.loads(path.read_text(encoding='utf-8'))
    return validate_settings_payload(payload)
