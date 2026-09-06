"""Tests for YouTube Trend Analysis API"""
import pytest
from unittest.mock import Mock, patch, AsyncMock
import json
import os
import sys

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Test data
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

@pytest.fixture
def sample_video_info():
    """Create sample VideoInfo for testing."""
    from main import VideoInfo
    return VideoInfo(
        video_id="abc123",
        title="Test Video",
        channel_title="Test Channel",
        published_at="2024-01-01T00:00:00Z",
        view_count=1000000,
        like_count=50000,
        comment_count=5000,
        description="Test",
        tags=["python", "tutorial"],
        thumbnail="https://img.youtube.com/vi/abc123/hqdefault.jpg",
        url="https://www.youtube.com/watch?v=abc123"
    )

class TestModels:
    """Test data models."""
    
    def test_create_video_info(self, sample_video_info):
        assert sample_video_info.video_id == "abc123"
        assert sample_video_info.view_count == 1000000
        assert sample_video_info.like_count == 50000
    
    def test_video_url_generation(self):
        from main import VideoInfo
        video = VideoInfo(
            video_id="test123",
            title="Test",
            channel_title="Test",
            published_at="2024-01-01",
            view_count=0,
            like_count=0,
            comment_count=0,
            description="",
            tags=[],
            thumbnail="",
            url="https://www.youtube.com/watch?v=test123"
        )
        assert video.url == "https://www.youtube.com/watch?v=test123"
    
    def test_trend_data_creation(self):
        from main import TrendData
        data = TrendData(
            videos=[],
            total_count=0,
            avg_views=0,
            top_keywords=[],
            upload_frequency=0
        )
        assert data.total_count == 0
        assert data.avg_views == 0.0

class TestCache:
    """Test caching functionality."""
    
    def test_cache_operations(self):
        from main import _cache
        
        _cache["test_key"] = (0, {"data": "value"})
        result = _cache.get("test_key")
        assert result is not None
        assert result[1] == {"data": "value"}
    
    def test_cache_ttl(self):
        from main import _CACHE_TTL
        assert _CACHE_TTL == 300  # 5 minutes

class TestDatabase:
    """Test database operations."""
    
    def test_search_history(self, tmp_path):
        from main import init_db, save_search_history, get_recent_searches
        import main as main_module
        original_path = main_module.DATABASE_PATH
        main_module.DATABASE_PATH = str(tmp_path / "test_history.db")
        
        try:
            init_db()
            save_search_history(["python", "tutorial"], 10)
            history = get_recent_searches()
            assert len(history) == 1
            assert history[0].query == "python, tutorial"
        finally:
            main_module.DATABASE_PATH = original_path
    
    def test_clear_history(self, tmp_path):
        from main import init_db, save_search_history, clear_search_history
        import main as main_module
        original_path = main_module.DATABASE_PATH
        main_module.DATABASE_PATH = str(tmp_path / "test_history.db")
        
        try:
            init_db()
            save_search_history(["python"], 5)
            count = clear_search_history()
            assert count >= 0
        finally:
            main_module.DATABASE_PATH = original_path

class TestFastAPIEndpoints:
    """Test FastAPI endpoints."""
    
    def test_health_endpoint(self):
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/health")
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"
    
    def test_cache_stats_endpoint(self):
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.get("/api/cache/stats")
        assert resp.status_code == 200
    
    def test_cache_clear_endpoint(self):
        from fastapi.testclient import TestClient
        from main import app
        client = TestClient(app)
        resp = client.post("/api/cache/clear")
        assert resp.status_code == 200
    
    def test_history_endpoints(self):
        from fastapi.testclient import TestClient
        from main import app, init_db
        init_db()
        client = TestClient(app)
        resp = client.get("/api/history")
        assert resp.status_code == 200
        resp = client.delete("/api/history")
        assert resp.status_code == 200

class TestErrorHandling:
    """Test error handling."""
    
    def test_missing_api_key(self):
        from fastapi.testclient import TestClient
        from main import app
        import main as main_module
        
        original_key = main_module.YOUTUBE_API_KEY
        main_module.YOUTUBE_API_KEY = ""
        
        try:
            client = TestClient(app)
            resp = client.get("/api/trends/search", params={
                "keywords": "python",
                "max_results": 10,
                "order": "viewCount",
                "time_range": "past_month"
            })
            assert resp.status_code in [422, 503]
        finally:
            main_module.YOUTUBE_API_KEY = original_key

class TestShortsEndpoint:
    """Test YouTube Shorts search endpoint."""
    
    def test_is_shorts_duration(self):
        """Test the _is_shorts_duration helper function."""
        from main import _is_shorts_duration
        
        # Shorts (under 60 seconds)
        assert _is_shorts_duration("PT30S") is True
        assert _is_shorts_duration("PT59S") is True
        assert _is_shorts_duration("PT58S") is True
        
        # Regular videos (60+ seconds)
        assert _is_shorts_duration("PT1M") is False
        assert _is_shorts_duration("PT1M30S") is False  # 90s
        assert _is_shorts_duration("PT2M30S") is False
        assert _is_shorts_duration("PT3M7S") is False
        
        # Edge cases
        assert _is_shorts_duration("") is False
    
    def test_shorts_search_endpoint(self):
        """Test the /api/trends/shorts/search endpoint with mocked API."""
        from unittest.mock import patch, AsyncMock
        from fastapi.testclient import TestClient
        from main import app
        
        mock_search_response = {
            "items": [
                {"id": {"videoId": "short1"}},
                {"id": {"videoId": "short2"}}
            ]
        }
        
        mock_video_response = {
            "items": [
                {
                    "id": "short1",
                    "snippet": {
                        "title": "Python Short 1",
                        "channelTitle": "Test Channel",
                        "publishedAt": "2024-01-01T00:00:00Z",
                        "description": "Test",
                        "tags": ["python", "shorts"],
                        "thumbnails": {"high": {"url": "https://img.youtube.com/vi/short1/hqdefault.jpg"}}
                    },
                    "statistics": {
                        "viewCount": "100000",
                        "likeCount": "5000",
                        "commentCount": "500"
                    },
                    "contentDetails": {"duration": "PT45S"}
                },
                {
                    "id": "short2",
                    "snippet": {
                        "title": "Regular Video",
                        "channelTitle": "Test Channel",
                        "publishedAt": "2024-01-01T00:00:00Z",
                        "description": "Test",
                        "tags": [],
                        "thumbnails": {"high": {"url": ""}}
                    },
                    "statistics": {
                        "viewCount": "500000",
                        "likeCount": "10000",
                        "commentCount": "1000"
                    },
                    "contentDetails": {"duration": "PT5M30S"}
                }
            ]
        }
        
        with patch('main._fetch_search', new_callable=AsyncMock) as mock_search, \
             patch('main._fetch_video_with_duration', new_callable=AsyncMock) as mock_stats:
            mock_search.return_value = mock_search_response
            mock_stats.return_value = mock_video_response
            
            from main import init_db
            init_db()
            client = TestClient(app)
            resp = client.get("/api/trends/shorts/search", params={
                "keywords": "python",
                "max_results": 10
            })
            
            assert resp.status_code == 200
            data = resp.json()
            
            assert data["total_count"] == 1
            assert len(data["videos"]) == 1
            assert data["videos"][0]["video_id"] == "short1"
            assert data["videos"][0]["title"] == "Python Short 1"
            assert data["avg_views"] == 100000.0
            assert "python" in data["top_keywords"]

if __name__ == "__main__":
    pytest.main([__file__, "-v"])
