# README reformat — design

## Problem

Current `README.md` is ~400 lines: full repo tree, all-57-endpoints table, cache-TTL table,
migration-stamp walkthrough, per-mode paragraphs. Most of this duplicates `docs/*.md` and
`backend/CLAUDE.md`/`frontend/CLAUDE.md`, which are the actual sources of truth — and the
duplication has already caused staleness bugs (stale endpoint/table counts, fixed 2026-08-02).
A GitHub visitor skimming the repo has to scroll past exhaustive engineering detail before
reaching "what is this and why should I care."

## Audience

Primary reader: a GitHub visitor / portfolio viewer deciding in ~30s whether the project is
interesting. Secondary: a contributor who needs to get a local instance running — served via a
collapsed manual-setup section, with Docker Compose as the primary fast path.

## Structure

1. **Hero** — one-liner + CI badge + tech-stack badges (Python/FastAPI/Next.js/PostgreSQL)
2. **What it is** — one bullet per mode (Stock Analysis, Market Picks, SME Signals, Screener,
   Watchlist/Portfolio, Portfolio Aggregator), 1-2 lines each, not paragraphs
3. **Tech stack** — compact bullet list, no sub-explanations
4. **Project structure** — short top-level-only tree (`backend/`, `frontend/`, `docs/` and a
   couple of headline files), for spatial orientation, not exhaustive
5. **Quickstart** — Docker Compose as the primary path; manual backend/frontend setup and DB
   migration steps collapsed under `<details>` for contributors who need them
6. **Tests** — one fenced block, no explanation
7. **Docs** — table linking to `docs/*.md`, `backend/CLAUDE.md`, `frontend/CLAUDE.md` for
   everything cut below (architecture, full API reference, database schema, deployment,
   customizing analyst behavior)

Target length: ~120-150 lines (down from ~400).

## Cut entirely (moved to / already in docs, not duplicated in README)

- Full repo tree (both backend and frontend) → stays only in `backend/CLAUDE.md`
- All-57-endpoints table → `docs/api-reference.md`
- Cache-TTL table → `backend/CLAUDE.md`
- Migration stamp-vs-upgrade walkthrough → `docs/setup.md` / `backend/CLAUDE.md`
- "Customizing analyst behavior" section → `backend/CLAUDE.md`
- Batch-pipeline CLI flag reference → `backend/CLAUDE.md`

No new content is invented — this is a trim-and-restructure of existing accurate prose, not a
rewrite of facts. Numbers that drift (endpoint counts, table counts) are dropped from README prose
entirely rather than re-stated, since docs/ is the authoritative source and keeping counts in two
places is what caused the prior staleness bug.

## Out of scope

- Screenshots/GIFs — skipped this pass, add as follow-up once dashboard UI is stable
- No changes to `docs/*.md` or `CLAUDE.md` content — those stay as the detailed reference
- No CONTRIBUTING.md split — single README file, per user's original ask

## Success criteria

- `README.md` reads top-to-bottom in under a minute
- Every fact retained is accurate as of current code (verified against `docs/api-reference.md`,
  `docs/database.md`, current CI workflow, current CLAUDE.md)
- Every cut section has a working link to where the detail now lives
- No broken relative links (`docs/index.md`, `backend/CLAUDE.md`, etc.)
