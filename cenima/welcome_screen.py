"""Composable welcome-screen components for the Rich TUI."""

from datetime import datetime
import random
import os

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text

from .cinema_theme import welcome_palette
from .welcome_logo import wordmark_logo


def _panel_box():
    if os.environ.get("TERM", "").lower() == "dumb" or os.environ.get("CINEMA_ASCII") == "1":
        return box.ASCII
    return box.ROUNDED


TIPS = (
    "Press / to search without leaving the welcome screen.",
    "Press c to continue, or d to open Downloads.",
    "Press w to save the current title for later.",
    "Use b or Esc to return to the previous screen.",
)


def greeting(now=None):
    hour = (now or datetime.now()).hour
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"


def progress_bar(progress=0.0, width=20):
    progress = max(0.0, min(100.0, float(progress)))
    filled = int(round(width * progress / 100.0))
    if os.environ.get("TERM", "").lower() == "dumb" or os.environ.get("CINEMA_ASCII") == "1":
        return "#" * filled + ">" + "-" * max(0, width - filled)
    return "━" * filled + "○" + "─" * max(0, width - filled)


def status_badge(label, ready, palette):
    color = palette["mint"] if ready else palette["warning"]
    marker = "✓" if ready else "!"
    return Text(f"[ {marker} {label} ]", style=f"bold {color}")


def welcome_header(version, player_ready, width, theme_name=None, network_ok=None):
    palette = welcome_palette(theme_name)
    logo = wordmark_logo(width, palette)
    header = Table.grid(expand=True)
    header.add_column(justify="center")
    if isinstance(logo, list):
        for line in logo:
            header.add_row(line)
    else:
        header.add_row(logo)
    header.add_row(Text("Search. Watch. Download.", style=f"bold {palette['gold']}",
                        justify="center"))

    badges = Table.grid(expand=True)
    badges.add_column(justify="right")
    badges.add_column(justify="left")
    mpv = status_badge("MPV", player_ready, palette)
    unicode_badge = status_badge("Arabic", True, palette)
    source = status_badge("Sources", network_ok is not False, palette)
    badges.add_row(
        Text(f"v{version}", style=palette["muted"]),
        Text.assemble(mpv, "  ", unicode_badge, "  ", source),
    )
    return Group(header, badges)


def continue_card(unfinished, width, theme_name=None):
    palette = welcome_palette(theme_name)
    if not unfinished:
        title = Text("Start with trending", style=f"bold {palette['text']}", justify="center")
        subtitle = Text("Explore today’s latest movies and series", style=palette["muted"], justify="center")
        action = Text("[Enter] Browse trending", style=f"bold {palette['accent']}", justify="center")
        body = Group(title, subtitle, Text(""), action)
    else:
        episode = ""
        if unfinished.episode_number:
            episode = f"S{unfinished.season_number or '?'}E{unfinished.episode_number}"
        title = Text(unfinished.show_title, style=f"bold {palette['text']}", no_wrap=True,
                     overflow="ellipsis", justify="center")
        episode_text = Text(f"{episode}  ·  {int(unfinished.progress_pct)}%",
                            style=palette["mint"] if width >= 80 else palette["muted"],
                            justify="center")
        remaining = max(0.0, unfinished.duration - unfinished.time_pos)
        bar_width = min(24, max(10, width - 44))
        bar = Text(progress_bar(unfinished.progress_pct, bar_width),
                   style=palette["gold"], justify="center")
        time_text = Text(f"{int(remaining // 60)}m left", style=palette["muted"], justify="center")
        action = Text("[Enter]  Resume", style=f"bold {palette['accent']}", justify="center")
        body = Group(title, episode_text, bar, time_text, action)

    return Panel(body, box=_panel_box(), border_style=palette["accent"],
                 style=f"on {palette['surface_alt']}", padding=(1, 2),
                 title=Text("CONTINUE WATCHING" if unfinished else "DISCOVER",
                            style=f"bold {palette['gold']}"))


ACTION_GROUPS = (
    ("DISCOVER", (
        ("t", "/last-mv", "Trending"),
        ("e", "/last-eps", "Latest episodes"),
        ("m", "/last-mv", "Movies"),
    )),
    ("YOUR SPACE", (
        ("l", "/library", "Library"),
        ("h", "/history", "History"),
        ("w", "/watch-later", "Watch Later"),
        ("c", "/continue", "Continue"),
    )),
    ("SYSTEM", (
        ("d", "/downloads", "Downloads"),
        ("s", "/settings", "Settings"),
        ("o", "/doctor", "Doctor"),
        ("q", "/exit", "Quit"),
    )),
)


def action_grid(selected_command=None, width=120, theme_name=None):
    palette = welcome_palette(theme_name)
    columns = 3 if width >= 80 else 1
    grid = Table.grid(expand=True, padding=(0, 1))
    for _ in range(columns):
        grid.add_column(ratio=1)

    groups = []
    for heading, actions in ACTION_GROUPS:
        table = Table.grid(expand=True)
        table.add_row(Text(heading, style=f"bold {palette['gold']}"))
        for key, command, label in actions:
            selected = selected_command == command
            row = Text()
            row.append(f"[{key.upper()}]", style=f"bold {palette['text']}" if selected
                       else palette["muted"])
            row.append("  " + label,
                       style=f"bold {palette['accent']}" if selected else palette["text"])
            table.add_row(row)
        table.add_row(Text(""))
        groups.append(table)

    if columns == 1:
        for table in groups:
            grid.add_row(table)
    else:
        grid.add_row(*groups)
    return Panel(grid, box=_panel_box(), border_style=palette["border"],
                 style=f"on {palette['surface']}", padding=(0, 1))


def mini_stats(recent_count, watched_count, downloads_running, theme_name=None):
    palette = welcome_palette(theme_name)
    if not recent_count and not downloads_running:
        return Text("")
    parts = []
    if recent_count:
        parts.append(f"{recent_count} recent")
    if watched_count:
        parts.append(f"{watched_count} watched")
    if downloads_running:
        parts.append(f"{downloads_running} downloading")
    return Text("  •  ".join(parts), style=palette["muted"], justify="center")


def footer_hint(theme_name=None, tip=None):
    palette = welcome_palette(theme_name)
    tip = tip or random.choice(TIPS)
    return Text.assemble(
        ("↑↓ navigate  ", palette["muted"]),
        ("Enter ", palette["accent"]),
        ("select  ", palette["muted"]),
        (" / ", palette["accent"]),
        ("search  ", palette["muted"]),
        ("? ", palette["accent"]),
        ("help  ", palette["muted"]),
        ("q ", palette["accent"]),
        ("quit", palette["muted"]),
        "\n",
        (tip, palette["muted"]),
    )


def welcome_screen(version, player_ready, history, unfinished, recent_count,
                   watched_count, downloads_running, width, theme_name=None,
                   network_ok=None, user_name=None, tip=None, selected_command=None):
    palette = welcome_palette(theme_name)
    name = f", {user_name}" if user_name else ""
    greeting_line = Text(f"{greeting()}{name}  -  Tonight is a good night to watch.",
                         style=palette["muted"], justify="center")
    selected_command = selected_command or ("/continue" if unfinished else "/last-mv")
    return Group(
        welcome_header_panel(version, player_ready, width, theme_name, network_ok,
                             user_name),
        welcome_body(unfinished, recent_count, watched_count, downloads_running,
                     width, theme_name, selected_command, tip),
    )


def welcome_header_panel(version, player_ready, width, theme_name=None,
                         network_ok=None, user_name=None):
    palette = welcome_palette(theme_name)
    name = f", {user_name}" if user_name else ""
    greeting_line = Text(f"{greeting()}{name}  -  Tonight is a good night to watch.",
                         style=palette["muted"], justify="center")
    return Panel(
        Group(
            welcome_header(version, player_ready, width, theme_name, network_ok),
            greeting_line,
        ),
        box=_panel_box(),
        border_style=palette["border"],
        style=f"on {palette['background']}",
        padding=(1, 2),
    )


def welcome_body(unfinished, recent_count, watched_count, downloads_running,
                 width, theme_name=None, selected_command=None, tip=None):
    palette = welcome_palette(theme_name)
    return Group(
        continue_card(unfinished, width, theme_name),
        action_grid(selected_command, width, theme_name),
        mini_stats(recent_count, watched_count, downloads_running, theme_name),
        Rule(style=palette["border"]),
        footer_hint(theme_name, tip),
    )
