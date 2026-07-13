"""Add shared_folders and folder_links tables for cross-user folder sharing."""

from collections.abc import Sequence

from alembic import op

revision: str = "170"
down_revision: str | None = "169"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS shared_folders (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            token VARCHAR(64) NOT NULL UNIQUE,
            source_folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
            source_search_space_id INTEGER NOT NULL REFERENCES searchspaces(id) ON DELETE CASCADE,
            created_by_id UUID REFERENCES "user"(id) ON DELETE SET NULL,
            expires_at TIMESTAMP WITH TIME ZONE,
            max_uses INTEGER,
            uses_count INTEGER NOT NULL DEFAULT 0,
            revoked_at TIMESTAMP WITH TIME ZONE,
            name VARCHAR(100)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS folder_links (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            share_id INTEGER NOT NULL REFERENCES shared_folders(id) ON DELETE CASCADE,
            source_folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
            target_search_space_id INTEGER NOT NULL REFERENCES searchspaces(id) ON DELETE CASCADE,
            created_by_id UUID REFERENCES "user"(id) ON DELETE SET NULL,
            CONSTRAINT uq_folder_link_target_source
                UNIQUE (target_search_space_id, source_folder_id)
        );
        """
    )
    for table, column in (
        ("shared_folders", "created_at"),
        ("shared_folders", "token"),
        ("shared_folders", "source_folder_id"),
        ("shared_folders", "source_search_space_id"),
        ("shared_folders", "created_by_id"),
        ("folder_links", "created_at"),
        ("folder_links", "share_id"),
        ("folder_links", "source_folder_id"),
        ("folder_links", "target_search_space_id"),
        ("folder_links", "created_by_id"),
    ):
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_{column} ON {table}({column});"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS folder_links CASCADE;")
    op.execute("DROP TABLE IF EXISTS shared_folders CASCADE;")
