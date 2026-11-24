from bs4 import BeautifulSoup

from cenima.scraper import (
    TopCinemaScraper,
    clean_arabic_title,
    clean_show_title,
    extract_season_number,
    extract_season_part,
    parse_episode_number,
)


def _make_offline_scraper() -> TopCinemaScraper:
    return TopCinemaScraper(base_url="https://topcinema.film", auto_discover=False)


def test_title_cleaning():
    assert clean_arabic_title("مسلسل Breaking Bad 2008 2008") == "Breaking Bad 2008"
    assert clean_show_title("مسلسل Breaking Bad الموسم الرابع الحلقة 13 1080p WEB-DL") == "Breaking Bad"
    assert clean_show_title("TopCinema") == ""


def test_parse_episode_number():
    assert parse_episode_number("12") == 12.0
    assert parse_episode_number("12.5") == 12.5
    assert parse_episode_number("Special") == 0.0
    assert parse_episode_number("0") == 0.0
    assert parse_episode_number("") == 99999.0


def test_season_extraction():
    assert extract_season_number("https://topcinema.film/series/show-الموسم-الرابع/") == 4
    assert extract_season_number("https://topcinema.film/series/show-الموسم-الثاني-عشر/") == 12
    assert extract_season_number("https://topcinema.film/series/show-season-3/") == 3
    assert extract_season_number("https://topcinema.film/anime/aot-final-part-2/") == 102
    assert extract_season_part("https://topcinema.film/anime/aot-part-2/") == "Part 2"
    assert extract_season_part("https://topcinema.film/anime/aot-الجزء-الثاني/") == "Part 2"


def test_deduplicate_prefers_canonical_series_url():
    scraper = _make_offline_scraper()
    results = [
        {
            "title": "مسلسل Dark الحلقة 1",
            "url": "https://topcinema.film/series/dark-الحلقة-1/",
            "type": "series",
            "metadata": {"rating": 8.7},
        },
        {
            "title": "مسلسل Dark",
            "url": "https://topcinema.film/series/dark/",
            "type": "series",
            "metadata": {"year": 2017},
        },
    ]
    deduped = scraper._deduplicate_search_results(results)
    assert len(deduped) == 1
    assert deduped[0]["url"] == "https://topcinema.film/series/dark/"
    assert deduped[0]["metadata"]["rating"] == 8.7
    assert deduped[0]["metadata"]["year"] == 2017


def test_parse_episode_link_resolves_relative_url():
    scraper = _make_offline_scraper()
    soup = BeautifulSoup('<a href="/series/dark-episode-5/" title="Episode 5">الحلقة 5</a>', "html.parser")
    ep = scraper._parse_episode_link(soup.find("a"), "/series/dark-episode-5/")
    assert ep is not None
    assert ep["url"] == "https://topcinema.film/series/dark-episode-5/"
    assert ep["episode_number"] == "5"


def test_get_series_details_matches_series_season_links():
    scraper = _make_offline_scraper()
    html = """
    <html><body>
      <h1 class="post-title">مسلسل Test Show</h1>
      <a href="https://topcinema.film/series/test-show-season-1/">Season 1</a>
      <a href="https://topcinema.film/series/test-show-season-2/">Season 2</a>
    </body></html>
    """

    class FakeResp:
        status_code = 200
        url = "https://topcinema.film/series/test-show/"
        text = html

        def raise_for_status(self):
            return None

    scraper.session.get = lambda *args, **kwargs: FakeResp()
    details = scraper.get_series_details("https://topcinema.film/series/test-show/")
    assert details is not None
    assert len(details["seasons"]) == 2
    assert [s["season_number"] for s in details["seasons"]] == [1, 2]
