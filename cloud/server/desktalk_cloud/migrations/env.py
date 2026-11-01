"""Alembic's entry point. run_migrations() (store_postgres.py) hands in an open connection; run by hand, it
reads DATABASE_URL."""

from __future__ import annotations

import os

import sqlalchemy as sa
from alembic import context


def run() -> None:
    connection = context.config.attributes.get('connection')
    if connection is not None:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
        return
    url = os.environ.get('DATABASE_URL')
    if not url:
        raise SystemExit('Set DATABASE_URL to the Postgres address first.')
    engine = sa.create_engine(url.replace('postgresql://', 'postgresql+psycopg://', 1))
    with engine.connect() as connection:
        context.configure(connection=connection)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


run()
