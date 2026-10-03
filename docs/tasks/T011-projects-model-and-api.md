# T011 — Project model, encrypted source config, and project API

Dependencies: T010. Status: see [task index](README.md).

## Outcome

A **project** is a stored, user-managed entity that holds the configuration of everything analysed for it. Today that is one Prometheus-compatible metrics source plus a list of label matchers that select the project's series. The model is shaped so that more source kinds (Wazuh, Cloudflare, Sentry, …) can be added later without a schema migration. Projects are created, edited, tested, and deleted through the API.

## Owner decisions (2026-10-03)

- One project is one analysis scope. The matchers fully define it (e.g. `project="shop", env="prod"`), and there is no env picker.
- The label filter is a list of **equality** matchers (`name="value"`). Regex and negative matchers are not supported.
- Projects are managed in the UI and stored in SQLite. Secrets are encrypted at rest and are never returned by the API.
- The encryption key is generated automatically on first start into `DATA_DIR/secret.key` (mode 0600). `SECRET_KEY` overrides it.
- Deletion is a hard delete that also removes the project's jobs and reports (see T012 for the cascade).
- The project form has a **Test connection** action: it checks reachability and auth, counts matching series, and shows the available signal families.

## Ownership

`backend/app/domain/projects.py` (new), `backend/app/storage/` (projects tables, secrets), `backend/app/api/routes.py` (project routes), `backend/migrations/versions/0002_*`, contracts/fixtures regeneration.

## Work

### Domain (`app/domain/projects.py`)

- `LabelMatcher {name, value}`. `name` must match `[a-zA-Z_][a-zA-Z0-9_]*` and must not be `__name__` or start with `__`. `value` is a non-empty `LabelValue`. A project has 1–10 matchers with unique names, stored sorted by name.
- `SourceKind` StrEnum with `prometheus` only for now. Each source config is a discriminated union member keyed by `kind`, and a project holds `sources: list[SourceConfig]` with at most one source per kind. Adding Wazuh later means adding a union member and an adapter, with no DB change.
- `PrometheusSourceConfig`:
  - `url`: http(s), or `synthetic://<scenario>` for demos and tests. It uses the same validation as today's `METRICS_URL`: no credentials, query string, or fragment in the URL, and the path prefix is preserved.
  - `tls_verify`.
  - `auth`: a discriminated union of `none`, `bearer {token}`, and `basic {username, password}`.
- Separate write and read contracts:
  - Write (`ProjectCreate`, `ProjectUpdate`): secret fields are `SecretStr | None`. On update, an omitted secret keeps the stored value. Changing the auth type requires the new secret.
  - Read (`Project`): secrets are replaced by `has_token` / `has_password: bool`. `url_display` contains only scheme, host, and path.
- `Project {project_id (UUIDv7), name (unique, case-insensitive, 1–100 chars), description?, matchers, sources, created_at, updated_at}`.

### Storage

- Migration `0002` adds:
  - `projects (project_id PK, name UNIQUE NOCASE, description, matchers JSON, created_at, updated_at)`
  - `project_sources (project_id FK ON DELETE CASCADE, kind, config JSON without secrets, secrets BLOB encrypted, updated_at, PK(project_id, kind))`
- `app/storage/secrets.py`: Fernet from `cryptography` (add it pinned exactly to `pyproject.toml` and lock it). Load the key from `SECRET_KEY` or `DATA_DIR/secret.key`. If neither exists, create the file atomically with mode 0600 and log a backup reminder once. If a stored secret cannot be decrypted (wrong or lost key), only that project is marked `credentials_unreadable`. The app still starts, and the user re-enters the secret.
- `ProjectRepository` Protocol in `app/domain/interfaces.py` with a SQLAlchemy Core implementation: list, get, create, update, delete. Secrets are decrypted only inside the source factory (T012) and never put into a domain read model.

### API (`/api/projects`)

| Method | Path | Notes |
| --- | --- | --- |
| GET | `/projects` | `ProjectList` of `ProjectSummary` (T012 adds the latest-analysis and trend fields) |
| POST | `/projects` | 201, returns `Project`. Name conflict → 409 `project_name_taken` |
| GET | `/projects/{project_id}` | 404 `project_not_found` |
| PUT | `/projects/{project_id}` | Full replace. Omitted secrets are kept |
| DELETE | `/projects/{project_id}` | 204. An active job → 409 `project_busy` (cancel it first) |
| POST | `/projects/test-connection` | Body: a draft config, optionally with `project_id` to reuse stored secrets. Returns `ConnectionTest` |
| GET | `/projects/{project_id}/health` | Cached result of the last connection test (TTL 60 s), for the list page |

- `ConnectionTest {reachable, auth_ok, matched_series, history_days, families: [{family, status, reason}], message, checked_at}`. It reuses capability discovery from `PrometheusMetricsSource.capabilities`, is bounded by the existing query timeout, and is read-only. Error messages must never echo secrets.
- The old label-discovery routes `GET /projects` (label values) and `GET /projects/{p}/envs` are replaced. `ENV_NOT_FOUND` is removed from `ErrorCode`.
- Optional helper for the form: `POST /projects/label-values {source, label, matchers}` lists the values of a label under the matchers already entered (truncated, bounded), so the user can pick a value instead of typing it.

## Acceptance

- [x] Project CRUD round-trips through the API and persists across restart.
- [x] Secrets are stored encrypted (verified by reading the raw DB row), are never present in any API response, log line, or fixture, and an omitted secret on update keeps the stored value.
- [x] A missing or wrong key degrades only the affected projects, with an actionable message.
- [x] Invalid matchers (bad name, `__name__`, duplicates, empty value) and invalid URLs return `validation_error` with field paths.
- [x] Test connection reports reachability, auth failure, zero matching series, and family capabilities correctly against a mocked HTTP source and `synthetic://`.
- [x] `make contracts`, `make fixtures`, and `make check` pass.

## Completion record

- Completed date: 2026-10-03
- Actual changed files and artifacts:
  - new: `backend/app/domain/projects.py`, `backend/app/storage/projects.py`, `backend/app/storage/secrets.py`, `backend/app/api/projects.py`, `backend/app/metrics/probe.py`, `backend/migrations/versions/0002_projects.py`, `backend/tests/test_projects.py`
  - changed: `app/settings.py` (shared URL validator, `SECRET_KEY`), `app/metrics/client.py` (`from_connection`), `app/metrics/promql.py` (`render_matchers`), `app/storage/db.py`, `app/container.py`, `app/main.py`, `app/domain/jobs.py` (new error codes, `DiscoveredProjectList`), `app/api/routes.py`
  - `cryptography==50.0.2` added (`pyproject.toml`, `uv.lock`)
  - regenerated contracts, `frontend/src/api/schema.d.ts`, fixtures (`api/projects.json` is now the project list; the label list moved to `api/discovered_projects.json`; new `api/connection_test.json`)
  - frontend: legacy discovery paths in `src/api/queries.ts`, `src/api/client.ts`, `src/App.test.ts`
  - docs: `DECISIONS.md` §2–§3, `OPERATIONS.md` (`SECRET_KEY`, backing up the key), `config.env.template`
- Commands/checks and results: `make contracts`, `make fixtures`, `make check` pass (293 backend tests passed, 2 skipped; 20 frontend tests; build).
- Decisions or dependency changes:
  - The old label-discovery routes moved to `/api/discovery/projects[/{project}/envs]` so the current UI keeps working until T012/T013 remove them.
  - `ConnectionTest.families` is part of the contract but is filled by T012. Capability discovery is still keyed by `Scope(project, env)` until then. The T011 probe measures reachability, auth, matching series, and history from the matchers directly.
  - `SourceInput` is a single model for now. It becomes a discriminated union on `kind` when a second source kind is added.
- Remaining limitations or blockers: none.
