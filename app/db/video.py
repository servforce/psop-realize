from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.video_config import video_settings as settings


class VideoBase(DeclarativeBase):
    pass


video_engine = create_engine(
    settings.video_database_url,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
    pool_timeout=10,
    future=True,
)
VideoSessionLocal = sessionmaker(bind=video_engine, autoflush=False, autocommit=False, future=True)


def init_video_db() -> None:
    from app.models import video  # noqa: F401

    VideoBase.metadata.create_all(bind=video_engine)
    _migrate_video_tables()


def _migrate_video_tables() -> None:
    statements = [
        """
        CREATE TABLE IF NOT EXISTS video_usage_records (
            id VARCHAR(64) PRIMARY KEY,
            video_id VARCHAR(64),
            feature_name VARCHAR(64) NOT NULL DEFAULT '',
            interface_type VARCHAR(64) NOT NULL DEFAULT '',
            tool_or_endpoint VARCHAR(255) NOT NULL DEFAULT '',
            model VARCHAR(255) NOT NULL DEFAULT '',
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            total_tokens INTEGER NOT NULL DEFAULT 0,
            status VARCHAR(64) NOT NULL DEFAULT 'processing',
            request_json TEXT NOT NULL DEFAULT '{}',
            response_json TEXT NOT NULL DEFAULT '{}',
            created_at TIMESTAMP WITH TIME ZONE NOT NULL,
            completed_at TIMESTAMP WITH TIME ZONE
        )
        """,
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
        "CREATE INDEX IF NOT EXISTS idx_video_jobs_status ON video_jobs (status)",
        "CREATE INDEX IF NOT EXISTS idx_video_frames_video_id ON video_frames (video_id)",
        "CREATE INDEX IF NOT EXISTS idx_wireframe_jobs_video_id ON wireframe_jobs (video_id)",
        "CREATE INDEX IF NOT EXISTS idx_wireframe_jobs_status ON wireframe_jobs (status)",
        "CREATE INDEX IF NOT EXISTS idx_video_usage_records_feature_name ON video_usage_records (feature_name)",
        "CREATE INDEX IF NOT EXISTS idx_video_usage_records_status ON video_usage_records (status)",
        "CREATE INDEX IF NOT EXISTS idx_video_usage_records_created_at ON video_usage_records (created_at)",
    ]
    with video_engine.begin() as connection:
        for statement in statements:
            connection.execute(text(statement))
