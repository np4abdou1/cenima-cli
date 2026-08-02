from pydantic import BaseModel
from typing import List, Optional, Any, Dict
from urllib.parse import urljoin
import re

# Import existing scraper and processor
from .scraper import TopCinemaScraper, clean_arabic_title, clean_show_title
from .processor import VidTubeProcessor

scraper = TopCinemaScraper()

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
        version="1.0.0"
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

def clean_show_title(title: str) -> str:
    cleaned = clean_arabic_title(title)
    
    if cleaned.lower() == 'topcinema':
        return ""

    junk_patterns = [
        r'\b(?:1080p|720p|480p|360p)\b',
        r'\b(?:WEB-DL|BluRay|HDTV|CAM)\b',
        r'\b(?:x264|x265|HEVC)\b',
        r'\b(?:\d{1,2}\.\d)\b',
        r'[★⭐]\s*\d+\.?\d*',
        r'\[\s*\d+\.?\d*\s*\]',
        r'\b(?:Season|الموسم)\s*\d+',
        r'\b(?:Episode|الحلقة)\s*\d+',
    ]
    
    for pattern in junk_patterns:
        cleaned = re.sub(pattern, '', cleaned, flags=re.IGNORECASE)
    
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    return cleaned

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
    referer = scraper.base_url
    ua = scraper.session.headers["User-Agent"]
    mpv_cmd = f'mpv "{selected["video_url"]}" --referrer="{referer}" --user-agent="{ua}" --vo=gpu --x11-bypass-compositor=no'

    return StreamSource(
        server_number=selected["server_number"],
        embed_url=selected["embed_url"],
        video_url=selected["video_url"],
        headers={"Referer": referer, "User-Agent": ua},
        mpv_command=mpv_cmd
    )

@app.get("/vidtube/extract")
async def extract_direct(url: str):
    video_url = await run_in_threadpool(scraper.vidtube_processor.extract, url)
    if not video_url:
        raise HTTPException(404, "Could not extract video")
    return {"video_url": video_url}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
