"""Business logic and YouTube API service layer."""
import asyncio
import re
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from collections import Counter

import httpx
from fastapi import HTTPException

from app import config
from app.models import (
    VideoInfo, TrendData, ShortsData, FeatureAnalysisResult,
    TrendTrackingResult, TrendSnapshot, TrendChange, BatchScanResult,
    KeywordResult, HotCategoryResult, ShortsDetailedResult,
    DurationBucket, HourlyStats, ViewsQuantile, TitleLengthBreakdown,
    ChannelInsightsResult, ChannelInsightVideo,
)
from app.database import (
    get_trend_snapshots, get_today_quota_used, get_keywords_due_for_snapshot,
    DAILY_QUOTA_LIMIT,
    save_search_history_async, save_trend_snapshot_async, record_quota_usage_async,
)

logger = logging.getLogger(__name__)

# YouTube Data API v3 quota cost per call variant. search.list costs 100 units,
# videos.list costs 1. See https://developers.google.com/youtube/v3/determine_quota_cost
QUOTA_COST_SEARCH = 100
QUOTA_COST_VIDEOS = 1

# Transient YouTube failures (connection resets, 5xx) are retried; 4xx are not.
_RETRY_ATTEMPTS = 3
_RETRY_BASE_DELAY = 0.5  # seconds, doubles each attempt

# ponytail: YouTube lowered the Shorts ceiling from 60s to 180s in Oct 2024.
# The old 60s gate silently dropped every 1-3 minute Short, so this is a
# correctness fix, not a tuning knob.
SHORTS_MAX_DURATION_SEC = 180


# ---- Cache helpers ----

def _cache_time() -> float:
    # monotonic: immune to wall-clock jumps (NTP adjustments, manual changes)
    return time.monotonic()


def _get_cached(key: str) -> Optional[dict]:
    entry = config._cache.get(key)
    if entry is not None:
        ts, data = entry
        if _cache_time() - ts < config._CACHE_TTL:
            logger.info(f"Cache hit for {key}")
            return data
        del config._cache[key]
    logger.info(f"Cache miss for {key}")
    return None


def _set_cached(key: str, data: dict) -> None:
    if len(config._cache) >= config._CACHE_MAX_ENTRIES:
        # reclaim expired entries first, then drop oldest by insertion order
        now = _cache_time()
        for k in [k for k, (ts, _) in config._cache.items()
                  if now - ts >= config._CACHE_TTL]:
            del config._cache[k]
        while len(config._cache) >= config._CACHE_MAX_ENTRIES:
            del config._cache[next(iter(config._cache))]
    config._cache[key] = (_cache_time(), data)


# ---- YouTube API client ----

async def fetch_json(client: httpx.AsyncClient, url: str, params: dict) -> dict:
    """GET a YouTube endpoint, retrying transient failures with backoff.

    400/403/404 map straight to an HTTPException — retrying a malformed request,
    an exhausted quota, or a missing resource only wastes time. Connection
    errors and 5xx are retried.
    """
    last_error: Optional[Exception] = None
    for attempt in range(_RETRY_ATTEMPTS):
        try:
            resp = await client.get(url, params=params, timeout=config.REQUEST_TIMEOUT)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            status = e.response.status_code
            logger.error(f"HTTP error: {status} - {e.response.text[:200]}")
            if status == 400:
                raise HTTPException(status_code=400, detail="Invalid YouTube API request")
            if status == 403:
                raise HTTPException(status_code=403, detail="YouTube API quota exceeded")
            if status == 404:
                raise HTTPException(status_code=404, detail="Resource not found")
            last_error = e
            if status < 500:
                raise HTTPException(status_code=502, detail=f"API error: {status}")
        except httpx.HTTPError as e:
            logger.warning(f"Transient YouTube API error (attempt {attempt + 1}): {e}")
            last_error = e
        if attempt < _RETRY_ATTEMPTS - 1:
            await asyncio.sleep(_RETRY_BASE_DELAY * (2 ** attempt))
    if isinstance(last_error, httpx.TimeoutException):
        raise HTTPException(status_code=504, detail="YouTube API request timeout")
    raise HTTPException(status_code=502, detail=f"HTTP error: {last_error}")


async def _fetch_search(api_key: str, params: dict) -> dict:
    cache_key = f"search:{hash((api_key, str(params)))}"
    cached = _get_cached(cache_key)
    if cached:
        return cached
    client_kwargs = {"timeout": config.REQUEST_TIMEOUT}
    if config.PROXY_URL:
        client_kwargs["proxy"] = config.PROXY_URL
    async with httpx.AsyncClient(**client_kwargs) as client:
        data = await fetch_json(client, f"{config.YOUTUBE_BASE_URL}/search", {**params, "key": api_key})
        await record_quota_usage_async(QUOTA_COST_SEARCH)
        _set_cached(cache_key, data)
        return data


async def _fetch_videos(api_key: str, video_ids: list, parts: str) -> dict:
    cache_key = f"videos:{hash((api_key, str(sorted(video_ids)), parts))}"
    cached = _get_cached(cache_key)
    if cached:
        return cached
    client_kwargs = {"timeout": config.REQUEST_TIMEOUT}
    if config.PROXY_URL:
        client_kwargs["proxy"] = config.PROXY_URL
    async with httpx.AsyncClient(**client_kwargs) as client:
        data = await fetch_json(client, f"{config.YOUTUBE_BASE_URL}/videos", {
            "part": parts,
            "id": ",".join(video_ids),
            "key": api_key
        })
        await record_quota_usage_async(QUOTA_COST_VIDEOS)
        _set_cached(cache_key, data)
        return data


async def _fetch_video_stats(api_key: str, video_ids: list) -> dict:
    """statistics+snippet lookup (no contentDetails)."""
    return await _fetch_videos(api_key, video_ids, "statistics,snippet")


async def _fetch_video_with_duration(api_key: str, video_ids: list) -> dict:
    """statistics+snippet+contentDetails lookup."""
    return await _fetch_videos(api_key, video_ids, "statistics,snippet,contentDetails")


async def _fetch_channels(api_key: str, channel_ids: list) -> dict:
    """snippet+statistics lookup (1 quota unit regardless of id count)."""
    cache_key = f"channels:{hash((api_key, str(sorted(channel_ids))))}"
    cached = _get_cached(cache_key)
    if cached:
        return cached
    client_kwargs = {"timeout": config.REQUEST_TIMEOUT}
    if config.PROXY_URL:
        client_kwargs["proxy"] = config.PROXY_URL
    async with httpx.AsyncClient(**client_kwargs) as client:
        data = await fetch_json(client, f"{config.YOUTUBE_BASE_URL}/channels", {
            "part": "snippet,statistics",
            "id": ",".join(channel_ids),
            "key": api_key,
        })
        await record_quota_usage_async(QUOTA_COST_VIDEOS)
        _set_cached(cache_key, data)
        return data


# ---- Helpers ----

def _resolve_days(time_range: str) -> int:
    """Map a time_range label to a day count.

    Unknown values used to silently fall back to 365 days (30 in some
    endpoints), so the UI could show "过去一年" for a typo'd range and the
    caller had no way to notice. Validate at the boundary instead.
    """
    if time_range not in config.TIME_RANGES:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid time_range '{time_range}'. Allowed: "
                   f"{', '.join(sorted(config.TIME_RANGES))}",
        )
    return config.TIME_RANGES[time_range]

def _parse_duration_seconds(duration: str) -> int:
    """ISO-8601 duration (PT1M30S) -> seconds. 0 for anything unparseable."""
    match = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", duration)
    if not match:
        return 0
    h, m, s = match.groups()
    return int(h or 0) * 3600 + int(m or 0) * 60 + int(s or 0)


def _is_shorts_duration(duration: str) -> bool:
    return 0 < _parse_duration_seconds(duration) <= SHORTS_MAX_DURATION_SEC


def analyze_posting_hours(videos: List[VideoInfo]) -> List[str]:
    hour_stats: Dict[int, Dict[str, int]] = {}
    for v in videos:
        if not v.published_at:
            continue
        try:
            dt = datetime.fromisoformat(v.published_at.replace("Z", "+00:00"))
            # YouTube timestamps are UTC; report hours in the viewer's local zone.
            h = dt.astimezone().hour
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


def _make_video_info(item: dict) -> VideoInfo:
    snippet = item["snippet"]
    stats = item.get("statistics", {})
    return VideoInfo(
        video_id=item["id"],
        title=snippet["title"],
        channel_title=snippet["channelTitle"],
        published_at=snippet.get("publishedAt", ""),
        view_count=int(stats.get("viewCount", 0)),
        like_count=int(stats.get("likeCount", 0)),
        comment_count=int(stats.get("commentCount", 0)),
        description=snippet.get("description", "")[:500],
        tags=snippet.get("tags", []),
        thumbnail=snippet["thumbnails"]["high"]["url"] if snippet.get("thumbnails", {}).get("high") else "",
        url=f"https://www.youtube.com/watch?v={item['id']}"
    )


def _published_after(days: int) -> str:
    """Cutoff for `publishedAfter`, bucketed to the start of the UTC day.

    The cutoff is part of the cache key, so including a live timestamp made the
    key change every second and the 5-minute TTL cache a near-total miss for all
    search-based endpoints (each miss costs 100 quota units). Day bucketing
    keeps the cache effective while only widening the window by <24h, which is
    immaterial next to the 7-365 day ranges it is used with.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    return cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _collect_shorts(
    api_key: str,
    query: str,
    max_results: int,
    days: int,
    order: str = "viewCount",
) -> tuple[List[VideoInfo], List[str], List[int]]:
    """Search + fetch + duration-filter in one place.

    Every Shorts-flavoured endpoint used to inline this same
    search -> videos.list -> `_is_shorts_duration` -> tag-count sequence.
    Returns (shorts, tags, durations) with the caller left to compute its own stats.
    """
    params = {
        "part": "snippet",
        "q": query,
        "maxResults": max_results,
        "order": order,
        "type": "video",
        "publishedAfter": _published_after(days),
    }
    data = await _fetch_search(api_key, params)
    video_ids = [item["id"]["videoId"] for item in data.get("items", [])]
    if not video_ids:
        return [], [], []

    stats_data = await _fetch_video_with_duration(api_key, video_ids)

    videos: List[VideoInfo] = []
    tags: List[str] = []
    durations: List[int] = []
    for item in stats_data.get("items", []):
        duration = item.get("contentDetails", {}).get("duration", "")
        if not _is_shorts_duration(duration):
            continue
        vid = _make_video_info(item)
        videos.append(vid)
        tags.extend(vid.tags)
        durations.append(_parse_duration_seconds(duration))
    return videos, tags, durations


# ---- Core search logic ----

async def fetch_trending_videos(api_key: str, params: dict) -> TrendData:
    # fetch_json already maps timeouts to a 504 HTTPException, so the old
    # httpx.ConnectTimeout guards here were unreachable.
    search_data = await _fetch_search(api_key, params)

    video_ids = [item["id"]["videoId"] for item in search_data.get("items", [])]
    if not video_ids:
        return TrendData(videos=[], total_count=0, avg_views=0, top_keywords=[], upload_frequency=0)

    stats_data = await _fetch_video_stats(api_key, video_ids)

    videos: List[VideoInfo] = []
    all_tags: List[str] = []
    total_views = 0
    published_dates: List[str] = []

    for item in stats_data.get("items", []):
        pub_date = item.get("snippet", {}).get("publishedAt", "")
        if pub_date:
            published_dates.append(pub_date)
        vid = _make_video_info(item)
        videos.append(vid)
        total_views += vid.view_count
        all_tags.extend(vid.tags)

    top_keywords = [kw for kw, _ in Counter(all_tags).most_common(20)]
    avg_views = total_views / len(videos) if videos else 0

    # ponytail: naive density estimate. Dividing by a 1-day span turns a single
    # publish day into "600 uploads/month", so the span is floored at a week —
    # below that the sample is too short to extrapolate a monthly rate from.
    upload_frequency = 0.0
    if len(published_dates) >= 2:
        try:
            dates = sorted([datetime.fromisoformat(d.replace("Z", "+00:00")) for d in published_dates])
            span_days = max((dates[-1] - dates[0]).days, 7)
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


# ---- Endpoint service functions ----

async def search_trends_service(keywords: List[str], max_results: int, order: str, time_range: str) -> TrendData:
    days = _resolve_days(time_range)
    if not config.YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")
    params = {
        "part": "snippet",
        "q": " ".join(keywords),
        "maxResults": max_results,
        "order": order,
        "type": "video",
        "publishedAfter": _published_after(days)
    }
    result = await fetch_trending_videos(config.YOUTUBE_API_KEY, params)
    await save_search_history_async(keywords, result.total_count)
    logger.info(f"Search completed: {keywords} -> {result.total_count} results")
    return result


async def channel_trends_service(channel_id: str, max_results: int, order: str, time_range: str) -> TrendData:
    """Trend stats for the videos published by one channel."""
    days = _resolve_days(time_range)
    if not config.YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")
    params = {
        "part": "snippet",
        "channelId": channel_id,
        "maxResults": max_results,
        "order": order,
        "type": "video",
        "publishedAfter": _published_after(days)
    }
    result = await fetch_trending_videos(config.YOUTUBE_API_KEY, params)
    logger.info(f"Channel search: {channel_id} -> {result.total_count} results")
    return result


async def search_shorts_service(keywords: List[str], max_results: int, order: str, time_range: str) -> ShortsData:
    days = _resolve_days(time_range)
    if not config.YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")
    videos, all_tags, _ = await _collect_shorts(
        config.YOUTUBE_API_KEY, " ".join(keywords) + " #shorts", max_results, days, order
    )
    if not videos:
        return ShortsData(videos=[], total_count=0, avg_views=0, top_keywords=[])

    total_views = sum(v.view_count for v in videos)
    top_keywords = [kw for kw, _ in Counter(all_tags).most_common(20)]
    await save_search_history_async(keywords, len(videos))
    return ShortsData(
        videos=videos, total_count=len(videos),
        avg_views=round(total_views / len(videos), 2), top_keywords=top_keywords
    )


async def batch_scan_service(keywords: List[str], max_results: int, time_range: str) -> BatchScanResult:
    days = _resolve_days(time_range)
    if not config.YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")

    sem = asyncio.Semaphore(config.MAX_CONCURRENT_SCANS)

    async def scan_keyword(keyword: str) -> Optional[KeywordResult]:
        try:
            async with sem:
                vids, all_tags, _ = await _collect_shorts(
                    config.YOUTUBE_API_KEY, keyword + " #shorts", max_results, days
                )
            if not vids:
                return None
            total_views = sum(v.view_count for v in vids)
            total_likes = sum(v.like_count for v in vids)
            total_comments = sum(v.comment_count for v in vids)
            engagement_rate = (total_likes + total_comments) / max(total_views, 1) * 100
            return KeywordResult(
                keyword=keyword,
                avg_views=round(total_views / len(vids), 2),
                total_views=total_views,
                video_count=len(vids),
                top_keywords=[kw for kw, _ in Counter(all_tags).most_common(10)],
                engagement_rate=round(engagement_rate, 2)
            )
        except Exception as e:
            logger.error(f"Error scanning keyword '{keyword}': {e}")
            return None

    scanned = await asyncio.gather(*(scan_keyword(kw) for kw in keywords))
    results = [r for r in scanned if r is not None]
    results.sort(key=lambda x: x.avg_views, reverse=True)
    return BatchScanResult(keywords=results, sorted_by="avg_views", scanned_at=datetime.now(timezone.utc).isoformat())


async def hot_categories_service(time_range: str, max_results: int) -> List[HotCategoryResult]:
    days = _resolve_days(time_range)
    if not config.YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")

    sem = asyncio.Semaphore(config.MAX_CONCURRENT_SCANS)

    async def scan_category(category: str) -> Optional[HotCategoryResult]:
        try:
            async with sem:
                vids, all_tags, _ = await _collect_shorts(
                    config.YOUTUBE_API_KEY, category + " #shorts", max_results, days
                )
            if not vids:
                return None
            total_views = sum(v.view_count for v in vids)
            top_views = max(v.view_count for v in vids)
            total_likes = sum(v.like_count for v in vids)
            total_comments = sum(v.comment_count for v in vids)
            engagement_rate = (total_likes + total_comments) / max(total_views, 1) * 100
            return HotCategoryResult(
                category=category,
                avg_views=round(total_views / len(vids), 2),
                total_views=total_views,
                video_count=len(vids),
                engagement_rate=round(engagement_rate, 2),
                top_video_views=top_views,
                top_keywords=[kw for kw, _ in Counter(all_tags).most_common(8)]
            )
        except Exception as e:
            logger.error(f"Error scanning category '{category}': {e}")
            return None

    scanned = await asyncio.gather(*(scan_category(cat) for cat in config.HOT_CATEGORIES))
    results = [r for r in scanned if r is not None]
    results.sort(key=lambda x: x.avg_views, reverse=True)
    return results


async def feature_analysis_service(keyword: str, max_results: int, time_range: str) -> FeatureAnalysisResult:
    days = _resolve_days(time_range)
    if not config.YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")
    videos, all_tags, durations = await _collect_shorts(
        config.YOUTUBE_API_KEY, keyword + " #shorts", max_results, days
    )
    if not videos:
        raise HTTPException(status_code=404, detail="No Shorts found")

    all_titles = [v.title for v in videos]
    total_views = sum(v.view_count for v in videos)

    # Title patterns
    title_patterns: List[str] = []
    avg_title_len = sum(len(t) for t in all_titles) / len(all_titles) if all_titles else 0
    if avg_title_len > 30:
        title_patterns.append(f"avg title length {avg_title_len:.0f} chars")

    durations.sort(reverse=True)

    # Engagement by tag
    tag_engagement: Dict[str, List[int]] = {}
    for v in videos:
        total_engagement = v.like_count + v.comment_count
        for tag in v.tags:
            tag_engagement.setdefault(tag, []).append(v.view_count)
    top_engagement_keywords = [kw for kw, _ in sorted(
        ((tag, sum(views)/len(views)) for tag, views in tag_engagement.items()),
        key=lambda x: x[1], reverse=True
    )][:8]

    # Success score
    avg_views = total_views / len(videos)
    engagement_rate = sum(v.like_count + v.comment_count for v in videos) / max(total_views, 1) * 100
    views_score = min(avg_views / 1000000, 100) * 0.6
    engagement_score = min(engagement_rate * 5, 100) * 0.4
    success_score = round(views_score + engagement_score, 2)

    # Duration range
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


async def detailed_shorts_service(keyword: str, max_results: int, time_range: str) -> ShortsDetailedResult:
    days = _resolve_days(time_range)
    if not config.YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")
    videos, all_tags, durations = await _collect_shorts(
        config.YOUTUBE_API_KEY, keyword + " #shorts", max_results, days
    )
    if not videos:
        raise HTTPException(status_code=404, detail="No videos found")
    return _build_detailed_result(keyword, videos, all_tags, durations)


def _build_detailed_result(
    keyword: str, videos: List[VideoInfo], all_tags: List[str], durations: List[int]
) -> ShortsDetailedResult:
    if not videos:
        return ShortsDetailedResult(keyword=keyword, total_videos=0)

    total_views = sum(v.view_count for v in videos)
    total_likes = sum(v.like_count for v in videos)
    total_comments = sum(v.comment_count for v in videos)

    n = len(videos)
    view_counts = sorted([v.view_count for v in videos])
    # nearest-rank percentile: index int(n*p)-1, clamped (the old int(n*p)
    # formula was off by one rank for every n).
    p25 = view_counts[max(0, min(n - 1, int(n * 0.25) - 1))] if n >= 4 else view_counts[0]
    p75 = view_counts[max(0, min(n - 1, int(n * 0.75) - 1))] if n >= 4 else view_counts[-1]
    median = view_counts[n // 2]

    # Duration buckets (durations came back alongside videos, so no O(n^2) lookup)
    dur_buckets: Dict[str, List[VideoInfo]] = {"0-15s": [], "15-30s": [], "30-45s": [], "45-60s": []}
    for v, secs in zip(videos, durations):
        if secs < 15:
            b = "0-15s"
        elif secs < 30:
            b = "15-30s"
        elif secs < 45:
            b = "30-45s"
        else:
            b = "45-60s"
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
        if not v.published_at:
            continue
        try:
            dt = datetime.fromisoformat(v.published_at.replace("Z", "+00:00"))
            # YouTube timestamps are UTC; report hours in the viewer's local zone.
            h = dt.astimezone().hour
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
    top_tags = [{"tag": k, "count": len(v), "avg_views": round(sum(v)/len(v), 2)}
                for k, v in sorted(tag_views.items(), key=lambda x: sum(x[1])/len(x[1]), reverse=True)[:15]]

    # Top channels
    ch_views: Dict[str, List[int]] = {}
    for v in videos:
        ch_views.setdefault(v.channel_title, []).append(v.view_count)
    top_channels = [{"channel": k, "video_count": len(v), "total_views": sum(v), "avg_views": round(sum(v)/len(v), 2)}
                    for k, v in sorted(ch_views.items(), key=lambda x: sum(x[1]), reverse=True)[:10]]

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


async def channel_insights_service(channel_id: str, max_results: int, time_range: str) -> ChannelInsightsResult:
    days = _resolve_days(time_range)
    if not config.YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")
    params = {
        "part": "snippet",
        "channelId": channel_id,
        "maxResults": max_results,
        "order": "date",
        "type": "video",
        "publishedAfter": _published_after(days)
    }
    data = await _fetch_search(config.YOUTUBE_API_KEY, params)
    video_ids = [item["id"]["videoId"] for item in data.get("items", [])]
    if not video_ids:
        raise HTTPException(status_code=404, detail="No videos found for this channel")
    stats_data = await _fetch_video_with_duration(config.YOUTUBE_API_KEY, video_ids)

    shorts: List[ChannelInsightVideo] = []
    all_tags: List[str] = []
    total_views = total_likes = total_comments = 0
    durations: List[int] = []
    views_by_date: Dict[str, int] = {}
    channel_title = ""

    for item in stats_data.get("items", []):
        snippet = item["snippet"]
        stats = item.get("statistics", {})
        dur_str = item.get("contentDetails", {}).get("duration", "")
        if not _is_shorts_duration(dur_str):
            continue
        channel_title = channel_title or snippet.get("channelTitle", "")
        views = int(stats.get("viewCount", 0))
        likes = int(stats.get("likeCount", 0))
        comments = int(stats.get("commentCount", 0))
        pub = snippet.get("publishedAt", "")
        secs = _parse_duration_seconds(dur_str)
        shorts.append(ChannelInsightVideo(
            video_id=item["id"], title=snippet["title"], published_at=pub,
            view_count=views, like_count=likes, comment_count=comments,
            duration_sec=secs,
            thumbnail=snippet["thumbnails"]["high"]["url"] if snippet.get("thumbnails", {}).get("high") else ""
        ))
        total_views += views
        total_likes += likes
        total_comments += comments
        all_tags.extend(snippet.get("tags", []))
        durations.append(secs)
        if pub:
            day = pub[:10]
            views_by_date[day] = views_by_date.get(day, 0) + views

    if not shorts:
        raise HTTPException(status_code=404, detail="No Shorts found for this channel")

    n = len(shorts)
    dates = sorted(set(s.published_at[:10] for s in shorts if s.published_at))
    if len(dates) >= 2:
        span_days = (datetime.fromisoformat(dates[-1]) - datetime.fromisoformat(dates[0])).days or 1
        freq = round(n / max(span_days / 30, 1), 2)
    else:
        freq = round(n / 30, 2)

    top_videos = sorted(shorts, key=lambda v: v.view_count, reverse=True)[:10]
    views_over_time = [{"date": d, "views": v} for d, v in sorted(views_by_date.items())[-30:]]

    dur_hist = {"<15s": 0, "15-30s": 0, "30-45s": 0, "45-60s": 0}
    for d in durations:
        if d < 15:
            dur_hist["<15s"] += 1
        elif d < 30:
            dur_hist["15-30s"] += 1
        elif d < 45:
            dur_hist["30-45s"] += 1
        else:
            dur_hist["45-60s"] += 1

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
        top_tags=[kw for kw, _ in Counter(all_tags).most_common(15)]
    )


async def compare_searches_service(query1: str, query2: str, time_range: str = "past_year"):
    if not config.YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")
    from app.models import ComparisonResult
    days = _resolve_days(time_range)
    keywords1 = [kw.strip() for kw in query1.split(",") if kw.strip()]
    keywords2 = [kw.strip() for kw in query2.split(",") if kw.strip()]
    params1 = {
        "part": "snippet", "q": " ".join(keywords1), "maxResults": 20,
        "order": "viewCount", "type": "video",
        "publishedAfter": _published_after(days)
    }
    params2 = {
        "part": "snippet", "q": " ".join(keywords2), "maxResults": 20,
        "order": "viewCount", "type": "video",
        "publishedAfter": _published_after(days)
    }
    result1 = await fetch_trending_videos(config.YOUTUBE_API_KEY, params1)
    result2 = await fetch_trending_videos(config.YOUTUBE_API_KEY, params2)
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
        query1=query1, query2=query2,
        stats1={"total_count": result1.total_count, "avg_views": result1.avg_views, "top_keywords": result1.top_keywords},
        stats2={"total_count": result2.total_count, "avg_views": result2.avg_views, "top_keywords": result2.top_keywords},
        comparison=comparison
    )


async def _snapshot_shorts_keyword(keyword: str) -> None:
    """Scan a keyword's Shorts once and append a trend_tracking snapshot.

    Snapshotting every refresh is the point of trend tracking: the growth curve
    is the difference between successive snapshots.
    """
    if not config.YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")
    vids, _, _ = await _collect_shorts(config.YOUTUBE_API_KEY, keyword + " #shorts", 20, 30)
    if not vids:
        raise HTTPException(status_code=404, detail="No Shorts found")
    total_views = sum(v.view_count for v in vids)
    total_likes = sum(v.like_count for v in vids)
    total_comments = sum(v.comment_count for v in vids)
    engagement_rate = (total_likes + total_comments) / max(total_views, 1) * 100
    await save_trend_snapshot_async(
        keyword, round(total_views / len(vids), 2), len(vids), round(engagement_rate, 2)
    )


async def snapshot_due_keywords() -> int:
    """Append one snapshot per tracked keyword whose newest is stale.

    Only keywords already in `trend_tracking` are touched, so this never starts
    spending quota on a keyword the user has not tracked; it just keeps existing
    trend lines alive. Returns the number refreshed.
    """
    if not config.YOUTUBE_API_KEY or config.TREND_SNAPSHOT_INTERVAL_HOURS <= 0:
        return 0
    if get_today_quota_used() >= DAILY_QUOTA_LIMIT * config.QUOTA_WARNING_THRESHOLD:
        logger.warning("Trend auto-snapshot skipped: quota usage above threshold")
        return 0
    cutoff = (
        datetime.now(timezone.utc) - timedelta(hours=config.TREND_SNAPSHOT_INTERVAL_HOURS)
    ).strftime("%Y-%m-%d %H:%M:%S")
    keywords = get_keywords_due_for_snapshot(cutoff, config.TREND_SNAPSHOT_MAX_KEYWORDS)
    refreshed = 0
    for keyword in keywords:
        try:
            await _snapshot_shorts_keyword(keyword)
            refreshed += 1
        except Exception as e:
            logger.error(f"Trend auto-snapshot failed for '{keyword}': {e}")
    if refreshed:
        logger.info(f"Trend auto-snapshot refreshed {refreshed} keyword(s)")
    return refreshed


async def trend_snapshot_loop() -> None:
    """Background loop started by the app lifespan (no-op when disabled)."""
    interval_hours = config.TREND_SNAPSHOT_INTERVAL_HOURS
    if interval_hours <= 0:
        return
    while True:
        try:
            await snapshot_due_keywords()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"Trend snapshot loop error: {e}")
        await asyncio.sleep(interval_hours * 3600)


async def trend_tracking_service(keyword: str, limit: int, refresh: bool = False) -> TrendTrackingResult:
    # refresh=True always re-scans and appends a snapshot; without an explicit
    # refresh the stored history is served as-is.
    if refresh:
        await _snapshot_shorts_keyword(keyword)

    snapshots = get_trend_snapshots(keyword, limit)
    # get_trend_snapshots returns newest-first (ordered by recorded_at then id,
    # so same-second snapshots still have a defined order). Reverse into
    # chronological order so [-2]/[-1] are previous/current.
    sorted_snapshots = list(reversed(snapshots))
    trend_direction = "stable"
    latest_change = None
    if len(sorted_snapshots) >= 2:
        previous = sorted_snapshots[-2]
        current = sorted_snapshots[-1]
        change_pct = ((current.avg_views - previous.avg_views) / max(previous.avg_views, 1)) * 100
        if change_pct > 10:
            trend_direction = "rising"
            latest_change = TrendChange(keyword=keyword, direction="rising", change_pct=round(change_pct, 2),
                                         current_avg_views=current.avg_views, previous_avg_views=previous.avg_views,
                                         change_timestamp=current.recorded_at)
        elif change_pct < -10:
            trend_direction = "falling"
            latest_change = TrendChange(keyword=keyword, direction="falling", change_pct=round(change_pct, 2),
                                         current_avg_views=current.avg_views, previous_avg_views=previous.avg_views,
                                         change_timestamp=current.recorded_at)
    return TrendTrackingResult(keyword=keyword, snapshots=snapshots, latest_change=latest_change, trend_direction=trend_direction)


async def search_channels_service(q: str, max_results: int):
    if not config.YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")
    from app.models import ChannelInfo, ChannelSearchResult
    params = {"part": "snippet", "q": q, "type": "channel", "maxResults": max_results}
    data = await _fetch_search(config.YOUTUBE_API_KEY, params)
    channels = []
    for item in data.get("items", []):
        snippet = item.get("snippet", {})
        channel_id = item.get("id", {}).get("channelId", "")
        if not channel_id:
            continue
        channels.append(ChannelInfo(
            channel_id=channel_id,
            title=snippet.get("title", ""),
            description=snippet.get("description", ""),
            thumbnail=snippet.get("thumbnails", {}).get("high", {}).get("url", "")
        ))
    # search.list returns no subscriber/video counts; enrich via channels.list
    # (1 extra quota unit) instead of leaving the model fields hardcoded to 0.
    if channels:
        stats_data = await _fetch_channels(
            config.YOUTUBE_API_KEY, [c.channel_id for c in channels]
        )
        by_id = {item["id"]: item.get("statistics", {}) for item in stats_data.get("items", [])}
        for c in channels:
            stats = by_id.get(c.channel_id, {})
            c.subscriber_count = int(stats.get("subscriberCount", 0) or 0)
            c.video_count = int(stats.get("videoCount", 0) or 0)
    return ChannelSearchResult(channels=channels, total_count=len(channels))
