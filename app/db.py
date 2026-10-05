from __future__ import annotations

import os
from contextvars import ContextVar
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .paths import data_dir, resource_root


BASE_DIR = resource_root()
DATA_DIR = data_dir()


def _default_sqlite_url() -> str:
    return f"sqlite:///{DATA_DIR / 'biomanager.db'}"


def get_database_url() -> str:
    raw_url = os.environ.get("DATABASE_URL", "").strip()
    if not raw_url:
        return _default_sqlite_url()

    if raw_url.startswith("postgres://"):
        return raw_url.replace("postgres://", "postgresql+psycopg://", 1)
    if raw_url.startswith("postgresql://") and "+psycopg" not in raw_url:
        return raw_url.replace("postgresql://", "postgresql+psycopg://", 1)
    return raw_url


DATABASE_URL = get_database_url()


class Base(DeclarativeBase):
    pass


engine = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)

# A connection every session opened while it is set joins, inside one outer
# transaction, its own commits becoming savepoints (app/contained.py): how a
# proposal runs the pages' own code and is then rolled back, or kept, whole.
JOINED: ContextVar = ContextVar("biomanager_joined_connection", default=None)


class LabSession(Session):
    def __init__(self, bind=None, **kwargs):
        joined = JOINED.get()
        if joined is not None:
            bind = joined
            kwargs["join_transaction_mode"] = "create_savepoint"
        super().__init__(bind=bind, **kwargs)


SessionLocal = sessionmaker(class_=LabSession, bind=engine, autoflush=False, autocommit=False, future=True)


# SQLite does not enforce foreign keys unless every connection asks it to.
# app/integrity.py decides at start-up (init_database), after checking the
# existing data: True turns enforcement on for every connection from then
# on; False keeps it off for a database whose data still has references it
# could not repair (it says so loudly), so the app keeps working until
# someone fixes the data. None (not yet checked) behaves as off, so the
# schema steps that run before the check see the database as they always did.
FOREIGN_KEYS_ENFORCED: bool | None = None


# SQLite: what every connection needs, on the app's engine and on the one a
# proposal runs in (contained_engine).
def _sqlite_foreign_keys(dbapi_connection, connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute(f"PRAGMA foreign_keys={'ON' if FOREIGN_KEYS_ENFORCED else 'OFF'}")
    cursor.close()


# SQLite's own lower() folds A-Z only, so a case-insensitive search
# (ilike is lower(x) LIKE lower(y) there) missed "Δ-Cre" for "δ-cre"
# and "Café" for "CAFÉ", which PostgreSQL finds. Python's lower() folds
# every script, as PostgreSQL's does.
def _sqlite_unicode_lower(dbapi_connection, connection_record) -> None:
    dbapi_connection.create_function(
        "lower", 1, lambda value: value.lower() if isinstance(value, str) else value, deterministic=True)


# A commit refused by a deferred foreign-key check leaves pysqlite's
# transaction open (SQLite keeps the transaction when COMMIT fails), and
# a session closed without an explicit rollback hands that connection
# back to the pool still holding the refused change and the write lock.
# Roll back anything still open whenever a connection returns.
def _sqlite_rollback_on_return(dbapi_connection, connection_record) -> None:
    if getattr(dbapi_connection, "in_transaction", False):
        try:
            dbapi_connection.rollback()
        except Exception:  # a broken connection is dropped by the pool anyway
            connection_record.invalidate()


if engine.dialect.name == "sqlite":
    event.listen(engine, "connect", _sqlite_foreign_keys)
    event.listen(engine, "connect", _sqlite_unicode_lower)
    event.listen(engine, "checkin", _sqlite_rollback_on_return)


_contained = None


def contained_engine():
    """The engine a proposal's outer transaction runs on. PostgreSQL: the
    app's own. SQLite: one of its own, because pysqlite only begins a
    transaction before a write, so a savepoint taken first would start, and
    its release commit, a transaction of its own; this engine leaves
    transactions to SQLAlchemy and begins one explicitly, so savepoints nest
    inside it and rolling it back rolls back everything."""
    global _contained
    if engine.dialect.name != "sqlite":
        return engine
    if _contained is None:
        _contained = create_engine(DATABASE_URL, future=True, pool_pre_ping=True)
        event.listen(_contained, "connect", _sqlite_foreign_keys)
        event.listen(_contained, "connect", _sqlite_unicode_lower)
        event.listen(_contained, "checkin", _sqlite_rollback_on_return)

        @event.listens_for(_contained, "connect")
        def _leave_transactions_to_sqlalchemy(dbapi_connection, connection_record) -> None:
            dbapi_connection.isolation_level = None

        @event.listens_for(_contained, "begin")
        def _begin_explicitly(conn) -> None:
            conn.exec_driver_sql("BEGIN")
    return _contained
