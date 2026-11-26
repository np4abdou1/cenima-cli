"""Disk budgeting, entity checks and finite media validation for supervised transfers."""
import asyncio
from email.utils import parsedate_to_datetime
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
import uuid


class DownloadError(RuntimeError):
    pass


class LocalTransferError(DownloadError):
    """A local problem must not retry every CDN as though it were a network fault."""


class IntegrityError(DownloadError):
    pass


DISK_RESERVE = 256 * 1024 * 1024


def check_space(directory, remaining=0, reserve=DISK_RESERVE):
    free = shutil.disk_usage(directory).free
    required = max(0, int(remaining)) + reserve
    if free < required:
        raise LocalTransferError(f'Not enough disk space: {free // 1024**2} MiB free; '
                                 f'{required // 1024**2} MiB required including headroom. '
                                 'Free space or change the media folder in /settings.')
    return free


def ffprobe_command():
    command = shutil.which('ffprobe')
    if not command:
        ffmpeg = shutil.which('ffmpeg')
        sibling = Path(ffmpeg).with_name('ffprobe.exe' if os.name == 'nt' else 'ffprobe') if ffmpeg else None
        command = str(sibling) if sibling and sibling.is_file() else None
    if not command:
        raise LocalTransferError('FFprobe is required to validate downloads. Install the full FFmpeg build and add its bin directory to PATH; run cinema-cli doctor.')
    return command


def probe_source(local_url):
    """Read headers through the existing TLS proxy, without saving signed URLs."""
    try:
        try:
            response = urlopen(Request(local_url, method='HEAD'), timeout=20)
        except HTTPError as exc:
            exc.close()
            if exc.code not in (403, 404, 405, 501):
                raise
            response = urlopen(Request(local_url, headers={'Range': 'bytes=0-0'}), timeout=20)
        with response:
            headers = response.headers
            kind = 'hls' if 'mpegurl' in headers.get('Content-Type', '').lower() or local_url.split('?')[0].endswith('.m3u8') else 'direct'
            size = int(headers.get('Content-Length', '0'))
            if headers.get('Content-Range'):
                size = int(headers['Content-Range'].rsplit('/', 1)[-1])
            etag = headers.get('ETag', '')
            etag = etag if etag and not etag.startswith('W/') else ''
            modified = headers.get('Last-Modified', '')
            try:
                parsedate_to_datetime(modified)
            except (ValueError, TypeError):
                modified = ''
        # Force playlist parsing before spawning yt-dlp. Variant playlists are
        # also checked by the strict proxy when yt-dlp requests them.
        if kind == 'hls':
            with urlopen(local_url, timeout=20) as playlist:
                body = playlist.read(8 * 1024 * 1024 + 1)
            if len(body) > 8 * 1024 * 1024:
                raise DownloadError('HLS playlist is unexpectedly large.')
            text = body.decode('utf-8', errors='replace')
            if '#EXT-X-ENDLIST' not in text:
                raise DownloadError('Live or unbounded HLS streams are not supported; only finite VOD playlists can be downloaded.')
        return {'kind': kind, 'size': size if kind == 'direct' else 0,
                'etag': etag, 'last_modified': modified}
    except Exception as exc:
        raise DownloadError(f'Upstream preflight failed: {exc}') from exc


def prepare_partial(root, source_id, entity):
    trusted = (entity['kind'] == 'direct' and entity.get('size', 0) > 0)
    # Validators partition partials even when the same URL begins serving a new
    # representation. No validator (or HLS) means a fresh generation, not append.
    generation = hashlib.sha256(json.dumps(entity, sort_keys=True).encode()).hexdigest()[:16] if trusted else uuid.uuid4().hex[:16]
    path = Path(root) / source_id / generation
    path.mkdir(parents=True, exist_ok=True)
    (path / 'entity.json').write_text(json.dumps(entity), encoding='utf-8')
    return path, trusted


def file_sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def process_options():
    return ({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW}
            if os.name == 'nt' else {'start_new_session': True})


class ProcessGuard:
    """Windows kill-on-close Job Object also catches children after parent exit."""
    def __init__(self, process):
        self.handle = None
        if os.name != 'nt':
            return
        import ctypes
        from ctypes import wintypes as w
        class Basic(ctypes.Structure):
            _fields_ = [('process_time', ctypes.c_longlong), ('job_time', ctypes.c_longlong),
                ('flags', w.DWORD), ('min_working', ctypes.c_size_t), ('max_working', ctypes.c_size_t),
                ('active', w.DWORD), ('affinity', ctypes.c_size_t), ('priority', w.DWORD), ('scheduling', w.DWORD)]
        class Extended(ctypes.Structure):
            _fields_ = [('basic', Basic), ('io', ctypes.c_ulonglong * 6),
                ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                ('peak_process', ctypes.c_size_t), ('peak_job', ctypes.c_size_t)]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.CreateJobObjectW.argtypes, kernel.CreateJobObjectW.restype = [w.LPVOID, w.LPCWSTR], w.HANDLE
        kernel.SetInformationJobObject.argtypes = [w.HANDLE, ctypes.c_int, w.LPVOID, w.DWORD]
        kernel.OpenProcess.argtypes, kernel.OpenProcess.restype = [w.DWORD, w.BOOL, w.DWORD], w.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [w.HANDLE, w.HANDLE]
        kernel.CloseHandle.argtypes = [w.HANDLE]
        handle = kernel.CreateJobObjectW(None, None)
        information = Extended()
        information.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        child = None
        try:
            if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(information), ctypes.sizeof(information)):
                raise ctypes.WinError(ctypes.get_last_error())
            child = kernel.OpenProcess(0x0100 | 0x0001, False, process.pid)
            if not child or not kernel.AssignProcessToJobObject(handle, child):
                # A very short-lived version/probe process may already be gone.
                if process.returncode is not None:
                    kernel.CloseHandle(handle)
                    return
                raise ctypes.WinError(ctypes.get_last_error())
            self.handle, self.kernel = handle, kernel
        except BaseException:
            if handle:
                kernel.CloseHandle(handle)
            raise
        finally:
            if child:
                kernel.CloseHandle(child)

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


async def spawn_owned(*command, **kwargs):
    process = await asyncio.create_subprocess_exec(*command, **kwargs, **process_options())
    try:
        return process, ProcessGuard(process)
    except BaseException:
        await terminate_tree(process)
        raise


async def terminate_tree(process):
    """Reap the owned child and its descendants, even if the parent exits first."""
    import signal
    if os.name == 'nt':
        if process.returncode is None:
            killer = await asyncio.create_subprocess_exec('taskkill', '/PID', str(process.pid), '/T', '/F',
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                creationflags=subprocess.CREATE_NO_WINDOW)
            try:
                await asyncio.wait_for(killer.wait(), 5)
            except asyncio.TimeoutError:
                killer.kill()
                await killer.wait()
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    if process.returncode is None:
        try:
            await asyncio.wait_for(process.wait(), 5)
        except asyncio.TimeoutError:
            process.kill()
            await process.wait()
    if os.name != 'nt':
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


async def validate_media(path, expected_size=0, expected_duration=0, expected_hash=None):
    path = Path(path)
    size = path.stat().st_size
    if size <= 0 or (expected_size and size != expected_size):
        raise IntegrityError(f'Media size mismatch: expected {expected_size} bytes, found {size}.')
    process, guard = await spawn_owned(ffprobe_command(), '-v', 'error', '-count_packets',
        '-show_entries', 'format=duration,size:stream=codec_type,codec_name,width,height,channels,nb_read_packets',
        '-of', 'json', str(path), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    try:
        output, error = await asyncio.wait_for(process.communicate(), timeout=300)
        if process.returncode or error.strip():
            raise IntegrityError('FFprobe rejected media: ' + error.decode('utf-8', errors='replace')[-1500:])
        data = json.loads(output)
        duration = float(data.get('format', {}).get('duration') or 0)
        streams = data.get('streams') or []
        video = [s for s in streams if s.get('codec_type') == 'video' and s.get('codec_name')
                 and int(s.get('width') or 0) > 0 and int(s.get('height') or 0) > 0
                 and int(s.get('nb_read_packets') or 0) > 0]
        if not math.isfinite(duration) or duration <= 0 or not video:
            raise IntegrityError('Media has no finite duration or readable video packets.')
        if expected_duration and abs(duration - expected_duration) > max(2, expected_duration * 0.03):
            raise IntegrityError(f'Media duration mismatch: expected {expected_duration:.1f}s, found {duration:.1f}s.')
        checksum = await asyncio.to_thread(file_sha256, path)
        if expected_hash and checksum != expected_hash:
            raise IntegrityError('Media checksum changed; the completed output is damaged or modified.')
        return {'sha256': checksum, 'duration': duration, 'streams': sorted({s['codec_type'] for s in streams})}
    except asyncio.TimeoutError as exc:
        raise IntegrityError('Media verification timed out; output was not accepted.') from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise IntegrityError('FFprobe returned invalid media metadata.') from exc
    finally:
        guard.close()
        await terminate_tree(process)


class TransferWatchdog:
    def __init__(self, stall_timeout=90, remux_timeout=600, now=None):
        self.phase = 'download'
        self.stall_timeout, self.remux_timeout = stall_timeout, remux_timeout
        self.updated = self.phase_started = time.monotonic() if now is None else now
        self.bytes = 0

    def bytes_progress(self, count, now=None):
        if count > self.bytes:
            self.bytes = count
            self.updated = time.monotonic() if now is None else now

    def processing(self, now=None):
        if self.phase != 'processing':
            self.phase = 'processing'
            self.phase_started = time.monotonic() if now is None else now

    def check(self, now=None):
        now = time.monotonic() if now is None else now
        if self.phase == 'processing':
            if now - self.phase_started > self.remux_timeout:
                raise DownloadError('Media remux/processing timed out; this is not a network stall.')
        elif now - self.updated > self.stall_timeout:
            raise DownloadError('Network transfer stalled without new bytes; partial files were retained.')
