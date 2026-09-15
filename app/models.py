from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any


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
    # views/day since publish: the only time-normalised signal, so a 2-day-old
    # hit can outrank a 30-day-old one with more cumulative views.
    views_per_day: float = 0.0


class TrendData(BaseModel):
    videos: List[VideoInfo]
    total_count: int
    avg_views: float
    avg_views_per_day: float = 0.0
    top_keywords: List[str]
    upload_frequency: float = 0.0


class ShortsData(BaseModel):
    videos: List[VideoInfo]
    total_count: int
    avg_views: float
    avg_views_per_day: float = 0.0
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
    views_per_day: float = 0.0
    recorded_at: str


class TrendChange(BaseModel):
    keyword: str
    direction: str  # "rising", "falling", "stable"
    change_pct: float
    metric: str = "views_per_day"  # which metric the values below are for
    current_value: float
    previous_value: float
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
    avg_views_per_day: float = 0.0
    engagement_rate: float = 0.0


class DiscoveryCandidate(BaseModel):
    keyword: str
    occurrences: int  # how often it co-occurred in the seed's tags/hashtags
    scanned: bool = False
    avg_views: float = 0.0
    avg_views_per_day: float = 0.0
    video_count: int = 0
    engagement_rate: float = 0.0


class DiscoveryResult(BaseModel):
    seed: str
    candidates: List[DiscoveryCandidate]
    scanned_at: str


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
    overall_engagement_rate: float = 0.0
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
    views_per_day: float = 0.0


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


class ComparisonResult(BaseModel):
    query1: str
    query2: str
    stats1: Dict[str, Any]
    stats2: Dict[str, Any]
    comparison: Dict[str, Any]
