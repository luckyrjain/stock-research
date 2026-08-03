.DEFAULT_GOAL := help

.PHONY: help setup check dev migrate test test-e2e lint seed screenshots

help:
	@echo "make setup       - create venv, install backend+frontend deps, copy .env.example -> .env"
	@echo "make check       - verify prerequisites (tool versions, .env, DB/Redis reachability)"
	@echo "make dev         - run backend (uvicorn --reload) + frontend (next dev) together"
	@echo "make migrate     - run Alembic migrations against DATABASE_URL"
	@echo "make test        - run backend pytest + frontend tsc --noEmit"
	@echo "make test-e2e    - run frontend Playwright e2e tests (installs Chromium on first run)"
	@echo "make lint        - run the one enforced lint gate (frontend tsc --noEmit)"
	@echo "make seed        - populate local Postgres via the SME Signals + Screener batch"
	@echo "                   pipelines (real NSE/yfinance data, several minutes)"
	@echo "make screenshots - recapture docs/screenshots/*.png from a live 'make dev' instance"

setup:
	@bash scripts/setup.sh

check:
	@bash scripts/check-prereqs.sh

dev:
	@bash scripts/dev.sh

migrate:
	@bash scripts/migrate.sh

test:
	@test -d .venv || (echo "No .venv found — run 'make setup' first." && exit 1)
	cd backend && ../.venv/bin/python -m pytest tests/
	cd frontend && npx tsc --noEmit

test-e2e:
	cd frontend && npx playwright install --with-deps chromium && npm run test:e2e

lint:
	cd frontend && npx tsc --noEmit
	@echo "No backend linter is configured in this repo — pylint is referenced via inline"
	@echo "'# pylint: disable=' comments but not enforced (see backend/CLAUDE.md)."

seed: migrate
	@bash scripts/seed.sh

screenshots:
	@bash scripts/update-screenshots.sh
