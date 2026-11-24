import io
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch
from rich.console import Console
from cenima.cli import CinemaCLI
from cenima.scraper import TopCinemaScraper
from cenima.models import NavigationOutcome
from bs4 import BeautifulSoup


class LatestScraperTests(unittest.TestCase):
    def setUp(self):
        self.scraper = TopCinemaScraper(base_url='https://catalog.test', auto_discover=False)
        self.addCleanup(self.scraper.session.close)
        self.response = Mock(url='https://catalog.test/', text=(
            Path(__file__).parent / 'fixtures/latest.html').read_text(encoding='utf-8'))
        self.patch = patch.object(self.scraper.session, 'get', return_value=self.response)
        self.get = self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_episode_order_keeps_only_newest_added_episode_per_show(self):
        items = self.scraper.get_latest_episodes()
        self.assertEqual([item['episode_number'] for item in items], ['6', '7'])
        self.assertEqual([item['show_title'] for item in items], ['Yaga', 'رحلة'])
        self.assertEqual([item['season_number'] for item in items], [1, 4])
        self.assertEqual(items[0]['metadata']['quality'], '1080p WEB-DL')
        self.assertTrue(all(item['url'].startswith('https://catalog.test/') for item in items))
        self.assertEqual(self.get.call_args.args[0], 'https://catalog.test/')

    def test_show_grouping_ignores_case_and_season_but_keeps_distinct_release_titles(self):
        soup = BeautifulSoup(self.response.text, 'html.parser')
        section = soup.select_one('.Wide--Contents')
        for title, url in [('مسلسل yAGA الموسم الثاني الحلقة 8 مترجمة', '/yaga-season-2-episode-8/'),
                           ('مسلسل Release 2020 الموسم الاول الحلقة 1 مترجمة', '/release-2020-episode-1/'),
                           ('مسلسل Release 2026 الموسم الاول الحلقة 2 مترجمة', '/release-2026-episode-2/')]:
            section.append(BeautifulSoup(f'<div class="Small--Box"><a href="{url}"><h3 class="title">{title}</h3></a></div>',
                                         'html.parser').div)
        self.response.text = str(soup)
        items = self.scraper.get_latest_episodes()
        self.assertEqual([item['show_title'] for item in items], ['Yaga', 'رحلة', 'Release 2020', 'Release 2026'])
        self.assertEqual(items[0]['episode_number'], '6')

    def test_movies_exclude_promo_and_keep_added_order_not_release_year(self):
        items = self.scraper.get_latest_movies()
        self.assertEqual([item['title'] for item in items], ['فيلم Classic 1960 مترجم', 'فيلم رحلة 2025 مترجم'])

    def home_with_archive(self):
        soup = BeautifulSoup(self.response.text, 'html.parser')
        soup.select_one('.Title--Box').append(BeautifulSoup(
            '<a href="/category/series/?key=episodes">More</a>', 'html.parser').a)
        return Mock(url='https://catalog.test/', text=str(soup))

    def archive(self, names, next_url=None):
        cards = ''.join(f'<div class="Small--Box"><a href="/show-{name}-episode-{i + 1}/">'
                        f'<h3 class="title">مسلسل {name} الموسم الاول الحلقة {i + 1} مترجمة</h3></a></div>'
                        for i, name in enumerate(names))
        pager = f'<ul class="page-numbers"><li><a href="{next_url}">»</a></li></ul>' if next_url else ''
        return Mock(url='https://catalog.test/category/series/?key=episodes',
                    text=f'<ul class="Posts--List">{cards}</ul>{pager}')

    def test_pagination_collects_fifteen_distinct_shows_and_preserves_episode_mode(self):
        self.get.side_effect = [self.home_with_archive(),
            self.archive(['Yaga'] * 20 + [f'Show{i}' for i in range(5)], '/category/series/page/2/'),
            self.archive([f'Show{i}' for i in range(4, 20)], '/category/series/page/3/')]
        items = self.scraper.get_latest_episodes()
        self.assertEqual(len(items), 15)
        self.assertEqual(len({item['show_title'].casefold() for item in items}), 15)
        self.assertEqual(items[0]['episode_number'], '6')
        self.assertEqual(self.get.call_count, 3)
        self.assertEqual(self.get.call_args.args[0], 'https://catalog.test/category/series/page/2/?key=episodes')

    def test_archive_exhaustion_or_repeated_next_link_does_not_loop(self):
        self.get.side_effect = [self.home_with_archive(), self.archive(['Yaga', 'New Show'], '/category/series/')]
        items = self.scraper.get_latest_episodes()
        self.assertEqual(len(items), 3)
        self.assertEqual(self.get.call_count, 2)

    def test_archive_failure_is_reported_for_retry_instead_of_silent_underfill(self):
        archive = self.archive([])
        archive.raise_for_status.side_effect = OSError('HTTP 503')
        self.get.side_effect = [self.home_with_archive(), archive]
        with self.assertRaisesRegex(OSError, '503'):
            self.scraper.get_latest_episodes()

    def test_empty_section_layout_failure_and_http_error_are_distinct(self):
        self.response.text = '<div class="Wide--Contents"><div class="Title--Box"><h3>آخر الحلقات المضافة</h3></div></div>'
        self.assertEqual(self.scraper.get_latest_episodes(), [])
        self.response.text = '<html>Site changed</html>'
        with self.assertRaisesRegex(ValueError, 'section was not found'):
            self.scraper.get_latest_episodes()
        self.response.raise_for_status.side_effect = OSError('HTTP 503')
        with self.assertRaisesRegex(OSError, '503'):
            self.scraper.get_latest_movies()

    def test_selected_episode_uses_parent_breadcrumb_not_recommendations(self):
        item = self.scraper.get_latest_episodes()[0]
        self.response.text = '''<a href="/series/wrong/">Wrong recommendation</a>
            <div class="breadcrumbs"><a href="/series/yaga/">مسلسل Yaga مترجم</a>
            <a href="/series/yaga-season-1/">Season 1</a></div>'''
        context = self.scraper.get_episode_context(item)
        self.assertEqual(context['show_url'], 'https://catalog.test/series/yaga/')
        self.assertEqual(context['show_title'], 'Yaga')
        self.assertEqual(context['episode_number'], '6')


class LatestNavigationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cli = object.__new__(CinemaCLI)
        self.cli.config = Mock()
        self.cli.config.get_theme.return_value = {}
        self.cli.banner = Mock()
        self.cli.run_fzf = Mock(return_value=0)
        self.cli.show_details_flow = AsyncMock(return_value=NavigationOutcome.BACK)
        self.cli.play_episode = AsyncMock()
        self.cli.download_items = AsyncMock()
        self.cli.run_fzf_objects = Mock()
        self.cli._load_with_retry = AsyncMock()
        self.episode = {'title': 'مسلسل Yaga الموسم الاول الحلقة 6 مترجمة', 'show_title': 'Yaga',
            'show_url': 'https://catalog.test/series/yaga/', 'url': 'https://catalog.test/episode/6',
            'season_number': 1, 'episode_number': '6'}
        self.console = patch('cenima.cli.CONSOLE', Console(file=io.StringIO()))
        self.console.start()
        self.addCleanup(self.console.stop)

    async def test_commands_dispatch_and_help_is_independent_of_search(self):
        self.cli.show_latest = AsyncMock()
        for command, kind in [('/last-eps', 'episode'), ('/last-mv', 'movie')]:
            self.assertTrue(await self.cli.handle_command(command))
            self.cli.show_latest.assert_awaited_with(kind)

    async def test_latest_movie_routes_to_existing_play_download_flow(self):
        movie = {'title': 'Movie', 'url': 'https://catalog.test/movie/'}
        self.cli._load_with_retry.return_value = [movie]
        self.cli.run_fzf_objects.side_effect = [movie, None]
        await self.cli.show_latest('movie')
        self.cli.show_details_flow.assert_awaited_once_with(movie)

    async def test_latest_episode_play_download_and_back_preserve_catalog_identity(self):
        for action in (0, 1, 2, 3, None):
            self.cli._load_with_retry.side_effect = [[self.episode], self.episode]
            self.cli.run_fzf_objects.side_effect = [self.episode, None]
            self.cli.run_fzf.return_value = action
            self.cli.play_episode.reset_mock()
            self.cli.download_items.reset_mock()
            self.cli.show_details_flow.reset_mock()
            await self.cli.show_latest('episode')
            self.assertEqual(self.cli.current_show_url, self.episode['show_url'])
            self.assertEqual(self.cli.current_episodes[0]['url'], self.episode['url'])
            if action == 0:
                self.cli.play_episode.assert_awaited_once_with(self.episode, 'Season 1')
            elif action == 1:
                self.cli.download_items.assert_awaited_once_with([self.episode], self.cli.current_season)
            elif action == 2:
                self.cli.show_details_flow.assert_awaited_once_with({'title': 'Yaga',
                    'url': self.episode['show_url'], 'type': 'series'})
                self.assertEqual(self.cli.run_fzf_objects.call_args.kwargs['initial_index'], 0)
            else:
                self.cli.play_episode.assert_not_awaited()
                self.cli.download_items.assert_not_awaited()
                self.cli.show_details_flow.assert_not_awaited()

    async def test_missing_show_link_does_not_open_episode_as_show_home(self):
        context = dict(self.episode, show_url=self.episode['url'])
        self.cli._load_with_retry.side_effect = [[self.episode], context]
        self.cli.run_fzf_objects.side_effect = [self.episode, None]
        self.cli.run_fzf.side_effect = [2, 0]
        await self.cli.show_latest('episode')
        self.cli.show_details_flow.assert_not_awaited()
        self.assertEqual(self.cli.run_fzf.call_args.kwargs['title'], 'Show page unavailable')

    async def test_show_page_back_to_search_outcome_leaves_latest_list(self):
        self.cli._load_with_retry.side_effect = [[self.episode], self.episode]
        self.cli.run_fzf_objects.return_value = self.episode
        self.cli.run_fzf.return_value = 2
        self.cli.show_details_flow.return_value = NavigationOutcome.SEARCH
        await self.cli.show_latest('episode')
        self.cli.run_fzf_objects.assert_called_once()

    async def test_empty_results_return_without_opening_a_picker(self):
        self.cli._load_with_retry.return_value = None
        await self.cli.show_latest('episode')
        self.cli.run_fzf_objects.assert_not_called()
