import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from cenima.atomic import atomic_write_json, load_json_state
from cenima.diagnostics import redact_diagnostic
from cenima.validation import (validate_http_url, validate_local_proxy_url,
                               safe_output_path, safe_import_file)
from cenima.diagnostics import log_path
from cenima.cli import CinemaCLI
from cenima.download import available_download_qualities, explicit_quality_fallback
from cenima.provider import ProviderFallback
from cenima.scraper import clean_show_title
from cenima.settings_io import (SCHEMA_VERSION, export_settings,
                                validate_settings_payload,
                                write_settings_export, read_settings_import)
from cenima.subtitles import (normalize_language, select_subtitle_track,
                              mpv_subtitle_args)
from cenima.watch_later import WatchLaterStore
from cenima.worker import DownloadWorker
from rich.text import Text


class AtomicStateTests(unittest.TestCase):
    def test_atomic_json_round_trip_and_malformed_state_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            atomic_write_json(path, {'theme': 'gold', 'future_field': 7})
            self.assertEqual(json.loads(path.read_text(encoding='utf-8'))['future_field'], 7)
            path.write_text('{partial', encoding='utf-8')
            self.assertEqual(load_json_state(path, {}), {})


class DiagnosticTests(unittest.TestCase):
    def test_log_location_is_bounded_under_config_directory(self):
        path = log_path()
        self.assertEqual(path.name, 'cenima.log')
        self.assertIn('logs', str(path.parent))
    def test_signed_url_cookie_and_home_are_redacted(self):
        message = ('https://cdn.test/video.mp4?token=secret '
                   'Cookie: abc authorization=Bearer123 C:\\Users\\Test\\Downloads')
        redacted = redact_diagnostic(message)
        self.assertNotIn('secret', redacted)
        self.assertNotIn('abc', redacted)
        self.assertNotIn('Bearer123', redacted)
        self.assertNotIn('C:\\Users\\Test', redacted)


class ValidationTests(unittest.TestCase):
    def test_url_schemes_and_loopback_proxy_are_strict(self):
        self.assertEqual(validate_http_url('https://example.test/video.mp4'),
                         'https://example.test/video.mp4')
        for unsafe in ('file:///tmp/video.mp4', 'javascript:alert(1)',
                       'ftp://example.test/video.mp4', 'https://'):
            with self.subTest(unsafe=unsafe):
                with self.assertRaises(ValueError):
                    validate_http_url(unsafe)
        self.assertEqual(validate_local_proxy_url('http://127.0.0.1:8123/stream.mp4'),
                         'http://127.0.0.1:8123/stream.mp4')
        with self.assertRaises(ValueError):
            validate_local_proxy_url('http://example.test/stream.mp4')

    def test_output_and_import_paths_cannot_escape_base(self):
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            output = safe_output_path(base, 'فيلم عربي.mp4')
            self.assertEqual(output.parent, base)
            with self.assertRaises(ValueError):
                safe_output_path(base, '../outside.mp4')
            source = base / 'inside.mp4'
            source.write_bytes(b'x')
            self.assertEqual(safe_import_file(base, source), source)
            with self.assertRaises(ValueError):
                safe_import_file(base, base.parent / 'outside.mp4')


class P1FeatureTests(unittest.TestCase):
    def test_mirror_watch_later_deduplicates_and_reloads(self):
        with tempfile.TemporaryDirectory() as directory:
            store = WatchLaterStore(Path(directory) / 'watch-later.json')
            one = {'title': 'فيلم عربي',
                   'show_url': 'https://topcinema.film/movie/example/',
                   'url': 'https://topcinema.film/movie/example/',
                   'content_type': 'movie'}
            mirror = {'title': 'فيلم عربي',
                      'show_url': 'https://topcinema.cam/movie/example/',
                      'url': 'https://topcinema.cam/movie/example/',
                      'content_type': 'movie'}
            store.add(one)
            store.add(mirror)
            self.assertEqual(len(store.list()), 1)
            self.assertTrue(store.contains(mirror))
            self.assertTrue(store.remove(mirror))
            self.assertFalse(store.contains(one))

    def test_quality_preferences_and_arabic_titles(self):
        cli = object.__new__(CinemaCLI)
        cli.config = Mock(download_quality='720p', download_speed='5MB/s')
        cli.config.to_dict = Mock(return_value={
            'download_quality': cli.config.download_quality,
            'download_speed': cli.config.download_speed,
        })
        payload = cli.config.to_dict()
        self.assertEqual(payload['download_quality'], '720p')
        self.assertEqual(payload['download_speed'], '5MB/s')
        self.assertEqual(clean_show_title(
            'الموج الموسم الثاني الحلقة 3 1080p مترجم'), 'الموج')
        title = 'فيلم عربي طويل - 1080p'
        self.assertIn('عربي', clean_show_title(title))
        self.assertTrue(Text(title).cell_len > 0)

    def test_available_quality_fallback(self):
        available = available_download_qualities(['1080p WEB-DL', '720p', '480p'])
        self.assertEqual(available, ['1080p', '720p', '480p'])
        self.assertEqual(explicit_quality_fallback('720p', available), '720p')
        self.assertEqual(explicit_quality_fallback('360p', available), 'fallback')


class P2FeatureTests(unittest.TestCase):
    def test_arabic_subtitle_preference_and_fallback(self):
        tracks = [
            {'type': 'sub', 'id': 1, 'lang': 'eng', 'title': 'English'},
            {'type': 'sub', 'id': 2, 'lang': 'ara', 'title': 'Arabic'},
        ]
        self.assertEqual(select_subtitle_track(tracks, 'ar')['id'], 2)
        self.assertEqual(select_subtitle_track(tracks, 'fr')['id'], 1)
        self.assertEqual(normalize_language('Arabic'), 'ar')
        self.assertIn('--slang=ar', mpv_subtitle_args('ar')[0])

    def test_settings_export_import_and_validation(self):
        config = Mock()
        config.to_dict.return_value = {
            'theme': 'gold',
            'download_dir': r'D:\Cinema_Cli_Download',
            'subtitle_language': 'ar',
            'token': 'top-secret',
            'cookie': 'private',
        }
        exported = export_settings(config)
        self.assertEqual(exported['schema'], SCHEMA_VERSION)
        self.assertNotIn('token', exported['settings'])
        self.assertNotIn('cookie', exported['settings'])
        with tempfile.TemporaryDirectory() as directory:
            path = write_settings_export(config, Path(directory) / 'settings.json')
            imported = read_settings_import(path)
            self.assertEqual(imported['theme'], 'gold')
        with self.assertRaisesRegex(ValueError, 'schema'):
            validate_settings_payload({'schema': 999, 'settings': {}})
        with self.assertRaisesRegex(ValueError, 'unsupported'):
            validate_settings_payload({
                'schema': SCHEMA_VERSION,
                'settings': {'credential': 'secret'},
            })

    def test_provider_fallback_and_notification_callback(self):
        fallback = ProviderFallback(['https://one.test', 'https://two.test'])
        first = Mock()
        first.search.side_effect = OSError('offline')
        second = Mock()
        second.search.return_value = [{'title': 'Recovered'}]
        fallback._providers = {'https://one.test': first, 'https://two.test': second}
        self.assertEqual(fallback.search('query'), [{'title': 'Recovered'}])
        self.assertIn('https://one.test', fallback.failed)
        notifier = Mock()
        store = Mock()
        store.jobs.return_value = [{'status': 'completed'}]
        worker = DownloadWorker(store, Mock(),
                                notifications_enabled=True, notify=notifier)
        self.assertTrue(worker.notifications_enabled)
        self.assertIs(worker.notify, notifier)
