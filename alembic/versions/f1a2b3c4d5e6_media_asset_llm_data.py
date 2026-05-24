"""media_asset_llm_data

Revision ID: f1a2b3c4d5e6
Revises: e7f8a9b0c1d2
Create Date: 2026-05-24 18:00:00.000000

Кэш сжатых байтов для vision API (/llm).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "f1a2b3c4d5e6"
down_revision: Union[str, Sequence[str], None] = "e7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "media_assets",
        sa.Column("llm_data", sa.LargeBinary(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("media_assets", "llm_data")
