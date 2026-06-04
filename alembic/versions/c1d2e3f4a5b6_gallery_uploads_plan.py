"""gallery uploads plan: media_token, gallery_kind, favorites user_id

Revision ID: c1d2e3f4a5b6
Revises: a9b8c7d6e5f4
Create Date: 2026-06-04 12:00:00.000000
"""

from __future__ import annotations

import secrets
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, Sequence[str], None] = "a9b8c7d6e5f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("media_token", sa.LargeBinary(), nullable=True))
    op.add_column(
        "users",
        sa.Column("media_token_created_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column("media_assets", sa.Column("owner_user_id", sa.Uuid(), nullable=True))
    op.add_column("media_assets", sa.Column("gallery_kind", sa.String(length=16), nullable=True))
    op.add_column(
        "media_assets",
        sa.Column("encryption_version", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("media_assets", sa.Column("sd_prompt", sa.Text(), nullable=True))
    op.add_column("media_assets", sa.Column("sd_negative", sa.Text(), nullable=True))
    op.add_column("media_assets", sa.Column("sd_params", sa.Text(), nullable=True))
    op.add_column(
        "media_assets",
        sa.Column("sd_meta_extracted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_media_assets_owner_user_id",
        "media_assets",
        "users",
        ["owner_user_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_media_assets_owner_kind_created",
        "media_assets",
        ["owner_user_id", "gallery_kind", "created_at"],
        unique=False,
    )

    op.add_column("media_favorites", sa.Column("user_id", sa.Uuid(), nullable=True))
    op.create_foreign_key(
        "fk_media_favorites_user_id",
        "media_favorites",
        "users",
        ["user_id"],
        ["id"],
        ondelete="CASCADE",
    )

    conn = op.get_bind()
    admin_row = conn.execute(
        sa.text("SELECT id FROM users WHERE role = 'admin' ORDER BY created_at LIMIT 1"),
    ).fetchone()
    if admin_row is None:
        admin_row = conn.execute(
            sa.text("SELECT id FROM users ORDER BY created_at LIMIT 1"),
        ).fetchone()

    if admin_row is not None:
        admin_id = admin_row[0]
        conn.execute(
            sa.text(
                "UPDATE media_assets SET gallery_kind = 'generation', owner_user_id = :uid "
                "WHERE mime_type LIKE 'image/%' AND (gallery_kind IS NULL OR gallery_kind = '')",
            ),
            {"uid": admin_id},
        )
        conn.execute(
            sa.text(
                "UPDATE media_favorites SET user_id = :uid WHERE user_id IS NULL",
            ),
            {"uid": admin_id},
        )
        token = secrets.token_bytes(32)
        conn.execute(
            sa.text(
                "UPDATE users SET media_token = :tok WHERE id = :uid AND media_token IS NULL",
            ),
            {"tok": token, "uid": admin_id},
        )

    try:
        op.drop_index("uq_media_favorites_source_id", table_name="media_favorites")
    except Exception:
        pass
    op.create_index(
        "uq_media_favorites_user_source_id",
        "media_favorites",
        ["user_id", "media_source", "media_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_media_favorites_user_source_id", table_name="media_favorites")
    op.create_index(
        "uq_media_favorites_source_id",
        "media_favorites",
        ["media_source", "media_id"],
        unique=True,
    )
    op.drop_constraint("fk_media_favorites_user_id", "media_favorites", type_="foreignkey")
    op.drop_column("media_favorites", "user_id")

    op.drop_index("ix_media_assets_owner_kind_created", table_name="media_assets")
    op.drop_constraint("fk_media_assets_owner_user_id", "media_assets", type_="foreignkey")
    op.drop_column("media_assets", "sd_meta_extracted_at")
    op.drop_column("media_assets", "sd_params")
    op.drop_column("media_assets", "sd_negative")
    op.drop_column("media_assets", "sd_prompt")
    op.drop_column("media_assets", "encryption_version")
    op.drop_column("media_assets", "gallery_kind")
    op.drop_column("media_assets", "owner_user_id")

    op.drop_column("users", "media_token_created_at")
    op.drop_column("users", "media_token")
