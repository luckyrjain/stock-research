.DEFAULT_GOAL := help

.PHONY: help setup check test test-e2e

help:
	@echo "make setup     - create venv, install backend+frontend deps, copy .env.example -> .env"
	@echo "make check     - verify prerequisites (tool versions, .env, DB/Redis reachability)"
	@echo "make test      - run backend pytest + frontend tsc --noEmit"
	@echo "make test-e2e  - run frontend Playwright e2e tests (installs Chromium on first run)"

setup:
	@bash scripts/setup.sh

check:
	@bash scripts/check-prereqs.sh

test:
	@test -d .venv || (echo "No .venv found — run 'make setup' first." && exit 1)
	cd backend && ../.venv/bin/python -m pytest tests/
	cd frontend && npx tsc --noEmit

test-e2e:
	cd frontend && npx playwright install --with-deps chromium && npm run test:e2e
