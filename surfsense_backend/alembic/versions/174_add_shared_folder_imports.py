"""add shared_folder_imports: everyone a share token ever reached

``folder_links`` rows are deleted when an import is removed or a token is
revoked by an admin, so they can't say who *used* a token. This table keeps one
row per (share, importing space) for good, with the first and latest import and
how the link was dropped. Backfilled from the links that exist today.

Outside ``zero_publication``, like ``shared_folders`` / ``folder_links``.

Revision ID: 174
Revises: 173
"""

from collections.abc import Sequence

from alembic import op

revision: str = "174"
down_revision: str | None = "173"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS shared_folder_imports (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            share_id INTEGER NOT NULL REFERENCES shared_folders(id) ON DELETE CASCADE,
            target_search_space_id INTEGER NOT NULL REFERENCES searchspaces(id) ON DELETE CASCADE,
            user_id UUID REFERENCES "user"(id) ON DELETE SET NULL,
            last_imported_at TIMESTAMP WITH TIME ZONE NOT NULL,
            import_count INTEGER NOT NULL DEFAULT 1,
            stopped_at TIMESTAMP WITH TIME ZONE,
            stop_reason VARCHAR(32),
            CONSTRAINT uq_shared_folder_import_share_target
                UNIQUE (share_id, target_search_space_id)
        );
        """
    )
    # Plain CREATE INDEX: zero-cache's DDL trigger rejects CONCURRENTLY.
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_shared_folder_imports_share_id "
        "ON shared_folder_imports (share_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_shared_folder_imports_target_search_space_id "
        "ON shared_folder_imports (target_search_space_id);"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_shared_folder_imports_created_at "
        "ON shared_folder_imports (created_at);"
    )
    op.execute(
        """
        INSERT INTO shared_folder_imports
            (created_at, share_id, target_search_space_id, user_id, last_imported_at)
        SELECT created_at, share_id, target_search_space_id, created_by_id, created_at
        FROM folder_links
        ON CONFLICT (share_id, target_search_space_id) DO NOTHING;
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS shared_folder_imports;")
