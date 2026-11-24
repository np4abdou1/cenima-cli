from cenima.history import HistoryManager
from cenima.models import WatchStatus


def test_history_progress_and_persistence(tmp_path):
    hist_file = tmp_path / "history.json"
    hm = HistoryManager(history_file=hist_file)

    p1 = hm.update_progress("Breaking Bad", season_number=1, episode_number="1", time_pos=10, duration=3000, content_type="series")
    assert p1.status == WatchStatus.UNWATCHED

    p2 = hm.update_progress("Breaking Bad", season_number=1, episode_number="2", time_pos=1200, duration=3000, content_type="series")
    assert p2.status == WatchStatus.IN_PROGRESS
    assert p2.progress_pct == 40.0

    p3 = hm.update_progress("Breaking Bad", season_number=1, episode_number="3", time_pos=2600, duration=3000, content_type="series")
    assert p3.status == WatchStatus.COMPLETED

    p4 = hm.update_progress("Inception", time_pos=8950, duration=9000, content_type="movie")
    assert p4.status == WatchStatus.COMPLETED

    # Verify reload from disk (atomic write persisted cleanly)
    hm2 = HistoryManager(history_file=hist_file)
    loaded = hm2.get_progress("  breaking   bad ", season_number=1, episode_number="2")
    assert loaded is not None
    assert loaded.time_pos == 1200.0
    assert loaded.status == WatchStatus.IN_PROGRESS

    watched_count, watched_eps = hm2.get_season_stats("Breaking Bad", 1)
    assert watched_count == 1
    assert watched_eps == ["3"]

    recent = hm2.get_recent_history(limit=2)
    assert len(recent) == 2
    assert recent[0].show_title == "Inception"

    hm2.clear_history()
    assert hm2.get_recent_history() == []


def test_history_recovers_from_corrupt_json(tmp_path):
    hist_file = tmp_path / "history.json"
    hist_file.write_text("{not valid json", encoding="utf-8")
    hm = HistoryManager(history_file=hist_file)
    assert hm.get_recent_history() == []
