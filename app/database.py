"""Database operations for YouTube Trend Analysis."""
import json
import logging
import os
from typing import List

import sqlite3

from fastapi.concurrency import run_in_threadpool

from app.models import SearchHistoryEntry, TrendSnapshot

DATABASE_PATH = os.getenv("YT_HISTORY_DB", "youtube_history.db")
DAILY_QUOTA_LIMIT = 10000

logger = logging.getLogger(__name__)


def get_db() -> sqlite3.Connection:
    # YT_HISTORY_DB may point into a directory that does not exist yet; sqlite3
    # will not create intermediate directories and raises "unable to open
    # database file". init_db() runs at import time, so that would otherwise
    # take the whole process down instead of just failing a request.
    parent = os.path.dirname(os.path.abspath(DATABASE_PATH))
    os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
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
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM search_history")
    count = cursor.rowcount
    conn.commit()
    conn.close()
    return count


def save_trend_snapshot(keyword: str, avg_views: float, total_videos: int, engagement_rate: float = 0.0) -> None:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO trend_tracking (keyword, avg_views, total_videos, engagement_rate) VALUES (?, ?, ?, ?)",
        (keyword, avg_views, total_videos, engagement_rate)
    )
    conn.commit()
    conn.close()


def get_trend_snapshots(keyword: str, limit: int = 30) -> List[TrendSnapshot]:
    # `recorded_at` has one-second granularity, so two refreshes can share a
    # timestamp; `id DESC` breaks that tie by insertion order. Without it the
    # newest/previous pair is picked arbitrarily and the trend direction can
    # invert (a 5x rise reported as falling).
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT keyword, avg_views, total_videos, engagement_rate, recorded_at FROM trend_tracking WHERE keyword = ? ORDER BY recorded_at DESC, id DESC LIMIT ?",
        (keyword, limit)
    )
    rows = cursor.fetchall()
    conn.close()
    return [TrendSnapshot(**dict(row)) for row in rows]


def record_quota_usage(units: int) -> None:
    """Accumulate today's YouTube API quota spend.

    The api_quota table existed but nothing ever wrote to it, so /api/quota
    had nothing real to report. Every actual (non-cached) API call now lands here.

    Bookkeeping must never break the request it is accounting for, so a DB
    problem here is logged and swallowed rather than raised.
    """
    try:
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM api_quota WHERE date = CURRENT_DATE ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        if row:
            cursor.execute("UPDATE api_quota SET used = used + ? WHERE id = ?", (units, row["id"]))
        else:
            cursor.execute(
                "INSERT INTO api_quota (used, remaining, daily_limit) VALUES (?, ?, ?)",
                (units, max(0, DAILY_QUOTA_LIMIT - units), DAILY_QUOTA_LIMIT)
            )
        conn.commit()
        conn.close()
    except sqlite3.Error as e:
        logger.warning(f"Quota accounting skipped: {e}")


def get_today_quota_used() -> int:
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT used FROM api_quota WHERE date = CURRENT_DATE ORDER BY id DESC LIMIT 1")
    row = cursor.fetchone()
    conn.close()
    return int(row["used"]) if row else 0


# ---- Async wrappers ----
# sqlite3 is blocking, and the service layer runs on the event loop, so these
# move the write off it. The sync functions stay as the implementation (and for
# scripts/tests); only the awaited entry points below are used by the API.
# run_in_threadpool (anyio, already a FastAPI dependency) is used rather than
# asyncio.to_thread because the latter binds to the loop's default executor,
# which is per-thread on Python 3.9 and unsafe for TestClient-style apps that
# run their loop in a worker thread.

async def save_search_history_async(keywords: List[str], result_count: int) -> None:
    await run_in_threadpool(save_search_history, keywords, result_count)


async def save_trend_snapshot_async(
    keyword: str, avg_views: float, total_videos: int, engagement_rate: float = 0.0
) -> None:
    await run_in_threadpool(save_trend_snapshot, keyword, avg_views, total_videos, engagement_rate)


async def record_quota_usage_async(units: int) -> None:
    await run_in_threadpool(record_quota_usage, units)
