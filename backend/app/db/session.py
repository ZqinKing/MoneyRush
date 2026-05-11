"""Database session helpers reserved for Milestone 2."""

from app.core.config import get_settings


def get_postgres_dsn() -> str:
    return get_settings().postgres_dsn
