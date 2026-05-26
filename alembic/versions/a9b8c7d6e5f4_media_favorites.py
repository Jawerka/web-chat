"""media_favorites

Revision ID: a9b8c7d6e5f4
Revises: f1a2b3c4d5e6
Create Date: 2026-05-26 20:55:00.000000

Избранные изображения для галереи и lightbox.
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "a9b8c7d6e5f4"
down_revision: Union[str, Sequence[str], None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "media_favorites",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("media_source", sa.String(length=16), nullable=False),
        sa.Column("media_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_media_favorites_media_source"), "media_favorites", ["media_source"], unique=False)
    op.create_index(op.f("ix_media_favorites_media_id"), "media_favorites", ["media_id"], unique=False)
    op.create_index(
        "uq_media_favorites_source_id",
        "media_favorites",
        ["media_source", "media_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_media_favorites_source_id", table_name="media_favorites")
    op.drop_index(op.f("ix_media_favorites_media_id"), table_name="media_favorites")
    op.drop_index(op.f("ix_media_favorites_media_source"), table_name="media_favorites")
    op.drop_table("media_favorites")

