"""Schedule turning existing duplicate source secrets into shared credentials (T021).

Grouping secrets needs them decrypted, which migrations cannot do (no key), so this only sets
``app_meta.shared_credentials_import`` to ``pending`` when inline secrets exist; the import runs
once at startup (``SqliteCredentialRepository.share_duplicate_secrets``).

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"

SHARED_IMPORT_KEY = "shared_credentials_import"


def upgrade() -> None:
    conn = op.get_bind()
    inline = conn.execute(
        sa.text("SELECT 1 FROM project_sources WHERE secrets IS NOT NULL LIMIT 1")
    ).first()
    if inline is not None:
        conn.execute(
            sa.text("INSERT OR IGNORE INTO app_meta (key, value) VALUES (:key, 'pending')"),
            {"key": SHARED_IMPORT_KEY},
        )
