"""Shared terminal design system. Renderers are pure and never fetch provider data."""
from dataclasses import dataclass
from typing import Tuple

from rich import box
from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.table import Table
from rich.text import Text


PALETTE = {
    "primary": "#22d3ee", "accent": "#a78bfa", "secondary": "#94a3b8",
    "text": "#e2e8f0", "muted": "#94a3b8", "border": "#334155",
    "surface": "#0f172a", "selected": "#164e63", "success": "#34d399",
    "warning": "#fbbf24", "error": "#fb7185", "source": "Cinema",
}


def theme_tokens(theme=None):
    return {**PALETTE, **(theme or {})}


def plain(value):
    """Keep untrusted catalog text on one line, including terminal control bytes."""
    return " ".join("".join(c if c.isprintable() or c.isspace() else " "
                            for c in str(value)).split())


def clipped(value, width):
    text = Text(plain(value), overflow="ellipsis", no_wrap=True)
    text.truncate(max(1, width), overflow="ellipsis")
    return text


@dataclass(frozen=True)
class PickerRow:
    cells: Tuple[str, ...]
    headers: Tuple[str, ...] = ("Title", "Type", "Quality / Year", "Servers")

    def __str__(self):
        return "  ".join(self.cells)


def result_row(result, title):
    meta = {**result, **(result.get("metadata") or {})}
    quality = meta.get("quality") or "Unknown"
    year = meta.get("year") or "—"
    servers = str(len(result["servers"])) if "servers" in result else "On demand"
    return PickerRow((plain(title or result.get("title", "Untitled")),
                      plain(result.get("type", "unknown").title()),
                      f"{plain(quality)} / {year}", servers))


def breadcrumb(parts, theme=None, width=80):
    t = theme_tokens(theme)
    result = Text(no_wrap=True, overflow="ellipsis")
    for index, part in enumerate(parts):
        if index:
            result.append("  ›  ", style=t["border"])
        budget = max(8, (width - 5 * (len(parts)-1)) // max(1, len(parts)))
        segment = clipped(part, budget)
        segment.stylize(t["primary"] if index == len(parts)-1 else t["muted"])
        result.append_text(segment)
    return result


def app_bar(version, theme=None):
    t = theme_tokens(theme)
    bar = Table.grid(expand=True)
    bar.add_column(ratio=1)
    bar.add_column(justify="right")
    bar.add_row(Text("▷  CINEMA CLI", style=f"bold {t['primary']}"),
                Text(f"v{version}  /  CINEMA IN YOUR TERMINAL", style=t["muted"]))
    return Group(bar, Rule(style=t["border"]))


def _unicode_supported():
    import os
    if os.environ.get("NO_COLOR") or os.environ.get("TERM", "").lower() == "dumb":
        return False
    return os.environ.get("CINEMA_ASCII") != "1"


def _welcome_box():
    from rich import box
    return box.ASCII if not _unicode_supported() else box.ROUNDED


def landing(version, player_ready, history, width, theme=None, downloads_running=0,
            user_name='', welcome_theme='midnight', selected_command=None,
            network_ok=None, tip=None):
    t = theme_tokens(theme)
    accent = t["error"]
    badge = t["success"]
    identity = Text("C I N E M A   C L I", style=f"bold {t['text']}", justify="center")
    divider = Text("─── ◆ ───" if _unicode_supported() else "--- + ---",
                   style=t["border"], justify="center")
    tagline = Text("Direct Terminal Cinema Experience",
                   style=accent, justify="center")
    status = Text.assemble(
        ("[ ", t["muted"]),
        (f"v{version}", badge),
        (" ]  [ ", t["muted"]),
        ("MPV ready" if player_ready else "MPV missing",
         badge if player_ready else t["warning"]),
        (" ]  [ ", t["muted"]),
        ("Arabic Unicode", badge),
        (" ]", t["muted"]),
        justify="center",
    )
    completed = sum(1 for item in history
                    if getattr(getattr(item, 'status', None), 'value', '') == 'completed')
    summary = Text(" ")
    summary.append(str(len(history)), style=f"bold {t['text']}")
    summary.append(" recent titles ", style=t["muted"])
    summary.append("·", style=t["border"])
    summary.append(f" {completed} ", style=f"bold {t['text']}")
    summary.append("watched ", style=t["muted"])
    summary.append("·", style=t["border"])
    summary.append(" Resume from history", style=t["muted"])
    summary.justify = "center"

    groups = (
        ("DISCOVER", (
            ('/', 'Search'),
            ('/le', 'Latest episodes'),
            ('/lm', 'Movies'),
            ('/t', 'Trending'),
        )),
        ("LIBRARY", (
            ('/l', 'Library'),
            ('/h', 'History'),
            ('/w', 'Watch later'),
            ('/c', 'Continue'),
        )),
        ("MANAGE", (
            ('/d', 'Downloads'),
            ('/s', 'Settings'),
        )),
        ("SYSTEM", (
            ('/do', 'Doctor'),
            ('/theme', 'Theme'),
            ('Q', 'Quit'),
        )),
    )
    column_count = 4 if width >= 96 else 2
    grid = Table.grid(expand=True, padding=(0, 3))
    for _ in range(column_count):
        grid.add_column(ratio=1)
    rendered_groups = []
    for heading, shortcuts in groups:
        column = Table.grid(expand=True)
        column.add_row(Text(heading, style=f"bold {t['muted']}"))
        for key, label in shortcuts:
            item = Text()
            item.append(f"[{key}]", style=f"bold {accent}")
            item.append(" " + label, style=t["muted"])
            column.add_row(item)
        rendered_groups.append(column)
    if column_count == 2:
        grid.add_row(rendered_groups[0], rendered_groups[1])
        grid.add_row(rendered_groups[2], rendered_groups[3])
    else:
        grid.add_row(*rendered_groups)

    small_tip = Text(tip if tip is not None else "Tip: use /w to save titles for later",
                     style=t["muted"], justify="center")
    contents = [
        identity,
        divider,
        tagline,
        status,
        Text(""),
        summary,
        divider,
        grid,
        Text(""),
        small_tip,
    ]
    return Panel(
        Group(*contents),
        box=_welcome_box(),
        border_style=t["border"],
        style="on #090d18",
        padding=(1, 2),
        subtitle=Text("DISCOVER  /  WATCH  /  DOWNLOAD", style=t["accent"]),
    )


def callout(title, message, level="info", theme=None):
    t = theme_tokens(theme)
    color = t.get(level, t["primary"])
    return Panel(Text(plain(message), style=t["text"]), title=Text(plain(title), style=f"bold {color}"),
                 title_align="left", border_style=color, box=box.ROUNDED, padding=(0, 1), height=4)


def key_hints(multi, filtering, theme):
    result = Text(no_wrap=True, overflow="ellipsis")
    for index, (key, label) in enumerate([
        ("Enter", "Done" if multi else "Select"),
        ("Tab", "Toggle") if multi else ("↑↓", "Navigate"),
        ("/", "Filter"), ("Esc", "Clear filter" if filtering else "Back")]):
        if index:
            result.append("  |  ", style=theme["border"])
        result.append(f"[{key}]", style=theme["primary"])
        result.append(" " + label, style=theme["muted"])
    if not filtering:
        result.append(' / [b] Back', style=theme['muted'])
    return result


def details_card(details, title, width, theme=None):
    t = theme_tokens(theme)
    meta = {**details, **(details.get("metadata") or {})}
    genres = meta.get("genres") or []
    genres = ", ".join(genres) if isinstance(genres, list) else str(genres)
    synopsis = meta.get("description") or meta.get("synopsis") or meta.get("story") or "Synopsis unavailable."
    info = Text(no_wrap=True, overflow="ellipsis")
    info.append(plain(title), style=f"bold {t['text']}")
    info.append(f"\n{plain(meta.get('type', 'unknown')).upper()}  /  {meta.get('year') or 'Year unknown'}",
                style=t["primary"])
    rating = meta.get("rating") or meta.get("imdb_rating")
    info.append(f"  /  ★ {rating}" if rating else "  /  Unrated", style=t["accent"])
    info.append("\n" + plain(genres or "Genres unavailable"), style=t["muted"])
    summary = Text(plain(synopsis), style=t["muted"], overflow="ellipsis")
    # Fixed-height summary keeps room for the picker on an 80×24 terminal.
    story = Table.grid(expand=True)
    story.add_column()
    story.add_row(info)
    story.add_row(Panel(summary, height=4, box=box.SIMPLE, padding=0))
    facts = Table.grid(padding=(0, 1))
    facts.add_column(style=t["muted"])
    facts.add_column(style=t["text"])
    for label, value in [("Quality", meta.get("quality") or "Unknown"),
                         ("Audio", meta.get("audio") or meta.get("language") or "Not listed"),
                         ("Subtitles", meta.get("subtitles") or "Not listed"),
                         ("Seasons", str(len(details["seasons"])) if "seasons" in details else "—")]:
        facts.add_row(Text(label), clipped(value, 26))
    if width >= 110:
        split = Table.grid(expand=True, padding=(0, 2))
        split.add_column(ratio=3)
        split.add_column(ratio=1, min_width=26)
        split.add_row(story, facts)
        content = split
    else:
        compact = Text(f"Quality: {meta.get('quality') or 'Unknown'}  ·  "
                       f"Audio: {meta.get('audio') or meta.get('language') or 'Not listed'}  ·  "
                       f"Subtitles: {meta.get('subtitles') or 'Not listed'}", style=t["muted"],
                       no_wrap=True, overflow="ellipsis")
        short = Text(plain(synopsis), style=t["muted"], no_wrap=True, overflow="ellipsis")
        content = Group(info, short, compact)
    return Panel(content, box=box.ROUNDED, border_style=t["border"], padding=(0, 1),
                 title=Text("NOW EXPLORING", style=t["accent"]), title_align="left")


def picker_view(items, selected, offset, page_size, title, subtitle, query,
                filtering, checked, multi, theme=None):
    t = theme_tokens(theme)
    structured = bool(items and isinstance(items[0][1], PickerRow))
    table = Table(expand=True, box=None, show_header=structured, padding=(0, 1),
                  header_style=f"bold {t['muted']}", collapse_padding=True)
    table.add_column("", width=2, no_wrap=True)
    if structured:
        for index, label in enumerate(items[0][1].headers):
            table.add_column(label, ratio=1 if index == 0 else None,
                             width=None if index == 0 else {
                                 "Type": 9, "Quality / Year": 20, "Servers": 10,
                                 "Progress": 22, "Status": 12, "Episode": 12,
                             }.get(label, 18),
                             no_wrap=True, overflow="ellipsis")
    else:
        table.add_column("", ratio=1, no_wrap=True, overflow="ellipsis")
    for position in range(offset, min(len(items), offset + page_size)):
        original_index, item = items[position]
        active = position == selected
        marker = "✓" if multi and original_index in checked else ">" if active else " "
        if structured:
            cells = [Text(plain(cell), no_wrap=True, overflow="ellipsis") for cell in item.cells]
            for cell in cells:
                state = cell.plain.casefold()
                if state in ("ready", "watched", "complete"):
                    cell.stylize(t["success"])
                elif state in ("slow", "retry"):
                    cell.stylize(t["warning"])
                elif state == "offline":
                    cell.stylize(t["error"])
        else:
            try:
                cells = [Text.from_markup(str(item))]
            except Exception:
                cells = [Text(str(item))]
            cells[0].no_wrap = True
            cells[0].overflow = "ellipsis"
        table.add_row(Text(marker, style=t["primary"]), *cells,
                      style=f"bold {t['text']} on {t['selected']}" if active else t["text"])
    if not items:
        table.add_row("", Text("No matches. Edit the filter or press Esc to clear it.", style=t["warning"]))
    page = selected // page_size + 1 if items else 1
    pages = max(1, (len(items) + page_size - 1) // page_size)
    status = Text(f"[Page {page}/{pages}]   {len(items)} items", style=t["muted"])
    if multi:
        status.append(f"   ·   {len(checked)} selected", style=t["accent"])
    if filtering or query:
        status.append("   / " + query + "▏", style=t["primary"])
    children = [status]
    if subtitle:
        children.append(Text(plain(subtitle), style=t["muted"], no_wrap=True, overflow="ellipsis"))
    children.append(table)
    return Group(Panel(Group(*children), title=Text(plain(title), style=f"bold {t['text']}"),
                       title_align="left", box=box.ROUNDED, border_style=t["border"], padding=(0, 1)),
                 key_hints(multi, filtering or bool(query), t))
