"""add folders.owner_thread_id for per-chat-session folder scope

NULL = space-wide (previous behavior for every existing row); a thread id
scopes the folder subtree to that chat session. The unique index on
(search_space_id, COALESCE(parent_id, 0), name) must widen to include the
owner, or two sessions could never upload a same-named root folder — the
whole point of session scoping.

Revision ID: 171
Revises: 170
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "171"
down_revision: str | None = "170"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_columns = [col["name"] for col in inspector.get_columns("folders")]
    if "owner_thread_id" not in existing_columns:
        op.add_column(
            "folders",
            sa.Column(
                "owner_thread_id",
                sa.Integer(),
                sa.ForeignKey(
                    "new_chat_threads.id",
                    ondelete="SET NULL",
                    name="fk_folders_owner_thread_id",
                ),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_folders_owner_thread_id", "folders", ["owner_thread_id"]
        )

    op.execute("DROP INDEX IF EXISTS uq_folder_space_parent_name;")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_folder_space_parent_name
        ON folders (
            search_space_id,
            COALESCE(parent_id, 0),
            name,
            COALESCE(owner_thread_id, 0)
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_folder_space_parent_name;")
    op.execute(
        """
        CREATE UNIQUE INDEX uq_folder_space_parent_name
        ON folders (search_space_id, COALESCE(parent_id, 0), name);
        """
    )
    op.drop_index("ix_folders_owner_thread_id", table_name="folders")
    op.drop_column("folders", "owner_thread_id")
