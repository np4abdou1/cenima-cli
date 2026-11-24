from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from cenima.download import redact_reason
from cenima.managers import idm_eligible, send_to_idm
from cenima.processor import StreamURL


class ManagerTests(unittest.TestCase):
    def test_idm_rejects_hls_signed_links_and_required_headers(self):
        self.assertTrue(idm_eligible('https://public.test/film.mp4'))
        self.assertFalse(idm_eligible('https://public.test/index.m3u8'))
        self.assertFalse(idm_eligible('https://public.test/film.mp4?token=secret'))
        self.assertFalse(idm_eligible(StreamURL('https://public.test/film.mp4', headers={'Cookie': 'secret'})))
        self.assertFalse(idm_eligible(StreamURL('https://public.test/film.mp4', referer='https://catalog.test')))
        self.assertFalse(idm_eligible('https://cdn-video.xyz/film.mp4'))

    def test_idm_handoff_preserves_unicode_and_uses_argument_arrays(self):
        with tempfile.TemporaryDirectory() as directory, \
                patch('cenima.managers.detected_managers', return_value={'idm': 'IDMan.exe'}), \
                patch('cenima.managers.subprocess.run') as run:
            send_to_idm('https://public.test/film.mp4', directory, 'فيلم عربي')
        args = run.call_args.args[0]
        self.assertEqual(args[args.index('/f') + 1], 'فيلم عربي.mp4')
        self.assertIsInstance(args, list)

    def test_diagnostics_keep_http_reason_but_remove_signed_urls(self):
        result = redact_reason('ERROR: HTTP Error 403: Forbidden https://cdn.test/private/secret.mp4?token=secret')
        self.assertIn('HTTP Error 403: Forbidden', result)
        self.assertNotIn('secret', result)
