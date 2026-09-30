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
        placeholders = ",".join("?" for _ in ACTIVE)
        rows = conn.execute(
            "SELECT analysis_id, project, env, end_time, state, created_at FROM analysis_jobs "
            f"WHERE created_at < ? AND state NOT IN ({placeholders}) ORDER BY created_at",
            (cutoff, *ACTIVE),
        ).fetchall()
        if not rows:
            print(f"Nothing older than {days} days ({cutoff}).")
            return 0
        for r in rows:
            print(
                f"{'delete' if yes else 'would delete'} {r[0]} {r[1]}/{r[2]} end={r[3]} "
                f"state={r[4]} created={r[5]}"
            )
        if not yes:
            print(f"{len(rows)} analyses would be deleted. Re-run with --yes to delete.")
            return 0
        ids = [r[0] for r in rows]
        marks = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM reports WHERE analysis_id IN ({marks})", ids)
        conn.execute(f"DELETE FROM analysis_jobs WHERE analysis_id IN ({marks})", ids)
    with sqlite3.connect(db) as conn:
        conn.execute("VACUUM")
    print(f"Deleted {len(rows)} analyses.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.maintenance")
    sub = parser.add_subparsers(dest="command", required=True)
    b = sub.add_parser("backup", help="copy the database to a new file")
    b.add_argument("target", type=Path)
    p = sub.add_parser("prune", help="delete finished analyses older than N days")
    p.add_argument("--older-than", type=int, required=True, metavar="DAYS")
    p.add_argument("--yes", action="store_true", help="actually delete (default: dry run)")
    args = parser.parse_args(argv)
    try:
        settings = load_settings()
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return 2
    if args.command == "backup":
        return backup(settings.database_path, args.target)
    return prune(settings.database_path, args.older_than, args.yes)


if __name__ == "__main__":
    raise SystemExit(main())
