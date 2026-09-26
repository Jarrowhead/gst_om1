"""SQLAlchemy 2 declarative base with GST-schema-aware naming conventions."""

from __future__ import annotations

from types import ModuleType

from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION: dict[str, str] = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by every model in core/gst/extraction schemas."""

    metadata = MetaData(
        naming_convention=NAMING_CONVENTION,
        # Schemas are seeded by scripts/bootstrap_stack.py; migrations only
        # create tables inside them.
        info={"schemas": ("core", "gst", "extraction")},
    )


def all_models() -> list[ModuleType]:
    """Import every model module so its tables register on Base.metadata.

    Call this before alembic autogenerate or create_all.
    """
    from app.db.models import core, extraction, gst  # noqa: F401

    return [core, extraction, gst]
