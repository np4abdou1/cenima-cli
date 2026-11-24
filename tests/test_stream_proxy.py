import io
import re
import unittest
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit
from unittest.mock import Mock, patch

from cenima.mpv import StreamProxy


class StreamProxyTests(unittest.TestCase):
    def setUp(self):
        self.responses = []
        self.sessions = []
        self.calls = []
        self.resources = {}

        def factory(**kwargs):
            session = Mock()
            session.closed_event = threading.Event()
            session.close.side_effect = session.closed_event.set
            self.sessions.append(session)

            def request(url, **kwargs):
                self.calls.append((url, kwargs))
                body, mime, final, status = self.resources[url]
                response = Mock()
                response.status_code = status
                response.url = final or url
                response.headers = {'Content-Type': mime, 'Content-Length': str(len(body)),
                                    'Content-Range': 'bytes 0-3/100', 'Accept-Ranges': 'bytes'}
                response.iter_content.return_value = iter([body])
                response.closed_event = threading.Event()
                response.close.side_effect = response.closed_event.set
                self.responses.append(response)
                return response

            session.get.side_effect = request
            session.head.side_effect = request
            return session

        self.patch = patch('curl_cffi.requests.Session', side_effect=factory)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.proxy = StreamProxy('https://origin.test/master.m3u8', referer='https://embed.test/')
        self.url = self.proxy.start()
        self.addCleanup(self.proxy.stop)

    def fetch(self, url, headers=None):
        with urlopen(Request(url, headers=headers or {}), timeout=5) as response:
            result = response.read(), response.headers, response.status
        if self.responses:
            self.assertTrue(self.responses[-1].closed_event.wait(2))
        return result

    def test_redirected_master_variant_keys_maps_segments_and_ranges(self):
        self.resources[self.proxy.target_url] = (
            b'#EXTM3U\n#EXT-X-MEDIA:TYPE=AUDIO,URI="../audio/list.m3u8"\nvariant/video.m3u8?token=a\n',
            'application/vnd.apple.mpegurl', 'https://edge.test/root/master.m3u8', 200)
        self.resources['https://edge.test/root/variant/video.m3u8?token=a'] = (
            b'#EXTM3U\n#EXT-X-KEY:METHOD=AES-128,URI="//keys.test/key?token=x"\n'
            b'#EXT-X-MAP:URI="../init.mp4"\n#EXT-X-PART:DURATION=1,URI="part.m4s"\n'
            b'../segment.ts?token=z\n', 'application/vnd.apple.mpegurl', None, 200)
        master, headers, _ = self.fetch(self.url)
        self.assertEqual(int(headers['Content-Length']), len(master))
        self.assertIsNone(headers.get('Content-Range'))
        variant = master.decode().splitlines()[-1]
        body, _, _ = self.fetch(variant)
        uris = re.findall(r'URI="([^"]+)"', body.decode())
        self.assertEqual(len(uris), 3)
        for uri in uris:
            self.assertTrue(uri.startswith(f'http://127.0.0.1:{self.proxy.port}/resource/'))
        segment = body.decode().splitlines()[-1]
        self.resources['https://edge.test/root/segment.ts?token=z'] = (b'data', 'video/mp2t', None, 206)
        data, headers, status = self.fetch(segment, {'Range': 'bytes=0-3'})
        self.assertEqual((data, status), (b'data', 206))
        self.assertEqual(headers['Content-Range'], 'bytes 0-3/100')
        self.assertEqual(self.calls[-1][1]['headers']['Range'], 'bytes=0-3')
        for url in ['https://keys.test/key?token=x', 'https://edge.test/root/init.mp4',
                    'https://edge.test/root/variant/part.m4s']:
            self.assertIn(url, self.proxy._resources.values())
        for response in self.responses:
            response.close.assert_called_once()
        for session in self.sessions:
            session.close.assert_called_once()

    def test_disconnect_closes_upstream_response_and_session(self):
        self.resources[self.proxy.target_url] = (b'video', 'video/mp4', 'https://cdn.test/video.mp4', 206)
        handler = object.__new__(self.proxy._server.RequestHandlerClass)
        handler.path = urlsplit(self.url).path
        handler.headers = {'Range': 'bytes=100-'}
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.wfile = Mock()
        handler.wfile.write.side_effect = BrokenPipeError('seek disconnected old request')
        handler.do_GET()
        self.responses[-1].close.assert_called_once()
        self.sessions[-1].close.assert_called_once()
        self.assertEqual(self.proxy._active, {})

    def test_upstream_failure_returns_502_and_closes_session(self):
        with self.assertRaises(HTTPError) as error:
            self.fetch(self.url)
        self.assertEqual(error.exception.code, 502)
        error.exception.close()
        self.assertTrue(self.sessions[-1].closed_event.wait(2))
        self.sessions[-1].close.assert_called_once()

    def test_unknown_resource_cannot_supply_an_upstream_url(self):
        with self.assertRaises(HTTPError) as error:
            self.fetch(f'http://127.0.0.1:{self.proxy.port}/resource/unknown?url=http://internal')
        self.assertEqual(error.exception.code, 404)
        error.exception.close()
        self.assertEqual(self.calls, [])

    def test_live_reload_keeps_tokens_and_player_query(self):
        self.resources['https://origin.test/master.m3u8?_HLS_msn=10&token=a'] = (
            b'#EXTM3U\nsegment.ts\n', 'application/vnd.apple.mpegurl', None, 200)
        body, _, _ = self.fetch(self.url + '?_HLS_msn=10&token=a')
        self.assertIn(b'/resource/', body)

    def test_head_closes_upstream_without_reading_body(self):
        self.resources[self.proxy.target_url] = (b'video', 'video/mp4', None, 200)
        with urlopen(Request(self.url, method='HEAD'), timeout=5) as response:
            self.assertEqual(response.read(), b'')
        self.responses[-1].iter_content.assert_not_called()
        self.assertTrue(self.responses[-1].closed_event.wait(2))
        self.responses[-1].close.assert_called_once()
        self.sessions[-1].close.assert_called_once()

    def test_stop_closes_active_resources_even_when_response_close_fails(self):
        session, response = Mock(), Mock()
        response.close.side_effect = OSError('connection closed')
        self.proxy._active[1] = (session, response)
        self.proxy.stop()
        session.close.assert_called_once()
        self.proxy._active.clear()
