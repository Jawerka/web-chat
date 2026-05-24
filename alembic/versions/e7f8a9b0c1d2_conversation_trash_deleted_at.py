"""conversation_trash_deleted_at

Revision ID: e7f8a9b0c1d2
Revises: d1a2b3c4e5f6
Create Date: 2026-05-24 14:00:00.000000

Корзина бесед: soft delete через deleted_at.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, Sequence[str], None] = "d1a2b3c4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_conversations_deleted_at",
        "conversations",
        ["deleted_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_conversations_deleted_at", table_name="conversations")
    op.drop_column("conversations", "deleted_at")
