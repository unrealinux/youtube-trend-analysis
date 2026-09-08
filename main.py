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

# Popular Shorts categories for trending analysis
HOT_CATEGORIES = [
    "搞笑", "美食", "健身", "科技", "游戏", "宠物", "旅行", "舞蹈",
    "美妆", "教育", "手工", "音乐", "运动", "汽车", "编程", "AI",
    "生活小窍门", "挑战", "反应视频", "ASMR", "Vlog", "开箱",
    "街拍", "美食制作", "特效", "魔术", "极限运动", "萌宠",
    "情侣日常", "亲子", "职场", "理财", "健康养生", "语言学习"
]

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

class FeatureAnalysisResult(BaseModel):
    keyword: str
    total_videos: int
    avg_views: float
    title_patterns: List[str] = []
    avg_duration_sec: float = 0.0
    duration_range: str = ""
    top_engagement_keywords: List[str] = []
    optimal_posting_hours: List[str] = []
    success_score: float = 0.0

class TrendSnapshot(BaseModel):
    keyword: str
    avg_views: float
    total_videos: int
    engagement_rate: float = 0.0
    recorded_at: str

class TrendChange(BaseModel):
    keyword: str
    direction: str  # "rising", "falling", "stable"
    change_pct: float
    current_avg_views: float
    previous_avg_views: float
    change_timestamp: str

class TrendTrackingResult(BaseModel):
    keyword: str
    snapshots: List[TrendSnapshot]
    latest_change: Optional[TrendChange] = None
    trend_direction: str = ""

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
class KeywordResult(BaseModel):
    keyword: str
    avg_views: float
    total_views: int
    video_count: int
    top_keywords: List[str]
    engagement_rate: float = 0.0

class BatchScanResult(BaseModel):
    keywords: List[KeywordResult]
    sorted_by: str = "avg_views"
    scanned_at: str

class HotCategoryResult(BaseModel):
    category: str
    avg_views: float
    total_views: int
    video_count: int
    engagement_rate: float = 0.0
    top_video_views: int = 0
    top_keywords: List[str] = []

class HourlyStats(BaseModel):
    hour: int
    avg_views: float
    avg_likes: float
    avg_comments: float
    video_count: int
    engagement_rate: float = 0.0

class DurationBucket(BaseModel):
    label: str
    count: int
    pct: float
    avg_views: float
    avg_engagement: float

class ViewsQuantile(BaseModel):
    percentile: int
    min_views: int
    max_views: int
    avg_views: float

class TitleLengthBreakdown(BaseModel):
    range_start: int
    range_end: int
    count: int
    pct: float
    avg_views: float

class ShortsDetailedResult(BaseModel):
    keyword: str
    total_videos: int
    avg_views: float
    median_views: int
    p25_views: int
    p75_views: int
    total_views: int
    total_likes: int
    total_comments: int
    overall_engagement_rate: float
    duration_buckets: List[DurationBucket] = []
    hourly_stats: List[HourlyStats] = []
    views_quantiles: List[ViewsQuantile] = []
    title_length_breakdown: List[TitleLengthBreakdown] = []
    top_tags_by_views: List[Dict[str, Any]] = []
    top_channels: List[Dict[str, Any]] = []
    top_keywords: List[str] = []
    top_video_ids: List[str] = []

class ChannelInsightVideo(BaseModel):
    video_id: str
    title: str
    published_at: str
    view_count: int
    like_count: int
    comment_count: int
    duration_sec: int
    thumbnail: str

class ChannelInsightsResult(BaseModel):
    channel_id: str
    channel_title: str
    total_shorts: int
    total_views: int
    total_likes: int
    total_comments: int
    avg_views: float
    avg_engagement_rate: float
    upload_frequency_per_month: float
    top_videos: List[ChannelInsightVideo] = []
    views_over_time: List[Dict[str, Any]] = []
    duration_histogram: List[Dict[str, Any]] = []
    top_tags: List[str] = []
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
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS trend_tracking (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            keyword TEXT NOT NULL,
            avg_views REAL NOT NULL,
            total_videos INTEGER NOT NULL,
            engagement_rate REAL DEFAULT 0,
            recorded_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_keyword ON trend_tracking(keyword)")
    cursor.execute("CREATE INDEX IF NOT EXISTS idx_recorded_at ON trend_tracking(recorded_at)")
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
def save_trend_snapshot(keyword: str, avg_views: float, total_videos: int, engagement_rate: float = 0.0) -> None:
    """Save a trend snapshot to database."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO trend_tracking (keyword, avg_views, total_videos, engagement_rate) VALUES (?, ?, ?, ?)",
        (keyword, avg_views, total_videos, engagement_rate)
    )
    conn.commit()
    conn.close()

def get_trend_snapshots(keyword: str, limit: int = 30) -> List[TrendSnapshot]:
    """Get trend history for a keyword."""
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT keyword, avg_views, total_videos, engagement_rate, recorded_at FROM trend_tracking WHERE keyword = ? ORDER BY recorded_at DESC LIMIT ?",
        (keyword, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    return [TrendSnapshot(**dict(row)) for row in rows]

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
    # Read actual remaining quota from YouTube API response header
    residual_quota = int(resp.headers.get("X-Goog-Residual-Quota", 0))
    # YouTube default daily limit is 10,000 units
    daily_limit = 10000
    remaining = max(0, residual_quota if residual_quota > 0 else daily_limit - quota)
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

@app.get("/api/trends/batch-scan", response_model=BatchScanResult)
async def batch_scan(
    keywords: str = Query(..., description="Comma-separated keywords to scan"),
    max_results: int = Query(default=20, ge=1, le=50),
    time_range: str = Query(default="this_month")
):
    """Scan multiple keywords and return ranked results."""
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")

    kw_list = [kw.strip() for kw in keywords.split(",") if kw.strip()]
    if not kw_list:
        raise HTTPException(status_code=400, detail="No keywords provided")

    days = _TIME_MAP.get(time_range, 30)
    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    results = []
    # ponytail: concurrent batch scan - process all keywords in parallel instead of serial
    async def scan_keyword(keyword):
        """Process a single keyword and return its result."""
        try:
            params = {
                "part": "snippet",
                "q": keyword + " #shorts",
                "maxResults": max_results,
                "order": "viewCount",
                "type": "video",
                "publishedAfter": published_after
            }
            data = await _fetch_search(YOUTUBE_API_KEY, params)
            items = data.get("items", [])

            video_ids = [item["id"]["videoId"] for item in items]
            if not video_ids:
                return None

            stats_data = await _fetch_video_with_duration(YOUTUBE_API_KEY, video_ids, "statistics,snippet,contentDetails")

            videos = []
            all_tags = []
            total_views = 0

            for item in stats_data.get("items", []):
                snippet = item["snippet"]
                stats = item.get("statistics", {})
                duration = item.get("contentDetails", {}).get("duration", "")

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
                    description="",
                    tags=snippet.get("tags", []),
                    thumbnail=snippet["thumbnails"]["high"]["url"] if snippet.get("thumbnails", {}).get("high") else "",
                    url=f"https://www.youtube.com/watch?v={item['id']}"
                )
                videos.append(video)
                total_views += video.view_count
                all_tags.extend(video.tags)

            if videos:
                avg_views = total_views / len(videos)
                total_likes = sum(v.like_count for v in videos)
                total_comments = sum(v.comment_count for v in videos)
                engagement_rate = (total_likes + total_comments) / max(total_views, 1) * 100
                top_tags = [kw for kw, _ in Counter(all_tags).most_common(10)]

                return KeywordResult(
                    keyword=keyword,
                    avg_views=round(avg_views, 2),
                    total_views=total_views,
                    video_count=len(videos),
                    top_keywords=top_tags,
                    engagement_rate=round(engagement_rate, 2)
                )
        except Exception as e:
            logger.error(f"Error scanning keyword '{keyword}': {e}")
        return None

    scanned = await asyncio.gather(*(scan_keyword(kw) for kw in kw_list))
    results = [r for r in scanned if r is not None]

    results.sort(key=lambda x: x.avg_views, reverse=True)

    return BatchScanResult(
        keywords=results,
        sorted_by="avg_views",
        scanned_at=datetime.now(timezone.utc).isoformat()
    )

@app.get("/api/trends/hot-categories", response_model=List[HotCategoryResult])
async def get_hot_categories(
    time_range: str = Query(default="this_month"),
    max_results: int = Query(default=10, ge=1, le=30)
):
    """Get ranked Shorts categories by average views."""
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")

    days = _TIME_MAP.get(time_range, 30)
    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    # ponytail: concurrent hot category scan - process all categories in parallel
    async def scan_category(category):
        """Process a single category and return its result."""
        try:
            params = {
                "part": "snippet",
                "q": category + " #shorts",
                "maxResults": max_results,
                "order": "viewCount",
                "type": "video",
                "publishedAfter": published_after
            }
            data = await _fetch_search(YOUTUBE_API_KEY, params)
            items = data.get("items", [])

            video_ids = [item["id"]["videoId"] for item in items]
            if not video_ids:
                return None

            stats_data = await _fetch_video_with_duration(YOUTUBE_API_KEY, video_ids, "statistics,snippet,contentDetails")

            videos = []
            all_tags = []
            total_views = 0
            top_views = 0

            for item in stats_data.get("items", []):
                snippet = item["snippet"]
                stats = item.get("statistics", {})
                duration = item.get("contentDetails", {}).get("duration", "")

                if not _is_shorts_duration(duration):
                    continue

                view_count = int(stats.get("viewCount", 0))
                total_views += view_count
                if view_count > top_views:
                    top_views = view_count

                videos.append(VideoInfo(
                    video_id=item["id"],
                    title=snippet["title"],
                    channel_title=snippet["channelTitle"],
                    published_at=snippet.get("publishedAt", ""),
                    view_count=view_count,
                    like_count=int(stats.get("likeCount", 0)),
                    comment_count=int(stats.get("commentCount", 0)),
                    description="",
                    tags=snippet.get("tags", []),
                    thumbnail=snippet["thumbnails"]["high"]["url"] if snippet.get("thumbnails", {}).get("high") else "",
                    url=""
                ))
                all_tags.extend(videos[-1].tags)

            if videos:
                avg_views = total_views / len(videos)
                total_likes = sum(v.like_count for v in videos)
                total_comments = sum(v.comment_count for v in videos)
                engagement_rate = (total_likes + total_comments) / max(total_views, 1) * 100
                top_tags = [kw for kw, _ in Counter(all_tags).most_common(8)]

                return HotCategoryResult(
                    category=category,
                    avg_views=round(avg_views, 2),
                    total_views=total_views,
                    video_count=len(videos),
                    engagement_rate=round(engagement_rate, 2),
                    top_video_views=top_views,
                    top_keywords=top_tags
                )
        except Exception as e:
            logger.error(f"Error scanning category '{category}': {e}")
        return None

    scanned = await asyncio.gather(*(scan_category(cat) for cat in HOT_CATEGORIES))
    results = [r for r in scanned if r is not None]
    results.sort(key=lambda x: x.avg_views, reverse=True)
    return results

@app.get("/api/trends/feature-analysis", response_model=FeatureAnalysisResult)
async def analyze_features(
    keyword: str = Query(..., description="Keyword to analyze"),
    max_results: int = Query(default=30, ge=1, le=50),
    time_range: str = Query(default="this_month")
):
    """Analyze top performing Shorts to extract success patterns."""
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")

    days = _TIME_MAP.get(time_range, 30)
    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "part": "snippet",
        "q": keyword + " #shorts",
        "maxResults": max_results,
        "order": "viewCount",
        "type": "video",
        "publishedAfter": published_after
    }

    try:
        data = await _fetch_search(YOUTUBE_API_KEY, params)
        items = data.get("items", [])

        video_ids = [item["id"]["videoId"] for item in items]
        if not video_ids:
            raise HTTPException(status_code=404, detail="No videos found")

        stats_data = await _fetch_video_with_duration(YOUTUBE_API_KEY, video_ids, "statistics,snippet,contentDetails")

        videos = []
        all_titles = []
        all_tags = []
        total_views = 0

        for item in stats_data.get("items", []):
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            duration = item.get("contentDetails", {}).get("duration", "")

            if not _is_shorts_duration(duration):
                continue

            view_count = int(stats.get("viewCount", 0))
            videos.append(VideoInfo(
                video_id=item["id"],
                title=snippet["title"],
                channel_title=snippet["channelTitle"],
                published_at=snippet.get("publishedAt", ""),
                view_count=view_count,
                like_count=int(stats.get("likeCount", 0)),
                comment_count=int(stats.get("commentCount", 0)),
                description="",
                tags=snippet.get("tags", []),
                thumbnail=snippet["thumbnails"]["high"]["url"] if snippet.get("thumbnails", {}).get("high") else "",
                url=""
            ))
            all_titles.append(snippet["title"])
            all_tags.extend(videos[-1].tags)
            total_views += view_count

        if not videos:
            raise HTTPException(status_code=404, detail="No Shorts found")

        title_patterns = []
        # Analyze title patterns
        emoji_count = sum(1 for t in all_titles if re.search(r'[\U0001F600-\U0001F64F\U0001F300-\U0001F5FF\U0001F680-\U0001F6FF\U0001F1E0-\U0001F1FF\U00002702-\U000027B0\U000024C2-\U0001F251]', t))
        avg_title_len = sum(len(t) for t in all_titles) / len(all_titles) if all_titles else 0
        if avg_title_len > 30:
            title_patterns.append(f"avg title length {avg_title_len:.0f} chars")

        # Duration analysis
        durations = []
        for item in stats_data.get("items", []):
            duration = item.get("contentDetails", {}).get("duration", "")
            if duration:
                match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
                total_seconds = int(match.group(1) or 0) * 3600 + int(match.group(2) or 0) * 60 + int(match.group(3) or 0)
                durations.append(total_seconds)
            durations.sort(reverse=True)

        # Calculate engagement by tag
        tag_engagement = {}
        for video in videos:
            total_engagement = video.like_count + video.comment_count
            for tag in video.tags:
                if tag not in tag_engagement:
                    tag_engagement[tag] = []
                tag_engagement[tag].append(video.view_count)

        top_engagement_keywords = []
        for tag, views_list in tag_engagement.items():
            avg = sum(views_list) / len(views_list)
            top_engagement_keywords.append((tag, avg))
        top_engagement_keywords.sort(key=lambda x: x[1], reverse=True)
        top_engagement_keywords = [kw for kw, _ in top_engagement_keywords[:8]]

        # Calculate success score (0-100)
        avg_views = total_views / len(videos) if videos else 0
        engagement_rate = sum(v.like_count + v.comment_count for v in videos) / max(total_views, 1) * 100
        # Score based on avg views (normalized), engagement rate, and content quality
        views_score = min(avg_views / 1000000, 100) * 0.6
        engagement_score = min(engagement_rate * 5, 100) * 0.4
        success_score = round(views_score + engagement_score, 2)

        # Determine duration range
        if durations:
            short_pct = sum(1 for d in durations if d < 30) / len(durations) * 100
            med_pct = sum(1 for d in durations if 30 <= d < 45) / len(durations) * 100
            long_pct = sum(1 for d in durations if d >= 45) / len(durations) * 100
            optimal = "<30s" if short_pct > 50 else "30-45s" if med_pct > 50 else ">45s"
            duration_range = f"{optimal} (short:{short_pct:.0f}%|med:{med_pct:.0f}%|long:{long_pct:.0f}%)"
            avg_duration = sum(durations) / len(durations)
        else:
            duration_range = "N/A"
            avg_duration = 0

        return FeatureAnalysisResult(
            keyword=keyword,
            total_videos=len(videos),
            avg_views=round(avg_views, 2),
            title_patterns=title_patterns,
            avg_duration_sec=round(avg_duration, 1),
            duration_range=duration_range,
            top_engagement_keywords=top_engagement_keywords,
            optimal_posting_hours=analyze_posting_hours(videos),
            success_score=success_score
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Feature analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/trends/trend-tracking", response_model=TrendTrackingResult)
async def get_trend_tracking(
    keyword: str = Query(..., description="Keyword to track"),
    limit: int = Query(default=30, ge=1, le=100),
    refresh: bool = Query(default=False, description="Force refresh from YouTube API")
):
    """Get trend history and detection for a keyword."""
    snapshots = get_trend_snapshots(keyword, limit)

    if (not snapshots) and refresh:
        if not YOUTUBE_API_KEY:
            raise HTTPException(status_code=503, detail="YouTube API Key not configured")

        days = 30
        published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "part": "snippet",
            "q": keyword + " #shorts",
            "maxResults": 20,
            "order": "viewCount",
            "type": "video",
            "publishedAfter": published_after
        }

        try:
            data = await _fetch_search(YOUTUBE_API_KEY, params)
            items = data.get("items", [])

            video_ids = [item["id"]["videoId"] for item in items]
            if not video_ids:
                raise HTTPException(status_code=404, detail="No videos found")

            stats_data = await _fetch_video_stats(YOUTUBE_API_KEY, video_ids, "statistics,snippet")

            videos = []
            total_views = 0
            all_tags = []

            for item in stats_data.get("items", []):
                snippet = item["snippet"]
                stats = item.get("statistics", {})

                view_count = int(stats.get("viewCount", 0))
                videos.append(VideoInfo(
                    video_id=item["id"],
                    title=snippet["title"],
                    channel_title=snippet["channelTitle"],
                    published_at=snippet.get("publishedAt", ""),
                    view_count=view_count,
                    like_count=int(stats.get("likeCount", 0)),
                    comment_count=int(stats.get("commentCount", 0)),
                    description=snippet["description"][:500],
                    tags=snippet.get("tags", []),
                    thumbnail=snippet["thumbnails"]["high"]["url"] if snippet.get("thumbnails", {}).get("high") else "",
                    url=f"https://www.youtube.com/watch?v={item["id"]}"
                ))
                all_tags.extend(videos[-1].tags)
                total_views += view_count

            if not videos:
                raise HTTPException(status_code=404, detail="No Shorts found")

            avg_views = total_views / len(videos)
            total_likes = sum(v.like_count for v in videos)
            total_comments = sum(v.comment_count for v in videos)
            engagement_rate = (total_likes + total_comments) / max(total_views, 1) * 100

            save_trend_snapshot(keyword, avg_views, len(videos), engagement_rate)
            snapshots = get_trend_snapshots(keyword, limit)

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Trend tracking error: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    # Sort by recorded_at ascending for trend calculation
    sorted_snapshots = sorted(snapshots, key=lambda s: s.recorded_at)
    trend_direction = "stable"
    latest_change = None
    if len(sorted_snapshots) >= 2:
        previous = sorted_snapshots[-2]
        current = sorted_snapshots[-1]
        change_pct = ((current.avg_views - previous.avg_views) / max(previous.avg_views, 1)) * 100
        if change_pct > 10:
            trend_direction = "rising"
            latest_change = TrendChange(keyword=keyword, direction="rising", change_pct=round(change_pct, 2), current_avg_views=current.avg_views, previous_avg_views=previous.avg_views, change_timestamp=current.recorded_at)
        elif change_pct < -10:
            trend_direction = "falling"
            latest_change = TrendChange(keyword=keyword, direction="falling", change_pct=round(change_pct, 2), current_avg_views=current.avg_views, previous_avg_views=previous.avg_views, change_timestamp=current.recorded_at)
    return TrendTrackingResult(keyword=keyword, snapshots=snapshots, latest_change=latest_change, trend_direction=trend_direction)
@app.get("/api/cache/stats")
async def cache_stats():
    """Get cache statistics."""
    return {
        "size": len(_cache),
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

def analyze_posting_hours(videos: List[VideoInfo]) -> List[str]:
    """Return hours (0-23) with highest engagement rate, sorted descending."""
    hour_stats: Dict[int, Dict[str, int]] = {}
    for v in videos:
        if not v.published_at:
            continue
        try:
            dt = datetime.fromisoformat(v.published_at.replace("Z", "+00:00"))
            h = dt.hour
            if h not in hour_stats:
                hour_stats[h] = {"views": 0, "likes": 0, "comments": 0, "count": 0}
            hour_stats[h]["views"] += v.view_count
            hour_stats[h]["likes"] += v.like_count
            hour_stats[h]["comments"] += v.comment_count
            hour_stats[h]["count"] += 1
        except (ValueError, TypeError):
            pass
    if not hour_stats:
        return []
    scored = []
    for h, s in hour_stats.items():
        eng = (s["likes"] + s["comments"]) / max(s["views"], 1)
        scored.append((h, eng, s["count"]))
    scored.sort(key=lambda x: x[1], reverse=True)
    return [f"{h:02d}:00-{(h+1)%24:02d}:00" for h, _, _ in scored[:5]]

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

async def _build_detailed_result(keyword: str, items: list, stats_data: dict) -> ShortsDetailedResult:
    """Build detailed analysis result from search items and video stats."""
    videos = []
    all_tags = []
    total_views = 0
    total_likes = 0
    total_comments = 0

    for item in stats_data.get("items", []):
        snippet = item["snippet"]
        stats = item.get("statistics", {})
        duration_str = item.get("contentDetails", {}).get("duration", "")
        if not _is_shorts_duration(duration_str):
            continue
        vid = VideoInfo(
            video_id=item["id"],
            title=snippet["title"],
            channel_title=snippet["channelTitle"],
            published_at=snippet.get("publishedAt", ""),
            view_count=int(stats.get("viewCount", 0)),
            like_count=int(stats.get("likeCount", 0)),
            comment_count=int(stats.get("commentCount", 0)),
            description="",
            tags=snippet.get("tags", []),
            thumbnail=snippet["thumbnails"]["high"]["url"] if snippet.get("thumbnails", {}).get("high") else "",
            url=f"https://www.youtube.com/watch?v={item['id']}"
        )
        videos.append(vid)
        total_views += vid.view_count
        total_likes += vid.like_count
        total_comments += vid.comment_count
        all_tags.extend(vid.tags)

    if not videos:
        return ShortsDetailedResult(keyword=keyword, total_videos=0)

    view_counts = sorted([v.view_count for v in videos])
    n = len(view_counts)
    p25 = view_counts[int(n * 0.25)] if n >= 4 else view_counts[0]
    p75 = view_counts[int(n * 0.75)] if n >= 4 else view_counts[-1]
    median = view_counts[n // 2]

    # Duration buckets
    dur_buckets = {"0-15s": [], "15-30s": [], "30-45s": [], "45-60s": []}
    for v in videos:
        dur_str = next((i.get("contentDetails", {}).get("duration", "") for i in stats_data.get("items", []) if i["id"] == v.video_id), "")
        m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", dur_str)
        secs = int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0) if m else 0
        if secs < 15: b = "0-15s"
        elif secs < 30: b = "15-30s"
        elif secs < 45: b = "30-45s"
        else: b = "45-60s"
        dur_buckets[b].append(v)

    duration_buckets = []
    for label, vlist in dur_buckets.items():
        if vlist:
            tv = sum(v.view_count for v in vlist)
            duration_buckets.append(DurationBucket(
                label=label, count=len(vlist), pct=round(len(vlist)/n*100, 1),
                avg_views=round(tv/len(vlist), 2),
                avg_engagement=round((sum(v.like_count for v in vlist)+sum(v.comment_count for v in vlist))/max(tv,1)*100, 2)
            ))

    # Hourly stats
    hour_data: Dict[int, Dict[str, int]] = {}
    for v in videos:
        if not v.published_at: continue
        try:
            dt = datetime.fromisoformat(v.published_at.replace("Z", "+00:00"))
            h = dt.hour
            if h not in hour_data:
                hour_data[h] = {"views": 0, "likes": 0, "comments": 0, "count": 0}
            hour_data[h]["views"] += v.view_count
            hour_data[h]["likes"] += v.like_count
            hour_data[h]["comments"] += v.comment_count
            hour_data[h]["count"] += 1
        except (ValueError, TypeError):
            pass
    hourly_stats = []
    for h, s in sorted(hour_data.items()):
        er = (s["likes"] + s["comments"]) / max(s["views"], 1) * 100
        hourly_stats.append(HourlyStats(
            hour=h, avg_views=round(s["views"]/s["count"], 2),
            avg_likes=round(s["likes"]/s["count"], 2),
            avg_comments=round(s["comments"]/s["count"], 2),
            video_count=s["count"], engagement_rate=round(er, 2)
        ))

    # Views quantiles
    quantile_bins = [(10, 25, "P10-P25"), (25, 50, "P25-P50"), (50, 75, "P50-P75"), (75, 100, "P75-P100")]
    views_quantiles = []
    for lo, hi, label in quantile_bins:
        idx_lo = max(0, int(n * lo / 100) - 1)
        idx_hi = min(n - 1, int(n * hi / 100))
        slice_v = view_counts[idx_lo:idx_hi+1]
        if slice_v:
            views_quantiles.append(ViewsQuantile(
                percentile=int((lo+hi)/2), min_views=slice_v[0], max_views=slice_v[-1],
                avg_views=round(sum(slice_v)/len(slice_v), 2)
            ))

    # Title length breakdown
    title_ranges = [(0, 20), (20, 30), (30, 40), (40, 60), (60, 100)]
    title_breakdown = []
    for lo, hi in title_ranges:
        sublist = [v for v in videos if lo <= len(v.title) < hi]
        if sublist:
            tv = sum(v.view_count for v in sublist)
            title_breakdown.append(TitleLengthBreakdown(
                range_start=lo, range_end=hi, count=len(sublist),
                pct=round(len(sublist)/n*100, 1),
                avg_views=round(tv/len(sublist), 2)
            ))

    # Top tags by views
    tag_views: Dict[str, List[int]] = {}
    for v in videos:
        for t in v.tags:
            tag_views.setdefault(t, []).append(v.view_count)
    top_tags = [{"tag": k, "count": len(v), "avg_views": round(sum(v)/len(v), 2)} for k, v in sorted(tag_views.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True)[:15]]

    # Top channels
    ch_views: Dict[str, List[int]] = {}
    for v in videos:
        ch_views.setdefault(v.channel_title, []).append(v.view_count)
    top_channels = [{"channel": k, "video_count": len(v), "total_views": sum(v), "avg_views": round(sum(v)/len(v), 2)} for k, v in sorted(ch_views.items(), key=lambda x: sum(x[1]), reverse=True)[:10]]

    top_keywords = [kw for kw, _ in Counter(all_tags).most_common(20)]
    top_video_ids = [v.video_id for v in sorted(videos, key=lambda v: v.view_count, reverse=True)[:10]]

    return ShortsDetailedResult(
        keyword=keyword,
        total_videos=n,
        avg_views=round(total_views/n, 2),
        median_views=median,
        p25_views=p25,
        p75_views=p75,
        total_views=total_views,
        total_likes=total_likes,
        total_comments=total_comments,
        overall_engagement_rate=round((total_likes+total_comments)/max(total_views,1)*100, 2),
        duration_buckets=duration_buckets,
        hourly_stats=hourly_stats,
        views_quantiles=views_quantiles,
        title_length_breakdown=title_breakdown,
        top_tags_by_views=top_tags,
        top_channels=top_channels,
        top_keywords=top_keywords,
        top_video_ids=top_video_ids
    )

@app.get("/api/shorts/detailed", response_model=ShortsDetailedResult)
async def get_shorts_detailed(
    keyword: str = Query(..., description="Keyword to analyze"),
    max_results: int = Query(default=30, ge=1, le=50),
    time_range: str = Query(default="this_month")
):
    """Deep analysis of Shorts performance for a keyword."""
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")
    days = _TIME_MAP.get(time_range, 30)
    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "part": "snippet",
        "q": keyword + " #shorts",
        "maxResults": max_results,
        "order": "viewCount",
        "type": "video",
        "publishedAfter": published_after
    }
    try:
        data = await _fetch_search(YOUTUBE_API_KEY, params)
        items = data.get("items", [])
        video_ids = [item["id"]["videoId"] for item in items]
        if not video_ids:
            raise HTTPException(status_code=404, detail="No videos found")
        stats_data = await _fetch_video_with_duration(YOUTUBE_API_KEY, video_ids, "statistics,snippet,contentDetails")
        return await _build_detailed_result(keyword, items, stats_data)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Detailed shorts analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/shorts/channel-insights", response_model=ChannelInsightsResult)
async def get_channel_insights(
    channel_id: str = Query(..., description="YouTube Channel ID"),
    max_results: int = Query(default=30, ge=1, le=50),
    time_range: str = Query(default="past_year")
):
    """Analyze a channel's Shorts performance and patterns."""
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")
    days = _TIME_MAP.get(time_range, 365)
    published_after = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {
        "part": "snippet",
        "channelId": channel_id,
        "maxResults": max_results,
        "order": "date",
        "type": "video",
        "publishedAfter": published_after
    }
    try:
        data = await _fetch_search(YOUTUBE_API_KEY, params)
        items = data.get("items", [])
        video_ids = [item["id"]["videoId"] for item in items]
        if not video_ids:
            raise HTTPException(status_code=404, detail="No videos found for this channel")
        stats_data = await _fetch_video_with_duration(YOUTUBE_API_KEY, video_ids, "statistics,snippet,contentDetails")

        shorts = []
        all_tags = []
        total_views = total_likes = total_comments = 0
        durations = []
        views_by_date: Dict[str, int] = {}

        for item in stats_data.get("items", []):
            snippet = item["snippet"]
            stats = item.get("statistics", {})
            dur_str = item.get("contentDetails", {}).get("duration", "")
            if not _is_shorts_duration(dur_str):
                continue
            vid = item["id"]
            views = int(stats.get("viewCount", 0))
            likes = int(stats.get("likeCount", 0))
            comments = int(stats.get("commentCount", 0))
            pub = snippet.get("publishedAt", "")
            m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", dur_str)
            secs = int(m.group(1) or 0) * 3600 + int(m.group(2) or 0) * 60 + int(m.group(3) or 0) if m else 0

            shorts.append(ChannelInsightVideo(
                video_id=vid,
                title=snippet["title"],
                published_at=pub,
                view_count=views,
                like_count=likes,
                comment_count=comments,
                duration_sec=secs,
                thumbnail=snippet["thumbnails"]["high"]["url"] if snippet.get("thumbnails", {}).get("high") else ""
            ))
            total_views += views; total_likes += likes; total_comments += comments
            all_tags.extend(snippet.get("tags", []))
            durations.append(secs)
            if pub:
                day = pub[:10]
                views_by_date.setdefault(day, 0); views_by_date[day] += views

        if not shorts:
            raise HTTPException(status_code=404, detail="No Shorts found for this channel")

        n = len(shorts)
        # Upload frequency: videos per month
        dates = sorted(set(s.published_at[:10] for s in shorts if s.published_at))
        if len(dates) >= 2:
            span_days = (datetime.fromisoformat(dates[-1]) - datetime.fromisoformat(dates[0])).days or 1
            freq = round(n / max(span_days / 30, 1), 2)
        else:
            freq = round(n / 30, 2)

        # Top videos by views
        top_videos = sorted(shorts, key=lambda v: v.view_count, reverse=True)[:10]

        # Views over time (last 30 entries)
        sorted_dates = sorted(views_by_date.items())
        views_over_time = [{"date": d, "views": v} for d, v in sorted_dates[-30:]]

        # Duration histogram
        dur_hist = {"<15s": 0, "15-30s": 0, "30-45s": 0, "45-60s": 0}
        for d in durations:
            if d < 15: dur_hist["<15s"] += 1
            elif d < 30: dur_hist["15-30s"] += 1
            elif d < 45: dur_hist["30-45s"] += 1
            else: dur_hist["45-60s"] += 1

        top_tags = [kw for kw, _ in Counter(all_tags).most_common(15)]

        # Get channel title from first video's snippet
        channel_title = shorts[0].title.split(" - ")[0] if shorts else ""
        for item in stats_data.get("items", []):
            if item["id"] == top_videos[0].video_id if top_videos else False:
                channel_title = item["snippet"]["channelTitle"]
                break

        return ChannelInsightsResult(
            channel_id=channel_id,
            channel_title=channel_title,
            total_shorts=n,
            total_views=total_views,
            total_likes=total_likes,
            total_comments=total_comments,
            avg_views=round(total_views/n, 2),
            avg_engagement_rate=round((total_likes+total_comments)/max(total_views,1)*100, 2),
            upload_frequency_per_month=freq,
            top_videos=top_videos,
            views_over_time=views_over_time,
            duration_histogram=[{"label": k, "count": v} for k, v in dur_hist.items()],
            top_tags=top_tags
        )
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Channel insights error: {e}")
        raise HTTPException(status_code=500, detail=str(e))

# ============================================================
# Main
# ============================================================
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8001)
