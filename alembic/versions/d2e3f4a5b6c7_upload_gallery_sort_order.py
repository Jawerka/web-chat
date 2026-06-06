"""upload gallery sort order

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-06-06 12:00:00.000000
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, Sequence[str], None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "media_assets",
        sa.Column("gallery_sort_order", sa.Integer(), nullable=True),
    )
    op.create_index(
        "ix_media_assets_gallery_sort_order",
        "media_assets",
        ["gallery_sort_order"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_media_assets_gallery_sort_order", table_name="media_assets")
    op.drop_column("media_assets", "gallery_sort_order")
