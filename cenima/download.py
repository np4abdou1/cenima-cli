"""Supervised transfers of freshly resolved streams with isolated partial files."""
import asyncio
from dataclasses import dataclass
import hashlib
import importlib.util
import os
from pathlib import Path
import re
import shutil
import sys
import signal
import subprocess
import json
from collections import deque
from typing import Optional
from urllib.parse import urlsplit

from .models import canonical_content_url
from .mpv import StreamProxy


class DownloadError(RuntimeError):
    pass


def downloader_command():
    if importlib.util.find_spec("yt_dlp") is not None:
        return [sys.executable, "-m", "yt_dlp"]
    executable = shutil.which("yt-dlp")
    if executable:
        return [executable]
    raise DownloadError('Install download support: python -m pip install ".[download]"')


def check_download_tools():
    command = downloader_command()
    if not shutil.which("ffmpeg"):
        raise DownloadError("Install ffmpeg and add it to PATH before downloading.")
    if not shutil.which("ffprobe"):
        ffmpeg = shutil.which("ffmpeg")
        sibling = Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe")
        if not sibling.is_file():
            raise DownloadError("Install FFprobe with FFmpeg and add it to PATH before downloading.")
    return command


def safe_name(value):
    value = re.sub(r'[<>:"/\\|?*\x00-\x1f\x7f]', "_", str(value)).strip(" .")
    value = value[:120].rstrip(' .') or 'Untitled'
    if re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', value):
        value = '_' + value
    return value


def available_download_qualities(quality_labels):
    qualities = set()
    for label in quality_labels:
        match = re.search(r'(?<!\d)(360|480|720|1080)p', str(label), re.I)
        if match:
            qualities.add(match.group(1) + 'p')
    return sorted(qualities, key=lambda value: int(value[:-1]), reverse=True)


def explicit_quality_fallback(preferred, available):
    if preferred == 'best' or preferred not in available:
        return 'best' if preferred == 'best' else 'fallback'
    return preferred


def download_stem(title, catalog_url):
    return safe_name(title)


def media_title(show_title, season_number=None, episode_number=None, season_part=None):
    if episode_number is None:
        return safe_name(show_title)
    try:
        season = f'{int(season_number):02d}'
    except (TypeError, ValueError):
        season = str(season_number or '?')
    try:
        number = float(str(episode_number))
        episode = f'{int(number):02d}' if number.is_integer() else str(episode_number)
    except ValueError:
        episode = str(episode_number)
    part = ' ' + str(season_part).strip() if season_part else ''
    suffix = f' S{season}E{episode}{part}'
    return safe_name(safe_name(show_title)[:max(1, 120 - len(suffix))] + suffix)


def redact_reason(message):
    message = ''.join(ch for ch in message if ch.isprintable() or ch in '\n\t')
    return re.sub(r'https?://[^\s\]\[<>"\']+',
                  lambda match: f'{urlsplit(match.group()).scheme}://{urlsplit(match.group()).hostname}/<redacted>',
                  message)


def partial_root(directory, title):
    return Path(directory) / '.partial' / hashlib.sha256(safe_name(title).casefold().encode()).hexdigest()[:16]


@dataclass
class DownloadResult:
    path: Optional[Path]
    skipped: bool = False
    integrity: Optional[dict] = None


async def download_stream(url, directory, title, catalog_url, on_progress=None, *, stall_timeout=90, manager='builtin',
                          quality='best', speed='unlimited'):
    """Reject concurrent writers to the same catalog item, across processes."""
    directory = Path(directory).expanduser().resolve()
    lock_dir = partial_root(directory, title)
    lock_dir.mkdir(parents=True, exist_ok=True)
    with (lock_dir / "download.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise DownloadError("This item is already downloading in another session.") from exc
        try:
            owner = lock_dir / 'owner.json'
            identity = hashlib.sha256(canonical_content_url(catalog_url).encode()).hexdigest()
            state = json.loads(owner.read_text(encoding='utf-8')) if owner.exists() else {'identity': identity}
            if state['identity'] != identity:
                raise DownloadError('A different catalog item uses this filename. Choose a separate destination.')
            temporary = owner.with_suffix('.tmp')
            temporary.write_text(json.dumps(state), encoding='utf-8')
            temporary.replace(owner)
            result = await _download_stream(url, directory, title, catalog_url, on_progress, stall_timeout, manager,
                                            quality, speed)
            completed = {'name': result.path.name, 'bytes': result.path.stat().st_size}
            if result.integrity:
                completed.update(result.integrity)
            state['completed'] = completed
            temporary.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
            temporary.replace(owner)
            return result
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


async def _download_stream(url, directory, title, catalog_url, on_progress=None, stall_timeout=90, manager='builtin',
                           quality='best', speed='unlimited'):
    """Delegate transfer/fragment resume to yt-dlp; never rename partials ourselves.

    A hidden catalog identity protects a clean output name. Each upstream resource gets
    its own partial directory so alternate servers cannot append incompatible data.
    No signed URL or request header is persisted by this module.
    """
    command = check_download_tools()
    from .validation import validate_http_url, safe_output_path
    try:
        url_text = validate_http_url(str(url), allow_loopback=True)
    except ValueError as exc:
        raise DownloadError(str(exc)) from exc
    directory = safe_output_path(directory, '.')
    directory.mkdir(parents=True, exist_ok=True)
    from .transfer_safety import check_space
    check_space(directory)
    stem = download_stem(title, catalog_url)
    owner_path = partial_root(directory, title) / 'owner.json'
    # Only recognize media outputs, never .part, .ytdl or empty files.
    for ext in ("mp4", "mkv", "webm", "m4v", "ts", "mov"):
        path = directory / (stem + "." + ext)
        if path.is_file() and path.stat().st_size:
            state = json.loads(owner_path.read_text(encoding='utf-8'))
            expected = state.get('completed') or {}
            if expected.get('name') != path.name or expected.get('bytes') != path.stat().st_size:
                raise DownloadError('Existing file is unverified or has changed. Move it before downloading again.')
            from .transfer_safety import validate_media
            await validate_media(path, expected_size=path.stat().st_size,
                                 expected_duration=expected.get('duration') or 0,
                                 expected_hash=expected.get('sha256'))
            return DownloadResult(path, skipped=True)
    resource = urlsplit(str(url))
    # Include the query: it may select different bytes/quality, not just a token.
    # Only a hash is persisted. Cross-URL resume needs upstream validators.
    source_id = hashlib.sha256((resource.netloc + resource.path +
        ('?' + resource.query if resource.query else '')).encode()).hexdigest()[:16]
    partial_dir = partial_root(directory, title) / source_id
    partial_dir.mkdir(parents=True, exist_ok=True)
    headers = dict(getattr(url, "headers", {}) or {})
    referer = getattr(url, "referer", "") or headers.get("Referer", "")
    proxy = StreamProxy(str(url), referer=referer,
                        user_agent=headers.get("User-Agent", ""), headers=headers)
    process = None
    final_path = None
    diagnostics = deque(maxlen=20)
    try:
        local_url = proxy.start()
        from .transfer_safety import probe_source, prepare_partial, validate_media
        try:
            entity = probe_source(local_url)
        except Exception as exc:
            raise DownloadError(str(exc)) from exc
        _, trusted = prepare_partial(partial_root(directory, title), source_id, entity)
        entity_file = partial_dir / 'entity.json'
        previous = json.loads(entity_file.read_text(encoding='utf-8')) if entity_file.exists() else None
        reusable = trusted and (previous is None or previous == entity)
        if entity['kind'] == 'hls' or not reusable:
            for suffix in ('.part', '.ytdl'):
                for partial in partial_dir.glob('*' + suffix):
                    partial.unlink(missing_ok=True)
        if reusable:
            entity_file.write_text(json.dumps(entity), encoding='utf-8')
        else:
            entity_file.write_text(json.dumps(entity), encoding='utf-8')
        # Escape literal percent signs in the directory/title, which yt-dlp
        # otherwise interprets as output-template expressions.
        template = stem.replace("%", "%%") + ".%(ext)s"
        format_sort = {
            '1080p': 'res:1080',
            '720p': 'res:720',
            '480p': 'res:480',
            '360p': 'res:360',
            'best': 'best',
        }.get(quality, 'best')
        speed_bytes = {
            '2MB/s': '2M',
            '5MB/s': '5M',
            '10MB/s': '10M',
            'unlimited': None,
        }.get(speed)
        command += ["--ignore-config", "--no-playlist", "--no-simulate",
                    "--force-generic-extractor",
                    "--format-sort", format_sort,
                    *([] if speed_bytes is None else ["--limit-rate", speed_bytes]),
                    "--continue", "--no-overwrites", "--no-colors", "--newline", "--encoding", "utf-8",
                    "--socket-timeout", "15", "--retries", "3", "--fragment-retries", "3",
                    "--abort-on-unavailable-fragments", "--concurrent-fragments", "1",
                    "--downloader", "dash,m3u8:native",
                    "--paths", "home:" + str(directory),
                    "--paths", "temp:" + str(partial_dir), "--output", template,
                    "--print", "after_move:CINEMA_FILE:%(filepath)s",
                    "--progress", "--progress-template",
                    "download:CINEMA_PROGRESS:%(progress._percent_str)s %(progress._speed_str)s ETA %(progress._eta_str)s",
                    "--", local_url]
        if manager == 'aria2c':
            from .managers import detected_managers, direct_file
            if 'aria2c' in detected_managers() and direct_file(url):
                command[-2:-2] = ['--downloader', 'http,https:aria2c', '--downloader-args',
                                  'aria2c:--max-connection-per-server=4 --split=4 --summary-interval=1']
                if on_progress:
                    on_progress('Delegated direct transfer to aria2c through the protected proxy')
        process = await asyncio.create_subprocess_exec(
            *command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
            limit=1024 * 1024, env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            **({"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
               if os.name == "nt" else {"start_new_session": True}))
        while True:
            try:
                line = await asyncio.wait_for(process.stdout.readline(), timeout=stall_timeout)
            except asyncio.TimeoutError as exc:
                raise DownloadError("Transfer stalled without progress; partial files were retained.") from exc
            if not line:
                break
            message = line.decode("utf-8", errors="replace").strip()
            if message.startswith("CINEMA_FILE:"):
                final_path = Path(message.partition(":")[2]).resolve()
            elif message.startswith("CINEMA_PROGRESS:") and on_progress:
                from .transfer_safety import check_space
                check_space(directory)
                on_progress(message.partition(":")[2])
            elif message:
                diagnostics.append(redact_reason(message))
                if manager == 'aria2c' and on_progress:
                    match = re.search(r'\((\d+)%\)', message)
                    if match:
                        on_progress(match[1] + '% · aria2c ' + redact_reason(message))
        returncode = await process.wait()
        if returncode:
            reason = '\n'.join(line for line in diagnostics if 'ERROR' in line.upper()) or '\n'.join(list(diagnostics)[-4:])
            if proxy.last_error:
                reason += '\nUpstream proxy: ' + redact_reason(proxy.last_error)
            from .diagnostics import log_event
            log_event('download', 'yt-dlp transfer failed', 'error',
                      title=title, reason=reason)
            raise DownloadError(f"yt-dlp exited with code {returncode}: {reason or 'No diagnostic output.'}")
        if (not final_path or final_path.parent != directory or
                not final_path.name.startswith(stem + ".") or
                not final_path.is_file() or not final_path.stat().st_size):
            raise DownloadError("Downloader did not report a completed media file.")
        from .transfer_safety import validate_media
        media = await validate_media(final_path, expected_size=entity.get('size') if entity.get('kind') == 'direct' else 0)
        return DownloadResult(final_path, integrity=media)
    finally:
        # asyncio cancellation (including Ctrl+C) must finish the child before
        # stopping the proxy; partial files remain available for a later retry.
        if process and process.returncode is None:
            from .transfer_safety import terminate_tree
            await terminate_tree(process)
        await asyncio.to_thread(proxy.stop)


async def download_catalog_item(scraper, item, directory, title, on_progress=None):
    """Fresh catalog resolution, transfer fallback, then one bounded link refresh.

    Queue records contain catalog pages only. Never cache a signed stream as a job.
    Each transfer retains its own host/path partial directory.
    """
    from .processor import ServerProcessor, StreamURL
    attempts = 0
    reasons = []
    failures = []
    for refresh in range(2):
        servers = await asyncio.to_thread(scraper.fetch_episode_servers, {"url": item["url"]})
        for server in servers or []:
            try:
                if on_progress:
                    on_progress(("Resolving " if not refresh else "Refreshing ") + str(server.get('name') or 'server'))
                processor = ServerProcessor()
                try:
                    video = server.get("video_url") or await asyncio.to_thread(
                        processor.extract, server.get("embed_url"), scraper.base_url)
                finally:
                    processor.session.close()
                if not video:
                    reasons.append(redact_reason(f"{server.get('name', 'Server')}: {processor.last_error or 'extractor did not find a media URL'}"))
                    continue
                manager = item.get('manager') or 'builtin'
                if manager == 'idm':
                    from .managers import idm_eligible, send_to_idm
                    if idm_eligible(video):
                        await asyncio.to_thread(send_to_idm, video, directory, title)
                        return DownloadResult(None)
                    if on_progress:
                        on_progress('IDM ineligible (HLS, headers or signed URL); using built-in transfer')
                if not getattr(video, "referer", ""):
                    address = str(video).lower()
                    referer = ("https://down.vidtube.one/" if any(key in address for key in
                        ("vidtube", "cdn-video", "serv-stream")) else
                        "https://updown.cam/" if "gamescdn" in address or "updown" in address else
                        "https://uqload.net/" if "uqload" in address else server.get("embed_url") or scraper.base_url)
                    video = StreamURL(str(video), referer, getattr(video, "headers", {}))
                attempts += 1
                return await download_stream(video, directory, title, item["url"], on_progress, manager=manager,
                                             quality=item.get('quality') or 'best',
                                             speed=item.get('speed') or 'unlimited')
            except Exception as exc:
                failures.append(redact_reason(f"{server.get('name', 'Server')}: {exc}"))
                from .diagnostics import log_event
                log_event('scraper', 'Provider resolution or transfer failed', 'warning',
                          server=server.get('name', 'Server'), error=str(exc))
                if on_progress:
                    on_progress("Server failed; trying another source")
                continue
    raise DownloadError(f"No working stream after one catalog refresh ({attempts} transfer attempts).\n" + '\n'.join((failures or reasons)[-4:]))
