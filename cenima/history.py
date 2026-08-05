"""History & Watch State Persistence Engine for cenima-cli."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import CONFIG_DIR, HISTORY_FILE, COMPLETED_THRESHOLD
from .models import WatchProgress, WatchStatus


class HistoryManager:
    """Manages persistent watch states and history for movies and TV episodes."""

    def __init__(self, history_file: Optional[Path] = None):
        self.history_file = history_file or HISTORY_FILE
        self._lock = threading.Lock()
        self._history: Dict[str, dict] = {}
        self.load()

    def _normalize_title(self, title: str) -> str:
        if not title:
            return "Unknown"
        return " ".join(title.strip().lower().split())

    def make_key(
        self,
        show_title: str,
        season_number: Optional[int] = None,
        episode_number: Optional[str] = None,
    ) -> str:
        norm_title = self._normalize_title(show_title)
        if season_number is not None or episode_number is not None:
            s_str = f"s{season_number}" if season_number is not None else "s?"
            e_str = f"e{episode_number}" if episode_number is not None else "e?"
            return f"{norm_title}::{s_str}::{e_str}"
        return f"{norm_title}::movie"

    def load(self) -> None:
        with self._lock:
            try:
                if self.history_file.exists():
                    raw = json.loads(self.history_file.read_text(encoding="utf-8"))
                    if isinstance(raw, dict):
                        self._history = raw
                    else:
                        self._history = {}
                else:
                    self._history = {}
            except Exception:
                self._history = {}

    def save(self) -> None:
        with self._lock:
            try:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                self.history_file.write_text(
                    json.dumps(self._history, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
            except Exception:
                pass

    def get_progress(
        self,
        show_title: str,
        season_number: Optional[int] = None,
        episode_number: Optional[str] = None,
    ) -> Optional[WatchProgress]:
        key = self.make_key(show_title, season_number, episode_number)
        with self._lock:
            data = self._history.get(key)
            if data:
                try:
                    return WatchProgress(**data)
                except Exception:
                    pass
        return None

    def update_progress(
        self,
        show_title: str,
        season_number: Optional[int] = None,
        episode_number: Optional[str] = None,
        time_pos: float = 0.0,
        duration: float = 0.0,
        show_url: Optional[str] = None,
        content_type: str = "movie",
        season_label: Optional[str] = None,
        episode_title: Optional[str] = None,
        episode_url: Optional[str] = None,
    ) -> WatchProgress:
        key = self.make_key(show_title, season_number, episode_number)

        pct = 0.0
        if duration > 0:
            pct = round((time_pos / duration) * 100.0, 1)

        status = WatchStatus.UNWATCHED
        if pct >= (COMPLETED_THRESHOLD * 100.0) or (duration > 0 and (duration - time_pos) <= 60):
            status = WatchStatus.COMPLETED
        elif pct >= 3.0 or time_pos >= 30.0:
            status = WatchStatus.IN_PROGRESS

        prog = WatchProgress(
            show_title=show_title,
            show_url=show_url,
            content_type=content_type,
            season_number=season_number,
            season_label=season_label,
            episode_number=episode_number,
            episode_title=episode_title,
            episode_url=episode_url,
            time_pos=round(time_pos, 1),
            duration=round(duration, 1),
            progress_pct=pct,
            status=status,
            last_watched=time.time(),
        )

        with self._lock:
            self._history[key] = prog.model_dump()
        self.save()
        return prog

    def mark_watched(
        self,
        show_title: str,
        season_number: Optional[int] = None,
        episode_number: Optional[str] = None,
        show_url: Optional[str] = None,
        content_type: str = "movie",
        season_label: Optional[str] = None,
        episode_title: Optional[str] = None,
        episode_url: Optional[str] = None,
    ) -> WatchProgress:
        return self.update_progress(
            show_title=show_title,
            season_number=season_number,
            episode_number=episode_number,
            time_pos=100.0,
            duration=100.0,
            show_url=show_url,
            content_type=content_type,
            season_label=season_label,
            episode_title=episode_title,
            episode_url=episode_url,
        )

    def get_season_stats(self, show_title: str, season_number: int) -> Tuple[int, List[str]]:
        """Return (watched_count, list_of_watched_episode_numbers) for a season."""
        norm_title = self._normalize_title(show_title)
        prefix = f"{norm_title}::s{season_number}::"
        watched_eps = []
        with self._lock:
            for key, data in self._history.items():
                if key.startswith(prefix):
                    st = data.get("status")
                    if st == WatchStatus.COMPLETED or data.get("progress_pct", 0) >= (COMPLETED_THRESHOLD * 100):
                        ep_num = data.get("episode_number")
                        if ep_num:
                            watched_eps.append(str(ep_num))
        return len(watched_eps), watched_eps

    def get_recent_history(self, limit: int = 20) -> List[WatchProgress]:
        items: List[WatchProgress] = []
        with self._lock:
            for key, data in self._history.items():
                try:
                    items.append(WatchProgress(**data))
                except Exception:
                    pass
        items.sort(key=lambda x: x.last_watched, reverse=True)
        return items[:limit]

    def clear_history(self) -> None:
        with self._lock:
            self._history = {}
        self.save()
