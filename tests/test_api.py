import pytest

import cenima.api as api


def test_validate_external_url_allows_public_topcinema():
    url = "https://topcinema.film/series/dark/"
    assert api.validate_external_url(url, require_topcinema=True, resolve_dns=False) == url


@pytest.mark.parametrize(
    "bad_url,require_tc",
    [
        ("http://127.0.0.1:8000/secret", False),
        ("http://10.0.0.1/internal", False),
        ("http://169.254.169.254/latest/meta-data/", False),
        ("http://localhost/admin", False),
        ("http://service.local/api", False),
        ("ftp://topcinema.film/video.mp4", True),
        ("https://example.com/series/dark/", True),
    ],
)
def test_validate_external_url_blocks_ssrf_and_invalid(bad_url, require_tc):
    with pytest.raises(ValueError):
        api.validate_external_url(bad_url, require_topcinema=require_tc, resolve_dns=False)


def test_transform_search_result():
    res = api.transform_search_result(
        {
            "title": "فيلم Inception 1080p BluRay",
            "url": "https://topcinema.film/movie/inception/",
            "type": "movie",
            "metadata": {"year": 2010, "imdb_rating": 8.8, "quality": "1080p BluRay"},
        }
    )
    assert res.title == "Inception"
    assert res.year == 2010
    assert res.rating == 8.8
    assert res.quality == "1080p BluRay"
