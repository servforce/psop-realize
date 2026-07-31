from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def beijing_now() -> datetime:
    return datetime.now(ZoneInfo("Asia/Shanghai")).replace(tzinfo=None)


class VideoJob(Base):
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


class VideoFrame(Base):
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


class WireframeJob(Base):
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


class Standard(Base):
    __tablename__ = "standards"

    id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(512), default="")
    code: Mapped[str] = mapped_column(String(128), default="")
    source_pdf_bucket: Mapped[str] = mapped_column(String(255), default="")
    source_pdf_object_key: Mapped[str] = mapped_column(String(1024), default="")
    status: Mapped[str] = mapped_column(String(64), default="registered", index=True)
    index_status: Mapped[str] = mapped_column(String(64), default="not_indexed", index=True)
    indexed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    index_error: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StandardMaterializeJob(Base):
    __tablename__ = "standard_materialize_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    standard_id: Mapped[str] = mapped_column(String(128), index=True)
    status: Mapped[str] = mapped_column(String(64), default="running", index=True)
    stage: Mapped[str] = mapped_column(String(128), default="starting")
    progress_percent: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str] = mapped_column(Text, default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StandardCrawlJob(Base):
    __tablename__ = "standard_crawl_jobs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    source_site: Mapped[str] = mapped_column(String(255), default="openstd.samr.gov.cn")
    source_url: Mapped[str] = mapped_column(String(2048), default="")
    crawl_scope: Mapped[str] = mapped_column(String(128), default="gbz_guidance")
    status: Mapped[str] = mapped_column(String(64), default="queued", index=True)
    total_pages: Mapped[int] = mapped_column(Integer, default=0)
    current_page: Mapped[int] = mapped_column(Integer, default=0)
    total_discovered: Mapped[int] = mapped_column(Integer, default=0)
    total_downloadable: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_duplicate_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_unavailable_count: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    current_item: Mapped[str] = mapped_column(String(512), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StandardCrawlItem(Base):
    __tablename__ = "standard_crawl_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    job_id: Mapped[str] = mapped_column(String(64), index=True)
    standard_id: Mapped[str] = mapped_column(String(128), default="", index=True)
    standard_code: Mapped[str] = mapped_column(String(128), default="", index=True)
    standard_name: Mapped[str] = mapped_column(String(512), default="")
    standard_status: Mapped[str] = mapped_column(String(64), default="")
    publish_date: Mapped[str] = mapped_column(String(64), default="")
    source_scope: Mapped[str] = mapped_column(String(128), default="")
    source_label: Mapped[str] = mapped_column(String(128), default="")
    source_url: Mapped[str] = mapped_column(String(2048), default="")
    detail_url: Mapped[str] = mapped_column(String(2048), default="")
    download_url: Mapped[str] = mapped_column(String(2048), default="")
    download_method: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(64), default="discovered", index=True)
    skip_reason: Mapped[str] = mapped_column(String(255), default="")
    error_message: Mapped[str] = mapped_column(Text, default="")
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    source_pdf_bucket: Mapped[str] = mapped_column(String(255), default="")
    source_pdf_object_key: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class StandardMatch(Base):
    __tablename__ = "standard_matches"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    search_id: Mapped[str] = mapped_column(String(64), index=True)
    standard_id: Mapped[str] = mapped_column(String(128), index=True)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=beijing_now)


class CallLog(Base):
    __tablename__ = "call_logs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    interface_type: Mapped[str] = mapped_column(String(32), default="rest", index=True)
    caller: Mapped[str] = mapped_column(String(128), default="anonymous", index=True)
    tool_or_endpoint: Mapped[str] = mapped_column(String(255), default="", index=True)
    request_summary: Mapped[str] = mapped_column(Text, default="")
    response_summary: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(32), default="success", index=True)
    error_message: Mapped[str] = mapped_column(Text, default="")
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    video_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    standard_id: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
