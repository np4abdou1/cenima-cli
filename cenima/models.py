from pydantic import BaseModel, Field, HttpUrl
from typing import List, Optional, Dict, Any
from enum import Enum

class ContentType(str, Enum):
    MOVIE = "movie"
    SERIES = "series"
    ANIME = "anime"
    UNKNOWN = "unknown"

class Metadata(BaseModel):
    year: Optional[int] = None
    rating: Optional[float] = None
    imdb_rating: Optional[float] = None
    quality: Optional[str] = None
    poster: Optional[str] = None
    genres: List[str] = Field(default_factory=list)
    synopsis: Optional[str] = None
    cast: List[str] = Field(default_factory=list)
    directors: List[str] = Field(default_factory=list)
    duration: Optional[str] = None
    language: Optional[str] = None
    country: Optional[str] = None

class StreamLink(BaseModel):
    name: str
    server_number: int
    embed_url: str
    video_url: Optional[str] = None

class Episode(BaseModel):
    episode_number: str
    display_number: str
    title: str = ""
    url: str
    is_special: bool = False
    servers: List[StreamLink] = Field(default_factory=list)

class Season(BaseModel):
    season_number: int
    season_part: Optional[str] = None
    display_label: str
    url: str
    poster: Optional[str] = None
    episodes: List[Episode] = Field(default_factory=list)

class Show(BaseModel):
    title: str
    url: str
    type: ContentType
    metadata: Metadata = Field(default_factory=Metadata)
    seasons: List[Season] = Field(default_factory=list)
    servers: List[StreamLink] = Field(default_factory=list) # For movies


class SearchResult(BaseModel):
    title: str = ""
    original_title: str = ""
    url: str
    type: str = "unknown"
    year: Optional[int] = None
    rating: Optional[float] = None
    poster: Optional[str] = None
    quality: Optional[str] = None


class ShowDetails(BaseModel):
    title: str = ""
    original_title: str = ""
    url: str
    type: str = "unknown"
    bg_poster: Optional[str] = None
    description: Optional[str] = None
    rating: Optional[float] = None
    year: Optional[int] = None
    genres: List[str] = Field(default_factory=list)
    trailer: Optional[str] = None
    seasons: List[Season] = Field(default_factory=list)
    servers: List[StreamLink] = Field(default_factory=list)


class StreamSource(BaseModel):
    """Resolved stream with machine-ready mpv launch data."""

    server_number: Optional[int] = None
    embed_url: Optional[str] = None
    video_url: str = ""
    headers: Dict[str, str] = Field(default_factory=dict)
    quality: Optional[str] = None
    mpv_command: str = ""
    mpv_arguments: List[str] = Field(default_factory=list)


class WatchStatus(str, Enum):
    UNWATCHED = "unwatched"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class WatchProgress(BaseModel):
    show_title: str
    show_url: Optional[str] = None
    content_type: str = "movie"
    season_number: Optional[int] = None
    season_label: Optional[str] = None
    episode_number: Optional[str] = None
    episode_title: Optional[str] = None
    episode_url: Optional[str] = None
    time_pos: float = 0.0
    duration: float = 0.0
    progress_pct: float = 0.0
    status: WatchStatus = WatchStatus.UNWATCHED
    last_watched: float = 0.0


class HistoryItem(BaseModel):
    key: str
    progress: WatchProgress


class HistoryResponse(BaseModel):
    items: List[WatchProgress] = Field(default_factory=list)
    total: int = 0

