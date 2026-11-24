"""History & Watch State Persistence Engine for cenima-cli."""
from __future__ import annotations

import json
import logging
import os
import tempfile
import warnings
import threading
import time
import re
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .config import HISTORY_FILE, COMPLETED_THRESHOLD
from .models import WatchProgress, WatchStatus, content_identity, canonical_content_url

logger = logging.getLogger(__name__)


class HistoryManager:
    """Manages persistent watch states and history for movies and TV episodes."""

    def __init__(self, history_file: Optional[Path] = None):
        self.history_file = history_file or HISTORY_FILE
        self._lock = threading.Lock()
        self._history: Dict[str, dict] = {}
        self.load()

    @contextmanager
    def _file_lock(self):
        """Lock a persistent sidecar, since atomic replacement changes file handles."""
        self.history_file.parent.mkdir(parents=True, exist_ok=True)
        with self.history_file.with_suffix(self.history_file.suffix + ".lock").open("a+b") as handle:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            deadline = time.monotonic() + 10
            while True:
                try:
                    handle.seek(0)
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Watch history is busy; try again")
                    time.sleep(0.02)
            try:
                yield
            finally:
                handle.seek(0)
                if os.name == "nt":
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _part(part, label=None):
        match = re.search(r"\bPart\s+\d+", label or "", re.IGNORECASE)
        return part or (match.group() if match else None)

    @contextmanager
    def _transaction(self):
        with self._lock, self._file_lock():
            try:
                original = self.history_file.read_bytes()
            except FileNotFoundError:
                original = b"{}"
            raw = json.loads(original.decode("utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("Invalid history file; refusing to overwrite it")
            migrated = {}
            changed = False
            for key, value in raw.items():
                try:
                    progress = WatchProgress(**value)
                    part = self._part(progress.season_part, progress.season_label)
                    identity = progress.content_id or content_identity(
                        progress.show_url, progress.season_number, progress.episode_number,
                        part, progress.content_type)
                except (ValueError, TypeError):
                    migrated[key] = value
                    continue
                if identity:
                    progress.content_id = identity
                    progress.season_part = part
                    value = {**value, **progress.model_dump(mode="json")}
                    changed |= key != identity or raw[key] != value
                    key = identity
                # Duplicate legacy aliases of one identity keep the newest snapshot.
                if key not in migrated or value.get("last_watched", 0) >= migrated[key].get("last_watched", 0):
                    migrated[key] = value
            self._history = migrated
            if changed:
                backup = self.history_file.with_suffix(self.history_file.suffix + ".legacy.bak")
                if not backup.exists():
                    backup.write_bytes(original)
                self._write_locked()
            yield

    def _write_locked(self):
        from .atomic import atomic_write_json
        atomic_write_json(self.history_file, self._history)

    def _normalize_title(self, title: str) -> str:
        if not title:
            return "Unknown"
        return " ".join(title.strip().lower().split())

    def make_key(
        self,
        show_title: str,
        season_number: Optional[int] = None,
        episode_number: Optional[str] = None,
        *, show_url=None, season_part=None, content_id=None, content_type="movie",
    ) -> str:
        identity = content_id or content_identity(show_url, season_number, episode_number,
                                                   season_part, content_type)
        if identity:
            return identity
        norm_title = self._normalize_title(show_title)
        if season_number is not None or episode_number is not None:
            s_str = f"s{season_number}" if season_number is not None else "s?"
            e_str = f"e{episode_number}" if episode_number is not None else "e?"
            return f"{norm_title}::{s_str}::{e_str}" + (f"::{season_part.casefold()}" if season_part else "")
        return f"{norm_title}::movie"

    def load(self) -> None:
        try:
            with self._transaction():
                pass
        except (OSError, ValueError) as exc:
            logger.warning("Failed to load watch history from %s: %s", self.history_file, exc)
            warnings.warn(f"Could not read watch history: {exc}", RuntimeWarning)

    def save(self) -> None:
        """Refresh before saving; mutations commit inside their own transaction."""
        with self._transaction():
            self._write_locked()

    def get_progress(
        self,
        show_title: str,
        season_number: Optional[int] = None,
        episode_number: Optional[str] = None,
        *, show_url=None, season_part=None, content_id=None, content_type="movie",
    ) -> Optional[WatchProgress]:
        key = self.make_key(show_title, season_number, episode_number, show_url=show_url,
                            season_part=season_part, content_id=content_id, content_type=content_type)
        with self._transaction():
            data = self._history.get(key)
            if not show_url and not content_id:
                matches = [value for value in self._history.values() if isinstance(value, dict)
                           and self._normalize_title(value.get("show_title", "")) == self._normalize_title(show_title)
                           and value.get("season_number") == season_number
                           and value.get("episode_number") == episode_number
                           and (self._part(value.get("season_part"), value.get("season_label")) or "").casefold()
                           == (season_part or "").casefold()]
                data = matches[0] if len(matches) == 1 else None
            if data:
                try:
                    return WatchProgress(**data)
                except Exception as exc:
                    logger.debug("Skipping invalid history entry %s: %s", key, exc)
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
        season_part: Optional[str] = None,
        content_id: Optional[str] = None,
    ) -> WatchProgress:
        season_part = self._part(season_part, season_label)
        key = self.make_key(show_title, season_number, episode_number, show_url=show_url,
                            season_part=season_part, content_id=content_id, content_type=content_type)

        pct = 0.0
        if duration > 0:
            pct = round((time_pos / duration) * 100.0, 1)

        status = WatchStatus.UNWATCHED
        if pct >= (COMPLETED_THRESHOLD * 100.0) or (duration > 60 and time_pos > 0 and (duration - time_pos) <= 60):
            status = WatchStatus.COMPLETED
        elif pct >= 3.0 or time_pos >= 30.0:
            status = WatchStatus.IN_PROGRESS

        prog = WatchProgress(
            content_id=key if content_id or show_url else None,
            season_part=season_part,
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

        with self._transaction():
            self._history[key] = prog.model_dump(mode="json")
            self._write_locked()
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
        season_part: Optional[str] = None,
        content_id: Optional[str] = None,
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
            season_part=season_part,
            content_id=content_id,
        )

    def get_season_stats(self, show_title: str, season_number: int,
                         *, show_url=None, season_part=None) -> Tuple[int, List[str]]:
        """Return (watched_count, list_of_watched_episode_numbers) for a season."""
        norm_title = self._normalize_title(show_title)
        watched_eps = []
        with self._transaction():
            for key, data in self._history.items():
                if not isinstance(data, dict):
                    continue
                same_show = (canonical_content_url(data.get("show_url") or "") == canonical_content_url(show_url)
                             if show_url else self._normalize_title(data.get("show_title", "")) == norm_title)
                part = self._part(data.get("season_part"), data.get("season_label"))
                if same_show and data.get("season_number") == season_number and (part or "").casefold() == (season_part or "").casefold():
                    st = data.get("status")
                    if st == WatchStatus.COMPLETED or data.get("progress_pct", 0) >= (COMPLETED_THRESHOLD * 100):
                        ep_num = data.get("episode_number")
                        if ep_num:
                            watched_eps.append(str(ep_num))
        return len(watched_eps), watched_eps

    def get_recent_history(self, limit: int = 20) -> List[WatchProgress]:
        items: List[WatchProgress] = []
        try:
            with self._transaction():
                for key, data in self._history.items():
                    try:
                        items.append(WatchProgress(**data))
                    except Exception as exc:
                        logger.debug("Skipping invalid history entry %s: %s", key, exc)
        except (OSError, ValueError) as exc:
            logger.warning("Failed to read watch history from %s: %s", self.history_file, exc)
            return []
        items.sort(key=lambda x: x.last_watched, reverse=True)
        return items[:limit]

    def clear_history(self) -> None:
        with self._transaction():
            self._history = {}
            self._write_locked()
