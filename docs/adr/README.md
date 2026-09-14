# Architecture Decision Records

One file per decision: `NNNN-slug.md` (e.g. `0001-quota-costs-search-list.md`).

Template:

```markdown
# NNNN. <decision>

Date: YYYY-MM-DD

## Context
<forces at play>

## Decision
<what we chose>

## Consequences
<what becomes easier / harder>
```

ADRs are append-only. To change a decision, add a new ADR that supersedes the
old one and say so in both files.

Existing decisions live in `CONTEXT.md` under "Invariants" until they earn a
record here.
