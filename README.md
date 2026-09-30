# DevOps AI Assistant

A web assistant that analyses the latest 24 hours of Prometheus-compatible metrics for one `project`/`env` scope. It compares them against preceding history, shows a two-week anomaly trend, and adds replaceable AI explanations (OpenAI first).

**Status:** the foundations are in place (T001–T002). Discovery, detection, the API behaviour, and the UI views are not implemented yet: routes return `501 not_implemented` and UI views say "Not implemented yet". See the [plan](PLAN.md) and the [task index](docs/tasks/README.md).

## Requirements

- [uv](https://docs.astral.sh/uv/) 0.12+ (installs/uses Python 3.14.7 from `backend/.python-version`)
- Node.js 24.21 LTS (`frontend/.nvmrc`)

## Commands

```sh
make install        # uv sync --locked; npm ci
make check          # lint, types, contract/fixture drift, tests, frontend build
make dev-backend    # http://127.0.0.1:8000/api/docs
make dev-frontend   # http://localhost:5173 (proxies /api to the backend)
make contracts      # regenerate JSON Schema, OpenAPI, and frontend API types
make fixtures       # regenerate synthetic fixtures
```

Configuration: copy `.env.example` to `backend/.env`. Neither a metrics connection nor an OpenAI key is needed to build, test, or validate contracts. Invalid settings stop startup with a message naming the variable.

## Documentation

- [Product plan](docs/PRODUCT_PLAN.md) · [Architecture](docs/ARCHITECTURE.md)
- [Decisions](docs/DECISIONS.md) · [UI specification](docs/UI_SPEC.md) · [Shared contracts](docs/contracts.md)
