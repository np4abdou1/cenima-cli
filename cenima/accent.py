import os
import re
import json
import configparser
import subprocess
import colorsys
from typing import Dict, Tuple, Optional

def hex_to_rgb(hex_str: str) -> Tuple[int, int, int]:
    hex_str = hex_str.lstrip('#')
    if len(hex_str) == 3:
        hex_str = "".join([c*2 for c in hex_str])
    try:
        return tuple(int(hex_str[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return (126, 191, 191)

def rgb_to_hex(rgb: Tuple[int, int, int]) -> str:
    return f"#{max(0, min(255, rgb[0])):02x}{max(0, min(255, rgb[1])):02x}{max(0, min(255, rgb[2])):02x}"

def adjust_brightness(hex_str: str, factor: float) -> str:
    r, g, b = hex_to_rgb(hex_str)
    h, l, s = colorsys.rgb_to_hls(r/255.0, g/255.0, b/255.0)
    new_l = max(0.15, min(0.85, l * factor))
    nr, ng, nb = colorsys.hls_to_rgb(h, new_l, s)
    return rgb_to_hex((int(nr * 255), int(ng * 255), int(nb * 255)))

def detect_system_accent() -> Tuple[str, str]:
    """Detects system accent color across KDE, GNOME, Omarchy, Pywal, Matugen, GTK, etc.
    Returns tuple of (source_name, hex_color)."""
    
    # 1. Omarchy theme (~/.config/omarchy/current/theme/colors.toml)
    omarchy_colors = os.path.expanduser('~/.config/omarchy/current/theme/colors.toml')
    if os.path.exists(omarchy_colors):
        try:
            with open(omarchy_colors, 'r', encoding='utf-8') as f:
                content = f.read()
                m = re.search(r'accent\s*=\s*["\'](#[0-9a-fA-F]{6})["\']', content)
                if not m:
                    m = re.search(r'color4\s*=\s*["\'](#[0-9a-fA-F]{6})["\']', content)
                if m:
                    return ("Omarchy", m.group(1))
        except Exception:
            pass

    # 2. Pywal (~/.cache/wal/colors.json)
    wal_file = os.path.expanduser('~/.cache/wal/colors.json')
    if os.path.exists(wal_file):
        try:
            with open(wal_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                accent = data.get('special', {}).get('accent') or data.get('colors', {}).get('color4')
                if accent and re.match(r'^#[0-9a-fA-F]{6}$', accent):
                    return ("Pywal", accent)
        except Exception:
            pass

    # 3. Matugen (~/.cache/matugen/colors.json or ~/.config/matugen/colors.json)
    for mat_path in [
        os.path.expanduser('~/.cache/matugen/colors.json'),
        os.path.expanduser('~/.config/matugen/colors.json')
    ]:
        if os.path.exists(mat_path):
            try:
                with open(mat_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    accent = data.get('colors', {}).get('primary') or data.get('colors', {}).get('accent')
                    if accent and re.match(r'^#[0-9a-fA-F]{6}$', accent):
                        return ("Matugen", accent)
            except Exception:
                pass

    # 4. KDE Plasma (~/.config/kdeglobals)
    kde_file = os.path.expanduser('~/.config/kdeglobals')
    if os.path.exists(kde_file):
        try:
            cp = configparser.ConfigParser(strict=False)
            cp.read(kde_file)
            rgb_str = None
            if 'General' in cp and 'AccentColor' in cp['General']:
                rgb_str = cp['General']['AccentColor']
            elif 'Colors:Selection' in cp and 'BackgroundNormal' in cp['Colors:Selection']:
                rgb_str = cp['Colors:Selection']['BackgroundNormal']
            
            if rgb_str:
                parts = [int(x.strip()) for x in rgb_str.split(',') if x.strip().isdigit()]
                if len(parts) >= 3:
                    return ("KDE", rgb_to_hex((parts[0], parts[1], parts[2])))
        except Exception:
            pass

    # 5. GNOME gsettings (org.gnome.desktop.interface accent-color)
    try:
        res = subprocess.run(
            ['gsettings', 'get', 'org.gnome.desktop.interface', 'accent-color'],
            capture_output=True, text=True, timeout=2
        )
        if res.returncode == 0 and res.stdout.strip():
            val = res.stdout.strip().strip("'\"")
            gnome_map = {
                'blue': '#3584e4',
                'teal': '#129eaf',
                'green': '#2ec27e',
                'yellow': '#e5a50a',
                'orange': '#e66100',
                'red': '#e01b24',
                'pink': '#d12771',
                'purple': '#9141ac',
                'slate': '#6f8396',
            }
            if val in gnome_map:
                return (f"GNOME ({val.title()})", gnome_map[val])
    except Exception:
        pass

    # 6. GTK CSS Files (~/.config/gtk-4.0/gtk.css or ~/.config/gtk-3.0/gtk.css)
    for gtk_path in [
        os.path.expanduser('~/.config/gtk-4.0/gtk.css'),
        os.path.expanduser('~/.config/gtk-3.0/gtk.css'),
        os.path.expanduser('~/.config/gtk-3.0/colors.css')
    ]:
        if os.path.exists(gtk_path):
            try:
                with open(gtk_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    m = re.search(r'@define-color\s+(?:accent_bg_color|theme_selected_bg_color)\s+(#[0-9a-fA-F]{6})', content)
                    if m:
                        return ("GTK Theme", m.group(1))
            except Exception:
                pass

    # Default Fallback (Cyan accent)
    return ("Default", "#7ebfbf")

def get_auto_theme() -> Dict[str, str]:
    """Generates a complete theme dictionary dynamically based on detected desktop accent color."""
    source, hex_color = detect_system_accent()
    secondary = adjust_brightness(hex_color, 1.2)
    accent = adjust_brightness(hex_color, 0.8)
    return {
        "primary": hex_color,
        "secondary": secondary,
        "accent": accent,
        "error": "#d97979",
        "source": source
    }
