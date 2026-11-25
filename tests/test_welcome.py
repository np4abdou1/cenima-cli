"""The welcome page must fit one viewport, including its input line."""
import io
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from rich.console import Console
from rich.segment import Segment
from rich.text import Text

from cenima.cli import CinemaCLI
from cenima.config import THEMES
from cenima.picker import CustomPrompt
from cenima.welcome import WelcomeScreen


class WelcomeTests(unittest.TestCase):
    def app(self):
        app = object.__new__(CinemaCLI)
        app.config = SimpleNamespace(theme='blue', get_theme=lambda: THEMES['blue'])
        app.history = Mock()
        app.history.get_recent_history.return_value = []
        app.github_stars = 5
        return app

    def test_screen_fits_terminal_and_keeps_prompt_visible(self):
        for width, height in [(140, 35), (110, 24), (80, 20), (60, 12), (30, 6), (12, 3)]:
            with self.subTest(width=width, height=height):
                console = Console(file=io.StringIO(), width=width, height=height)
                with patch('cenima.cli.CONSOLE', console):
                    frame = WelcomeScreen(self.app()._banner_renderable, Text('> search_here'))
                    lines = console.render_lines(frame)
                self.assertLessEqual(len(lines), height - 1)
                self.assertTrue(all(Segment.get_line_length(line) <= width for line in lines))
                self.assertIn('> search', ''.join(segment.text for line in lines for segment in line))

    def test_long_query_remains_one_line_without_losing_input(self):
        console = Console(file=io.StringIO(), width=20, height=8, force_terminal=True,
                          legacy_windows=False, _environ={'TERM': 'xterm-256color'})
        rendered = []

        def frame(prompt):
            rendered.append(prompt)
            return prompt

        with patch('cenima.picker.raw_terminal'), patch('cenima.picker.read_key', side_effect=['ع'] * 60 + ['\r']):
            result = CustomPrompt(console, THEMES['blue']).ask('> ', render_frame=frame)
        self.assertEqual(result, 'ع' * 60)
        self.assertTrue(all(prompt.cell_len <= 20 for prompt in rendered))
        self.assertTrue(all(prompt.no_wrap for prompt in rendered))

    def test_frame_recomputes_layout_after_resize(self):
        console = Console(file=io.StringIO(), width=110, height=24)
        with patch('cenima.cli.CONSOLE', console):
            frame = WelcomeScreen(self.app()._banner_renderable, Text('> input'))
            self.assertEqual(len(console.render_lines(frame)), 23)
            console.width, console.height = 30, 6
            self.assertEqual(len(console.render_lines(frame)), 5)
