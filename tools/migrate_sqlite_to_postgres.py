from __future__ import annotations

import argparse
import os
import sqlite3
from pathlib import Path
from typing import Any

try:
    import psycopg
except ImportError as exc:  # pragma: no cover - environment guard
    raise SystemExit("Missing dependency psycopg. Install project requirements before running this migration.") from exc


DEFAULT_TABLE_ORDER = [
    "video_jobs",
    "video_frames",
    "wireframe_jobs",
    "standards",
    "standard_materialize_jobs",
    "standard_matches",
    "call_logs",
]
SKIP_TABLES = {
    "sqlite_sequence",
    "standard_search_indexes",
    "audio_chunks",
    "standard_artifacts",
    "test_runs",
    "video_artifacts",
}


def load_dotenv(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def normalize_postgres_url(value: str) -> str:
    if value.startswith("postgresql+psycopg://"):
        return "postgresql://" + value.removeprefix("postgresql+psycopg://")
    return value


def quote_ident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def sqlite_tables(connection: sqlite3.Connection) -> list[str]:
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name"
    ).fetchall()
    names = [row[0] for row in rows if row[0] not in SKIP_TABLES]
    ordered = [name for name in DEFAULT_TABLE_ORDER if name in names]
    ordered.extend(name for name in names if name not in ordered)
    return ordered


def sqlite_columns(connection: sqlite3.Connection, table_name: str) -> list[str]:
    return [row[1] for row in connection.execute(f"PRAGMA table_info({quote_ident(table_name)})").fetchall()]


def postgres_columns(connection: psycopg.Connection, table_name: str) -> dict[str, str]:
    rows = connection.execute(
        """
        SELECT column_name, data_type
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s
        ORDER BY ordinal_position
        """,
        (table_name,),
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def postgres_primary_keys(connection: psycopg.Connection, table_name: str) -> list[str]:
    rows = connection.execute(
        """
        SELECT a.attname
        FROM pg_index i
        JOIN pg_class c ON c.oid = i.indrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
        WHERE i.indisprimary = TRUE
          AND n.nspname = 'public'
          AND c.relname = %s
        ORDER BY array_position(i.indkey, a.attnum)
        """,
        (table_name,),
    ).fetchall()
    return [row[0] for row in rows]


def convert_value(value: Any, postgres_type: str) -> Any:
    if value is None:
        return None
    if postgres_type == "boolean":
        return bool(value)
    return value


def build_upsert_sql(table_name: str, columns: list[str], primary_keys: list[str]) -> str:
    quoted_columns = ", ".join(quote_ident(column) for column in columns)
    placeholders = ", ".join(f"%({column})s" for column in columns)
    sql = f"INSERT INTO {quote_ident(table_name)} ({quoted_columns}) VALUES ({placeholders})"
    if not primary_keys:
        return sql
    conflict_target = ", ".join(quote_ident(column) for column in primary_keys)
    update_columns = [column for column in columns if column not in primary_keys]
    if not update_columns:
        return f"{sql} ON CONFLICT ({conflict_target}) DO NOTHING"
    updates = ", ".join(f"{quote_ident(column)} = EXCLUDED.{quote_ident(column)}" for column in update_columns)
    return f"{sql} ON CONFLICT ({conflict_target}) DO UPDATE SET {updates}"


def reset_sequence(connection: psycopg.Connection, table_name: str, primary_keys: list[str]) -> None:
    if len(primary_keys) != 1:
        return
    pk = primary_keys[0]
    sequence = connection.execute("SELECT pg_get_serial_sequence(%s, %s)", (f"public.{table_name}", pk)).fetchone()[0]
    if not sequence:
        return
    max_value = connection.execute(
        f"SELECT MAX({quote_ident(pk)}) FROM {quote_ident(table_name)}"
    ).fetchone()[0]
    if max_value is not None:
        connection.execute("SELECT setval(%s, %s, true)", (sequence, int(max_value)))


def migrate_table(
    *,
    sqlite_connection: sqlite3.Connection,
    postgres_connection: psycopg.Connection,
    table_name: str,
    batch_size: int,
    dry_run: bool,
) -> dict[str, Any]:
    source_columns = sqlite_columns(sqlite_connection, table_name)
    target_columns = postgres_columns(postgres_connection, table_name)
    if not target_columns:
        return {"table": table_name, "status": "skipped", "reason": "target table does not exist"}
    columns = [column for column in source_columns if column in target_columns]
    if not columns:
        return {"table": table_name, "status": "skipped", "reason": "no shared columns"}
    row_count = sqlite_connection.execute(f"SELECT COUNT(*) FROM {quote_ident(table_name)}").fetchone()[0]
    if dry_run:
        return {"table": table_name, "status": "dry_run", "rows": row_count, "columns": columns}

    primary_keys = postgres_primary_keys(postgres_connection, table_name)
    sql = build_upsert_sql(table_name, columns, primary_keys)
    cursor = sqlite_connection.execute(
        f"SELECT {', '.join(quote_ident(column) for column in columns)} FROM {quote_ident(table_name)}"
    )
    migrated = 0
    while True:
        rows = cursor.fetchmany(batch_size)
        if not rows:
            break
        payload = []
        for row in rows:
            payload.append(
                {
                    column: convert_value(row[column], target_columns[column])
                    for column in columns
                }
            )
        with postgres_connection.cursor() as pg_cursor:
            pg_cursor.executemany(sql, payload)
        migrated += len(payload)
    reset_sequence(postgres_connection, table_name, primary_keys)
    postgres_connection.commit()
    return {"table": table_name, "status": "migrated", "rows": migrated, "columns": columns}


def main() -> None:
    parser = argparse.ArgumentParser(description="Migrate existing SQLite workbench data into PostgreSQL.")
    parser.add_argument("--sqlite", default="data/workbench.db", help="Path to the source SQLite database.")
    parser.add_argument("--database-url", default="", help="Target PostgreSQL URL. Defaults to DATABASE_URL from .env.")
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    load_dotenv(Path(".env"))
    sqlite_path = Path(args.sqlite)
    if not sqlite_path.exists():
        raise SystemExit(f"SQLite database not found: {sqlite_path}")
    database_url = args.database_url or os.getenv("DATABASE_URL", "")
    if not database_url:
        raise SystemExit("Missing target DATABASE_URL.")
    if not database_url.startswith("postgresql"):
        raise SystemExit("Target DATABASE_URL must be PostgreSQL. Check .env before running migration.")

    sqlite_connection = sqlite3.connect(sqlite_path)
    sqlite_connection.row_factory = sqlite3.Row
    with psycopg.connect(normalize_postgres_url(database_url)) as postgres_connection:
        for table_name in sqlite_tables(sqlite_connection):
            result = migrate_table(
                sqlite_connection=sqlite_connection,
                postgres_connection=postgres_connection,
                table_name=table_name,
                batch_size=max(1, args.batch_size),
                dry_run=args.dry_run,
            )
            print(result)


if __name__ == "__main__":
    main()
