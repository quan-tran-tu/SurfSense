"""add folders.promoted_from_thread_id and the app_settings table

``promoted_from_thread_id`` remembers which chat session a promoted folder came
from, so the promotion can be undone (demoted back to that session). NULL for
folders uploaded space-wide, and set to NULL when that chat is deleted — there
is then no session left to demote into.

``app_settings`` holds deployment-wide settings an admin changes from the UI
(today: folder retention), overriding the env defaults. Outside
``zero_publication``, like the other admin tables.

Revision ID: 173
Revises: 172
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "173"
down_revision: str | None = "172"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    existing_columns = [col["name"] for col in inspector.get_columns("folders")]
    if "promoted_from_thread_id" not in existing_columns:
        op.add_column(
            "folders",
            sa.Column(
                "promoted_from_thread_id",
                sa.Integer(),
                sa.ForeignKey(
                    "new_chat_threads.id",
                    ondelete="SET NULL",
                    name="fk_folders_promoted_from_thread_id",
                ),
                nullable=True,
            ),
        )
        op.create_index(
            "ix_folders_promoted_from_thread_id", "folders", ["promoted_from_thread_id"]
        )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS app_settings (
            key VARCHAR(100) PRIMARY KEY,
            value JSONB NOT NULL,
            updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
            updated_by_id UUID REFERENCES "user"(id) ON DELETE SET NULL
        );
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS app_settings;")
    op.drop_index("ix_folders_promoted_from_thread_id", table_name="folders")
    op.drop_column("folders", "promoted_from_thread_id")
