#!/usr/bin/env python3
import sys
import re
import os
import json
import shutil
import subprocess
import asyncio
import time
import threading
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

from .api import scraper, clean_show_title
from .config import (
    __version__, __author__, __license__,
    CONFIG_DIR, CONFIG_FILE,
    ASCII_ART, GOODBYE_ART,
    THEMES, DEFAULT_THEME, SPINNERS
)

CONSOLE = Console()
GITHUB_REPO_URL = "https://github.com/np4abdou1/cenima-cli"
GITHUB_API_URL = "https://api.github.com/repos/np4abdou1/cenima-cli"


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
        self.load()
    
    def load(self):
        try:
            if CONFIG_FILE.exists():
                data = json.loads(CONFIG_FILE.read_text())
                self.theme = data.get("theme", DEFAULT_THEME)
        except Exception:
            pass
    
    def save(self):
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            CONFIG_FILE.write_text(json.dumps({"theme": self.theme}, indent=2))
        except Exception:
            pass
    
    def get_theme(self) -> Dict:
        return THEMES.get(self.theme, THEMES[DEFAULT_THEME])


class CinemaCLI:
    def __init__(self):
        self.config = Config()
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
        
        info_text.append("👤 ", style=theme['accent'])
        info_text.append("Author    ", style=f"bold {theme['secondary']}")
        info_text.append(f"│ {__author__}\n")
        
        info_text.append("💻 ", style=theme['accent'])
        info_text.append("OS        ", style=f"bold {theme['secondary']}")
        try:
            info_text.append(f"│ {platform.system()} {platform.release()}\n")
        except:
            info_text.append("│ Unknown\n")
        
        info_text.append("🎨 ", style=theme['accent'])
        info_text.append("Theme     ", style=f"bold {theme['secondary']}")
        info_text.append(f"│ {self.config.theme.title()}\n")
        
        shell = os.environ.get('SHELL', 'Unknown')
        if '/' in shell:
            shell = shell.split('/')[-1]
        info_text.append("🐚 ", style=theme['accent'])
        info_text.append("Shell     ", style=f"bold {theme['secondary']}")
        info_text.append(f"│ {shell}\n")
        

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
        
        info_text.append("🔗 ", style=theme['accent'])
        info_text.append("Repository", style=f"bold {theme['secondary']}")
        info_text.append("\n   ", style="")
        # Make URL clickable in supported terminals, show a shorter display text
        short_repo = GITHUB_REPO_URL.replace("https://", "").replace("http://", "")
        # Use from_markup to properly parse the link markup
        link_text = Text.from_markup(f"[link={GITHUB_REPO_URL}]{short_repo}[/link]", style=f"italic {theme['accent']}")
        info_text.append_text(link_text)
        info_text.append("\n")


        
        info_text.append("\n")
        info_text.append("💡 Type ", style="dim")
        info_text.append("/help", style=f"bold {theme['accent']}")
        info_text.append(" for commands", style="dim")
        
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
        numbered = [f"{i}\t{items[i]}" for i in range(len(items))]
        input_str = "\n".join(numbered)
        
        self._show_picker_intro(title, subtitle)
        
        theme = self.theme
        args = [
            'fzf', '--ansi', '--layout=reverse', '--height=80%',
            f'--prompt={prompt}',
            '--delimiter=\t', '--with-nth=2',
            f'--color=fg:-1,bg:-1,hl:{theme["accent"]},fg+:-1,bg+:-1,hl+:{theme["primary"]}',
            f'--color=info:{theme["secondary"]},prompt:{theme["primary"]},pointer:{theme["primary"]}',
            f'--color=marker:{theme["accent"]},spinner:{theme["primary"]},header:{theme["secondary"]}'
        ]
        if header:
            args.append(f'--header={header}')
        
        try:
            process = subprocess.run(args, input=input_str, capture_output=True, text=True)
            CONSOLE.clear()
            
            if process.returncode == 0:
                selected = process.stdout.strip()
                if not selected:
                    return None
                parts = selected.split('\t', 1)
                try:
                    idx = int(parts[0])
                    if 0 <= idx < len(items):
                        return idx
                except ValueError:
                    pass
                return None
            
            if process.returncode in (130, 1):
                return None
            
            return None
        except Exception:
            return None
    
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
        
        choices = [format_func(i) for i in items]
        idx = self.run_fzf(choices, prompt, header=header, title=title, subtitle=subtitle)
        
        if idx is not None:
            return items[idx]
        return None
    
    def handle_command(self, query: str) -> bool:
        cmd = query.strip().lower()
        
        if cmd == "/theme":
            self.theme_selector()
            return True
        elif cmd == "/help":
            self.show_help()
            return True
        elif cmd == "/version":
            CONSOLE.print(f"[bold]cenima-cli[/bold] v{__version__}")
            CONSOLE.print(f"[dim]Author: {__author__} • License: {__license__}[/dim]")
            input("\nPress Enter to continue...")
            return True
        
        return False
    
    def theme_selector(self):
        theme_names = list(THEMES.keys())
        
        def fmt_theme(name):
            colors = THEMES[name]
            indicator = " ★" if name == self.config.theme else ""
            return f"\033[38;2;{self._hex_to_rgb(colors['primary'])}m● {name.title()}{indicator}\033[0m"
        
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
        CONSOLE.print("[dim]/theme   - Change color theme[/dim]")
        CONSOLE.print("[dim]/help    - Show this help[/dim]")
        CONSOLE.print("[dim]/version - Show version info[/dim]")
        CONSOLE.print("[dim]exit     - Quit the application[/dim]")
        CONSOLE.print("")
        CONSOLE.print(f"[bold {theme['primary']}]🎮 Controls[/bold {theme['primary']}]")
        CONSOLE.print("[dim]↑/↓      - Navigate lists[/dim]")
        CONSOLE.print("[dim]Enter    - Select item[/dim]")
        CONSOLE.print("[dim]Esc      - Go back[/dim]")
        CONSOLE.print("[dim]Type     - Filter results[/dim]")
        CONSOLE.print("")
        input("Press Enter to continue...")
    
    async def search_flow(self):
        while True:
            self.banner()
            theme = self.theme
            
            CONSOLE.print(f"[{theme['accent']}]╭[/{theme['accent']}]" + f"[{theme['accent']}]─[/{theme['accent']}]" * 15 + f"[{theme['accent']}] 🔍 Search [/{theme['accent']}]" + f"[{theme['accent']}]─[/{theme['accent']}]" * 15 + f"[{theme['accent']}]╮[/{theme['accent']}]")
            
            CONSOLE.print(f"[{theme['accent']}]│[/{theme['accent']}]", end="")
            query = Prompt.ask(f"[{theme['primary']}]❯[/{theme['primary']}]", console=CONSOLE)
            
            CONSOLE.print(f"[{theme['accent']}]╰[/{theme['accent']}]" + f"[dim {theme['accent']}]─ movies • series • anime ─[/dim {theme['accent']}]" + f"[{theme['accent']}]╯[/{theme['accent']}]")
            
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
                rating = result.get('metadata', {}).get('rating') or result.get('metadata', {}).get('imdb_rating') or 0
                
                if title == query_lower:
                    return (0, -rating)
                elif title.startswith(query_lower):
                    return (1, -rating)
                elif query_lower in title:
                    return (2, -rating)
                else:
                    return (3, -rating)
            
            results.sort(key=lambda r: get_relevance_score(r, query))
            
            def fmt_res(r):
                type_emoji = "🎬" if r['type'] == 'movie' else "📺" if r['type'] == 'series' else "🎌"
                type_color = "34" if r['type'] == 'movie' else "35" if r['type'] == 'series' else "36"
                clean_t = clean_show_title(r['title'])
                
                year = r.get('metadata', {}).get('year')
                year_str = f" ({year})" if year and str(year).lower() != 'n/a' else ""
                
                rating = r.get('metadata', {}).get('rating') or r.get('metadata', {}).get('imdb_rating')
                rating_str = f" \033[38;2;218;165;32m★{rating}\033[0m" if rating else ""
                
                quality = r.get('metadata', {}).get('quality')
                quality_str = ""
                if quality:
                    q_upper = quality.upper()
                    if 'CAM' in q_upper:
                        q_color = "31"
                    elif 'BLURAY' in q_upper:
                        q_color = "36"
                    elif 'WEB' in q_upper:
                        q_color = "32"
                    else:
                        q_color = "37"
                    quality_str = f" \033[{q_color}m[{quality}]\033[0m"
                
                return f"{type_emoji} \033[{type_color}m{r['type'].title().ljust(8)}\033[0m│ {clean_t}{year_str}{rating_str}{quality_str}"
            
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
        patterns = [
            rf'^(?:Episode\s+)?0*{re.escape(str(num_label))}\s*[\.\-:]\s*',
            r'^[\s\.\-:]*\d+\s*[\.\-:]\s*'
        ]
        for pat in patterns:
            t = re.sub(pat, '', t, flags=re.IGNORECASE)
        return t.strip()
    
    def _format_episode_label(self, episode):
        num_raw = episode.get('display_number') or episode.get('episode_number')
        num_label = self._normalize_episode_number(num_raw)
        return f"📺 Episode {num_label}"
    
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
                label = season.get('display_label')
                if not label:
                    num = season.get('season_number', '?')
                    label = f"Season {num}"
                return f"📂 {label}"
            
            season_header = f"📊 {season_count} seasons"
            selected_season = self.run_fzf_objects(
                seasons,
                fmt_season,
                prompt="📂 Season ❯ ",
                title=f"📺 {self.current_show_title}",
                subtitle=season_header,
                header=season_header
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
                header=episode_header
            )
            
            if not selected_ep:
                break
            
            self.current_episode_index = episodes.index(selected_ep)
            
            await self.play_episode(selected_ep, season_label)
    
    async def play_episode(self, episode, season_label):
        theme = self.theme
        
        episode_num = self._normalize_episode_number(episode.get('episode_number') or episode.get('display_number'))
        episode_title = self._clean_episode_title(episode.get('title', ''), episode_num)
        
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
        
        await self.select_and_play(servers, display_context, is_episode=True)
    
    async def select_and_play(self, servers, title_context, is_episode=False):
        theme = self.theme
        
        if not servers:
            self.log("✗", "No servers available", theme['error'])
            await asyncio.sleep(1)
            return
        
        selected_server = servers[0]
        video_url = selected_server.get('video_url')
        referer = selected_server.get('embed_url') or scraper.base_url
        
        if not video_url:
            self.log("✗", "Failed to extract video URL", theme['error'])
            await asyncio.sleep(1)
            return
        
        self.log("▶️", "Launching player...")
        await asyncio.sleep(0.3)
        
        self.play_video(video_url, referer, title_context)
        
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
    
    def play_video(self, url, referer, title=""):
        theme = self.theme
        ua = scraper.session.headers.get("User-Agent", "Mozilla/5.0")
        
        cmd = [
            "mpv",
            url,
            f"--referrer={referer}",
            f"--user-agent={ua}",
            "--vo=gpu",
            "--hwdec=auto",
            "--x11-bypass-compositor=no",
            "--fs",
            "--cache=yes",
            "--cache-secs=300",
            "--demuxer-max-bytes=200MiB",
            "--demuxer-max-back-bytes=100MiB",
            "--cache-pause-initial=yes",
            "--cache-pause-wait=5",
            "--network-timeout=30",
            f"--force-media-title={title}",
            "--osd-level=1",
            "--osd-duration=2000",
            "--really-quiet",
            "--msg-level=all=no"
        ]
        
        CONSOLE.clear()
        
        content = f"""
[bold white]{title}[/bold white]

[{theme['secondary']}]🔄 Buffering video stream...[/{theme['secondary']}]
[dim]Connection established. Waiting for buffer fill...[/dim]

[bold {theme['primary']}]Controls:[/bold {theme['primary']}]
• [bold]q[/bold]     Quit player
• [bold]Space[/bold] Pause/Resume
• [bold]f[/bold]     Toggle Fullscreen
• [bold]m[/bold]     Mute
"""
        CONSOLE.print(Panel(
            content.strip(),
            title=f"[bold {theme['primary']}]🎬  NOW PLAYING[/bold {theme['primary']}]",
            border_style=theme['primary'],
            padding=(1, 2)
        ))
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True)
            
            CONSOLE.clear()
            if result.returncode == 0:
                self.log("✓", "Playback finished", f"bold {theme['primary']}")
            else:
                pass
                
        except Exception as e:
            self.log("✗", f"Player error: {e}", theme['error'])
            input("Press Enter to continue...")


def main():
    cli = CinemaCLI()
    try:
        asyncio.run(cli.search_flow())
    except KeyboardInterrupt:
        cli.goodbye()
        sys.exit(0)


if __name__ == "__main__":
    main()
