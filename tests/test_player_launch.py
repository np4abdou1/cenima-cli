"""Playback source validation, local media, and request-context regressions."""
from contextlib import ExitStack
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from cenima.mpv import MpvIpcError, MpvState, run_mpv_monitored
from cenima.processor import StreamURL


class PlayerLaunchTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.watcher = self.stack.enter_context(patch('cenima.mpv.MpvWatcher')).return_value
        self.watcher.state = MpvState()
        self.watcher.ident = None
        self.launch = self.stack.enter_context(patch('subprocess.Popen'))
        self.launch.return_value.wait.return_value = 0
        self.launch.return_value.poll.return_value = 0
        self.proxy = self.stack.enter_context(patch('cenima.mpv.StreamProxy'))
        self.proxy.return_value.start.return_value = 'http://127.0.0.1:1234/stream.mp4'

    def test_local_file_requires_explicit_mode_and_is_passed_as_a_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'episode café.mp4'
            path.write_bytes(b'fixture')
            with self.assertRaises(MpvIpcError):
                run_mpv_monitored(str(path))
            self.assertEqual(run_mpv_monitored(str(path), local_file=True)[0], 0)
            self.assertEqual(self.launch.call_args.args[0][-2:], ['--', str(path.resolve())])
            self.proxy.assert_not_called()

    def test_missing_local_file_does_not_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(MpvIpcError):
                run_mpv_monitored(str(Path(directory) / 'missing.mp4'), local_file=True)
        self.launch.assert_not_called()

    def test_directory_and_unsupported_remote_scheme_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(MpvIpcError):
                run_mpv_monitored(directory, local_file=True)
        with self.assertRaises(MpvIpcError):
            run_mpv_monitored('ftp://example.test/episode.mp4')
        self.launch.assert_not_called()

    def test_stream_referer_and_user_agent_survive_url_validation(self):
        stream = StreamURL('https://cdn.test/video.mp4', 'https://catalog.test/', {'User-Agent': 'FixtureUA'})
        run_mpv_monitored(stream)
        arguments = self.launch.call_args.args[0]
        self.assertIn('--referrer=https://catalog.test/', arguments)
        self.assertIn('--user-agent=FixtureUA', arguments)

    def test_custom_stream_headers_reach_proxy(self):
        stream = StreamURL('https://cdn.test/video.mp4', 'https://catalog.test/', {'X-Playback': 'required'})
        run_mpv_monitored(stream)
        self.assertEqual(self.proxy.call_args.kwargs['headers'], {'X-Playback': 'required'})
        self.assertEqual(self.launch.call_args.args[0][-1], 'http://127.0.0.1:1234/stream.mp4')
        self.proxy.return_value.stop.assert_called_once()

    def test_proxy_start_failure_is_cleaned_up_without_starting_watcher(self):
        self.proxy.return_value.start.side_effect = OSError('proxy failed')
        with self.assertLogs('cenima.mpv', level='ERROR'):
            self.assertEqual(run_mpv_monitored('https://vidtube.test/video.mp4')[0], 1)
        self.proxy.return_value.stop.assert_called_once()
        self.watcher.start.assert_not_called()
        self.launch.assert_not_called()
