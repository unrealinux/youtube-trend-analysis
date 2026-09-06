from dotenv import load_dotenv
load_dotenv()
"""YouTube Trend Analysis API - Optimized"""
import os
import re
import json
import sqlite3
import logging
import asyncio
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone
from collections import Counter

import httpx
from fastapi import FastAPI, HTTPException, Query, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field

_TIME_MAP = {"today": 1, "this_week": 7, "this_month": 30, "past_year": 365}
# ponytail: simple dict cache, replace with redis if concurrent requests become an issue
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300  # 5 minutes

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# ============================================================
YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
PROXY_URL = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("ALL_PROXY")
YOUTUBE_BASE_URL = "https://www.googleapis.com/youtube/v3"
REQUEST_TIMEOUT = 30.0
DATABASE_PATH = os.getenv("YT_HISTORY_DB", "youtube_history.db")
QUOTA_WARNING_THRESHOLD = 0.8  # 80% quota usage warning
# ============================================================

# ============================================================
# Models
# ============================================================
class VideoInfo(BaseModel):
    video_id: str
    title: str
    channel_title: str
    published_at: str
    view_count: int
    like_count: int
    comment_count: int
    description: str
    tags: List[str]
    thumbnail: str
    url: str = ""

class TrendData(BaseModel):
    videos: List[VideoInfo]
    total_count: int
    avg_views: float
    top_keywords: List[str]
    upload_frequency: float = 0.0

class ShortsData(BaseModel):
    videos: List[VideoInfo]
    total_count: int
    avg_views: float
    top_keywords: List[str]
    upload_frequency: float = 0.0

class SearchHistoryEntry(BaseModel):
    id: int
    query: str
    timestamp: str
    result_count: int

class QuotaStatus(BaseModel):
    used: int
    remaining: int
    limit: int
    usage_percent: float
    warning: bool = False

class ComparisonResult(BaseModel):
    query1: str
    query2: str
    stats1: Optional[Dict[str, Any]]
    stats2: Optional[Dict[str, Any]]
    comparison: Optional[Dict[str, Any]] = None

class ChannelInfo(BaseModel):
    channel_id: str
    title: str
    subscriber_count: int = 0
    video_count: int = 0
    description: str = ""
    thumbnail: str = ""

class ChannelSearchResult(BaseModel):
    channels: List[ChannelInfo]
    total_count: int

# ============================================================
# Cache helpers
# ============================================================
def _get_cached(key: str) -> Optional[dict]:
    if key in _cache:
        ts, data = _cache[key]
        if datetime.now(timezone.utc).timestamp() - ts < _CACHE_TTL:
            logger.info(f"Cache hit for {key}")
            return data
        del _cache[key]
    logger.info(f"Cache miss for {key}")
    return None

def _set_cached(key: str, data: dict) -> None:
    _cache[key] = (datetime.now(timezone.utc).timestamp(), data)

# ============================================================
# Database
# ============================================================
def get_db() -> sqlite3.Connection:
    """Get database connection with row factory."""
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db() -> None:
    """Initialize database schema."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS search_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            query TEXT NOT NULL,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            result_count INTEGER DEFAULT 0,
            keywords TEXT
        )
    """)
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_quota (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date DATE DEFAULT CURRENT_DATE,
            used INTEGER DEFAULT 0,
            remaining INTEGER DEFAULT 0,
            daily_limit INTEGER DEFAULT 0
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON search_history(timestamp)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_query ON search_history(query)")
    conn.commit()
    conn.close()
    logger.info("Database initialized")

def save_search_history(keywords: List[str], result_count: int) -> None:
    """Save search to history."""
    conn = get_db()
    cursor = conn.cursor()
    query = ", ".join(keywords)
    cursor.execute(
        "INSERT INTO search_history (query, result_count, keywords) VALUES (?, ?, ?)",
        (query, result_count, json.dumps(keywords))
    )
    conn.commit()
    conn.close()

def get_recent_searches(limit: int = 10) -> List[SearchHistoryEntry]:
    """Get recent searches."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, query, timestamp, result_count FROM search_history ORDER BY timestamp DESC LIMIT ?",
        (limit,)
    )
    rows = cursor.fetchall()
    conn.close()
    return [SearchHistoryEntry(**dict(row)) for row in rows]

def clear_search_history() -> int:
    """Clear all search history."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM search_history")
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count

# ============================================================
# API Client
# ============================================================
async def fetch_json(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    """Fetch JSON from URL with error handling."""
    try:
        resp = await client.get(url, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error: {e.response.status_code} - {e.response.text}")
        if e.response.status_code == 400:
            raise HTTPException(status_code=400, detail="Invalid YouTube API request")
        elif e.response.status_code == 403:
            raise HTTPException(status_code=403, detail="YouTube API quota exceeded")
        elif e.response.status_code == 404:
            raise HTTPException(status_code=404, detail="Resource not found")
        raise HTTPException(status_code=502, detail=f"API error: {e.response.status_code}")
    except httpx.ConnectTimeout:
        raise HTTPException(status_code=504, detail="YouTube API request timeout")
    except httpx.HTTPError as e:
        logger.error(f"HTTP error: {e}")
        raise HTTPException(status_code=502, detail=f"HTTP error: {e}")

async def _fetch_search(api_key: str, params: dict) -> dict:
    """Fetch search results from YouTube API."""
    cache_key = f"search:{hash((api_key, str(params)))}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    client_kwargs = {"timeout": REQUEST_TIMEOUT}
    if PROXY_URL:
        client_kwargs["proxy"] = PROXY_URL
    async with httpx.AsyncClient(**client_kwargs) as client:
        data = await fetch_json(client, f"{YOUTUBE_BASE_URL}/search", {**params, "key": api_key})
        _set_cached(cache_key, data)
        return data

async def _fetch_videos(api_key: str, video_ids: list, parts: str) -> dict:
    """Fetch video stats from YouTube API (statistics, snippet, and optional contentDetails)."""
    cache_key = f"videos:{hash((api_key, str(sorted(video_ids)), parts))}"
    cached = _get_cached(cache_key)
    if cached:
        return cached

    client_kwargs = {"timeout": REQUEST_TIMEOUT}
    if PROXY_URL:
        client_kwargs["proxy"] = PROXY_URL
    async with httpx.AsyncClient(**client_kwargs) as client:
        data = await fetch_json(client, f"{YOUTUBE_BASE_URL}/videos", {
            "part": parts,
            "id": ",".join(video_ids),
            "key": api_key
        })
        _set_cached(cache_key, data)
        return data

# Alias for backward compatibility
_fetch_video_stats = _fetch_videos
_fetch_video_with_duration = _fetch_videos

# ============================================================
# Data Processing
# ============================================================
async def fetch_trending_videos(api_key: str, params: dict) -> TrendData:
    """Fetch and process trending videos."""
    search_coro = _fetch_search(api_key, params)

    try:
        search_data = await search_coro
    except httpx.ConnectTimeout:
        raise HTTPException(status_code=504, detail="YouTube API request timeout")

    video_ids = [item["id"]["videoId"] for item in search_data.get("items", [])]
    if not video_ids:
        return TrendData(videos=[], total_count=0, avg_views=0, top_keywords=[], upload_frequency=0)

    stats_coro = _fetch_video_stats(api_key, video_ids, "statistics,snippet")

    try:
        stats_data = await stats_coro
    except httpx.ConnectTimeout:
        raise HTTPException(status_code=504, detail="YouTube API request timeout")

    videos = []
    all_tags = []
    total_views = 0
    published_dates = []

    for item in stats_data.get("items", []):
        snippet = item["snippet"]
        stats = item.get("statistics", {})
        pub_date = snippet.get("publishedAt", "")
        if pub_date:
            published_dates.append(pub_date)

        video = VideoInfo(
            video_id=item["id"],
            title=snippet["title"],
            channel_title=snippet["channelTitle"],
            published_at=pub_date,
            view_count=int(stats.get("viewCount", 0)),
            like_count=int(stats.get("likeCount", 0)),
            comment_count=int(stats.get("commentCount", 0)),
            description=snippet["description"][:500],
            tags=snippet.get("tags", []),
            thumbnail=snippet["thumbnails"]["high"]["url"] if snippet.get("thumbnails", {}).get("high") else "",
            url=f"https://www.youtube.com/watch?v={item['id']}"
        )
        videos.append(video)
        total_views += video.view_count
        all_tags.extend(video.tags)

    top_keywords = [kw for kw, _ in Counter(all_tags).most_common(20)]
    avg_views = total_views / len(videos) if videos else 0

    upload_frequency = 0.0
    if len(published_dates) >= 2:
        try:
            dates = sorted([datetime.fromisoformat(d.replace("Z", "+00:00")) for d in published_dates])
            span_days = max((dates[-1] - dates[0]).days, 1)
            upload_frequency = round(len(dates) / span_days * 30, 2)
        except (ValueError, TypeError):
            upload_frequency = round(len(videos) / 30, 2)
    else:
        upload_frequency = round(len(videos) / 30, 2)

    return TrendData(
        videos=videos,
        total_count=len(videos),
        avg_views=round(avg_views, 2),
        top_keywords=top_keywords,
        upload_frequency=upload_frequency
    )

# ============================================================
# FastAPI Application
# ============================================================
app = FastAPI(title="YouTube Trend Analysis", version="2.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8001", "http://127.0.0.1:8001", "http://localhost", "http://127.0.0.1"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize database on startup
@app.on_event("startup")
async def startup_event():
    init_db()
    logger.info("YouTube Trend Analysis API started")

# Mount static files
if os.path.exists("static"):
    app.mount("/static", StaticFiles(directory="static"), name="static")

# ============================================================
# Endpoints
# ============================================================
@app.get("/", response_class=HTMLResponse)
async def index():
    template_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    with open(template_path, "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "api_key_configured": bool(YOUTUBE_API_KEY),
        "cache_size": len(_cache),
        "database": DATABASE_PATH
    }

@app.get("/api/quota", response_model=QuotaStatus)
async def get_quota():
    """Check YouTube API quota status."""
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")

    client_kwargs = {"timeout": REQUEST_TIMEOUT}
    if PROXY_URL:
        client_kwargs["proxy"] = PROXY_URL
    async with httpx.AsyncClient(**client_kwargs) as client:
        resp = await client.get(YOUTUBE_BASE_URL, params={
            "part": "snippet",
            "key": YOUTUBE_API_KEY
        })
        # Quota check - just verify API key works

    quota = 1  # Minimum quota usage for successful request
    daily_limit = 10000  # Default quota
    remaining = max(0, daily_limit - quota)
    usage_percent = quota / daily_limit

    warning = usage_percent >= QUOTA_WARNING_THRESHOLD

    # Save quota status
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO api_quota (used, remaining, daily_limit) VALUES (?, ?, ?)",
        (quota, remaining, daily_limit)
    )
    conn.commit()
    conn.close()

    if warning:
        logger.warning(f"High quota usage: {usage_percent:.1%}")

    return QuotaStatus(
        used=quota,
        remaining=remaining,
        limit=daily_limit,
        usage_percent=round(usage_percent * 100, 2),
        warning=warning
    )

@app.get("/api/trends/search", response_model=TrendData)
async def search_trends(
    keywords: str = Query(..., description="Comma-separated keywords"),
    max_results: int = Query(default=20, ge=1, le=50),
    order: str = Query(default="viewCount", pattern="relevance|date|viewCount|rating"),
    time_range: str = Query(default="past_year")
):
    """Search trends by keywords."""
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")

    kw_list = [kw.strip() for kw in keywords.split(",") if kw.strip()]
    days = _TIME_MAP.get(time_range, 365)
    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "part": "snippet",
        "q": " ".join(kw_list) if kw_list else "",
        "maxResults": max_results,
        "order": order,
        "type": "video",
        "publishedAfter": published_after
    }

    try:
        result = await fetch_trending_videos(YOUTUBE_API_KEY, params)
        save_search_history(kw_list, result.total_count)
        logger.info(f"Search completed: {kw_list} -> {result.total_count} results")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/trends/channel", response_model=TrendData)
async def get_channel_trends(
    channel_id: str = Query(..., description="YouTube Channel ID"),
    max_results: int = Query(default=20, ge=1, le=50),
    order: str = Query(default="date", pattern="date|viewCount|rating"),
    time_range: str = Query(default="past_year")
):
    """Get channel trends."""
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")

    days = _TIME_MAP.get(time_range, 365)
    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "part": "snippet",
        "channelId": channel_id,
        "maxResults": max_results,
        "order": order,
        "type": "video",
        "publishedAfter": published_after
    }

    try:
        result = await fetch_trending_videos(YOUTUBE_API_KEY, params)
        logger.info(f"Channel search: {channel_id} -> {result.total_count} results")
        return result
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Channel search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/history", response_model=List[SearchHistoryEntry])
async def get_history(limit: int = 10):
    """Get search history."""
    return get_recent_searches(limit)

@app.delete("/api/history")
async def delete_history():
    """Clear search history."""
    count = clear_search_history()
    logger.info(f"Cleared {count} history entries")
    return {"deleted": count}

@app.post("/api/compare")
async def compare_searches(req: dict):
    """Compare two search queries."""
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")

    query1 = req.get("query1", "")
    query2 = req.get("query2", "")

    if not query1 or not query2:
        raise HTTPException(status_code=400, detail="Both queries are required")

    keywords1 = [kw.strip() for kw in query1.split(",")]
    keywords2 = [kw.strip() for kw in query2.split(",")]

    try:
        params1 = {
            "part": "snippet",
            "q": " ".join(keywords1),
            "maxResults": 20,
            "order": "viewCount",
            "type": "video",
            "publishedAfter": (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
        }
        params2 = {
            "part": "snippet",
            "q": " ".join(keywords2),
            "maxResults": 20,
            "order": "viewCount",
            "type": "video",
            "publishedAfter": (datetime.now(timezone.utc) - timedelta(days=365)).strftime("%Y-%m-%dT%H:%M:%SZ")
        }

        result1 = await fetch_trending_videos(YOUTUBE_API_KEY, params1)
        result2 = await fetch_trending_videos(YOUTUBE_API_KEY, params2)

        # Calculate comparison metrics
        comparison = {
            "query1_avg_views": result1.avg_views,
            "query2_avg_views": result2.avg_views,
            "query1_total_views": sum(v.view_count for v in result1.videos),
            "query2_total_views": sum(v.view_count for v in result2.videos),
            "query1_total_likes": sum(v.like_count for v in result1.videos),
            "query2_total_likes": sum(v.like_count for v in result2.videos),
            "query1_upload_freq": result1.upload_frequency,
            "query2_upload_freq": result2.upload_frequency,
            "winner": "query1" if result1.avg_views > result2.avg_views else "query2"
        }

        return ComparisonResult(
            query1=query1,
            query2=query2,
            stats1={
                "total_count": result1.total_count,
                "avg_views": result1.avg_views,
                "top_keywords": result1.top_keywords
            },
            stats2={
                "total_count": result2.total_count,
                "avg_views": result2.avg_views,
                "top_keywords": result2.top_keywords
            },
            comparison=comparison
        )
    except Exception as e:
        logger.error(f"Comparison error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/cache/stats")
async def cache_stats():
    """Get cache statistics."""
    return {
        "size": len(_cache),
        "max_size": 100,
        "ttl": _CACHE_TTL
    }

@app.post("/api/cache/clear")
async def clear_cache():
    """Clear the cache."""
    _cache.clear()
    logger.info("Cache cleared")

@app.get("/api/trends/channels/search", response_model=ChannelSearchResult)
async def search_channels(
    q: str = Query(..., description="Channel name to search"),
    max_results: int = Query(default=10, ge=1, le=50)
):
    """Search YouTube channels by name."""
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")

    params = {
        "part": "snippet",
        "q": q,
        "type": "channel",
        "maxResults": max_results
    }

    try:
        data = await _fetch_search(YOUTUBE_API_KEY, params)
        channels = []
        for item in data.get("items", []):
            snippet = item.get("snippet", {})
            channel_id = item.get("id", {}).get("channelId", "")
            channels.append(ChannelInfo(
                channel_id=channel_id,
                title=snippet.get("title", ""),
                description=snippet.get("description", ""),
                thumbnail=snippet.get("thumbnails", {}).get("high", {}).get("url", "")
            ))
        return ChannelSearchResult(channels=channels, total_count=len(channels))
    except Exception as e:
        logger.error(f"Channel search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

def _is_shorts_duration(duration: str) -> bool:
    """Check if a YouTube ISO 8601 duration string represents a Short (< 60 seconds)."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return False
    h, m, s = match.groups()
    total_seconds = int(h or 0) * 3600 + int(m or 0) * 60 + int(s or 0)
    return total_seconds < 60


@app.get("/api/trends/shorts/search", response_model=ShortsData)
async def search_shorts(
    keywords: str = Query(..., description="Comma-separated keywords"),
    max_results: int = Query(default=20, ge=1, le=50),
    order: str = Query(default="viewCount", pattern="relevance|date|viewCount|rating"),
    time_range: str = Query(default="past_year")
):
    """Search YouTube Shorts by keywords (videos under 60 seconds)."""
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")

    kw_list = [kw.strip() for kw in keywords.split(",") if kw.strip()]
    days = _TIME_MAP.get(time_range, 365)
    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # Search with #shorts and duration filter
    search_params = {
        "part": "snippet",
        "q": " ".join(kw_list) + " #shorts",
        "maxResults": max_results,
        "order": order,
        "type": "video",
        "publishedAfter": published_after
    }

    try:
        data = await _fetch_search(YOUTUBE_API_KEY, search_params)
        items = data.get("items", [])

        # Fetch stats for all videos
        video_ids = [item["id"]["videoId"] for item in items]
        if not video_ids:
            return ShortsData(videos=[], total_count=0, avg_views=0, top_keywords=[])

        stats_data = await _fetch_video_with_duration(YOUTUBE_API_KEY, video_ids, "statistics,snippet,contentDetails")

        videos = []
        all_tags = []
        total_views = 0

        for item in stats_data.get("items", []):
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            duration = item.get("contentDetails", {}).get("duration", "")

            # Filter: only include videos under 60 seconds (Shorts)
            if not _is_shorts_duration(duration):
                continue

            video = VideoInfo(
                video_id=item["id"],
                title=snippet["title"],
                channel_title=snippet["channelTitle"],
                published_at=snippet.get("publishedAt", ""),
                view_count=int(stats.get("viewCount", 0)),
                like_count=int(stats.get("likeCount", 0)),
                comment_count=int(stats.get("commentCount", 0)),
                description=snippet["description"][:500],
                tags=snippet.get("tags", []),
                thumbnail=snippet["thumbnails"]["high"]["url"] if snippet.get("thumbnails", {}).get("high") else "",
                url=f"https://www.youtube.com/watch?v={item['id']}"
            )
            videos.append(video)
            total_views += video.view_count
            all_tags.extend(video.tags)

        top_keywords = [kw for kw, _ in Counter(all_tags).most_common(20)]
        avg_views = total_views / len(videos) if videos else 0

        save_search_history(kw_list, len(videos))
        return ShortsData(videos=videos, total_count=len(videos), avg_views=avg_views, top_keywords=top_keywords)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Shorts search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
