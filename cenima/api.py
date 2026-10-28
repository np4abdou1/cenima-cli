from pydantic import BaseModel
from typing import List, Optional, Any, Dict
from urllib.parse import urljoin
import re
import shlex

# Import existing scraper and processor
from .scraper import TopCinemaScraper, clean_arabic_title, clean_show_title
from .processor import VidTubeProcessor
from .history import HistoryManager
from .models import SearchResult, ShowDetails, StreamSource, Episode, Season, WatchProgress, HistoryResponse

scraper = TopCinemaScraper()
history_manager = HistoryManager()

try:
    from fastapi import FastAPI, HTTPException, Query, Path
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.concurrency import run_in_threadpool
    HAS_FASTAPI = True
except ImportError:
    HAS_FASTAPI = False
    FastAPI = None

if HAS_FASTAPI:
    app = FastAPI(
        title="Cenima CLI API",
        description="REST API for TopCinema browsing and streaming",
        version="0.2.0"
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
else:
    app = None

def transform_search_result(res: dict) -> SearchResult:
    meta = res.get("metadata", {})
    return SearchResult(
        title=clean_show_title(res["title"]),
        original_title=res["title"], # Keep original just in case
        url=res["url"],
        type=res["type"],
        year=meta.get("year"),
        rating=meta.get("rating") or meta.get("imdb_rating"),
        poster=meta.get("poster"),
        quality=meta.get("quality")
    )

@app.get("/health")
async def health_check():
    return {"status": "ok", "domain": scraper.base_url}

@app.get("/search", response_model=List[SearchResult])
async def search(
    q: str = Query(..., min_length=1),
    type: Optional[str] = Query(None, pattern="^(movie|series|anime)$")
):
    results = await run_in_threadpool(scraper.search, q, type)
    return [transform_search_result(r) for r in results if r]

@app.get("/show/details", response_model=ShowDetails)
async def get_show_details(url: str = Query(..., alias="url")):
    details = await run_in_threadpool(scraper.get_show_details, url)
    
    if not details:
        raise HTTPException(status_code=404, detail="Show not found")

    seasons_data = []
    if "seasons" in details:
        for s in details["seasons"]:
            season_obj = Season(
                season_number=s["season_number"],
                display_label=s["display_label"],
                poster=s.get("poster"),
                episodes=[] 
            )
            seasons_data.append(season_obj)

    clean_title = clean_show_title(details.get("title", ""))

    return ShowDetails(
        title=clean_title,
        original_title=details.get("title"),
        url=details["url"],
        type=details["type"],
        bg_poster=details.get("poster"),
        description=details.get("synopsis"),
        rating=details.get("imdb_rating"),
        year=details.get("year"),
        genres=details.get("genres", []),
        trailer=details.get("trailer"),
        seasons=seasons_data,
        servers=details.get("servers", [])
    )

@app.get("/season/episodes", response_model=List[Episode])
async def get_season_episodes(
    url: str = Query(...)
):
    season_dummy = {"url": url}
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
            servers=[]
        ) for ep in episodes
    ]

@app.get("/stream/resolve", response_model=StreamSource)
async def resolve_stream(
    url: str = Query(..., description="Episode/Movie watch URL")
):
    content_dummy = {"url": url}

    servers = await run_in_threadpool(scraper.fetch_episode_servers, content_dummy)

    if not servers:
        raise HTTPException(status_code=404, detail="No working VidTube servers found")

    selected = servers[0]
    video_url = selected.get("video_url")
    if not video_url:
        raise HTTPException(status_code=404, detail="Could not extract a direct video URL")

    referer = getattr(video_url, "referer", None) or selected.get("embed_url") or scraper.base_url
    ua = getattr(video_url, "headers", {}).get("User-Agent") or scraper.session.headers.get("User-Agent", "Mozilla/5.0")

    is_vidtube = any(k in str(video_url).lower() for k in ["cdn-video", "serv-stream", "vidtube"]) or \
                 bool(referer and any(k in str(referer).lower() for k in ["vidtube", "cdn-video"]))

    if is_vidtube:
        if not referer or not any(k in str(referer).lower() for k in ["vidtube", "down.vidtube.one"]):
            referer = "https://down.vidtube.one/"
        if not ua:
            ua = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
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
    # Copy-pasteable shell command, properly quoted/escaped.
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
async def extract_direct(url: str):
    video_url = await run_in_threadpool(scraper.vidtube_processor.extract, url)
    if not video_url:
        raise HTTPException(404, "Could not extract video")
    return {"video_url": video_url}

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
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
