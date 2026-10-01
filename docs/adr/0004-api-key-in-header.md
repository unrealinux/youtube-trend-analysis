# 0004. API key travels in X-goog-api-key, not the query string

Date: 2026-10-01

## Context

The key was passed as `?key=...`. `httpx` logs full request URLs at INFO, so
the key was written in plaintext to `server.log` (and would appear in any
proxy or referrer along the path). A leaked key was found live in that log.

## Decision

`fetch_json` sends the key in the `X-goog-api-key` header; the query string
carries only non-secret params.

## Consequences

Easier: logs, proxies, and referrers no longer see the key.
Harder: nothing — every call site already funnels through `fetch_json`.
A key that has already been logged is still compromised: rotate it, the code
fix does not retract the exposure.
