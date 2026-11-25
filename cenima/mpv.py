"""mpv JSON IPC client and live-state watcher for cenima-cli.

cenima-cli launches mpv with ``--input-ipc-server`` using a Unix socket or
Windows named pipe, and reads real-time playback properties over JSON IPC
so the TUI can render a live timeline, pause state, buffering %, cache,
resolution/codec info, etc.

mpv JSON IPC protocol (see ``man mpv`` -> JSON IPC):
    client sends:  {"command": ["get_property", "time-pos"], "request_id": N}
    server replies (same line): {"request_id": N, "data": <value>}
"""
from __future__ import annotations

import json
import logging
import os
import socket
import threading
import time
from typing import Dict, List, Optional, Tuple
import http.server
import socketserver
import hashlib
import re
from urllib.parse import urljoin, urlsplit, parse_qsl, urlencode, urlunsplit

logger = logging.getLogger(__name__)


class MpvIpcError(Exception):
    pass


class _WindowsPipe:
    """Socket-like byte transport for mpv's Windows named pipe."""

    def __init__(self, path: str, timeout: float):
        import ctypes
        from ctypes import wintypes
        import msvcrt

        self._ctypes = ctypes
        self._timeout = timeout
        self._peek = ctypes.WinDLL("kernel32", use_last_error=True).PeekNamedPipe
        self._peek.argtypes = [wintypes.HANDLE, wintypes.LPVOID, wintypes.DWORD,
                               wintypes.LPDWORD, wintypes.LPDWORD, wintypes.LPDWORD]
        self._peek.restype = wintypes.BOOL
        self._file = open(path, "r+b", buffering=0)
        self._handle = msvcrt.get_osfhandle(self._file.fileno())

    def recv(self, size: int) -> bytes:
        from ctypes import wintypes

        deadline = time.monotonic() + self._timeout
        while True:
            available = wintypes.DWORD()
            if not self._peek(self._handle, None, 0, None,
                              self._ctypes.byref(available), None):
                raise self._ctypes.WinError(self._ctypes.get_last_error())
            if available.value:
                return self._file.read(min(size, available.value))
            if time.monotonic() >= deadline:
                raise socket.timeout()
            time.sleep(0.01)

    def sendall(self, data: bytes) -> None:
        remaining = memoryview(data)
        while remaining:
            written = self._file.write(remaining)
            if not written:
                raise OSError("mpv IPC pipe closed during write")
            remaining = remaining[written:]

    def close(self) -> None:
        self._file.close()


class MpvIpcClient:
    """Minimal mpv JSON IPC client over a Unix socket or Windows named pipe."""

    def __init__(self, socket_path: str, timeout: float = 0.15):
        self.socket_path = socket_path
        self._timeout = timeout
        if os.name == "nt":
            self._sock = _WindowsPipe(socket_path, timeout)
        else:
            self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self._sock.settimeout(self._timeout)
            try:
                self._sock.connect(socket_path)
            except OSError:
                self._sock.close()
                raise
        self._buf = b""
        self._request_id = 0

    def _read_line(self) -> Optional[bytes]:
        deadline = time.time() + 2.0
        while time.time() < deadline:
            if b"\n" in self._buf:
                line, self._buf = self._buf.split(b"\n", 1)
                return line
            try:
                chunk = self._sock.recv(4096)
            except socket.timeout:
                continue
            except OSError:
                return None
            if not chunk:
                return None
            self._buf += chunk
        return None

    def command(self, command_args: List) -> Optional[Dict]:
        self._request_id += 1
        req_id = self._request_id
        payload = json.dumps({"command": list(command_args), "request_id": req_id})
        try:
            self._sock.sendall(payload.encode("utf-8") + b"\n")
        except OSError:
            return None
        deadline = time.time() + 3.0
        while time.time() < deadline:
            line = self._read_line()
            if line is None:
                break
            try:
                resp = json.loads(line.decode("utf-8", "replace"))
            except (ValueError, UnicodeDecodeError):
                continue
            if resp.get("request_id") == req_id:
                return resp
        return None

    def get_property(self, name: str) -> Optional[object]:
        resp = self.command(["get_property", name])
        if resp and "data" in resp:
            return resp["data"]
        return None

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass


class MpvState:
    """Latest snapshot of mpv playback properties (updated by MpvWatcher)."""

    def __init__(self) -> None:
        self.time_pos: Optional[float] = None
        self.duration: Optional[float] = None
        self.paused: Optional[bool] = None
        self.buffering: Optional[int] = None
        self.cache_duration: Optional[float] = None
        self.video_format: Optional[str] = None
        self.video_codec: Optional[str] = None
        self.width: Optional[int] = None
        self.height: Optional[int] = None
        self.video_fps: Optional[float] = None
        self.container_fps: Optional[float] = None
        self.display_fps: Optional[float] = None
        self.audio_codec: Optional[str] = None
        self.audio_samplerate: Optional[int] = None
        self.audio_channels: Optional[int] = None
        self.tracks: List[Dict] = []


# mpv property name -> MpvState attribute (only poll what history tracking requires)
PROPERTY_MAP: Dict[str, str] = {
    "time-pos": "time_pos",
    "duration": "duration",
    "pause": "paused",
    "track-list": "tracks",
}


class _ThreadedProxyServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


class StreamProxy:
    """Lightweight local HTTP streaming proxy that proxies stream requests
    using curl_cffi with chrome120 impersonation. This bypasses anti-bot TLS
    fingerprinting (JA3/JA4) enforced by video CDNs (such as VidTube's
    serv-stream-cdn*.cdn-video.xyz) which terminate standard OpenSSL / FFmpeg
    connections mid-stream with 'corrupted CTTS atom' / EOF.
    """

    def __init__(self, target_url: str, referer: str = "", user_agent: str = "", headers=None):
        self.target_url = str(target_url)
        self.referer = referer or ""
        self.headers = {k: v for k, v in (headers or {}).items()
                        if k.lower() not in {"host", "range", "content-length", "connection", "accept-encoding"}}
        self.user_agent = user_agent or "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        self._server: Optional[_ThreadedProxyServer] = None
        self._thread: Optional[threading.Thread] = None
        self.port: int = 0
        self.stream_url: str = ""
        self._resources = {}
        self._resource_lock = threading.Lock()
        self._active = {}
        self._stopping = threading.Event()
        self.last_error = ''

    def _resource(self, url: str) -> str:
        """Register an upstream URI; clients cannot supply arbitrary proxy targets."""
        if urlsplit(url).scheme not in ("http", "https"):
            return url
        suffix = next((ext for ext in (".m3u8", ".mp4", ".m4v", ".webm", ".mkv", ".mpd")
                       if urlsplit(url).path.lower().endswith(ext)), ".bin")
        path = "/resource/" + hashlib.sha256(url.encode()).hexdigest() + suffix
        with self._resource_lock:
            self._resources[path] = url
        return f"http://127.0.0.1:{self.port}{path}"

    def _rewrite_playlist(self, body: bytes, base_url: str) -> bytes:
        def resolve(uri):
            return self._resource(urljoin(base_url, uri))

        lines = []
        for line in body.decode("utf-8-sig").splitlines():
            if line.startswith("#"):
                line = re.sub(r'\bURI="([^"]+)"',
                              lambda match: 'URI="' + resolve(match[1]) + '"', line)
            elif line.strip():
                line = resolve(line.strip())
            lines.append(line)
        return ("\n".join(lines) + "\n").encode("utf-8")

    def start(self) -> str:
        from curl_cffi import requests
        proxy = self
        self._stopping.clear()
        referer = self.referer
        user_agent = self.user_agent

        class ProxyHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                self._forward()

            def do_HEAD(self):
                self._forward(head=True)

            def _forward(self, head=False):
                path = urlsplit(self.path)
                with proxy._resource_lock:
                    target_url = proxy._resources.get(path.path)
                if not target_url or proxy._stopping.is_set():
                    self.send_error(404)
                    return
                # Preserve player-generated live playlist reload parameters.
                if path.query:
                    parts = urlsplit(target_url)
                    query = dict(parse_qsl(parts.query, keep_blank_values=True))
                    query.update(parse_qsl(path.query, keep_blank_values=True))
                    target_url = urlunsplit(parts._replace(query=urlencode(query)))
                session = requests.Session(impersonate="chrome120")
                resp = None
                sent_headers = False
                req_headers = {
                    **proxy.headers,
                    "User-Agent": user_agent,
                    "Accept-Encoding": "identity",
                }
                if referer:
                    req_headers["Referer"] = referer
                if "Range" in self.headers:
                    req_headers["Range"] = self.headers["Range"]

                resp = None
                headers_sent = False
                try:
                    with proxy._resource_lock:
                        if proxy._stopping.is_set():
                            return
                        proxy._active[id(session)] = (session, None)
                    request = session.head if head else session.get
                    resp = request(target_url, headers=req_headers, stream=True, timeout=15)
                    with proxy._resource_lock:
                        proxy._active[id(session)] = (session, resp)
                    if proxy._stopping.is_set():
                        return
                    iterator = iter(resp.iter_content()) if not head else iter(())
                    first = next(iterator, b"")
                    # Network chunks need not contain a complete playlist signature.
                    while not head and len(first) < 10:
                        chunk = next(iterator, b"")
                        if not chunk:
                            break
                        first += chunk
                    content_type = resp.headers.get("Content-Type", "").lower()
                    playlist = (resp.status_code == 200 and not head and (
                        "mpegurl" in content_type or
                        urlsplit(str(resp.url or target_url)).path.lower().endswith(".m3u8") or
                        first.lstrip(b"\xef\xbb\xbf \r\n").startswith(b"#EXTM3U")))
                    body = None
                    if playlist:
                        chunks, size = [first], len(first)
                        if size > 8 * 1024 * 1024:
                            raise ValueError("HLS playlist exceeds size limit")
                        for chunk in iterator:
                            size += len(chunk)
                            if size > 8 * 1024 * 1024:
                                raise ValueError("HLS playlist exceeds size limit")
                            chunks.append(chunk)
                        body = proxy._rewrite_playlist(b"".join(chunks), str(resp.url or target_url))
                    self.send_response(resp.status_code)
                    sent_headers = True
                    for h in ["Content-Type", "Content-Length", "Content-Range", "Accept-Ranges"]:
                        if playlist or (h == "Content-Length" and resp.headers.get("Content-Encoding")):
                            continue
                        val = resp.headers.get(h)
                        if val:
                            self.send_header(h, val)
                    if playlist:
                        self.send_header("Content-Type", "application/vnd.apple.mpegurl")
                        self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    if head:
                        return
                    if playlist:
                        self.wfile.write(body)
                    else:
                        if first:
                            self.wfile.write(first)
                        for chunk in iterator:
                            if proxy._stopping.is_set():
                                break
                            self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                except Exception as exc:
                    logger.debug("StreamProxy request error for %s: %s", target_url, exc)
                    proxy.last_error = f'{type(exc).__name__}: {exc}'
                    if not sent_headers:
                        try:
                            self.send_error(502, "Upstream stream unavailable")
                        except OSError:
                            pass
                finally:
                    with proxy._resource_lock:
                        proxy._active.pop(id(session), None)
                    try:
                        if resp is not None:
                            resp.close()
                    except Exception:
                        pass
                    finally:
                        try:
                            session.close()
                        except Exception:
                            pass

            def log_message(self, format, *args):
                pass

        self._server = _ThreadedProxyServer(("127.0.0.1", 0), ProxyHandler)
        self.port = self._server.server_address[1]
        suffix = "m3u8" if urlsplit(self.target_url).path.lower().endswith(".m3u8") else "mp4"
        path = f"/stream.{suffix}"
        self._resources[path] = self.target_url
        self.stream_url = f"http://127.0.0.1:{self.port}{path}"
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self.stream_url

    def stop(self) -> None:
        self._stopping.set()
        with self._resource_lock:
            active = list(self._active.values())
        for session, response in active:
            try:
                if response is not None:
                    response.close()
            except Exception:
                pass
            finally:
                try:
                    session.close()
                except Exception:
                    pass
        if self._server:
            try:
                self._server.shutdown()
                self._server.server_close()
            except Exception:
                pass
        if self._thread:
            self._thread.join(timeout=3)



class MpvWatcher(threading.Thread):
    """Polls mpv JSON IPC properties in the background, exposing the latest state."""

    def __init__(self, socket_path: str, poll_interval: float = 0.4):
        super().__init__(daemon=True, name="mpv-watcher")
        self.socket_path = socket_path
        self.state = MpvState()
        self._poll_interval = poll_interval
        self._stop_event = threading.Event()

    def _connect(self) -> Optional[MpvIpcClient]:
        # mpv creates the socket shortly after startup; retry for a while.
        for _ in range(100):  # up to ~10s of retries
            if self._stop_event.is_set():
                return None
            try:
                return MpvIpcClient(self.socket_path)
            except OSError:
                self._stop_event.wait(0.1)
        return None

    def run(self) -> None:
        client = self._connect()
        if client is None:
            return
        try:
            while not self._stop_event.is_set():
                for prop, attr in PROPERTY_MAP.items():
                    if self._stop_event.is_set():
                        break
                    try:
                        value = client.get_property(prop)
                    except Exception:
                        value = None
                    if value is not None:
                        setattr(self.state, attr, value)
                self._stop_event.wait(self._poll_interval)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop_event.set()


def run_mpv_monitored(
    url: str,
    title: Optional[str] = None,
    start_time: float = 0.0,
    extra_args: Optional[List[str]] = None,
    referer: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
    subtitle_language: Optional[str] = None,
    local_file: bool = False,
) -> Tuple[int, MpvState]:
    """Launch mpv with IPC monitoring enabled and return exit code and final MpvState."""
    import subprocess
    import tempfile
    from pathlib import Path
    from .validation import validate_http_url
    # Validation returns a plain str; retain StreamURL's request context first.
    request_headers = dict(getattr(url, "headers", {}) or {})
    request_headers.update(headers or {})
    source_referer = getattr(url, "referer", None)
    try:
        if local_file:
            path = Path(url).expanduser().resolve(strict=True)
            if not path.is_file():
                raise ValueError("The local media path is not a file.")
            url = str(path)
        else:
            url = validate_http_url(url, allow_loopback=True)
    except (ValueError, OSError) as exc:
        raise MpvIpcError(str(exc)) from exc

    ipc_name = f"cenima-mpv-{os.getpid()}-{time.time_ns()}"
    if os.name == "nt":
        sock_path = "\\\\.\\pipe\\" + ipc_name
    else:
        sock_path = os.path.join(tempfile.gettempdir(), ipc_name + ".sock")

    cmd = ["mpv", f"--input-ipc-server={sock_path}", "--no-terminal"]
    if subtitle_language:
        from .subtitles import mpv_subtitle_args
        cmd.extend(mpv_subtitle_args(subtitle_language))

    if title:
        cmd.append(f"--title={title}")

    if start_time > 5.0:
        cmd.append(f"--start={int(start_time)}")

    # Extract or infer headers and referrer
    lower_headers = {key.lower(): value for key, value in request_headers.items()}
    req_referer = referer or source_referer or lower_headers.get("referer")
    req_ua = lower_headers.get("user-agent")

    # Detect VidTube specifically (requires specific referer and a real browser UA to prevent 403 / TLS drops)
    is_vidtube = any(k in str(url).lower() for k in ["cdn-video", "serv-stream", "vidtube"]) or \
                 bool(req_referer and any(k in req_referer.lower() for k in ["vidtube", "cdn-video"]))

    # Detect UpDown specifically (gamescdn.online requires Referer: https://updown.cam/ to prevent 403 Forbidden)
    is_updown = any(k in str(url).lower() for k in ["gamescdn", "updown"]) or \
                bool(req_referer and "updown" in req_referer.lower())

    # Detect Uqload specifically
    is_uqload = "uqload" in str(url).lower() or bool(req_referer and "uqload" in req_referer.lower())

    if is_vidtube:
        if not req_referer or not any(k in req_referer.lower() for k in ["vidtube", "down.vidtube.one"]):
            req_referer = "https://down.vidtube.one/"
        if not req_ua:
            req_ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    elif is_updown:
        if not req_referer or "updown" not in req_referer.lower():
            req_referer = "https://updown.cam/"
        if not req_ua:
            req_ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    elif is_uqload:
        if not req_referer:
            req_referer = "https://uqload.net/"
        if not req_ua:
            req_ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    if extra_args:
        cmd.extend(extra_args)

    # Fast start optimizations: skip external yt-dlp probing on direct streams
    cmd.append("--ytdl=no")

    # VidTube servers enforce TLS browser fingerprint checks; route through local StreamProxy
    proxy = None
    watcher = MpvWatcher(sock_path, poll_interval=0.5)
    proc = None
    try:
        extra_headers = any(key not in ('referer', 'user-agent') for key in lower_headers)
        if not local_file and (is_vidtube or extra_headers):
            proxy = StreamProxy(target_url=str(url), referer=req_referer,
                                user_agent=req_ua, headers=request_headers)
            play_url = proxy.start()
        else:
            if not local_file and req_referer:
                cmd.append(f"--referrer={req_referer}")
            if not local_file and req_ua:
                cmd.append(f"--user-agent={req_ua}")
            play_url = str(url)
        cmd.extend(['--', play_url])
        watcher.start()
        proc = subprocess.Popen(cmd)
        rc = proc.wait()
        from .diagnostics import log_event
        if rc not in (0, 130):
            log_event('playback', 'mpv exited unexpectedly', 'warning',
                      title=title or 'Unknown', return_code=rc)
    except KeyboardInterrupt:
        rc = 130
    except Exception as exc:
        logger.error("Failed to launch mpv: %s", exc)
        rc = 1
    finally:
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        watcher.stop()
        if watcher.ident is not None:
            watcher.join(timeout=3.0)
        if proxy:
            proxy.stop()
        if os.name != "nt" and os.path.exists(sock_path):
            try:
                os.remove(sock_path)
            except OSError:
                pass

    return rc, watcher.state

