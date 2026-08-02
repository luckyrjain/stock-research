import os
from logging.config import fileConfig

from sqlalchemy import engine_from_config
from sqlalchemy import pool

from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
# disable_existing_loggers=False: fileConfig()'s default (True) permanently
# disables every already-created logger not named in alembic.ini's [loggers]
# section (root/sqlalchemy/alembic only) — including every tools.*/signals.*
# module logger the app itself already set up before this ever runs, with no
# way to re-enable them afterward. Confirmed via test bisection: running
# Alembic migrations in-process (tests/test_alembic_migrations.py) silently
# broke assertLogs-based tests in unrelated modules for the rest of the
# pytest session. Same risk applies outside tests — any script that imports
# this app's modules before calling into Alembic would have its logging
# silently killed app-wide.
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

# db.models.metadata is the single source of truth for every table this app
# already hand-declares (SQLAlchemy Core, not an ORM Base) — reusing it here
# is what makes `alembic revision --autogenerate` possible at all, and means
# a schema change only ever needs to touch db/models.py, never a second
# Alembic-specific model definition that could drift from it.
from db.models import metadata as target_metadata  # noqa: E402

# DATABASE_URL is the one env var every other DB-backed module in this
# codebase already reads (db/models.py::get_engine, cache-backed endpoints
# in api.py, the batch pipelines) — alembic.ini's own sqlalchemy.url is left
# blank deliberately so there's exactly one place a deployment configures
# its connection string. Raises with a clear message rather than silently
# falling through to alembic.ini's unset default and failing on a confusing
# "driver://user:pass@localhost/dbname" connection attempt.
def _database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Alembic reads the same DATABASE_URL "
            "env var as the rest of this app (see db/models.py::get_engine) "
            "— set it before running `alembic upgrade`/`alembic revision`."
        )
    return url


# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _database_url()
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
