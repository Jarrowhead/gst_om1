"""aato_latest_minor to BigInteger

Revision ID: 6c4b85f9c8d2
Revises: a41c7e2d90f5
Create Date: 2026-09-27

TECHNICAL_ARCHITECTURE.md §3 and AI_BUILD_PLAYBOOK hard rule #2:
money columns store integer paise. `gst_registrations.aato_latest_minor`
represents AATO in paise and must accommodate values above 2^31-1, so it is
being widened from Integer to BigInteger.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "6c4b85f9c8d2"
down_revision: str | Sequence[str] | None = "a41c7e2d90f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Widen core.gst_registrations.aato_latest_minor from Integer to BigInteger.

    Flow:
        alter_column in schema core. Paise amounts above 2^31-1 must fit.

    Debug:
        Downgrade zeroes out-of-range values before narrowing. Do not downgrade production data.
    """
    op.alter_column(
        "gst_registrations",
        "aato_latest_minor",
        schema="core",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        existing_nullable=False,
    )


def downgrade() -> None:
    """Narrow aato_latest_minor back to Integer after zeroing values outside 32-bit range.

    Flow:
        UPDATE rows outside ±2147483647 to 0, then alter_column to Integer.

    Debug:
        Data loss is intentional so the type change can reverse on a dev database.
    """
    conn = op.get_bind()
    # AATO values may exceed 32-bit int after forward tests; truncate to 0
    # before narrowing the column so the downgrade stays reversible.
    conn.exec_driver_sql(
        "UPDATE core.gst_registrations SET aato_latest_minor = 0 "
        "WHERE aato_latest_minor > 2147483647 OR aato_latest_minor < -2147483648"
    )
    op.alter_column(
        "gst_registrations",
        "aato_latest_minor",
        schema="core",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        existing_nullable=False,
    )
