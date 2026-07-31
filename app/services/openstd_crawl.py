from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.entities import Standard, StandardCrawlItem, StandardCrawlJob
from app.services.standards import safe_filename, standard_id_from_name
from app.services.storage import StorageService, storage_service


OPENSTD_SOURCE_SITE = "openstd.samr.gov.cn"
DOWNLOADABLE_STATUSES = {"pending_download", "failed"}


def openstd_standard_id(standard_code: str, standard_name: str = "") -> str:
    basis = standard_code.strip() or standard_name.strip() or uuid.uuid4().hex
    return standard_id_from_name(f"{basis}.pdf")


def openstd_pdf_filename(standard_code: str, standard_name: str = "") -> str:
    base = standard_code.strip() or standard_name.strip() or "openstd"
    name = safe_filename(f"{base}.pdf")
    if Path(name).suffix.lower() != ".pdf":
        name = f"{Path(name).stem}.pdf"
    return name


def object_key_for_openstd_pdf(standard_id: str, filename: str) -> str:
    return f"standards/{standard_id}/source/{filename}"


class OpenStdToolRunner:
    def __init__(self, tool_dir: str | Path | None = None) -> None:
        self.tool_dir = Path(tool_dir or settings.openstd_importer_tool_dir)
        if not self.tool_dir.is_absolute():
            self.tool_dir = Path.cwd() / self.tool_dir
        self.script = self.tool_dir / "scripts" / "openstd_importer.py"

    def discover(self, *, url: str, scope: str, max_pages: int, interval_seconds: float) -> dict[str, Any]:
        command = [
            sys.executable,
            str(self.script),
            "discover",
            "--url",
            url,
            "--scope",
            scope,
            "--allowed-statuses",
            settings.openstd_allowed_statuses,
            "--max-pages",
            str(max_pages),
            "--max-items",
            str(settings.openstd_max_items),
            "--interval",
            str(interval_seconds),
            "--output-json",
        ]
        return self._run(command, timeout_seconds=max(120.0, settings.openstd_download_timeout_seconds))

    def download(self, *, detail_url: str, output_dir: Path) -> dict[str, Any]:
        command = [
            sys.executable,
            str(self.script),
            "download",
            "--detail-url",
            detail_url,
            "--output-dir",
            str(output_dir),
            "--timeout",
            str(settings.openstd_download_timeout_seconds),
            "--output-json",
        ]
        return self._run(command, timeout_seconds=settings.openstd_download_timeout_seconds + 60)

    def _run(self, command: list[str], *, timeout_seconds: float) -> dict[str, Any]:
        if not self.script.exists():
            raise FileNotFoundError(f"OpenSTD importer tool script not found: {self.script}")
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout_seconds,
        )
        stdout = completed.stdout.strip()
        if not stdout:
            raise RuntimeError(f"OpenSTD importer produced no JSON output: {completed.stderr.strip()}")
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"OpenSTD importer returned invalid JSON: {stdout[:1000]}") from exc
        if completed.returncode not in {0, 2} and payload.get("status") == "failed":
            raise RuntimeError(payload.get("message") or payload.get("reason") or completed.stderr.strip())
        return payload


class OpenStdCrawlService:
    def __init__(
        self,
        *,
        storage: StorageService = storage_service,
        tool_runner: OpenStdToolRunner | None = None,
    ) -> None:
        self.storage = storage
        self.tool_runner = tool_runner or OpenStdToolRunner()

    def create_job(self, session: Session) -> dict[str, Any]:
        running = session.scalars(
            select(StandardCrawlJob)
            .where(StandardCrawlJob.status.in_(["queued", "running"]))
            .order_by(StandardCrawlJob.created_at.desc())
        ).first()
        if running is not None:
            result = self.job_to_dict(session, running)
            result["created"] = False
            return result
        job = StandardCrawlJob(
            id=uuid.uuid4().hex,
            source_site=OPENSTD_SOURCE_SITE,
            source_url=settings.openstd_source_url,
            crawl_scope=settings.openstd_crawl_scope,
            status="queued",
        )
        session.add(job)
        session.commit()
        result = self.job_to_dict(session, job)
        result["created"] = True
        return result

    def latest_job(self, session: Session) -> dict[str, Any] | None:
        job = session.scalars(select(StandardCrawlJob).order_by(StandardCrawlJob.created_at.desc())).first()
        return self.job_to_dict(session, job) if job else None

    def claim_next_job(self, session: Session) -> StandardCrawlJob | None:
        job = session.scalars(
            select(StandardCrawlJob)
            .where(StandardCrawlJob.status.in_(["queued", "running"]))
            .order_by(StandardCrawlJob.created_at.asc())
        ).first()
        if job is None:
            return None
        if job.status == "queued":
            job.status = "running"
            job.started_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
            session.add(job)
            session.commit()
        return job

    def run_job(self, session: Session, job_id: str) -> dict[str, Any]:
        job = session.get(StandardCrawlJob, job_id)
        if job is None:
            raise ValueError(f"OpenSTD crawl job not found: {job_id}")
        job.status = "running"
        job.started_at = job.started_at or datetime.now(timezone.utc)
        job.error_message = ""
        session.add(job)
        session.commit()
        try:
            if not self._has_items(session, job_id):
                self._discover_items(session, job)
            self._download_items(session, job)
            self._refresh_counts(session, job)
            job.status = "completed_with_errors" if job.failed_count else "completed"
            job.completed_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
            session.add(job)
            session.commit()
            return self.job_to_dict(session, job)
        except Exception as exc:
            job.status = "failed"
            job.error_message = str(exc)
            job.completed_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
            session.add(job)
            session.commit()
            raise

    def _has_items(self, session: Session, job_id: str) -> bool:
        count = session.scalar(select(func.count()).select_from(StandardCrawlItem).where(StandardCrawlItem.job_id == job_id))
        return bool(count)

    def _discover_items(self, session: Session, job: StandardCrawlJob) -> None:
        payload = self.tool_runner.discover(
            url=job.source_url or settings.openstd_source_url,
            scope=job.crawl_scope or settings.openstd_crawl_scope,
            max_pages=settings.openstd_max_pages,
            interval_seconds=settings.openstd_request_interval_seconds,
        )
        items = payload.get("items") if isinstance(payload, dict) else []
        if not isinstance(items, list):
            items = []
        job.total_pages = int(payload.get("total_pages") or 0)
        job.current_page = int(payload.get("pages_processed") or 0)
        job.total_discovered = len(items)
        session.add(job)
        seen_codes: set[str] = set()
        for raw in items:
            if not isinstance(raw, dict):
                continue
            code = str(raw.get("standard_code") or "").strip()
            detail_url = str(raw.get("detail_url") or "").strip()
            if not code and not detail_url:
                continue
            is_duplicate_in_job = bool(code and code in seen_codes)
            if code:
                seen_codes.add(code)
            is_existing_standard = self._is_duplicate_standard(session, code)
            status = "skipped_duplicate" if is_duplicate_in_job or is_existing_standard else "pending_download"
            skip_reason = ""
            if is_duplicate_in_job:
                skip_reason = "duplicate_in_job"
            elif is_existing_standard:
                skip_reason = "standard_code_exists"
            item = StandardCrawlItem(
                id=uuid.uuid4().hex,
                job_id=job.id,
                standard_id=openstd_standard_id(code, str(raw.get("standard_name") or "")),
                standard_code=code,
                standard_name=str(raw.get("standard_name") or ""),
                standard_status=str(raw.get("standard_status") or ""),
                publish_date=str(raw.get("publish_date") or ""),
                source_scope=str(raw.get("source_scope") or ""),
                source_label=str(raw.get("source_label") or ""),
                source_url=str(raw.get("source_url") or ""),
                detail_url=detail_url,
                status=status,
                skip_reason=skip_reason,
            )
            session.add(item)
        session.commit()
        self._refresh_counts(session, job)

    def _download_items(self, session: Session, job: StandardCrawlJob) -> None:
        while True:
            item = session.scalars(
                select(StandardCrawlItem)
                .where(
                    StandardCrawlItem.job_id == job.id,
                    StandardCrawlItem.status.in_(list(DOWNLOADABLE_STATUSES)),
                    StandardCrawlItem.retry_count < settings.openstd_max_retries,
                )
                .order_by(StandardCrawlItem.created_at.asc())
            ).first()
            if item is None:
                return
            if self._is_duplicate_standard(session, item.standard_code):
                item.status = "skipped_duplicate"
                item.skip_reason = "standard_code_exists"
                item.updated_at = datetime.now(timezone.utc)
                session.add(item)
                session.commit()
                self._refresh_counts(session, job)
                continue
            self._download_one(session, job, item)
            time.sleep(max(0.0, settings.openstd_request_interval_seconds))

    def _download_one(self, session: Session, job: StandardCrawlJob, item: StandardCrawlItem) -> None:
        item.status = "downloading"
        item.retry_count += 1
        item.error_message = ""
        item.updated_at = datetime.now(timezone.utc)
        job.current_item = f"{item.standard_code} {item.standard_name}".strip()
        job.updated_at = datetime.now(timezone.utc)
        session.add(item)
        session.add(job)
        session.commit()
        try:
            workdir = Path(settings.standard_workdir)
            workdir.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(prefix=f"openstd_{item.id}_", dir=str(workdir)) as tmp:
                payload = self.tool_runner.download(detail_url=item.detail_url, output_dir=Path(tmp))
                item.download_url = str((payload.get("detail") or {}).get("download_url") or "")
                item.download_method = str(payload.get("download_method") or "")
                if payload.get("status") == "skipped":
                    item.status = "skipped_unavailable"
                    item.skip_reason = str(payload.get("reason") or "not_downloadable")
                    session.add(item)
                    session.commit()
                    self._refresh_counts(session, job)
                    return
                if payload.get("status") != "downloaded":
                    raise RuntimeError(str(payload.get("reason") or "download_failed"))
                pdf_path = Path(str(payload.get("pdf_path") or ""))
                if not pdf_path.exists():
                    raise RuntimeError("downloaded PDF path does not exist")
                standard_id = item.standard_id or openstd_standard_id(item.standard_code, item.standard_name)
                filename = openstd_pdf_filename(item.standard_code, item.standard_name)
                object_key = object_key_for_openstd_pdf(standard_id, filename)
                stored = self.storage.upload_file(
                    object_key=object_key,
                    path=pdf_path,
                    media_type="application/pdf",
                    bucket=settings.openstd_object_store_bucket,
                )
                standard = Standard(
                    id=standard_id,
                    name=item.standard_name or Path(filename).stem,
                    code=item.standard_code,
                    source_pdf_bucket=stored.bucket,
                    source_pdf_object_key=stored.object_key,
                    status="registered",
                    index_status="not_indexed",
                    index_error="",
                )
                session.merge(standard)
                item.standard_id = standard_id
                item.source_pdf_bucket = stored.bucket
                item.source_pdf_object_key = stored.object_key
                item.status = "registered"
                item.updated_at = datetime.now(timezone.utc)
                session.add(item)
                session.commit()
                self._refresh_counts(session, job)
        except Exception as exc:
            item.status = "failed"
            item.error_message = str(exc)
            item.updated_at = datetime.now(timezone.utc)
            session.add(item)
            session.commit()
            self._refresh_counts(session, job)

    def _is_duplicate_standard(self, session: Session, standard_code: str) -> bool:
        code = standard_code.strip()
        if not code:
            return False
        standard_id = openstd_standard_id(code)
        return bool(
            session.scalars(
                select(Standard).where((Standard.code == code) | (Standard.id == standard_id))
            ).first()
        )

    def _refresh_counts(self, session: Session, job: StandardCrawlJob) -> None:
        rows = session.execute(
            select(StandardCrawlItem.status, func.count())
            .where(StandardCrawlItem.job_id == job.id)
            .group_by(StandardCrawlItem.status)
        ).all()
        counts = {str(status): int(count) for status, count in rows}
        job.total_discovered = sum(counts.values())
        job.total_downloadable = (
            counts.get("pending_download", 0)
            + counts.get("downloading", 0)
            + counts.get("registered", 0)
            + counts.get("failed", 0)
        )
        job.uploaded_count = counts.get("registered", 0)
        job.skipped_duplicate_count = counts.get("skipped_duplicate", 0)
        job.skipped_unavailable_count = counts.get("skipped_unavailable", 0)
        job.failed_count = counts.get("failed", 0)
        job.updated_at = datetime.now(timezone.utc)
        session.add(job)
        session.commit()

    def _standard_status_counts(self, session: Session, job_id: str) -> dict[str, int]:
        rows = session.execute(
            select(StandardCrawlItem.standard_status, func.count())
            .where(StandardCrawlItem.job_id == job_id)
            .group_by(StandardCrawlItem.standard_status)
        ).all()
        counts = {"current": 0, "upcoming": 0, "scrapped": 0, "other": 0}
        for status, count in rows:
            label = str(status or "").strip()
            value = int(count)
            if "废止" in label:
                counts["scrapped"] += value
            elif label == "现行":
                counts["current"] += value
            elif label == "即将实施":
                counts["upcoming"] += value
            else:
                counts["other"] += value
        return counts

    def job_to_dict(self, session: Session, job: StandardCrawlJob) -> dict[str, Any]:
        self._refresh_counts(session, job)
        standard_status_counts = self._standard_status_counts(session, job.id)
        return {
            "id": job.id,
            "source_site": job.source_site,
            "source_url": job.source_url,
            "crawl_scope": job.crawl_scope,
            "status": job.status,
            "total_pages": job.total_pages,
            "current_page": job.current_page,
            "total_discovered": job.total_discovered,
            "total_downloadable": job.total_downloadable,
            "uploaded_count": job.uploaded_count,
            "skipped_duplicate_count": job.skipped_duplicate_count,
            "skipped_unavailable_count": job.skipped_unavailable_count,
            "failed_count": job.failed_count,
            "standard_status_counts": standard_status_counts,
            "current_item": job.current_item,
            "error_message": job.error_message,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "updated_at": job.updated_at.isoformat() if job.updated_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }

    def item_to_dict(self, item: StandardCrawlItem) -> dict[str, Any]:
        return {
            "id": item.id,
            "job_id": item.job_id,
            "standard_id": item.standard_id,
            "standard_code": item.standard_code,
            "standard_name": item.standard_name,
            "standard_status": item.standard_status,
            "publish_date": item.publish_date,
            "source_scope": item.source_scope,
            "source_label": item.source_label,
            "source_url": item.source_url,
            "detail_url": item.detail_url,
            "download_url": item.download_url,
            "download_method": item.download_method,
            "status": item.status,
            "skip_reason": item.skip_reason,
            "error_message": item.error_message,
            "retry_count": item.retry_count,
            "source_pdf_bucket": item.source_pdf_bucket,
            "source_pdf_object_key": item.source_pdf_object_key,
            "created_at": item.created_at.isoformat() if item.created_at else None,
            "updated_at": item.updated_at.isoformat() if item.updated_at else None,
        }


openstd_crawl_service = OpenStdCrawlService()
