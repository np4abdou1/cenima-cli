import sys
import tty
import termios
import time
import os
import select
from contextlib import contextmanager
from typing import List, Optional, Any, Callable, Tuple

from rich.console import Console
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.markup import escape, MarkupError

@contextmanager
def raw_terminal(fd: int = sys.stdin.fileno()):
    """Safely enter and exit raw terminal mode with guaranteed restoration."""
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield fd
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

def read_key(fd: int, timeout: float = 0.3) -> Optional[str]:
    """Read a single keypress or escape sequence non-blockingly."""
    r, _, _ = select.select([fd], [], [], timeout)
    if not r:
        return None
    try:
        ch = os.read(fd, 1).decode('utf-8', errors='ignore')
    except OSError:
        return None

    if ch == '\x1b':
        # Non-blocking check for escape sequences (arrow keys, fn keys, page up/down)
        r_seq, _, _ = select.select([fd], [], [], 0.05)
        if r_seq:
            try:
                ch += os.read(fd, 5).decode('utf-8', errors='ignore')
            except OSError:
                pass
    return ch

def safe_renderable_text(item_str: str, is_selected: bool, primary_color: str) -> Text:
    """Parse Rich markup safely, falling back to raw Text if markup is invalid or bracketed."""
    prefix = "❯ " if is_selected else "  "
    style_prefix = f"[bold {primary_color}]" if is_selected else ""
    style_suffix = "[/]" if is_selected else ""
    
    markup_str = f"{style_prefix}{prefix}{item_str}{style_suffix}"
    try:
        return Text.from_markup(markup_str)
    except Exception:
        # Fallback to plain text on markup errors (e.g. unescaped brackets in video title)
        t = Text(prefix + item_str)
        if is_selected:
            t.stylize(f"bold {primary_color}")
        return t

class RichPicker:
    """Lightweight left-side interactive picker with dynamic scrollbar & live filter support."""
    
    def __init__(self, console: Console, theme: dict):
        self.console = console
        self.theme = theme

    def pick(
        self,
        items: List[Any],
        format_func: Callable[[Any], str],
        title: str = "",
        subtitle: str = ""
    ) -> Optional[int]:
        if not items:
            return None

        selected = 0
        offset = 0
        filter_mode = False
        filter_query = ""

        def get_filtered_indices() -> List[int]:
            if not filter_query:
                return list(range(len(items)))
            q = filter_query.lower()
            return [i for i, item in enumerate(items) if q in format_func(item).lower()]

        fd = sys.stdin.fileno()

        with raw_terminal(fd):
            while True:
                term_height = self.console.size.height
                max_visible = max(4, term_height - 10)
                
                valid_indices = get_filtered_indices()
                if not valid_indices:
                    display_indices = []
                    selected = 0
                    offset = 0
                else:
                    if selected >= len(valid_indices):
                        selected = len(valid_indices) - 1
                    if selected < 0:
                        selected = 0
                    display_indices = valid_indices

                # Ensure cursor remains inside visible scrolling window
                if selected < offset:
                    offset = selected
                elif selected >= offset + max_visible:
                    offset = selected - max_visible + 1

                def generate_renderable():
                    table = Table.grid(expand=True)
                    table.add_column("item")
                    
                    num_display = len(display_indices)
                    if num_display > max_visible:
                        table.add_column("scrollbar", justify="right", width=1)

                    # Subtitle or Filter Line
                    if filter_mode or filter_query:
                        filter_text = f"[bold yellow]🔍 Filter:[/] {filter_query}█"
                        table.add_row(Text.from_markup(filter_text), "")
                    elif subtitle:
                        table.add_row(Text.from_markup(f"[dim]{subtitle}[/]\n"), "")

                    if not display_indices:
                        table.add_row(Text.from_markup("  [dim italic]No matching results[/]"), "")
                    else:
                        visible_slice = display_indices[offset : offset + max_visible]
                        
                        # Scrollbar Thumb Calculation
                        visible_ratio = max_visible / num_display if num_display > 0 else 1
                        thumb_size = max(1, int(max_visible * visible_ratio))
                        max_offset = max(1, num_display - max_visible)
                        scroll_ratio = offset / max_offset if num_display > max_visible else 0
                        thumb_start = int(scroll_ratio * (max_visible - thumb_size))

                        for idx, real_idx in enumerate(visible_slice):
                            item_str = format_func(items[real_idx])
                            is_sel = (offset + idx == selected)
                            item_renderable = safe_renderable_text(item_str, is_sel, self.theme['primary'])

                            if num_display > max_visible:
                                is_thumb = (thumb_start <= idx < thumb_start + thumb_size)
                                bar_char = "█" if is_thumb else "│"
                                bar_style = self.theme['primary'] if is_thumb else "dim"
                                bar_renderable = Text(bar_char, style=bar_style)
                                table.add_row(item_renderable, bar_renderable)
                            else:
                                table.add_row(item_renderable)

                    panel_title = title
                    if filter_query:
                        panel_title += f" [dim]({len(display_indices)}/{len(items)})[/dim]"

                    return Panel(table, title=panel_title, border_style=self.theme['accent'])

                with Live(generate_renderable(), console=self.console, screen=False, auto_refresh=False) as live:
                    live.update(generate_renderable(), refresh=True)
                    ch = read_key(fd, timeout=0.3)
                    
                    if ch is None:
                        continue

                    # Global / Exit keys
                    if ch in ('\x03', 'q', 'Q') and not filter_mode:
                        raise KeyboardInterrupt()
                    elif ch in ('\x1b', 'b', 'B') and not filter_mode and not filter_query:
                        return None
                    elif ch in ('\x1b',): # ESC key clears filter
                        if filter_mode or filter_query:
                            filter_mode = False
                            filter_query = ""
                            selected = 0
                            offset = 0
                            continue
                        else:
                            return None
                    elif ch in ('\r', '\n'): # Enter selects item
                        if display_indices:
                            return display_indices[selected]
                        continue
                    
                    # Filtering toggles
                    elif ch == '/' and not filter_mode:
                        filter_mode = True
                        filter_query = ""
                        selected = 0
                        offset = 0
                        continue
                    elif filter_mode:
                        if ch in ('\x7f', '\b'): # Backspace in filter
                            filter_query = filter_query[:-1]
                            if not filter_query:
                                filter_mode = False
                        elif len(ch) == 1 and ch.isprintable():
                            filter_query += ch
                            selected = 0
                            offset = 0
                        continue

                    # Navigation Keys
                    if ch in ('\x1b[A', '\x1bOA', 'k'): # Up
                        selected = max(0, selected - 1)
                    elif ch in ('\x1b[B', '\x1bOB', 'j'): # Down
                        selected = min(len(display_indices) - 1, selected + 1) if display_indices else 0
                    elif ch in ('\x1b[5~',): # Page Up
                        selected = max(0, selected - max_visible)
                    elif ch in ('\x1b[6~',): # Page Down
                        selected = min(len(display_indices) - 1, selected + max_visible) if display_indices else 0
                    elif ch in ('\x1b[H', '\x1b[1~'): # Home
                        selected = 0
                    elif ch in ('\x1b[F', '\x1b[4~'): # End
                        selected = len(display_indices) - 1 if display_indices else 0


class CustomPrompt:
    """Interactive single-line terminal prompt with non-blocking live cursor pulse."""
    
    def __init__(self, console: Console, theme: dict):
        self.console = console
        self.theme = theme

    def ask(self, prompt_text: str = "❯ ", history: List[str] = None) -> str:
        input_str = ""
        history = history or []
        hist_idx = len(history)
        fd = sys.stdin.fileno()

        def render(show_cursor: bool):
            cursor = "█" if show_cursor else " "
            return Text.from_markup(f"[{self.theme['primary']}]{prompt_text}[/] {input_str}{cursor}")

        with raw_terminal(fd):
            with Live(render(True), console=self.console, screen=False, auto_refresh=False, transient=True) as live:
                last_blink = time.time()
                cursor_state = True

                while True:
                    # Handle cursor blinking timer
                    now = time.time()
                    if now - last_blink >= 0.4:
                        cursor_state = not cursor_state
                        last_blink = now
                        live.update(render(cursor_state), refresh=True)

                    ch = read_key(fd, timeout=0.1)
                    if ch is None:
                        continue

                    if ch in ('\x03', '\x1b'): # Ctrl+C or Escape
                        if not input_str:
                            return "/exit"
                        input_str = ""
                    elif ch in ('\r', '\n'): # Enter
                        if not input_str:
                            continue
                        return input_str
                    elif ch in ('\x7f', '\b'): # Backspace
                        input_str = input_str[:-1]
                    elif ch in ('\x1b[A', '\x1bOA'): # Up arrow
                        if history:
                            hist_idx = max(0, hist_idx - 1)
                            input_str = history[hist_idx]
                    elif ch in ('\x1b[B', '\x1bOB'): # Down arrow
                        if history:
                            hist_idx = min(len(history), hist_idx + 1)
                            input_str = history[hist_idx] if hist_idx < len(history) else ""
                    elif len(ch) == 1 and ch.isprintable():
                        if not input_str:
                            if ch == 'q': return "/exit"
                            if ch == 'h': return "/history"
                            if ch == 'c': return "/theme"
                            if ch == 'b': return "/back"
                        input_str += ch
                    
                    live.update(render(True), refresh=True)
