"""alembic env: migrations against app.db models (async asyncpg engine).

Schemas core/gst/extraction are pre-seeded by scripts/bootstrap_stack.py; the
migration never creates or drops schemas so downgrade leaves the seeded
database structure itself intact.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from app.db import base as app_db_base
from app.db.base import Base
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The ini's sqlalchemy.url is the default; an explicit Config override (tests
# point migrations at a scratch database) wins because alembic CLI/env.py
# precedence is ini value only when nothing else set it.
app_db_base.all_models()
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a DB connection.

    Flow:
        Read sqlalchemy.url, configure Alembic with literal binds, run_migrations.

    Debug:
        Used by `alembic upgrade --sql`. Scratch-DB tests use the online path instead.
    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Run migrations on an open connection. Version table stays in public.

    Flow:
        configure(include_schemas=False, version_table_schema=public) then run_migrations.

    Debug:
        alembic_version in schema gst means this pin was bypassed. Search path $user is why.
    """
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        compare_server_default=True,
        # Every table names its schema explicitly, so autogenerate never
        # emits cross-schema noise.
        include_schemas=False,
        # Pin the version stamp to public: search_path's '$user' arm resolves
        # to the role-owned 'gst' schema, which would bury alembic_version in
        # the gst business schema.
        version_table_schema="public",
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Open a NullPool async engine and run do_run_migrations inside run_sync.

    Flow:
        engine from ini URL → connect → run_sync(do_run_migrations) → dispose.

    Debug:
        Tests override the URL on the Alembic Config before env.py runs.
    """
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Entrypoint for a real database: asyncio.run(run_async_migrations).

    Debug:
        Windows needs the selector loop policy (tests set it). Nested asyncio.run fails
        if this is called from an already-running loop.

    Flow:
        1. If Alembic is not in offline mode, this is the entrypoint.
        2. asyncio.run calls run_async_migrations, which opens the DB and applies revisions.
    """
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
