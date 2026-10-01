# 0005. In-process dict cache, not Redis

Date: 2026-10-01

## Context

Repeated searches re-spend quota (`search.list` = 100 units), so responses are
cached. A Redis dependency would add an external service to run and operate for
a tool that serves one user locally.

## Decision

A module-level dict in `app/config.py`: 5-minute TTL, 512-entry cap, eviction
by insertion order (expired entries reclaimed first).

## Consequences

Easier: zero infrastructure; the cache dies with the process.
Harder (**ceiling**): the cache is not shared across workers or processes, so
running uvicorn with `--workers > 1` multiplies cache misses and quota spend.
Upgrade path when concurrency demands it: Redis (or any shared store) behind
the same `_get_cached` / `_set_cached` pair.
