"""invoice_drafts: nullable extraction_job_id + source column (task 11.1)

Revision ID: c4a1b7e93d21
Revises: 8483441721c7
Create Date: 2026-10-10

Bulk Excel/CSV import (PHASE11_13_RECOMMENDATION_BUILD.md rec 1) ingests
spreadsheet rows straight into `extraction.invoice_drafts`. Such a row has no
OCR extraction job — the file is already structured data — so the draft's
`extraction_job_id` must be nullable, and a `source` column records which
population a draft belongs to.

Additive only: no column is dropped, no existing row is rewritten. Existing
rows keep source='EXTRACTION' via the server default.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "c4a1b7e93d21"
down_revision: str | Sequence[str] | None = "8483441721c7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DRAFT_SOURCE_EXTRACTION = "EXTRACTION"


def upgrade() -> None:
    op.alter_column(
        "invoice_drafts",
        "extraction_job_id",
        existing_type=sa.Uuid(),
        nullable=True,
        schema="extraction",
    )
    op.add_column(
        "invoice_drafts",
        sa.Column(
            "source",
            sa.String(length=16),
            nullable=False,
            server_default=DRAFT_SOURCE_EXTRACTION,
        ),
        schema="extraction",
    )


def downgrade() -> None:
    # A spreadsheet-imported draft has extraction_job_id IS NULL and cannot
    # satisfy the restored NOT NULL constraint. Refuse loudly instead of
    # silently deleting filing data: an operator must explicitly remove those
    # drafts first (they are drafts, never filed originals).
    op.execute(
        sa.text(
            "DO $$ BEGIN "
            "IF EXISTS (SELECT 1 FROM extraction.invoice_drafts "
            "WHERE extraction_job_id IS NULL) THEN "
            "RAISE EXCEPTION 'cannot downgrade c4a1b7e93d21: bulk-imported "
            "invoice_drafts exist (extraction_job_id IS NULL); delete them first'; "
            "END IF; END $$;"
        )
    )
    op.drop_column("invoice_drafts", "source", schema="extraction")
    op.alter_column(
        "invoice_drafts",
        "extraction_job_id",
        existing_type=sa.Uuid(),
        nullable=False,
        schema="extraction",
    )
