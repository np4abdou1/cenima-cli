"""Server liveness probing with a short-lived cache for cenima-cli.

Each server gets one of three verdicts:

``live``     a direct stream URL was resolved *and* the CDN answered the very
             request mpv will make (HTTP 200/206, playable content type)
``slow``     a stream URL was resolved but the CDN did not answer in time or
             returned an unexpected status -- it may still play in mpv
``offline``  no stream could be resolved at all

Probing is expensive, so verdicts are cached per embed URL and shared by every
picker in the process.
"""

import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from .processor import ServerProcessor

LIVE = "live"
SLOW = "slow"
OFFLINE = "offline"

# File hosts that never yield a stream mpv can play.
DEAD_HOST_MARKERS = (
    "bowfile", "1cloudfile", "nitroflare", "1fichier",
    "rapidgator", "ddownload", "mdiaload", "cloudfile",
)

PLAYABLE_STATUS = (200, 206)
GONE_STATUS = (404, 410)
DEFAULT_TIMEOUT = 6.0
PREFLIGHT_TIMEOUT = 4.0
DEFAULT_TTL = 300.0  # seconds a cached verdict stays valid

_RANK = {LIVE: 0, SLOW: 1, OFFLINE: 2}

# embed_url -> (checked_at, verdict, stream)
_CACHE: Dict[str, Tuple[float, str, Optional[str]]] = {}
_CACHE_LOCK = threading.Lock()


def clear_cache() -> None:
    """Drop every cached liveness verdict (used by tests / manual refresh)."""
    with _CACHE_LOCK:
        _CACHE.clear()


def _cache_get(url: str, ttl: float) -> Optional[Tuple[str, Optional[str]]]:
    if not url:
        return None
    with _CACHE_LOCK:
        entry = _CACHE.get(url)
        if entry is None:
            return None
        checked_at, verdict, stream = entry
        if (time.time() - checked_at) > ttl:
            _CACHE.pop(url, None)
            return None
        return verdict, stream


def _cache_put(url: str, verdict: str, stream: Optional[str]) -> None:
    if not url:
        return
    with _CACHE_LOCK:
        _CACHE[url] = (time.time(), verdict, stream)


class ServerHealthChecker:
    """Probe embed servers concurrently and annotate them with ``health``."""

    def __init__(self, referer: Optional[str] = None, timeout: float = DEFAULT_TIMEOUT,
                 ttl: float = DEFAULT_TTL):
        self.referer = referer
        self.timeout = timeout
        self.preflight_timeout = min(timeout, PREFLIGHT_TIMEOUT)
        self.ttl = ttl
        # curl_cffi sessions are not guaranteed to be thread-safe, so each
        # worker thread keeps its own processor/session.
        self._local = threading.local()

    def _processor(self) -> ServerProcessor:
        proc = getattr(self._local, "proc", None)
        if proc is None:
            proc = self._local.proc = ServerProcessor()
        return proc

    def _reachable(self, url: str, proc: ServerProcessor) -> Optional[str]:
        """Cheap pre-flight so dead hosts fail fast (no full extraction).

        Returns the final URL after any 301/302 redirect, or ``None`` when the
        embed page itself is unreachable.
        """
        try:
            resp = proc.session.get(url, timeout=self.preflight_timeout,
                                    stream=True, allow_redirects=True)
        except Exception:
            return None
        try:
            if resp.status_code >= 400:
                return None
            return str(resp.url or url)
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def _verify_stream(self, stream: str, embed_url: str,
                       proc: ServerProcessor) -> str:
        """Ask the CDN for the first bytes of the stream and grade the answer."""
        headers = {"User-Agent": proc.headers.get("User-Agent", ""), "Referer": embed_url}
        headers.update(getattr(stream, "headers", {}) or {})
        if getattr(stream, "referer", None):
            headers["Referer"] = stream.referer

        try:
            resp = proc.session.get(str(stream), headers=headers, stream=True, timeout=self.timeout)
        except Exception:
            # Resolved, but the CDN refused/timed out: let the user try anyway.
            return SLOW

        try:
            if resp.status_code in GONE_STATUS:
                return OFFLINE
            if resp.status_code not in PLAYABLE_STATUS:
                return SLOW
            ctype = (resp.headers.get("content-type") or "").lower()
            if "text/html" in ctype and ".m3u8" not in str(stream).lower():
                return OFFLINE
            return LIVE
        finally:
            try:
                resp.close()
            except Exception:
                pass

    def _probe(self, url: str) -> Tuple[str, Optional[str]]:
        if not url:
            return OFFLINE, None

        host = (urlparse(url).hostname or "").lower()
        if any(marker in host for marker in DEAD_HOST_MARKERS):
            return OFFLINE, None

        proc = self._processor()
        # Embed links often 301/302 to the canonical host; extract from there.
        target = self._reachable(url, proc)
        if not target:
            return OFFLINE, None

        try:
            stream = proc.extract(target, referer=self.referer)
        except Exception:
            return OFFLINE, None
        if not stream or not str(stream).startswith("http"):
            return OFFLINE, None

        return self._verify_stream(stream, url, proc), stream

    def probe(self, server: Dict[str, Any]) -> Dict[str, Any]:
        """Annotate one server dict with ``health``, ``is_live`` and ``video_url``."""
        url = server.get("embed_url") or ""
        cached = _cache_get(url, self.ttl)
        if cached is not None:
            verdict, stream = cached
        else:
            verdict, stream = self._probe(url)
            _cache_put(url, verdict, stream)

        server["health"] = verdict
        server["is_live"] = verdict == LIVE
        if stream:
            server["video_url"] = stream
        elif not server.get("video_url"):
            server["video_url"] = None
        return server

    def probe_all(self, servers: List[Dict[str, Any]], max_workers: int = 16,
                  on_progress: Optional[Callable[[], None]] = None) -> List[Dict[str, Any]]:
        """Probe every server concurrently, return them sorted live/slow/offline.

        The returned list preserves the incoming order within each status
        group, so server numbering stays predictable.
        """
        if not servers:
            return []

        position = {id(srv): i for i, srv in enumerate(servers)}
        results: List[Dict[str, Any]] = []

        with ThreadPoolExecutor(max_workers=max(1, min(len(servers), max_workers))) as pool:
            futures = {pool.submit(self.probe, srv): srv for srv in servers}
            for future in as_completed(futures):
                srv = futures[future]
                try:
                    results.append(future.result())
                except Exception:
                    srv["health"] = OFFLINE
                    srv["is_live"] = False
                    results.append(srv)
                if on_progress:
                    on_progress()

        results.sort(key=lambda srv: (_RANK.get(srv.get("health", OFFLINE), 2),
                                      position.get(id(srv), 0)))
        return results
