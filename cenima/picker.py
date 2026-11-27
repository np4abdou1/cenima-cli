import sys
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
from rich.cells import cell_len


_WINDOWS = os.name == "nt"
if _WINDOWS:
    import msvcrt
else:
    import tty
    import termios


_WINDOWS_KEYS = {
    "H": "\x1b[A",  # Up
    "P": "\x1b[B",  # Down
    "K": "\x1b[D",  # Left
    "M": "\x1b[C",  # Right
    "I": "\x1b[5~",  # Page Up
    "Q": "\x1b[6~",  # Page Down
    "G": "\x1b[H",  # Home
    "O": "\x1b[F",  # End
    "S": "\x1b[3~",  # Delete
}


class PickerAction:
    def __init__(self, action: str, item: Any = None, index: Optional[int] = None):
        self.action = action
        self.item = item
        self.index = index

@contextmanager
def raw_terminal(fd: Optional[int] = None):
    """Safely enter and exit raw terminal mode with guaranteed restoration."""
    if fd is None:
        try:
            fd = sys.stdin.fileno()
        except Exception:
            fd = 0
    if _WINDOWS:
        # getwch reads console input without line buffering or echo. POSIX
        # terminal modes and select() on stdin are not available on Windows.
        yield fd
        return
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield fd
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)

def read_key(fd: int, timeout: float = 0.3) -> Optional[str]:
    """Read a single keypress or escape sequence non-blockingly."""
    if _WINDOWS:
        deadline = time.monotonic() + max(0.0, timeout)
        while not msvcrt.kbhit():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(0.01, remaining))
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            # Consume the complete extended key, including unsupported keys,
            # so its scan code cannot become accidental prompt text.
            return _WINDOWS_KEYS.get(msvcrt.getwch())
        return ch

    r, _, _ = select.select([fd], [], [], timeout)
    if not r:
        return None
    try:
        first = os.read(fd, 1)
    except OSError:
        return None

    if not first:
        return None
    data = first
    if first == b'\x1b':
        # Read exactly one escape sequence, not the following Enter/key.
        for _ in range(10):
            if not select.select([fd], [], [], 0.05)[0]:
                break
            part = os.read(fd, 1)
            if not part:
                break
            data += part
            if len(data) == 2 and part not in (b'[', b'O'):
                break
            if len(data) > 2 and 0x40 <= part[0] <= 0x7e:
                break
    else:
        length = (2 if 0xc2 <= first[0] <= 0xdf else
                  3 if 0xe0 <= first[0] <= 0xef else
                  4 if 0xf0 <= first[0] <= 0xf4 else 1)
        while len(data) < length and select.select([fd], [], [], 0.05)[0]:
            part = os.read(fd, 1)
            if not part:
                break
            data += part
    return data.decode('utf-8', errors='replace')

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
    """A single in-place display for menus and refreshing download jobs."""

    def __init__(self, console: Console, theme: dict):
        self.console = console
        self.theme = theme

    def pick(
        self, items: List[Any], format_func: Callable[[Any], str],
        title: str = "", subtitle: str = "", *, initial_index: int = 0,
        shortcuts=None, multi: bool = False, refresh_items=None, item_key=None,
        header: Optional[Callable[[], str]] = None,
    ) -> Any:
        if not items:
            return [] if multi else None
        items = list(items)
        selected = max(0, min(initial_index, len(items) - 1))
        offset = 0
        filter_mode, filter_query = False, ""
        marked = set()
        shortcuts = shortcuts or {}
        last_refresh = time.monotonic()

        def filtered():
            query = filter_query.casefold()
            return [i for i, item in enumerate(items)
                    if not query or query in format_func(item).casefold()]

        def render(indices, page_size):
            table = Table.grid(expand=True)
            table.add_column("item", no_wrap=True, overflow="ellipsis", ratio=1)
            scrolling = len(indices) > page_size
            if scrolling:
                table.add_column("scrollbar", width=1)
            if filter_mode or filter_query:
                cells = [Text("Filter: " + filter_query + "█", style="yellow", no_wrap=True)]
                table.add_row(*(cells + [Text("")] if scrolling else cells))
            elif subtitle:
                cells = [Text(subtitle.replace("\n", " "), style="dim", no_wrap=True)]
                table.add_row(*(cells + [Text("")] if scrolling else cells))
            if header:
                text = safe_renderable_text(header(), False, self.theme["primary"])
                text.stylize('bold dim')
                cells = [text]
                table.add_row(*(cells + [Text("")] if scrolling else cells))
            if not indices:
                cells = [Text("No matching results", style="dim italic")]
                table.add_row(*(cells + [Text("")] if scrolling else cells))
            else:
                thumb_size = max(1, int(page_size * page_size / len(indices)))
                thumb_start = int(offset / max(1, len(indices) - page_size) * (page_size - thumb_size))
                for row, real_index in enumerate(indices[offset:offset + page_size]):
                    label = str(format_func(items[real_index])).replace("\r", " ").replace("\n", " ")
                    text = safe_renderable_text(label, offset + row == selected, self.theme["primary"])
                    if multi:
                        text = Text("☑ " if real_index in marked else "☐ ") + text
                    cells = [text]
                    if scrolling:
                        cells.append(Text("█" if thumb_start <= row < thumb_start + thumb_size else "│",
                                          style=self.theme["primary"]))
                    table.add_row(*cells)
            caption = title.replace("\n", " ")
            if filter_query:
                caption += f" ({len(indices)}/{len(items)})"
            if multi:
                caption += f" · {len(marked)} selected · Tab toggles"
            return Panel(table, title=Text(caption), border_style=self.theme["accent"])

        try:
            fd = sys.stdin.fileno()
        except (AttributeError, OSError, ValueError):
            fd = 0

        # Closing a Live display on every input timeout appends a new panel.
        # Own it for the whole menu and remove it before the next screen.
        with raw_terminal(fd), Live(
            console=self.console, screen=False, auto_refresh=False,
            transient=True, vertical_overflow="crop",
        ) as live:
            while True:
                if refresh_items and time.monotonic() - last_refresh >= 0.5:
                    previous = filtered()
                    identity = (item_key(items[previous[selected]])
                                if item_key and previous and selected < len(previous) else None)
                    items = list(refresh_items())
                    last_refresh = time.monotonic()
                    if not items:
                        return [] if multi else None
                    current = filtered()
                    if identity is not None:
                        selected = next((pos for pos, index in enumerate(current)
                                         if item_key(items[index]) == identity), selected)

                indices = filtered()
                selected = max(0, min(selected, len(indices) - 1))
                page_size = max(1, self.console.size.height -
                                (5 if subtitle or filter_mode else 3) - bool(header))
                offset = max(0, min(offset, max(0, len(indices) - page_size)))
                if selected < offset:
                    offset = selected
                elif selected >= offset + page_size:
                    offset = selected - page_size + 1
                live.update(render(indices, page_size), refresh=True)
                ch = read_key(fd, timeout=0.3)
                if ch is None:
                    continue
                if ch == "\x03":
                    raise KeyboardInterrupt()
                if ch == "\x1b":
                    if filter_mode or filter_query:
                        filter_mode, filter_query = False, ""
                        selected, offset = 0, 0
                        continue
                    return [] if multi else None
                if ch in ("\r", "\n"):
                    if indices:
                        index = indices[selected]
                        if multi:
                            return sorted(marked) if marked else [index]
                        return items[index] if refresh_items else index
                    continue
                if ch == "\t" and multi:
                    if indices:
                        index = indices[selected]
                        marked.symmetric_difference_update({index})
                    continue
                if not filter_mode:
                    if ch in shortcuts:
                        action = shortcuts[ch]
                        if isinstance(action, int):
                            return action
                        index = indices[selected] if indices else None
                        return PickerAction(action, items[index] if index is not None else None, index)
                    if ch in ("q", "Q"):
                        raise KeyboardInterrupt()
                    if ch in ("b", "B") and not filter_query:
                        return [] if multi else None
                    if ch == "/":
                        filter_mode, filter_query = True, ""
                        selected, offset = 0, 0
                        continue
                if ch in ("\x1b[A", "\x1bOA") or (ch == "k" and not filter_mode):
                    selected = max(0, selected - 1)
                elif ch in ("\x1b[B", "\x1bOB") or (ch == "j" and not filter_mode):
                    selected = min(len(indices) - 1, selected + 1) if indices else 0
                elif ch == "\x1b[5~":
                    selected = max(0, selected - page_size)
                elif ch == "\x1b[6~":
                    selected = min(len(indices) - 1, selected + page_size) if indices else 0
                elif ch in ("\x1b[H", "\x1b[1~"):
                    selected = 0
                elif ch in ("\x1b[F", "\x1b[4~"):
                    selected = len(indices) - 1 if indices else 0
                elif filter_mode:
                    if ch in ("\x7f", "\b"):
                        filter_query = filter_query[:-1]
                    elif len(ch) == 1 and ch.isprintable():
                        filter_query += ch
                    selected, offset = 0, 0


class CustomPrompt:
    """Interactive single-line terminal prompt with non-blocking live cursor pulse."""
    
    def __init__(self, console: Console, theme: dict):
        self.console = console
        self.theme = theme

    def ask(self, prompt_text: str = "❯ ", history: List[str] = None, *, render_frame=None) -> str:
        input_str = ""
        history = history or []
        hist_idx = len(history)
        try:
            fd = sys.stdin.fileno()
        except Exception:
            fd = 0

        def render(show_cursor: bool):
            cursor = "█" if show_cursor else " "
            t = Text.from_markup(f"[{self.theme['primary']}]{prompt_text}[/] ")
            t.truncate(max(0, self.console.width - 1), overflow='ellipsis')
            remaining = max(0, self.console.width - t.cell_len - 1)
            tail, used = [], 0
            for char in reversed(input_str):
                size = cell_len(char)
                if used + size > remaining:
                    break
                tail.append(char)
                used += size
            t.append(''.join(reversed(tail)) + cursor)
            t.no_wrap = True
            return render_frame(t) if render_frame else t

        with raw_terminal(fd):
            with Live(render(True), console=self.console, screen=False, auto_refresh=False,
                      transient=True, vertical_overflow='crop') as live:
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
                        input_str += ch
                    
                    live.update(render(True), refresh=True)
