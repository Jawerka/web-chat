"""user_auth_credentials

Revision ID: b4e8c1a92f03
Revises: 860ba0641744
Create Date: 2026-05-23 23:10:00.000000

P2.2: login, password_hash, role для users.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b4e8c1a92f03"
down_revision: Union[str, Sequence[str], None] = "860ba0641744"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.add_column(sa.Column("login", sa.String(length=64), nullable=True))
        batch_op.add_column(sa.Column("password_hash", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("role", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("is_active", sa.Boolean(), nullable=True))
        batch_op.add_column(sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))

    op.execute("UPDATE users SET login = slug WHERE login IS NULL")
    op.execute(
        "UPDATE users SET password_hash = "
        "'$2b$12$W3KcBzGgzV0mgVMxeSrWFeU5hq6FumnLBKm2Yp8QRLVyInIMIQ5h.' "
        "WHERE password_hash IS NULL",
    )
    op.execute("UPDATE users SET role = 'user' WHERE role IS NULL")
    op.execute("UPDATE users SET is_active = true WHERE is_active IS NULL")

    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.alter_column("login", nullable=False)
        batch_op.alter_column("password_hash", nullable=False)
        batch_op.alter_column("role", nullable=False)
        batch_op.alter_column("is_active", nullable=False)
        batch_op.create_index(batch_op.f("ix_users_login"), ["login"], unique=True)


def downgrade() -> None:
    with op.batch_alter_table("users", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_users_login"))
        batch_op.drop_column("last_login_at")
        batch_op.drop_column("is_active")
        batch_op.drop_column("role")
        batch_op.drop_column("password_hash")
        batch_op.drop_column("login")
