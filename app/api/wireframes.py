from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from app.db.session import SessionLocal
from app.services.audit import finish_call, logged_call
from app.services.wireframes import wireframe_service


router = APIRouter(prefix="/api/wireframes", tags=["wireframes"])


@router.post("/image")
async def generate_image_wireframe(file: UploadFile = File(...)):
    filename = file.filename or "image.png"
    suffix = Path(filename).suffix.lower()
    if suffix not in {".jpg", ".jpeg", ".png", ".webp", ".bmp"}:
        raise HTTPException(status_code=400, detail=f"不支持的图片格式: {suffix or 'unknown'}")
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="上传图片为空")
    with SessionLocal() as session:
        with logged_call(
            session,
            interface_type="rest",
            tool_or_endpoint="POST /api/wireframes/image",
            request={"filename": filename, "size_bytes": len(content)},
        ) as call_id:
            try:
                result = wireframe_service.generate_uploaded_image_wireframe(
                    filename=filename,
                    content=content,
                    media_type=file.content_type or "application/octet-stream",
                )
            except Exception as exc:
                raise HTTPException(status_code=500, detail=str(exc)) from exc
            finish_call(session, call_id, result)
            return result
