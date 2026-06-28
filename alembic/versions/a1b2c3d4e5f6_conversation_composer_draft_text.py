"""conversation composer_draft_text

Revision ID: a1b2c3d4e5f6
Revises: d2e3f4a5b6c7
Create Date: 2026-06-18 12:00:00.000000

Серверный черновик composer для handoff из внешней галереи.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "d2e3f4a5b6c7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "conversations",
        sa.Column("composer_draft_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("conversations", "composer_draft_text")
