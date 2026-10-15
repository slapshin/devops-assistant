"""Sources keep secrets only in credentials (T021).

Sources still storing their own secret are moved into credentials by the startup import
(``SqliteCredentialRepository.move_inline_secrets``), which needs the key; this marks it
``pending`` again when such sources exist, also after the duplicate-only import of 0007 ran.

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"

INLINE_IMPORT_KEY = "shared_credentials_import"


def upgrade() -> None:
    conn = op.get_bind()
    inline = conn.execute(
        sa.text("SELECT 1 FROM project_sources WHERE secrets IS NOT NULL LIMIT 1")
    ).first()
    if inline is not None:
        conn.execute(
            sa.text("INSERT OR REPLACE INTO app_meta (key, value) VALUES (:key, 'pending')"),
            {"key": INLINE_IMPORT_KEY},
        )
