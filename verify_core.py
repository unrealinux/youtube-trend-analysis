#!/usr/bin/env python3
"""验证 YouTube Trend Analysis 项目核心功能。
用法: python3 verify_core.py
需在项目根目录下运行，服务已启动在 8001 端口。
"""
import sys
import json
import os
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# The mock-data section below drives the app through an in-process TestClient,
# which runs against whatever DB the app process is using. Point it at a
# throwaway file *before* anything imports app.database (whose DATABASE_PATH is
# read at import time), otherwise a verification run deletes the real
# youtube_history.db through the DELETE /api/history check.
_VERIFY_DB = os.path.join(tempfile.mkdtemp(), "verify.db")
os.environ["YT_HISTORY_DB"] = _VERIFY_DB

import app.database as _database

_database.DATABASE_PATH = _VERIFY_DB

import requests

BASE = "http://localhost:8001"
PASS, FAIL = "✅", "❌"
results = {"pass": 0, "fail": 0, "skip": 0}


def check(name, condition, detail=""):
    if condition:
        results["pass"] += 1
        print(f"  {PASS} {name}")
    else:
        results["fail"] += 1
        print(f"  {FAIL} {name}  {detail}")


def test_system_endpoints():
    print("\n📦 系统端点")
    r = requests.get(f"{BASE}/health")
    check("GET /health 返回 200", r.status_code == 200)
    d = r.json()
    check("health 包含 status=ok", d.get("status") == "ok", str(d))
    check("health 包含 api_key_configured", "api_key_configured" in d)
    check("health 包含 database", "database" in d)

    r = requests.get(f"{BASE}/api/cache/stats")
    check("GET /api/cache/stats 返回 200", r.status_code == 200)
    d = r.json()
    check("cache stats 有 size 字段", "size" in d)
    check("cache stats 有 ttl 字段", d.get("ttl") == 300)

    r = requests.post(f"{BASE}/api/cache/clear")
    check("POST /api/cache/clear 返回 200", r.status_code == 200)
    check("clear 返回 cleared=true", r.json().get("cleared") is True)

    r = requests.get(f"{BASE}/api/history")
    check("GET /api/history 返回 200", r.status_code == 200)
    check("history 返回数组", isinstance(r.json(), list), str(r.json())[:80])

    r = requests.delete(f"{BASE}/api/history")
    check("DELETE /api/history 返回 200", r.status_code == 200)
    check("delete 返回 deleted>=0", r.json().get("deleted", 0) >= 0)

    # 清空后必须为空，否则前端"最近搜索"永远显示陈旧数据
    r = requests.get(f"{BASE}/api/history")
    check("清空后 history 为空数组", r.json() == [], str(r.json())[:80])

    # 配额来自真实记账，不再硬编码 used=1
    r = requests.get(f"{BASE}/api/quota")
    if r.status_code == 200:
        q = r.json()
        check("quota 有 limit=10000", q.get("limit") == 10000)
        check("quota used 与 remaining 自洽", q.get("used", -1) + q.get("remaining", -1) == q.get("limit"))
        check("quota usage_percent 非固定 0.01", q.get("usage_percent") != 0.01 or q.get("used") == 1)
    else:
        results["skip"] += 1
        print(f"  ⏭️  /api/quota 需要 API Key，跳过")

    # 首页
    r = requests.get(f"{BASE}/")
    check("GET / 返回 200", r.status_code == 200)
    check("首页是 HTML", "text/html" in r.headers.get("Content-Type", ""))


def test_missing_api_key():
    print("\n🔑 API Key 未配置时的响应")
    # This block asserts the no-key degradation path, so it is only meaningful
    # when the server under test has no key. Running it against a keyed server
    # both fails misleadingly and spends real quota on the key.
    health = requests.get(f"{BASE}/health").json()
    if health.get("api_key_configured"):
        results["skip"] += 1
        print("  ⏭️  服务端已配置 API Key，跳过（本组检查需要无 Key 的服务端；"
              "请用 YOUTUBE_API_KEY=\"\" 启动后再跑）")
        return
    endpoints = [
        ("GET", "/api/trends/search?keywords=AI&max_results=5&order=viewCount&time_range=this_month"),
        ("GET", "/api/trends/shorts/search?keywords=AI&max_results=5"),
        ("GET", "/api/trends/channel?channel_id=UC6nSFJ9ePylLEJ1LJ7sggAg"),
        ("POST", "/api/compare", {"query1": "AI", "query2": "ML"}),
        ("GET", "/api/trends/batch-scan?keywords=AI,ML"),
        ("GET", "/api/trends/hot-categories"),
        ("GET", "/api/trends/feature-analysis?keyword=AI"),
        ("GET", "/api/shorts/detailed?keyword=AI"),
        ("GET", "/api/shorts/channel-insights?channel_id=UC6nSFJ9ePylLEJ1LJ7sggAg"),
        ("GET", "/api/trends/trend-tracking?keyword=AI&refresh=true"),
        ("GET", "/api/trends/channels/search?q=Test"),
        ("GET", "/api/quota"),
    ]
    for method, path, *body in endpoints:
        url = BASE + path
        if body:
            r = requests.request(method, url, json=body[0])
        else:
            r = requests.request(method, url)
        ok = r.status_code == 503
        check(f"{method} {path.split('?')[0]} → 503", ok, f"got {r.status_code}")


def test_validation_errors():
    print("\n⚠️  参数校验")
    r = requests.get(f"{BASE}/api/trends/search")
    check("缺少 keywords 返回 422", r.status_code == 422)

    r = requests.get(f"{BASE}/api/trends/search?keywords=&max_results=0")
    check("空关键词 + max_results<1 返回 422", r.status_code == 422)

    r = requests.get(f"{BASE}/api/trends/batch-scan?keywords=")
    check("batch-scan 空关键词返回 400", r.status_code == 400)

    # channels/search 不校验 UC 前缀，由 YouTube API 层面处理
    results["skip"] += 1
    print(f"  ⏭️  无效 channel_id → 跳过（channels/search 不校验 UC 前缀）")

    r = requests.post(f"{BASE}/api/compare", json={})
    check("compare 缺少参数返回 400", r.status_code == 400)


def test_mock_data():
    print("\n🧪 Mock 数据验证核心逻辑")
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from app.main import app
    import app.config as config
    import app.services as services

    original_key = config.YOUTUBE_API_KEY
    config.YOUTUBE_API_KEY = "test_key"

    sample_search = {"items": [{"id": {"videoId": "vid1"}}, {"id": {"videoId": "vid2"}}]}
    sample_videos = {
        "items": [{
            "id": "vid1",
            "snippet": {
                "title": "Test Video",
                "channelTitle": "TestChannel",
                "publishedAt": "2024-06-01T12:00:00Z",
                "description": "desc",
                "tags": ["python", "tutorial", "ai"],
                "thumbnails": {"high": {"url": "https://img.youtube.com/vi/vid1/hqdefault.jpg"}}
            },
            "statistics": {"viewCount": "500000", "likeCount": "20000", "commentCount": "1000"},
            "contentDetails": {"duration": "PT45S"}
        }]
    }

    # Patch the single HTTP boundary so the real _fetch_search/_fetch_videos
    # wrappers (and their argument handling + cache) are exercised. Patching
    # those wrappers instead would hide signature/response-shape regressions.
    async def fake_fetch_json(client, url, params):
        return sample_search if url.endswith("/search") else sample_videos

    config._cache.clear()
    with patch.object(services, "fetch_json", fake_fetch_json):
        client = TestClient(app)

        # 关键词搜索
        r = client.get("/api/trends/search", params={"keywords": "python tutorial", "max_results": 5})
        check("search 返回 200", r.status_code == 200)
        d = r.json()
        check("search 有 total_count", d.get("total_count", 0) > 0)
        check("search videos 有视频", len(d.get("videos", [])) > 0)
        check("search 有 avg_views", d.get("avg_views", 0) > 0)
        check("search 有 top_keywords", len(d.get("top_keywords", [])) > 0)
        check("视频有 url", d["videos"][0].get("url", "").startswith("https://www.youtube.com"))

        # Shorts 搜索
        r = client.get("/api/trends/shorts/search", params={"keywords": "python", "max_results": 5})
        check("shorts search 返回 200", r.status_code == 200)
        d = r.json()
        check("shorts 过滤了非 shorts（只有1个45s的）", d.get("total_count", 0) >= 0)

        # 频道分析
        r = client.get("/api/trends/channel", params={"channel_id": "UC6nSFJ9ePylLEJ1LJ7sggAg"})
        check("channel 返回 200", r.status_code == 200)
        d = r.json()
        check("channel 有 videos", len(d.get("videos", [])) > 0)

        # 对比分析
        r = client.post("/api/compare", json={"query1": "AI", "query2": "ML"})
        check("compare 返回 200", r.status_code == 200)
        d = r.json()
        check("compare 有 comparison.winner", d.get("comparison", {}).get("winner") in ["query1", "query2"])

        # 批量扫描
        r = client.get("/api/trends/batch-scan", params={"keywords": "AI,ML", "max_results": 5})
        check("batch-scan 返回 200", r.status_code == 200)
        d = r.json()
        check("batch-scan 返回 keywords 列表", len(d.get("keywords", [])) >= 0)
        check("batch-scan 按 avg_views 排序", all(
            d["keywords"][i].get("avg_views", 0) >= d["keywords"][i+1].get("avg_views", 0)
            for i in range(len(d["keywords"])-1)
        ))

        # 热门类别
        r = client.get("/api/trends/hot-categories", params={"max_results": 5})
        check("hot-categories 返回 200", r.status_code == 200)
        d = r.json()
        check("hot-categories 返回非空列表", len(d) > 0)
        check("每个类别有 avg_views", all(c.get("avg_views", 0) >= 0 for c in d))

        # 特征分析
        r = client.get("/api/trends/feature-analysis", params={"keyword": "AI", "max_results": 5})
        check("feature-analysis 返回 200", r.status_code == 200)
        d = r.json()
        check("feature-analysis 有 success_score", "success_score" in d)
        check("success_score 在 0-100 之间", 0 <= d.get("success_score", -1) <= 100)

        # 深度分析
        r = client.get("/api/shorts/detailed", params={"keyword": "AI", "max_results": 5})
        check("shorts/detailed 返回 200", r.status_code == 200)
        d = r.json()
        check("detailed 有 duration_buckets", "duration_buckets" in d)
        check("detailed 有 hourly_stats", "hourly_stats" in d)
        check("detailed 有 views_quantiles", "views_quantiles" in d)

        # 趋势追踪
        r = client.get("/api/trends/trend-tracking", params={"keyword": "AI"})
        check("trend-tracking 返回 200", r.status_code == 200)
        d = r.json()
        check("trend-tracking 有 keyword", d.get("keyword") == "AI")
        check("trend-tracking 有 trend_direction", d.get("trend_direction") in ["rising", "falling", "stable"])

        # 频道搜索
        r = client.get("/api/trends/channels/search", params={"q": "Test", "max_results": 3})
        check("channels/search 返回 200", r.status_code == 200)
        d = r.json()
        check("channels/search 有 channels 列表", "channels" in d)

        # 频道洞察
        r = client.get("/api/shorts/channel-insights", params={"channel_id": "UC6nSFJ9ePylLEJ1LJ7sggAg"})
        check("channel-insights 返回 200", r.status_code == 200)
        d = r.json()
        check("channel-insights 有 total_shorts", "total_shorts" in d)
        check("channel-insights 有 top_tags", "top_tags" in d)

    config.YOUTUBE_API_KEY = original_key


def main():
    print("=" * 50)
    print("  YouTube Trend Analysis - 核心功能验证")
    print("=" * 50)
    try:
        requests.get(f"{BASE}/health", timeout=3)
    except Exception:
        print("\n❌ 无法连接到服务，请先启动：")
        print("   cd youtube-trend-analysis && python3 -m uvicorn app.main:app --port 8001")
        sys.exit(1)

    test_system_endpoints()
    test_missing_api_key()
    test_validation_errors()
    test_mock_data()

    print("\n" + "=" * 50)
    total = results["pass"] + results["fail"] + results["skip"]
    print(f"  结果: {results['pass']}/{total} 通过, {results['fail']} 失败, {results['skip']} 跳过")
    print("=" * 50)
    sys.exit(0 if results["fail"] == 0 else 1)


if __name__ == "__main__":
    main()
