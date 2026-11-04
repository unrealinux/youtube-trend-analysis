# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

One person: the repo owner, working alone at their own machine. They research
short-video niches before producing videos. No accounts, no shared data, no
second role. Multi-user support is explicitly out of scope until the owner
says otherwise.

## Product Purpose

Turn YouTube Data API data into a decision about what video to make next.
Success is a direction concrete enough to start producing — which niche, how
long, what title shape, when to publish — not a dashboard of raw charts.
The owner uses it for three jobs, in priority order: find an enterable niche,
track how chosen niches move over time, and study competitor channels.

## Positioning

Most YouTube tools report demand (views, engagement) or supply (channel
rankings) separately. This one puts both in one flow and ends each row with a
verdict — `值得做` / `观望` / `过宽` / `避开` — computed from velocity, real
engagement, and head concentration. The verdict, not the raw number, is the
product. A neighboring analytics tool could show the same metrics but would
still leave the reader to decide.

## Operating Context

- Runs locally via uvicorn on `127.0.0.1:8001`; started from the owner's own
  terminal because background processes do not survive the agent session.
- YouTube Data API v3 quota is 10,000 units/day, reset at midnight Pacific.
  `search.list` costs 100, `videos.list`/`channels.list` cost 1 each.
- Networks without direct Google access need `HTTP_PROXY`/`HTTPS_PROXY` in
  `.env`.
- A third-party MCP radar (`lab.ytdeepsea.com`) supplies cross-platform topic
  signals (B站, 抖音, TikTok, 小红书, X); it reports conclusions without raw
  data, so its claims are cross-checked against this project's YouTube numbers
  whenever the target platform is YouTube.
- Work happens across many short sessions; state that matters must be on
  screen, not carried between turns.

## Capabilities and Constraints

Confirmed capabilities: keyword and Shorts search, channel analysis, batch
scan, hot categories, feature analysis, deep Shorts analysis, trend tracking
with snapshots, keyword discovery with verdicts, channel leaderboard, export
to JSON/CSV, quota accounting, search history.

Confirmed constraints:

- Verification covers YouTube only. Topics native to B站/抖音/小红书/TikTok
  cannot be validated by this project's leaderboard or discovery.
- Samples are capped (≤50 videos per query) and ordered by view count, so
  averages describe the head of the distribution, not the typical video.
- Quota is a hard budget: every scan-class action must be worth its units.
- Verdict thresholds are descriptive heuristics, not market truth; the
  `过宽` rule already misses categories when an entire batch has high velocity.
- SQLite stores history, trend snapshots, and quota usage. No authentication
  exists and none is wanted while the tool stays local.

## Evidence on Hand

Live YouTube API data only. No testimonials, customers, benchmarks, pricing, or
deployment claims exist, and future work must not fabricate them. `.scratch/`
is empty, so there is no user-research record to cite.

## Product Principles

1. Every view ends in a decision. If a screen shows data without answering
   "should I make this, and how", it is unfinished.
2. Demand and supply are read together. Velocity alone flatters a niche that
   one channel already owns.
3. Quota is a budget, not a suggestion. Ask before expensive scans and say what
   they cost.
4. Conclusions must be executable: duration, title shape, posting window, and
   the next concrete action.
5. Build for one person on one machine. Do not add multi-user complexity for an
   audience that does not exist yet.
