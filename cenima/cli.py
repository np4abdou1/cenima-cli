#!/usr/bin/env python3
import sys
import os
import re
import json
import shutil
import asyncio
import argparse
import textwrap
import time
from difflib import SequenceMatcher
from pathlib import Path
from contextlib import nullcontext
from typing import List, Any, Optional, Dict, Union
from rich.console import Console, Group
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table
from rich.markup import escape
from rich.panel import Panel
from rich import box

from .scraper import TopCinemaScraper, clean_show_title
from .history import HistoryManager
from .models import WatchStatus, NavigationOutcome, PlaybackOutcome, canonical_content_url
from .mpv import run_mpv_monitored
from .picker import RichPicker, CustomPrompt, PickerAction
from .accent import get_auto_theme
from .ui import (theme_tokens, landing, app_bar, breadcrumb, callout,
                 details_card, result_row, PickerRow)
from .health import ServerHealthChecker, LIVE, SLOW, OFFLINE
from .processor import ServerProcessor

class _LazyScraper:
    def __init__(self):
        self._instance = None
        self.base_url = ''

    def _get_instance(self):
        if self._instance is None:
            from .provider import ProviderFallback
            self._instance = ProviderFallback()
            self.base_url = self._instance.base_url
        return self._instance

    def __getattr__(self, name):
        return getattr(self._get_instance(), name)

scraper = _LazyScraper()
from .config import (

    __version__, __author__, __license__,
    CONFIG_DIR, CONFIG_FILE,
    THEMES, DEFAULT_THEME
)

CONSOLE = Console()


def download_engine_labels(manager_keys):
    labels = {
        'builtin': 'Built-in downloader (recommended)',
        'aria2c': 'aria2c for direct files',
        'idm': 'Internet Download Manager',
    }
    return [labels.get(key, key) for key in manager_keys]


def configure_utf8_terminal():
    for stream in (getattr(sys, 'stdout', None), getattr(sys, 'stderr', None)):
        reconfigure = getattr(stream, 'reconfigure', None)
        if reconfigure:
            try:
                reconfigure(encoding='utf-8', errors='replace')
            except (AttributeError, OSError):
                pass
    if os.name == 'nt':
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleOutputCP(65001)
            ctypes.windll.kernel32.SetConsoleCP(65001)
        except (AttributeError, OSError):
            pass
    os.environ.setdefault('PYTHONIOENCODING', 'utf-8')


def format_seconds(secs: float) -> str:
    if not secs or secs < 0:
        return "00:00"
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


class Config:
    
    def __init__(self):
        self.theme = DEFAULT_THEME
        # Player options
        self.simple_mode = True   # Launch mpv minimally (mirrors the known-good command)
        self.send_headers = False # Inject --referrer/--user-agent when simple_mode is on
        self.referer = ""
        self.user_agent = ""
        self.fullscreen = True
        self.hwdec = False
        self.cache_pause = False
        self.download_dir = str(Path.home() / "Downloads" / "cenima-cli")
        self.download_quality = "best"
        self.download_speed = "unlimited"
        self.subtitle_language = "ar"
        self.notifications = False
        self.user_name = ""
        self.welcome_theme = "midnight"
        self.welcome_motion = False
        self.load()
    
    def load(self):
        try:
            if CONFIG_FILE.exists():
                try:
                    data = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
                    if not isinstance(data, dict):
                        raise ValueError('Config root is not an object')
                except (json.JSONDecodeError, ValueError, OSError):
                    backup = CONFIG_FILE.with_name(CONFIG_FILE.name + '.corrupt.bak')
                    backup.write_bytes(CONFIG_FILE.read_bytes())
                    data = {}
                self.theme = data.get("theme", DEFAULT_THEME)
                self.simple_mode = bool(data.get("simple_mode", True))
                self.send_headers = bool(data.get("send_headers", False))
                self.referer = data.get("referer", "")
                self.user_agent = data.get("user_agent", "")
                self.fullscreen = bool(data.get("fullscreen", True))
                self.hwdec = bool(data.get("hwdec", False))
                self.cache_pause = bool(data.get("cache_pause", False))
                self.download_dir = data.get("download_dir") or self.download_dir
                self.download_quality = data.get("download_quality") or "best"
                self.download_speed = data.get("download_speed") or "unlimited"
                self.subtitle_language = data.get("subtitle_language") or "ar"
                self.notifications = bool(data.get("notifications", False))
                self.user_name = str(data.get("user_name", "")).strip()
                self.welcome_theme = data.get("welcome_theme", "midnight")
                self.welcome_motion = bool(data.get("welcome_motion", False))
        except Exception:
            pass
    
    def save(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            existing = {}
            if CONFIG_FILE.exists():
                existing = json.loads(CONFIG_FILE.read_text(encoding='utf-8'))
                if not isinstance(existing, dict):
                    existing = {}
            data = dict(existing)
            data.update(self.to_dict())
            from .atomic import atomic_write_json
            atomic_write_json(CONFIG_FILE, data)
            return True
        except Exception:
            return False
    
    def to_dict(self) -> Dict:
        return {
            "theme": self.theme,
            "simple_mode": self.simple_mode,
            "send_headers": self.send_headers,
            "referer": self.referer,
            "user_agent": self.user_agent,
            "fullscreen": self.fullscreen,
            "hwdec": self.hwdec,
            "cache_pause": self.cache_pause,
            "download_dir": self.download_dir,
            "download_quality": self.download_quality,
            "download_speed": self.download_speed,
            "subtitle_language": self.subtitle_language,
            "notifications": self.notifications,
            "user_name": self.user_name,
            "welcome_theme": self.welcome_theme,
            "welcome_motion": self.welcome_motion,
        }
    
    def get_theme(self) -> Dict:
        if self.theme == "auto":
            if not getattr(self, "_auto_theme", None):
                self._auto_theme = get_auto_theme()
            return theme_tokens(self._auto_theme)
        return theme_tokens(THEMES.get(self.theme, THEMES["cinema"]))


class CinemaCLI:
    def __init__(self):
        self.config = Config()
        self.history = HistoryManager()
        from .watch_later import WatchLaterStore
        self.watch_later = WatchLaterStore()
        self.picker_hint = "↑↓ navigate • Enter select • Esc / b back"
        self.current_show_title = ""
        self.current_overview_panel = None
        self.current_episodes = []
        self.current_episode_index = 0
        self.current_season = None
        self.check_deps()

    @property
    def media_store(self):
        if not hasattr(self, '_media_store'):
            from .library import MediaStore
            self._media_store = MediaStore(CONFIG_DIR / 'media.sqlite3')
        return self._media_store

    @property
    def download_worker(self):
        if not hasattr(self, '_download_worker'):
            from .worker import DownloadWorker
            from .notifications import notify
            self._download_worker = DownloadWorker(
                self.media_store, TopCinemaScraper,
                notifications_enabled=bool(getattr(self.config, 'notifications', False)),
                notify=notify)
        return self._download_worker

    @property
    def theme(self) -> Dict:
        return theme_tokens(self.config.get_theme())
    
    def check_deps(self):
        missing = []
        if not shutil.which("mpv"):
            missing.append("mpv")
        
        if missing:
            CONSOLE.print(f"[bold red]✗ Missing dependencies:[/bold red] {', '.join(missing)}")
            CONSOLE.print(callout("PLAYER REQUIRED", "Install mpv and add it to PATH, then reopen the terminal.",
                                  "warning", self.theme))
            sys.exit(1)
    
    def _set_breadcrumbs(self, *parts):
        self._breadcrumbs = ["Home"]
        query = getattr(self, "_search_query", "")
        if query:
            self._breadcrumbs.append(f'Search: "{query}"')
        if getattr(self, "current_show_title", ""):
            self._breadcrumbs.append(self.current_show_title)
        self._breadcrumbs.extend(parts)

    def log(self, emoji: str, message: str, style: str = "dim"):
        level = "info"
        if "✗" in emoji:
            level = "error"
        elif "⚠" in emoji:
            level = "warning"
        elif emoji in ("✓", "💾", "👁️"):
            level = "success"
        labels = {"info": "STATUS", "error": "ACTION REQUIRED",
                  "warning": "PLEASE CHECK", "success": "SAVED / COMPLETE"}
        try:
            message = Text.from_markup(message).plain
        except Exception:
            pass
        panel = callout(labels[level], message, level, self.theme)
        if level != "info" or emoji == "🎬":
            self._notice = panel
        self.banner()
        CONSOLE.print(panel)

    def banner(self):
        CONSOLE.clear()
        parts = getattr(self, "_breadcrumbs", ["Home"])
        if parts == ["Home"]:
            recent = self.history.get_recent_history(limit=30)
            CONSOLE.print(landing(__version__, bool(shutil.which("mpv")), recent,
                                  CONSOLE.width, self.theme))
            CONSOLE.print(Text("  /continue  Resume   /library  Offline   /downloads  Queue   /settings  Folder",
                               style=self.theme["muted"]))
            CONSOLE.print()
        else:
            CONSOLE.print(app_bar(__version__, self.theme))
            CONSOLE.print(breadcrumb(parts, self.theme, CONSOLE.width))

    def goodbye(self):
        CONSOLE.clear()
        CONSOLE.print(callout("UNTIL NEXT TIME", "Your watch history is saved. See you at the next story.",
                              "success", self.theme))

    def _show_picker_intro(self, title=None, subtitle=None):
        self.banner()
        if title:
            self._print_focus_panel(title, subtitle)

    def _print_focus_panel(self, title, subtitle=None):
        CONSOLE.print(callout(title, subtitle or "Choose an item below to continue.", theme=self.theme))

    def run_fzf(
        self,
        items: List[Any],
        prompt: str = "❯ ",
        header: Optional[str] = None,
        title: Optional[str] = None,
        subtitle: Optional[str] = None,
        initial_index: int = 0,
        notice=None,
        shortcuts=None
    ) -> Optional[Union[int, PickerAction]]:
        # Keep the historical method name for callers; Rich owns the visual surface.
        if not items:
            return None
        details = getattr(self, "_details", None)
        notice = notice or getattr(self, "_notice", None)
        self._notice = None
        def context():
            card = details_card(details, self.current_show_title, CONSOLE.width, self.theme) if details else None
            if not notice:
                return card
            combined = Group(card, notice) if card else notice
            height = len(CONSOLE.render_lines(combined, CONSOLE.options, pad=False))
            return notice if height > CONSOLE.height - 12 else combined
        picker = RichPicker(CONSOLE, self.theme,
                            breadcrumbs=getattr(self, "_breadcrumbs", ["Home"]), context=context, shortcuts=shortcuts)
        try:
            label = Text.from_markup(title or "Choose an option").plain
        except Exception:
            label = title or "Choose an option"
        return picker.pick(items, format_func=lambda item: item, title=label,
                           subtitle=subtitle or header or "", initial_index=initial_index)

    def run_fzf_objects(
        self,
        items: List[Any],
        format_func,
        prompt: str = "❯ ",
        title: Optional[str] = None,
        subtitle: Optional[str] = None,
        header: Optional[str] = None,
        initial_index: int = 0
    ) -> Optional[Any]:
        if not items:
            return None

        formatted_items = [format_func(item) for item in items]
        idx = self.run_fzf(
            formatted_items,
            prompt=prompt,
            header=header,
            title=title,
            subtitle=subtitle,
            initial_index=initial_index
        )
        if isinstance(idx, int) and 0 <= idx < len(items):
            return items[idx]
        return None
    
    async def handle_command(self, query: str) -> bool:
        cmd = query.strip().lower()
        command_aliases = {
            '/d': '/downloads', '/do': '/doctor', '/doctor': '/doctor',
            '/ds': '/downloads', '/downloads': '/downloads',
            '/le': '/last-eps', '/lasteps': '/last-eps', '/last-eps': '/last-eps',
            '/lm': '/last-mv', '/lastmv': '/last-mv', '/last-mv': '/last-mv',
            '/h': '/history', '/history': '/history', '/recent': '/history',
            '/c': '/continue', '/continue': '/continue', '/resume': '/continue',
            '/l': '/library', '/library': '/library',
            '/s': '/settings', '/settings': '/settings',
            '/w': '/watch-later', '/wl': '/watch-later', '/watch-later': '/watch-later',
            '/x': '/export-settings', '/export': '/export-settings',
            '/i': '/import-settings', '/import': '/import-settings',
            '/t': '/last-mv', '/trending': '/last-mv',
            '/theme': '/theme', '/v': '/version', '/help': '/help',
            '/f': '/folder', '/folder': '/folder',
        }
        cmd = command_aliases.get(cmd, cmd)

        if cmd == "/doctor":
            from .doctor import run_doctor
            run_doctor(CONSOLE, self.config)
            return True
        elif cmd == "/export-settings":
            from .settings_io import write_settings_export
            path = Path.home() / 'cenima-settings.json'
            write_settings_export(self.config, path)
            self.run_fzf(['Back'], title='Settings exported', subtitle=str(path))
            return True
        elif cmd == "/import-settings":
            path = CustomPrompt(CONSOLE, self.theme).ask(title='IMPORT SETTINGS', initial_value=str(Path.home() / 'cenima-settings.json'))
            if path:
                try:
                    from .settings_io import read_settings_import
                    settings = read_settings_import(path)
                    for key, value in settings.items():
                        setattr(self.config, key, value)
                    if not self.config.save():
                        raise OSError('Could not save imported settings.')
                    self.run_fzf(['Back'], title='Settings imported', subtitle='Sensitive credentials are never imported.')
                except (OSError, ValueError, json.JSONDecodeError) as exc:
                    self.run_fzf(['Back'], title='Settings import failed', subtitle=str(exc))
            return True
        elif cmd in ('/watch-later', '/watchlater'):
            await self.show_watch_later()
            return True
        elif cmd == "/theme":
            self.theme_selector()
            return True
        elif cmd == '/settings':
            self.show_settings()
            return True
        elif cmd in ('/last-eps', '/last-mv'):
            await self.show_latest('episode' if cmd == '/last-eps' else 'movie')
            return True
        elif cmd == '/folder':
            from .storage import open_media_folder
            try:
                open_media_folder(self.config.download_dir)
            except (OSError, ValueError) as exc:
                self.run_fzf(['Back'], title='Cannot open media folder', subtitle=str(exc))
            return True
        elif cmd in ("/history", "/recent"):
            await self.show_history_menu()
            return True
        elif cmd in ('/continue', '/resume'):
            recent = [p for p in self.history.get_recent_history(limit=100)
                      if p.time_pos > 0 and p.status != WatchStatus.COMPLETED]
            if recent:
                await self.resume_progress(recent[0])
            else:
                self.run_fzf(['Back'], title='No unfinished titles yet')
            return True
        elif cmd == '/library':
            self.show_library()
            return True
        elif cmd == '/downloads':
            await self.show_download_queue()
            return True
        elif cmd == "/help":
            self.show_help()
            return True
        elif cmd == "/version":
            self._breadcrumbs = ["Home", "About"]
            self._details = None
            self.banner()
            self.run_fzf(["Back to Search"], title="About Cinema", notice=callout(
                f"CINEMA CLI v{__version__}", f"By {__author__} · {__license__}", theme=self.theme))
            return True
        elif cmd == "/back":
            return True
        
        return False

    async def show_watch_later(self):
        while True:
            items = self.watch_later.list()
            if not items:
                self.run_fzf(["Back"], title="Watch Later", subtitle="No saved titles yet")
                return
            selected = self.run_fzf_objects(items, lambda item:
                PickerRow((item.get('title') or item.get('show_title') or 'Untitled',
                           item.get('episode_number') or 'Movie'),
                          ('Title', 'Episode')),
                title="Watch Later", subtitle="Enter opens saved catalog page")
            if selected is None:
                return
            choice = self.run_fzf(["Open", "Remove", "Back"], title=
                                  selected.get('title') or selected.get('show_title') or 'Watch Later')
            if choice == 0:
                await self.show_details_flow({
                    'title': selected.get('title') or selected.get('show_title'),
                    'url': selected.get('show_url') or selected.get('url'),
                    'type': selected.get('content_type') or 'movie',
                })
            elif choice == 1:
                self.watch_later.remove(selected)

    async def show_history_menu(self):
        self._breadcrumbs = ["Home", "Watch history"]
        self._details = None
        self.banner()
        theme = self.theme
        history_items = self.history.get_recent_history(limit=30)
        
        if not history_items:
            self.log("ℹ️", "No watch history yet", theme['secondary'])
            self.run_fzf(["Back to Search"], title="Watch history")
            return

        def fmt_history_item(item):
            episode = f"S{item.season_number or '?'} E{item.episode_number}" if item.episode_number else "Movie"
            status = "Watched" if item.status == WatchStatus.COMPLETED else "Continue" if item.time_pos > 0 else "Unwatched"
            progress = f"{int(item.progress_pct)}% · {format_seconds(item.time_pos)}"
            return PickerRow((item.show_title, episode, progress, status),
                             ("Title", "Episode", "Progress", "Status"))

        header = f"📊 {len(history_items)} items in history"
        selected = self.run_fzf_objects(
            history_items,
            fmt_history_item,
            prompt="🕒 History ❯ ",
            title="Continue watching",
            subtitle=header,
            header=header
        )

        if selected:
            options = [
                "Resume saved title",
                f"🔍 Search '{selected.show_title}'",
                "Remove from Watch Later" if self.watch_later.contains({
                    'show_url': selected.show_url, 'title': selected.show_title,
                    'content_type': selected.content_type,
                    'season_number': selected.season_number,
                    'episode_number': selected.episode_number,
                    'season_part': selected.season_part,
                }) else "Add to Watch Later",
                "🗑️ Clear Watch History",
                "⬅️ Back"
            ]
            choice_idx = self.run_fzf(
                options,
                prompt="❯ ",
                title=f"🕒 {selected.show_title}",
                header="Action for selected history item"
            )
            if choice_idx == 0:
                await self.resume_progress(selected)
            elif choice_idx == 1:
                await self.search_and_open(selected.show_title)
            elif choice_idx == 2:
                saved = {
                    'title': selected.show_title,
                    'show_url': selected.show_url,
                    'url': selected.episode_url or selected.show_url,
                    'content_type': selected.content_type,
                    'season_number': selected.season_number,
                    'episode_number': selected.episode_number,
                    'season_part': selected.season_part,
                }
                if self.watch_later.contains(saved):
                    self.watch_later.remove(saved)
                else:
                    self.watch_later.add(saved)
            elif choice_idx == 3:
                self.history.clear_history()
                self.log("✓", "History cleared", theme['primary'])
                time.sleep(1)

    async def show_latest(self, kind):
        from .download import media_title
        label = 'Latest added episodes' if kind == 'episode' else 'Latest added movies'
        self._search_query, self._details = '', None
        self.current_show_title = ''
        self._breadcrumbs = ['Home', label]
        operation = scraper.get_latest_episodes if kind == 'episode' else scraper.get_latest_movies
        items = await self._load_with_retry(label, operation, empty_message=f'No {label.lower()} are listed right now.')
        if not items:
            return
        index = 0
        while True:
            self._breadcrumbs, self._details, self._notice = ['Home', label], None, None
            def format_item(item):
                if kind == 'movie':
                    return result_row(item, clean_show_title(item['title']))
                season = str(item.get('season_number') or '?').zfill(2)
                episode = self._normalize_episode_number(item['episode_number']).zfill(2)
                return PickerRow((item['show_title'],
                    f'S{season}E{episode}',
                    (item.get('metadata') or {}).get('quality') or 'Unknown'), ('Title', 'Episode', 'Quality'))
            selected = self.run_fzf_objects(items, format_item, title=label,
                subtitle=('Newest added episode per show' if kind == 'episode' else 'Provider added order') +
                         ' · Enter actions · Esc / b back', initial_index=index)
            if selected is None:
                return
            index = items.index(selected)
            if kind == 'movie':
                await self.show_details_flow(selected)
                continue
            context = await self._load_with_retry('Loading episode context', scraper.get_episode_context, selected,
                                                 empty_message='Episode details are unavailable.')
            if not context:
                continue
            self.current_show_title = context['show_title']
            self.current_show_url = context['show_url']
            self.current_season = {'season_number': context.get('season_number'),
                'season_part': context.get('season_part'), 'display_label': f"Season {context.get('season_number', '?')}"}
            self.current_episodes, self.current_episode_index = [context], 0
            self._set_breadcrumbs(self.current_season['display_label'], f"Episode {context['episode_number']}")
            action = self.run_fzf(['Play', 'Download', 'Open show page', 'Back'], title=media_title(self.current_show_title,
                context.get('season_number'), context['episode_number'], context.get('season_part')))
            if action == 0:
                await self.play_episode(context, self.current_season['display_label'])
            elif action == 1:
                await self.download_items([context], self.current_season)
            elif action == 2:
                show_url = context.get('show_url')
                if not show_url or canonical_content_url(show_url) == canonical_content_url(context['url']):
                    self.run_fzf(['Back'], title='Show page unavailable',
                        subtitle='The provider did not supply a parent show link for this episode.')
                    continue
                outcome = await self.show_details_flow({'title': context['show_title'], 'url': show_url,
                                                       'type': context.get('show_type') or 'series'})
                if outcome == NavigationOutcome.SEARCH:
                    return

    def set_download_folder(self, value, imports=()):
        from .storage import media_folder, consolidate
        target = media_folder(value)
        if hasattr(self, '_download_worker'):
            self._download_worker.shutdown()
            if self._download_worker.thread and self._download_worker.thread.is_alive():
                raise RuntimeError('Downloads are still stopping. Try changing the folder again in a moment.')
        with self.media_store.worker_lock():
            previous = self.config.download_dir
            self.config.download_dir = str(target)
            if not self.config.save():
                self.config.download_dir = previous
                raise OSError('Could not save the media folder setting.')
            try:
                result = consolidate(self.media_store, target, imports)
            except BaseException:
                self.config.download_dir = previous
                self.config.save()
                raise
        return result

    def show_settings(self):
        from .storage import open_media_folder
        self._breadcrumbs, self._details = ['Home', 'Settings'], None
        while True:
            folder = Path(self.config.download_dir).expanduser().resolve()
            info = Text(str(folder), style=f"bold {self.theme['primary']} link {folder.as_uri()}")
            info.append('\n' + folder.as_uri(), style=self.theme['muted'])
            choice = self.run_fzf(['Change media folder and consolidate', 'Open media folder',
                                   'Download preferences', 'Subtitle preferences',
                                   'Completion notifications', 'Welcome theme', 'Back'],
                title='Settings', notice=Panel(info, title='SHARED MEDIA FOLDER'),
                subtitle='One folder for downloads, managers and library. Changing it pauses transfers and moves completed files.')
            if choice in (None, 6):
                return
            try:
                if choice == 1:
                    open_media_folder(folder)
                elif choice == 2:
                    quality_options = ['Best available', '1080p', '720p', '480p', '360p']
                    quality_values = ['best', '1080p', '720p', '480p', '360p']
                    selected_quality = self.run_fzf(quality_options, title='Preferred download quality',
                                                    subtitle='Unavailable quality falls back to the closest available stream')
                    if selected_quality is not None:
                        self.config.download_quality = quality_values[selected_quality]
                    speed_options = ['Unlimited', '10 MB/s', '5 MB/s', '2 MB/s']
                    speed_values = ['unlimited', '10MB/s', '5MB/s', '2MB/s']
                    selected_speed = self.run_fzf(speed_options, title='Download speed limit')
                    if selected_speed is not None:
                        self.config.download_speed = speed_values[selected_speed]
                    if not self.config.save():
                        raise OSError('Could not save download preferences.')
                elif choice == 3:
                    languages = ['Arabic', 'English', 'French', 'German', 'No preference']
                    values = ['ar', 'en', 'fr', 'de', '']
                    selected = self.run_fzf(languages, title='Preferred subtitle language')
                    if selected is not None:
                        self.config.subtitle_language = values[selected]
                        if not self.config.save():
                            raise OSError('Could not save subtitle preference.')
                elif choice == 4:
                    selected = self.run_fzf(
                        ['Enable completion notifications', 'Disable completion notifications'],
                        title='Completion notifications')
                    if selected is not None:
                        self.config.notifications = selected == 0
                        if not self.config.save():
                            raise OSError('Could not save notification preference.')
                elif choice == 5:
                    selected = self.run_fzf(
                        ['Midnight Cinema', 'Noir Gold', 'Neon Cyan'],
                        title='Welcome theme')
                    if selected is not None:
                        self.config.welcome_theme = ('midnight', 'noir-gold', 'neon-cyan')[selected]
                        if not self.config.save():
                            raise OSError('Could not save welcome theme.')
                elif choice == 0:
                    CONSOLE.clear()
                    value = CustomPrompt(CONSOLE, self.theme).ask(initial_value=str(folder), title='MEDIA FOLDER')
                    if value in ('/exit', '/continue', '/library'):
                        continue
                    with CONSOLE.status('Consolidating completed media…'):
                        result = self.set_download_folder(value)
                    self.run_fzf(['Back'], title='Media folder saved',
                        subtitle=f"{result['moved']} moved · {result['deduplicated']} identical copies consolidated",
                        notice=Panel(Text(result['folder']), title='MEDIA FOLDER'))
            except (OSError, ValueError, RuntimeError) as exc:
                self.run_fzf(['Back'], title='Folder change could not complete', notice=Panel(Text(str(exc))))

    def _restore_media_context(self, metadata):
        self._details = None
        self._search_query = ''
        self.current_overview_panel = None
        self.current_show_title = metadata['show_title']
        self.current_show_url = metadata.get('show_url') or metadata.get('catalog_url')
        self.current_episode_index = 0
        number = metadata.get('episode_number')
        self.current_season = ({'season_number': metadata.get('season_number'),
            'display_label': metadata.get('season_label'), 'season_part': metadata.get('season_part')}
            if number is not None else None)
        self.current_episodes = ([{'url': metadata.get('episode_url') or metadata.get('catalog_url'),
            'episode_number': number}] if number is not None else [])
        self._set_breadcrumbs('Continue watching' if number is None else f'Episode {number}')

    async def resume_progress(self, progress):
        metadata = progress.model_dump()
        catalog = progress.episode_url if progress.episode_number is not None else progress.show_url
        if not catalog:
            if self.run_fzf(['Search for this title', 'Back'], title='Legacy history has no catalog page') == 0:
                await self.search_and_open(progress.show_title)
            return
        metadata['catalog_url'] = catalog
        self._restore_media_context(metadata)
        servers = await self._load_with_retry('Resolving saved title', scraper.fetch_episode_servers,
                                              {'url': catalog}, empty_message='Saved title has no available servers.')
        if servers:
            self._resume_direct = True
            try:
                await self.select_and_play(servers, progress.show_title,
                    is_episode=progress.episode_number is not None, season_num=progress.season_number,
                    ep_num=progress.episode_number, ep_title=progress.episode_title)
            finally:
                self._resume_direct = False

    def show_library(self):
        self._breadcrumbs, self._details = ['Home', 'Local library'], None
        while True:
            items = self.media_store.media()
            if not items:
                self.run_fzf(['Back'], title='No managed downloads yet',
                             subtitle='Completed downloads appear here automatically.')
                return
            selected = self.run_fzf_objects(items, lambda row: PickerRow((
                row['download_title'], 'Ready' if row['available'] else 'Missing / changed'), ('Title', 'Status')),
                title='Local library', subtitle='Offline playback · Esc / b back')
            if selected is None:
                return
            if not selected['available']:
                self.run_fzf(['Back'], title='Local file is missing or has changed',
                             subtitle='Restore the original file or download the catalog item again.')
                continue
            self._restore_media_context(selected)
            self.play_video(selected['path'], title=selected['download_title'],
                season_num=selected.get('season_number'), ep_num=selected.get('episode_number'),
                ep_title=selected.get('episode_title'), content_type=selected.get('content_type') or 'movie')
            self._breadcrumbs, self._details = ['Home', 'Local library'], None

    async def show_download_queue(self):
        self._breadcrumbs, self._details = ['Home', 'Downloads'], None
        while True:
            jobs = self.media_store.jobs()
            pending = sum(job['status'] in ('pending', 'running') for job in jobs)
            failed = sum(job['status'] in ('failed', 'cancelled') for job in jobs)
            worker_error = self._download_worker.error if hasattr(self, '_download_worker') else ''
            choice = self.run_fzf(['Open live downloads', f'Start/resume queue ({pending})',
                                   f'Retry failed/cancelled ({failed})', 'Back'], title='Downloads',
                                   subtitle=worker_error or 'R: retry failed/cancelled downloads', shortcuts={'r': 2})
            if choice in (None, 3):
                return
            if choice in (1, 2):
                self.download_worker.start(retry_failed=choice == 2)
            elif jobs:
                self.show_live_downloads()
            elif choice == 0:
                self.run_fzf(['Back'], title='No queued downloads')

    def _retry_download(self, identity):
        latest = next((job for job in self.media_store.jobs() if job['id'] == identity), None)
        if latest and latest['status'] in ('failed', 'cancelled'):
            self.media_store.retry(identity)
            self.download_worker.start()
            return True
        return False

    def show_live_downloads(self):
        index = 0
        while True:
            selected = RichPicker(CONSOLE, self.theme, breadcrumbs=self._breadcrumbs,
                shortcuts={'r': 'retry'}).pick(self.media_store.jobs, format_func=self._download_row,
                    title='Live downloads', subtitle='Enter: actions · R: retry failed item · Esc / b: back',
                    initial_index=index)
            if selected is None:
                return
            shortcut = isinstance(selected, PickerAction)
            selected = selected.item if shortcut else selected
            jobs = self.media_store.jobs()
            index = next((i for i, job in enumerate(jobs) if job['id'] == selected['id']), 0)
            latest = next((job for job in jobs if job['id'] == selected['id']), selected)
            if shortcut:
                self._retry_download(latest['id'])
                continue
            status = latest['status']
            retryable = status in ('failed', 'cancelled')
            actions = (['Retry item'] + (['View error'] if latest['error'] else []) + ['Back'] if retryable else
                       ['Cancel item', 'Back'] if status in ('pending', 'running') else ['Back'])
            detail = latest['error'] or latest.get('progress') or status
            decision = self.run_fzf(actions, title=escape(latest['download_title']),
                subtitle='R: retry now' if retryable else '', shortcuts={'r': 0} if retryable else None,
                notice=Panel(Text(detail[:300]), title='Transfer details'))
            if decision == 0 and retryable:
                self._retry_download(latest['id'])
            elif decision == 0 and actions[0] == 'Cancel item':
                self.download_worker.cancel(latest['id'])
            elif decision is not None and actions[decision] == 'View error':
                lines = [part for line in latest['error'].splitlines()
                         for part in textwrap.wrap(line, width=max(20, CONSOLE.width - 8))]
                result = self.run_fzf([PickerRow((line,), ('Diagnostic',)) for line in lines] or ['No diagnostics'],
                    title='Transfer failure', subtitle='R: retry now · Enter / Esc / b: return', shortcuts={'r': 'retry'})
                if isinstance(result, PickerAction):
                    self._retry_download(latest['id'])

    @staticmethod
    def _download_row(job):
        progress = job.get('progress', '')
        match = re.search(r'(\d+(?:\.\d+)?)%', progress)
        percentage = min(100, max(0, float(match[1]))) if match else None
        if job['status'] == 'completed':
            percentage = 100
        bar = ('█' * int(percentage / 10) + '░' * (10 - int(percentage / 10)) + f' {percentage:.0f}%') if percentage is not None else '··········'
        status = {'pending': 'Queued', 'running': 'Active'}.get(job['status'], job['status'].title())
        return PickerRow((job['download_title'], status, bar, progress),
                         ('Title', 'Status', 'Progress', 'Transfer'))

    async def search_and_open(self, query: str):
        self._search_query = query
        self._breadcrumbs = ["Home", f'Search: "{query}"']
        self.banner()
        theme = self.theme
        self.log("🔄", f"Searching '{query}'...")
        results = await self._load_with_retry("Searching watch history", scraper.search, query,
                                              empty_message="No matching title found. Retry or return to history.")
        if results:
            await self.show_details_flow(results[0])
        else:
            self.log("⚠️", "No matching show found", theme['error'])
            await asyncio.sleep(1.5)
    
    def theme_selector(self):
        self._breadcrumbs = ["Home", "Appearance"]
        self._details = None
        theme_names = list(THEMES.keys())
        
        def fmt_theme(name):
            if name == "auto":
                auto_theme = get_auto_theme()
                indicator = " ★" if name == self.config.theme else ""
                return f"[{auto_theme['primary']}]● Auto Detect ({auto_theme['source']}){indicator}[/]"
            colors = THEMES[name]
            indicator = " ★" if name == self.config.theme else ""
            return f"[{colors['primary']}]● {name.title()}{indicator}[/]"
        
        idx = self.run_fzf(
            [fmt_theme(t) for t in theme_names],
            prompt="🎨 Theme ❯ ",
            title="Appearance",
            header="Current theme marked with ★"
        )
        
        if idx is not None:
            self.config.theme = theme_names[idx]
            self.config._auto_theme = None
            self.config.save()
            self.log("✓", f"Theme changed to {theme_names[idx].title()}", f"bold {self.theme['primary']}")
            time.sleep(0.5)
    
    def _hex_to_rgb(self, hex_color: str) -> str:
        hex_color = hex_color.lstrip('#')
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        return f"{r};{g};{b}"
    
    def show_help(self):
        self._breadcrumbs = ["Home", "Guide"]
        self._details = None
        self.banner()
        rows = []
        for key, action in [('/h · /history', 'Continue watching and manage history'),
                            ('/le · /last-eps', 'Newest added episode per show; play, download or open show'),
                            ('/lm · /last-mv', 'Browse latest added movies'),
                            ('/s · /settings', 'Choose the shared media folder'), ('/do · /doctor', 'Check Python and native tools'),
                            ('/f · /folder', 'Open the shared media folder'),
                            ('/c · /continue · Ctrl+R', 'Resume the latest unfinished title'),
                            ('/l · /library · Ctrl+L', 'Browse downloaded media offline'),
                            ('/d · /downloads', 'Resume pending or retry failed downloads'),
                            ('/wl · /watch-later', 'Open catalog-saved Watch Later items'),
                            ('/theme', 'Choose a color theme'), ('/version', 'Application version'),
                            ('/exit', 'Save and quit'), ('↑ / ↓ · j / k', 'Navigate picker items'),
                            ('Enter', 'Select an item'), ('/', 'Filter the current list'),
                            ('Esc / b', 'Back in menus; b stays text while filtering/typing'),
                            ('Home / End', 'First / last item'), ('PgUp / PgDn', 'Previous / next page'),
                            ('← / → · Delete', 'Edit the search query'),
                            ('↑ / ↓ in search', 'Recall previous queries'), ('Ctrl+C', 'Exit')]:
            rows.append(PickerRow((key, action), ("Command / key", "Action")))
        self.run_fzf(rows, title="Quick guide", subtitle="Esc / b returns to search")

    async def search_flow(self):
        while True:
            self._breadcrumbs = ["Home"]
            self._details = None
            self._notice = None
            theme = self.theme

            recent = self.history.get_recent_history(limit=30)
            def home_context():
                unfinished = next((p for p in recent if p.time_pos > 0 and p.status != WatchStatus.COMPLETED), None)
                header = landing(__version__, bool(shutil.which('mpv')), recent,
                                  CONSOLE.width, theme)
                if unfinished:
                    elapsed = int(unfinished.time_pos)
                    resume_text = Text()
                    resume_text.append("▶ ", style=theme['primary'])
                    resume_text.append("Continue  ", style=f"bold {theme['text']}")
                    resume_text.append(unfinished.show_title, style=theme['text'])
                    resume_text.append(" · ", style=theme['border'])
                    resume_text.append(format_seconds(elapsed), style=theme['success'])
                    resume_text.append(" · ", style=theme['border'])
                    resume_text.append("press /c", style=theme['muted'])
                    if unfinished.duration > 0:
                        pct = max(0.0, min(100.0, unfinished.progress_pct))
                        width = min(24, max(8, CONSOLE.width - 12))
                        filled = int(round(width * pct / 100.0))
                        resume_text.append("\n")
                        resume_text.append("━" * filled + "─" * max(0, width - filled),
                                           style=theme['success'])
                    resume = Panel(resume_text, box=box.ROUNDED,
                                   border_style=theme['border'], padding=(0, 1))
                else:
                    resume = Text("Search for your next title",
                                  style=theme['muted'], no_wrap=True, overflow='ellipsis')
                return Group(header, resume)

            CONSOLE.clear()
            custom_prompt = CustomPrompt(CONSOLE, theme, context=home_context)
            
            if not hasattr(self, 'search_history'):
                self.search_history = []
                
            query = custom_prompt.ask(f"[{theme['primary']}]>[/{theme['primary']}]",
                                      history=self.search_history,
                                      title='SEARCH',
                                      placeholder='Search movies & series…',
                                      hint='Enter to search · /h history · Q quit',
                                      idle_refresh=bool(getattr(self.config, 'welcome_motion', False)),
                                      welcome_actions=[
                                          {'key': '/', 'command': ''},
                                          {'key': 'l', 'command': '/library'},
                                          {'key': 'h', 'command': '/history'},
                                          {'key': 'c', 'command': '/continue'},
                                          {'key': 'w', 'command': '/watch-later'},
                                          {'key': 'd', 'command': '/downloads'},
                                          {'key': 's', 'command': '/settings'},
                                          {'key': 'o', 'command': '/doctor'},
                                          {'key': 'q', 'command': '/exit'},
                                      ])
            
            if not query:
                continue
                
            if not query.startswith('/'):
                if not self.search_history or self.search_history[-1] != query:
                    self.search_history.append(query)
            
            if query.lower() in ('exit', 'quit', '/exit', '/quit'):
                self.goodbye()
                sys.exit(0)
            
            if query.startswith('/'):
                if await self.handle_command(query):
                    continue
            
            self._search_query = query
            self._breadcrumbs = ["Home", f'Search: "{query}"']
            
            results = await self._load_with_retry(
                "Searching", scraper.search, query,
                empty_message="No matches found. Retry or go back to edit your search.")
            if not results:
                continue
            
            def get_relevance_score(result, search_query):
                title = clean_show_title(result['title']).lower()
                query_lower = search_query.lower()
                norm_title = re.sub(r'[\W_]+', ' ', title).strip()
                norm_query = re.sub(r'[\W_]+', ' ', query_lower).strip()
                rating = result.get('metadata', {}).get('rating') or result.get('metadata', {}).get('imdb_rating') or 0
                similarity = SequenceMatcher(None, norm_title, norm_query).ratio()
                
                if norm_title == norm_query or title == query_lower:
                    return (0, -rating)
                elif norm_title.startswith(norm_query) or title.startswith(query_lower):
                    return (1, -rating)
                elif norm_query in norm_title or query_lower in title:
                    return (2, -rating)
                elif similarity >= 0.72:
                    return (3, -similarity)
                else:
                    return (4, -similarity)
            
            results.sort(key=lambda r: get_relevance_score(r, query))
            
            def fmt_res(result):
                return result_row(result, clean_show_title(result.get("title", "")))

            results_count = len(results)
            result_header = f"📊 {results_count} {'match' if results_count == 1 else 'matches'}"
            
            selected_show = self.run_fzf_objects(
                results,
                fmt_res,
                prompt="🎯 Select ❯ ",
                title="Search results",
                header=result_header
            )
            
            if not selected_show:
                continue
            
            await self.show_details_flow(selected_show)
    
    async def show_details_flow(self, show):
        self.banner()
        theme = self.theme
        
        self.log("🔄", "Loading details...")
        
        details = await self._load_with_retry("Loading details", scraper.get_show_details, show["url"],
                                              empty_message="Show details unavailable.")
        if not details:
            return NavigationOutcome.BACK

        self._display_show_overview(details)
        if getattr(self, "current_show_url", None) != show["url"]:
            self.current_season = None
            self.current_episodes = []
            self.current_episode_index = 0
        self.current_show_url = show["url"]
        show_type = details.get('type')
        if show_type == 'movie':
            return await self.handle_movie(details)
        else:
            return await self.handle_series(details)
    
    async def handle_movie(self, details):
        watch_item = {'title': details.get('title'), 'show_title': self.current_show_title,
                      'show_url': details.get('url') or getattr(self, 'current_show_url', None),
                      'url': details.get('url') or getattr(self, 'current_show_url', None),
                      'content_type': 'movie'}
        saved = getattr(self, 'watch_later', None) and self.watch_later.contains(watch_item)
        action = self.run_fzf(["Play", "Download",
                               "Remove from Watch Later" if saved else "Add to Watch Later", "Back"],
                              title=self.current_show_title)
        if action in (None, 3):
            return NavigationOutcome.BACK
        if action == 1:
            await self.download_items([details])
            return NavigationOutcome.BACK
        if action == 2:
            if saved:
                self.watch_later.remove(watch_item)
            else:
                self.watch_later.add(watch_item)
            return NavigationOutcome.BACK
        servers = details.get('servers', [])
        
        if not servers:
            servers = await self._load_with_retry(
                "Loading servers", scraper.fetch_episode_servers, details,
                empty_message="No streaming servers available for this movie.")
            if not servers:
                return NavigationOutcome.BACK
        
        while True:
            try:
                result = await self.select_and_play(servers, self.current_show_title)
            except Exception as exc:
                self.log("⚠", escape(f"Playback failed: {exc}"), self.theme["error"])
                result = PlaybackOutcome.FAILED
            if result != PlaybackOutcome.FAILED:
                return NavigationOutcome.BACK
            if self.run_fzf(["Retry playback", "Back"], title="Playback unavailable") != 0:
                return NavigationOutcome.BACK
    
    def _episode_sort_key(self, episode):
        num = episode.get('episode_number') or episode.get('display_number')
        try:
            return float(str(num).strip())
        except Exception:
            return float('inf')
    
    def _normalize_episode_number(self, value):
        if value is None:
            return "?"
        try:
            num = float(str(value).strip())
            if num.is_integer():
                return str(int(num))
            return str(num).rstrip('0').rstrip('.')
        except Exception:
            return str(value).strip() or "?"
    
    def _clean_episode_title(self, title, num_label):
        if not title:
            return ""
        t = title.strip()
        # Clean quality, resolution, ratings, and common metadata tags
        junk_patterns = [
            r'\b(?:1080p|720p|480p|360p)\b',
            r'\b(?:WEB-DL|BluRay|HDTV|CAM)\b',
            r'\b(?:x264|x265|HEVC)\b',
            r'\b\d+\.\d*\b',
            r'\b\.\d+\b',
            r'\b\d+\.\b',
            r'[★⭐]\s*\d+\.?\d*',
            r'\[\s*\d+\.?\d*\s*\]',
            r'\b(?:Season|الموسم)\s*\d+',
            r'\b(?:Episode|الحلقة)\s*\d+',
        ]
        for pat in junk_patterns:
            t = re.sub(pat, '', t, flags=re.IGNORECASE)
        patterns = [
            rf'^(?:Episode\s+)?0*{re.escape(str(num_label))}\s*[\.\-:]\s*',
            r'^[\s\.\-:]*\d+\s*[\.\-:]\s*'
        ]
        for pat in patterns:
            t = re.sub(pat, '', t, flags=re.IGNORECASE)
        if hasattr(self, 'current_show_title') and self.current_show_title:
            t = re.sub(re.escape(self.current_show_title), '', t, flags=re.IGNORECASE)
        return t.strip(' .-_:')
    
    def _format_episode_label(self, episode):
        num_raw = episode.get('display_number') or episode.get('episode_number')
        num_label = self._normalize_episode_number(num_raw)
        
        season_num = self.current_season.get('season_number') if self.current_season else None
        prog = self.history.get_progress(
            self.current_show_title, season_num,
            self._normalize_episode_number(episode.get('episode_number') or episode.get('display_number')),
            show_url=getattr(self, "current_show_url", None),
            season_part=(self.current_season or {}).get("season_part"))

        status = "Unwatched"
        progress = "—"
        if prog:
            status = "Watched" if prog.status == WatchStatus.COMPLETED else "Continue"
            progress = f"{int(prog.progress_pct)}% · {format_seconds(prog.time_pos)}"
        return PickerRow((f"Episode {num_label}", progress, status), ("Episode", "Progress", "Status"))

    def _display_show_overview(self, details):
        title = clean_show_title(details.get("title", ""))
        if not title:
            title = details.get("title", "Untitled")
        
        self.current_show_title = title
        self._details = details
        self._set_breadcrumbs()
        self.current_overview_panel = details_card(details, title, CONSOLE.width, self.theme)

    async def _load_with_retry(self, label, operation, *args, empty_message=None):
        """Retry a recoverable operation without losing the current screen."""
        while True:
            error = None
            try:
                self.banner()
                with CONSOLE.status(Text(label + " …", style=theme_tokens(self.theme)["primary"]), spinner="dots"):
                    result = await asyncio.to_thread(operation, *args)
                if result:
                    return result
            except Exception as exc:
                error = str(exc)
            message = f"{label}: {error}" if error else (empty_message or "Nothing available.")
            notice = callout("CONNECTION FAILED" if error else "NOTHING HERE YET", message,
                             "error" if error else "warning", self.theme)
            if self.run_fzf(["Retry", "Back"], title=label, notice=notice,
                            header="Retry this step or return to the previous screen") != 0:
                return None

    async def handle_series(self, details):
        self._set_breadcrumbs()
        seasons = details.get("seasons", [])
        if not seasons:
            self.run_fzf(["Back"], title="No seasons available")
            return NavigationOutcome.BACK
        season_index = 0
        while True:
            self._set_breadcrumbs()
            if len(seasons) == 1:
                selected = seasons[0]
            else:
                selected = self.run_fzf_objects(
                    seasons, lambda season: escape(season.get("display_label") or
                        f"Season {season.get('season_number', '?')}"),
                    title=escape(self.current_show_title), header="Select a season · Esc / b: back",
                    initial_index=season_index)
                if selected is None:
                    return NavigationOutcome.BACK
                season_index = seasons.index(selected)
            outcome = await self.handle_season_episodes(selected, seasons)
            if outcome == NavigationOutcome.SEARCH:
                return outcome
            if len(seasons) == 1:
                return NavigationOutcome.BACK

    async def handle_season_episodes(self, selected_season, all_seasons=None):
        season_label = selected_season.get("display_label") or f"Season {selected_season.get('season_number', '?')}"
        self._set_breadcrumbs(season_label)
        episodes = await self._load_with_retry(
            "Loading episodes", scraper.fetch_season_episodes, selected_season,
            empty_message=f"No episodes available for {season_label}.")
        if not episodes:
            return NavigationOutcome.BACK
        episodes = sorted(episodes, key=self._episode_sort_key)
        previous_url = None
        if self.current_season and self.current_season.get("url") == selected_season.get("url") and self.current_episodes:
            previous_url = self.current_episodes[self.current_episode_index].get("url")
        self.current_season = selected_season
        self.current_episodes = episodes
        self.current_episode_index = next(
            (i for i, episode in enumerate(episodes) if episode.get("url") == previous_url), 0)
        while True:
            self._set_breadcrumbs(season_label)
            action = self.run_fzf(["Play an episode", "Download episodes", "Back"],
                                  title=season_label)
            if action in (None, 2):
                return NavigationOutcome.BACK
            if action == 1:
                self.banner()
                chosen = RichPicker(CONSOLE, self.theme,
                    breadcrumbs=getattr(self, "_breadcrumbs", ["Home"])).pick_many(
                        episodes, self._format_episode_label, title="Download episodes",
                        subtitle="Tab toggles an episode · Enter reviews selection · Esc / b back")
                if chosen:
                    await self.download_items([episodes[i] for i in chosen], selected_season)
                continue
            selected = self.run_fzf_objects(
                episodes, self._format_episode_label,
                title=escape(f"{self.current_show_title} — {season_label}"),
                header=f"{len(episodes)} episodes · Esc / b: back",
                initial_index=self.current_episode_index)
            if selected is None:
                return NavigationOutcome.BACK
            self.current_episode_index = episodes.index(selected)
            while True:
                episode = episodes[self.current_episode_index]
                result = await self.play_episode(episode, season_label)
                if result != PlaybackOutcome.FINISHED:
                    break
                outcome = await self.prompt_next_episode()
                if outcome == NavigationOutcome.SEARCH:
                    return outcome
                if outcome != NavigationOutcome.NEXT:
                    break
                self.current_episode_index += 1

    async def play_episode(self, episode, season_label):
        episode_num = self._normalize_episode_number(episode.get("episode_number") or episode.get("display_number"))
        episode_title = self._clean_episode_title(episode.get("title", ""), episode_num)
        self._set_breadcrumbs(season_label, f"Episode {episode_num}")
        self.banner()
        self._print_focus_panel(self.current_show_title, f"{season_label} · Episode {episode_num}")
        servers = await self._load_with_retry(
            "Loading servers", scraper.fetch_episode_servers, episode,
            empty_message="No streaming servers available for this episode.")
        if not servers:
            return PlaybackOutcome.CANCELLED
        while True:
            try:
                result = await self.select_and_play(
                    servers, f"{season_label} · Episode {episode_num}", is_episode=True,
                    season_num=self.current_season.get("season_number"),
                    ep_num=episode_num, ep_title=episode_title)
            except Exception as exc:
                self.log("⚠", escape(f"Playback failed: {exc}"), self.theme["error"])
                result = PlaybackOutcome.FAILED
            if result != PlaybackOutcome.FAILED:
                return result
            if self.run_fzf(["Retry playback", "Back to Episodes"], title="Playback unavailable") != 0:
                return PlaybackOutcome.CANCELLED

    async def download_items(self, items, season=None):
        from .download import check_download_tools, media_title
        from .managers import detected_managers
        try:
            check_download_tools()
        except RuntimeError as exc:
            self.run_fzf(["Back"], title="Download tools required",
                         notice=callout("SETUP", str(exc), "warning", self.theme))
            return
        directory = self.config.download_dir
        labels = ["Episode " + self._normalize_episode_number(
            item.get("episode_number") or item.get("display_number"))
            if season else self.current_show_title for item in items]
        manager_keys = list(detected_managers())
        selected_manager = self.run_fzf(download_engine_labels(manager_keys) + ['Back'],
            title='How should downloads run?',
            subtitle='Choose the downloader. The app keeps retry and cancellation handling.')
        if selected_manager is None or selected_manager >= len(manager_keys):
            return
        manager = manager_keys[selected_manager]
        if self.run_fzf(["Queue in background", "Back"], title=f"Download {len(items)} item(s)?",
                        subtitle=f"Save to {directory} · " + ", ".join(labels)) != 0:
            return
        for item, label in zip(items, labels):
            number = self._normalize_episode_number(item.get('episode_number') or item.get('display_number')) if season else None
            title = media_title(self.current_show_title, (season or {}).get('season_number'), number,
                                (season or {}).get('season_part'))
            self.media_store.enqueue({
                'catalog_url': item['url'], 'show_url': getattr(self, 'current_show_url', None) or item['url'],
                'show_title': self.current_show_title, 'download_title': title,
                'content_type': 'series' if season else 'movie',
                'season_number': (season or {}).get('season_number'),
                'season_part': (season or {}).get('season_part'),
                'season_label': (season or {}).get('display_label'),
                'episode_number': self._normalize_episode_number(item.get('episode_number') or item.get('display_number')) if season else None,
                'episode_title': item.get('title') if season else None,
                'manager': manager,
                'quality': (self.config.download_quality if isinstance(getattr(self.config, 'download_quality', None), str)
                            else 'best'),
                'speed': (self.config.download_speed if isinstance(getattr(self.config, 'download_speed', None), str)
                          else 'unlimited'),
            }, directory)
        self.download_worker.start()
        choice = self.run_fzf(['Keep browsing', 'Open Downloads'], title='Queued in background',
                     subtitle='/downloads shows live progress, retry and cancel controls')
        if choice == 1:
            await self.show_download_queue()

    async def run_download_queue(self, retry_failed=False):
        self.download_worker.start(retry_failed=retry_failed)

    async def select_and_play(self, servers, title_context, is_episode=False, season_num=None, ep_num=None, ep_title=None):
        theme = self.theme
        
        if not servers:
            self.log("✗", "No servers available", theme['error'])
            await asyncio.sleep(1)
            return PlaybackOutcome.CANCELLED

        primary_color = theme.get('primary', 'cyan')

        # Fast-track VidTube server directly (skip expensive multi-server health probing)
        def is_vidtube_server(s):
            url_str = str(s.get('embed_url') or '').lower()
            name_str = str(s.get('name') or '').lower()
            return any(k in url_str for k in ['vidtube', 'cdn-video', 'serv-stream']) or 'videotube' in name_str

        vidtube_server = next((s for s in servers if is_vidtube_server(s)), None)
        if vidtube_server:
            selected_server = vidtube_server
            video_url = selected_server.get('video_url')
            srv_url = selected_server.get('embed_url') or scraper.base_url

            if not video_url:
                with Progress(
                    SpinnerColumn("star"),
                    TextColumn("[progress.description]{task.description}"),
                    console=CONSOLE,
                    transient=True
                ) as progress:
                    progress.add_task(f"[{primary_color}]Resolving VidTube stream...", total=None)
                    proc = ServerProcessor()
                    video_url = await asyncio.to_thread(proc.extract, srv_url, scraper.base_url)

            if video_url:
                if hasattr(video_url, "referer") and video_url.referer:
                    referer = video_url.referer
                else:
                    referer = "https://down.vidtube.one/"

                self.log("▶️", f"Launching player with {selected_server.get('name')}...")
                await asyncio.sleep(0.1)

                return self.play_video(
                    video_url,
                    referer=referer,
                    title=title_context,
                    season_num=season_num,
                    ep_num=ep_num,
                    ep_title=ep_title,
                    content_type="series" if is_episode else "movie"
                )


        # Fallback: probe servers if VidTube is unavailable or extraction failed
        checker = ServerHealthChecker(referer=scraper.base_url)
        loop = asyncio.get_running_loop()

        with Progress(
            SpinnerColumn("dots2"),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(bar_width=25),
            TextColumn(f"[bold {primary_color}]{{task.completed}}/{{task.total}}[/]"),
            console=CONSOLE,
            transient=True
        ) as progress:
            ptask = progress.add_task(f"[{primary_color}]Checking servers...", total=len(servers))

            def advance():
                # Called from the probe worker thread; progress lives on the loop.
                loop.call_soon_threadsafe(progress.advance, ptask, 1)

            servers = await asyncio.to_thread(checker.probe_all, servers, 16, advance)
            # Let the queued progress callbacks drain while the bar still exists.
            await asyncio.sleep(0)

        total_servers = len(servers)
        live_count = sum(1 for s in servers if s.get("health") == LIVE)
        slow_count = sum(1 for s in servers if s.get("health") == SLOW)

        if total_servers == 1 and live_count == 1:
            # Nothing to choose between and we already know it works.
            selected_server = servers[0]
        else:
            def fmt_server(server):
                status = {LIVE: "Ready", SLOW: "Slow", OFFLINE: "Offline"}.get(server.get('health'), "Unknown")
                return PickerRow((str(server.get('name') or 'Server'), status), ("Server", "Status"))

            summary = []
            if live_count:
                summary.append(f"🟢 {live_count}/{total_servers} online")
            if slow_count:
                summary.append(f"🟡 {slow_count} slow")
            if not summary:
                summary.append(f"⚠️  No server online ({total_servers} checked)")
            subtitle = " · ".join(summary) + f" · {title_context}"

            selected_server = self.run_fzf_objects(
                servers,
                fmt_server,
                prompt="📡 Server ❯ ",
                title="Streaming servers",
                subtitle=subtitle,
                header=None
            )
        if not selected_server:
            return PlaybackOutcome.CANCELLED
        
        video_url = selected_server.get('video_url')
        srv_url = selected_server.get('embed_url') or scraper.base_url
        
        if not video_url:
            name = selected_server.get('name', 'Server')
            if selected_server.get('health') == OFFLINE:
                self.log("⚠️", f"{name} was marked offline — retrying anyway...", f"bold {theme['error']}")
            else:
                self.log("🔄", f"Resolving {name}...")
            with Progress(
                SpinnerColumn("star"),
                TextColumn("[progress.description]{task.description}"),
                console=CONSOLE,
                transient=True
            ) as progress:
                progress.add_task(f"[{primary_color}]Extracting stream...", total=None)
                proc = ServerProcessor()
                video_url = await asyncio.to_thread(proc.extract, srv_url, scraper.base_url)
        
        if not video_url:
            self.log("✗", f"Server offline: {selected_server.get('name')}", theme['error'])
            return PlaybackOutcome.FAILED

        # Determine the correct referer for mpv anti-hotlink headers
        if hasattr(video_url, "referer") and video_url.referer:
            referer = video_url.referer
        elif any(k in str(video_url).lower() for k in ["cdn-video", "serv-stream", "vidtube"]):
            referer = "https://down.vidtube.one/"
        elif any(k in str(video_url).lower() for k in ["gamescdn", "updown"]):
            referer = "https://updown.cam/"
        elif "uqload" in str(video_url).lower():
            referer = "https://uqload.net/"
        else:
            referer = srv_url
        
        self.log("▶️", f"Launching player with {selected_server.get('name')}...")
        await asyncio.sleep(0.3)
        
        return self.play_video(
            video_url,
            referer=referer,
            title=title_context,
            season_num=season_num,
            ep_num=ep_num,
            ep_title=ep_title,
            content_type="series" if is_episode else "movie"
        )
        
    
    async def prompt_next_episode(self):
        has_next = self.current_episode_index + 1 < len(self.current_episodes)
        options = (["Play Next Episode"] if has_next else []) + ["Back to Episodes", "Back to Search"]
        choice = self.run_fzf(options, title="Playback finished" if has_next else "End of season",
                              header="Esc / b: back to episodes")
        if choice is None:
            return NavigationOutcome.EPISODES
        return {"Play Next Episode": NavigationOutcome.NEXT,
                "Back to Search": NavigationOutcome.SEARCH,
                "Back to Episodes": NavigationOutcome.EPISODES}[options[choice]]

    def _build_mpv_command(self, url, referer=None, title=None, sock=None):
        cmd = ["mpv"]
        if sock:
            cmd.append(f"--input-ipc-server={sock}")
        if title:
            cmd.append(f"--title={title}")
        req_referer = referer or getattr(url, "referer", None)
        is_vidtube = any(k in str(url).lower() for k in ["cdn-video", "serv-stream", "vidtube"]) or \
                     bool(req_referer and any(k in str(req_referer).lower() for k in ["vidtube", "cdn-video"]))
        if is_vidtube:
            if not req_referer or not any(k in str(req_referer).lower() for k in ["vidtube", "down.vidtube.one"]):
                req_referer = "https://down.vidtube.one/"
            cmd.append(f"--referrer={req_referer}")
            cmd.append("--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
        elif req_referer:
            cmd.append(f"--referrer={req_referer}")
        cmd.append(str(url))
        return cmd

    def play_video(
        self,
        url: str,
        referer: str = "",
        title: str = "",
        season_num: Optional[int] = None,
        ep_num: Optional[str] = None,
        ep_title: Optional[str] = None,
        content_type: str = "movie"
    ):
        theme = self.theme
        season = (self.current_season or {}) if ep_num is not None else {}
        prog = self.history.get_progress(
            self.current_show_title, season_num, ep_num,
            show_url=getattr(self, "current_show_url", None), season_part=season.get("season_part"))
        start_time = 0.0

        if prog and prog.time_pos > 10.0 and prog.status != WatchStatus.COMPLETED:
            start_time = prog.time_pos if getattr(self, '_resume_direct', False) else 0.0
            pos_fmt = format_seconds(prog.time_pos)
            pct_int = int(prog.progress_pct)
            options = [
                f"▶️  Resume from {pos_fmt} ({pct_int}%)",
                "🔄  Start from beginning (00:00)"
            ]
            choice = 0 if getattr(self, '_resume_direct', False) else self.run_fzf(
                options,
                prompt="▶️ Playback ❯ ",
                title=f"▶️ Saved Progress: {pos_fmt}",
                header=f"Resume {title} at {pos_fmt}?"
            )
            if choice is None:
                return PlaybackOutcome.CANCELLED
            if choice == 0:
                start_time = prog.time_pos

        self.banner()
        self.log("🎬", f"Playing {title}...", f"bold {theme['primary']}")

        rc, mpv_state = run_mpv_monitored(
            url=url,
            title=title or self.current_show_title or "cenima-cli",
            start_time=start_time,
            referer=referer,
            subtitle_language=self.config.subtitle_language,
        )

        if mpv_state and mpv_state.time_pos and mpv_state.time_pos > 5:
            saved_prog = self.history.update_progress(
                show_title=self.current_show_title or title,
                season_number=season_num,
                episode_number=ep_num,
                time_pos=mpv_state.time_pos,
                duration=mpv_state.duration or (prog.duration if prog else 0.0),
                content_type=content_type,
                episode_title=ep_title,
                show_url=getattr(self, "current_show_url", None),
                season_label=season.get("display_label"),
                season_part=season.get("season_part"),
                episode_url=(self.current_episodes[self.current_episode_index].get("url")
                             if ep_num is not None and self.current_episodes else None)
            )
            if saved_prog.status == WatchStatus.COMPLETED:
                self.log("👁️", f"Marked as Watched ({int(saved_prog.progress_pct)}%)", f"bold {theme['primary']}")
            else:
                pos_fmt = format_seconds(saved_prog.time_pos)
                self.log("💾", f"Saved progress at {pos_fmt} ({int(saved_prog.progress_pct)}%)", theme['secondary'])

        if rc == 130:
            raise KeyboardInterrupt()
        if rc in (0, None):
            self.log("✓", "Playback finished", f"bold {theme['primary']}")
            return PlaybackOutcome.FINISHED
        else:
            self.log("⚠️", f"MPV exited with code {rc}", theme["error"])
            return PlaybackOutcome.FAILED



def main(argv=None):
    configure_utf8_terminal()
    parser = argparse.ArgumentParser(description="Browse, play and download movies, series and anime.")
    parser.add_argument("--version", action="version", version=f"cenima-cli {__version__}")
    parser.add_argument("--doctor", action="store_true", help="Check the local environment and exit")
    parser.add_argument("--no-animation", action="store_true", help="Disable welcome-screen motion")
    parser.add_argument("--export-settings", metavar="PATH", help="Export redacted settings as JSON")
    parser.add_argument("--import-settings", metavar="PATH", help="Import and validate settings JSON")
    args = parser.parse_args(argv)
    if args.doctor:
        from .doctor import run_doctor
        run_doctor(CONSOLE, Config())
        return
    if args.export_settings:
        from .settings_io import write_settings_export
        path = write_settings_export(Config(), args.export_settings)
        CONSOLE.print(f"Settings exported to {path}")
        return
    if args.import_settings:
        from .settings_io import read_settings_import
        try:
            settings = read_settings_import(args.import_settings)
            config = Config()
            for key, value in settings.items():
                setattr(config, key, value)
            if not config.save():
                raise OSError("Could not save imported settings.")
            CONSOLE.print("Settings imported")
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            parser.error(str(exc))
        return
    # The entire session owns the alternate buffer. Individual pickers must not
    # switch buffers and restore an obsolete screen when a selection is made.
    with CONSOLE.screen() if CONSOLE.is_terminal else nullcontext():
        cli = CinemaCLI()
        cli.no_animation = bool(args.no_animation or
                                os.environ.get('CINEMA_REDUCED_MOTION') == '1')
        try:
            asyncio.run(cli.search_flow())
        except KeyboardInterrupt:
            cli.goodbye()
        finally:
            if hasattr(cli, '_download_worker'):
                cli._download_worker.shutdown()


if __name__ == "__main__":
    main()
