from cenima.health import LIVE, OFFLINE, SLOW, ServerHealthChecker, clear_cache


def test_dead_host_short_circuits_to_offline():
    clear_cache()
    checker = ServerHealthChecker()
    srv = {"name": "Rapidgator", "embed_url": "https://rapidgator.net/file/abc123"}
    res = checker.probe(srv)
    assert res["health"] == OFFLINE
    assert res["is_live"] is False
    assert res["video_url"] is None


def test_probe_uses_cache_and_sorts_results():
    clear_cache()
    checker = ServerHealthChecker()
    calls = []

    def fake_probe(url):
        calls.append(url)
        if "live" in url:
            return LIVE, f"{url}/stream.mp4"
        if "slow" in url:
            return SLOW, f"{url}/stream.mp4"
        return OFFLINE, None

    checker._probe = fake_probe  # type: ignore[method-assign]

    servers = [
        {"name": "S1", "embed_url": "https://host/offline-1"},
        {"name": "S2", "embed_url": "https://host/slow-1"},
        {"name": "S3", "embed_url": "https://host/live-1"},
        {"name": "S4", "embed_url": "https://host/live-2"},
    ]

    progress_ticks = []
    ordered = checker.probe_all(servers, max_workers=4, on_progress=lambda: progress_ticks.append(1))
    assert len(progress_ticks) == 4
    assert [s["name"] for s in ordered] == ["S3", "S4", "S2", "S1"]
    assert len(calls) == 4

    # Second probe should hit cache (no extra _probe calls)
    checker.probe({"name": "S3", "embed_url": "https://host/live-1"})
    assert len(calls) == 4
