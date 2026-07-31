from __future__ import annotations

import json
import os
import tempfile
import uuid
import zipfile
from io import BytesIO
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, BackgroundTasks, File, HTTPException, Query, UploadFile
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select

from app.db.session import SessionLocal
from app.core.config import settings
from app.models.entities import CallLog, Standard, StandardCrawlItem, StandardCrawlJob, StandardMaterializeJob
from app.services.audit import finish_call, logged_call
from app.services.openstd_crawl import openstd_crawl_service
from app.services.standards import MARKDOWN_KINDS, standard_markdown_object_key, standard_service

router = APIRouter(prefix="/api/standards", tags=["standards"])


@router.post("/refresh-pdfs")
def refresh_pdfs():
    raise HTTPException(status_code=410, detail="本地目录刷新已停用，请使用标准 PDF 上传。")


@router.post("/upload")
async def upload_standards(files: list[UploadFile] = File(...)):
    workdir = Path(settings.standard_workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    uploaded = []
    with SessionLocal() as session:
        with logged_call(session, interface_type="rest", tool_or_endpoint="POST /api/standards/upload") as call_id:
            for file in files:
                filename = file.filename or "standard.pdf"
                if Path(filename).suffix.lower() != ".pdf":
                    raise HTTPException(status_code=400, detail=f"只支持 PDF 文件: {filename}")
                fd, temp_name = tempfile.mkstemp(prefix="standard_upload_", suffix=".pdf", dir=str(workdir))
                os.close(fd)
                temp_path = Path(temp_name)
                try:
                    size = 0
                    with temp_path.open("wb") as output:
                        while True:
                            chunk = await file.read(1024 * 1024)
                            if not chunk:
                                break
                            size += len(chunk)
                            output.write(chunk)
                    if size <= 0:
                        raise HTTPException(status_code=400, detail=f"上传文件为空: {filename}")
                    uploaded.append(
                        standard_service.upload_pdf(
                            session,
                            pdf_path=temp_path,
                            filename=filename,
                            media_type=file.content_type or "application/pdf",
                        )
                    )
                finally:
                    temp_path.unlink(missing_ok=True)
            result = {"count": len(uploaded), "standards": uploaded}
            finish_call(session, call_id, result)
            return result


@router.get("")
def list_standards():
    with SessionLocal() as session:
        standards = session.scalars(select(Standard).order_by(Standard.updated_at.desc(), Standard.created_at.desc())).all()
        return [standard_to_dict(session, item) for item in standards]


@router.post("/index/rebuild")
def rebuild_standard_search_index():
    with SessionLocal() as session:
        with logged_call(session, interface_type="rest", tool_or_endpoint="POST /api/standards/index/rebuild") as call_id:
            try:
                result = standard_service.rebuild_search_index(session)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finish_call(
                session,
                call_id,
                {
                    "indexed_count": result["indexed_count"],
                    "failed_count": result["failed_count"],
                    "embedding_model": result["embedding_model"],
                    "embedding_dimensions": result["embedding_dimensions"],
                },
            )
            return result


@router.post("/{standard_id}/index/rebuild")
def rebuild_one_standard_search_index(standard_id: str):
    with SessionLocal() as session:
        with logged_call(
            session,
            interface_type="rest",
            tool_or_endpoint="POST /api/standards/{standard_id}/index/rebuild",
            request={"standard_id": standard_id},
            standard_id=standard_id,
        ) as call_id:
            try:
                result = standard_service.index_standard(session, standard_id)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc
            finish_call(session, call_id, result)
            return result


@router.get("/search/history")
def search_history(limit: int = 0):
    with SessionLocal() as session:
        statement = (
            select(CallLog)
            .where(CallLog.tool_or_endpoint == "POST /api/standards/search")
            .order_by(CallLog.created_at.desc())
        )
        if limit > 0:
            statement = statement.limit(max(1, min(limit, 1000)))
        rows = session.scalars(statement).all()
        return [search_log_to_dict(row) for row in rows]


@router.post("/openstd/crawl")
def create_openstd_crawl_job(background_tasks: BackgroundTasks):
    with SessionLocal() as session:
        with logged_call(session, interface_type="rest", tool_or_endpoint="POST /api/standards/openstd/crawl") as call_id:
            result = openstd_crawl_service.create_job(session)
            if result.get("created") or result.get("status") in {"queued", "running"}:
                background_tasks.add_task(run_openstd_crawl_job, result["id"])
            finish_call(session, call_id, result)
            return result


@router.get("/openstd/crawl/latest")
def get_latest_openstd_crawl_job():
    with SessionLocal() as session:
        result = openstd_crawl_service.latest_job(session)
        if result is None:
            return {"status": "none"}
        return result


@router.get("/openstd/crawl/{job_id}")
def get_openstd_crawl_job(job_id: str):
    with SessionLocal() as session:
        job = session.get(StandardCrawlJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="openstd crawl job not found")
        return openstd_crawl_service.job_to_dict(session, job)


@router.get("/openstd/crawl/{job_id}/items")
def list_openstd_crawl_items(job_id: str, status: str = Query("", alias="status"), limit: int = 100):
    with SessionLocal() as session:
        job = session.get(StandardCrawlJob, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="openstd crawl job not found")
        statement = select(StandardCrawlItem).where(StandardCrawlItem.job_id == job_id)
        if status:
            statement = statement.where(StandardCrawlItem.status == status)
        statement = statement.order_by(StandardCrawlItem.created_at.desc()).limit(max(1, min(limit, 500)))
        return [openstd_crawl_service.item_to_dict(item) for item in session.scalars(statement).all()]


@router.get("/{standard_id}")
def get_standard(standard_id: str):
    with SessionLocal() as session:
        standard = session.get(Standard, standard_id)
        if standard is None:
            raise HTTPException(status_code=404, detail="standard not found")
        return standard_to_dict(session, standard)


@router.post("/{standard_id}/materialize")
def materialize_standard(standard_id: str, background_tasks: BackgroundTasks):
    with SessionLocal() as session:
        with logged_call(
            session,
            interface_type="rest",
            tool_or_endpoint="POST /api/standards/{standard_id}/materialize",
            request={"standard_id": standard_id},
            standard_id=standard_id,
        ) as call_id:
            standard = session.get(Standard, standard_id)
            if standard is None:
                raise HTTPException(status_code=404, detail="standard not found")
            running_job = latest_materialize_job(session, standard_id, statuses={"running"})
            if running_job is not None:
                result = materialize_job_to_dict(running_job)
                finish_call(session, call_id, result)
                return result
            job = StandardMaterializeJob(
                id=uuid.uuid4().hex,
                standard_id=standard_id,
                status="running",
                stage="starting",
                progress_percent=1,
                message="解析记录已创建，准备解析标准 PDF。",
            )
            standard.status = "processing"
            session.add(job)
            session.add(standard)
            session.commit()
            result = materialize_job_to_dict(job)
            background_tasks.add_task(run_standard_materialize_job, standard_id, job.id)
            finish_call(session, call_id, result)
            return result


@router.get("/{standard_id}/materialize-status")
def get_materialize_status(standard_id: str):
    with SessionLocal() as session:
        job = latest_materialize_job(session, standard_id)
        if job is not None:
            return materialize_job_to_dict(job)
    return standard_service.get_materialize_progress(standard_id)


@router.get("/{standard_id}/markdown/{kind}", response_class=PlainTextResponse)
def get_standard_markdown(standard_id: str, kind: str):
    if kind not in MARKDOWN_KINDS:
        raise HTTPException(status_code=400, detail="unsupported markdown kind")
    with SessionLocal() as session:
        try:
            result = standard_service.get_markdown(session, standard_id, kind)
        except Exception as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return result["markdown"]


@router.get("/{standard_id}/markdown.zip")
def download_standard_markdown_zip(standard_id: str):
    with SessionLocal() as session:
        standard = session.get(Standard, standard_id)
        if standard is None:
            raise HTTPException(status_code=404, detail="standard not found")
        buffer = BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for kind in ("overview", "structure", "logic", "body"):
                try:
                    result = standard_service.get_markdown(session, standard_id, kind)
                except Exception as exc:
                    raise HTTPException(status_code=404, detail=f"{kind}.md not found: {exc}") from exc
                archive.writestr(f"{kind}.md", result["markdown"])
        buffer.seek(0)
        safe_name = "".join(ch if ch.isascii() and (ch.isalnum() or ch in "._-") else "_" for ch in standard.name) or standard_id
        download_name = f"{standard.name or standard_id}-markdown.zip"
        return Response(
            content=buffer.getvalue(),
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{safe_name}-markdown.zip"; '
                    f"filename*=UTF-8''{quote(download_name, safe='')}"
                )
            },
        )


@router.post("/search")
def search_standards(query: str = Query(...), limit: int = 5):
    with SessionLocal() as session:
        with logged_call(
            session,
            interface_type="rest",
            tool_or_endpoint="POST /api/standards/search",
            request={"query": query, "limit": limit},
        ) as call_id:
            result = standard_service.search(session, query=query, limit=limit)
            search_id = uuid.uuid4().hex
            saved_match_count = standard_service.save_standard_matches(
                session,
                matches=result.get("matches") or [],
                search_id=search_id,
            )
            finish_call(
                session,
                call_id,
                {
                    "search_id": search_id,
                    "mode": result.get("mode"),
                    "embedding_model": result.get("embedding_model"),
                    "embedding_dimensions": result.get("embedding_dimensions"),
                    "match_count": len(result.get("matches") or []),
                    "saved_match_count": saved_match_count,
                    "excluded_count": len(result.get("excluded") or []),
                    "matches": [
                        {
                            "standard_id": item.get("standard_id"),
                            "standard_name": item.get("standard_name"),
                            "decision": item.get("decision"),
                            "score": item.get("score"),
                        }
                        for item in (result.get("matches") or [])[:5]
                    ],
                    "message": result.get("message"),
                },
            )
            return result


@router.post("/match-video/{video_id}")
def match_video(video_id: str, analysis_text: str = Query(...), limit: int = 5):
    with SessionLocal() as session:
        with logged_call(
            session,
            interface_type="rest",
            tool_or_endpoint="POST /api/standards/match-video/{video_id}",
            request={"video_id": video_id, "analysis_text_chars": len(analysis_text), "limit": limit},
            video_id=video_id,
        ) as call_id:
            result = standard_service.match_video(session, video_id=video_id, analysis_text=analysis_text, limit=limit)
            finish_call(
                session,
                call_id,
                {
                    "match_count": len(result.get("matches") or []),
                    "matches": [
                        {
                            "standard_id": item.get("standard_id"),
                            "score": item.get("score"),
                        }
                        for item in (result.get("matches") or [])[:5]
                    ],
                },
            )
            return result


def standard_to_dict(session, standard: Standard) -> dict:
    return {
        "id": standard.id,
        "name": standard.name,
        "code": standard.code,
        "status": standard.status,
        "source_pdf_bucket": standard.source_pdf_bucket,
        "source_pdf_object_key": standard.source_pdf_object_key,
        "index_status": standard.index_status,
        "indexed_at": standard.indexed_at.isoformat() if standard.indexed_at else None,
        "index_error": standard.index_error,
        "artifacts": {kind: standard_markdown_object_key(standard.id, kind) for kind in sorted(MARKDOWN_KINDS)},
        "created_at": standard.created_at.isoformat() if standard.created_at else None,
        "updated_at": standard.updated_at.isoformat() if standard.updated_at else None,
    }


def run_standard_materialize_job(standard_id: str, job_id: str) -> None:
    with SessionLocal() as session:
        with logged_call(
            session,
            interface_type="background",
            tool_or_endpoint="standard_materialize_job",
            request={"standard_id": standard_id, "job_id": job_id},
            standard_id=standard_id,
        ) as call_id:
            result = standard_service.materialize(session, standard_id, job_id=job_id)
            finish_call(session, call_id, result)


def run_openstd_crawl_job(job_id: str) -> None:
    with SessionLocal() as session:
        with logged_call(
            session,
            interface_type="background",
            tool_or_endpoint="openstd_crawl_job",
            request={"job_id": job_id},
        ) as call_id:
            result = openstd_crawl_service.run_job(session, job_id)
            finish_call(session, call_id, result)


def latest_materialize_job(
    session,
    standard_id: str,
    *,
    statuses: set[str] | None = None,
) -> StandardMaterializeJob | None:
    statement = select(StandardMaterializeJob).where(StandardMaterializeJob.standard_id == standard_id)
    if statuses:
        statement = statement.where(StandardMaterializeJob.status.in_(statuses))
    statement = statement.order_by(StandardMaterializeJob.created_at.desc())
    return session.scalars(statement).first()


def materialize_job_to_dict(job: StandardMaterializeJob) -> dict:
    return {
        "standard_id": job.standard_id,
        "job_id": job.id,
        "status": job.status,
        "stage": job.stage,
        "progress_percent": job.progress_percent,
        "message": job.message,
        "error": job.error_message,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
        "completed_at": job.completed_at.isoformat() if job.completed_at else None,
    }


def parse_log_json(value: str) -> dict[str, Any]:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def search_log_to_dict(row: CallLog) -> dict:
    request = parse_log_json(row.request_summary)
    response = parse_log_json(row.response_summary)
    return {
        "id": row.id,
        "query": request.get("query", ""),
        "limit": request.get("limit"),
        "status": row.status,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "duration_ms": row.duration_ms,
        "error_message": row.error_message,
        "mode": response.get("mode"),
        "embedding_model": response.get("embedding_model"),
        "embedding_dimensions": response.get("embedding_dimensions"),
        "match_count": response.get("match_count"),
        "excluded_count": response.get("excluded_count"),
        "matches": response.get("matches") if isinstance(response.get("matches"), list) else [],
        "message": response.get("message"),
    }
