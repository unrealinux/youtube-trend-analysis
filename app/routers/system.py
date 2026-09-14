"""System-level API routes: health, quota, cache, history."""
import logging

from typing import List

from fastapi import APIRouter, HTTPException

from app.config import YOUTUBE_API_KEY, QUOTA_WARNING_THRESHOLD, DATABASE_PATH
from app.database import (
    get_recent_searches, clear_search_history,
    get_today_quota_used, DAILY_QUOTA_LIMIT,
)
from app.models import QuotaStatus, SearchHistoryEntry

logger = logging.getLogger(__name__)
router = APIRouter(tags=["system"])


@router.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "api_key_configured": bool(YOUTUBE_API_KEY),
        "database": DATABASE_PATH,
    }


@router.get("/api/quota", response_model=QuotaStatus)
async def get_quota():
    if not YOUTUBE_API_KEY:
        raise HTTPException(status_code=503, detail="YouTube API Key not configured")
    # Reported from the units actually spent by this app today, not from a
    # probe request — the old version hardcoded used=1 and burned quota to lie.
    used = get_today_quota_used()
    remaining = max(0, DAILY_QUOTA_LIMIT - used)
    usage_percent = min(100.0, used / DAILY_QUOTA_LIMIT * 100)
    warning = usage_percent >= QUOTA_WARNING_THRESHOLD * 100
    if warning:
        logger.warning(f"High quota usage: {usage_percent:.1f}%")
    return QuotaStatus(
        used=used, remaining=remaining, limit=DAILY_QUOTA_LIMIT,
        usage_percent=round(usage_percent, 2), warning=warning
    )


@router.get("/api/history", response_model=List[SearchHistoryEntry])
async def get_history(limit: int = 10) -> list:
    return get_recent_searches(limit)


@router.delete("/api/history")
async def delete_history():
    count = clear_search_history()
    logger.info(f"Cleared {count} history entries")
    return {"deleted": count}


@router.get("/api/cache/stats")
async def cache_stats() -> dict:
    from app.config import _cache, _CACHE_TTL
    return {"size": len(_cache), "ttl": _CACHE_TTL}


@router.post("/api/cache/clear")
async def clear_cache() -> dict:
    from app.config import _cache
    _cache.clear()
    logger.info("Cache cleared")
    return {"cleared": True}
