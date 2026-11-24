"""Local environment diagnostics. This module never installs or changes native tools."""

import importlib.util
import shutil
import sys
from pathlib import Path

from .config import CONFIG_DIR


def _writable_directory(path):
    try:
        path = Path(path).expanduser()
        path.mkdir(parents=True, exist_ok=True)
        probe = path / '.cenima-write-test'
        with probe.open('wb') as handle:
            handle.write(b'')
        probe.unlink()
        return True, f'Writable: {path}'
    except OSError as exc:
        return False, f'Not writable: {path} ({exc})'


def collect_doctor(config=None):
    results = []
    py_ok = sys.version_info >= (3, 9)
    results.append({
        'name': 'Python',
        'ok': py_ok,
        'detail': f'{sys.version.split()[0]}',
        'fix': 'Install Python 3.9 or newer and add it to PATH.',
    })

    tools = (
        ('mpv', shutil.which('mpv'), 'Install mpv and add its executable directory to PATH.',
         'Required for playback'),
        ('yt-dlp', shutil.which('yt-dlp') or ('python -m yt_dlp' if importlib.util.find_spec('yt_dlp') else None),
         'Install support with: python -m pip install "cenima-cli[download]"',
         'Required for downloads'),
        ('ffmpeg', shutil.which('ffmpeg'),
         'Install FFmpeg, including ffprobe, and add the bin directory to PATH.',
         'Required for download processing'),
        ('ffprobe', shutil.which('ffprobe'),
         'Install FFprobe with FFmpeg and add the bin directory to PATH.',
         'Required for completion validation'),
    )
    for name, executable, fix, purpose in tools:
        results.append({
            'name': name,
            'ok': bool(executable),
            'detail': executable or 'Not found',
            'fix': fix,
            'purpose': purpose,
        })

    aria = shutil.which('aria2c')
    results.append({
        'name': 'aria2c',
        'ok': bool(aria),
        'detail': aria or 'Not installed (optional)',
        'fix': 'Install aria2c and add it to PATH only if you want optional direct transfer support.',
        'optional': True,
    })

    config_ok, config_detail = _writable_directory(CONFIG_DIR)
    results.append({
        'name': 'Config directory',
        'ok': config_ok,
        'detail': config_detail,
        'fix': 'Change the file permissions or set HOME to a writable user directory.',
    })

    download_dir = getattr(config, 'download_dir', None) if config else None
    if not download_dir:
        download_dir = Path.home() / 'Downloads' / 'cenima-cli'
    download_ok, download_detail = _writable_directory(download_dir)
    results.append({
        'name': 'Download directory',
        'ok': download_ok,
        'detail': download_detail,
        'fix': 'Choose a writable media folder in /settings.',
    })
    return results


def run_doctor(console, config=None):
    results = collect_doctor(config)
    failed = False
    console.print('[bold]Cinema CLI environment check[/bold]')
    for result in results:
        optional = result.get('optional')
        if result['ok']:
            console.print(f"[green]OK[/green]     {result['name']}: {result['detail']}")
        elif optional:
            console.print(f"[yellow]OPTIONAL[/yellow] {result['name']}: {result['detail']}")
        else:
            failed = True
            console.print(f"[red]MISSING[/red] {result['name']}: {result['detail']}")
            console.print(f"        Fix: {result['fix']}")
    if not failed:
        console.print('[green]Environment ready[/green]')
    from .diagnostics import log_path
    console.print(f"Diagnostic log: {log_path()}")
    return not failed
