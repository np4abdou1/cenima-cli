import json
import multiprocessing
import tempfile
import unittest
import warnings
from pathlib import Path
from unittest.mock import Mock, patch
from cenima.cli import CinemaCLI
from cenima.models import PlaybackOutcome, WatchStatus
from cenima.mpv import MpvState

from cenima.history import HistoryManager
from cenima.models import WatchProgress, content_identity


def write_history(path, barrier, worker):
    manager = HistoryManager(Path(path))
    barrier.wait(timeout=15)
    for item in range(10):
        manager.update_progress(f'Movie {worker}-{item}', time_pos=120, duration=900,
                                show_url=f'https://catalog.test/{worker}/{item}')


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / 'history.json'

    def test_processes_merge_updates(self):
        context = multiprocessing.get_context('spawn')
        barrier = context.Barrier(4)
        processes = [context.Process(target=write_history, args=(str(self.path), barrier, i))
                     for i in range(4)]
        try:
            for process in processes:
                process.start()
            for process in processes:
                process.join(timeout=25)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(len(HistoryManager(self.path).get_recent_history(100)), 40)
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)

    def test_stale_manager_save_does_not_resurrect_cleared_history(self):
        first, second = HistoryManager(self.path), HistoryManager(self.path)
        first.update_progress('A', time_pos=30)
        self.assertIsNotNone(second.get_progress('A'))
        second.clear_history()
        first.save()
        self.assertEqual(first.get_recent_history(), [])
        first.update_progress('B', time_pos=30)
        self.assertEqual([p.show_title for p in second.get_recent_history()], ['B'])

    def test_remakes_parts_mirrors_and_renamed_titles(self):
        manager = HistoryManager(self.path)
        for url in ['https://topcinema.film/movie/dune-1984/',
                    'https://topcinema.film/movie/dune-2021/']:
            manager.update_progress('Dune', show_url=url, time_pos=120, duration=1000)
        self.assertIsNone(manager.get_progress('Dune'))  # Ambiguous title lookup.
        progress = manager.get_progress('Renamed', show_url='https://topcinema.cam/movie/dune-1984')
        self.assertEqual(progress.time_pos, 120)
        for part in ['Part 1', 'Part 2']:
            manager.mark_watched('Show', 1, '1', show_url='https://catalog.test/show', season_part=part)
        self.assertEqual(len(manager.get_recent_history()), 4)
        self.assertEqual(manager.get_season_stats('Show', 1, show_url='https://catalog.test/show',
                                                 season_part='Part 2'), (1, ['1']))

    def test_legacy_migration_is_backed_up_and_idempotent(self):
        legacy = WatchProgress(show_title='Example', show_url='https://topcinema.film/series/example/',
                               season_number=1, season_label='Season 1 Part 2', episode_number='3',
                               time_pos=150, duration=900).model_dump(mode='json')
        legacy.pop('content_id')
        legacy.pop('season_part')
        original = json.dumps({'example::s1::e3': legacy}).encode()
        self.path.write_bytes(original)
        manager = HistoryManager(self.path)
        backup = self.path.with_suffix('.json.legacy.bak')
        self.assertEqual(backup.read_bytes(), original)
        progress = manager.get_progress('Example', 1, '3', show_url='https://topcinema.red/series/example',
                                        season_part='Part 2')
        self.assertEqual(progress.time_pos, 150)
        self.assertEqual(progress.content_id, content_identity(legacy['show_url'], 1, '3', 'Part 2'))
        migrated = self.path.read_bytes()
        manager.load()
        self.assertEqual(self.path.read_bytes(), migrated)
        self.assertEqual(backup.read_bytes(), original)

    def test_title_only_legacy_entries_are_not_assigned_to_a_remake(self):
        legacy = WatchProgress(show_title='Dune', time_pos=100).model_dump(mode='json')
        self.path.write_text(json.dumps({'dune::movie': legacy}))
        manager = HistoryManager(self.path)
        self.assertEqual(manager.get_progress('Dune').time_pos, 100)
        self.assertIsNone(manager.get_progress('Dune', show_url='https://catalog.test/dune-2021'))
        manager.update_progress('Dune', show_url='https://catalog.test/dune-2021', time_pos=300)
        self.assertEqual(len(manager.get_recent_history()), 2)

    def test_corrupt_file_cannot_be_overwritten_by_update(self):
        self.path.write_bytes(b'{broken')
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            manager = HistoryManager(self.path)
        with self.assertRaises(ValueError):
            manager.update_progress('Example', time_pos=30)
        self.assertEqual(self.path.read_bytes(), b'{broken')

    def test_failed_replace_preserves_history_and_releases_lock(self):
        manager = HistoryManager(self.path)
        manager.update_progress('A', time_pos=40)
        original = self.path.read_bytes()
        with patch('cenima.history.os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                manager.update_progress('B', time_pos=50)
        self.assertEqual(self.path.read_bytes(), original)
        manager.update_progress('C', time_pos=60)
        self.assertEqual({p.show_title for p in manager.get_recent_history()}, {'A', 'C'})

    def test_query_based_catalog_ids_do_not_collide(self):
        self.assertNotEqual(content_identity('https://topcinema.film/?p=1'),
                            content_identity('https://topcinema.cam/?p=2'))

    def test_cli_tracks_catalog_identity_not_cdn_url(self):
        cli = object.__new__(CinemaCLI)
        cli.config = Mock()
        cli.config.get_theme.return_value = {'primary': 'cyan', 'secondary': 'cyan'}
        cli.current_show_title = 'Example'
        cli.current_show_url = 'https://topcinema.film/series/example/'
        cli.current_season = {'season_number': 1, 'season_part': 'Part 2',
                              'display_label': 'Season 1 Part 2'}
        cli.current_episodes = [{'url': 'https://topcinema.film/episode/example-1/'}]
        cli.current_episode_index = 0
        cli.history = HistoryManager(self.path)
        cli.log = Mock()
        cli.banner = Mock()
        state = MpvState()
        state.time_pos, state.duration = 50, 500
        with patch('cenima.cli.run_mpv_monitored', return_value=(0, state)), patch('cenima.cli.CONSOLE'):
            self.assertEqual(cli.play_video('https://cdn.test/video.mp4?expires=123', season_num=1,
                                           ep_num='1', content_type='series'), PlaybackOutcome.FINISHED)
        saved = cli.history.get_progress('Example', 1, '1', show_url=cli.current_show_url, season_part='Part 2')
        self.assertEqual(saved.episode_url, cli.current_episodes[0]['url'])
        self.assertEqual(saved.show_url, cli.current_show_url)
        self.assertEqual(saved.season_part, 'Part 2')


def test_history_progress_and_persistence(tmp_path):
    hist_file = tmp_path / "history.json"
    hm = HistoryManager(history_file=hist_file)

    p1 = hm.update_progress("Breaking Bad", season_number=1, episode_number="1", time_pos=10, duration=3000, content_type="series")
    assert p1.status == WatchStatus.UNWATCHED

    p2 = hm.update_progress("Breaking Bad", season_number=1, episode_number="2", time_pos=1200, duration=3000, content_type="series")
    assert p2.status == WatchStatus.IN_PROGRESS
    assert p2.progress_pct == 40.0

    p3 = hm.update_progress("Breaking Bad", season_number=1, episode_number="3", time_pos=2600, duration=3000, content_type="series")
    assert p3.status == WatchStatus.COMPLETED

    p4 = hm.update_progress("Inception", time_pos=8950, duration=9000, content_type="movie")
    assert p4.status == WatchStatus.COMPLETED

    # Verify reload from disk (atomic write persisted cleanly)
    hm2 = HistoryManager(history_file=hist_file)
    loaded = hm2.get_progress("  breaking   bad ", season_number=1, episode_number="2")
    assert loaded is not None
    assert loaded.time_pos == 1200.0
    assert loaded.status == WatchStatus.IN_PROGRESS

    watched_count, watched_eps = hm2.get_season_stats("Breaking Bad", 1)
    assert watched_count == 1
    assert watched_eps == ["3"]

    recent = hm2.get_recent_history(limit=2)
    assert len(recent) == 2
    assert recent[0].show_title == "Inception"

    hm2.clear_history()
    assert hm2.get_recent_history() == []


def test_history_recovers_from_corrupt_json(tmp_path):
    hist_file = tmp_path / "history.json"
    hist_file.write_text("{not valid json", encoding="utf-8")
    hm = HistoryManager(history_file=hist_file)
    assert hm.get_recent_history() == []
