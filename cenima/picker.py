import sys
import time
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Optional, Any, Callable, Tuple

from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
from rich.screen import Screen
from rich.markup import escape, MarkupError
from .ui import theme_tokens, picker_view, app_bar, breadcrumb, PickerRow
from .config import __version__

if os.name == "nt":
    import msvcrt
else:
    import select
    import termios
    import tty


@contextmanager
def raw_terminal(fd: Optional[int] = None):
    """Safely enter and exit raw terminal mode with guaranteed restoration."""
    if os.name == "nt":
        yield fd
        return

    target_fd = sys.stdin.fileno() if fd is None else fd
    old_settings = termios.tcgetattr(target_fd)
    try:
        tty.setraw(target_fd)
        yield target_fd
    finally:
        termios.tcsetattr(target_fd, termios.TCSADRAIN, old_settings)


def _read_key_windows(timeout: float = 0.3) -> Optional[str]:
    """Non-blocking key reader for Windows consoles using msvcrt."""
    deadline = time.monotonic() + max(timeout, 0.0)
    while not msvcrt.kbhit():
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.01)

    ch = msvcrt.getwch()
    if ch in ("\x00", "\xe0"):
        # Special/arrow/function keys send a two-character sequence on Windows
        ext = msvcrt.getwch()
        # Map common Windows scan codes to ANSI-like sequences if the picker checks them
        scan_map = {
            "G": "\x1b[H", "O": "\x1b[F", "S": "\x1b[3~",
            "H": "\x1b[A",  # Up
            "P": "\x1b[B",  # Down
            "M": "\x1b[C",  # Right
            "K": "\x1b[D",  # Left
            "I": "\x1b[5~", # Page Up
            "Q": "\x1b[6~", # Page Down
        }
        return scan_map.get(ext, ch + ext)

    if ch == "\r":
        return "\r"
    return ch


def read_key(fd: Optional[int], timeout: float = 0.3) -> Optional[str]:
    """Read a single keypress or escape sequence non-blockingly."""
    if os.name == "nt":
        return _read_key_windows(timeout=timeout)

    target_fd = sys.stdin.fileno() if fd is None else fd
    r, _, _ = select.select([target_fd], [], [], timeout)
    if not r:
        return None
    try:
        raw = os.read(target_fd, 1)
        if not raw:
            raise EOFError("Terminal input closed")
        first = raw[0]
        size = 2 if 0xC2 <= first <= 0xDF else 3 if 0xE0 <= first <= 0xEF else 4 if 0xF0 <= first <= 0xF4 else 1
        while len(raw) < size:
            part = os.read(target_fd, 1)
            if not part:
                break
            raw += part
        ch = raw.decode("utf-8", errors="replace")
    except OSError:
        return None

    if ch == "\x1b":
        # Non-blocking check for escape sequences (arrow keys, fn keys, page up/down)
        r_seq, _, _ = select.select([target_fd], [], [], 0.05)
        if r_seq:
            try:
                ch += os.read(target_fd, 5).decode("utf-8", errors="ignore")
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

@dataclass
class PickerAction:
    action: str
    item: Any


class RichPicker:
    """One persistent Live surface for keyboard-driven single and multiple selection."""

    def __init__(self, console: Console, theme: dict, breadcrumbs=None, context=None, shortcuts=None):
        self.console = console
        self.theme = theme_tokens(theme)
        self.breadcrumbs = breadcrumbs or ["Home"]
        self.context = context
        self.shortcuts = shortcuts or {}

    def pick(self, items, format_func=str, title="", subtitle="", initial_index=0):
        return self._pick(items, format_func, title, subtitle, initial_index, multi=False)

    def pick_many(self, items, format_func=str, title="", subtitle="", initial_index=0):
        """Return original indices; None means cancelled, [] means confirmed empty."""
        return self._pick(items, format_func, title, subtitle, initial_index, multi=True)

    def _pick(self, items, format_func, title, subtitle, initial_index, multi):
        source = items if callable(items) else None
        if source:
            items = source()
        if not items:
            return [] if multi else None
        labels = [format_func(item) for item in items]
        selected = max(0, min(initial_index, len(items) - 1))
        query, filtering = "", False
        checked = set()
        fd = sys.stdin.fileno()
        self.console.clear()
        with raw_terminal(fd), Live(console=self.console, screen=False,
                                    auto_refresh=False, transient=True) as live:
            while True:
                if source:
                    items = source()
                    labels = [format_func(item) for item in items]
                indices = [i for i, label in enumerate(labels) if not query or
                           query.casefold() in (str(label) if isinstance(label, PickerRow) else
                               safe_renderable_text(str(label), False, self.theme['primary']).plain).casefold()]
                selected = max(0, min(selected, len(indices) - 1))
                context = (self.context() if callable(self.context) else self.context) if self.console.size.height >= 24 else None
                context_height = len(self.console.render_lines(context, self.console.options, pad=False)) if context else 0
                if context_height > self.console.size.height - 13:
                    context, context_height = None, 0
                # Measure actual headers/footer: wrapped subtitles and structured
                # tables must not steal rows from the last page after a resize.
                header = [app_bar(__version__, self.theme), breadcrumb(self.breadcrumbs, self.theme, self.console.width)]
                sample = picker_view([(i, labels[i]) for i in indices[:1]], 0, 0, 1,
                    title, subtitle, query, filtering, checked, multi, self.theme)
                overhead = len(self.console.render_lines(Group(*header, sample), self.console.options, pad=False)) - 1
                page_size = max(1, self.console.size.height - context_height - overhead - 1)
                offset = (selected // page_size) * page_size
                content = [app_bar(__version__, self.theme), breadcrumb(self.breadcrumbs, self.theme, self.console.width)]
                if context:
                    content.append(context)
                content.append(picker_view([(i, labels[i]) for i in indices], selected, offset,
                    page_size, title, subtitle, query, filtering, checked, multi, self.theme))
                live.update(Screen(Group(*content)), refresh=True)
                ch = read_key(fd, timeout=0.15)
                if ch is None:
                    continue
                if ch == '\x03':
                    raise KeyboardInterrupt()
                if ch in ('b', 'B') and not filtering:
                    return None
                if ch == '\x1b':
                    if filtering or query:
                        query, filtering, selected = "", False, 0
                        continue
                    return None
                if not filtering and indices and ch.casefold() in self.shortcuts:
                    action = self.shortcuts[ch.casefold()]
                    return action if isinstance(action, int) else PickerAction(action, items[indices[selected]])
                if ch in ('\r', '\n'):
                    if multi:
                        return sorted(checked)
                    if indices:
                        return items[indices[selected]] if source else indices[selected]
                    continue
                if multi and ch == '\t' and not filtering and indices:
                    index = indices[selected]
                    checked.symmetric_difference_update({index})
                    continue
                if ch == '/' and not filtering:
                    query, filtering, selected = "", True, 0
                    continue
                # Navigation remains active while a filter is applied.
                if ch in ('\x1b[A', '\x1bOA') or (ch == 'k' and not filtering):
                    selected = max(0, selected - 1)
                elif ch in ('\x1b[B', '\x1bOB') or (ch == 'j' and not filtering):
                    selected = min(len(indices) - 1, selected + 1)
                elif ch == '\x1b[5~':
                    selected = max(0, selected - page_size)
                elif ch == '\x1b[6~':
                    selected = min(len(indices) - 1, selected + page_size)
                elif ch in ('\x1b[H', '\x1b[1~'):
                    selected = 0
                elif ch in ('\x1b[F', '\x1b[4~'):
                    selected = max(0, len(indices) - 1)
                elif filtering and ch in ('\x7f', '\b'):
                    query, selected = query[:-1], 0
                elif filtering and len(ch) == 1 and ch.isprintable():
                    query += ch
                    selected = 0


class CustomPrompt:
    """Editable Unicode search field with history and a bounded one-line viewport."""

    def __init__(self, console: Console, theme: dict, context=None):
        self.console = console
        self.theme = theme_tokens(theme)
        self.context = context

    def ask(self, prompt_text="Search", history=None, initial_value='', title='SEARCH',
            placeholder='', hint='', context_after=False, context_before=None,
            welcome_actions=None, welcome_state=None, idle_refresh=False):
        history = history or []
        welcome_actions = welcome_actions or []
        welcome_state = welcome_state if welcome_state is not None else {'selected': 0}
        selected = max(0, min(int(welcome_state.get('selected', 0)), max(0, len(welcome_actions) - 1)))
        welcome_state['selected'] = selected
        value, draft, cursor, hist_idx = initial_value, initial_value, len(initial_value), len(history)
        fd = sys.stdin.fileno()

        def render():
            # Keep the insertion point visible without wrapping long pasted queries.
            room = max(8, self.console.size.width - 10)
            before = Text(value[:cursor])
            before.truncate(max(1, room - 1), overflow='ellipsis', pad=False)
            if Text(value[:cursor]).cell_len > room - 1:
                before = Text('…' + value[:cursor][-max(1, room // 2):])
            text = Text('  › ', style=self.theme['primary'])
            if not value and placeholder:
                text.append(placeholder, style=self.theme['muted'])
                text.append('▏', style=self.theme['primary'])
            else:
                text.append_text(before)
                text.append('▏', style=self.theme['primary'])
                tail = Text(value[cursor:], style=self.theme['text'])
                tail.truncate(max(0, room - before.cell_len - 1), overflow='ellipsis')
                text.append_text(tail)
            field = Panel(text, title=Text(title, style=self.theme['error']),
                          title_align='left', border_style=self.theme['error'], padding=(0, 1))
            if hint:
                field = Group(field, Text(hint, style=self.theme['muted'], justify='left'))
            context = self.context() if callable(self.context) else self.context
            before = context_before() if callable(context_before) else context_before
            if before:
                if context:
                    return Group(before, field, context)
                return Group(before, field)
            if context:
                return Group(field, context) if context_after else Group(context, field)
            return field

        with raw_terminal(fd), Live(render(), console=self.console, auto_refresh=False,
                                   transient=True) as live:
            while True:
                ch = read_key(fd, timeout=0.15)
                if ch is None:
                    continue
                live.update(render(), refresh=True)
                if ch == '\x03':
                    raise KeyboardInterrupt()
                if ch == '\x12':
                    return '/continue'
                if ch == '\x0c':
                    return '/library'
                if welcome_actions and not value:
                    # Slash opens/enters the search field; it must be typed, not
                    # interpreted as a one-key shortcut.
                    if ch == '/':
                        value = '/'
                        cursor = len(value)
                        live.update(render(), refresh=True)
                        continue
                    if ch in ('\x1b[A', '\x1bOA', '\x1b[D', '\x1bOD'):
                        welcome_state['selected'] = max(0, welcome_state['selected'] - 1)
                        live.update(render(), refresh=True)
                        continue
                    if ch in ('\x1b[B', '\x1bOB', '\x1b[C', '\x1bOC', '\t'):
                        welcome_state['selected'] = min(len(welcome_actions) - 1,
                                                        welcome_state['selected'] + 1)
                        live.update(render(), refresh=True)
                        continue
                    if ch == '?':
                        return '/help'
                    shortcuts = {item['key'].casefold(): item['command'] for item in welcome_actions}
                    if ch.casefold() in shortcuts:
                        return shortcuts[ch.casefold()]
                if ch == '\x1b':
                    if not value:
                        return '/exit'
                    value, cursor = '', 0
                elif ch in ('\r', '\n'):
                    if value.strip():
                        return value.strip()
                    if welcome_actions:
                        selected = welcome_state['selected']
                        return welcome_actions[selected]['command']
                elif ch in ('\x7f', '\b'):
                    if cursor:
                        value = value[:cursor-1] + value[cursor:]
                        cursor -= 1
                elif ch == '\x1b[3~':
                    value = value[:cursor] + value[cursor+1:]
                elif ch == '\x15':
                    value, cursor = '', 0
                elif ch in ('\x1b[D', '\x1bOD'):
                    cursor = max(0, cursor - 1)
                elif ch in ('\x1b[C', '\x1bOC'):
                    cursor = min(len(value), cursor + 1)
                elif ch in ('\x1b[H', '\x1b[1~'):
                    cursor = 0
                elif ch in ('\x1b[F', '\x1b[4~'):
                    cursor = len(value)
                elif ch in ('\x1b[A', '\x1bOA') and history:
                    if hist_idx == len(history):
                        draft = value
                    hist_idx = max(0, hist_idx - 1)
                    value = history[hist_idx]
                    cursor = len(value)
                elif ch in ('\x1b[B', '\x1bOB') and history:
                    hist_idx = min(len(history), hist_idx + 1)
                    value = history[hist_idx] if hist_idx < len(history) else draft
                    cursor = len(value)
                elif len(ch) == 1 and ch.isprintable():
                    value = value[:cursor] + ch + value[cursor:]
                    cursor += 1
                    live.update(render(), refresh=True)
