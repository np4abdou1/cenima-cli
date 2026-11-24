from types import SimpleNamespace
import unittest
from unittest.mock import Mock
from cenima.processor import ServerProcessor


class ProcessorTests(unittest.TestCase):
    def response(self, text='', status=200, headers=None):
        return SimpleNamespace(text=text, status_code=status,
                               url='https://host.test/download/item', headers=headers or {})

    def test_relative_file_links_keep_origin_and_required_headers(self):
        session = Mock()
        session.get.return_value = self.response('<a href="/video/film.mp4">Download</a>')
        processor = ServerProcessor(session=session)
        result = processor.extract_xfilesharing('https://host.test/item')
        self.assertEqual(str(result), 'https://host.test/video/film.mp4')
        self.assertEqual(result.headers['Referer'], 'https://host.test/item')

    def test_relative_redirect_is_resolved_and_non_http_redirect_is_rejected(self):
        for location, expected in [('/video/film.mp4', 'https://host.test/video/film.mp4'),
                                   ('javascript:void(0)', None)]:
            session = Mock()
            session.get.return_value = self.response('<form><input name="op" value="download1"></form>')
            session.post.side_effect = [self.response('<form><input name="op" value="download2"></form>'),
                                        self.response(status=302, headers={'Location': location})]
            result = ServerProcessor(session=session).extract_xfilesharing('https://host.test/item')
            self.assertEqual(str(result) if result else None, expected)
