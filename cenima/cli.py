#!/usr/bin/env python3
import sys
import re
import os
import json
import shutil
import subprocess
import asyncio
import time
import platform
import urllib.request
from pathlib import Path
from typing import List, Any, Optional, Dict
from rich.console import Console, Group
from rich.prompt import Prompt
from rich.text import Text
from rich.progress import Progress, SpinnerColumn, TextColumn, BarColumn, TimeElapsedColumn
from rich.live import Live
from rich.table import Table
from rich.markup import escape
from rich.panel import Panel
from rich.style import Style
from rich.progress_bar import ProgressBar

from .scraper import TopCinemaScraper, clean_show_title
from .history import HistoryManager
from .models import WatchStatus
from .mpv import run_mpv_monitored
from .picker import RichPicker, CustomPrompt
from .accent import get_auto_theme
from .health import ServerHealthChecker, LIVE, SLOW, OFFLINE
from .processor import ServerProcessor

scraper = TopCinemaScraper()
from .config import (
    __version__, __author__, __license__,
    CONFIG_DIR, CONFIG_FILE,
    ASCII_ART, GOODBYE_ART,
    THEMES, DEFAULT_THEME, SPINNERS
)

CONSOLE = Console()
GITHUB_REPO_URL = "https://github.com/np4abdou1/cenima-cli"
GITHUB_API_URL = "https://api.github.com/repos/np4abdou1/cenima-cli"


def format_seconds(secs: float) -> str:
    if not secs or secs < 0:
        return "00:00"
    m, s = divmod(int(secs), 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def get_github_stars() -> int:

    cache_file = CONFIG_DIR / "stars_cache.json"
    cache_duration = 3600
    
    if cache_file.exists():
        try:
            data = json.loads(cache_file.read_text())
            if time.time() - data.get('timestamp', 0) < cache_duration:
                return data.get('stars', 0)
        except Exception:
            pass
    
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
        # Player options
        self.simple_mode = True   # Launch mpv minimally (mirrors the known-good command)
        self.send_headers = False # Inject --referrer/--user-agent when simple_mode is on
        self.referer = ""
        self.user_agent = ""
        self.fullscreen = True
        self.hwdec = False
        self.cache_pause = False
        self.load()
    
    def load(self):
        try:
            if CONFIG_FILE.exists():
                data = json.loads(CONFIG_FILE.read_text())
                self.theme = data.get("theme", DEFAULT_THEME)
                self.simple_mode = bool(data.get("simple_mode", True))
                self.send_headers = bool(data.get("send_headers", False))
                self.referer = data.get("referer", "")
                self.user_agent = data.get("user_agent", "")
                self.fullscreen = bool(data.get("fullscreen", True))
                self.hwdec = bool(data.get("hwdec", False))
                self.cache_pause = bool(data.get("cache_pause", False))
        except Exception:
            pass
    
    def save(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps(self.to_dict(), indent=2))
        except Exception:
            pass
    
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
        }
    
    def get_theme(self) -> Dict:
        if self.theme == "auto":
            return get_auto_theme()
        return THEMES.get(self.theme, get_auto_theme())


class CinemaCLI:
    def __init__(self):
        self.config = Config()
        self.history = HistoryManager()
        self.picker_hint = "↑↓ navigate • Enter select • Esc back"
        self.current_show_title = ""
        self.current_overview_panel = None
        self.current_episodes = []
        self.current_episode_index = 0
        self.current_season = None
        self.github_stars = None  # Lazy load
        self.check_deps()

    
    @property
    def theme(self) -> Dict:
        return self.config.get_theme()
    
    def check_deps(self):
        missing = []
        if not shutil.which("fzf"):
            missing.append("fzf")
        if not shutil.which("mpv"):
            missing.append("mpv")
        
        if missing:
            CONSOLE.print(f"[bold red]✗ Missing dependencies:[/bold red] {', '.join(missing)}")
            CONSOLE.print("[dim]Install with: sudo apt install fzf mpv[/dim]")
            sys.exit(1)
    
    def log(self, emoji: str, message: str, style: str = "dim"):
        CONSOLE.print(f"[{style}]{emoji} {message}[/{style}]")
    
    def banner(self):
        CONSOLE.clear()
        theme = self.theme
        
        grid = Table.grid(padding=(0, 2))
        grid.add_column()
        grid.add_column(justify="left", vertical="middle")
        
        art = Text(ASCII_ART, style=theme["primary"])
        
        info_text = Text()
        info_text.append("╭────────────────────╮\n", style=theme["accent"])
        info_text.append("│  ", style=theme["accent"])
        info_text.append("cenima-cli", style=f"bold {theme['primary']}")
        info_text.append(f"  v{__version__}", style="dim")
        info_text.append("  │\n", style=theme["accent"])
        info_text.append("╰────────────────────╯\n", style=theme["accent"])
        info_text.append("\n")
        # Calculate stats
        history_items = self.history.get_recent_history(limit=5000)
        watched_eps = sum(1 for i in history_items if i.episode_number and (i.status == WatchStatus.COMPLETED or i.progress_pct >= 85.0))
        watched_movies = sum(1 for i in history_items if i.content_type == "movie" and (i.status == WatchStatus.COMPLETED or i.progress_pct >= 85.0))
        total_watch_time = sum(i.time_pos for i in history_items)
        hours = int(total_watch_time // 3600)
        
        info_text.append("🎬 ", style=theme['accent'])
        info_text.append("Movies    ", style=f"bold {theme['secondary']}")
        info_text.append(f"│ {watched_movies} Watched\n")
        
        info_text.append("📺 ", style=theme['accent'])
        info_text.append("Episodes  ", style=f"bold {theme['secondary']}")
        info_text.append(f"│ {watched_eps} Watched\n")
        
        info_text.append("⏱️  ", style=theme['accent'])
        info_text.append("Time Wasted", style=f"bold {theme['secondary']}")
        info_text.append(f"│ {hours} hours\n")
        
        info_text.append("💻 ", style=theme['accent'])
        info_text.append("OS        ", style=f"bold {theme['secondary']}")
        try:
            info_text.append(f"│ {platform.system()} {platform.release()}\n")
        except:
            info_text.append("│ Unknown\n")
        
        info_text.append("🎨 ", style=theme['accent'])
        info_text.append("Theme     ", style=f"bold {theme['secondary']}")
        if self.config.theme == "auto":
            source = theme.get("source", "System")
            info_text.append(f"│ Auto ({source})\n")
        else:
            info_text.append(f"│ {self.config.theme.title()}\n")


        if self.github_stars is None:
            self.github_stars = get_github_stars()
        
        info_text.append("⭐ ", style="#daa520")
        info_text.append("Stars     ", style=f"bold {theme['secondary']}")
        info_text.append("│ ", style="")
        if self.github_stars > 0:
            info_text.append(str(self.github_stars), style="bold #daa520")
            info_text.append(" (Thank you!)\n", style="dim")
        else:
            info_text.append("0", style="#daa520")
            info_text.append(" (Star us!)\n", style="dim")
        info_text.append("\n")
        info_text.append("💡 Global Shortcuts\n", style=f"bold {theme['primary']}")
        info_text.append("   b/Esc: Back    q/exit: Quit\n", style="dim")
        info_text.append("   h/history: History     c/theme: Settings\n", style="dim")
        
        grid.add_row(art, info_text)
        
        CONSOLE.print(grid)
        CONSOLE.print("")
    
    def goodbye(self):
        CONSOLE.clear()
        theme = self.theme
        CONSOLE.print(Text(GOODBYE_ART, style=theme["primary"]))
        CONSOLE.print(f"[{theme['secondary']}]Thanks for using cenima-cli![/{theme['secondary']}]")
    
    def _show_picker_intro(self, title: Optional[str] = None, subtitle: Optional[str] = None):
        CONSOLE.clear()
        if not title and not subtitle:
            return
        theme = self.theme
        if title:
            CONSOLE.print(f"[bold {theme['primary']}]{escape(title)}[/bold {theme['primary']}]")
        if subtitle:
            CONSOLE.print(f"[dim]{escape(subtitle)}[/dim]")
        CONSOLE.print("")
    
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
        subtitle: Optional[str] = None
    ) -> Optional[int]:
        if not items:
            return None

        # 1. Native fzf subprocess with ANSI color support
        if shutil.which("fzf"):
            try:
                cmd = [
                    "fzf",
                    "--height=15",
                    "--layout=reverse",
                    "--border",
                    "--prompt=" + prompt,
                    "--pointer=❯",
                    "--info=inline",
                    "--ansi"
                ]
                
                def to_plain(text_str: str) -> str:
                    try:
                        return Text.from_markup(text_str).plain
                    except Exception:
                        return str(text_str)

                def to_ansi(text_str: str) -> str:
                    try:
                        temp_c = Console(force_terminal=True, color_system="truecolor")
                        with temp_c.capture() as capture:
                            temp_c.print(Text.from_markup(text_str), end="")
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

                if header_parts:
                    cmd.append(f"--header={' · '.join(header_parts)}")

                # Convert Rich items to ANSI escape sequences for fzf
                ansi_items = [to_ansi(str(item)) for item in items]
                input_data = "\n".join([f"{i+1}. {item}" for i, item in enumerate(ansi_items)])
                
                proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=None,  # fzf renders TUI on stderr!
                    text=True
                )
                out, _ = proc.communicate(input=input_data)
                
                if proc.returncode == 0 and out.strip():
                    selected_line = out.strip()
                    match = re.match(r"^(\d+)\.", selected_line)
                    if match:
                        idx = int(match.group(1)) - 1
                        if 0 <= idx < len(items):
                            return idx
                elif proc.returncode == 130:
                    # User pressed ESC or Ctrl+C to cancel
                    return None
            except Exception:
                pass

        # 2. Fallback to RichPicker if fzf is absent or fails
        picker = RichPicker(CONSOLE, self.theme)
        display_title = title or ""
        if subtitle:
            display_title += f"\n[dim]{subtitle}[/dim]"
        if header:
            display_title += f"\n[bold]{header}[/bold]"
            
        return picker.pick(
            items, 
            format_func=lambda x: str(x), 
            title=display_title
        )

    def run_fzf_objects(
        self,
        items: List[Any],
        format_func,
        prompt: str = "❯ ",
        title: Optional[str] = None,
        subtitle: Optional[str] = None,
        header: Optional[str] = None
    ) -> Optional[Any]:
        if not items:
            return None

        formatted_items = [format_func(item) for item in items]
        idx = self.run_fzf(
            formatted_items,
            prompt=prompt,
            header=header,
            title=title,
            subtitle=subtitle
        )
        if idx is not None and 0 <= idx < len(items):
            return items[idx]
        return None
    
    def handle_command(self, query: str) -> bool:
        cmd = query.strip().lower()
        
        if cmd == "/theme":
            self.theme_selector()
            return True
        elif cmd in ("/history", "/recent"):
            self.show_history_menu()
            return True
        elif cmd == "/help":
            self.show_help()
            return True
        elif cmd == "/version":
            CONSOLE.print(f"[bold]cenima-cli[/bold] v{__version__}")
            CONSOLE.print(f"[dim]Author: {__author__} • License: {__license__}[/dim]")
            input("\nPress Enter to continue...")
            return True
        elif cmd == "/back":
            return True
        
        return False

    def show_history_menu(self):
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
                asyncio.run(self.search_and_open(selected.show_title))
            elif choice_idx == 1:
                self.history.clear_history()
                self.log("✓", "History cleared", theme['primary'])
                time.sleep(1)

    async def search_and_open(self, query: str):
        self.banner()
        theme = self.theme
        self.log("🔄", f"Searching '{query}'...")
        results = await asyncio.to_thread(scraper.search, query)
        if results:
            await self.show_details_flow(results[0])
        else:
            self.log("⚠️", "No matching show found", theme['error'])
            await asyncio.sleep(1.5)
    
    def theme_selector(self):
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
            title="🎨 Select Theme",
            header="Current theme marked with ★"
        )
        
        if idx is not None:
            self.config.theme = theme_names[idx]
            self.config.save()
            self.log("✓", f"Theme changed to {theme_names[idx].title()}", f"bold {self.theme['primary']}")
            time.sleep(0.5)
    
    def _hex_to_rgb(self, hex_color: str) -> str:
        hex_color = hex_color.lstrip('#')
        r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        return f"{r};{g};{b}"
    
    def show_help(self):
        self.banner()
        theme = self.theme
        
        CONSOLE.print(f"[bold {theme['primary']}]📚 Commands[/bold {theme['primary']}]")
        CONSOLE.print("[dim]/history - View recent watch history & continue watching[/dim]")
        CONSOLE.print("[dim]/theme   - Change color theme[/dim]")
        CONSOLE.print("[dim]/help    - Show this help[/dim]")
        CONSOLE.print("[dim]/version - Show version info[/dim]")
        CONSOLE.print("[dim]exit     - Quit the application[/dim]")
        CONSOLE.print("")
        CONSOLE.print(f"[bold {theme['primary']}]🎮 Controls[/bold {theme['primary']}]")
        CONSOLE.print("[dim]↑/↓ (j/k) - Navigate item lists[/dim]")
        CONSOLE.print("[dim]/         - Live filter list items[/dim]")
        CONSOLE.print("[dim]PgUp/PgDn - Page scroll up / down[/dim]")
        CONSOLE.print("[dim]Home/End  - Jump to top / bottom[/dim]")
        CONSOLE.print("[dim]Enter     - Select item[/dim]")
        CONSOLE.print("[dim]Esc (b)   - Go back / Clear filter[/dim]")
        CONSOLE.print("[dim]Type     - Filter results[/dim]")
        CONSOLE.print("")
        input("Press Enter to continue...")
    
    async def search_flow(self):
        while True:
            self.banner()
            theme = self.theme
            
            CONSOLE.print(f"[{theme['primary']}]🔍 Search [dim]• movies • series • anime[/dim][/{theme['primary']}]")
            custom_prompt = CustomPrompt(CONSOLE, theme)
            
            if not hasattr(self, 'search_history'):
                self.search_history = []
                
            query = custom_prompt.ask(f"[{theme['accent']}]❯[/{theme['accent']}]", history=self.search_history)
            
            if not query:
                continue
                
            if not query.startswith('/'):
                if not self.search_history or self.search_history[-1] != query:
                    self.search_history.append(query)
            
            if query.lower() in ('exit', 'quit', '/exit', '/quit'):
                self.goodbye()
                sys.exit(0)
            
            if query.startswith('/'):
                if self.handle_command(query):
                    continue
            
            self.log("🔄", "Fetching results...")
            
            with Progress(
                SpinnerColumn("dots2"),
                TextColumn("[progress.description]{task.description}"),
                console=CONSOLE,
                transient=True
            ) as progress:
                task = progress.add_task(f"[{theme['primary']}]Searching...", total=None)
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
                query_lower = search_query.lower()
                norm_title = re.sub(r'[^a-z0-9]+', ' ', title).strip()
                norm_query = re.sub(r'[^a-z0-9]+', ' ', query_lower).strip()
                rating = result.get('metadata', {}).get('rating') or result.get('metadata', {}).get('imdb_rating') or 0
                
                if norm_title == norm_query or title == query_lower:
                    return (0, -rating)
                elif norm_title.startswith(norm_query) or title.startswith(query_lower):
                    return (1, -rating)
                elif norm_query in norm_title or query_lower in title:
                    return (2, -rating)
                else:
                    return (3, -rating)
            
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
        self.banner()
        theme = self.theme
        
        self.log("🔄", "Loading details...")
        
        with Progress(
            SpinnerColumn("arc"),
            TextColumn("[progress.description]{task.description}"),
            console=CONSOLE,
            transient=True
        ) as progress:
            task = progress.add_task(f"[{theme['primary']}]Fetching info...", total=None)
            details = await asyncio.to_thread(scraper.get_show_details, show['url'])
        
        if not details:
            self.log("✗", "Failed to load details", theme['error'])
            await asyncio.sleep(2)
            return
        
        self._display_show_overview(details)
        
        show_type = details.get('type')
        if show_type == 'movie':
            await self.handle_movie(details)
        else:
            await self.handle_series(details)
    
    async def handle_movie(self, details):
        servers = details.get('servers', [])
        
        if not servers:
            self.log("⚠️", "No servers found", self.theme['error'])
            await asyncio.sleep(2)
            return
        
        await self.select_and_play(servers, self.current_show_title or clean_show_title(details.get('title', '')))
    
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
        prog = self.history.get_progress(self.current_show_title, season_num, num_label)
        
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
        
        # Display only episode number
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
    
    async def handle_series(self, details):
        seasons = details.get('seasons', [])
        theme = self.theme
        
        if not seasons:
            self.log("✗", "No seasons found", theme['error'])
            await asyncio.sleep(2)
            return
        
        season_count = len(seasons)
        
        if season_count == 1:
            self.log("📂", "Auto-selecting Season 1")
            await asyncio.sleep(0.3)
            await self.handle_season_episodes(seasons[0], seasons)
            return
        
        while True:
            def fmt_season(season):
                num = season.get('season_number', 1)
                label = season.get('display_label') or f"Season {num}"
                watched_cnt, _ = self.history.get_season_stats(self.current_show_title, num)
                stats_str = f" [green](👁️ {watched_cnt} watched)[/]" if watched_cnt > 0 else ""
                return f"📂 {label}{stats_str}"
            
            season_header = f"📊 {season_count} seasons"
            selected_season = self.run_fzf_objects(
                seasons,
                fmt_season,
                prompt="📂 Season ❯ ",
                title=f"📺 {self.current_show_title}",
                subtitle=season_header,
                header=None
            )
            
            if not selected_season:
                break
            
            await self.handle_season_episodes(selected_season, seasons)
    
    async def handle_season_episodes(self, selected_season, all_seasons=None):
        self.banner()
        theme = self.theme
        
        if self.current_overview_panel:
            CONSOLE.print(self.current_overview_panel)
        
        season_label = selected_season.get('display_label') or f"Season {selected_season.get('season_number', '?')}"
        self._print_focus_panel(season_label)
        
        self.log("🔄", "Fetching episodes...")
        
        with Progress(
            SpinnerColumn("bouncingBar"),
            TextColumn("[progress.description]{task.description}"),
            console=CONSOLE,
            transient=True
        ) as progress:
            task = progress.add_task(f"[{theme['primary']}]Loading episodes...", total=None)
            episodes = await asyncio.to_thread(scraper.fetch_season_episodes, selected_season)
        
        if not episodes:
            self.log("⚠️", f"No episodes found for {season_label}", theme['error'])
            await asyncio.sleep(2)
            return
        
        try:
            episodes.sort(key=self._episode_sort_key)
        except Exception:
            pass
        
        self.log("✓", f"Found {len(episodes)} episodes", f"bold {theme['primary']}")
        await asyncio.sleep(0.3)
        
        self.current_episodes = episodes
        self.current_season = selected_season
        
        episode_count = len(episodes)
        while True:
            episode_header = f"📊 {episode_count} episodes"
            selected_ep = self.run_fzf_objects(
                episodes,
                self._format_episode_label,
                prompt="📺 Episode ❯ ",
                title=f"📺 {self.current_show_title} — {season_label}",
                subtitle=episode_header,
                header=None
            )
            
            if not selected_ep:
                break
            
            self.current_episode_index = episodes.index(selected_ep)
            
            await self.play_episode(selected_ep, season_label)
    
    async def play_episode(self, episode, season_label):
        theme = self.theme
        
        episode_num = self._normalize_episode_number(episode.get('episode_number') or episode.get('display_number'))
        episode_title = self._clean_episode_title(episode.get('title', ''), episode_num)
        season_num = self.current_season.get('season_number') if self.current_season else None
        
        self.banner()
        if self.current_overview_panel:
            CONSOLE.print(self.current_overview_panel)
        
        subtitle = f"Episode {episode_num}"
        if episode_title:
            subtitle += f" · {episode_title}"
        self._print_focus_panel(season_label, subtitle)
        
        self.log("🔄", "Resolving server...")
        
        with Progress(
            SpinnerColumn("star"),
            TextColumn("[progress.description]{task.description}"),
            console=CONSOLE,
            transient=True
        ) as progress:
            task = progress.add_task(f"[{theme['primary']}]Getting stream...", total=None)
            servers = await asyncio.to_thread(scraper.fetch_episode_servers, episode)
        
        if not servers:
            self.log("✗", "No servers found", theme['error'])
            await asyncio.sleep(1)
            return
        
        display_context = f"{season_label} · Episode {episode_num}"
        if episode_title:
            display_context += f" · {episode_title}"
        
        await self.select_and_play(
            servers,
            display_context,
            is_episode=True,
            season_num=season_num,
            ep_num=episode_num,
            ep_title=episode_title
        )
    
    async def select_and_play(self, servers, title_context, is_episode=False, season_num=None, ep_num=None, ep_title=None):
        theme = self.theme
        
        if not servers:
            self.log("✗", "No servers available", theme['error'])
            await asyncio.sleep(1)
            return

        # Probe every server up-front so the picker can show which ones are
        # actually streaming right now. Verdicts are cached per embed URL, so
        # coming back to the same episode is instant.
        primary_color = theme.get('primary', 'cyan')
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
            return
        
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
            return

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
        
        self.play_video(
            video_url,
            referer=referer,
            title=title_context,
            season_num=season_num,
            ep_num=ep_num,
            ep_title=ep_title,
            content_type="series" if is_episode else "movie"
        )
        
        if is_episode and self.current_episodes:
            await self.prompt_next_episode()
    
    async def prompt_next_episode(self):
        theme = self.theme
        
        if self.current_episode_index + 1 >= len(self.current_episodes):
            self.log("🏁", "Season complete!", f"bold {theme['primary']}")
            await asyncio.sleep(1)
            return
        
        next_ep = self.current_episodes[self.current_episode_index + 1]
        next_num = self._normalize_episode_number(next_ep.get('episode_number') or next_ep.get('display_number'))
        
        self.banner()
        CONSOLE.print(f"[bold {theme['primary']}]⏭️  Next Episode Available[/bold {theme['primary']}]")
        CONSOLE.print(f"[dim]Episode {next_num}[/dim]")
        CONSOLE.print("")
        
        options = ["▶️  Play Next Episode", "📋 Back to Episodes", "🏠 Back to Search"]
        idx = self.run_fzf(
            options,
            prompt="❯ ",
            title="What's next?",
            header="Choose an action"
        )
        
        if idx == 0:
            self.current_episode_index += 1
            season_label = self.current_season.get('display_label') or f"Season {self.current_season.get('season_number', '?')}"
            await self.play_episode(next_ep, season_label)
        elif idx == 1:
            pass
    
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
        prog = self.history.get_progress(self.current_show_title, season_num, ep_num)
        start_time = 0.0

        if prog and prog.time_pos > 10.0 and prog.status != WatchStatus.COMPLETED:
            pos_fmt = format_seconds(prog.time_pos)
            pct_int = int(prog.progress_pct)
            options = [
                f"▶️  Resume from {pos_fmt} ({pct_int}%)",
                "🔄  Start from beginning (00:00)"
            ]
            choice = self.run_fzf(
                options,
                prompt="▶️ Playback ❯ ",
                title=f"▶️ Saved Progress: {pos_fmt}",
                header=f"Resume {title} at {pos_fmt}?"
            )
            if choice == 0:
                start_time = prog.time_pos

        CONSOLE.clear()
        self.log("🎬", f"Playing {title}...", f"bold {theme['primary']}")

        rc, mpv_state = run_mpv_monitored(
            url=url,
            title=title or self.current_show_title or "cenima-cli",
            start_time=start_time,
            referer=referer,
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
                episode_url=url
            )
            if saved_prog.status == WatchStatus.COMPLETED:
                self.log("👁️", f"Marked as Watched ({int(saved_prog.progress_pct)}%)", f"bold {theme['primary']}")
            else:
                pos_fmt = format_seconds(saved_prog.time_pos)
                self.log("💾", f"Saved progress at {pos_fmt} ({int(saved_prog.progress_pct)}%)", theme['secondary'])

        if rc in (0, None):
            self.log("✓", "Playback finished", f"bold {theme['primary']}")
        else:
            self.log("⚠️", f"MPV exited with code {rc}", theme["error"])



def main():
    cli = CinemaCLI()
    try:
        asyncio.run(cli.search_flow())
    except KeyboardInterrupt:
        cli.goodbye()
        sys.exit(0)


if __name__ == "__main__":
    main()
