"""URL and filesystem validation for subprocesses and local storage."""

from pathlib import Path
from urllib.parse import urlsplit


def validate_http_url(value, *, allow_loopback=False):
    value = str(value).strip()
    parts = urlsplit(value)
    if parts.scheme not in ('http', 'https'):
        raise ValueError('Only http:// and https:// URLs are allowed.')
    if not parts.netloc or not parts.hostname:
        raise ValueError('The URL has no valid host.')
    host = parts.hostname.lower()
    if parts.username or parts.password:
        raise ValueError('URLs containing embedded credentials are not allowed.')
    if host in ('localhost', '127.0.0.1', '::1'):
        if not allow_loopback:
            raise ValueError('Loopback URLs are not allowed here.')
    if any(char in value for char in '\r\n\0'):
        raise ValueError('The URL contains unsafe control characters.')
    return value


def validate_local_proxy_url(value):
    value = validate_http_url(value, allow_loopback=True)
    host = (urlsplit(value).hostname or '').lower()
    if host not in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Only controlled local proxy URLs are allowed.')
    return value


def safe_output_path(base, filename_or_path):
    base = Path(base).expanduser().resolve()
    candidate = Path(filename_or_path)
    if not candidate.is_absolute():
        candidate = base / candidate
    candidate = candidate.resolve()
    try:
        candidate.relative_to(base)
    except ValueError as exc:
        raise ValueError('Output path escapes the allowed base directory.') from exc
    if candidate.name in ('', '.', '..'):
        raise ValueError('Output filename is invalid.')
    return candidate


def safe_import_file(base, source):
    base = Path(base).expanduser().resolve()
    source = Path(source).expanduser().resolve()
    try:
        source.relative_to(base)
    except ValueError as exc:
        raise ValueError('Import path escapes the allowed base directory.') from exc
    if not source.is_file():
        raise FileNotFoundError(str(source))
    return source
