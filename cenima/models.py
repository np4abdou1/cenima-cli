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
