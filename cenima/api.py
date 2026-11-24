from __future__ import annotations

import ipaddress
import os
import shlex
import socket
from typing import List, Optional
from urllib.parse import urlparse

from .config import __version__
from .history import HistoryManager
from .models import (
    Episode,
    HistoryResponse,
    SearchResult,
    Season,
    ShowDetails,
    StreamSource,
    WatchProgress,
)
from .scraper import TopCinemaScraper, clean_show_title

_scraper: Optional[TopCinemaScraper] = None
history_manager = HistoryManager()


def get_scraper() -> TopCinemaScraper:
    global _scraper
    if _scraper is None:
        _scraper = TopCinemaScraper()
    return _scraper


def _is_blocked_ip(ip_str: str) -> bool:
    ip = ipaddress.ip_address(ip_str)
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def validate_external_url(url: str, require_topcinema: bool = False, resolve_dns: bool = True) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("Invalid URL: must be an http or https URL with a hostname")

    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith((".local", ".internal")):
        raise ValueError("Private or local hostnames are not allowed")

    if require_topcinema and "topcinema" not in host:
        raise ValueError("URL must belong to a TopCinema domain")

    try:
        if _is_blocked_ip(host):
            raise ValueError("Private or non-public IP addresses are not allowed")
    except ValueError as exc:
        if "not allowed" in str(exc):
            raise
        if resolve_dns:
            try:
                infos = socket.getaddrinfo(host, None)
                for info in infos:
                    addr = info[4][0]
                    if _is_blocked_ip(addr):
                        raise ValueError("Hostname resolves to a private or non-public IP address")
            except socket.gaierror:
                pass

    return url.strip()


def transform_search_result(res: dict) -> SearchResult:
    meta = res.get("metadata", {})
    return SearchResult(
        title=clean_show_title(res["title"]),
        original_title=res["title"],
        url=res["url"],
        type=res["type"],
        year=meta.get("year"),
        rating=meta.get("rating") or meta.get("imdb_rating"),
        poster=meta.get("poster"),
        quality=meta.get("quality"),
    )


try:
    from fastapi import FastAPI, HTTPException, Query
    from fastapi.concurrency import run_in_threadpool
    from fastapi.middleware.cors import CORSMiddleware

    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    FastAPI = None  # type: ignore[assignment]

app = None

if HAS_FASTAPI:
    app = FastAPI(
        title="Cenima CLI API",
        description="REST API for TopCinema browsing and streaming",
        version=__version__,
    )

    _cors_env = os.environ.get("CENIMA_CORS_ORIGINS", "")
    _allowed_origins = (
        [o.strip() for o in _cors_env.split(",") if o.strip()]
        if _cors_env.strip()
        else ["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_allowed_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )

    def _ensure_safe_url(url: str, require_topcinema: bool = False) -> str:
        try:
            return validate_external_url(url, require_topcinema=require_topcinema)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.get("/health")
    async def health_check():
        scraper = get_scraper()
        return {"status": "ok", "domain": scraper.base_url}

    @app.get("/search", response_model=List[SearchResult])
    async def search(
        q: str = Query(..., min_length=1),
        type: Optional[str] = Query(None, pattern="^(movie|series|anime)$"),
    ):
        scraper = get_scraper()
        results = await run_in_threadpool(scraper.search, q, type)
        return [transform_search_result(r) for r in results if r]

    @app.get("/show/details", response_model=ShowDetails)
    async def get_show_details(url: str = Query(..., alias="url")):
        safe_url = _ensure_safe_url(url, require_topcinema=True)
        scraper = get_scraper()
        details = await run_in_threadpool(scraper.get_show_details, safe_url)

        if not details:
            raise HTTPException(status_code=404, detail="Show not found")

        seasons_data = [
            Season(
                season_number=s["season_number"],
                season_part=s.get("season_part"),
                display_label=s["display_label"],
                url=s["url"],
                poster=s.get("poster"),
                episodes=[],
            )
            for s in details.get("seasons", [])
        ]

        raw_title = details.get("title") or ""
        clean_title = clean_show_title(raw_title)

        return ShowDetails(
            title=clean_title,
            original_title=raw_title,
            url=details["url"],
            type=details["type"],
            bg_poster=details.get("poster"),
            description=details.get("synopsis"),
            rating=details.get("imdb_rating"),
            year=details.get("year"),
            genres=details.get("genres", []),
            trailer=details.get("trailer"),
            seasons=seasons_data,
            servers=details.get("servers", []),
        )

    @app.get("/season/episodes", response_model=List[Episode])
    async def get_season_episodes(url: str = Query(...)):
        safe_url = _ensure_safe_url(url, require_topcinema=True)
        scraper = get_scraper()
        season_dummy = {"url": safe_url}
        episodes = await run_in_threadpool(scraper.fetch_season_episodes, season_dummy)

        if not episodes:
            return []

        return [
            Episode(
                episode_number=str(ep.get("episode_number", "?")),
                display_number=ep.get("display_number", ""),
                title=ep.get("title", ""),
                url=ep["url"],
                is_special=ep.get("is_special", False),
                servers=[],
            )
            for ep in episodes
        ]

    @app.get("/stream/resolve", response_model=StreamSource)
    async def resolve_stream(
        url: str = Query(..., description="Episode/Movie watch URL"),
    ):
        safe_url = _ensure_safe_url(url, require_topcinema=True)
        scraper = get_scraper()
        content_dummy = {"url": safe_url}

        servers = await run_in_threadpool(scraper.fetch_episode_servers, content_dummy)
        if not servers:
            raise HTTPException(status_code=404, detail="No servers found")

        def _is_vidtube(srv: dict) -> bool:
            embed = str(srv.get("embed_url") or "").lower()
            name = str(srv.get("name") or "").lower()
            return any(k in embed for k in ("vidtube", "cdn-video", "serv-stream")) or "videotube" in name

        ordered_servers = sorted(servers, key=lambda s: (0 if _is_vidtube(s) else 1))

        selected = None
        video_url = None
        for srv in ordered_servers:
            candidate = srv.get("video_url")
            if not candidate and srv.get("embed_url"):
                candidate = await run_in_threadpool(
                    scraper.vidtube_processor.extract,
                    srv["embed_url"],
                    scraper.base_url,
                )
            if candidate:
                selected = srv
                video_url = candidate
                break

        if not selected or not video_url:
            raise HTTPException(status_code=404, detail="Could not extract a direct video URL")

        referer = getattr(video_url, "referer", None) or selected.get("embed_url") or scraper.base_url
        ua = getattr(video_url, "headers", {}).get("User-Agent") or scraper.session.headers.get(
            "User-Agent", "Mozilla/5.0"
        )

        is_vidtube = any(
            k in str(video_url).lower() for k in ["cdn-video", "serv-stream", "vidtube"]
        ) or bool(referer and any(k in str(referer).lower() for k in ["vidtube", "cdn-video"]))

        if is_vidtube:
            if not referer or not any(k in str(referer).lower() for k in ["vidtube", "down.vidtube.one"]):
                referer = "https://down.vidtube.one/"
            if not ua:
                ua = (
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
            mpv_arguments = [
                "mpv",
                f"--referrer={referer}",
                f"--user-agent={ua}",
                str(video_url),
            ]
        else:
            mpv_arguments = [
                "mpv",
                str(video_url),
            ]

        mpv_command = " ".join(shlex.quote(a) for a in mpv_arguments)

        return StreamSource(
            server_number=selected.get("server_number"),
            embed_url=selected.get("embed_url"),
            video_url=str(video_url),
            headers={"Referer": referer, "User-Agent": ua},
            quality=selected.get("quality"),
            mpv_command=mpv_command,
            mpv_arguments=mpv_arguments,
        )

    @app.get("/vidtube/extract")
    async def extract_direct(url: str = Query(...)):
        safe_url = _ensure_safe_url(url, require_topcinema=False)
        scraper = get_scraper()
        video_url = await run_in_threadpool(scraper.vidtube_processor.extract, safe_url)
        if not video_url:
            raise HTTPException(status_code=404, detail="Could not extract video")
        return {"video_url": str(video_url)}

    @app.get("/history", response_model=HistoryResponse)
    async def get_history(limit: int = Query(20, ge=1, le=100)):
        items = history_manager.get_recent_history(limit=limit)
        return HistoryResponse(items=items, total=len(items))

    @app.post("/history/update", response_model=WatchProgress)
    async def update_history(item: WatchProgress):
        return history_manager.update_progress(
            show_title=item.show_title,
            season_number=item.season_number,
            episode_number=item.episode_number,
            time_pos=item.time_pos,
            duration=item.duration,
            show_url=item.show_url,
            content_type=item.content_type,
            season_label=item.season_label,
            episode_title=item.episode_title,
            episode_url=item.episode_url,
        )

    @app.delete("/history")
    async def clear_history():
        history_manager.clear_history()
        return {"status": "ok", "message": "Watch history cleared"}


if __name__ == "__main__":
    if app is None:
        raise SystemExit("FastAPI is not installed. Install with: pip install 'cenima-cli[api]'")
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
