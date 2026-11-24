import io
import unittest
from unittest.mock import AsyncMock, Mock, patch

from rich.console import Console

from cenima.cli import CinemaCLI
from cenima.models import NavigationOutcome, PlaybackOutcome


class NavigationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.cli = object.__new__(CinemaCLI)
        self.cli.config = Mock()
        self.cli.config.get_theme.return_value = {'primary': 'cyan', 'error': 'red'}
        self.cli.current_show_title = 'Example'
        self.cli.current_overview_panel = None
        self.cli.current_season = None
        self.cli.current_episodes = []
        self.cli.current_episode_index = 0
        self.cli.banner = Mock()
        self.cli.run_fzf = Mock(return_value=0)
        self.cli.log = Mock()
        self.console = patch('cenima.cli.CONSOLE', Console(file=io.StringIO()))
        self.console.start()
        self.addCleanup(self.console.stop)

    async def test_long_sequence_is_iterative_and_search_leaves_season(self):
        episodes = [{'episode_number': str(i), 'url': str(i)} for i in range(1200)]
        self.cli._load_with_retry = AsyncMock(return_value=episodes)
        self.cli.run_fzf_objects = Mock(return_value=episodes[0])
        self.cli.play_episode = AsyncMock(return_value=PlaybackOutcome.FINISHED)
        self.cli.prompt_next_episode = AsyncMock(
            side_effect=[NavigationOutcome.NEXT] * 1199 + [NavigationOutcome.SEARCH])
        result = await self.cli.handle_season_episodes({'url': 'season', 'season_number': 1})
        self.assertEqual(result, NavigationOutcome.SEARCH)
        self.assertEqual(self.cli.current_episode_index, 1199)
        self.assertEqual(self.cli.play_episode.await_count, 1200)

    async def test_cancelled_playback_preserves_episode_without_next_prompt(self):
        episodes = [{'episode_number': str(i), 'url': str(i)} for i in range(3)]
        self.cli._load_with_retry = AsyncMock(return_value=episodes)
        self.cli.run_fzf_objects = Mock(side_effect=[episodes[1], None])
        self.cli.play_episode = AsyncMock(return_value=PlaybackOutcome.CANCELLED)
        self.cli.prompt_next_episode = AsyncMock()
        self.assertEqual(await self.cli.handle_season_episodes({'url': 'season'}), NavigationOutcome.BACK)
        self.cli.prompt_next_episode.assert_not_awaited()
        self.assertEqual(self.cli.run_fzf_objects.call_args.kwargs['initial_index'], 1)

    async def test_search_outcome_propagates_through_multiple_seasons(self):
        seasons = [{'url': 'one'}, {'url': 'two'}]
        self.cli.run_fzf_objects = Mock(return_value=seasons[1])
        self.cli.handle_season_episodes = AsyncMock(return_value=NavigationOutcome.SEARCH)
        self.assertEqual(await self.cli.handle_series({'seasons': seasons}), NavigationOutcome.SEARCH)
        self.cli.run_fzf_objects.assert_called_once()

    async def test_loading_retries_exception_and_empty_then_succeeds(self):
        operation = Mock(side_effect=[OSError('offline'), [], ['episode']])
        self.cli.run_fzf = Mock(return_value=0)
        self.assertEqual(await self.cli._load_with_retry('Episodes', operation), ['episode'])
        self.assertEqual(operation.call_count, 3)

    async def test_end_of_season_offers_search_without_next(self):
        self.cli.current_episodes = [{'url': 'last'}]
        self.cli.run_fzf = Mock(return_value=1)
        self.assertEqual(await self.cli.prompt_next_episode(), NavigationOutcome.SEARCH)
        self.assertEqual(self.cli.run_fzf.call_args.args[0], ['Back to Episodes', 'Back to Search'])

    async def test_search_to_episode_back_outcome_preserves_catalog_state(self):
        seasons = [{'url': 'season-one', 'season_number': 1}]
        episodes = [{'url': 'episode-one', 'episode_number': 1}]
        self.cli.current_show_title = 'Example'
        self.cli.current_season = seasons[0]
        self.cli.current_episodes = episodes
        self.cli.current_episode_index = 0
        self.cli.run_fzf_objects = Mock(side_effect=[seasons[0], episodes[0], None])
        self.cli.handle_season_episodes = AsyncMock(return_value=NavigationOutcome.BACK)
        self.assertEqual(await self.cli.handle_series({'seasons': seasons}), NavigationOutcome.BACK)
        self.assertEqual(self.cli.current_season, seasons[0])
        self.assertEqual(self.cli.current_episodes, episodes)
