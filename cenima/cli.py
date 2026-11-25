#!/usr/bin/env python3
import sys
import re
import os
import json
import logging
import shutil
import subprocess
import asyncio
import argparse
import textwrap
import time
import platform
import urllib.request
from difflib import SequenceMatcher
from pathlib import Path
from typing import List, Any, Optional, Dict
from rich.console import Console, Group
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn
from rich.table import Table
from rich.markup import escape
from rich.cells import cell_len, set_cell_size

from .scraper import TopCinemaScraper, clean_show_title
from .history import HistoryManager
from .models import WatchStatus, NavigationOutcome, PlaybackOutcome, canonical_content_url
from .mpv import run_mpv_monitored
from .picker import RichPicker, CustomPrompt, PickerAction
from .accent import get_auto_theme
from .health import ServerHealthChecker, LIVE, SLOW, OFFLINE
from .processor import ServerProcessor
from .config import (
    __version__, __author__, __license__,
    CONFIG_DIR, CONFIG_FILE,
    ASCII_ART, GOODBYE_ART,
    THEMES, DEFAULT_THEME
)


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

CONSOLE = Console()
logger = logging.getLogger(__name__)
GITHUB_REPO_URL = "https://github.com/np4abdou1/cenima-cli"
GITHUB_API_URL = "https://api.github.com/repos/np4abdou1/cenima-cli"


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


def get_github_stars(allow_network: bool = True) -> int:
    cache_file = CONFIG_DIR / "stars_cache.json"
    cache_duration = 3600

    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            if not allow_network or (time.time() - data.get('timestamp', 0) < cache_duration):
                return data.get('stars', 0)
        except Exception:
            pass

    if not allow_network:
        return 0

    try:
        req = urllib.request.Request(GITHUB_API_URL)
        req.add_header('Accept', 'application/vnd.github.v3+json')
        with urllib.request.urlopen(req, timeout=3) as response:
            data = json.loads(response.read().decode())
            stars = data.get('stargazers_count', 0)
            try:
                CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                cache_file.write_text(json.dumps({
                    'stars': stars,
                    'timestamp': time.time()
                }))
            except Exception:
                pass
            return stars
    except Exception:
        if cache_file.exists():
            try:
                data = json.loads(cache_file.read_text())
                return data.get('stars', 0)
            except Exception:
                pass
        return 0


class Config:

    def __init__(self):
        self.theme = DEFAULT_THEME
        self.mpv_args: List[str] = []
        self.simple_mode = True
        self.send_headers = False
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
                    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                    if not isinstance(data, dict):
                        raise ValueError("Config root is not an object")
                except (json.JSONDecodeError, ValueError, OSError):
                    backup = CONFIG_FILE.with_name(CONFIG_FILE.name + ".corrupt.bak")
                    backup.write_bytes(CONFIG_FILE.read_bytes())
                    data = {}
                self.theme = data.get("theme", DEFAULT_THEME)
                raw_args = data.get("mpv_args", [])
                self.mpv_args = [str(a) for a in raw_args if isinstance(a, str)] if isinstance(raw_args, list) else []
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
                language = data.get("subtitle_language", "ar")
                self.subtitle_language = language if isinstance(language, str) else "ar"
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
                try:
                    loaded = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        existing = loaded
                except Exception:
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
            "mpv_args": self.mpv_args,
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
            return self._auto_theme
        theme = THEMES.get(self.theme)
        if not theme:
            return get_auto_theme()
        return theme


class CinemaCLI:
    def __init__(self):
        self.config = Config()
        self.history = HistoryManager()
        from .watch_later import WatchLaterStore
        self.watch_later = WatchLaterStore()
        self.picker_hint = "↑↓ navigate • Enter select • Esc back"
        self.current_show_title = ""
        self.current_overview_panel = None
        self.current_episodes = []
        self.current_episode_index = 0
        self.current_season = None
        self.github_stars = get_github_stars(allow_network=False)
        import threading
        threading.Thread(target=self._refresh_stars_background, daemon=True).start()
        self.check_deps()

    def _refresh_stars_background(self):
        try:
            stars = get_github_stars(allow_network=True)
            if stars:
                self.github_stars = stars
        except Exception:
            pass

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
        return self.config.get_theme()

    def check_deps(self):
        if not shutil.which("mpv"):
            CONSOLE.print("[bold red]✗ Missing dependency:[/bold red] mpv")
            CONSOLE.print("[dim]Install mpv (and optionally fzf) using your package manager.[/dim]")
            sys.exit(1)

    def log(self, emoji: str, message: str, style: str = "dim"):
        CONSOLE.print(f"[{style}]{emoji} {message}[/{style}]")

    def _banner_renderable(self, available_height):
        theme = self.theme

        grid = Table.grid(padding=(0, 2))
        grid.add_column()
        grid.add_column(justify="left", vertical="middle")

        art = Text(ASCII_ART.strip('\n'), style=theme["primary"], no_wrap=True)

        info_text = Text()
        fzf_ready = bool(shutil.which("fzf"))
        engine_tag = "mpv + fzf" if fzf_ready else "mpv"
        header_inner = f"  cenima-cli  v{__version__}  •  {engine_tag}  "
        border_line = "─" * len(header_inner)

        info_text.append(f"╭{border_line}╮\n", style=theme["accent"])
        info_text.append("│  ", style=theme["accent"])
        info_text.append("cenima-cli", style=f"bold {theme['primary']}")
        info_text.append(f"  v{__version__}", style="dim")
        info_text.append("  •  ", style=theme["accent"])
        info_text.append(engine_tag, style=f"bold {theme['secondary']}")
        info_text.append("  │\n", style=theme["accent"])
        info_text.append(f"╰{border_line}╯\n", style=theme["accent"])
        info_text.append("\n")

        history_items = self.history.get_recent_history(limit=5000)
        watched_eps = sum(1 for i in history_items if i.episode_number and (i.status == WatchStatus.COMPLETED or i.progress_pct >= 85.0))
        watched_movies = sum(1 for i in history_items if i.content_type == "movie" and (i.status == WatchStatus.COMPLETED or i.progress_pct >= 85.0))
        total_watch_time = sum(i.time_pos for i in history_items)
        hours = int(total_watch_time // 3600)

        try:
            os_label = f"{platform.system()} {platform.release()}"
        except Exception:
            os_label = "Unknown"

        if self.config.theme == "auto":
            theme_label = f"Auto ({theme.get('source', 'System')})"
        else:
            theme_label = self.config.theme.title()

        if getattr(self, "github_stars", None) is None:
            self.github_stars = get_github_stars(allow_network=False)

        rows = [
            ("Movies", f"{watched_movies} watched", None),
            ("Episodes", f"{watched_eps} watched", None),
            ("Watch Time", f"{hours} hours", None),
            ("System", os_label, None),
            ("Theme", theme_label, None),
        ]
        for label, value, val_style in rows:
            info_text.append(f"  {label:<11} ", style=f"bold {theme['secondary']}")
            info_text.append("│ ", style=theme["accent"])
            info_text.append(f"{value}\n", style=val_style or "")

        info_text.append(f"  {'GitHub':<11} ", style=f"bold {theme['secondary']}")
        info_text.append("│ ", style=theme["accent"])
        if self.github_stars > 0:
            info_text.append(f"★ {self.github_stars}", style="bold #daa520")
            info_text.append(" (Thank you!)\n", style="dim")
        else:
            info_text.append("★ 0", style="#daa520")
            info_text.append(" (Star us!)\n", style="dim")

        info_text.append("\n")
        info_text.append("  Commands\n", style=f"bold {theme['primary']}")
        info_text.append("    /continue   /history     /last-eps   /last-mv\n", style="dim")
        info_text.append("    /downloads  /library     /watch-later /settings\n", style="dim")
        info_text.append("    /theme      /doctor      /help       exit (Esc)", style="dim")

        grid.add_row(art, info_text)
        if CONSOLE.width >= 110 and available_height >= 17:
            return grid

        # Use the same information without the reel when space is limited.
        compact = [Text(f'cenima-cli v{__version__} · {engine_tag}',
                        style=f"bold {theme['primary']}", no_wrap=True)]
        if available_height >= 3:
            compact.append(Text(f'{watched_movies} movies · {watched_eps} episodes · {hours} hours watched',
                                style='dim', no_wrap=True))
        commands = ['/continue', '/history', '/last-eps', '/last-mv', '/downloads',
                    '/library', '/watch-later', '/settings', '/theme', '/doctor', '/help', 'exit']
        columns = max(1, min(4, CONSOLE.width // 15))
        command_rows = [' '.join(command.ljust(14) for command in commands[i:i + columns]).rstrip()
                        for i in range(0, len(commands), columns)]
        slots = max(0, available_height - len(compact))
        compact.extend(Text(row, style='dim', no_wrap=True) for row in command_rows[:slots])
        return Group(*compact)

    def banner(self):
        CONSOLE.clear()
        CONSOLE.print(self._banner_renderable(max(1, CONSOLE.height - 6)))
        CONSOLE.print('')

    def goodbye(self):
        CONSOLE.clear()
        theme = self.theme
        CONSOLE.print(Text(GOODBYE_ART, style=theme['primary']))
        CONSOLE.print("[dim]Thanks for watching with cenima-cli! 🎬[/dim]\n")

    def _print_focus_panel(self, title: str, subtitle: Optional[str] = None):
        theme = self.theme
        CONSOLE.print(f"[bold {theme['primary']}]{escape(title)}[/bold {theme['primary']}]")
        if subtitle:
            CONSOLE.print(f"[dim]{escape(subtitle)}[/dim]")
        CONSOLE.print("")

    def run_fzf(
        self,
        items: List[str],
        prompt: str = "❯ ",
        header: Optional[str] = None,
        title: Optional[str] = None,
        subtitle: Optional[str] = None,
        initial_index: int = 0,
        shortcuts: Optional[Dict] = None,
        notice: Any = None,
        multi: bool = False,
    ) -> Any:
        if not items:
            return [] if multi else None

        CONSOLE.clear()

        if shutil.which("fzf"):
            try:
                cmd = [
                    "fzf",
                    "--height=100%",
                    "--layout=reverse",
                    "--border",
                    "--prompt=" + prompt,
                    "--pointer=❯",
                    "--info=inline",
                    "--ansi",
                ]
                if multi:
                    cmd.append("--multi")
                if initial_index:
                    position = max(0, min(initial_index, len(items) - 1)) + 1
                    cmd.append(f"--bind=load:pos({position})")
                if shortcuts:
                    cmd.append("--expect=" + ",".join(shortcuts))

                def to_plain(text_str: str) -> str:
                    try:
                        return Text.from_markup(str(text_str)).plain
                    except Exception:
                        return str(text_str)

                def to_ansi(text_str: str) -> str:
                    try:
                        temp_c = Console(force_terminal=True, color_system="truecolor")
                        with temp_c.capture() as capture:
                            temp_c.print(Text.from_markup(str(text_str)), end="")
                        return capture.get()
                    except Exception:
                        return to_plain(text_str)

                header_parts = []
                if title:
                    header_parts.append(to_plain(title))
                if subtitle:
                    header_parts.append(to_plain(subtitle))
                if header:
                    header_parts.append(to_plain(header))
                if notice is not None:
                    notice_text = getattr(notice, "renderable", notice)
                    if isinstance(notice_text, Text):
                        header_parts.append(notice_text.plain.replace("\n", " · "))
                    elif isinstance(notice_text, str):
                        header_parts.append(to_plain(notice_text))

                if header_parts:
                    cmd.append(f"--header={' · '.join(p for p in header_parts if p)}")

                ansi_items = [to_ansi(str(item)) for item in items]
                input_data = "\n".join([f"{i+1}. {item}" for i, item in enumerate(ansi_items)])

                with subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=None,
                    text=True,
                    encoding="utf-8",
                ) as proc:
                    try:
                        out, _ = proc.communicate(input=input_data)
                    except BaseException:
                        # A failed transfer or Ctrl+C must not leave an fzf
                        # screen competing with the fallback picker.
                        if proc.poll() is None:
                            proc.kill()
                        proc.communicate()
                        raise

                if proc.returncode == 0 and out.strip():
                    lines = out.splitlines()
                    pressed = lines.pop(0) if shortcuts else ""
                    indices = []
                    for selected_line in lines:
                        match = re.match(r"^(\d+)\.", selected_line)
                        if match:
                            idx = int(match.group(1)) - 1
                            if 0 <= idx < len(items):
                                indices.append(idx)
                    if pressed in (shortcuts or {}):
                        action = shortcuts[pressed]
                        if isinstance(action, int):
                            return action
                        index = indices[0] if indices else None
                        return PickerAction(action, items[index] if index is not None else None, index)
                    if multi:
                        return indices
                    return indices[0] if indices else None
                elif proc.returncode == 130:
                    return [] if multi else None
            except Exception:
                logger.warning("fzf failed; using the built-in picker", exc_info=True)

        picker = RichPicker(CONSOLE, self.theme)
        return picker.pick(
            items,
            format_func=lambda x: str(x),
            title=title or "",
            subtitle=" · ".join(part for part in (subtitle, header) if part),
            initial_index=initial_index,
            shortcuts=shortcuts,
            multi=multi,
        )

    def run_fzf_objects(
        self,
        items: List[Any],
        format_func,
        prompt: str = "❯ ",
        title: Optional[str] = None,
        subtitle: Optional[str] = None,
        header: Optional[str] = None,
        initial_index: int = 0,
        shortcuts: Optional[Dict] = None,
        notice: Any = None,
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
            initial_index=initial_index,
            shortcuts=shortcuts,
            notice=notice,
        )
        if isinstance(idx, PickerAction):
            item = items[idx.index] if idx.index is not None and 0 <= idx.index < len(items) else None
            return PickerAction(idx.action, item, idx.index)
        if idx is not None and 0 <= idx < len(items):
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
            path = CustomPrompt(CONSOLE, self.theme).ask("Import settings path ❯ ")
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
            self.banner()
            CONSOLE.print(f"[bold]cenima-cli[/bold] v{__version__}")
            CONSOLE.print(f"[dim]Author: {__author__} • License: {__license__}[/dim]")
            input("\nPress Enter to continue...")
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
            selected = self.run_fzf_objects(
                items,
                lambda item: f"📌 {escape(item.get('title') or item.get('show_title') or 'Untitled')} ({item.get('episode_number') or 'Movie'})",
                prompt="📌 Watch Later ❯ ",
                title="📌 Watch Later",
                subtitle="Enter opens saved catalog page",
            )
            if selected is None:
                return
            choice = self.run_fzf(
                ["Open", "Remove", "Back"],
                title=selected.get('title') or selected.get('show_title') or 'Watch Later',
            )
            if choice == 0:
                await self.show_details_flow({
                    'title': selected.get('title') or selected.get('show_title'),
                    'url': selected.get('show_url') or selected.get('url'),
                    'type': selected.get('content_type') or 'movie',
                })
            elif choice == 1:
                self.watch_later.remove(selected)

    async def show_history_menu(self):
        self.banner()
        theme = self.theme
        history_items = self.history.get_recent_history(limit=30)

        if not history_items:
            self.log("ℹ️", "No watch history yet", theme['secondary'])
            input("\nPress Enter to continue...")
            return

        def fmt_history_item(item):
            badge = "👁️" if item.status == WatchStatus.COMPLETED or item.progress_pct >= 85 else "⏳" if item.status == WatchStatus.IN_PROGRESS else "📺"
            title = escape(item.show_title)

            detail = ""
            if item.season_number or item.episode_number:
                s_str = f"S{item.season_number}" if item.season_number else ""
                e_str = f"E{item.episode_number}" if item.episode_number else ""
                detail = f" — {s_str}{e_str}".strip()
                if item.episode_title:
                    detail += f" ({item.episode_title})"

            prog_str = ""
            if item.duration > 0:
                pos_fmt = format_seconds(item.time_pos)
                dur_fmt = format_seconds(item.duration)
                prog_str = f" [yellow][{int(item.progress_pct)}% · {pos_fmt}/{dur_fmt}][/]"
            elif item.status == WatchStatus.COMPLETED:
                prog_str = " [green][Watched][/]"

            return f"{badge} [b]{title}[/]{detail}{prog_str}"

        header = f"📊 {len(history_items)} items in history"
        selected = self.run_fzf_objects(
            history_items,
            fmt_history_item,
            prompt="🕒 History ❯ ",
            title="🕒 Recent Watch History",
            subtitle=header,
            header=header
        )

        if selected:
            options = [
                f"🔍 Search '{selected.show_title}'",
                "▶️ Resume if available",
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
                await self.search_and_open(selected.show_title)
            elif choice_idx == 1:
                await self.resume_progress(selected)
            elif choice_idx == 2:
                self.history.clear_history()
                self.log("✓", "History cleared", theme['primary'])
                time.sleep(1)

    async def show_latest(self, kind):
        from .download import media_title
        label = 'Latest added episodes' if kind == 'episode' else 'Latest added movies'
        self.current_show_title = ''
        operation = scraper.get_latest_episodes if kind == 'episode' else scraper.get_latest_movies
        items = await self._load_with_retry(label, operation, empty_message=f'No {label.lower()} are listed right now.')
        if not items:
            return
        index = 0
        while True:
            def format_item(item):
                if kind == 'movie':
                    clean_t = clean_show_title(item['title'])
                    year = (item.get('metadata') or {}).get('year')
                    year_str = f" ({year})" if year and str(year).lower() != 'n/a' else ""
                    quality = (item.get('metadata') or {}).get('quality')
                    q_str = f" [{quality}]" if quality else ""
                    return f"🎬 {escape(clean_t)}{year_str}{q_str}"
                season = str(item.get('season_number') or '?').zfill(2)
                episode = self._normalize_episode_number(item['episode_number']).zfill(2)
                quality = (item.get('metadata') or {}).get('quality') or 'Unknown'
                return f"📺 {escape(item['show_title'])} · S{season}E{episode} [{escape(str(quality))}]"

            selected = self.run_fzf_objects(
                items, format_item, title=label,
                subtitle=('Newest added episode per show' if kind == 'episode' else 'Provider added order') +
                         ' · Enter actions · Esc back',
                initial_index=index,
            )
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
            self.current_season = {
                'season_number': context.get('season_number'),
                'season_part': context.get('season_part'),
                'display_label': f"Season {context.get('season_number', '?')}",
            }
            self.current_episodes, self.current_episode_index = [context], 0
            action = self.run_fzf(
                ['Play', 'Download', 'Open show page', 'Back'],
                title=media_title(
                    self.current_show_title,
                    context.get('season_number'),
                    context['episode_number'],
                    context.get('season_part'),
                ),
            )
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
                outcome = await self.show_details_flow({
                    'title': context['show_title'],
                    'url': show_url,
                    'type': context.get('show_type') or 'series',
                })
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
        while True:
            folder = Path(self.config.download_dir).expanduser().resolve()
            choice = self.run_fzf(
                [
                    'Change media folder and consolidate',
                    'Open media folder',
                    'Download preferences',
                    'Subtitle preferences',
                    'Completion notifications',
                    'Back',
                ],
                title='Settings',
                subtitle=f'Media folder: {folder}',
            )
            if choice in (None, 5):
                return
            try:
                if choice == 1:
                    open_media_folder(folder)
                elif choice == 2:
                    quality_options = ['Best available', '1080p', '720p', '480p', '360p']
                    quality_values = ['best', '1080p', '720p', '480p', '360p']
                    selected_quality = self.run_fzf(
                        quality_options,
                        title='Preferred download quality',
                        subtitle='Unavailable quality falls back to the closest available stream',
                    )
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
                        title='Completion notifications',
                    )
                    if selected is not None:
                        self.config.notifications = selected == 0
                        if not self.config.save():
                            raise OSError('Could not save notification preference.')
                elif choice == 0:
                    CONSOLE.clear()
                    value = CustomPrompt(CONSOLE, self.theme).ask(f"Media folder [{folder}] ❯ ")
                    if not value or value in ('/exit', '/continue', '/library'):
                        continue
                    with CONSOLE.status('Consolidating completed media…'):
                        result = self.set_download_folder(value)
                    self.run_fzf(
                        ['Back'],
                        title='Media folder saved',
                        subtitle=f"{result['moved']} moved · {result['deduplicated']} identical copies consolidated · {result['folder']}",
                    )
            except (OSError, ValueError, RuntimeError) as exc:
                self.run_fzf(['Back'], title='Folder change could not complete', subtitle=str(exc))

    def _restore_media_context(self, metadata):
        self.current_overview_panel = None
        self.current_show_title = metadata['show_title']
        self.current_show_url = metadata.get('show_url') or metadata.get('catalog_url')
        self.current_episode_index = 0
        number = metadata.get('episode_number')
        self.current_season = (
            {
                'season_number': metadata.get('season_number'),
                'display_label': metadata.get('season_label'),
                'season_part': metadata.get('season_part'),
            }
            if number is not None
            else None
        )
        self.current_episodes = (
            [{'url': metadata.get('episode_url') or metadata.get('catalog_url'), 'episode_number': number}]
            if number is not None
            else []
        )

    async def resume_progress(self, progress):
        metadata = progress.model_dump()
        catalog = progress.episode_url if progress.episode_number is not None else progress.show_url
        if not catalog:
            if self.run_fzf(['Search for this title', 'Back'], title='Legacy history has no catalog page') == 0:
                await self.search_and_open(progress.show_title)
            return
        metadata['catalog_url'] = catalog
        self._restore_media_context(metadata)
        servers = await self._load_with_retry(
            'Resolving saved title', scraper.fetch_episode_servers,
            {'url': catalog}, empty_message='Saved title has no available servers.',
        )
        if servers:
            self._resume_direct = True
            try:
                await self.select_and_play(
                    servers, progress.show_title,
                    is_episode=progress.episode_number is not None,
                    season_num=progress.season_number,
                    ep_num=progress.episode_number,
                    ep_title=progress.episode_title,
                )
            finally:
                self._resume_direct = False

    def show_library(self):
        while True:
            items = self.media_store.media()
            if not items:
                self.run_fzf(
                    ['Back'],
                    title='No managed downloads yet',
                    subtitle='Completed downloads appear here automatically.',
                )
                return
            selected = self.run_fzf_objects(
                items,
                lambda row: f"{'✓' if row['available'] else '✗'} {escape(row['download_title'])} ({'Ready' if row['available'] else 'Missing / changed'})",
                prompt="📁 Library ❯ ",
                title='Local library',
                subtitle='Offline playback · Esc back',
            )
            if selected is None:
                return
            if not selected['available']:
                self.run_fzf(
                    ['Back'],
                    title='Local file is missing or has changed',
                    subtitle='Restore the original file or download the catalog item again.',
                )
                continue
            self._restore_media_context(selected)
            self.play_video(
                selected['path'],
                local_file=True,
                title=selected['download_title'],
                season_num=selected.get('season_number'),
                ep_num=selected.get('episode_number'),
                ep_title=selected.get('episode_title'),
                content_type=selected.get('content_type') or 'movie',
            )

    async def show_download_queue(self):
        while True:
            jobs = self.media_store.jobs()
            pending = sum(job['status'] in ('pending', 'running') for job in jobs)
            failed = sum(job['status'] in ('failed', 'cancelled') for job in jobs)
            worker_error = self._download_worker.error if hasattr(self, '_download_worker') else ''
            choice = self.run_fzf(
                [
                    'Open live downloads',
                    f'Start/resume queue ({pending})',
                    f'Retry failed/cancelled ({failed})',
                    'Back',
                ],
                title='Downloads',
                subtitle=worker_error or 'R: retry failed/cancelled downloads',
                shortcuts={'r': 2},
            )
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
        while True:
            jobs = self.media_store.jobs()
            if not jobs:
                self.run_fzf(['Back'], title='No queued downloads')
                return
            selected = RichPicker(CONSOLE, self.theme).pick(
                jobs,
                format_func=self._download_row,
                title='Live downloads',
                subtitle='Enter: actions · Esc: back',
                shortcuts={'r': 'retry'},
                refresh_items=self.media_store.jobs,
                item_key=lambda job: job['id'],
            )
            if selected is None:
                return
            shortcut = isinstance(selected, PickerAction)
            if shortcut:
                selected = selected.item
            elif isinstance(selected, int):
                selected = jobs[selected]
            latest = next((job for job in self.media_store.jobs() if job['id'] == selected['id']), None)
            if latest is None:
                continue
            if shortcut:
                self._retry_download(latest['id'])
                continue
            status = latest['status']
            retryable = status in ('failed', 'cancelled')
            actions = (
                ['Retry item'] + (['View error'] if latest['error'] else []) + ['Back']
                if retryable
                else ['Cancel item', 'Back']
                if status in ('pending', 'running')
                else ['Back']
            )
            detail = latest['error'] or latest.get('progress') or status
            decision = self.run_fzf(
                actions,
                title=escape(latest['download_title']),
                subtitle=detail[:200],
                shortcuts={'r': 0} if retryable else None,
            )
            if decision == 0 and retryable:
                self._retry_download(latest['id'])
            elif decision == 0 and actions[0] == 'Cancel item':
                self.download_worker.cancel(latest['id'])
            elif decision is not None and actions[decision] == 'View error':
                lines = [
                    part
                    for line in latest['error'].splitlines()
                    for part in textwrap.wrap(line, width=max(20, CONSOLE.width - 8))
                ]
                result = self.run_fzf(
                    lines or ['No diagnostics'],
                    title='Transfer failure',
                    subtitle='Enter / Esc: return',
                    shortcuts={'r': 'retry'},
                )
                if isinstance(result, PickerAction):
                    self._retry_download(latest['id'])

    @staticmethod
    def _download_row(job):
        progress = job.get('progress', '')
        match = re.search(r'(\d+(?:\.\d+)?)%', progress)
        percentage = min(100, max(0, float(match[1]))) if match else None
        if job['status'] == 'completed':
            percentage = 100
        bar = (
            '█' * int(percentage / 10) + '░' * (10 - int(percentage / 10)) + f' {percentage:.0f}%'
            if percentage is not None
            else '··········'
        )
        status = {'pending': 'Queued', 'running': 'Active'}.get(job['status'], job['status'].title())
        width = CONSOLE.width
        title = str(job['download_title']).replace('\n', ' ').replace('\r', ' ')
        title_width = max(8, min(40, width // 3))
        if cell_len(title) > title_width:
            title = set_cell_size(title, title_width - 1) + '…'
        if width < 60:
            amount = f'{percentage:.0f}%' if percentage is not None else '—'
            return f"{escape(title)} │ {status} │ {amount}"
        fixed = f"{title} │ {status} │ {bar}"
        remaining = max(0, width - cell_len(fixed) - 3)
        detail = progress.replace('\n', ' ').replace('\r', ' ')
        if cell_len(detail) > remaining:
            detail = set_cell_size(detail, remaining)
        return escape(fixed + (' │ ' + detail if detail else ''))

    async def search_and_open(self, query: str):
        self.banner()
        theme = self.theme
        self.log("🔄", f"Searching '{query}'...")
        results = await self._load_with_retry(
            "Searching watch history", scraper.search, query,
            empty_message="No matching title found. Retry or return to history.",
        )
        if results:
            await self.show_details_flow(results[0])
        else:
            self.log("⚠️", "No matching show found", theme['error'])
            await asyncio.sleep(1.5)

    def theme_selector(self):
        theme_names = list(THEMES.keys())

        def fmt_theme(name):
            if name == "auto":
                auto_theme = get_auto_theme(refresh=True)
                indicator = " ★" if name == self.config.theme else ""
                return f"[{auto_theme['primary']}]● Auto Detect ({auto_theme['source']}){indicator}[/]"
            colors = THEMES[name]
            indicator = " ★" if name == self.config.theme else ""
            return f"[{colors['primary']}]● {name.title()}{indicator}[/]"

        idx = self.run_fzf(
            [fmt_theme(t) for t in theme_names],
            prompt="🎨 Theme ❯ ",
            title="🎨 Select Theme",
            header="Current theme marked with ★"
        )

        if idx is not None:
            self.config.theme = theme_names[idx]
            self.config.save()
            self.log("✓", f"Theme changed to {theme_names[idx].title()}", f"bold {self.theme['primary']}")
            time.sleep(0.5)

    def show_help(self):
        self.banner()
        theme = self.theme

        CONSOLE.print(f"[bold {theme['primary']}]📚 Commands[/bold {theme['primary']}]")
        CONSOLE.print("[dim]/history     - View recent watch history & continue watching[/dim]")
        CONSOLE.print("[dim]/continue    - Resume your most recent unfinished title[/dim]")
        CONSOLE.print("[dim]/last-eps    - Browse latest added episodes[/dim]")
        CONSOLE.print("[dim]/last-mv     - Browse latest added movies[/dim]")
        CONSOLE.print("[dim]/watch-later - Open your saved Watch Later list[/dim]")
        CONSOLE.print("[dim]/downloads   - Manage background download queue[/dim]")
        CONSOLE.print("[dim]/library     - Play downloaded offline media[/dim]")
        CONSOLE.print("[dim]/settings    - Configure media folder, quality, subtitles[/dim]")
        CONSOLE.print("[dim]/doctor      - Run environment diagnostics[/dim]")
        CONSOLE.print("[dim]/theme       - Change color theme[/dim]")
        CONSOLE.print("[dim]/help        - Show this help[/dim]")
        CONSOLE.print("[dim]/version     - Show version info[/dim]")
        CONSOLE.print("[dim]exit         - Quit the application[/dim]")
        CONSOLE.print("")
        CONSOLE.print(f"[bold {theme['primary']}]🎮 Controls[/bold {theme['primary']}]")
        CONSOLE.print("[dim]↑/↓ (j/k) - Navigate item lists[/dim]")
        CONSOLE.print("[dim]/         - Live filter list items[/dim]")
        CONSOLE.print("[dim]PgUp/PgDn - Page scroll up / down[/dim]")
        CONSOLE.print("[dim]Home/End  - Jump to top / bottom[/dim]")
        CONSOLE.print("[dim]Enter     - Select item[/dim]")
        CONSOLE.print("[dim]Esc (b)   - Go back / Clear filter[/dim]")
        CONSOLE.print("[dim]Type      - Filter results[/dim]")
        CONSOLE.print("")
        input("Press Enter to continue...")

    async def search_flow(self):
        while True:
            self.banner()
            theme = self.theme
            CONSOLE.print(f"[{theme['primary']}]Search [dim]• movies • series • anime[/dim][/{theme['primary']}]")
            custom_prompt = CustomPrompt(CONSOLE, theme)

            if not hasattr(self, 'search_history'):
                self.search_history = []

            query = custom_prompt.ask(
                f"[{theme['accent']}]❯[/{theme['accent']}]",
                history=self.search_history,
            )

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

            self.log("🔄", "Fetching results...")

            with Progress(
                SpinnerColumn("dots2"),
                TextColumn("[progress.description]{task.description}"),
                console=CONSOLE,
                transient=True
            ) as progress:
                progress.add_task(f"[{theme['primary']}]Searching...", total=None)
                try:
                    results = await asyncio.to_thread(scraper.search, query)
                except Exception as e:
                    CONSOLE.print(f"[{theme['error']}]✗ Search failed: {e}[/{theme['error']}]")
                    await asyncio.sleep(2)
                    continue

            if not results:
                self.log("⚠️", "No results found", f"{theme['error']}")
                await asyncio.sleep(1.5)
                continue

            def get_relevance_score(result, search_query):
                title = clean_show_title(result['title']).lower()
                query_lower = search_query.lower().strip()
                norm_title = re.sub(r'[\W_]+', ' ', title).strip()
                norm_query = re.sub(r'[\W_]+', ' ', query_lower).strip()
                rating = float(result.get('metadata', {}).get('rating') or result.get('metadata', {}).get('imdb_rating') or 0)
                similarity = SequenceMatcher(None, norm_title, norm_query).ratio()
                query_tokens = set(norm_query.split())
                title_tokens = set(norm_title.split())

                if norm_query and (norm_title == norm_query or title == query_lower):
                    return (0, -rating, -similarity)
                elif norm_query and (norm_title.startswith(norm_query) or title.startswith(query_lower)):
                    return (1, -similarity, -rating)
                elif norm_query and (norm_query in norm_title or query_lower in title):
                    return (2, -similarity, -rating)
                elif query_tokens and query_tokens.issubset(title_tokens):
                    return (3, -similarity, -rating)
                elif similarity >= 0.72:
                    return (4, -similarity, -rating)
                else:
                    return (5, -similarity, -rating)

            results.sort(key=lambda r: get_relevance_score(r, query))

            def fmt_res(r):
                type_emoji = "🎬" if r['type'] == 'movie' else "📺" if r['type'] == 'series' else "🎌"
                type_color = "blue" if r['type'] == 'movie' else "magenta" if r['type'] == 'series' else "cyan"
                clean_t = clean_show_title(r['title'])

                year = r.get('metadata', {}).get('year')
                year_str = f" ({year})" if year and str(year).lower() != 'n/a' else ""

                rating = r.get('metadata', {}).get('rating') or r.get('metadata', {}).get('imdb_rating')
                rating_str = f" [gold1]★{rating}[/]" if rating else ""

                quality = r.get('metadata', {}).get('quality')
                quality_str = ""
                if quality:
                    q_upper = quality.upper()
                    if 'CAM' in q_upper:
                        q_color = "red"
                    elif 'BLURAY' in q_upper:
                        q_color = "cyan"
                    elif 'WEB' in q_upper:
                        q_color = "green"
                    else:
                        q_color = "white"
                    quality_str = f" [{q_color}][{quality}][/]"

                return f"{type_emoji} [{type_color}]{r['type'].title().ljust(8)}[/]│ {clean_t}{year_str}{rating_str}{quality_str}"

            results_count = len(results)
            result_header = f"📊 {results_count} {'match' if results_count == 1 else 'matches'}"

            selected_show = self.run_fzf_objects(
                results,
                fmt_res,
                prompt="🎯 Select ❯ ",
                title=f"🔍 Results for \"{query}\"",
                header=result_header
            )

            if not selected_show:
                continue

            await self.show_details_flow(selected_show)

    async def show_details_flow(self, show):
        CONSOLE.clear()
        theme = self.theme

        self.log("🔄", "Loading details...")

        with Progress(
            SpinnerColumn("arc"),
            TextColumn("[progress.description]{task.description}"),
            console=CONSOLE,
            transient=True
        ) as progress:
            progress.add_task(f"[{theme['primary']}]Fetching info...", total=None)
            details = await asyncio.to_thread(scraper.get_show_details, show['url'])

        if not details:
            self.log("✗", "Failed to load details", theme['error'])
            await asyncio.sleep(2)
            return NavigationOutcome.BACK

        self.current_show_url = details.get('url') or show.get('url')
        self._display_show_overview(details)

        show_type = details.get('type')
        if show_type == 'movie':
            return await self.handle_movie(details)
        return await self.handle_series(details)

    async def handle_movie(self, details):
        watch_item = {
            'title': details.get('title'),
            'show_title': self.current_show_title,
            'show_url': details.get('url') or getattr(self, 'current_show_url', None),
            'url': details.get('url') or getattr(self, 'current_show_url', None),
            'content_type': 'movie',
        }
        saved = getattr(self, 'watch_later', None) and self.watch_later.contains(watch_item)
        action = self.run_fzf(
            [
                "Play",
                "Download",
                "Remove from Watch Later" if saved else "Add to Watch Later",
                "Back",
            ],
            title=self.current_show_title,
        )
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
                empty_message="No streaming servers available for this movie.",
            )
            if not servers:
                return NavigationOutcome.BACK

        while True:
            try:
                result = await self.select_and_play(servers, self.current_show_title or clean_show_title(details.get('title', '')))
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
            self.current_show_title,
            season_num,
            num_label,
            show_url=getattr(self, "current_show_url", None),
            season_part=(self.current_season or {}).get("season_part"),
        )

        badge = "📺"
        status_str = ""
        if prog:
            if prog.status == WatchStatus.COMPLETED or prog.progress_pct >= 85.0:
                badge = "👁️ "
                status_str = " [green][Watched][/]"
            elif prog.status == WatchStatus.IN_PROGRESS and prog.progress_pct > 0:
                badge = "⏳"
                pos_fmt = format_seconds(prog.time_pos)
                status_str = f" [yellow][{int(prog.progress_pct)}% · {pos_fmt}][/]"

        if str(num_label).lower().startswith(("episode", "ep")):
            ep_text = num_label
        elif str(num_label).replace('.', '', 1).isdigit():
            ep_text = f"Episode {num_label}"
        else:
            ep_text = str(num_label)

        return f"{badge} {ep_text}{status_str}"

    def _display_show_overview(self, details):
        title = clean_show_title(details.get("title", ""))
        if not title:
            title = details.get("title", "Untitled")

        self.current_show_title = title
        theme = self.theme

        metadata = details.get('metadata', {}) or {}
        year = metadata.get('year') or details.get('year')
        rating = metadata.get('rating') or metadata.get('imdb_rating')
        show_type = details.get('type', 'unknown').title()
        quality = metadata.get('quality')
        seasons = len(details.get('seasons', [])) if details.get('seasons') else None

        type_emoji = "🎬" if show_type.lower() == 'movie' else "📺" if show_type.lower() == 'series' else "🎌"

        parts = [f"[bold {theme['primary']}]{escape(title)}[/bold {theme['primary']}]"]

        if year and str(year).lower() not in ('unknown', 'n/a'):
            parts.append(f"📅 {year}")

        if rating:
            parts.append(f"[#daa520]⭐ {rating}[/#daa520]")

        if quality:
            parts.append(f"📀 {quality}")

        parts.append(f"{type_emoji} {show_type}")

        if seasons is not None and show_type.lower() != "movie":
            parts.append(f"📂 {seasons} Seasons")

        info = " • ".join(parts)
        CONSOLE.print(info)

        description = (details.get('description') or details.get('story') or "").strip()
        if description:
            CONSOLE.print(Text(description[:200] + "..." if len(description) > 200 else description, style="dim"))
        CONSOLE.print("")

        self.current_overview_panel = Group(
            Text.from_markup(info),
            Text(description[:200] + "..." if len(description) > 200 else description, style="dim") if description else Text("")
        )

    async def _load_with_retry(self, label, operation, *args, empty_message=None):
        """Retry a recoverable operation without losing the current screen."""
        while True:
            error = None
            try:
                self.banner()
                with CONSOLE.status(Text(label + " …", style=self.theme.get("primary", "cyan")), spinner="dots"):
                    result = await asyncio.to_thread(operation, *args)
                if result:
                    return result
            except Exception as exc:
                error = str(exc)
            message = f"{label}: {error}" if error else (empty_message or "Nothing available.")
            if self.run_fzf(["Retry", "Back"], title=label, subtitle=message,
                            header="Retry this step or return to the previous screen") != 0:
                return None

    async def handle_series(self, details):
        seasons = details.get("seasons", [])
        if not seasons:
            self.run_fzf(["Back"], title="No seasons available")
            return NavigationOutcome.BACK

        season_index = 0
        while True:
            if len(seasons) == 1:
                selected = seasons[0]
            else:
                def fmt_season(s):
                    return s.get("display_label") or f"Season {s.get('season_number', '?')}"

                selected = self.run_fzf_objects(
                    seasons,
                    fmt_season,
                    prompt="📂 Season ❯ ",
                    title=escape(self.current_show_title),
                    header="Select a season · Esc: back",
                    initial_index=season_index,
                )
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
        episodes = await self._load_with_retry(
            "Loading episodes", scraper.fetch_season_episodes, selected_season,
            empty_message=f"No episodes available for {season_label}.",
        )
        if not episodes:
            return NavigationOutcome.BACK

        episodes = sorted(episodes, key=self._episode_sort_key)
        previous_url = None
        if self.current_season and self.current_season.get("url") == selected_season.get("url") and self.current_episodes:
            previous_url = self.current_episodes[self.current_episode_index].get("url")
        self.current_season = selected_season
        self.current_episodes = episodes
        self.current_episode_index = next(
            (i for i, episode in enumerate(episodes) if episode.get("url") == previous_url), 0
        )

        while True:
            action = self.run_fzf(
                ["Play an episode", "Download episodes", "Back"],
                title=season_label,
            )
            if action in (None, 2):
                return NavigationOutcome.BACK
            if action == 1:
                formatted = [self._format_episode_label(ep) for ep in episodes]
                chosen = self.run_fzf(
                    formatted,
                    prompt="⬇️ Download ❯ ",
                    title="Download episodes",
                    subtitle="Tab toggles an episode (in fzf) · Enter confirms · Esc back",
                    multi=True,
                )
                if chosen:
                    await self.download_items([episodes[i] for i in chosen], selected_season)
                continue

            selected = self.run_fzf_objects(
                episodes,
                self._format_episode_label,
                prompt="🎬 Episode ❯ ",
                title=escape(f"{self.current_show_title} — {season_label}"),
                header=f"{len(episodes)} episodes · Esc: back",
                initial_index=self.current_episode_index,
            )
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
        self.banner()
        self._print_focus_panel(self.current_show_title, f"{season_label} · Episode {episode_num}")

        servers = await self._load_with_retry(
            "Loading servers", scraper.fetch_episode_servers, episode,
            empty_message="No streaming servers available for this episode.",
        )
        if not servers:
            return PlaybackOutcome.CANCELLED

        while True:
            try:
                result = await self.select_and_play(
                    servers,
                    f"{season_label} · Episode {episode_num}",
                    is_episode=True,
                    season_num=self.current_season.get("season_number"),
                    ep_num=episode_num,
                    ep_title=episode_title,
                )
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
            self.run_fzf(["Back"], title="Download tools required", subtitle=str(exc))
            return

        managers = list(detected_managers())
        manager = managers[0]
        if len(managers) > 1:
            choice = self.run_fzf(download_engine_labels(managers), title='Download engine')
            if choice is None:
                return
            manager = managers[choice]

        directory = str(Path(getattr(self.config, 'download_dir', None) or (Path.home() / 'Downloads' / 'cenima-cli')).expanduser())
        for item in items:
            ep_num = self._normalize_episode_number(item.get('episode_number') or item.get('display_number')) if season else None
            title = media_title(
                self.current_show_title,
                (season or {}).get('season_number'),
                ep_num,
                (season or {}).get('season_part'),
            )
            self.media_store.enqueue({
                'catalog_url': item['url'],
                'show_url': getattr(self, 'current_show_url', None) or item.get('url'),
                'show_title': self.current_show_title,
                'download_title': title,
                'content_type': 'series' if season else 'movie',
                'season_number': (season or {}).get('season_number'),
                'season_part': (season or {}).get('season_part'),
                'season_label': (season or {}).get('display_label'),
                'episode_number': ep_num,
                'episode_title': item.get('title') if season else None,
                'manager': manager,
                'quality': (self.config.download_quality if isinstance(getattr(self.config, 'download_quality', None), str) else 'best'),
                'speed': (self.config.download_speed if isinstance(getattr(self.config, 'download_speed', None), str) else 'unlimited'),
            }, directory)

        self.download_worker.start()
        choice = self.run_fzf(
            ['Keep browsing', 'Open Downloads'],
            title='Queued in background',
            subtitle='/downloads shows live progress, retry and cancel controls',
        )
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

        def is_vidtube_server(s):
            url_str = str(s.get('embed_url') or '').lower()
            name_str = str(s.get('name') or '').lower()
            return any(k in url_str for k in ['vidtube', 'cdn-video', 'serv-stream']) or 'videotube' in name_str

        vidtube_server = next((s for s in servers if is_vidtube_server(s)), None)
        if vidtube_server and not is_episode:
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
                loop.call_soon_threadsafe(progress.advance, ptask, 1)

            servers = await asyncio.to_thread(checker.probe_all, servers, 16, advance)
            await asyncio.sleep(0)

        total_servers = len(servers)
        live_count = sum(1 for s in servers if s.get("health") == LIVE)
        slow_count = sum(1 for s in servers if s.get("health") == SLOW)

        if total_servers == 1 and live_count == 1:
            selected_server = servers[0]
        else:
            def fmt_server(s):
                name = escape(str(s.get('name') or 'Server'))
                health = s.get('health', OFFLINE)
                if health == LIVE:
                    return f"[green]🟢[/] [green]Online [/] {name}"
                if health == SLOW:
                    return f"[yellow]🟡[/] [yellow]Slow   [/] {name}"
                return f"[red]🔴[/] [dim red]Offline[/] {name}"

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
                title="📡 Select Streaming Server",
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
            await asyncio.sleep(2)
            return PlaybackOutcome.FAILED

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
        choice = self.run_fzf(
            options,
            title="Playback finished" if has_next else "End of season",
            header="Esc: back to episodes",
        )
        if choice is None:
            return NavigationOutcome.EPISODES
        return {
            "Play Next Episode": NavigationOutcome.NEXT,
            "Back to Search": NavigationOutcome.SEARCH,
            "Back to Episodes": NavigationOutcome.EPISODES,
        }[options[choice]]

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
        content_type: str = "movie",
        local_file: bool = False,
    ):
        theme = self.theme
        season = (self.current_season or {}) if ep_num is not None else {}
        prog = self.history.get_progress(
            self.current_show_title,
            season_num,
            ep_num,
            show_url=getattr(self, "current_show_url", None),
            season_part=season.get("season_part"),
        )
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
            subtitle_language=getattr(self.config, "subtitle_language", "ar"),
            local_file=local_file,
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
                episode_url=(
                    self.current_episodes[self.current_episode_index].get("url")
                    if ep_num is not None and self.current_episodes
                    else None
                ),
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

    if not CONSOLE.is_terminal:
        parser.error(
            "Interactive mode needs terminal output. Run cinema-cli directly "
            "in PowerShell or a terminal, without rtk proxy, pipes, or output capture."
        )

    with CONSOLE.screen():
        cli = CinemaCLI()
        cli.no_animation = bool(args.no_animation or os.environ.get('CINEMA_REDUCED_MOTION') == '1')
        try:
            asyncio.run(cli.search_flow())
        except KeyboardInterrupt:
            cli.goodbye()
        finally:
            if hasattr(cli, '_download_worker'):
                cli._download_worker.shutdown()


if __name__ == "__main__":
    main()
