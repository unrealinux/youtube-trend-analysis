# CONTEXT — YouTube 数据分析平台

Single-context domain model for this repo. Read this before changing analysis
math, quota accounting, or the Shorts filter — those carry the project's real
decisions.

## Domains / glossary

- **Shorts** — a video whose `contentDetails.duration` is `0 < s <= 180`
  (`SHORTS_MAX_DURATION_SEC`). 180s, not 60s: YouTube raised the ceiling in
  Oct 2024. Duration 0/absent/`PT` is *not* a Short.
- **TrendData** — result of a keyword or channel search: videos + `avg_views`
  + `top_keywords` (tags) + `upload_frequency` (per month).
- **Upload frequency** — `count / span_days * 30`, with `span_days` floored at
  7. The floor stops a same-day batch (e.g. 20 uploads in one day) becoming
  "600/month".
- **Engagement rate** — `(likes + comments) / views * 100`. Already a percent
  in `ShortsDetailedResult`/batch/hot; `TrendData` exposes raw counts only.
- **Trend snapshot** — one row in `trend_tracking`. `refresh=true` appends;
  without it the stored history is served as-is. Direction compares the latest
  two snapshots: `> +10%` rising, `< -10%` falling, else stable.
- **Quota** — YouTube Data API v3 units. `search.list` = 100, `videos.list` /
  `channels.list` = 1 each. Only real (non-cache-hit) calls are charged. The
  day bucket uses Pacific Time (`QUOTA_TIMEZONE`), matching YouTube's reset.
- **Auto snapshot** — background loop (`TREND_SNAPSHOT_INTERVAL_HOURS`, default
  24) that appends a snapshot for keywords already in `trend_tracking` whose
  newest snapshot is stale. Never starts tracking a new keyword, capped per
  cycle, and skipped above 80% daily quota.
- **Cache** — in-process dict in `app/config.py`, 5 min TTL, 512 entries.
  `publishedAfter` is bucketed to the UTC day so keys are stable across seconds.

## Layout

- `CONTEXT.md` (this file) — the single domain context.
- `docs/adr/` — architecture decision records, one file per decision.
- `docs/agents/` — how agents operate in this repo (issues, labels, domain).

## Invariants (don't regress)

1. `time_range` is validated at the boundary; unknown values are **400**, never
   a silent 365-day fallback.
2. Every route declares a real Pydantic `response_model`; every time-range
   endpoint rejects bad ranges even when the API key is missing.
3. `init_db()` is idempotent and runs at import time.
4. DB writes never block the event loop (`run_in_threadpool`) and quota
   bookkeeping never fails the request it accounts for.
5. Untrusted YouTube data is HTML-escaped before it reaches `innerHTML`.
6. Transient HTTP failures are retried with backoff; 4xx (bad key, exhausted
   quota, missing resource) are not. Fan-out scans are concurrency-capped by
   `MAX_CONCURRENT_SCANS`.
