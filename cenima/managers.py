"""Optional external transfer managers. IDM is a handoff, not tracked completion."""
import os
from pathlib import Path
import shutil
import subprocess
from urllib.parse import urlsplit


def detected_managers():
    found = {'builtin': None}
    aria = shutil.which('aria2c')
    if aria:
        found['aria2c'] = aria
    idm = shutil.which('idman.exe')
    if os.name == 'nt' and not idm:
        for root in (os.environ.get('ProgramFiles(x86)'), os.environ.get('ProgramFiles')):
            if root:
                candidate = Path(root) / 'Internet Download Manager' / 'IDMan.exe'
                if candidate.is_file():
                    idm = str(candidate)
                    break
    if idm:
        found['idm'] = idm
    return found


def direct_file(url):
    return urlsplit(str(url)).path.lower().endswith(('.mp4', '.mkv', '.webm', '.m4v', '.mov'))


def idm_eligible(url):
    parts = urlsplit(str(url))
    return (parts.scheme in ('http', 'https') and (parts.hostname or '').lower() not in ('localhost', '127.0.0.1', '::1')
            and direct_file(url) and not parts.query
            and not getattr(url, 'headers', {}) and not getattr(url, 'referer', '')
            and not any(host in (parts.hostname or '') for host in ('vidtube', 'cdn-video', 'serv-stream', 'gamescdn', 'uqload')))


def send_to_idm(url, directory, title):
    executable = detected_managers().get('idm')
    if not executable or not idm_eligible(url):
        raise RuntimeError('IDM requires an available manager and a header-free, non-expiring direct file.')
    from .download import safe_name
    from .validation import validate_http_url, safe_output_path
    url = validate_http_url(url)
    directory = safe_output_path(directory, '.')
    directory.mkdir(parents=True, exist_ok=True)
    extension = Path(urlsplit(str(url)).path).suffix.lower()
    subprocess.run([executable, '/d', str(url), '/p', str(directory), '/f', safe_name(title) + extension, '/n'],
                   check=True, timeout=15, **({'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}))
