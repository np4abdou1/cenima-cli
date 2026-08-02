from pathlib import Path

# --- Version & Metadata ---
__version__ = "0.1"
__author__ = "np4abdou1"
__license__ = "GPL-3.0"

# --- Directories ---
CONFIG_DIR = Path.home() / ".config" / "cenima-cli"
CONFIG_FILE = CONFIG_DIR / "config.json"

# --- Scraper Configuration ---
BASE_URL = "https://topcinemaa.cc"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": BASE_URL,
}

AJAX_HEADERS = {
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": BASE_URL,
    "User-Agent": HEADERS["User-Agent"],
}

REQUEST_TIMEOUT = 15
RETRY_TOTAL = 3
RETRY_BACKOFF = 0.5
RETRY_STATUS_CODES = [429, 500, 502, 503, 504]

# --- ASCII Art ---
ASCII_ART = r"""
⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⣀⣀⣀⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⠀⠀⢀⣠⣴⣾⡿⠿⠟⠛⠿⠿⣿⣶⣤⣀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀
⠀⠀⠀⢀⣴⣿⢿⣿⣿⣧⠀⠀⠀⠀⢠⣿⣿⣿⢿⣷⡄⠱⣦⡀⠀⠀⠀⠀⠀⠀
⠀⠀⢀⣾⠏⠀⠀⠙⢿⣿⣧⡀⠀⣠⣿⣿⠟⠁⠀⠹⣿⣆⠈⠻⣦⡀⠀⠀⠀⠀
⠀⠀⣾⡏⠀⠀⠀⠀⠈⣿⣿⣷⣾⣿⣿⡏⠀⠀⠀⠀⢸⣿⡆⠀⠈⢻⣄⠀⠀⠀
⠀⢸⣿⣷⣤⣤⣤⣤⣴⣿⠋⣠⣤⡈⢻⣷⣤⣤⣤⣤⣴⣿⣷⠀⠀⠀⢻⣆⠀⠀
⠀⢸⣿⣿⠿⠿⠿⠿⠿⣿⡀⠻⠿⠃⣸⡿⠿⠿⠿⠿⢿⣿⣿⠀⠀⠀⠀⢿⡄⠀
⠀⠀⣿⣇⠀⠀⠀⠀⠀⣿⣿⣶⣶⣾⣿⡇⠀⠀⠀⠀⢰⣿⡇⠀⠀⠀⠀⢸⡇⠀
⠀⠀⠘⣿⣆⠀⠀⢀⣼⣿⡟⠁⠈⠻⣿⣿⣄⠀⠀⢠⣾⡟⠀⠀⠀⠀⠀⢸⡇⠀
⠀⠀⠀⠘⢿⣷⣶⣿⣿⡟⠀⠀⠀⠀⠘⣿⣿⣷⣶⣿⠏⠀⠀⠀⠀⠀⠀⢸⡇⠀
⠀⠀⠀⠀⠀⠉⠻⢿⣿⣷⣤⣤⣤⣤⣴⣿⣿⠿⠋⠁⠀⠀⠀⠀⠀⠀⠀⣿⠃⠀
⠀⠀⠀⠀⠀⠀⠀⠀⠈⠉⠙⠛⠛⠛⠉⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⢸⡏⠀⠀
⠀⠀⠀⠀⠀⣀⣤⣴⠶⠶⠶⠷⠶⠶⢶⣤⣤⣀⠀⠀⠀⠀⠀⠀⠀⢠⡿⠀⠀⠀
⠀⢀⣠⡶⠛⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠛⠳⢶⣤⣄⣀⣴⠟⠁⠀⠀⠀
⠀⠀⠉⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠀⠉⠉⠀⠀⠀⠀⠀⠀
"""

GOODBYE_ART = r"""
 _             _ 
| |__ _  _ ___| |
| '_ \ || / -_)_|
|_.__/\_, \___(_)
      |__/       
"""

# --- Theme Definitions ---
THEMES = {
    "blue": {"primary": "#7eb3d4", "secondary": "#9ac9e3", "accent": "#5a9bc7", "error": "#d97979"},
    "red": {"primary": "#d97979", "secondary": "#e59393", "accent": "#c55a5a", "error": "#d97979"},
    "green": {"primary": "#8ba87f", "secondary": "#a3ba98", "accent": "#6d8a62", "error": "#d97979"},
    "purple": {"primary": "#a88dbd", "secondary": "#bda3cf", "accent": "#8a6fa0", "error": "#d97979"},
    "cyan": {"primary": "#7ebfbf", "secondary": "#9bd3d3", "accent": "#5fa3a3", "error": "#d97979"},
    "yellow": {"primary": "#d9c379", "secondary": "#e5d193", "accent": "#c4a85a", "error": "#d97979"},
    "pink": {"primary": "#d9a3ba", "secondary": "#e5b8cd", "accent": "#c4859d", "error": "#d97979"},
    "orange": {"primary": "#d9a379", "secondary": "#e5b693", "accent": "#c4855a", "error": "#d97979"},
    "teal": {"primary": "#6b9a9a", "secondary": "#85b0b0", "accent": "#4d7c7c", "error": "#d97979"},
    "magenta": {"primary": "#c77eb8", "secondary": "#d79acd", "accent": "#a9609a", "error": "#d97979"},
    "lime": {"primary": "#a3ba8d", "secondary": "#b7cba3", "accent": "#859c6f", "error": "#d97979"},
    "coral": {"primary": "#d99382", "secondary": "#e5a899", "accent": "#c47563", "error": "#d97979"},
    "lavender": {"primary": "#b4a8cf", "secondary": "#c8bedd", "accent": "#968ab1", "error": "#d97979"},
    "gold": {"primary": "#c9b87f", "secondary": "#d9ca98", "accent": "#ab9a61", "error": "#d97979"},
    "mint": {"primary": "#8dbaa3", "secondary": "#a3cbb7", "accent": "#6f9c85", "error": "#d97979"},
    "rose": {"primary": "#d97ea8", "secondary": "#e599bd", "accent": "#bb608a", "error": "#d97979"},
    "sunset": {"primary": "#e48b7a", "secondary": "#f0a19a", "accent": "#c66d5c", "error": "#d97979"},
}

DEFAULT_THEME = "cyan"

# --- Spinners ---
SPINNERS = ["dots", "dots2", "dots3", "line", "star", "growVertical", "arc", "bouncingBar", "aesthetic"]
