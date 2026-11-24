import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from cenima.cli import Config
from cenima.library import MediaStore
from cenima.storage import consolidate, media_folder, rename_managed_title


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = MediaStore(self.root / 'state.sqlite3')
        self.metadata = {'catalog_url': 'https://catalog.test/film', 'show_url': 'https://catalog.test/film',
                         'show_title': 'فيلم عربي', 'download_title': 'فيلم عربي', 'content_type': 'movie'}

    def test_move_updates_library_pending_jobs_and_preserves_dedup_identity(self):
        old = self.root / 'old'
        old.mkdir()
        path = old / 'فيلم عربي.mp4'
        path.write_bytes(b'media')
        identity = self.store.enqueue(self.metadata, old)
        self.store.complete(self.store.jobs()[0], path)
        pending = self.store.enqueue({**self.metadata, 'catalog_url': 'https://catalog.test/second'}, old)
        target = self.root / 'selected'
        with self.store.worker_lock():
            result = consolidate(self.store, target)
        self.assertEqual(result['moved'], 1)
        self.assertFalse(path.exists())
        self.assertEqual(self.store.media()[0]['path'], str(target / path.name))
        self.assertTrue(self.store.media()[0]['available'])
        self.assertTrue(all(job['directory'] == str(target) for job in self.store.jobs()))
        self.assertEqual(identity, self.store.enqueue(self.metadata, target))
        self.assertEqual(len(self.store.jobs()), 2)
        self.assertTrue(list((target / '.partial').rglob('owner.json')))

    def test_conflicts_rejected_before_any_move_and_identical_files_are_merged(self):
        one, two, target = self.root / 'one.mp4', self.root / 'two.mp4', self.root / 'selected'
        one.write_bytes(b'first')
        two.write_bytes(b'other')
        records = [{'path': str(p), 'filename': 'Film.mp4', 'metadata': self.metadata} for p in (one, two)]
        with self.assertRaises(FileExistsError):
            consolidate(self.store, target, records)
        self.assertTrue(one.exists() and two.exists())
        self.assertFalse(list(target.glob('*.mp4')))
        two.write_bytes(b'first')
        result = consolidate(self.store, target, records)
        self.assertEqual(result['deduplicated'], 1)
        self.assertEqual((target / 'Film.mp4').read_bytes(), b'first')
        self.assertFalse(one.exists() or two.exists())
        self.assertEqual(len(self.store.media()), 1)

    def test_folder_setting_round_trips_unicode_and_preserves_other_settings(self):
        config_file = self.root / 'config.json'
        config_file.write_text(json.dumps({'custom_preference': 'keep', 'theme': 'cinema'}), encoding='utf-8')
        with patch('cenima.cli.CONFIG_DIR', self.root), patch('cenima.cli.CONFIG_FILE', config_file):
            config = Config()
            selected = media_folder((self.root / 'أفلام').as_uri())
            config.download_dir = str(selected)
            self.assertTrue(config.save())
            self.assertEqual(Config().download_dir, str(selected))
        self.assertEqual(json.loads(config_file.read_text(encoding='utf-8'))['custom_preference'], 'keep')
        with self.assertRaises(ValueError):
            media_folder('https://example.com/folder')

    def test_explicit_title_correction_renames_file_and_updates_both_indexes(self):
        metadata = {**self.metadata, 'show_title': 'Yaga مسلسل عربي',
                    'download_title': 'Yaga مسلسل عربي S01E06', 'season_number': 1, 'episode_number': '6'}
        path = self.root / 'Yaga مسلسل عربي S01E06.mp4'
        path.write_bytes(b'media')
        identity = self.store.enqueue(metadata, self.root)
        self.store.complete(self.store.jobs()[0], path)
        with self.store.worker_lock():
            updated = rename_managed_title(self.store, identity, 'Yaga')
        self.assertEqual(updated.name, 'Yaga S01E06.mp4')
        self.assertEqual(updated.read_bytes(), b'media')
        self.assertFalse(path.exists())
        self.assertTrue(self.store.media()[0]['available'])
        self.assertEqual(self.store.media()[0]['show_title'], 'Yaga')
        self.assertEqual(self.store.jobs()[0]['download_title'], 'Yaga S01E06')
