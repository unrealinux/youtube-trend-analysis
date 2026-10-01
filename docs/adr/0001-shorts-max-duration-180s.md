# 0001. Shorts threshold is 180 seconds, not 60

Date: 2026-10-01

## Context

YouTube raised the Shorts ceiling from 60s to 3 minutes in October 2024.
Filtering at 60s would drop 1–3 minute Shorts entirely, or misclassify them as
regular videos — skewing `avg_views`, the duration buckets, and every Shorts
panel.

## Decision

A video is a Short when `0 < duration_sec <= 180` (`SHORTS_MAX_DURATION_SEC`).
A duration of 0, absent, or `"PT"` is **not** a Short.

## Consequences

Easier: the sample matches what YouTube actually serves as Shorts today.
Harder: the number now carries platform history — if YouTube moves the ceiling
again, changing the constant without this record would look arbitrary.
