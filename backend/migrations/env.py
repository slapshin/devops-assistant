"""Alembic environment: uses the connection passed by app.storage.db.migrate."""

from alembic import context

from app.storage.db import metadata

connection = context.config.attributes.get("connection")
if connection is None:  # pragma: no cover - offline generation is not used
    raise RuntimeError("run migrations through app.storage.db.migrate")
context.configure(connection=connection, target_metadata=metadata, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
