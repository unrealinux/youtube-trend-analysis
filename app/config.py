"""Configuration constants for YouTube Trend Analysis."""
import os

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
PROXY_URL = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("ALL_PROXY")
YOUTUBE_BASE_URL = "https://www.googleapis.com/youtube/v3"
REQUEST_TIMEOUT = 30.0
DATABASE_PATH = os.getenv("YT_HISTORY_DB", "youtube_history.db")
QUOTA_WARNING_THRESHOLD = 0.8  # 80% quota usage warning

# Browser origins allowed to call this API. The app serves its own frontend, so
# same-origin requests work regardless; this only matters for a separately
# hosted page. Defaults cover the ports the README and start scripts use.
# Override with a comma-separated CORS_ORIGINS (use * to allow any origin).
CORS_ORIGINS = [
    o.strip() for o in os.getenv(
        "CORS_ORIGINS",
        "http://localhost:8000,http://127.0.0.1:8000,"
        "http://localhost:8001,http://127.0.0.1:8001",
    ).split(",") if o.strip()
]

TIME_RANGES = {"today": 1, "this_week": 7, "this_month": 30, "past_year": 365}

# Ponytail: simple dict cache, replace with redis if concurrent requests become an issue
_cache: dict[str, tuple[float, dict]] = {}
_CACHE_TTL = 300  # 5 minutes
# A single hot-categories scan inserts ~2 entries per category (66 for the list
# below), so without a cap the dict grows for the process lifetime. Insertion
# order is the eviction order; expired entries are reclaimed first.
_CACHE_MAX_ENTRIES = 512

# Popular Shorts categories for trending analysis
HOT_CATEGORIES = [
    "搞笑", "美食", "健身", "科技", "游戏", "宠物", "旅行", "舞蹈",
    "美妆", "教育", "手工", "音乐", "运动", "汽车", "编程", "AI",
    "生活小窍门", "挑战", "反应视频", "ASMR", "Vlog", "开箱",
    "街拍", "美食制作", "特效", "魔术", "极限运动", "萌宠",
    "情侣日常", "亲子", "职场", "理财", "健康养生", "语言学习"
]
