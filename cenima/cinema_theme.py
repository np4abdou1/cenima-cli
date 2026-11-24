"""Welcome-screen themes. Runtime UI themes remain in config.py."""

import os
from typing import Dict


MIDNIGHT_CINEMA = {
    "background": "#070914",
    "surface": "#0d1020",
    "surface_alt": "#11172b",
    "gold": "#e5b85c",
    "accent": "#f2c66d",
    "mint": "#66d7c6",
    "text": "#f4f1e8",
    "muted": "#8c91a4",
    "border": "#34394d",
    "selected": "#2a2418",
    "warning": "#e17b60",
}

NOIR_GOLD = {
    "background": "#090909",
    "surface": "#121212",
    "surface_alt": "#1a1814",
    "gold": "#cda84b",
    "accent": "#edc77b",
    "mint": "#72c5aa",
    "text": "#f7f3ea",
    "muted": "#96928a",
    "border": "#3d3a34",
    "selected": "#2b2618",
    "warning": "#d97866",
}

NEON_CYAN = {
    "background": "#050914",
    "surface": "#09111f",
    "surface_alt": "#0d1a29",
    "gold": "#7dd3fc",
    "accent": "#22d3ee",
    "mint": "#5eead4",
    "text": "#ecfeff",
    "muted": "#94a3b8",
    "border": "#334155",
    "selected": "#12313a",
    "warning": "#fb7185",
}

WELCOME_THEMES = {
    "midnight": MIDNIGHT_CINEMA,
    "noir-gold": NOIR_GOLD,
    "neon-cyan": NEON_CYAN,
}


def welcome_palette(name=None) -> Dict[str, str]:
    if name not in WELCOME_THEMES:
        name = os.environ.get("CINEMA_WELCOME_THEME", "midnight")
    return WELCOME_THEMES.get(name, MIDNIGHT_CINEMA)
