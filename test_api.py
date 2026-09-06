#!/usr/bin/env python3
"""Test YouTube API connection"""
import os
import asyncio
import httpx
from dotenv import load_dotenv
load_dotenv("C:/tmp/.env")

YOUTUBE_API_KEY = os.getenv("YOUTUBE_API_KEY", "")
YOUTUBE_BASE_URL = "https://www.googleapis.com/youtube/v3"

async def test_api():
    if not YOUTUBE_API_KEY:
        print("❌ ERROR: YOUTUBE_API_KEY not configured")
        print("   Please set it in .env file or environment")
        return

    async with httpx.AsyncClient(timeout=30.0) as client:
        # Test 1: Quota check
        print("\n📊 测试 1: 检查 API 配额...")
        try:
            resp = await client.get(f"{YOUTUBE_BASE_URL}", params={
                "part": "quota",
                "key": YOUTUBE_API_KEY
            })
            if resp.status_code == 200:
                data = resp.json()
                quota = data.get("quotaUsage", 0)
                print(f"   ✓ API Key 有效")
                print(f"   ✓ 今日已用配额: {quota} 单位")
                print(f"   ✓ 每日配额限制: 10000 单位")
            else:
                print(f"   ✗ 配额检查失败: {resp.status_code}")
                print(f"   响应: {resp.text}")
        except Exception as e:
            print(f"   ✗ 错误: {e}")

        # Test 2: Search test
        print("\n🔍 测试 2: 搜索测试视频...")
        try:
            resp = await client.get(f"{YOUTUBE_BASE_URL}/search", params={
                "part": "snippet",
                "q": "Python tutorial",
                "maxResults": 3,
                "type": "video",
                "key": YOUTUBE_API_KEY
            })
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                print(f"   ✓ 找到 {len(items)} 个视频")
                for i, item in enumerate(items[:3], 1):
                    snippet = item.get("snippet", {})
                    print(f"   {i}. {snippet.get('title', 'N/A')[:50]}...")
            else:
                print(f"   ✗ 搜索失败: {resp.status_code}")
                print(f"   响应: {resp.text}")
        except Exception as e:
            print(f"   ✗ 错误: {e}")

        # Test 3: Channel test
        print("\n📺 测试 3: 频道信息查询...")
        try:
            # 使用一个知名频道 ID
            resp = await client.get(f"{YOUTUBE_BASE_URL}/channels", params={
                "part": "snippet,statistics",
                "id": "UC6nSFJ9ePylLEJ1LJ7sggAg",  # GeogeeksForGeeks
                "key": YOUTUBE_API_KEY
            })
            if resp.status_code == 200:
                data = resp.json()
                items = data.get("items", [])
                if items:
                    snippet = items[0].get("snippet", {})
                    stats = items[0].get("statistics", {})
                    print(f"   ✓ 频道: {snippet.get('title', 'N/A')}")
                    print(f"   ✓ 订阅者: {int(stats.get('subscriberCount', 0)):,}")
                    print(f"   ✓ 视频数: {int(stats.get('videoCount', 0)):,}")
            else:
                print(f"   ✗ 频道查询失败: {resp.status_code}")
                print(f"   响应: {resp.text}")
        except Exception as e:
            print(f"   ✗ 错误: {e}")

        print("\n✅ 测试完成")

if __name__ == "__main__":
    asyncio.run(test_api())
