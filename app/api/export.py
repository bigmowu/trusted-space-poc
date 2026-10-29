from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.common import biz_error, client_ip, ok
from app.config import EXPORTS_DIR
from app.db import connection
from app.export.gate import (
    approve_export,
    execute_export,
    list_requests,
    request_detail,
    request_export,
    risk_summary,
)


router = APIRouter(prefix="/api/export", tags=["export"])


class ExportRequestBody(BaseModel):
    sandbox_id: str
    file_path: str
    applicant: str
    purpose: str
    receiver: str
    data_level: str


class ApprovalBody(BaseModel):
    approver: str
    decision: str = "approve"
    comment: str = ""


class ExecuteBody(BaseModel):
    actor: str = "system"


@router.post("/request")
def create_request(body: ExportRequestBody, request: Request):
    result = request_export(
        sandbox_id=body.sandbox_id,
        file_path=body.file_path,
        applicant=body.applicant,
        purpose=body.purpose,
        receiver=body.receiver,
        data_level=body.data_level,
        ip=client_ip(request),
    )
    return ok(result)


@router.get("/requests")
def get_requests(status: str = Query(default="all")):
    items = []
    for item in list_requests(status):
        enriched = dict(item)
        enriched["risk"] = risk_summary(item)
        enriched["filename"] = Path(item["file_path"]).name
        items.append(enriched)
    return ok({"items": items, "total": len(items)})


@router.get("/{request_id}")
def get_request_detail(request_id: str):
    detail = request_detail(request_id)
    detail["risk"] = risk_summary(detail["request"])
    return ok(detail)


@router.post("/{request_id}/approve")
def approve(request_id: str, body: ApprovalBody, http_request: Request):
    result = approve_export(
        request_id,
        approver=body.approver,
        decision=body.decision,
        comment=body.comment,
        ip=client_ip(http_request),
    )
    return ok(result)


@router.post("/{request_id}/execute")
def execute(request_id: str, request: Request, body: ExecuteBody | None = None):
    actor = (body.actor if body else None) or "system"
    ip = client_ip(request)
    return ok(execute_export(request_id, actor=actor, ip=ip))


@router.get("/{request_id}/download")
def download_export(request_id: str):
    with connection() as conn:
        row = conn.execute("SELECT * FROM export_requests WHERE id=?", (request_id,)).fetchone()
    if row is None or not row["watermark_id"]:
        raise biz_error(1003, "导出文件不存在")
    filename = f"{row['watermark_id']}_{Path(row['file_path']).name}"
    path = EXPORTS_DIR / filename
    if not path.exists():
        raise biz_error(1003, "导出文件已丢失")
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=filename)

