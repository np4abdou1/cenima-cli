import asyncio
import io
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from rich.console import Console
from cenima.cli import CinemaCLI
from cenima.download import DownloadError, DownloadResult
from cenima.library import MediaStore
from cenima.models import WatchProgress, WatchStatus, PlaybackOutcome
from cenima.worker import DownloadWorker


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.store = MediaStore(self.root / 'state.sqlite3')
        self.metadata = {'catalog_url': 'https://topcinema.film/episode/one',
                         'show_url': 'https://topcinema.film/show', 'show_title': 'مسلسل عربي',
                         'download_title': 'مسلسل عربي S01E04', 'episode_number': '4',
                         'season_number': 1, 'content_type': 'series'}

    def test_restart_deduplication_and_metadata_excludes_signed_sources(self):
        identity = self.store.enqueue({**self.metadata, 'video_url': 'secret', 'headers': {'Cookie': 'secret'}}, self.root)
        other = {**self.metadata, 'catalog_url': 'https://topcinema.cam/episode/one/'}
        self.assertEqual(identity, self.store.enqueue(other, self.root))
        restored = MediaStore(self.store.path).jobs()
        self.assertEqual(len(restored), 1)
        self.assertEqual(restored[0]['show_title'], 'مسلسل عربي')
        self.assertNotIn('secret', restored[0]['metadata'])
        self.store.status(identity, 'running')
        with self.store.worker_lock():
            self.store.recover()
            with self.assertRaises(RuntimeError):
                with MediaStore(self.store.path).worker_lock():
                    pass
        self.assertEqual(self.store.jobs()[0]['status'], 'pending')

    def test_completed_library_is_atomic_and_detects_missing_or_truncated_files(self):
        identity = self.store.enqueue(self.metadata, self.root)
        path = self.root / 'مسلسل عربي S01E04.mp4'
        path.write_bytes(b'fixture')
        self.store.complete(self.store.jobs()[0], path)
        self.assertEqual(self.store.jobs()[0]['status'], 'completed')
        self.assertTrue(self.store.media()[0]['available'])
        path.write_bytes(b'bad')
        self.assertFalse(self.store.media()[0]['available'])
        path.unlink()
        self.assertFalse(self.store.media()[0]['available'])


class WorkerTests(StoreTests):
    def wait_for(self, predicate):
        deadline = time.monotonic() + 8
        while not predicate():
            if time.monotonic() > deadline:
                self.fail('Background worker timed out')
            time.sleep(0.02)

    def test_background_cancellation_retry_and_exit_recovery(self):
        identity = self.store.enqueue(self.metadata, self.root)
        started = threading.Event()
        mode = ['block']

        async def transfer(scraper, item, directory, title, progress):
            progress('25.0% 1MiB/s ETA 00:05')
            started.set()
            if mode[0] == 'block':
                await asyncio.Event().wait()
            path = Path(directory) / (title + '.mp4')
            path.write_bytes(b'completed')
            return DownloadResult(path)

        with patch('cenima.worker.download_catalog_item', side_effect=transfer):
            worker = DownloadWorker(self.store, lambda: object())
            try:
                before = time.monotonic()
                worker.start()
                self.assertLess(time.monotonic() - before, 0.5)
                self.assertTrue(started.wait(5))
                self.assertEqual(self.store.jobs()[0]['status'], 'running')
                self.assertIn('25.0%', self.store.jobs()[0]['progress'])
                worker.cancel(identity)
                self.wait_for(lambda: self.store.jobs()[0]['status'] == 'cancelled')
                self.assertFalse(self.store.media())
                mode[0] = 'complete'
                worker.start(retry_failed=True)
                self.wait_for(lambda: self.store.jobs()[0]['status'] == 'completed')
                self.assertTrue(self.store.media()[0]['available'])
            finally:
                worker.shutdown()
            self.assertFalse(worker.thread.is_alive())

        second = self.store.enqueue({**self.metadata, 'catalog_url': 'https://catalog.test/two',
                                     'download_title': 'Second'}, self.root)
        started.clear()
        mode[0] = 'block'
        with patch('cenima.worker.download_catalog_item', side_effect=transfer):
            worker = DownloadWorker(self.store, lambda: object())
            worker.start()
            self.assertTrue(started.wait(5))
            worker.shutdown()
        self.assertEqual(next(j for j in self.store.jobs() if j['id'] == second)['status'], 'pending')
        self.assertFalse(worker.thread.is_alive())

    def test_one_failed_episode_does_not_stop_later_jobs(self):
        self.store.enqueue(self.metadata, self.root)
        self.store.enqueue({**self.metadata, 'catalog_url': 'https://catalog.test/two',
                            'download_title': 'Second'}, self.root)

        async def transfer(scraper, item, directory, title, progress):
            if title == self.metadata['download_title']:
                raise DownloadError('HTTP Error 403: Forbidden')
            path = Path(directory) / 'Second.mp4'
            path.write_bytes(b'completed')
            return DownloadResult(path)

        with patch('cenima.worker.download_catalog_item', side_effect=transfer):
            worker = DownloadWorker(self.store, lambda: object())
            try:
                worker.start()
                self.wait_for(lambda: any(j['status'] == 'completed' for j in self.store.jobs()))
            finally:
                worker.shutdown()
        self.assertEqual({j['status'] for j in self.store.jobs()}, {'completed', 'failed'})
        self.assertIn('403', next(j for j in self.store.jobs() if j['status'] == 'failed')['error'])

    def test_retry_of_cancelled_queued_item_does_not_repeat_old_cancellation(self):
        identity = self.store.enqueue(self.metadata, self.root)
        async def transfer(scraper, item, directory, title, progress):
            path = Path(directory) / (title + '.mp4')
            path.write_bytes(b'completed')
            return DownloadResult(path)
        with patch('cenima.worker.download_catalog_item', side_effect=transfer):
            worker = DownloadWorker(self.store, lambda: object())
            worker.cancel(identity)
            self.assertEqual(self.store.jobs()[0]['status'], 'cancelled')
            try:
                worker.start(retry_failed=True)
                self.wait_for(lambda: self.store.jobs()[0]['status'] == 'completed')
            finally:
                worker.shutdown()


class ResumeTests(unittest.IsolatedAsyncioTestCase):
    async def test_quick_resume_resolves_saved_episode_without_search(self):
        cli = object.__new__(CinemaCLI)
        cli._load_with_retry = AsyncMock(return_value=[{'name': 'server'}])
        cli.select_and_play = AsyncMock(return_value=PlaybackOutcome.FINISHED)
        progress = WatchProgress(show_title='مسلسل عربي', show_url='https://catalog.test/show',
            episode_url='https://catalog.test/episode/4', episode_number='4', season_number=1,
            season_part='Part 2', time_pos=120, status=WatchStatus.IN_PROGRESS)
        with patch('cenima.cli.scraper') as scraper:
            await cli.resume_progress(progress)
        scraper.search.assert_not_called()
        self.assertEqual(cli._load_with_retry.call_args.args[2], {'url': progress.episode_url})
        self.assertEqual(cli.current_season['season_part'], 'Part 2')
        cli.select_and_play.assert_awaited_once()
        self.assertFalse(cli._resume_direct)

    async def test_library_playback_does_not_access_scraper(self):
        cli = object.__new__(CinemaCLI)
        item = {'show_title': 'Film', 'download_title': 'Film', 'show_url': 'https://catalog.test/movie',
                'catalog_url': 'https://catalog.test/movie', 'content_type': 'movie',
                'path': '/local/Film.mp4', 'available': True}
        cli._media_store = Mock()
        cli._media_store.media.return_value = [item]
        cli.run_fzf_objects = Mock(side_effect=[item, None])
        cli.play_video = Mock()
        with patch('cenima.cli.scraper') as scraper:
            cli.show_library()
        scraper.assert_not_called()
        self.assertEqual(cli.play_video.call_args.args[0], '/local/Film.mp4')
