from __future__ import annotations

from pathlib import Path

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings


class Base(DeclarativeBase):
    pass


def _connect_args() -> dict:
    if settings.database_url.startswith("sqlite"):
        return {"check_same_thread": False}
    return {}


if settings.database_url.startswith("sqlite:///"):
    db_path = Path(settings.database_url.removeprefix("sqlite:///"))
    if db_path.parent:
        db_path.parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    settings.database_url,
    connect_args=_connect_args(),
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=10,
    future=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def init_db() -> None:
    from app.models import entities  # noqa: F401

    Base.metadata.create_all(bind=engine)
    if settings.database_url.startswith("sqlite"):
        _migrate_sqlite_tables()
    elif settings.database_url.startswith("postgresql"):
        _migrate_postgresql_tables()


def _migrate_sqlite_tables() -> None:
    columns = {
        "standards": {
            "index_status": "VARCHAR(64) DEFAULT 'not_indexed'",
            "indexed_at": "DATETIME",
            "index_error": "TEXT DEFAULT ''",
        },
        "video_jobs": {
            "content_type": "VARCHAR(128) DEFAULT ''",
            "size_bytes": "INTEGER DEFAULT 0",
            "analysis_video_bucket": "VARCHAR(255) DEFAULT ''",
            "analysis_video_object_key": "VARCHAR(1024) DEFAULT ''",
            "analysis_video_codec": "VARCHAR(64) DEFAULT ''",
            "analysis_video_height": "INTEGER DEFAULT 0",
            "analysis_video_size_bytes": "INTEGER DEFAULT 0",
            "duration_ms": "INTEGER DEFAULT 0",
            "frame_count": "INTEGER DEFAULT 0",
            "audio_temp_path": "VARCHAR(1024)",
            "stage_processed": "INTEGER DEFAULT 0",
            "stage_total": "INTEGER DEFAULT 0",
            "stage_message": "VARCHAR(255) DEFAULT ''",
        },
        "video_frames": {
            "timestamp_seconds": "FLOAT DEFAULT 0.0",
            "frame_source": "VARCHAR(64) DEFAULT 'timeline_sample'",
            "width": "INTEGER",
            "height": "INTEGER",
            "size_bytes": "INTEGER DEFAULT 0",
            "selected_for_wireframe": "BOOLEAN DEFAULT 1",
            "selection_status": "VARCHAR(64) DEFAULT 'pending'",
            "selection_score": "FLOAT",
            "selection_reason": "TEXT DEFAULT ''",
            "selection_details_json": "TEXT DEFAULT '{}'",
            "matched_section_index": "INTEGER",
        },
        "standard_crawl_items": {
            "source_scope": "VARCHAR(128) DEFAULT ''",
            "source_label": "VARCHAR(128) DEFAULT ''",
            "source_url": "VARCHAR(2048) DEFAULT ''",
        },
    }
    with engine.begin() as connection:
        connection.execute(text("DROP TABLE IF EXISTS video_artifacts"))
        for table_name, required_columns in columns.items():
            existing = {
                row[1]
                for row in connection.execute(text(f"PRAGMA table_info({table_name})")).all()
            }
            for column_name, column_type in required_columns.items():
                if column_name not in existing:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))
        standard_match_columns = {
            row[1]
            for row in connection.execute(text("PRAGMA table_info(standard_matches)")).all()
        }
        expected_standard_match_columns = {"id", "search_id", "standard_id", "score", "reason", "created_at"}
        if standard_match_columns and standard_match_columns != expected_standard_match_columns:
            connection.execute(text("DROP TABLE standard_matches"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS standard_matches (
                    id VARCHAR(64) PRIMARY KEY,
                    search_id VARCHAR(64) NOT NULL,
                    standard_id VARCHAR(128) NOT NULL,
                    score FLOAT DEFAULT 0.0,
                    reason TEXT DEFAULT '',
                    created_at DATETIME
                )
                """
            )
        )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_standard_matches_search_id ON standard_matches (search_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_standard_matches_standard_id ON standard_matches (standard_id)"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS standard_crawl_jobs (
                    id VARCHAR(64) PRIMARY KEY,
                    source_site VARCHAR(255) DEFAULT 'openstd.samr.gov.cn',
                    source_url VARCHAR(2048) DEFAULT '',
                    crawl_scope VARCHAR(128) DEFAULT 'gbz_guidance',
                    status VARCHAR(64) DEFAULT 'queued',
                    total_pages INTEGER DEFAULT 0,
                    current_page INTEGER DEFAULT 0,
                    total_discovered INTEGER DEFAULT 0,
                    total_downloadable INTEGER DEFAULT 0,
                    uploaded_count INTEGER DEFAULT 0,
                    skipped_duplicate_count INTEGER DEFAULT 0,
                    skipped_unavailable_count INTEGER DEFAULT 0,
                    failed_count INTEGER DEFAULT 0,
                    current_item VARCHAR(512) DEFAULT '',
                    error_message TEXT DEFAULT '',
                    created_at DATETIME,
                    updated_at DATETIME,
                    started_at DATETIME,
                    completed_at DATETIME
                )
                """
            )
        )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_standard_crawl_jobs_status ON standard_crawl_jobs (status)"))
        connection.execute(
            text(
                """
                CREATE TABLE IF NOT EXISTS standard_crawl_items (
                    id VARCHAR(64) PRIMARY KEY,
                    job_id VARCHAR(64) NOT NULL,
                    standard_id VARCHAR(128) DEFAULT '',
                    standard_code VARCHAR(128) DEFAULT '',
                    standard_name VARCHAR(512) DEFAULT '',
                    standard_status VARCHAR(64) DEFAULT '',
                    publish_date VARCHAR(64) DEFAULT '',
                    source_scope VARCHAR(128) DEFAULT '',
                    source_label VARCHAR(128) DEFAULT '',
                    source_url VARCHAR(2048) DEFAULT '',
                    detail_url VARCHAR(2048) DEFAULT '',
                    download_url VARCHAR(2048) DEFAULT '',
                    download_method VARCHAR(64) DEFAULT '',
                    status VARCHAR(64) DEFAULT 'discovered',
                    skip_reason VARCHAR(255) DEFAULT '',
                    error_message TEXT DEFAULT '',
                    retry_count INTEGER DEFAULT 0,
                    source_pdf_bucket VARCHAR(255) DEFAULT '',
                    source_pdf_object_key VARCHAR(1024) DEFAULT '',
                    created_at DATETIME,
                    updated_at DATETIME
                )
                """
            )
        )
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_standard_crawl_items_job_id ON standard_crawl_items (job_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_standard_crawl_items_standard_id ON standard_crawl_items (standard_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_standard_crawl_items_standard_code ON standard_crawl_items (standard_code)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_standard_crawl_items_status ON standard_crawl_items (status)"))


def _migrate_postgresql_tables() -> None:
    statements = [
        "CREATE EXTENSION IF NOT EXISTS vector",
        "DROP TABLE IF EXISTS audio_chunks",
        "DROP TABLE IF EXISTS standard_artifacts",
        "DROP TABLE IF EXISTS test_runs",
        "DROP TABLE IF EXISTS video_artifacts",
        "ALTER TABLE standards DROP COLUMN IF EXISTS version",
        "ALTER TABLE standards DROP COLUMN IF EXISTS keywords",
        "ALTER TABLE standards DROP COLUMN IF EXISTS search_text",
        "ALTER TABLE standards ADD COLUMN IF NOT EXISTS index_status VARCHAR(64) DEFAULT 'not_indexed'",
        "ALTER TABLE standards ADD COLUMN IF NOT EXISTS indexed_at TIMESTAMPTZ",
        "ALTER TABLE standards ADD COLUMN IF NOT EXISTS index_error TEXT DEFAULT ''",
        """
        DO $$
        BEGIN
            IF to_regclass('public.standard_search_indexes') IS NOT NULL THEN
                ALTER TABLE standard_search_indexes DROP COLUMN IF EXISTS standard_version;
            END IF;
        END $$;
        """,
        f"""
        CREATE TABLE IF NOT EXISTS standard_search_indexes (
            id UUID PRIMARY KEY,
            standard_id VARCHAR(128) NOT NULL REFERENCES standards(id) ON DELETE CASCADE,
            index_kind VARCHAR(64) NOT NULL DEFAULT 'overview',
            content TEXT NOT NULL,
            embedding vector({settings.standard_embedding_dimensions}) NOT NULL,
            embedding_model VARCHAR(128) NOT NULL,
            embedding_dimensions INTEGER NOT NULL DEFAULT {settings.standard_embedding_dimensions},
            content_hash VARCHAR(64) NOT NULL,
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_standard_search_indexes_standard_id ON standard_search_indexes (standard_id)",
        "CREATE INDEX IF NOT EXISTS idx_standard_search_indexes_kind ON standard_search_indexes (index_kind)",
        "CREATE INDEX IF NOT EXISTS idx_standard_search_indexes_embedding ON standard_search_indexes USING hnsw (embedding vector_cosine_ops)",
        "ALTER TABLE video_frames ADD COLUMN IF NOT EXISTS selected_for_wireframe BOOLEAN DEFAULT TRUE",
        "ALTER TABLE video_frames ADD COLUMN IF NOT EXISTS selection_status VARCHAR(64) DEFAULT 'pending'",
        "ALTER TABLE video_frames ADD COLUMN IF NOT EXISTS selection_score FLOAT",
        "ALTER TABLE video_frames ADD COLUMN IF NOT EXISTS selection_reason TEXT DEFAULT ''",
        "ALTER TABLE video_frames ADD COLUMN IF NOT EXISTS selection_details_json TEXT DEFAULT '{}'",
        "ALTER TABLE video_frames ADD COLUMN IF NOT EXISTS matched_section_index INTEGER",
        "ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS stage_processed INTEGER DEFAULT 0",
        "ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS stage_total INTEGER DEFAULT 0",
        "ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS stage_message VARCHAR(255) DEFAULT ''",
        "ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS analysis_video_bucket VARCHAR(255) DEFAULT ''",
        "ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS analysis_video_object_key VARCHAR(1024) DEFAULT ''",
        "ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS analysis_video_codec VARCHAR(64) DEFAULT ''",
        "ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS analysis_video_height INTEGER DEFAULT 0",
        "ALTER TABLE video_jobs ADD COLUMN IF NOT EXISTS analysis_video_size_bytes INTEGER DEFAULT 0",
        """
        DO $$
        DECLARE
            existing_columns TEXT[];
        BEGIN
            IF to_regclass('public.standard_matches') IS NOT NULL THEN
                SELECT array_agg(column_name::TEXT ORDER BY column_name)
                INTO existing_columns
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'standard_matches';

                IF existing_columns <> ARRAY['created_at','id','reason','score','search_id','standard_id']::TEXT[] THEN
                    DROP TABLE standard_matches;
                END IF;
            END IF;
        END $$;
        """,
        """
        CREATE TABLE IF NOT EXISTS standard_matches (
            id VARCHAR(64) PRIMARY KEY,
            search_id VARCHAR(64) NOT NULL,
            standard_id VARCHAR(128) NOT NULL REFERENCES standards(id) ON DELETE CASCADE,
            score FLOAT DEFAULT 0.0,
            reason TEXT DEFAULT '',
            created_at TIMESTAMP
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_standard_matches_search_id ON standard_matches (search_id)",
        "CREATE INDEX IF NOT EXISTS idx_standard_matches_standard_id ON standard_matches (standard_id)",
        """
        CREATE TABLE IF NOT EXISTS standard_crawl_jobs (
            id VARCHAR(64) PRIMARY KEY,
            source_site VARCHAR(255) DEFAULT 'openstd.samr.gov.cn',
            source_url VARCHAR(2048) DEFAULT '',
            crawl_scope VARCHAR(128) DEFAULT 'gbz_guidance',
            status VARCHAR(64) DEFAULT 'queued',
            total_pages INTEGER DEFAULT 0,
            current_page INTEGER DEFAULT 0,
            total_discovered INTEGER DEFAULT 0,
            total_downloadable INTEGER DEFAULT 0,
            uploaded_count INTEGER DEFAULT 0,
            skipped_duplicate_count INTEGER DEFAULT 0,
            skipped_unavailable_count INTEGER DEFAULT 0,
            failed_count INTEGER DEFAULT 0,
            current_item VARCHAR(512) DEFAULT '',
            error_message TEXT DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now(),
            started_at TIMESTAMPTZ,
            completed_at TIMESTAMPTZ
        )
        """,
        "CREATE INDEX IF NOT EXISTS idx_standard_crawl_jobs_status ON standard_crawl_jobs (status)",
        """
        CREATE TABLE IF NOT EXISTS standard_crawl_items (
            id VARCHAR(64) PRIMARY KEY,
            job_id VARCHAR(64) NOT NULL,
            standard_id VARCHAR(128) DEFAULT '',
            standard_code VARCHAR(128) DEFAULT '',
            standard_name VARCHAR(512) DEFAULT '',
            standard_status VARCHAR(64) DEFAULT '',
            publish_date VARCHAR(64) DEFAULT '',
            source_scope VARCHAR(128) DEFAULT '',
            source_label VARCHAR(128) DEFAULT '',
            source_url VARCHAR(2048) DEFAULT '',
            detail_url VARCHAR(2048) DEFAULT '',
            download_url VARCHAR(2048) DEFAULT '',
            download_method VARCHAR(64) DEFAULT '',
            status VARCHAR(64) DEFAULT 'discovered',
            skip_reason VARCHAR(255) DEFAULT '',
            error_message TEXT DEFAULT '',
            retry_count INTEGER DEFAULT 0,
            source_pdf_bucket VARCHAR(255) DEFAULT '',
            source_pdf_object_key VARCHAR(1024) DEFAULT '',
            created_at TIMESTAMPTZ DEFAULT now(),
            updated_at TIMESTAMPTZ DEFAULT now()
        )
        """,
        "ALTER TABLE standard_crawl_items ADD COLUMN IF NOT EXISTS source_scope VARCHAR(128) DEFAULT ''",
        "ALTER TABLE standard_crawl_items ADD COLUMN IF NOT EXISTS source_label VARCHAR(128) DEFAULT ''",
        "ALTER TABLE standard_crawl_items ADD COLUMN IF NOT EXISTS source_url VARCHAR(2048) DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS idx_standard_crawl_items_job_id ON standard_crawl_items (job_id)",
        "CREATE INDEX IF NOT EXISTS idx_standard_crawl_items_standard_id ON standard_crawl_items (standard_id)",
        "CREATE INDEX IF NOT EXISTS idx_standard_crawl_items_standard_code ON standard_crawl_items (standard_code)",
        "CREATE INDEX IF NOT EXISTS idx_standard_crawl_items_status ON standard_crawl_items (status)",
    ]
    with engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
