# Repository checks. Requires uv and Node.js 24.21 LTS (see docs/DECISIONS.md).
-include ./config.env
export

COMPOSE_ARGS=-f tools/compose/compose.yml

.PHONY: install check check-backend check-frontend contracts fixtures dev-backend dev-frontend build serve docker-build up down stop logs backup

install:
	cd backend && uv sync --locked
	cd frontend && npm ci

contracts:
	cd backend && uv run python -m scripts.export_schemas
	cd frontend && npm run gen:api

fixtures:
	cd backend && uv run python -m scripts.generate_fixtures

check: check-backend check-frontend

check-backend:
	cd backend && uv run ruff format --check .
	cd backend && uv run ruff check .
	cd backend && uv run mypy
	cd backend && uv run python -m scripts.export_schemas --check
	cd backend && uv run python -m scripts.generate_fixtures --check
	cd backend && uv run pytest -q

check-frontend:
	cd frontend && npm run check

dev-backend:
	cd backend && uv run uvicorn app.main:create_app --factory --reload --host 127.0.0.1 --port 8000

dev-frontend:
	cd frontend && npm run dev

build:
	cd frontend && npm run build

serve: build
	cd backend && uv run python -m app

# -- compose --
docker-build:
	docker build -f devops/docker/Dockerfile -t devops-ai-assistant:local .

up:
	docker compose ${COMPOSE_ARGS} up -d --build --remove-orphans

down:
	docker compose ${COMPOSE_ARGS} down

stop:
	docker compose ${COMPOSE_ARGS} stop

logs:
	docker compose ${COMPOSE_ARGS} logs -f assistant

backup:
	cd backend && uv run python -m app.maintenance backup ../backups/assistant-$$(date +%Y%m%d-%H%M%S).sqlite3
