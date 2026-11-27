import io
import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from cenima.transfer_safety import probe_source


class PreflightTests(unittest.TestCase):
    def test_rejected_head_uses_one_range_request_and_closes_error(self):
        for status in (403, 404, 405, 501):
            with self.subTest(status=status):
                body = io.BytesIO(b'HEAD unavailable')
                failure = HTTPError('http://127.0.0.1/stream.mp4', status, 'HEAD unavailable', {}, body)
                response = io.BytesIO(b'x')
                response.headers = {'Content-Type': 'video/mp4', 'Content-Length': '1',
                                    'Content-Range': 'bytes 0-0/1234'}
                with patch('cenima.transfer_safety.urlopen', side_effect=[failure, response]) as request:
                    result = probe_source('http://127.0.0.1/stream.mp4')
                self.assertEqual(request.call_count, 2)
                self.assertEqual(request.call_args.args[0].get_header('Range'), 'bytes=0-0')
                self.assertEqual(result['size'], 1234)
                self.assertTrue(body.closed)
                self.assertTrue(response.closed)
