"""Synthetic HTML fixtures: no provider requests or startup domain discovery."""
from pathlib import Path
from unittest import TestCase
from unittest.mock import Mock, patch

from bs4 import BeautifulSoup
from cenima.scraper import TopCinemaScraper

FIXTURES = Path(__file__).parent / 'fixtures'


class ScraperFixtureTests(TestCase):
    def setUp(self):
        self.scraper = TopCinemaScraper(base_url='https://catalog.test', auto_discover=False)
        self.addCleanup(self.scraper.session.close)
        self.get = patch.object(self.scraper.session, 'get', side_effect=AssertionError('Unexpected GET'))
        self.post = patch.object(self.scraper.session, 'post', side_effect=AssertionError('Unexpected POST'))
        self.mock_get, self.mock_post = self.get.start(), self.post.start()
        self.addCleanup(self.get.stop)
        self.addCleanup(self.post.stop)

    def response(self, name, url='https://catalog.test/series/example/'):
        return Mock(status_code=200, text=(FIXTURES / name).read_text(encoding='utf-8'), url=url)

    def test_relative_episode_links_and_absolute_links(self):
        soup = BeautifulSoup(self.response('episodes.html').text, 'html.parser')
        episodes = [self.scraper._parse_episode_link(a, a['href']) for a in soup.select('.row a')]
        self.assertEqual(episodes[0]['url'], 'https://catalog.test/series/example-season-1-episode-2/')
        self.assertEqual(episodes[1]['episode_number'], '1')
        self.assertEqual(episodes[-1]['is_special'], True)
        self.mock_get.assert_not_called()

    def test_season_episode_loading_sorts_and_deduplicates(self):
        self.mock_get.side_effect = None
        self.mock_get.return_value = self.response('episodes.html')
        season = {'url': 'https://catalog.test/series/example-season-1/'}
        episodes = self.scraper.fetch_season_episodes(season)
        self.assertEqual([e['episode_number'] for e in episodes], ['Special', '1', '2'])
        self.assertEqual(season['poster'], 'https://images.test/example.jpg')
        calls = self.mock_get.call_count
        self.assertEqual(self.scraper.fetch_season_episodes(season), episodes)
        self.assertEqual(self.mock_get.call_count, calls)

    def test_fallback_season_discovery_sorts_and_deduplicates(self):
        self.mock_get.side_effect = None
        self.mock_get.return_value = self.response('seasons_fallback.html')
        details = self.scraper.get_series_details('https://catalog.test/series/example/')
        self.assertEqual([s['season_number'] for s in details['seasons']], [1, 2])
        self.assertEqual(details['synopsis'], 'A fixture series with two seasons.')
        self.assertEqual(details['seasons'][0]['url'], 'https://catalog.test/series/example-season-1/')

    def test_box_season_discovery_preserves_parts(self):
        self.mock_get.side_effect = None
        self.mock_get.return_value = self.response('seasons_boxes.html')
        details = self.scraper.get_series_details('https://catalog.test/anime/example/', show_type='anime')
        self.assertEqual(details['type'], 'anime')
        self.assertEqual({s['season_part'] for s in details['seasons']}, {'Part 1', 'Part 2'})

    def test_search_filters_types_and_ignores_malformed_cards(self):
        self.mock_post.side_effect = None
        self.mock_post.return_value = self.response('search.html')
        results = self.scraper.search('Example', content_type='movie')
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]['metadata'], {
            'year': 2024, 'quality': '1080p BluRay', 'poster': 'https://images.test/movie.jpg'})
        self.mock_get.assert_not_called()
