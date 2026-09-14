# Domain docs

This repo uses the **single-context** layout:

- `CONTEXT.md` at the root — the one domain model and glossary. Keep every
  term and invariant there; do not scatter definitions across files.
- `docs/adr/` — architecture decision records. Create `NNNN-slug.md` when a
  decision is hard to reverse or will surprise a future reader. ADRs are
  append-only: supersede, don't rewrite.

Rules:

- New domain term → add it to `CONTEXT.md` glossary.
- Decision that changes an invariant → write an ADR and update `CONTEXT.md`.
- Keep it short. An ADR is context + decision + consequences, nothing more.
