"""Copy a local NouriVolt SQLite database into an empty PostgreSQL database.

Usage:
    python migrate_sqlite_to_postgres.py --source data/nourivolt.db

Set DATABASE_URL before running. The target database must be empty.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Iterable

from sqlalchemy import MetaData, create_engine, func, inspect, select, text


def normalize_url(url: str) -> str:
    url = url.strip()
    if url.startswith("postgres://"):
        return "postgresql://" + url[len("postgres://") :]
    return url


def table_order(metadata: MetaData) -> Iterable:
    preferred = [
        "users",
        "food_logs",
        "water_logs",
        "workout_sessions",
        "exercise_sets",
        "measurements",
        "goals",
        "smart_scans",
        "daily_checkins",
    ]
    seen = set()
    for name in preferred:
        if name in metadata.tables:
            seen.add(name)
            yield metadata.tables[name]
    for name in sorted(metadata.tables):
        if name not in seen:
            yield metadata.tables[name]


def target_has_rows(engine, table_names: list[str]) -> bool:
    inspector = inspect(engine)
    existing = set(inspector.get_table_names())
    with engine.connect() as connection:
        for name in table_names:
            if name in existing:
                count = connection.execute(text(f'SELECT COUNT(*) FROM "{name}"')).scalar_one()
                if count:
                    return True
    return False


def reset_postgres_sequences(engine, metadata: MetaData) -> None:
    with engine.begin() as connection:
        for table in metadata.sorted_tables:
            if "id" not in table.c:
                continue
            sequence_name = connection.execute(
                text("SELECT pg_get_serial_sequence(:table_name, 'id')"),
                {"table_name": table.name},
            ).scalar_one_or_none()
            if not sequence_name:
                continue
            max_id = connection.execute(select(func.max(table.c.id))).scalar_one()
            if max_id is None:
                connection.execute(
                    text("SELECT setval(CAST(:sequence_name AS regclass), 1, false)"),
                    {"sequence_name": sequence_name},
                )
            else:
                connection.execute(
                    text("SELECT setval(CAST(:sequence_name AS regclass), :value, true)"),
                    {"sequence_name": sequence_name, "value": int(max_id)},
                )


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate NouriVolt SQLite data to PostgreSQL.")
    parser.add_argument("--source", default="data/nourivolt.db", help="Path to the SQLite database")
    args = parser.parse_args()

    source_path = Path(args.source).expanduser().resolve()
    if not source_path.is_file():
        raise SystemExit(f"SQLite database not found: {source_path}")

    target_url = os.getenv("DATABASE_URL") or os.getenv("database_url")
    if not target_url:
        raise SystemExit("Set DATABASE_URL to the PostgreSQL connection string before running.")
    target_url = normalize_url(target_url)
    if not target_url.startswith("postgresql"):
        raise SystemExit("DATABASE_URL must point to PostgreSQL.")

    source_engine = create_engine(f"sqlite:///{source_path}")
    target_engine = create_engine(target_url, pool_pre_ping=True, pool_recycle=300)

    source_metadata = MetaData()
    source_metadata.reflect(bind=source_engine)
    if "users" not in source_metadata.tables:
        raise SystemExit("The source file does not look like a NouriVolt database.")

    table_names = [table.name for table in table_order(source_metadata)]
    if target_has_rows(target_engine, table_names):
        raise SystemExit("The PostgreSQL target already contains data. Migration stopped to prevent duplicates.")

    target_metadata = MetaData()
    for table in table_order(source_metadata):
        table.to_metadata(target_metadata)
    target_metadata.create_all(target_engine)

    copied: dict[str, int] = {}
    with source_engine.connect() as source, target_engine.begin() as target:
        for source_table in table_order(source_metadata):
            rows = [dict(row) for row in source.execute(select(source_table)).mappings()]
            copied[source_table.name] = len(rows)
            if rows:
                target_table = target_metadata.tables[source_table.name]
                target.execute(target_table.insert(), rows)

    reset_postgres_sequences(target_engine, target_metadata)

    print("Migration completed.")
    for name, count in copied.items():
        print(f"{name}: {count} row(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
