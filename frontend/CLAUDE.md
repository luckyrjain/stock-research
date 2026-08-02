# CLAUDE.md (frontend)

This file provides guidance to Claude Code when working with code in `frontend/`. See the
repo-root [`CLAUDE.md`](../CLAUDE.md) for the project overview and pointers to the backend/product
docs. See [`../docs/design.md`](../docs/design.md) (AlphaPulse Design System) for colors,
typography, spacing, and component patterns — all UI work must follow it; do not hard-code hex
values or invent new patterns, always use the existing design tokens from `tailwind.config.ts`.

---

## Frontend (Next.js)

### Runtime & package manager

- **Node.js** — no `.nvmrc`; any Node 18+ works (tested on v25)
- **npm** (package-lock.json present; do not use yarn or pnpm)
- Install: `cd frontend && npm install`

### Running dev server

```bash
cd frontend && npm run dev   # starts on port 3000
```

### Type-checking (no lint config exists)

```bash
cd frontend && npx tsc --noEmit
```

There is no ESLint config. TypeScript strict mode (`"strict": true`) is the primary code quality
gate alongside the E2E suite below.

### End-to-end tests (Playwright)

```bash
cd frontend && npx playwright install --with-deps chromium   # once, or in a fresh CI runner
cd frontend && npm run test:e2e
```

`frontend/e2e/*.spec.ts` covers core flows (home page search + a full mocked stock-analysis run,
watchlist, screener, portfolio) against `npm run dev`. **Every backend response is mocked at the
browser network layer** (`page.route()`, see `frontend/e2e/fixtures.ts` for the shared SSE/JSON
fixture builders) — this suite never talks to a real FastAPI backend, matching this repo's
existing "no live external calls in CI" convention (`tests/*.py`'s own docstring already states
this for the pytest suite; a live E2E run would mean real NSE/yfinance/Screener.in scraping on
every PR, exactly the flakiness that convention exists to avoid). Anything a test doesn't
explicitly mock falls through to the Next.js proxy routes' existing "backend unavailable" 503
handling (no FastAPI process runs in the E2E job at all), which the frontend already renders
gracefully — so an unmocked add-on fetch (e.g. peer comparison, insider activity) degrades to
"not available" instead of hanging or crashing the page.

### Design system

All UI work must follow `design.md` (AlphaPulse Design System) — the single source of truth for colors, typography, spacing, component patterns (cards, badges, buttons, tables, animations), and responsive strategy. Do not hard-code hex values or invent new patterns; always use the existing design tokens from `tailwind.config.ts`.

### Key libraries and patterns

- **Next.js 15** with App Router; all pages are `'use client'` components
- **React 19** — no React Query, no state management library; plain `useState`/`useRef`/`useCallback`
- **Tailwind CSS v3** with a custom dark-theme palette defined in `tailwind.config.ts` (key colors: `bg`, `surface`, `card`, `tx`, `muted`, `buy`, `sell`, `hold`, `accent`)
- **SSE via `EventSource`** — all streaming uses the browser's native EventSource API, not WebSockets
- **Proxy routes** — `frontend/app/api/*/route.ts` files proxy to `http://localhost:8000` (configurable via `API_URL` env var). They pipe the SSE stream directly; no buffering.
- **`@/` path alias** maps to `frontend/` root (set in `tsconfig.json`)
- All canonical TypeScript types live in `frontend/types/index.ts` — always update this when adding new SSE events or report fields

---

## Code Style & Conventions

### TypeScript / React

- **Strict TypeScript** — no `any`, no type assertions unless unavoidable.
- No Prettier config present — match surrounding formatting.
- Component files use `export default function`. Props interfaces are defined inline above the component.
- All SSE message types are discriminated unions in `frontend/types/index.ts`.

### Naming

- TypeScript: `PascalCase` for types/interfaces/components; `camelCase` for variables and functions

---

## Environment & Config

| Variable | Default | Purpose |
|---|---|---|
| `API_URL` | `http://localhost:8000` | FastAPI backend URL (set in Next.js env) |
| `TRUSTED_PROXY_SECRET` | unset | Same value as the backend's env var of the same name (see `backend/CLAUDE.md`'s "Trusted client IP for per-IP rate limiting"). Server-only (never exposed to the browser) |

---

## Important Rules for Claude

- **Run `npx tsc --noEmit` in `frontend/`** before marking any frontend task done. This does NOT catch everything — a CSS syntax error, for example, only surfaces under the production minifier (`npm run build`), not `tsc` or `next dev`. CI runs both; when in doubt, especially after touching `globals.css` or raw CSS, run `npm run build` locally too.
