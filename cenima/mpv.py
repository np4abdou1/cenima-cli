"""mpv JSON IPC client and live-state watcher for cenima-cli.

cenima-cli launches mpv with ``--input-ipc-server=/tmp/cenima-mpv-<pid>.sock``
and uses this module to read real-time playback properties over mpv's JSON IPC
socket so the TUI can render a live timeline, pause state, buffering %, cache,
resolution/codec info, etc.

mpv JSON IPC protocol (see ``man mpv`` -> JSON IPC):
    client sends:  {"command": ["get_property", "time-pos"], "request_id": N}
    server replies (same line): {"request_id": N, "data": <value>}
"""
from __future__ import annotations

import json
import socket
import threading
import time
from typing import Dict, List, Optional


class MpvIpcError(Exception):
    pass


class MpvIpcClient:
    """Minimal synchronous mpv JSON IPC client over a unix socket."""

    def __init__(self, socket_path: str, timeout: float = 0.15):
        self.socket_path = socket_path
        self._timeout = timeout
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(self._timeout)
        self._sock.connect(socket_path)
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


# mpv property name -> MpvState attribute
PROPERTY_MAP: Dict[str, str] = {
    "time-pos": "time_pos",
    "duration": "duration",
    "pause": "paused",
    "cache-buffering-state": "buffering",
    "demuxer-cache-duration": "cache_duration",
    "video-format": "video_format",
    "video-codec": "video_codec",
    "video-params/width": "width",
    "video-params/height": "height",
    "video-params/fps": "video_fps",
    "container-fps": "container_fps",
    "display-fps": "display_fps",
    "audio-codec": "audio_codec",
    "audio-params/samplerate": "audio_samplerate",
    "audio-params/channels": "audio_channels",
}


class MpvWatcher(threading.Thread):
    """Polls mpv JSON IPC properties in the background, exposing the latest state."""

    def __init__(self, socket_path: str, poll_interval: float = 0.4):
        super().__init__(daemon=True, name="mpv-watcher")
        self.socket_path = socket_path
        self.state = MpvState()
        self._poll_interval = poll_interval
        self._stop = threading.Event()

    def _connect(self) -> Optional[MpvIpcClient]:
        # mpv creates the socket shortly after startup; retry for a while.
        for _ in range(100):  # up to ~10s of retries
            if self._stop.is_set():
                return None
            try:
                return MpvIpcClient(self.socket_path)
            except OSError:
                time.sleep(0.1)
        return None

    def run(self) -> None:
        client = self._connect()
        if client is None:
            return
        try:
            while not self._stop.is_set():
                for prop, attr in PROPERTY_MAP.items():
                    try:
                        value = client.get_property(prop)
                    except Exception:
                        value = None
                    if value is not None:
                        setattr(self.state, attr, value)
                time.sleep(self._poll_interval)
        finally:
            try:
                client.close()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop.set()


def run_mpv_monitored(
    url: str,
    title: Optional[str] = None,
    start_time: float = 0.0,
    extra_args: Optional[List[str]] = None,
    referer: Optional[str] = None,
    headers: Optional[Dict[str, str]] = None,
) -> Tuple[int, MpvState]:
    """Launch mpv with IPC monitoring enabled and return exit code and final MpvState."""
    import os
    import subprocess
    import tempfile

    sock_dir = tempfile.gettempdir()
    sock_path = os.path.join(sock_dir, f"cenima-mpv-{os.getpid()}-{int(time.time())}.sock")

    cmd = ["mpv", f"--input-ipc-server={sock_path}"]

    if title:
        cmd.append(f"--title={title}")

    if start_time > 5.0:
        cmd.append(f"--start={int(start_time)}")

    # Extract or infer headers and referrer
    req_referer = referer or getattr(url, "referer", None) or (headers.get("Referer") if headers else None)
    req_ua = (headers.get("User-Agent") if headers else None) or getattr(url, "headers", {}).get("User-Agent")

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

    if req_referer:
        cmd.append(f"--referrer={req_referer}")

    if req_ua:
        cmd.append(f"--user-agent={req_ua}")

    if extra_args:
        cmd.extend(extra_args)

    cmd.append(str(url))

    watcher = MpvWatcher(sock_path, poll_interval=0.3)
    watcher.start()

    try:
        proc = subprocess.run(cmd)
        rc = proc.returncode
    except KeyboardInterrupt:
        rc = 0
    except Exception:
        rc = 1
    finally:
        watcher.stop()
        if os.path.exists(sock_path):
            try:
                os.remove(sock_path)
            except OSError:
                pass

    return rc, watcher.state

