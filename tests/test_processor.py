from cenima.processor import ServerProcessor, StreamURL


def test_stream_url_preserves_metadata():
    s = StreamURL("https://cdn.example/video.mp4", referer="https://ref.example/", headers={"User-Agent": "UA"})
    assert str(s) == "https://cdn.example/video.mp4"
    assert s.startswith("https://")
    assert s.referer == "https://ref.example/"
    assert s.headers["User-Agent"] == "UA"


def test_extract_file_code():
    proc = ServerProcessor()
    assert proc._extract_file_code("https://vidtube.one/embed-abc12345def.html") == "abc12345def"
    assert proc._extract_file_code("https://down.vidtube.one/d/xyz987654321_h") == "xyz987654321"


def test_unpack_and_handle_vidtube_one():
    proc = ServerProcessor()
    html = (
        "<script>eval(function(p,a,c,k,e,d){return p}"
        "('0:\"1://2.3/4.5\"',6,6,'file|https|cdn-video|xyz|movie|mp4'.split('|')))</script>"
    )
    res = proc._handle_vidtube_one(html, referer="https://vidtube.one/embed-test1234.html")
    assert isinstance(res, StreamURL)
    assert str(res) == "https://cdn-video.xyz/movie.mp4"
    assert res.referer == "https://vidtube.one/"


def test_handle_vidtube_pro_prefers_highest_quality():
    class FakeResp:
        def __init__(self, text, status_code=200):
            self.text = text
            self.status_code = status_code

    class FakeSession:
        def __init__(self):
            self.requested = []

        def get(self, url, **kwargs):
            self.requested.append(url)
            return FakeResp('<a class="btn btn-gradient submit-btn" href="https://cdn.example/1080.mp4">DL</a>')

    session = FakeSession()
    proc = ServerProcessor(session=session)
    html = '<a href="/d/abc12345_h">720p</a><a href="/d/abc12345_x">1080p</a>'
    res = proc._handle_vidtube_pro("https://down.vidtube.one/d/abc12345.html", html, proc.headers)
    assert isinstance(res, StreamURL)
    assert str(res) == "https://cdn.example/1080.mp4"
    assert session.requested == ["https://down.vidtube.one/d/abc12345_x"]
