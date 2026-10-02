# Repository commands. Requires uv and Node.js 24.21 LTS (see docs/DECISIONS.md).
# Run `make` or `make help` for the list of targets.
-include ./config.env
export

COMPOSE_ARGS=-f tools/compose/compose.yml
COMPOSE=docker compose ${COMPOSE_ARGS}
SERVICE=assistant
APP_URL=http://127.0.0.1:$${APP_PORT:-8000}

MONITORING_FORWARD := 8428:localhost:8428
MONITORING_HOST := art-monitoring

.DEFAULT_GOAL := help

.PHONY: help config install check check-backend check-frontend contracts fixtures \
	dev-backend dev-frontend build serve \
	docker-build run up demo down stop restart status ps logs shell health backup docker-backup

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "} /^# -- / {h = $$0; gsub(/^# -- | --$$/, "", h); printf "\n\033[1m%s\033[0m\n", h} /^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)

config: ## Create config.env from config.env.template (never overwrites)
	@if [ -f config.env ]; then echo "config.env already exists"; else cp config.env.template config.env && echo "created config.env; edit it before starting"; fi

# -- native --
install: ## Install backend (uv) and frontend (npm) dependencies
	cd backend && uv sync --locked
	cd frontend && npm ci

contracts: ## Export JSON schemas and regenerate frontend API types
	cd backend && uv run python -m scripts.export_schemas
	cd frontend && npm run gen:api

fixtures: ## Regenerate test fixtures
	cd backend && uv run python -m scripts.generate_fixtures

check: check-backend check-frontend ## Run all backend and frontend checks

check-backend: ## ruff, mypy, contract/fixture drift, pytest
	cd backend && uv run ruff format --check .
	cd backend && uv run ruff check .
	cd backend && uv run mypy
	cd backend && uv run python -m scripts.export_schemas --check
	cd backend && uv run python -m scripts.generate_fixtures --check
	cd backend && uv run pytest -q

check-frontend: ## API types, vue-tsc, eslint, vitest, build
	cd frontend && npm run check

dev-backend: ## Backend with reload on :8000
	cd backend && uv run uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000

dev-frontend: ## Vite dev server on :5173 (proxies /api)
	cd frontend && npm run dev

build: ## Build the frontend
	cd frontend && npm run build

serve: build ## Build the frontend and serve the app natively
	cd backend && uv run python -m app

backup: ## Back up the native SQLite database to ./backups
	cd backend && uv run python -m app.maintenance backup ../backups/assistant-$$(date +%Y%m%d-%H%M%S).sqlite3

# -- docker --
docker-build: ## Build the Docker image
	docker build -f devops/docker/Dockerfile -t devops-ai-assistant:local .

run: up ## Alias for up

up: ## Build and start the container in the background
	${COMPOSE} up -d --build --remove-orphans
	@echo "assistant: ${APP_URL}"

demo: ## Start with synthetic metrics and the fake AI provider
	METRICS_URL=synthetic://incident AI_PROVIDER=fake ${COMPOSE} up -d --build --remove-orphans
	@echo "assistant (demo): ${APP_URL}"

down: ## Stop and remove the container (keeps the data volume)
	${COMPOSE} down

stop: ## Stop the container without removing it
	${COMPOSE} stop

restart: ## Restart the running container
	${COMPOSE} restart ${SERVICE}

status: ps ## Alias for ps

ps: ## Show container status
	${COMPOSE} ps

logs: ## Follow container logs
	${COMPOSE} logs -f ${SERVICE}

shell: ## Open a shell in the running container
	${COMPOSE} exec ${SERVICE} sh

health: ## Query /api/health
	@curl -fsS ${APP_URL}/api/health && echo

docker-backup: ## Back up the container database to ./backups
	@mkdir -p backups
	${COMPOSE} exec ${SERVICE} /app/backend/.venv/bin/python -m app.maintenance backup /data/backup.sqlite3
	${COMPOSE} cp ${SERVICE}:/data/backup.sqlite3 ./backups/assistant-docker-$$(date +%Y%m%d-%H%M%S).sqlite3
	${COMPOSE} exec ${SERVICE} rm -f /data/backup.sqlite3

tunnels:
	@ssh -N -L $(MONITORING_FORWARD) $(MONITORING_HOST) & mon=$$!;
	trap 'kill $$mon $$ch 2>/dev/null' EXIT INT TERM; \
	echo "forwarding $(MONITORING_FORWARD) via $(MONITORING_HOST); Ctrl-C to close"; \
	wait
