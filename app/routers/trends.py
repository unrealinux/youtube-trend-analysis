"""Trends and search-related API routes."""
import logging
from typing import List

from fastapi import APIRouter, HTTPException, Query

from app.models import (
    TrendData, ShortsData, ComparisonResult, BatchScanResult,
    HotCategoryResult, FeatureAnalysisResult, ShortsDetailedResult,
    ChannelInsightsResult, TrendTrackingResult, ChannelSearchResult,
    DiscoveryResult,
)
from app.services import (
    search_trends_service, search_shorts_service, batch_scan_service,
    hot_categories_service, feature_analysis_service, detailed_shorts_service,
    channel_insights_service, compare_searches_service, trend_tracking_service,
    search_channels_service, channel_trends_service, discover_keywords_service,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["trends"])


@router.get("/trends/search", response_model=TrendData)
async def search_trends(
    keywords: str = Query(..., description="Comma-separated keywords"),
    max_results: int = Query(default=20, ge=1, le=50),
    order: str = Query(default="viewCount", pattern="relevance|date|viewCount|rating|velocity"),
    time_range: str = Query(default="past_year"),
):
    kw_list = [kw.strip() for kw in keywords.split(",") if kw.strip()]
    if not kw_list:
        raise HTTPException(status_code=400, detail="No keywords provided")
    try:
        result = await search_trends_service(kw_list, max_results, order, time_range)
        return result.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trends/shorts/search", response_model=ShortsData)
async def search_shorts(
    keywords: str = Query(..., description="Comma-separated keywords"),
    max_results: int = Query(default=20, ge=1, le=50),
    order: str = Query(default="viewCount", pattern="relevance|date|viewCount|rating|velocity"),
    time_range: str = Query(default="past_year"),
):
    kw_list = [kw.strip() for kw in keywords.split(",") if kw.strip()]
    if not kw_list:
        raise HTTPException(status_code=400, detail="No keywords provided")
    try:
        result = await search_shorts_service(kw_list, max_results, order, time_range)
        return result.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Shorts search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trends/channel", response_model=TrendData)
async def get_channel_trends(
    channel_id: str = Query(..., description="YouTube Channel ID"),
    max_results: int = Query(default=20, ge=1, le=50),
    order: str = Query(default="date", pattern="date|viewCount|rating|velocity"),
    time_range: str = Query(default="past_year"),
):
    if not channel_id or not channel_id.startswith("UC"):
        raise HTTPException(status_code=400, detail="Invalid channel ID format")
    try:
        result = await channel_trends_service(channel_id, max_results, order, time_range)
        return result.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Channel search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/compare", response_model=ComparisonResult)
async def compare_searches(req: dict):
    query1 = req.get("query1", "")
    query2 = req.get("query2", "")
    if not query1 or not query2:
        raise HTTPException(status_code=400, detail="Both queries are required")
    try:
        result = await compare_searches_service(query1, query2, req.get("time_range", "past_year"))
        return result.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Comparison error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trends/batch-scan", response_model=BatchScanResult)
async def batch_scan(
    keywords: str = Query(..., description="Comma-separated keywords to scan"),
    max_results: int = Query(default=20, ge=1, le=50),
    time_range: str = Query(default="this_month"),
):
    kw_list = [kw.strip() for kw in keywords.split(",") if kw.strip()]
    if not kw_list:
        raise HTTPException(status_code=400, detail="No keywords provided")
    try:
        result = await batch_scan_service(kw_list, max_results, time_range)
        return result.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Batch scan error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trends/hot-categories", response_model=List[HotCategoryResult])
async def get_hot_categories(
    time_range: str = Query(default="this_month"),
    max_results: int = Query(default=10, ge=1, le=30),
):
    try:
        return await hot_categories_service(time_range, max_results)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Hot categories error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trends/feature-analysis", response_model=FeatureAnalysisResult)
async def analyze_features(
    keyword: str = Query(..., description="Keyword to analyze"),
    max_results: int = Query(default=30, ge=1, le=50),
    time_range: str = Query(default="this_month"),
):
    if not keyword:
        raise HTTPException(status_code=400, detail="Keyword is required")
    try:
        result = await feature_analysis_service(keyword, max_results, time_range)
        return result.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Feature analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/shorts/detailed", response_model=ShortsDetailedResult)
async def get_shorts_detailed(
    keyword: str = Query(..., description="Keyword to analyze"),
    max_results: int = Query(default=30, ge=1, le=50),
    time_range: str = Query(default="this_month"),
):
    if not keyword:
        raise HTTPException(status_code=400, detail="Keyword is required")
    try:
        result = await detailed_shorts_service(keyword, max_results, time_range)
        return result.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Detailed shorts analysis error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/shorts/channel-insights", response_model=ChannelInsightsResult)
async def get_channel_insights(
    channel_id: str = Query(..., description="YouTube Channel ID"),
    max_results: int = Query(default=30, ge=1, le=50),
    time_range: str = Query(default="past_year"),
):
    if not channel_id or not channel_id.startswith("UC"):
        raise HTTPException(status_code=400, detail="Invalid channel ID format")
    try:
        result = await channel_insights_service(channel_id, max_results, time_range)
        return result.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Channel insights error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trends/trend-tracking", response_model=TrendTrackingResult)
async def get_trend_tracking(
    keyword: str = Query(..., description="Keyword to track"),
    limit: int = Query(default=30, ge=1, le=100),
    refresh: bool = Query(default=False, description="Force refresh from YouTube API"),
):
    if not keyword:
        raise HTTPException(status_code=400, detail="Keyword is required")
    try:
        result = await trend_tracking_service(keyword, limit, refresh)
        return result.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Trend tracking error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/trends/channels/search", response_model=ChannelSearchResult)
async def search_channels(
    q: str = Query(..., description="Channel name to search"),
    max_results: int = Query(default=10, ge=1, le=50),
):
    if not q:
        raise HTTPException(status_code=400, detail="Query is required")
    try:
        result = await search_channels_service(q, max_results)
        return result.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Channel search error: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/discover", response_model=DiscoveryResult)
async def discover_keywords(
    seed: str = Query(..., description="Seed keyword to mine related keywords from"),
    limit: int = Query(default=5, ge=1, le=10),
    scan: bool = Query(default=True, description="Verify each candidate with a full scan (100 quota units each)"),
    time_range: str = Query(default="this_month"),
):
    if not seed:
        raise HTTPException(status_code=400, detail="Seed keyword is required")
    try:
        result = await discover_keywords_service(seed, limit, scan, time_range)
        return result.model_dump()
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Discovery error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
