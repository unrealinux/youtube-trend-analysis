"""Regression checks for the analysis math and refresh/quota behaviour.

Unlike tests/test_main.py, these patch only `app.services.fetch_json` — the
lowest HTTP boundary — so the real `_fetch_search` / `_fetch_videos` wrappers,
their argument handling and the cache all execute. Patching those wrappers
directly (as the older tests do) hides signature and response-shape mistakes.
"""
import asyncio
import os
import sys

import pytest
import httpx

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app.config as config
import app.services as services


def short(vid, views, duration="PT30S", published="2024-06-01T12:00:00Z", tags=("python",), title="t"):
    return {
        "id": vid,
        "snippet": {
            "title": title, "channelTitle": "TestChannel", "publishedAt": published,
            "description": "d", "tags": list(tags), "thumbnails": {"high": {"url": "u"}},
        },
        "statistics": {"viewCount": str(views), "likeCount": "10", "commentCount": "2"},
        "contentDetails": {"duration": duration},
    }


class FakeHTTP:
    """Stands in for `fetch_json`, recording which YouTube endpoints were hit."""

    def __init__(self, videos):
        self.videos = videos
        self.calls = []

    async def __call__(self, client, url, params):
        self.calls.append((url, params))
        if url.endswith("/search"):
            return {"items": [{"id": {"videoId": v["id"]}} for v in self.videos]}
        return {"items": self.videos}

    @property
    def search_calls(self):
        return [c for c in self.calls if c[0].endswith("/search")]


@pytest.fixture(autouse=True)
def clean_state():
    config._cache.clear()
    config.YOUTUBE_API_KEY = "test_key"
    yield
    config._cache.clear()


@pytest.fixture
def db(tmp_path, monkeypatch):
    import app.database as database
    monkeypatch.setattr(database, "DATABASE_PATH", str(tmp_path / "t.db"))
    database.init_db()
    return database


def run(coro):
    return asyncio.run(coro)


# ---- Shorts duration gate (was: < 60s, dropping every 1-3 min Short) ----

@pytest.mark.parametrize("duration,expected", [
    ("PT30S", True), ("PT1M", True), ("PT2M59S", True), ("PT3M", True),
    ("PT3M1S", False), ("PT10M", False), ("", False), ("PT", False),
])
def test_shorts_gate_covers_3_minute_ceiling(duration, expected):
    assert services._is_shorts_duration(duration) is expected


def test_three_minute_shorts_are_collected(monkeypatch):
    """A 2m30s Short must survive the filter end to end."""
    fake = FakeHTTP([short("a", 1000, "PT2M30S"), short("b", 500, "PT10M")])
    monkeypatch.setattr(services, "fetch_json", fake)
    videos, tags, durations = run(services._collect_shorts("k", "q #shorts", 10, 30))
    assert [v.video_id for v in videos] == ["a"]
    assert durations == [150]
    assert "python" in tags


def test_detailed_duration_buckets_and_percentiles(monkeypatch):
    """Durations must line up with their own bucket; p25/p75 use nearest rank."""
    vids = [short(f"v{i}", i, "PT10S" if i <= 5 else "PT40S") for i in range(1, 21)]
    monkeypatch.setattr(services, "fetch_json", FakeHTTP(vids))
    res = run(services.detailed_shorts_service("kw", 20, "this_month"))

    assert res.total_videos == 20
    assert res.median_views == 11
    # nearest-rank: index int(20*0.25)-1 = 4 -> view 5 (the old int(20*0.25) gave 6)
    assert res.p25_views == 5
    assert res.p75_views == 15
    buckets = {b.label: b.count for b in res.duration_buckets}
    assert buckets["0-15s"] == 5 and buckets["30-45s"] == 15
    assert sum(b.count for b in res.duration_buckets) == res.total_videos


# ---- Monthly upload frequency (was: 1-day span -> 600/month) ----

def test_same_day_batch_does_not_explode_frequency(monkeypatch):
    vids = [short(f"v{i}", 100, published="2024-06-01T12:00:00Z") for i in range(20)]
    monkeypatch.setattr(services, "fetch_json", FakeHTTP(vids))
    result = run(services.fetch_trending_videos("k", {"q": "x", "maxResults": 20}))
    # 20 uploads / floored 7-day span * 30 = 85.71, not 600
    assert result.upload_frequency == pytest.approx(85.71, abs=0.01)


def test_single_video_still_reports_a_rate(monkeypatch):
    monkeypatch.setattr(services, "fetch_json", FakeHTTP([short("v1", 100)]))
    result = run(services.fetch_trending_videos("k", {"q": "x", "maxResults": 20}))
    assert result.upload_frequency == pytest.approx(1 / 30, abs=0.01)


# ---- Refresh semantics (was: inverted, and never refreshed a populated table) ----

def test_refresh_true_appends_snapshot_even_when_history_exists(monkeypatch, db):
    monkeypatch.setattr(services, "fetch_json", FakeHTTP([short("a", 1000)]))
    db.save_trend_snapshot("AI", 10.0, 1, 1.0)  # pre-existing history

    run(services.trend_tracking_service("AI", 30, refresh=True))
    assert len(db.get_trend_snapshots("AI", 30)) == 2


def test_no_refresh_leaves_history_untouched(monkeypatch, db):
    monkeypatch.setattr(services, "fetch_json", FakeHTTP([short("a", 1000)]))
    db.save_trend_snapshot("AI", 10.0, 1, 1.0)

    run(services.trend_tracking_service("AI", 30, refresh=False))
    assert len(db.get_trend_snapshots("AI", 30)) == 1


def test_trend_direction_uses_latest_two_snapshots(db):
    db.save_trend_snapshot("AI", 100.0, 1, 1.0)
    db.save_trend_snapshot("AI", 500.0, 1, 1.0)
    res = run(services.trend_tracking_service("AI", 30, refresh=False))
    assert res.trend_direction == "rising"
    assert res.latest_change.change_pct == pytest.approx(400.0)


def test_same_second_snapshots_are_ordered_by_insertion(db):
    """Regression: recorded_at has 1s granularity, so `ORDER BY recorded_at`
    alone left the newest/previous pair unspecified and a 5x rise could be
    reported as falling."""
    import sqlite3
    conn = db.get_db()
    ts = "2026-09-11 16:38:08"
    for views in (1000.0, 5000.0):          # older first, same timestamp
        conn.execute(
            "INSERT INTO trend_tracking (keyword, avg_views, total_videos, "
            "engagement_rate, recorded_at) VALUES (?,?,?,?,?)",
            ("AI", views, 1, 1.0, ts),
        )
    conn.commit()
    conn.close()

    snaps = db.get_trend_snapshots("AI", 30)
    assert [s.avg_views for s in snaps] == [5000.0, 1000.0], "newest must be first"

    res = run(services.trend_tracking_service("AI", 30, refresh=False))
    assert res.trend_direction == "rising", res.trend_direction
    assert res.latest_change.change_pct == pytest.approx(400.0)


# ---- Quota accounting (was: hardcoded used=1 in the router) ----

def test_quota_accumulates_per_day_row(db):
    assert db.get_today_quota_used() == 0
    db.record_quota_usage(100)
    db.record_quota_usage(1)
    db.record_quota_usage(100)
    assert db.get_today_quota_used() == 201
    rows = db.get_db().execute("SELECT COUNT(*) FROM api_quota").fetchone()[0]
    assert rows == 1, "should upsert one row per day, not append per call"


def test_quota_counts_search_and_video_calls(monkeypatch, db):
    fake = FakeHTTP([short("a", 100)])
    monkeypatch.setattr(services, "fetch_json", fake)
    run(services.search_shorts_service(["ai"], 5, "viewCount", "this_month"))

    assert len(fake.search_calls) == 1
    # 1 search (100 units) + 1 videos.list (1 unit)
    assert db.get_today_quota_used() == services.QUOTA_COST_SEARCH + services.QUOTA_COST_VIDEOS


def test_quota_not_charged_on_cache_hit(monkeypatch, db):
    fake = FakeHTTP([short("a", 100)])
    monkeypatch.setattr(services, "fetch_json", fake)
    run(services.search_shorts_service(["ai"], 5, "viewCount", "this_month"))
    calls_after_first = len(fake.calls)
    after_first = db.get_today_quota_used()

    run(services.search_shorts_service(["ai"], 5, "viewCount", "this_month"))
    assert len(fake.calls) == calls_after_first, "second call must be served from cache"
    assert db.get_today_quota_used() == after_first, "cached call must not spend quota"


# ---- Channel insights title (was: unreadable inline conditional, always "") ----

def test_channel_insights_reports_channel_title(monkeypatch):
    monkeypatch.setattr(services, "fetch_json", FakeHTTP([short("a", 1000)]))
    res = run(services.channel_insights_service("UCabc", 10, "past_year"))
    assert res.channel_title == "TestChannel"
    assert res.total_shorts == 1


# ---- Cache is bounded (was: grew for the process lifetime) ----

def test_cache_evicts_oldest_past_cap(monkeypatch):
    monkeypatch.setattr(config, "_CACHE_MAX_ENTRIES", 5)
    config._cache.clear()
    for i in range(20):
        services._set_cached(f"k{i}", {"i": i})
    assert len(config._cache) == 5
    assert "k19" in config._cache and "k0" not in config._cache


def test_cache_reclaims_expired_before_evicting_live(monkeypatch):
    monkeypatch.setattr(config, "_CACHE_MAX_ENTRIES", 3)
    config._cache.clear()
    services._set_cached("stale", {"x": 1})
    ts, data = config._cache["stale"]
    config._cache["stale"] = (ts - config._CACHE_TTL - 1, data)
    for i in range(3):
        services._set_cached(f"live{i}", {"i": i})
    assert "stale" not in config._cache
    assert len(config._cache) == 3


def test_published_after_is_stable_across_seconds():
    """Regression: a live timestamp here changed the cache key every second,
    so repeat searches never hit the cache and burned 100 quota units each."""
    import time
    first = services._published_after(30)
    time.sleep(1.1)
    assert services._published_after(30) == first
    assert first.endswith("T00:00:00Z")


# ---- Blocking DB writes are awaited (not fire-and-forget) ----

def test_async_writes_are_durable_when_awaited(monkeypatch, db):
    monkeypatch.setattr(services, "fetch_json", FakeHTTP([short("a", 1000)]))
    run(services.trend_tracking_service("AI", 30, refresh=True))
    # a plain blocking read immediately after the await must see the row
    assert len(db.get_trend_snapshots("AI", 30)) == 1


def test_async_search_writes_history_and_quota(monkeypatch, db):
    monkeypatch.setattr(services, "fetch_json", FakeHTTP([short("a", 1000)]))
    run(services.search_shorts_service(["ai"], 5, "viewCount", "this_month"))
    assert len(db.get_recent_searches(10)) == 1
    # 1 search (100) + 1 videos.list (1)
    assert db.get_today_quota_used() == services.QUOTA_COST_SEARCH + services.QUOTA_COST_VIDEOS


def test_sync_write_helpers_remain_usable(db):
    """The sync implementations back the async wrappers; keep both working."""
    db.save_search_history(["x"], 3)
    db.save_trend_snapshot("k", 1.0, 1, 0.5)
    db.record_quota_usage(7)
    assert len(db.get_recent_searches(5)) == 1
    assert len(db.get_trend_snapshots("k", 5)) == 1
    assert db.get_today_quota_used() == 7


# ---- Config: CORS defaults cover documented ports; DB path dir is created ----

def test_cors_defaults_cover_documented_ports():
    from app.config import CORS_ORIGINS
    assert "http://localhost:8000" in CORS_ORIGINS   # README's access port
    assert "http://localhost:8001" in CORS_ORIGINS   # uvicorn --reload port


def test_get_db_creates_missing_parent_directory(tmp_path, monkeypatch):
    import app.database as database
    target = tmp_path / "nested" / "deep" / "x.db"
    monkeypatch.setattr(database, "DATABASE_PATH", str(target))
    database.init_db()
    assert target.exists()


# ---- time_range is validated, not silently defaulted ----

@pytest.mark.parametrize("time_range,days", [
    ("today", 1), ("this_week", 7), ("this_month", 30), ("past_year", 365),
])
def test_resolve_days_accepts_documented_ranges(time_range, days):
    assert services._resolve_days(time_range) == days


@pytest.mark.parametrize("bad", ["past_month", "365", "", "THIS_MONTH", "last_month"])
def test_resolve_days_rejects_unknown_range(bad):
    """Regression: unknown ranges silently became 365 days."""
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        services._resolve_days(bad)
    assert exc.value.status_code == 400
    assert bad in exc.value.detail or bad == ""  # message names the offender


def test_channel_search_rejects_bad_time_range_end_to_end(monkeypatch):
    """Must 400 on the parameter itself, even with a key configured."""
    monkeypatch.setattr(services, "fetch_json", FakeHTTP([short("a", 100)]))
    from fastapi.testclient import TestClient
    from app.main import app
    resp = TestClient(app).get("/api/trends/channel", params={
        "channel_id": "UCabc", "time_range": "past_month",
    })
    assert resp.status_code == 400, f"got {resp.status_code}: {resp.text[:120]}"
    assert "past_month" in resp.json()["detail"]


@pytest.mark.parametrize("path,params", [
    ("/api/trends/search", {"keywords": "AI"}),
    ("/api/trends/shorts/search", {"keywords": "AI"}),
    ("/api/trends/channel", {"channel_id": "UCabc"}),
    ("/api/trends/batch-scan", {"keywords": "AI"}),
    ("/api/trends/hot-categories", {}),
    ("/api/trends/feature-analysis", {"keyword": "AI"}),
    ("/api/shorts/detailed", {"keyword": "AI"}),
    ("/api/shorts/channel-insights", {"channel_id": "UCabc"}),
])
def test_every_time_range_endpoint_rejects_bad_range(monkeypatch, path, params):
    """Parameter validation must not depend on whether a key is configured."""
    original = config.YOUTUBE_API_KEY
    config.YOUTUBE_API_KEY = ""          # validation should still win over 503
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        resp = TestClient(app).get(path, params={**params, "time_range": "past_month"})
        assert resp.status_code == 400, f"{path} -> {resp.status_code}: {resp.text[:120]}"
    finally:
        config.YOUTUBE_API_KEY = original


def test_missing_key_still_503_for_valid_range(monkeypatch):
    """The key check must still fire once the parameters are valid."""
    original = config.YOUTUBE_API_KEY
    config.YOUTUBE_API_KEY = ""
    try:
        from fastapi.testclient import TestClient
        from app.main import app
        resp = TestClient(app).get("/api/trends/search", params={
            "keywords": "AI", "time_range": "this_month",
        })
        assert resp.status_code == 503
    finally:
        config.YOUTUBE_API_KEY = original


def test_channel_search_rejects_bad_channel_id():
    from fastapi.testclient import TestClient
    from app.main import app
    resp = TestClient(app).get("/api/trends/channel", params={"channel_id": "not-a-channel"})
    assert resp.status_code == 400


# ---- channel_trends_service (extracted out of the router) ----

def test_channel_trends_service_returns_trend_data(monkeypatch):
    monkeypatch.setattr(services, "fetch_json", FakeHTTP([short("a", 1000), short("b", 500)]))
    result = run(services.channel_trends_service("UCabc", 10, "date", "this_month"))
    assert result.total_count == 2
    assert result.avg_views == 750.0
    assert result.top_keywords


def test_channel_trends_service_requires_api_key():
    from fastapi import HTTPException
    original = config.YOUTUBE_API_KEY
    config.YOUTUBE_API_KEY = ""
    try:
        with pytest.raises(HTTPException) as exc:
            run(services.channel_trends_service("UCabc", 10, "date", "this_month"))
        assert exc.value.status_code == 503
    finally:
        config.YOUTUBE_API_KEY = original


# ---- Every route with a declared response_model must serialize its real payload ----

@pytest.mark.parametrize("method,path,params,body,expect_key", [
    ("get", "/api/trends/search", {"keywords": "ai", "time_range": "this_month"}, None, "videos"),
    ("get", "/api/trends/shorts/search", {"keywords": "ai", "time_range": "this_month"}, None, "videos"),
    ("get", "/api/trends/channel", {"channel_id": "UCabc", "time_range": "this_month"}, None, "videos"),
    ("post", "/api/compare", None, {"query1": "a", "query2": "b"}, "comparison"),
    ("get", "/api/trends/batch-scan", {"keywords": "ai", "time_range": "this_month"}, None, "keywords"),
    ("get", "/api/trends/hot-categories", {"time_range": "this_month"}, None, None),
    ("get", "/api/trends/feature-analysis", {"keyword": "ai", "time_range": "this_month"}, None, "success_score"),
    ("get", "/api/shorts/detailed", {"keyword": "ai", "time_range": "this_month"}, None, "duration_buckets"),
    ("get", "/api/shorts/channel-insights", {"channel_id": "UCabc", "time_range": "this_month"}, None, "total_shorts"),
    ("get", "/api/trends/trend-tracking", {"keyword": "ai"}, None, "trend_direction"),
    ("get", "/api/trends/channels/search", {"q": "test"}, None, "channels"),
])
def test_response_model_matches_real_payload(monkeypatch, method, path, params, body, expect_key):
    """A wrong response_model would make FastAPI reject the payload at runtime
    even though the service returns valid data, so exercise every route."""
    monkeypatch.setattr(services, "fetch_json", FakeHTTP([short("a", 1000), short("b", 500)]))
    config._cache.clear()
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    resp = getattr(client, method)(path, params=params, json=body) if body \
        else getattr(client, method)(path, params=params)
    assert resp.status_code == 200, f"{path} -> {resp.status_code}: {resp.text[:200]}"
    payload = resp.json()
    if expect_key:
        assert expect_key in payload, f"{path} missing {expect_key}: {list(payload)[:8]}"


def test_openapi_declares_real_schemas():
    """Regression: every route used to declare response_model=dict, so /docs
    showed untyped objects."""
    from fastapi.testclient import TestClient
    from app.main import app
    spec = TestClient(app).get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    for name in ["TrendData", "ComparisonResult", "HotCategoryResult",
                 "TrendTrackingResult", "SearchHistoryEntry"]:
        assert name in schemas, f"{name} not in OpenAPI components"
    ref = spec["paths"]["/api/trends/search"]["get"]["responses"]["200"] \
        ["content"]["application/json"]["schema"]
    assert ref.get("$ref", "").endswith("/TrendData"), ref


# ---- Channel search enrichment (was: subscriber_count/video_count stuck at 0) ----

class ChannelHTTP:
    """Fake that models channel search + channels.list."""

    def __init__(self):
        self.calls = []

    async def __call__(self, client, url, params):
        self.calls.append((url, params))
        if url.endswith("/search"):
            return {"items": [
                {"id": {"channelId": "UC1"},
                 "snippet": {"title": "One", "description": "d",
                             "thumbnails": {"high": {"url": "u"}}}},
                {"id": {"channelId": "UC2"},
                 "snippet": {"title": "Two", "description": "d",
                             "thumbnails": {}}},
            ]}
        return {"items": [
            {"id": "UC1", "statistics": {"subscriberCount": "1234", "videoCount": "56"}},
            {"id": "UC2", "statistics": {}},  # hidden counts
        ]}


def test_channel_search_reports_subscriber_and_video_counts(monkeypatch):
    fake = ChannelHTTP()
    monkeypatch.setattr(services, "fetch_json", fake)
    res = run(services.search_channels_service("test", 5))

    assert [c.channel_id for c in res.channels] == ["UC1", "UC2"]
    assert res.channels[0].subscriber_count == 1234
    assert res.channels[0].video_count == 56
    assert res.channels[1].subscriber_count == 0, "hidden stats must not crash"
    assert any(url.endswith("/channels") for url, _ in fake.calls)


# ---- compare honours time_range instead of hardcoding 365 days ----

def test_compare_time_range_controls_published_after(monkeypatch, db):
    fake = FakeHTTP([short("a", 100)])
    monkeypatch.setattr(services, "fetch_json", fake)
    run(services.compare_searches_service("AI", "ML", "this_month"))

    expected = services._published_after(30)
    assert [c[1]["publishedAfter"] for c in fake.search_calls] == [expected, expected]


def test_compare_rejects_unknown_time_range(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(services, "fetch_json", FakeHTTP([short("a", 100)]))
    with pytest.raises(HTTPException) as exc:
        run(services.compare_searches_service("AI", "ML", "past_month"))
    assert exc.value.status_code == 400


# ---- Quota day is Pacific, not UTC (was: CURRENT_DATE -> UTC bucket) ----

def test_quota_day_follows_configured_timezone(monkeypatch):
    import app.database as database
    from zoneinfo import ZoneInfo
    from datetime import datetime
    for zone in ("America/Los_Angeles", "Pacific/Kiritimati", "Pacific/Niue"):
        monkeypatch.setattr(database, "QUOTA_TIMEZONE", zone)
        assert database._quota_day() == datetime.now(ZoneInfo(zone)).date().isoformat()
    # an unknown zone falls back to UTC instead of raising
    monkeypatch.setattr(database, "QUOTA_TIMEZONE", "Not/AZone")
    from datetime import timezone
    assert database._quota_day() == datetime.now(timezone.utc).date().isoformat()


def test_quota_rows_are_keyed_by_quota_day(db):
    db.record_quota_usage(100)
    row = db.get_db().execute("SELECT date FROM api_quota").fetchone()
    assert row["date"] == db._quota_day()


# ---- Transient HTTP failures are retried; 4xx are not ----

class _Resp:
    def __init__(self, payload=None, status=200):
        self._payload = payload
        self.status_code = status
        self.text = str(payload)

    def raise_for_status(self):
        if self.status_code >= 400:
            req = httpx.Request("GET", "http://x")
            raise httpx.HTTPStatusError(
                "boom", request=req, response=httpx.Response(self.status_code, request=req)
            )

    def json(self):
        return self._payload


class FlakyClient:
    def __init__(self, failures, status=200):
        self.failures = failures
        self.status = status
        self.calls = 0

    async def get(self, *args, **kwargs):
        self.calls += 1
        if self.calls <= self.failures:
            if self.status >= 400:
                return _Resp(status=self.status)
            raise httpx.ConnectTimeout("boom")
        return _Resp({"ok": True})


def test_fetch_json_retries_connection_errors(monkeypatch):
    monkeypatch.setattr(services, "_RETRY_BASE_DELAY", 0)
    client = FlakyClient(failures=2)
    assert run(services.fetch_json(client, "http://x", {})) == {"ok": True}
    assert client.calls == 3


def test_fetch_json_gives_up_as_504_after_max_attempts(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(services, "_RETRY_BASE_DELAY", 0)
    client = FlakyClient(failures=99)
    with pytest.raises(HTTPException) as exc:
        run(services.fetch_json(client, "http://x", {}))
    assert exc.value.status_code == 504
    assert client.calls == services._RETRY_ATTEMPTS


def test_fetch_json_retries_5xx(monkeypatch):
    monkeypatch.setattr(services, "_RETRY_BASE_DELAY", 0)
    client = FlakyClient(failures=1, status=503)
    assert run(services.fetch_json(client, "http://x", {})) == {"ok": True}
    assert client.calls == 2


def test_fetch_json_does_not_retry_403(monkeypatch):
    """403 is usually an exhausted quota; retrying just burns time."""
    from fastapi import HTTPException
    monkeypatch.setattr(services, "_RETRY_BASE_DELAY", 0)
    client = FlakyClient(failures=99, status=403)
    with pytest.raises(HTTPException) as exc:
        run(services.fetch_json(client, "http://x", {}))
    assert exc.value.status_code == 403
    assert client.calls == 1


# ---- Fan-out scans are concurrency-capped ----

def test_hot_categories_caps_concurrent_scans(monkeypatch, db):
    state = {"cur": 0, "max": 0}

    async def fake_collect(*args, **kwargs):
        state["cur"] += 1
        state["max"] = max(state["max"], state["cur"])
        await asyncio.sleep(0.01)
        state["cur"] -= 1
        return [short("a", 100)], ["t"], [30]

    monkeypatch.setattr(services, "_collect_shorts", fake_collect)
    run(services.hot_categories_service("this_month", 5))

    assert state["max"] <= config.MAX_CONCURRENT_SCANS
    assert state["max"] > 1, "categories should still run concurrently"


# ---- Auto trend snapshots ----

def _insert_stale_keyword(db, keyword):
    conn = db.get_db()
    conn.execute(
        "INSERT INTO trend_tracking (keyword, avg_views, total_videos, "
        "engagement_rate, recorded_at) VALUES (?,?,?,?,?)",
        (keyword, 1.0, 1, 1.0, "2020-01-01 00:00:00"),
    )
    conn.commit()
    conn.close()


def test_auto_snapshot_only_refreshes_stale_keywords(monkeypatch, db):
    monkeypatch.setattr(services, "fetch_json", FakeHTTP([short("a", 1000)]))
    db.save_trend_snapshot("fresh", 10.0, 1, 1.0)     # just now
    _insert_stale_keyword(db, "stale")

    assert run(services.snapshot_due_keywords()) == 1
    assert len(db.get_trend_snapshots("stale", 10)) == 2
    assert len(db.get_trend_snapshots("fresh", 10)) == 1


def test_auto_snapshot_disabled_when_interval_is_zero(monkeypatch, db):
    monkeypatch.setattr(config, "TREND_SNAPSHOT_INTERVAL_HOURS", 0)
    _insert_stale_keyword(db, "stale")
    assert run(services.snapshot_due_keywords()) == 0
    assert len(db.get_trend_snapshots("stale", 10)) == 1


def test_auto_snapshot_skips_when_quota_above_threshold(monkeypatch, db):
    monkeypatch.setattr(services, "fetch_json", FakeHTTP([short("a", 1000)]))
    db.record_quota_usage(int(db.DAILY_QUOTA_LIMIT * config.QUOTA_WARNING_THRESHOLD) + 1)
    _insert_stale_keyword(db, "stale")

    assert run(services.snapshot_due_keywords()) == 0
    assert len(db.get_trend_snapshots("stale", 10)) == 1


def test_auto_snapshot_loop_is_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(config, "TREND_SNAPSHOT_INTERVAL_HOURS", 0)
    # returns immediately instead of sleeping forever
    run(asyncio.wait_for(services.trend_snapshot_loop(), timeout=1))
