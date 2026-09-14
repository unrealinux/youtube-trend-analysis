"""Tests for YouTube Trend Analysis API"""
import pytest
from unittest.mock import Mock, patch, AsyncMock
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SAMPLE_SEARCH_RESPONSE = {
    "items": [
        {"id": {"videoId": "abc123"}},
        {"id": {"videoId": "def456"}}
    ]
}

SAMPLE_VIDEO_RESPONSE = {
    "items": [
        {
            "id": "abc123",
            "snippet": {
                "title": "Test Video 1",
                "channelTitle": "Test Channel",
                "publishedAt": "2024-01-01T00:00:00Z",
                "description": "Test description",
                "tags": ["python", "tutorial"],
                "thumbnails": {"high": {"url": "https://img.youtube.com/vi/abc123/hqdefault.jpg"}}
            },
            "statistics": {
                "viewCount": "1000000",
                "likeCount": "50000",
                "commentCount": "5000"
            }
        }
    ]
}


class TestModels:
    def test_video_info(self):
        from app.models import VideoInfo
        v = VideoInfo(
            video_id="abc123", title="Test", channel_title="Ch",
            published_at="2024-01-01", view_count=1000, like_count=100,
            comment_count=10, description="", tags=[], thumbnail="", url=""
        )
        assert v.video_id == "abc123"
        assert v.url == ""

    def test_comparison_result_exists(self):
        """Regression test: ComparisonResult was missing, causing /api/compare to crash."""
        from app.models import ComparisonResult
        r = ComparisonResult(
            query1="AI", query2="ML",
            stats1={"total_count": 10, "avg_views": 5000.0, "top_keywords": ["ai"]},
            stats2={"total_count": 8, "avg_views": 3000.0, "top_keywords": ["ml"]},
            comparison={"winner": "query1"}
        )
        assert r.query1 == "AI"
        assert r.comparison["winner"] == "query1"


class TestCache:
    def test_cache_ttl(self):
        from app.config import _CACHE_TTL
        assert _CACHE_TTL == 300


class TestDatabase:
    def test_search_history(self, tmp_path):
        from app.database import init_db, save_search_history, get_recent_searches, clear_search_history
        import app.database as db_module
        original = db_module.DATABASE_PATH
        db_module.DATABASE_PATH = str(tmp_path / "test.db")
        try:
            init_db()
            save_search_history(["python", "tutorial"], 10)
            history = get_recent_searches()
            assert len(history) == 1
            assert history[0].query == "python, tutorial"
            count = clear_search_history()
            assert count >= 0
        finally:
            db_module.DATABASE_PATH = original


class TestFastAPIEndpoints:
    def test_health(self):
        from fastapi.testclient import TestClient
        from app.main import app
        resp = TestClient(app).get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_cache_stats(self):
        from fastapi.testclient import TestClient
        from app.main import app
        resp = TestClient(app).get("/api/cache/stats")
        assert resp.status_code == 200

    def test_cache_clear(self):
        from fastapi.testclient import TestClient
        from app.main import app
        resp = TestClient(app).post("/api/cache/clear")
        assert resp.status_code == 200

    def test_history_endpoints(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import init_db
        init_db()
        client = TestClient(app)
        assert client.get("/api/history").status_code == 200
        assert client.delete("/api/history").status_code == 200

    def test_missing_api_key(self):
        from fastapi.testclient import TestClient
        from app.main import app
        import app.config as config
        original = config.YOUTUBE_API_KEY
        config.YOUTUBE_API_KEY = ""
        try:
            resp = TestClient(app).get("/api/trends/search", params={
                "keywords": "python", "max_results": 10,
                "order": "viewCount", "time_range": "this_month"
            })
            assert resp.status_code in [422, 503]
        finally:
            config.YOUTUBE_API_KEY = original


class TestShortsHelpers:
    def test_is_shorts_duration(self):
        from app.services import _is_shorts_duration
        # YouTube raised the Shorts ceiling from 60s to 180s in Oct 2024.
        assert _is_shorts_duration("PT30S") is True
        assert _is_shorts_duration("PT59S") is True
        assert _is_shorts_duration("PT1M") is True
        assert _is_shorts_duration("PT1M30S") is True
        assert _is_shorts_duration("PT2M59S") is True
        assert _is_shorts_duration("PT3M") is True
        assert _is_shorts_duration("PT3M1S") is False
        assert _is_shorts_duration("PT10M") is False
        # malformed / missing durations must not be counted as Shorts
        assert _is_shorts_duration("") is False
        assert _is_shorts_duration("PT") is False
        assert _is_shorts_duration("P1D") is False

    def test_shorts_search_endpoint(self):
        from unittest.mock import AsyncMock
        from fastapi.testclient import TestClient
        from app.main import app
        import app.config as config
        original_key = config.YOUTUBE_API_KEY
        config.YOUTUBE_API_KEY = "test_key"
        try:
            mock_search = AsyncMock(return_value={"items": [{"id": {"videoId": "short1"}}]})
            mock_stats = AsyncMock(return_value={
                "items": [{
                    "id": "short1",
                    "snippet": {
                        "title": "Python Short",
                        "channelTitle": "Test",
                        "publishedAt": "2024-01-01T00:00:00Z",
                        "description": "Test",
                        "tags": ["python", "shorts"],
                        "thumbnails": {"high": {"url": "https://img.youtube.com/vi/short1/hqdefault.jpg"}}
                    },
                    "statistics": {"viewCount": "100000", "likeCount": "5000", "commentCount": "500"},
                    "contentDetails": {"duration": "PT45S"}
                }]
            })

            with patch('app.services._fetch_search', mock_search), \
                 patch('app.services._fetch_video_with_duration', mock_stats):
                resp = TestClient(app).get("/api/trends/shorts/search", params={
                    "keywords": "python", "max_results": 10
                })
                assert resp.status_code == 200
                data = resp.json()
                assert data["total_count"] == 1
                assert data["videos"][0]["video_id"] == "short1"
                assert data["avg_views"] == 100000.0
                assert "python" in data["top_keywords"]
        finally:
            config.YOUTUBE_API_KEY = original_key


class TestBatchScanEndpoint:
    def test_batch_scan_missing_api_key(self):
        from fastapi.testclient import TestClient
        from app.main import app
        import app.config as config
        original = config.YOUTUBE_API_KEY
        config.YOUTUBE_API_KEY = ""
        try:
            resp = TestClient(app).get("/api/trends/batch-scan", params={
                "keywords": "AI,ML", "time_range": "this_month"
            })
            assert resp.status_code in [500, 503]
        finally:
            config.YOUTUBE_API_KEY = original


class TestHotCategoriesEndpoint:
    def test_hot_categories_missing_api_key(self):
        from fastapi.testclient import TestClient
        from app.main import app
        import app.config as config
        original = config.YOUTUBE_API_KEY
        config.YOUTUBE_API_KEY = ""
        try:
            resp = TestClient(app).get("/api/trends/hot-categories")
            assert resp.status_code in [500, 503]
        finally:
            config.YOUTUBE_API_KEY = original


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
