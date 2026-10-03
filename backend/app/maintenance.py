"""Explicit, operator-run maintenance. Nothing here runs automatically.

uv run python -m app.maintenance backup <file>            # consistent online copy
uv run python -m app.maintenance prune --older-than 90    # dry run: lists what would go
uv run python -m app.maintenance prune --older-than 90 --yes
"""

import argparse
import sqlite3
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.domain.common import format_utc
from app.settings import ConfigError, load_settings
from app.storage.repository import ACTIVE, SqliteReportRepository

CONFIG_ERROR_EXIT_CODE = 2


def backup(db: Path, target: Path) -> int:
    if target.exists():
        print(f"Refusing to overwrite {target}", file=sys.stderr)
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db) as src, sqlite3.connect(target) as dst:
        src.backup(dst)  # safe while the application is running (WAL)
    print(f"Backed up {db} -> {target}")
    return 0


def prune(db: Path, days: int, yes: bool) -> int:
    SqliteReportRepository(db).close()  # ensures migrations are applied
    cutoff = format_utc(datetime.now(UTC) - timedelta(days=days))

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        rows = _finished_before(conn, cutoff)
        if not rows:
            print(f"Nothing older than {days} days ({cutoff}).")
            return 0

        verb = "delete" if yes else "would delete"
        for analysis_id, project, end_time, state, created_at in rows:
            print(
                f"{verb} {analysis_id} project={project!r} end={end_time} "
                f"state={state} created={created_at}"
            )
        if not yes:
            print(f"{len(rows)} analyses would be deleted. Re-run with --yes to delete.")
            return 0

        ids = [row[0] for row in rows]
        marks = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM reports WHERE analysis_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM analysis_jobs WHERE analysis_id IN ({marks})", ids)

    # VACUUM cannot run inside an open transaction, so it runs after the deletes commit.
    with sqlite3.connect(db) as conn:
        conn.execute("VACUUM")
    print(f"Deleted {len(rows)} analyses.")
    return 0


def _finished_before(conn: sqlite3.Connection, cutoff: str) -> list[tuple[str, ...]]:
    placeholders = ",".join("?" for _ in ACTIVE)
    rows: list[tuple[str, ...]] = conn.execute(
        "SELECT j.analysis_id, p.name, j.end_time, j.state, j.created_at FROM analysis_jobs j "
        "JOIN projects p ON p.project_id = j.project_id "
        f"WHERE j.created_at < ? AND j.state NOT IN ({placeholders}) ORDER BY j.created_at",
        (cutoff, *ACTIVE),
    ).fetchall()
    return rows


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.maintenance")
    commands = parser.add_subparsers(dest="command", required=True)
    backup_parser = commands.add_parser("backup", help="copy the database to a new file")
    backup_parser.add_argument("target", type=Path)
    prune_parser = commands.add_parser("prune", help="delete finished analyses older than N days")
    prune_parser.add_argument("--older-than", type=int, required=True, metavar="DAYS")
    prune_parser.add_argument(
        "--yes", action="store_true", help="actually delete (default: dry run)"
    )
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return CONFIG_ERROR_EXIT_CODE

    if args.command == "backup":
        return backup(settings.database_path, args.target)
    return prune(settings.database_path, args.older_than, args.yes)


if __name__ == "__main__":
    raise SystemExit(main())
