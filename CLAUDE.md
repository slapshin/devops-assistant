# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

The project instructions live in `AGENTS.md` (shared with other coding agents) and are imported here so there is one source of truth. Update `AGENTS.md`, not this file.

@AGENTS.md

## Additional notes

- Docker layout: `devops/docker/Dockerfile` builds the image; `tools/compose/compose.yml` runs it locally (driven by `make up`/`down`/`stop`/`logs`).
- Backups: `make backup` (native SQLite) or `make docker-backup` (container volume), both into `./backups`.
- Release steps are in `docs/RELEASE_CHECKLIST.md`; API contract conventions in `docs/contracts.md`.
