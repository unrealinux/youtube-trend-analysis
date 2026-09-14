#!/usr/bin/env python3
"""YouTube Data API connectivity + key check.

Usage:  python3 test_api.py
Exit code 0 when every check passes, 1 otherwise.

Run this before starting the server when you want to know whether your
YOUTUBE_API_KEY works from this machine. The app itself reports the same
failures through /health and the per-endpoint 503 responses.

Note: googleapis.com is unreachable in some regions. If every request fails
with a connection error rather than a 4xx, set HTTP_PROXY / HTTPS_PROXY in
.env (the app reads them via app.config.PROXY_URL) and re-run.
"""
import asyncio
import os
import sys

import httpx
from dotenv import load_dotenv

load_dotenv()  # reads the .env next to the project root, not a machine-specific path

YOUTUBE_BASE_URL = "https://www.googleapis.com/youtube/v3"
# A stable, long-lived channel used only to prove the key can read channels.
SAMPLE_CHANNEL_ID = "UC_x5XG1OV2P6uZZ5FSM9Ttw"  # Google Developers

results = {"pass": 0, "fail": 0}


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        results["pass"] += 1
        print(f"  ✅ {name}")
    else:
        results["fail"] += 1
        print(f"  ❌ {name}  {detail}")


def client_kwargs() -> dict:
    kwargs = {"timeout": 15.0}
    proxy = os.getenv("HTTP_PROXY") or os.getenv("HTTPS_PROXY") or os.getenv("ALL_PROXY")
    if proxy:
        kwargs["proxy"] = proxy
        print(f"  (using proxy: {proxy})")
    return kwargs


async def main() -> int:
    api_key = os.getenv("YOUTUBE_API_KEY", "")
    if not api_key:
        print("❌ YOUTUBE_API_KEY 未配置。请复制 .env.example 为 .env 并填入 Key。")
        return 1
    print(f"🔑 API Key: ...{api_key[-4:]} ({len(api_key)} chars)\n")

    async with httpx.AsyncClient(**client_kwargs()) as client:
        print("🔍 检查 1: search.list（关键词搜索，100 单位）")
        try:
            resp = await client.get(f"{YOUTUBE_BASE_URL}/search", params={
                "part": "snippet", "q": "Python tutorial",
                "maxResults": 3, "type": "video", "key": api_key,
            })
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                check("search.list 可用", True)
                for i, item in enumerate(items[:3], 1):
                    print(f"       {i}. {item['snippet']['title'][:50]}")
            elif resp.status_code == 429:
                check("search.list 可用", False,
                      "429 每日搜索配额已用尽（search.list 上限 100 次/天，每次 100 单位）。"
                      "配额按太平洋时间午夜重置。若之前反复扫描，请等重置后再试。")
            elif resp.status_code in (400, 403):
                # 403 is normally an invalid/restricted key or an exhausted quota.
                check("search.list 可用", False, f"{resp.status_code}: {resp.text[:160]}")
            else:
                check("search.list 可用", False, f"HTTP {resp.status_code}: {resp.text[:160]}")
        except httpx.HTTPError as e:
            check("search.list 可用", False, f"网络错误 {type(e).__name__}: {e}")

        print("\n📺 检查 2: channels.list（频道信息，1 单位）")
        try:
            resp = await client.get(f"{YOUTUBE_BASE_URL}/channels", params={
                "part": "snippet,statistics", "id": SAMPLE_CHANNEL_ID, "key": api_key,
            })
            if resp.status_code == 200:
                items = resp.json().get("items", [])
                if items:
                    snippet = items[0]["snippet"]
                    stats = items[0].get("statistics", {})
                    check("channels.list 可用", True)
                    print(f"       {snippet['title']} — "
                          f"{int(stats.get('subscriberCount', 0)):,} 订阅, "
                          f"{int(stats.get('videoCount', 0)):,} 视频")
                else:
                    check("channels.list 可用", False, "返回 0 个频道（示例频道 ID 可能已变更）")
            else:
                check("channels.list 可用", False, f"HTTP {resp.status_code}: {resp.text[:160]}")
        except httpx.HTTPError as e:
            check("channels.list 可用", False, f"网络错误 {type(e).__name__}: {e}")

    print(f"\n结果: {results['pass']} 通过 / {results['fail']} 失败")
    if results["fail"]:
        print("提示: 若全部为网络错误，请检查是否需要设置代理（HTTP_PROXY / HTTPS_PROXY）。")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
