"""Bounded welcome viewport inside the application's existing terminal screen."""
from rich.console import Group
from rich.segment import Segment
from rich.text import Text


class WelcomeScreen:
    def __init__(self, banner, prompt):
        self.banner = banner
        self.prompt = prompt.copy()
        self.prompt.no_wrap = True
        self.prompt.overflow = 'crop'

    def __rich_console__(self, console, options):
        # Keep one spare row so drawing the cursor never advances scrollback.
        height = max(1, console.size.height - 1)
        width = options.max_width
        footer = [
            Text('Search · movies · series · anime', style='bold', no_wrap=True),
            self.prompt,
            Text('Enter: search · /help: commands · Esc: exit', style='dim', no_wrap=True),
        ]
        if height == 1:
            footer = [self.prompt]
        elif height == 2:
            footer = footer[1:]
        banner_height = height - len(footer)
        lines = []
        if banner_height:
            lines.extend(console.render_lines(
                self.banner(banner_height), options.update(width=width, height=banner_height), pad=True,
            ))
        lines.extend(console.render_lines(
            Group(*footer), options.update(width=width, height=len(footer)), pad=True,
        ))
        for index, line in enumerate(lines[:height]):
            yield from line
            if index < min(len(lines), height) - 1:
                yield Segment.line()
