# 0002. Velocity (views/day) is the only time-normalised metric

Date: 2026-10-01

## Context

Cumulative `view_count` grows with age, so a fixed cohort's average always
rises — an old video can outrank a fresh hit purely by having existed longer.
Trend direction computed on raw averages therefore reports "rising" for a
cohort that is simply ageing.

## Decision

`views_per_day = view_count / max(age_days, 1)` is the only time-normalised
signal. `order=velocity` re-ranks the candidate pool by it. Trend snapshots
store the **median views/day of recent uploads** (order=date, 14-day window) and
compare the latest two snapshots: `> +10%` rising, `< -10%` falling, else
stable.

## Consequences

Easier: ranking and trend direction answer "what is heating up", not "what
accumulated".
Age is floored at 1 day so a same-day upload cannot produce an infinite value.
Snapshots written before velocity existed fall back to `avg_views`.
