from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.video import VideoBase


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class VideoJob(VideoBase):
    __tablename__ = "video_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(255), default="")
    filename: Mapped[str] = mapped_column(String(512), default="")
    content_type: Mapped[str] = mapped_column(String(128), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    source_bucket: Mapped[str] = mapped_column(String(255), default="")
    source_object_key: Mapped[str] = mapped_column(String(1024), default="")
    analysis_video_bucket: Mapped[str] = mapped_column(String(255), default="")
    analysis_video_object_key: Mapped[str] = mapped_column(String(1024), default="")
    analysis_video_codec: Mapped[str] = mapped_column(String(64), default="")
    analysis_video_height: Mapped[int] = mapped_column(Integer, default=0)
    analysis_video_size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(64), default="queued", index=True)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    current_stage: Mapped[str] = mapped_column(String(128), default="uploaded")
    stage_processed: Mapped[int] = mapped_column(Integer, default=0)
    stage_total: Mapped[int] = mapped_column(Integer, default=0)
    stage_message: Mapped[str] = mapped_column(String(255), default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    frame_count: Mapped[int] = mapped_column(Integer, default=0)
    audio_temp_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    transcript_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    markdown_object_key: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VideoFrame(VideoBase):
    __tablename__ = "video_frames"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    video_id: Mapped[str] = mapped_column(String(64), index=True)
    timestamp_ms: Mapped[int] = mapped_column(Integer, default=0)
    timestamp_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    frame_source: Mapped[str] = mapped_column(String(64), default="timeline_sample")
    bucket: Mapped[str] = mapped_column(String(255), default="")
    object_key: Mapped[str] = mapped_column(String(1024), default="")
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    caption: Mapped[str] = mapped_column(Text, default="")
    selected_for_wireframe: Mapped[bool] = mapped_column(Boolean, default=True)
    selection_status: Mapped[str] = mapped_column(String(64), default="pending")
    selection_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    selection_reason: Mapped[str] = mapped_column(Text, default="")
    selection_details_json: Mapped[str] = mapped_column(Text, default="{}")
    matched_section_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WireframeJob(VideoBase):
    __tablename__ = "wireframe_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    video_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(64), default="queued", index=True)
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    total_frames: Mapped[int] = mapped_column(Integer, default=0)
    completed_frames: Mapped[int] = mapped_column(Integer, default=0)
    current_frame_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class VideoUsageRecord(VideoBase):
    __tablename__ = "video_usage_records"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    video_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    feature_name: Mapped[str] = mapped_column(String(64), index=True, default="")
    interface_type: Mapped[str] = mapped_column(String(64), default="")
    tool_or_endpoint: Mapped[str] = mapped_column(String(255), default="")
    model: Mapped[str] = mapped_column(String(255), default="")
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(64), default="processing", index=True)
    request_json: Mapped[str] = mapped_column(Text, default="{}")
    response_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
