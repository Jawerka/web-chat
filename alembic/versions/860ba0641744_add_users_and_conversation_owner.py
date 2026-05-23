"""add_users_and_conversation_owner

Revision ID: 860ba0641744
Revises: 2d462089f839
Create Date: 2026-05-23 21:49:27.251452

P2.2: пользователи и владелец беседы.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "860ba0641744"
down_revision: Union[str, Sequence[str], None] = "2d462089f839"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=120), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_users_slug"), ["slug"], unique=True)

    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.add_column(sa.Column("owner_user_id", sa.Uuid(), nullable=True))
        batch_op.create_index(
            batch_op.f("ix_conversations_owner_user_id"),
            ["owner_user_id"],
            unique=False,
        )
        batch_op.create_foreign_key(
            "fk_conversations_owner_user_id_users",
            "users",
            ["owner_user_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("conversations", schema=None) as batch_op:
        batch_op.drop_constraint("fk_conversations_owner_user_id_users", type_="foreignkey")
        batch_op.drop_index(batch_op.f("ix_conversations_owner_user_id"))
        batch_op.drop_column("owner_user_id")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_slug"))
    op.drop_table("users")
