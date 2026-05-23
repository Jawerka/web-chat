"""document_chunks_rag

Revision ID: d1a2b3c4e5f6
Revises: b4e8c1a92f03
Create Date: 2026-05-23 23:45:00.000000

P2.3: фрагменты документов для semantic search.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d1a2b3c4e5f6"
down_revision: Union[str, Sequence[str], None] = "b4e8c1a92f03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "document_chunks",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("attachment_id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["attachment_id"], ["attachments.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_document_chunks_attachment_id"), "document_chunks", ["attachment_id"], unique=False)
    op.create_index(op.f("ix_document_chunks_conversation_id"), "document_chunks", ["conversation_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_document_chunks_conversation_id"), table_name="document_chunks")
    op.drop_index(op.f("ix_document_chunks_attachment_id"), table_name="document_chunks")
    op.drop_table("document_chunks")
