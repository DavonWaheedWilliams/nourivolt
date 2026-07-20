"""Verify the configured NouriVolt database without displaying private records."""

from __future__ import annotations

import os
import sys
from sqlalchemy import create_engine, inspect, text


def normalize_url(url: str) -> str:
    url = url.strip()
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def main() -> int:
    url = os.getenv("DATABASE_URL") or os.getenv("database_url")
    if not url:
        print("DATABASE_URL is not set.")
        return 1

    engine = create_engine(normalize_url(url), pool_pre_ping=True, pool_recycle=300)
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
        tables = sorted(inspect(connection).get_table_names())

    print("Database connection successful.")
    print("Tables found:", ", ".join(tables) if tables else "none yet")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
