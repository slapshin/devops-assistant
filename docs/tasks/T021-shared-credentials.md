# T021 — Shared credentials and per-kind source authentication

Dependencies: T020. Status: see [task index](README.md).

## Outcome

Many projects often use the same secret, for example one Grafana service account or one Cloudflare API token. A **shared credential** stores such a secret once. Sources of any number of projects reference it, and rotating it updates them all. Every source kind now has its own set of authentication mechanisms, so a kind can gain a mechanism (a tenant header, a client certificate, a Cloudflare global key) without affecting the others.

## Decisions (2026-10-15)

- **Per-kind auth unions** (`app/domain/auth.py`). Every source input has an `auth` field. The mechanisms per kind:

  | Kind | Mechanisms |
  | --- | --- |
  | Prometheus | `none`, `bearer`, `basic` |
  | Cloudflare | `none`, `token` |
  | Sentry | `none`, `token` |
  | Wazuh | `none`, `basic` |
  | Grafana | `none`, `token` |

  Every kind also accepts `{type: credential, credential_id}`.
  - This replaces the flat fields `api_token` (Cloudflare), `auth_token` (Sentry), `username`/`password` (Wazuh) and `token` (Grafana), so the contract changed for those kinds.
  - Read models return the same union with `token_set` / `password_set`, or `{type: credential, credential_id, name}`.
- **A credential belongs to one source kind** and holds one of that kind's secret mechanisms (`CREDENTIAL_AUTH_TYPES`). Using it from another kind is rejected with a 422 on `auth.credential_id`. A credential stores only the secret (and the basic-auth username). URL, organization and TLS settings stay on the source.
- **Storage.**
  - **`credentials` table:** name (unique, case-insensitive), kind, public auth JSON, and the Fernet-encrypted secret.
  - **`project_sources.credential_id`:** a foreign key with `ON DELETE RESTRICT`.
  - **Source configs:** `config["auth"]` holds the public part of the mechanism (`type`, `username`).
  - **When the secret is read:** a source's secret is resolved each time a connection is built, so a rotated credential applies at the next analysis or test without editing projects.
- **Credential in use.** It cannot be deleted or change its kind: `409 credential_in_use` names the projects. Changing its mechanism type, e.g. Prometheus bearer to basic, is allowed and needs the new secret.
- **Migration 0006.**
  - Creates the table and the column.
  - Rewrites the Cloudflare, Sentry, Grafana and Wazuh configs into the `auth` shape, and the pre-multi-project Sentry `project` key into `projects`.
  - Secrets are not re-encrypted, because migrations have no key. The repository still reads the old `api_token` / `auth_token` secret keys (`LEGACY_TOKEN_KEYS`) and re-keys them on the next save.
- **Existing values are shared on upgrade** (migration 0007 + `SqliteCredentialRepository.share_duplicate_secrets`).
  - Migrations cannot decrypt, so 0007 only marks `app_meta.shared_credentials_import` as `pending` when inline secrets exist.
  - Once at the next startup, inline secrets that are identical are grouped. Identical means the same kind, the same mechanism (type and username) and the same decrypted secret.
  - Every group used by two or more sources becomes one credential, named after the kind and host, e.g. "Grafana token (grafana.example.com)" or "Wazuh reader@indexer".
  - Those sources then reference the credential instead of their own copy.
  - Secrets used once, and secrets that cannot be decrypted, stay inline. The log reports how many credentials were created.
  - The import never runs again, so duplicates made later on purpose stay inline.
- **"Auth required" is checked when a source is saved, not in the model.** Cloudflare, Sentry and Grafana sources with a real URL and `none` get `422` on `sources.N.auth`, as `api_token` did before. This keeps the input models constructible without secrets.
- **Clones copy the reference, not the secret.** A source that uses a credential keeps using it in the clone.
- **Health cache.** Updating a credential drops the cached health of every project that uses it.
- **Demo.** The demo projects' Grafana sources share the "Demo Grafana token" credential, which synthetic sources ignore.

## Acceptance

- [x] `GET/POST /api/credentials` and `GET/PUT/DELETE /api/credentials/{id}`:
  - secrets are write-only, and an omitted secret keeps the stored one;
  - names are unique (`409 credential_name_taken`), and an unknown ID gives `404 credential_not_found`;
  - each kind accepts only its own mechanisms (422);
  - a credential in use cannot be deleted or change its kind (`409 credential_in_use`);
  - a credential that cannot be decrypted reports `readable: false`.
- [x] Sources reference a credential of their kind and show its name; a missing credential or one of another kind gives 422 on `auth.credential_id`.
  - Rotation reaches every project using the credential.
  - `used_by` lists those projects.
  - Clones keep the reference, and test-connection resolves credentials in drafts.
  - `credentials_readable` covers referenced credentials.
- [x] Migration 0006 converts existing source configs, and existing secrets (including old secret keys) stay usable.
- [x] On upgrade, identical existing secrets used by two or more sources become shared credentials, and those sources are linked to them.
  - Single-use, differing (other username or token) and unreadable secrets stay inline.
  - The import runs once.
  - A fresh database never schedules it.
- [x] UI:
  - every source's authentication uses one `AuthEditor`, which lists the kind's mechanisms plus *Shared credential* once one of that kind exists;
  - *Save as shared credential…* turns a typed secret into a credential and links it;
  - the `/credentials` page (rail entry) lists credentials with kind, mechanism, the projects using them and when they were last updated, and lets you create, edit (secret kept when left empty; kind locked while in use) and delete them (409 reason shown);
  - the project page names the shared credential.
- [x] `make contracts`, `make fixtures` and `make check` pass.

## Remaining limitations / follow-ups

- No new mechanisms yet (Prometheus custom header or mTLS, Cloudflare global key); the unions are ready for them.
- Identical inline secrets are merged only once, on upgrade. Duplicates created later are converted one source at a time with the UI shortcut.
- There is no access control, as for the rest of the app: anyone who can open the UI can point a source at any URL and use any credential of its kind. A credential is not bound to a URL.

## Completion record

- Completed date: 2026-10-15
- Changed files:
  - New backend files:
    - `backend/app/domain/auth.py`
    - `backend/app/domain/credentials.py`
    - `backend/app/storage/auth.py`
    - `backend/app/storage/credentials.py`
    - `backend/app/api/credentials.py`
    - `backend/migrations/versions/0006_credentials.py`, `0007_share_existing_secrets.py`
    - `backend/tests/test_credentials.py`
  - Changed backend files:
    - `app/domain/projects.py`
    - `app/storage/projects.py`, `app/storage/db.py`
    - `app/api/projects.py`, `app/api/problems.py`
    - `app/main.py`, `app/container.py`, `app/bootstrap.py`
    - `app/domain/jobs.py` (error codes), `app/domain/ids.py`
    - `scripts/export_schemas.py`, `scripts/generate_fixtures.py`
    - the source tests
  - Frontend:
    - new: `src/lib/auth.ts`, `src/components/projects/AuthEditor.vue`, `src/views/credentials/CredentialsView.vue`
    - changed: `src/lib/projects.ts`, `src/views/projects/ProjectFormView.vue`, `src/views/projects/ProjectView.vue`, `src/api/{client,queries}.ts`, `src/router.ts`, `src/components/shell/AppRail.vue`, `src/App.test.ts`
  - Generated: `docs/contracts/*` (including `Credential{Input,List}.schema.json`), `fixtures/api/credentials.json`, `frontend/src/api/schema.d.ts`.
- Commands: `make contracts`, `make fixtures`, `make check` (backend 1116 passed / 6 skipped; frontend 62 passed; build OK).
