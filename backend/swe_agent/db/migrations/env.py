"""Alembic environment (sync, psycopg 3)."""

from __future__ import annotations

from alembic import context
from sqlalchemy import engine_from_config, pool

from swe_agent.db.models import Base

config = context.config
target_metadata = Base.metadata


def _include(
    obj: object, name: str | None, type_: str, reflected: bool, compare_to: object
) -> bool:
    # LangGraph's checkpoint tables are managed by its own saver, never by our migrations.
    return not (type_ == "table" and name is not None and name.startswith("checkpoint"))


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        include_object=_include,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata, include_object=_include
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
