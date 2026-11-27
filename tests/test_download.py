"""Real yt-dlp transfers through the proxy against generated local media."""
import asyncio
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import hashlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import AsyncMock, Mock, patch

from rich.console import Console
from cenima.cli import CinemaCLI
from cenima.download import DownloadError, download_stem, download_stream, download_catalog_item, partial_root, media_title
from cenima.processor import StreamURL
from cenima.library import MediaStore


class DownloadNamingTests(unittest.TestCase):
    def test_catalog_identity_is_stable_across_mirrors_and_safe_on_windows(self):
        title = '../CON: فيلم % title'
        one = download_stem(title, 'https://topcinema.film/episode/one/')
        self.assertEqual(one, download_stem(title, 'https://topcinema.cam/episode/one'))
        self.assertEqual(one, download_stem(title, 'https://topcinema.cam/episode/two'))
        self.assertFalse(any(char in one for char in '/\\:'))
        self.assertEqual(media_title('مسلسل عربي', 1, '4'), 'مسلسل عربي S01E04')
        self.assertEqual(media_title('فيلم عربي'), 'فيلم عربي')
        self.assertTrue(media_title('A' * 200, 1, 4).endswith('S01E04'))
        self.assertNotEqual(media_title('A' * 200, 1, 4), media_title('A' * 200, 1, 5))


class BatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_transfer_failure_uses_next_server_and_expiry_refreshes_catalog_once(self):
        for refresh in (False, True):
            scraper = Mock(base_url='https://catalog.test')
            dead = {'video_url': StreamURL('https://cdn.test/old.mp4')}
            good = {'video_url': StreamURL('https://cdn.test/new.mp4')}
            scraper.fetch_episode_servers.side_effect = [[dead], [good]] if refresh else [[dead, good]]
            result = Mock()
            with patch('cenima.download.download_stream', new_callable=AsyncMock,
                       side_effect=[DownloadError('HTTP 403'), result]) as transfer:
                self.assertIs(await download_catalog_item(scraper, {'url': 'catalog'}, 'out', 'title'), result)
            self.assertEqual(transfer.await_count, 2)
            self.assertEqual(scraper.fetch_episode_servers.call_count, 2 if refresh else 1)

    async def test_batch_is_persisted_and_started_without_waiting_for_transfers(self):
        cli = object.__new__(CinemaCLI)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        cli._media_store = MediaStore(directory / 'state.sqlite3')
        cli._download_worker = Mock()
        cli.config = Mock(download_dir=str(directory))
        cli.config.get_theme.return_value = {}
        cli.current_show_title = 'Example'
        cli.banner = Mock()
        cli.history = Mock()
        cli.run_fzf = Mock(return_value=0)
        episodes = [{'url': str(i), 'episode_number': str(i)} for i in (1, 2)]
        resolver = Mock(base_url='https://catalog.test')
        resolver.fetch_episode_servers.side_effect = [OSError('offline'), [
            {'video_url': StreamURL('https://cdn.test/file.mp4', referer='https://catalog.test')}]]
        with patch('cenima.cli.scraper', resolver), patch('cenima.download.check_download_tools'), \
                patch('cenima.download.download_catalog_item', new_callable=AsyncMock) as transfer, \
                patch('cenima.cli.CONSOLE', Console(file=io.StringIO())):
            output = directory / 'completed.mp4'
            output.write_bytes(b'media fixture')
            transfer.side_effect = [OSError('offline'), Mock(skipped=False, path=output)]
            await cli.download_items(episodes, {'season_number': 1})
        transfer.assert_not_awaited()
        cli._download_worker.start.assert_called_once()
        self.assertEqual(len(cli.media_store.jobs()), 2)
        cli.history.update_progress.assert_not_called()
        self.assertEqual(cli.run_fzf.call_args.kwargs['title'], 'Queued in background')


@unittest.skipUnless(importlib.util.find_spec('yt_dlp') and shutil.which('ffmpeg'),
                     'Requires optional yt-dlp and ffmpeg')
class DownloadIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        args = ['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'color=c=blue:s=160x90:r=10',
                '-t', '2', '-c:v', 'libx264', '-pix_fmt', 'yuv420p']
        subprocess.run(args + [str(cls.root / 'clip.mp4')], check=True, capture_output=True)
        subprocess.run(args + ['-g', '10', '-sc_threshold', '0', '-f', 'hls', '-hls_time', '1', '-hls_list_size', '0',
                              str(cls.root / 'index.m3u8')], check=True, capture_output=True)
        cls.requests = []
        cls.fragment_lock = threading.Lock()
        cls.fragment_active = cls.fragment_peak = 0

        class Handler(SimpleHTTPRequestHandler):
            def do_GET(self):
                fragment = self.path.split('?')[0].endswith('.ts')
                if fragment:
                    with cls.fragment_lock:
                        cls.fragment_active += 1
                        cls.fragment_peak = max(cls.fragment_peak, cls.fragment_active)
                try:
                    if fragment:
                        time.sleep(0.15)  # Simulate per-fragment request latency.
                    self._serve()
                finally:
                    if fragment:
                        with cls.fragment_lock:
                            cls.fragment_active -= 1

            def _serve(self):
                cls.requests.append((self.path, dict(self.headers)))
                if self.headers.get('X-Media-Test') != 'preserved':
                    self.send_error(403)
                    return
                if self.path.startswith('/range.mp4'):
                    content = (cls.root / 'clip.mp4').read_bytes()
                    start = int(self.headers.get('Range', 'bytes=0-').split('=')[1].split('-')[0])
                    self.send_response(206 if start else 200)
                    self.send_header('Content-Type', 'video/mp4')
                    self.send_header('Content-Length', str(len(content) - start))
                    if start:
                        self.send_header('Content-Range', f'bytes {start}-{len(content)-1}/{len(content)}')
                    self.end_headers()
                    self.wfile.write(content[start:])
                    return
                super().do_GET()

            def log_message(self, *args):
                pass

        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), partial(Handler, directory=cls.temp.name))
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.base = f'http://127.0.0.1:{cls.server.server_port}'

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()
        cls.temp.cleanup()

    def stream(self, path):
        return StreamURL(self.base + path, referer='https://catalog.test/',
                         headers={'X-Media-Test': 'preserved'})

    async def test_direct_file_is_identical_and_repeat_is_skipped(self):
        out = self.root / 'مسار عربي' / 'direct'
        events = []
        result = await download_stream(self.stream('/clip.mp4'), out, 'Film % Arabic فيلم', 'https://catalog.test/film')
        self.assertEqual(result.path.read_bytes(), (self.root / 'clip.mp4').read_bytes())
        repeat = await download_stream(self.stream('/clip.mp4?new-token=1'), out,
                                       'Film % Arabic فيلم', 'https://catalog.test/film')
        self.assertTrue(repeat.skipped)
        self.assertIn('فيلم', result.path.name)
        self.assertEqual(result.path.name, 'Film % Arabic فيلم.mp4')

    async def test_hls_produces_playable_media_and_preserves_segment_headers(self):
        self.__class__.fragment_peak = 0
        result = await download_stream(self.stream('/index.m3u8'), self.root / 'hls',
                                       'Episode 1', 'https://catalog.test/episode/1')
        self.assertGreater(result.path.stat().st_size, 0)
        probe = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(result.path), '-f', 'null', '-'],
                               capture_output=True)
        self.assertEqual(probe.returncode, 0, probe.stderr)
        self.assertTrue(any(path.endswith('.ts') for path, _ in self.requests))
        self.assertTrue(all(headers.get('Referer') == 'https://catalog.test/' for _, headers in self.requests))
        self.assertGreater(self.fragment_peak, 1, 'Unlimited HLS must overlap fragment requests')
        self.assertLessEqual(self.fragment_peak, 4)

    async def test_explicit_rate_limit_reaches_downloader_and_keeps_fragments_serial(self):
        spawn = asyncio.create_subprocess_exec
        with patch('cenima.download.asyncio.create_subprocess_exec', wraps=spawn) as launch:
            result = await download_stream(self.stream('/clip.mp4'), self.root / 'limited',
                'Limited', 'https://catalog.test/limited', quality='720p', speed='2MB/s')
        arguments = next(call.args for call in launch.call_args_list
                         if '--force-generic-extractor' in call.args)
        self.assertEqual(arguments[arguments.index('--limit-rate') + 1], '2M')
        self.assertEqual(arguments[arguments.index('--concurrent-fragments') + 1], '1')
        self.assertEqual(arguments[arguments.index('--format-sort') + 1], 'res:720')
        self.assertEqual(result.path.read_bytes(), (self.root / 'clip.mp4').read_bytes())

    async def test_upstream_failure_does_not_publish_a_final_file(self):
        out = self.root / 'failure'
        with self.assertRaisesRegex(DownloadError, '404'):
            await download_stream(self.stream('/missing.mp4'), out, 'Missing', 'https://catalog.test/missing')
        self.assertFalse(list(out.glob('*.mp4')))

    async def test_same_filename_cannot_silently_skip_a_different_catalog_item(self):
        out = self.root / 'collision'
        await download_stream(self.stream('/clip.mp4'), out, 'Film', 'https://catalog.test/one')
        with self.assertRaisesRegex(DownloadError, 'different catalog item'):
            await download_stream(self.stream('/clip.mp4'), out, 'Film', 'https://catalog.test/two')

    async def test_changed_file_is_not_mistaken_for_a_completed_download(self):
        out = self.root / 'changed'
        result = await download_stream(self.stream('/clip.mp4'), out, 'Film', 'https://catalog.test/one')
        result.path.write_bytes(b'changed')
        with self.assertRaisesRegex(DownloadError, 'has changed'):
            await download_stream(self.stream('/clip.mp4'), out, 'Film', 'https://catalog.test/one')

    @unittest.skipUnless(shutil.which('aria2c'), 'Requires optional aria2c')
    async def test_real_aria2_direct_transfer_uses_the_header_preserving_proxy(self):
        result = await download_stream(self.stream('/range.mp4'), self.root / 'aria2',
                                      'فيلم عربي', 'https://catalog.test/aria2', manager='aria2c')
        self.assertEqual(result.path.read_bytes(), (self.root / 'clip.mp4').read_bytes())

    async def test_direct_resume_uses_range_and_ignored_ranges_restart_cleanly(self):
        for path in ('/range.mp4', '/clip.mp4'):
            with self.subTest(path=path):
                out = self.root / ('resume-range' if 'range' in path else 'resume-ignored')
                stem = download_stem('Resume', 'https://catalog.test/resume')
                source = hashlib.sha256((self.base.split('://')[1] + path).encode()).hexdigest()[:16]
                partial = partial_root(out, 'Resume') / source / (stem + '.mp4.part')
                partial.parent.mkdir(parents=True)
                original = (self.root / 'clip.mp4').read_bytes()
                partial.write_bytes(original[:100])
                result = await download_stream(self.stream(path), out, 'Resume', 'https://catalog.test/resume')
                self.assertEqual(result.path.read_bytes(), original)
                self.assertTrue(any(p == path and h.get('Range') == 'bytes=100-' for p, h in self.requests))

    async def test_query_selected_variants_never_share_partial_bytes(self):
        out = self.root / 'variants'
        low = '/range.mp4?quality=low'
        high = '/range.mp4?quality=high'
        source = hashlib.sha256((self.base.split('://')[1] + low).encode()).hexdigest()[:16]
        partial = partial_root(out, 'Variant') / source / 'Variant.mp4.part'
        partial.parent.mkdir(parents=True)
        partial.write_bytes(b'x' * 100)
        result = await download_stream(self.stream(high), out, 'Variant', 'https://catalog.test/variant')
        self.assertEqual(result.path.read_bytes(), (self.root / 'clip.mp4').read_bytes())
        self.assertFalse(any(p == high and h.get('Range') == 'bytes=100-' for p, h in self.requests))

    async def test_cancellation_reaps_child_and_releases_item_lock(self):
        out = self.root / 'cancel'
        # Use a real idle subprocess to test cancellation independently of CDN speed.
        worker = [sys.executable, '-c', 'import time; time.sleep(60)']
        with patch('cenima.download.check_download_tools', return_value=worker):
            task = asyncio.create_task(download_stream(self.stream('/clip.mp4'), out,
                                                       'Cancel', 'https://catalog.test/cancel'))
            await asyncio.sleep(0.3)
            with self.assertRaises(DownloadError):
                await download_stream(self.stream('/clip.mp4'), out, 'Cancel', 'https://catalog.test/cancel')
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        result = await download_stream(self.stream('/clip.mp4'), out, 'Cancel', 'https://catalog.test/cancel')
        self.assertTrue(result.path.exists())

    async def test_stalled_subprocess_is_reaped(self):
        worker = [sys.executable, '-c', 'import time; time.sleep(60)']
        with patch('cenima.download.check_download_tools', return_value=worker):
            with self.assertRaisesRegex(DownloadError, 'stalled'):
                await download_stream(self.stream('/clip.mp4'), self.root / 'stall',
                                      'Stall', 'https://catalog.test/stall', stall_timeout=0.2)

    @unittest.skipUnless(os.name == 'nt', 'Windows process-tree qualification')
    async def test_cancellation_terminates_real_descendant_process(self):
        import ctypes
        from ctypes import wintypes
        pid_file = self.root / 'grandchild.pid'
        code = ("import subprocess,sys,time; from pathlib import Path; "
                "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)']); "
                "Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)")
        command = [sys.executable, '-c', code, str(pid_file)]
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = None
        with patch('cenima.download.check_download_tools', return_value=command):
            task = asyncio.create_task(download_stream(self.stream('/clip.mp4'), self.root / 'tree',
                                                        'Tree', 'https://catalog.test/tree'))
            try:
                for _ in range(100):
                    if pid_file.exists() and pid_file.read_text().strip():
                        break
                    await asyncio.sleep(0.03)
                self.assertTrue(pid_file.exists() and pid_file.read_text().strip())
                handle = kernel.OpenProcess(0x00100000, False, int(pid_file.read_text()))
                self.assertTrue(handle)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertEqual(kernel.WaitForSingleObject(handle, 2000), 0)
            finally:
                if not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                if handle:
                    kernel.CloseHandle(handle)
