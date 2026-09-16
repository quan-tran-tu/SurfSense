"""Add user groups and folder-to-group grants.

Three tables, all outside ``zero_publication`` (like ``shared_folders`` /
``folder_links``): the client learns about grants through REST, not Zero.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "172"
down_revision: str | None = "171"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_groups (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            name VARCHAR(100) NOT NULL,
            description VARCHAR(500),
            created_by_id UUID REFERENCES "user"(id) ON DELETE SET NULL,
            CONSTRAINT uq_user_group_name UNIQUE (name)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_group_memberships (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            group_id INTEGER NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
            user_id UUID NOT NULL REFERENCES "user"(id) ON DELETE CASCADE,
            added_by_id UUID REFERENCES "user"(id) ON DELETE SET NULL,
            CONSTRAINT uq_user_group_member UNIQUE (group_id, user_id)
        );
        """
    )
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS folder_group_grants (
            id SERIAL PRIMARY KEY,
            created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            group_id INTEGER NOT NULL REFERENCES user_groups(id) ON DELETE CASCADE,
            folder_id INTEGER NOT NULL REFERENCES folders(id) ON DELETE CASCADE,
            granted_by_id UUID REFERENCES "user"(id) ON DELETE SET NULL,
            CONSTRAINT uq_folder_group_grant UNIQUE (group_id, folder_id)
        );
        """
    )
    for table, column in (
        ("user_groups", "created_at"),
        ("user_groups", "name"),
        ("user_groups", "created_by_id"),
        ("user_group_memberships", "created_at"),
        ("user_group_memberships", "group_id"),
        ("user_group_memberships", "user_id"),
        ("folder_group_grants", "created_at"),
        ("folder_group_grants", "group_id"),
        ("folder_group_grants", "folder_id"),
    ):
        op.execute(
            f"CREATE INDEX IF NOT EXISTS ix_{table}_{column} ON {table}({column});"
        )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS folder_group_grants CASCADE;")
    op.execute("DROP TABLE IF EXISTS user_group_memberships CASCADE;")
    op.execute("DROP TABLE IF EXISTS user_groups CASCADE;")
