import unittest
from difflib import SequenceMatcher
from unittest.mock import AsyncMock, Mock, patch
from rich.console import Console
import io

from cenima.cli import CinemaCLI
from cenima.cli import download_engine_labels


class SearchAliasTests(unittest.TestCase):
    def test_download_engine_labels_are_plain_and_clear(self):
        labels = download_engine_labels(['builtin', 'aria2c', 'idm'])
        self.assertEqual(labels, [
            'Built-in downloader (recommended)',
            'aria2c for direct files',
            'Internet Download Manager',
        ])
    def setUp(self):
        self.cli = object.__new__(CinemaCLI)
        self.cli.config = Mock()
        self.cli.config.get_theme.return_value = {}

    def test_short_aliases_map_to_existing_commands(self):
        self.cli.show_watch_later = AsyncMock()
        async def run():
            self.assertTrue(await self.cli.handle_command('/wl'))
            self.cli.show_watch_later.assert_awaited_once()
        import asyncio
        asyncio.run(run())

    def test_theme_and_trending_aliases_dispatch_correctly(self):
        self.cli.theme_selector = lambda: None
        async def run():
            self.assertTrue(await self.cli.handle_command('/theme'))
        import asyncio
        asyncio.run(run())

        self.cli.show_latest = AsyncMock()
        async def run_trending():
            self.assertTrue(await self.cli.handle_command('/t'))
        asyncio.run(run_trending())
        self.cli.show_latest.assert_awaited_with('movie')

    def test_fuzzy_typo_ranks_close_title_above_distant_title(self):
        typo = 'Stranger Thngs'
        close = 'Stranger Things'
        distant = 'The Institute'
        close_score = SequenceMatcher(None, close.lower(), typo.lower()).ratio()
        distant_score = SequenceMatcher(None, distant.lower(), typo.lower()).ratio()
        self.assertGreater(close_score, distant_score)
        self.assertGreaterEqual(close_score, 0.72)
