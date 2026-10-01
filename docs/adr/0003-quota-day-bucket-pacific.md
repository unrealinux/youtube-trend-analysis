# 0003. Quota day bucket uses Pacific Time

Date: 2026-10-01

## Context

YouTube Data API v3 resets its daily quota at midnight **Pacific**, not UTC and
not the host's local time. Bucketing usage by UTC would roll the counter at the
wrong moment and let usage carry across the real reset, misreporting remaining
quota.

## Decision

Quota accounting buckets by `QUOTA_TIMEZONE` (default `America/Los_Angeles`).

## Consequences

Easier: `/api/quota` matches the number the Google Cloud console shows.
Harder: quota code depends on zoneinfo/tz data being present. A wrong zone
value silently shifts the reset point, so it is read once at startup.
