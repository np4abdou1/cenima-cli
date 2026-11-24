import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rich.console import Console
from cenima.history import HistoryManager
from cenima.models import WatchStatus
from cenima.picker import CustomPrompt
from cenima.scraper import TopCinemaScraper
from bs4 import BeautifulSoup


class StabilityTests(unittest.TestCase):
    def test_titles_and_markup_are_literal_input(self):
        for query in ('harry', 'batman', 'cars', 'queen', '[/]'):
            with self.subTest(query=query), patch('cenima.picker.raw_terminal'), patch(
                'cenima.picker.read_key', side_effect=list(query) + ['\r']
            ):
                prompt = CustomPrompt(Console(file=io.StringIO()), {'primary': 'cyan'})
                self.assertEqual(prompt.ask(), query)

    def test_nested_history_persists_without_completing_unplayed_short(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nested' / 'history.json'
            history = HistoryManager(path)
            progress = history.update_progress('Short', time_pos=0, duration=45)
            self.assertEqual(progress.status, WatchStatus.UNWATCHED)
            self.assertEqual(HistoryManager(path).get_progress('Short').duration, 45)
            self.assertEqual(set(path.parent.iterdir()), {path, path.with_suffix('.json.lock')})

    def test_relative_episode_link(self):
        scraper = TopCinemaScraper(auto_discover=False)
        try:
            anchor = BeautifulSoup('<a>Episode 1</a>', 'html.parser').a
            episode = scraper._parse_episode_link(anchor, '/episode-1/')
            self.assertEqual(episode['url'], scraper.base_url + '/episode-1/')
        finally:
            scraper.session.close()
