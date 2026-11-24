"""Compact, non-animated CINEMA wordmarks for welcome surfaces."""

import os
from rich.text import Text


BLOCK_FONT = {
    "C": ("▟███▙", "▐   ", "▐   ", "▝███▘"),
    "I": ("▟███▙", "  █  ", "  █  ", "▟███▙"),
    "N": ("█  ██", "█ ▄██", "██ █ ", "█  █ "),
    "E": ("████▙", "███▘ ", "███▖ ", "████▟"),
    "M": ("█   █", "██▄██", "█▀█▀█", "█   █"),
    "A": (" ▄█▄ ", "█   █", "█████", "█   █"),
}


def _supports_truecolor():
    return os.environ.get("COLORTERM", "").lower() in ("truecolor", "24bit")


def compact_logo(palette):
    if os.environ.get("TERM", "").lower() == "dumb" or os.environ.get("CINEMA_ASCII") == "1":
        return Text("CINEMA CLI", style=f"bold {palette['text']}")
    title = Text("CINEMA", style=f"bold {palette['text']}")
    title.stylize(f"bold {palette['accent']}", 0, 2)
    title.append(" ")
    title.append("CLI", style=f"bold {palette['mint']}")
    return title


def wordmark_logo(width, palette):
    if width < 80 or os.environ.get("TERM", "").lower() == "dumb" or os.environ.get("CINEMA_ASCII") == "1":
        return compact_logo(palette)

    rows = [Text("", justify="center") for _ in range(4)]
    colors = [
        palette["accent"],
        palette["gold"],
        palette["text"],
        palette["mint"],
    ]
    for index, letter in enumerate("CINEMA"):
        glyph = BLOCK_FONT[letter]
        for row_index, part in enumerate(glyph):
            style = colors[index % len(colors)] if _supports_truecolor() else palette["text"]
            rows[row_index].append(part, style=f"bold {style}")
            if index < 5:
                rows[row_index].append("  ", style=palette["muted"])

    return rows
