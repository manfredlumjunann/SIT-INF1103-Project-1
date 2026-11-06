"""Alembic environment: connects using DATABASE_URL and compares against the
SQLAlchemy models in data_manager.py.

Run from the data-layer folder (or pass -c path/to/alembic.ini):
    alembic upgrade head                              # apply all migrations
    alembic revision --autogenerate -m "describe it"  # after changing a model
"""
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine, pool

# data_manager.py sits next to alembic.ini (in the repo and in the Docker image)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from data_manager import Base, Database  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def database_url() -> str:
    url = os.getenv('DATABASE_URL')
    if not url:
        raise RuntimeError("DATABASE_URL is not set; it tells Alembic which database to migrate")
    return Database.normalise_url(url)


def run_migrations_offline() -> None:
    """Print the SQL instead of running it (alembic upgrade head --sql)."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={'paramstyle': 'named'},
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(database_url(), poolclass=pool.NullPool)
    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
