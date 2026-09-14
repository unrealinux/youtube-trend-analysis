# Issue tracker

Issues live as local markdown files, one directory per feature:

```
.scratch/
└── <feature>/
    ├── 001-short-title.md
    └── 002-another.md
```

`.scratch/` is working state, not shipped code — keep it out of commits unless
the team decides otherwise.

Each issue file starts with a `Status:` line (see `triage-labels.md`), then a
short body:

```markdown
Status: needs-triage

## Problem
<what is broken / wanted, with file:line evidence>

## Acceptance
<what must be true when done>
```

Workflow: file it → triage (set `Status:`) → pick up `ready-for-agent` →
close by deleting the file or moving it under a `done/` sibling.
