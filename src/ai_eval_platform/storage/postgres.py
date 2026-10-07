from __future__ import annotations

import os
from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from .models import Base


def get_database_url() -> str:
    """
    Resolve the database URL lazily.

    Reading the environment at call time (instead of at import time) keeps
    this module importable even when DATABASE_URL is not configured, which is
    required for tests and tooling that never touch a real database.
    """
    url = os.getenv("DATABASE_URL")

    if not url:
        raise RuntimeError(
            "DATABASE_URL environment variable is not configured."
        )

    return url


_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    """Return the lazily-created, process-wide engine."""
    global _engine

    if _engine is None:
        _engine = create_engine(
            get_database_url(),
            pool_pre_ping=True,
            pool_recycle=1800,
            pool_size=int(os.getenv("DB_POOL_SIZE", "10")),
            max_overflow=int(os.getenv("DB_MAX_OVERFLOW", "20")),
            future=True,
        )

    return _engine


def get_sessionmaker() -> sessionmaker[Session]:
    """Return the lazily-created session factory bound to the engine."""
    global _session_factory

    if _session_factory is None:
        _session_factory = sessionmaker(
            bind=get_engine(),
            class_=Session,
            autoflush=False,
            expire_on_commit=False,
        )

    return _session_factory


def get_session() -> Session:
    """
    Create a new database session.

    Caller is responsible for closing the session.
    """
    return get_sessionmaker()()


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    """
    Transactional session scope (unit of work).

    Commits when the block succeeds, rolls back when an exception occurs,
    and always closes the session.

    This is the ONLY place a transaction is committed. Repositories never
    commit; they only flush, so several repositories can share one
    transaction by sharing the session yielded here.
    """
    session = get_session()

    try:
        yield session
        session.commit()

    except Exception:
        session.rollback()
        raise

    finally:
        session.close()


def create_tables() -> None:
    """
    Create all tables.

    Intended for local development and tests.

    Production deployments should normally use Alembic migrations
    instead of calling this function.
    """
    Base.metadata.create_all(bind=get_engine())


def drop_tables() -> None:
    """
    Drop all tables.

    Intended only for local development/tests.
    """
    Base.metadata.drop_all(bind=get_engine())


def check_connection() -> bool:
    """
    Verify that PostgreSQL is reachable.
    """
    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))

        return True

    except Exception:
        return False


def dispose_engine() -> None:
    """
    Dispose the SQLAlchemy connection pool and reset the lazy state.
    """
    global _engine, _session_factory

    if _engine is not None:
        _engine.dispose()

    _engine = None
    _session_factory = None
