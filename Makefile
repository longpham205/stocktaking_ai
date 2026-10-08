# Stocktaking AI: every day-to-day command. Run from the repo root.
# On Windows run it from Git Bash (the recipes are POSIX shell).
.PHONY: help setup docker-up docker-up-gpu docker-up-prod docker-up-data docker-down logs \
        migrate migration dev-api test lint format type-check check-env clean reset-password \
        reset-advanced-password smoke test-web seed-demo import-legacy db-save db-restore purge-media assets

COMPOSE      := docker compose
COMPOSE_GPU  := $(COMPOSE) -f docker-compose.yml -f docker-compose.gpu.yml
COMPOSE_PROD := $(COMPOSE) -f docker-compose.yml -f docker-compose.prod.yml
# linted and formatted: the web app. The engine and backend/scripts pass `ruff check` but are not ruff-formatted.
LINT_PATHS   := app entrypoints migrations tests/api tests/test_import_boundary.py
S            ?= api

help:            ## list the targets
	@grep -E '^[a-zA-Z_-]+:.*##' $(MAKEFILE_LIST) | sed -E 's/:.*## /\t/' | expand -t 18

setup: .env      ## create .env, fill any missing secret in it, check docker and uv
	@for key in MEDIA_URL_SECRET JWT_SECRET; do \
	  if ! grep -q "^$$key=..*" .env; then \
	    secret=$$(openssl rand -hex 32); \
	    if grep -q "^$$key=" .env; then sed -i.bak "s|^$$key=.*|$$key=$$secret|" .env && rm -f .env.bak; \
	    else printf '%s=%s\n' "$$key" "$$secret" >> .env; fi; \
	    echo ".env: generated $$key"; \
	  fi; \
	done
	@docker --version >/dev/null 2>&1 || (echo "docker not found: install Docker Desktop" && exit 1)
	@docker compose version >/dev/null 2>&1 || (echo "docker compose not found" && exit 1)
	@uv --version >/dev/null 2>&1 || (echo "uv not found: https://docs.astral.sh/uv/" && exit 1)
	@echo "ok: docker, docker compose, uv. Next: make docker-up"

.env:
	@cp .env.example .env
	@echo ".env created from .env.example"

docker-up: setup ## build and start postgres, migrate, api, web -> http://localhost:5173
	$(COMPOSE) up --build -d --wait
	@echo "web: http://localhost:5173   api: http://localhost:8000/healthz   docs (dev): http://localhost:8000/docs"

docker-up-gpu: setup  ## same, with the real pipeline on an NVIDIA GPU (large image)
	$(COMPOSE_GPU) up --build -d --wait

docker-up-prod: setup  ## same, without reload or source mounts
	$(COMPOSE_PROD) up --build -d --wait

docker-up-data:  ## only postgres (published on :5437), for dev-api, migrate and test on the host
	$(COMPOSE) up -d --wait postgres

docker-down:     ## stop everything (the database volume is kept)
	$(COMPOSE) down

logs:            ## follow one service: make logs S=api
	$(COMPOSE) logs -f $(S)

migrate: docker-up-data  ## apply every Alembic revision to DATABASE_URL
	cd backend && uv run alembic upgrade head

migration: docker-up-data  ## autogenerate a revision from the models: make migration MSG="add x to y"
	@test -n "$(MSG)" || (echo 'usage: make migration MSG="what changed"' && exit 1)
	cd backend && uv run alembic upgrade head && \
	  n=$$(printf '%04d' $$(( $$(ls migrations/versions/*.py 2>/dev/null | wc -l) + 1 ))) && \
	  uv run alembic revision --autogenerate --rev-id $$n -m "$(MSG)"

# One API process, always (`--workers 1` in docker compose and the Dockerfile): the model, the
# recognition queue, the login limiter and the idempotency keys live in that process's memory.
dev-api: migrate ## API with reload on :8000, on the host (reads .env)
	cd backend && uv run uvicorn entrypoints.api:app --reload --port 8000

smoke:           ## end-to-end check of the running API over HTTP; needs SMOKE_ADMIN(_PW), SMOKE_STAFF(_PW) [PHOTO=...]
	cd backend && uv run python scripts/smoke_api.py $(or $(PHOTO),data_demo/query/query_01.jpg)

test: docker-up-data  ## backend suite; the API tests use their own database (stocktaking_test)
	cd backend && uv run pytest -q

test-web:        ## frontend suite (vitest), on the host (needs pnpm)
	cd frontend && pnpm install --frozen-lockfile && pnpm test

lint:            ## ruff check + format check on the web app
	cd backend && uv run ruff check $(LINT_PATHS) && uv run ruff format --check $(LINT_PATHS)

format:          ## apply ruff's fixes and formatting to the web app
	cd backend && uv run ruff check --fix $(LINT_PATHS) && uv run ruff format $(LINT_PATHS)

type-check:      ## mypy (strict) on app/, entrypoints/, migrations/; tsc (strict) on the frontend
	cd backend && uv run mypy
	cd frontend && pnpm install --frozen-lockfile && pnpm typecheck

reset-password: migrate  ## new random password for an account, created if missing: make reset-password USER_NAME=admin ROLE=admin
	@test -n "$(USER_NAME)" || (echo 'usage: make reset-password USER_NAME=admin [ROLE=admin]' && exit 1)
	cd backend && uv run python -m entrypoints.reset_password $(USER_NAME) $(if $(ROLE),--create --role $(ROLE),)

reset-advanced-password: migrate  ## new random advanced password (guards the engine settings), printed once
	cd backend && uv run python -m entrypoints.reset_password --advanced

check-env:       ## what this installation has and lacks: tools, secrets, database, catalog, pipeline, GPU
	@docker --version; docker compose version; uv --version
	@test -f .env && echo ".env: present" || echo ".env: missing (make setup)"
	cd backend && uv run python -m entrypoints.check_env
	cd backend && uv run python scripts/set_device.py show

seed-demo: migrate  ## demo catalog (50 products) and demo prices into DATABASE_URL; safe to run again
	cd backend && uv run python -m entrypoints.seed_demo

import-legacy: migrate  ## copy a web v1 SQLite database into DATABASE_URL: make import-legacy DATA_DIR=data_demo [REPLACE=1]
	@test -n "$(DATA_DIR)" || (echo 'usage: make import-legacy DATA_DIR=data_demo [REPLACE=1]' && exit 1)
	cd backend && uv run python -m entrypoints.import_legacy_sqlite --sqlite $(DATA_DIR)/db/app.db $(if $(REPLACE),--replace,)

db-save: docker-up-data  ## dump the database to backups/NAME.dump: make db-save NAME=demo_clean
	@test -n "$(NAME)" || (echo 'usage: make db-save NAME=demo_clean' && exit 1)
	@mkdir -p backups
	$(COMPOSE) exec -T postgres pg_dump -U stocktaking -d stocktaking -Fc > backups/$(NAME).dump
	@echo "saved backups/$(NAME).dump"

db-restore: docker-up-data  ## REPLACE the database with backups/NAME.dump (stop the api first): make db-restore NAME=demo_clean
	@test -f "backups/$(NAME).dump" || (echo 'usage: make db-restore NAME=<a file in backups/ without .dump>' && exit 1)
	$(COMPOSE) exec -T postgres pg_restore -U stocktaking -d stocktaking --clean --if-exists --no-owner < backups/$(NAME).dump
	@echo "restored backups/$(NAME).dump"

purge-media:     ## delete capture photos older than DAYS (default 30): make purge-media DAYS=30 [DRY_RUN=1]
	cd backend && uv run python -m entrypoints.purge_media --days $(or $(DAYS),30) $(if $(DRY_RUN),--dry-run,)

assets:          ## download the released weights + data into backend/ and check their SHA-256: make assets [TAG=assets-v1]
	cd backend && uv run python scripts/fetch_assets.py $(if $(TAG),--tag $(TAG),)

clean:           ## remove caches (not data, not weights, not the database volume)
	rm -rf backend/.pytest_cache backend/.mypy_cache backend/.ruff_cache frontend/dist
	find backend -name __pycache__ -type d -prune -exec rm -rf {} +
