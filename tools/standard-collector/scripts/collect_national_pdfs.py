from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import sys
import tempfile
import time
import uuid
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import SessionLocal, init_db
from app.models.entities import Standard, StandardSyncItem, StandardSyncJob
from app.services.openstd_crawl import (
    OPENSTD_SOURCE_SITE,
    national_standard_category_from_code,
    object_key_for_openstd_pdf,
    openstd_pdf_filename,
    openstd_standard_id,
)
from app.services.storage import storage_service


LOGGER = logging.getLogger("collect_national_pdfs")
FAILED_RETRY_ACTIONS = {"download_failed", "upload_failed", "invalid_pdf"}
DEFAULT_LOG_FILE = PROJECT_ROOT / "tools" / "standard-collector" / "logs" / "collect_national_pdfs.log"


class OpenStdImporterClient:
    def __init__(self, tool_dir: str | Path | None = None) -> None:
        self.tool_dir = Path(tool_dir or settings.openstd_importer_tool_dir)
        if not self.tool_dir.is_absolute():
            self.tool_dir = PROJECT_ROOT / self.tool_dir
        self.script = self.tool_dir / "scripts" / "openstd_importer.py"
        if not self.script.exists():
            raise FileNotFoundError(f"OpenSTD importer tool script not found: {self.script}")
        spec = importlib.util.spec_from_file_location("openstd_importer_tool", self.script)
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Unable to load OpenSTD importer module from: {self.script}")
        module = importlib.util.module_from_spec(spec)
        sys.modules.setdefault("openstd_importer_tool", module)
        spec.loader.exec_module(module)
        module.progress = lambda message: LOGGER.info("%s", message)
        self.module = module

    def new_http_client(self):
        return self.module.OpenStdHttpClient(timeout_seconds=settings.openstd_download_timeout_seconds)

    def resolve_sources(self, scope: str, url: str) -> list[dict[str, str]]:
        return self.module.resolve_sources(scope, url)

    def build_page_url(self, source_url: str, page: int) -> str:
        return self.module.build_page_url(source_url, page)

    def parse_total_pages(self, html: str) -> int:
        return self.module.parse_total_pages(html)

    def parse_list_items(
        self,
        html: str,
        page_url: str,
        *,
        source_scope: str = "",
        source_label: str = "",
        source_url: str = "",
        allowed_statuses: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        return self.module.parse_list_items(
            html,
            page_url,
            source_scope=source_scope,
            source_label=source_label,
            source_url=source_url,
            allowed_statuses=allowed_statuses,
        )

    def download(self, *, detail_url: str, output_dir: Path) -> dict[str, Any]:
        return self.module.download(
            detail_url,
            output_dir=output_dir,
            timeout_seconds=settings.openstd_download_timeout_seconds,
        )


def normalize_source_status(raw_status: str) -> str:
    value = (raw_status or "").strip()
    lowered = value.lower()
    if "废" in value or "abolish" in lowered or "scrap" in lowered:
        return "abolished"
    if "即将" in value or "upcoming" in lowered:
        return "upcoming"
    if "现行" in value or "active" in lowered:
        return "active"
    return "active"


def standard_org_from_code(code: str) -> str:
    value = (code or "").strip()
    return value.split(" ", 1)[0] if value else ""


def external_id_from_detail_url(detail_url: str) -> str:
    parsed = urlparse(detail_url or "")
    query = parse_qs(parsed.query)
    for key in ("hcno", "id"):
        values = query.get(key)
        if values:
            return values[0]
    return ""


def source_pdf_fingerprint(*, checksum: str, code: str, source_status_raw: str, publish_date: str) -> str:
    # The PDF checksum is the strongest signal; metadata is included to catch visible source changes.
    payload = json.dumps(
        {
            "checksum": checksum,
            "code": code,
            "source_status_raw": source_status_raw,
            "publish_date": publish_date,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    import hashlib

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def create_sync_job(session: Session, *, args: argparse.Namespace, trigger_type: str) -> StandardSyncJob:
    now = datetime.now(timezone.utc)
    job = StandardSyncJob(
        id=uuid.uuid4().hex,
        trigger_type=trigger_type,
        source_scope=args.scope,
        source_summary_json=json.dumps({"national": True, "site": OPENSTD_SOURCE_SITE}, ensure_ascii=False),
        source_site=OPENSTD_SOURCE_SITE,
        source_url=args.source_url,
        crawl_scope=args.scope,
        status="running",
        stage="retrying" if args.retry_failed else "discovering",
        started_at=now,
        updated_at=now,
    )
    session.add(job)
    session.commit()
    return job


def existing_standard(session: Session, *, code: str, detail_url: str, standard_id: str) -> Standard | None:
    statement = select(Standard)
    conditions = []
    if code:
        conditions.append(Standard.code == code)
    if standard_id:
        conditions.append(Standard.id == standard_id)
    external_id = external_id_from_detail_url(detail_url)
    if external_id:
        conditions.append(Standard.external_id == external_id)
    if not conditions:
        return None
    from sqlalchemy import or_

    return session.scalars(statement.where(or_(*conditions))).first()


def record_sync_item(
    session: Session,
    *,
    job: StandardSyncJob,
    raw: dict[str, Any],
    standard_id: str,
    action: str,
    status: str,
    retry_count: int = 0,
    skip_reason: str = "",
    error_message: str = "",
    download_url: str = "",
    download_method: str = "",
    bucket: str = "",
    object_key: str = "",
    new_fingerprint: str = "",
) -> StandardSyncItem:
    item = StandardSyncItem(
        id=uuid.uuid4().hex,
        job_id=job.id,
        standard_id=standard_id,
        action=action,
        status=status,
        source_type="national",
        source_site=OPENSTD_SOURCE_SITE,
        source_scope=str(raw.get("source_scope") or ""),
        source_label=str(raw.get("source_label") or ""),
        source_url=str(raw.get("source_url") or ""),
        external_id=external_id_from_detail_url(str(raw.get("detail_url") or "")),
        standard_code=str(raw.get("standard_code") or ""),
        standard_name=str(raw.get("standard_name") or ""),
        standard_status=str(raw.get("standard_status") or raw.get("source_status_raw") or ""),
        source_status_raw=str(raw.get("standard_status") or raw.get("source_status_raw") or ""),
        publish_date=str(raw.get("publish_date") or ""),
        effective_date=str(raw.get("effective_date") or ""),
        detail_url=str(raw.get("detail_url") or ""),
        download_url=download_url,
        download_method=download_method,
        new_fingerprint=new_fingerprint,
        skip_reason=skip_reason,
        error_message=error_message,
        retry_count=retry_count,
        source_pdf_bucket=bucket,
        source_pdf_object_key=object_key,
        updated_at=datetime.now(timezone.utc),
    )
    session.add(item)
    return item


def write_standard(
    session: Session,
    *,
    raw: dict[str, Any],
    standard_id: str,
    bucket: str,
    object_key: str,
    checksum: str,
    size_bytes: int,
    fingerprint: str,
    effective_date: str = "",
) -> Standard:
    now = datetime.now(timezone.utc)
    code = str(raw.get("standard_code") or "")
    source_status_raw = str(raw.get("standard_status") or raw.get("source_status_raw") or "")
    standard = Standard(
        id=standard_id,
        name=str(raw.get("standard_name") or ""),
        code=code,
        standard_type="national",
        standard_category=national_standard_category_from_code(code),
        standard_org=standard_org_from_code(code),
        source_status=normalize_source_status(source_status_raw),
        source_status_raw=source_status_raw,
        publish_date=str(raw.get("publish_date") or ""),
        source_site=OPENSTD_SOURCE_SITE,
        source_scope=str(raw.get("source_scope") or ""),
        source_url=str(raw.get("source_url") or ""),
        detail_url=str(raw.get("detail_url") or ""),
        external_id=external_id_from_detail_url(str(raw.get("detail_url") or "")),
        effective_date=effective_date,
        source_pdf_bucket=bucket,
        source_pdf_object_key=object_key,
        source_pdf_hash=checksum,
        source_pdf_size_bytes=size_bytes,
        materialize_status="not_started",
        materialize_error="",
        index_status="not_indexed",
        index_error="",
        fingerprint=fingerprint,
        last_synced_at=now,
        updated_at=now,
    )
    session.merge(standard)
    return standard


def process_one(
    *,
    session: Session,
    importer: OpenStdImporterClient,
    job: StandardSyncJob,
    raw: dict[str, Any],
    args: argparse.Namespace,
) -> str:
    code = str(raw.get("standard_code") or "").strip()
    name = str(raw.get("standard_name") or "").strip()
    detail_url = str(raw.get("detail_url") or "").strip()
    standard_id = openstd_standard_id(code, name)
    LOGGER.info("standard=%s name=%s detail=%s", code or standard_id, name, detail_url)
    if existing_standard(session, code=code, detail_url=detail_url, standard_id=standard_id):
        LOGGER.info("skip existing standard=%s name=%s", code or standard_id, name)
        job.unchanged_count += 1
        job.updated_at = datetime.now(timezone.utc)
        session.add(job)
        session.commit()
        return "unchanged"

    workdir = Path(settings.standard_workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    last_error = ""
    phase = "download"
    payload: dict[str, Any] = {}
    for attempt in range(1, args.max_retries + 1):
        try:
            with tempfile.TemporaryDirectory(prefix=f"history_pdf_{standard_id}_", dir=str(workdir)) as tmp:
                phase = "download"
                LOGGER.info("downloading standard=%s name=%s", code or standard_id, name)
                payload = importer.download(detail_url=detail_url, output_dir=Path(tmp))
                download_url = str((payload.get("detail") or {}).get("download_url") or "")
                download_method = str(payload.get("download_method") or "")
                if payload.get("status") == "skipped":
                    LOGGER.info(
                        "download skipped standard=%s name=%s reason=%s",
                        code or standard_id,
                        name,
                        str(payload.get("reason") or "not_downloadable"),
                    )
                    record_sync_item(
                        session,
                        job=job,
                        raw=raw,
                        standard_id=standard_id,
                        action="not_downloadable",
                        status="skipped",
                        retry_count=attempt - 1,
                        skip_reason=str(payload.get("reason") or "not_downloadable"),
                        download_url=download_url,
                        download_method=download_method,
                    )
                    job.skipped_unavailable_count += 1
                    job.skipped_count += 1
                    job.updated_at = datetime.now(timezone.utc)
                    session.add(job)
                    session.commit()
                    return "skipped"
                if payload.get("status") != "downloaded":
                    raise RuntimeError(str(payload.get("reason") or "download_failed"))
                pdf_path = Path(str(payload.get("pdf_path") or ""))
                if not pdf_path.exists():
                    raise RuntimeError("downloaded PDF path does not exist")
                with pdf_path.open("rb") as file:
                    header = file.read(5)
                if header != b"%PDF-":
                    phase = "invalid_pdf"
                    raise RuntimeError("downloaded file is not a valid PDF")
                filename = openstd_pdf_filename(code, name)
                object_key = object_key_for_openstd_pdf(standard_id, filename)
                phase = "upload"
                LOGGER.info(
                    "uploading standard=%s name=%s bucket=%s object_key=%s",
                    code or standard_id,
                    name,
                    settings.object_store_standard_bucket,
                    object_key,
                )
                stored = storage_service.upload_file(
                    object_key=object_key,
                    path=pdf_path,
                    media_type="application/pdf",
                    bucket=settings.object_store_standard_bucket,
                )
                fingerprint = source_pdf_fingerprint(
                    checksum=stored.checksum,
                    code=code,
                    source_status_raw=str(raw.get("standard_status") or ""),
                    publish_date=str(raw.get("publish_date") or ""),
                )
                effective_date = str((payload.get("detail") or {}).get("effective_date") or raw.get("effective_date") or "")
                write_standard(
                    session,
                    raw=raw,
                    standard_id=standard_id,
                    bucket=stored.bucket,
                    object_key=stored.object_key,
                    checksum=stored.checksum,
                    size_bytes=stored.size_bytes,
                    fingerprint=fingerprint,
                    effective_date=effective_date,
                )
                record_sync_item(
                    session,
                    job=job,
                    raw=raw,
                    standard_id=standard_id,
                    action="new",
                    status="registered",
                    retry_count=attempt - 1,
                    download_url=download_url,
                    download_method=download_method,
                    bucket=stored.bucket,
                    object_key=stored.object_key,
                    new_fingerprint=fingerprint,
                )
                job.new_count += 1
                job.uploaded_count += 1
                job.updated_at = datetime.now(timezone.utc)
                session.add(job)
                session.commit()
                LOGGER.info(
                    "uploaded standard=%s name=%s bucket=%s object_key=%s size_bytes=%s",
                    code or standard_id,
                    name,
                    stored.bucket,
                    stored.object_key,
                    stored.size_bytes,
                )
                return "registered"
        except Exception as exc:
            session.rollback()
            last_error = str(exc)
            if attempt < args.max_retries:
                sleep_seconds = max(0.0, args.retry_backoff_seconds * attempt)
                LOGGER.warning(
                    "retrying standard=%s name=%s attempt=%s/%s phase=%s error=%s sleep=%.1fs",
                    code,
                    name,
                    attempt,
                    args.max_retries,
                    phase,
                    last_error,
                    sleep_seconds,
                )
                time.sleep(sleep_seconds)
                continue

    action = "invalid_pdf" if phase == "invalid_pdf" else "upload_failed" if phase == "upload" else "download_failed"
    record_sync_item(
        session,
        job=job,
        raw=raw,
        standard_id=standard_id,
        action=action,
        status="failed",
        retry_count=args.max_retries,
        error_message=last_error,
        download_url=str((payload.get("detail") or {}).get("download_url") or "") if payload else "",
        download_method=str(payload.get("download_method") or "") if payload else "",
    )
    if action == "upload_failed":
        job.upload_failed_count += 1
    else:
        job.download_failed_count += 1
    job.failed_count += 1
    job.updated_at = datetime.now(timezone.utc)
    session.add(job)
    session.commit()
    LOGGER.error(
        "failed standard=%s name=%s phase=%s error=%s",
        code or standard_id,
        name,
        phase,
        last_error,
    )
    return "failed"


def stream_discovery(
    importer: OpenStdImporterClient,
    args: argparse.Namespace,
    *,
    page_handler,
) -> tuple[int, int]:
    client = importer.new_http_client()
    total_seen = 0
    pages_processed = 0
    try:
        for source in importer.resolve_sources(args.scope, args.source_url):
            LOGGER.info("discovering source=%s scope=%s url=%s", source["label"], source["scope"], source["url"])
            page = 1
            source_pages_processed = 0
            while True:
                if args.max_items > 0 and total_seen >= args.max_items:
                    return total_seen, pages_processed
                page_url = importer.build_page_url(source["url"], page)
                LOGGER.info("[discover] source=%s page=%s url=%s", source["label"], page, page_url)
                html, final_url = client.get_html(page_url, referer=source["url"] if page > 1 else "")
                source_total_pages = importer.parse_total_pages(html)
                discovered_items = importer.parse_list_items(
                    html,
                    final_url,
                    source_scope=source["scope"],
                    source_label=source["label"],
                    source_url=source["url"],
                    allowed_statuses={item.strip() for item in settings.openstd_allowed_statuses.split(",") if item.strip()},
                )
                page_items = [asdict(item) if hasattr(item, "__dataclass_fields__") else dict(item) for item in discovered_items]
                pages_processed += 1
                source_pages_processed += 1
                total_seen += len(page_items)
                LOGGER.info(
                    "[discover] source=%s page=%s items=%s total_seen=%s pages_processed=%s total_pages=%s",
                    source["label"],
                    page,
                    len(page_items),
                    total_seen,
                    pages_processed,
                    source_total_pages,
                )
                should_stop = page_handler(
                    source=source,
                    page=page,
                    page_url=page_url,
                    final_url=final_url,
                    source_total_pages=source_total_pages,
                    page_items=page_items,
                    pages_processed=pages_processed,
                    total_seen=total_seen,
                )
                if should_stop:
                    return total_seen, pages_processed
                if args.max_pages > 0 and source_pages_processed >= args.max_pages:
                    break
                if source_total_pages and page >= source_total_pages:
                    break
                if not page_items and not source_total_pages:
                    break
                page += 1
                if args.request_interval > 0:
                    time.sleep(args.request_interval)
            if args.max_items > 0 and total_seen >= args.max_items:
                break
            if args.request_interval > 0:
                time.sleep(args.request_interval)
    finally:
        client.close()
    return total_seen, pages_processed


def failed_items(session: Session, *, limit: int) -> list[dict[str, Any]]:
    statement = (
        select(StandardSyncItem)
        .where(
            StandardSyncItem.status == "failed",
            StandardSyncItem.action.in_(FAILED_RETRY_ACTIONS),
        )
        .order_by(StandardSyncItem.updated_at.asc(), StandardSyncItem.created_at.asc())
    )
    if limit > 0:
        statement = statement.limit(limit)
    rows = session.scalars(statement).all()
    items: list[dict[str, Any]] = []
    for row in rows:
        if row.standard_code:
            existing = session.scalars(select(Standard).where(Standard.code == row.standard_code)).first()
            if existing and existing.source_pdf_object_key:
                continue
        items.append(
            {
                "source_scope": row.source_scope,
                "source_label": row.source_label,
                "source_url": row.source_url,
                "standard_code": row.standard_code,
                "standard_name": row.standard_name,
                "standard_status": row.source_status_raw or row.standard_status,
                "publish_date": row.publish_date,
                "effective_date": row.effective_date,
                "detail_url": row.detail_url,
            }
        )
    return items


def build_run_summary(*, job: StandardSyncJob, total_items: int, mode: str) -> dict[str, Any]:
    return {
        "mode": mode,
        "job_id": job.id,
        "status": job.status,
        "total_items": total_items,
        "uploaded_success": job.uploaded_count,
        "new_items": job.new_count,
        "unchanged_items": job.unchanged_count,
        "skipped_items": job.skipped_count,
        "download_failed": job.download_failed_count,
        "upload_failed": job.upload_failed_count,
        "failed_total": job.failed_count,
    }


def emit_run_summary(summary: dict[str, Any]) -> None:
    LOGGER.info(
        "summary: mode=%s job_id=%s status=%s total=%s uploaded=%s unchanged=%s skipped=%s download_failed=%s upload_failed=%s failed=%s",
        summary["mode"],
        summary["job_id"],
        summary["status"],
        summary["total_items"],
        summary["uploaded_success"],
        summary["unchanged_items"],
        summary["skipped_items"],
        summary["download_failed"],
        summary["upload_failed"],
        summary["failed_total"],
    )
    print(json.dumps({"summary": summary}, ensure_ascii=False, indent=2))


def run_collection(args: argparse.Namespace) -> int:
    args.max_retries = max(1, args.max_retries)
    setup_logging(args.log_file)
    LOGGER.info(
        "===== BEGIN RUN %s mode=%s scope=%s max_pages=%s max_items=%s =====",
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "retry_failed" if args.retry_failed else "initial",
        args.scope,
        args.max_pages,
        args.max_items,
    )
    init_db()
    importer = OpenStdImporterClient()

    if args.retry_failed:
        with SessionLocal() as session:
            items = failed_items(session, limit=args.failed_limit)
        LOGGER.info("loaded %s failed items for retry limit=%s", len(items), args.failed_limit or "all")
        if args.dry_run:
            print(json.dumps({"dry_run": True, "count": len(items), "items": items[:5]}, ensure_ascii=False, indent=2))
            return 0
        with SessionLocal() as session:
            job = create_sync_job(
                session,
                args=args,
                trigger_type="retry_failed",
            )
            job.total_discovered = len(items)
            job.scanned_count = len(items)
            job.total_downloadable = len(items)
            job.stage = "downloading"
            session.add(job)
            session.commit()

            for index, raw in enumerate(items, start=1):
                code = str(raw.get("standard_code") or "")
                name = str(raw.get("standard_name") or "")
                job.current_item = f"{code} {name}".strip()
                job.updated_at = datetime.now(timezone.utc)
                session.add(job)
                session.commit()
                LOGGER.info("[%s/%s] processing %s", index, len(items), job.current_item)
                result = process_one(session=session, importer=importer, job=job, raw=raw, args=args)
                LOGGER.info("[%s/%s] %s -> %s", index, len(items), job.current_item, result)
                if args.request_interval > 0:
                    time.sleep(args.request_interval)

            job.stage = "completed"
            job.status = "completed_with_errors" if job.failed_count else "completed"
            job.completed_at = datetime.now(timezone.utc)
            job.updated_at = datetime.now(timezone.utc)
            session.add(job)
            session.commit()
            LOGGER.info(
                "job %s finished: status=%s new=%s unchanged=%s failed=%s skipped=%s",
                job.id,
                job.status,
                job.new_count,
                job.unchanged_count,
                job.failed_count,
                job.skipped_count,
            )
            emit_run_summary(
                build_run_summary(
                    job=job,
                    total_items=len(items),
                    mode="retry_failed",
                )
            )
        return 0

    LOGGER.info(
        "discovering national standards from %s scope=%s max_pages=%s max_items=%s interval=%.1fs",
        args.source_url,
        args.scope,
        args.max_pages,
        args.max_items,
        args.request_interval,
    )

    if args.dry_run:
        preview: list[dict[str, Any]] = []

        def collect_preview(**kwargs: Any) -> bool:
            for raw in kwargs["page_items"]:
                if len(preview) < 5:
                    preview.append(raw)
            return False

        total_seen, _ = stream_discovery(importer, args, page_handler=collect_preview)
        print(json.dumps({"dry_run": True, "count": total_seen, "items": preview}, ensure_ascii=False, indent=2))
        return 0

    with SessionLocal() as session:
        job = create_sync_job(
            session,
            args=args,
            trigger_type="initial",
        )
        job.stage = "streaming"
        job.total_discovered = 0
        job.scanned_count = 0
        job.total_downloadable = 0
        session.add(job)
        session.commit()

        processed_items = 0

        def handle_page(**kwargs: Any) -> bool:
            nonlocal processed_items
            source = kwargs["source"]
            page = int(kwargs["page"])
            page_items = kwargs["page_items"]
            source_total_pages = int(kwargs["source_total_pages"] or 0)
            pages_processed = int(kwargs["pages_processed"] or 0)
            total_seen = int(kwargs["total_seen"] or 0)

            if page == 1 and source_total_pages > 0:
                job.total_pages += source_total_pages
            job.current_page = pages_processed
            job.total_discovered = total_seen
            job.scanned_count = total_seen
            job.total_downloadable = total_seen
            job.stage = "processing"
            job.updated_at = datetime.now(timezone.utc)
            session.add(job)
            session.commit()

            if not page_items:
                return False

            LOGGER.info(
                "processing source=%s page=%s items=%s processed=%s/%s",
                source["label"],
                page,
                len(page_items),
                processed_items,
                args.max_items if args.max_items > 0 else total_seen,
            )
            for raw in page_items:
                if args.max_items > 0 and processed_items >= args.max_items:
                    return True
                code = str(raw.get("standard_code") or "")
                name = str(raw.get("standard_name") or "")
                job.current_item = f"{code} {name}".strip()
                job.updated_at = datetime.now(timezone.utc)
                session.add(job)
                session.commit()
                LOGGER.info(
                    "[%s/%s] processing %s",
                    processed_items + 1,
                    args.max_items if args.max_items > 0 else total_seen,
                    job.current_item,
                )
                result = process_one(session=session, importer=importer, job=job, raw=raw, args=args)
                processed_items += 1
                LOGGER.info("[%s/%s] %s -> %s", processed_items, args.max_items if args.max_items > 0 else total_seen, job.current_item, result)
                if args.request_interval > 0:
                    time.sleep(args.request_interval)
            return False

        total_seen, pages_processed = stream_discovery(importer, args, page_handler=handle_page)

        job.stage = "completed"
        job.status = "completed_with_errors" if job.failed_count else "completed"
        job.completed_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        job.total_discovered = max(job.total_discovered, total_seen)
        job.scanned_count = max(job.scanned_count, total_seen)
        job.total_downloadable = max(job.total_downloadable, processed_items)
        job.current_page = max(job.current_page, pages_processed)
        session.add(job)
        session.commit()
        LOGGER.info(
            "job %s finished: status=%s new=%s unchanged=%s failed=%s skipped=%s",
            job.id,
            job.status,
            job.new_count,
            job.unchanged_count,
            job.failed_count,
            job.skipped_count,
        )
        emit_run_summary(
            build_run_summary(
                job=job,
                total_items=processed_items,
                mode="initial",
            )
        )
    return 0


def setup_logging(log_file: str) -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_file:
        path = Path(log_file)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(path, encoding="utf-8"))
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Collect national standard PDFs into MinIO and PostgreSQL.")
    parser.add_argument("--scope", default=settings.openstd_crawl_scope or "all_national_standards")
    parser.add_argument("--source-url", default=settings.openstd_source_url)
    parser.add_argument("--max-pages", type=int, default=0)
    parser.add_argument("--max-items", type=int, default=0)
    parser.add_argument(
        "--request-interval",
        type=float,
        default=settings.standard_collector_request_interval_seconds,
    )
    parser.add_argument("--max-retries", type=int, default=max(1, settings.standard_collector_max_retries))
    parser.add_argument(
        "--retry-backoff-seconds",
        type=float,
        default=settings.standard_collector_retry_backoff_seconds,
    )
    parser.add_argument("--retry-failed", action="store_true", help="Retry failed download/upload items only.")
    parser.add_argument("--failed-limit", type=int, default=0, help="Max failed items to retry; 0 means no limit.")
    parser.add_argument("--dry-run", action="store_true", help="Discover or load retry items without downloading or writing DB.")
    parser.add_argument(
        "--log-file",
        default=settings.standard_collector_log_file or str(DEFAULT_LOG_FILE),
    )
    return parser.parse_args()


def main() -> None:
    raise SystemExit(run_collection(parse_args()))


if __name__ == "__main__":
    main()
