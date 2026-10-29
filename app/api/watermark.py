from __future__ import annotations

import base64
import binascii
import mimetypes
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel

from app.common import biz_error, client_ip, ok
from app.config import EXPORTS_DIR
from app.db import connection, insert_audit
from app.logging_config import log_event
from app.observability.trace import TraceRecorder
from app.trace.repository import list_exported_files, watermark_evidence as get_watermark_evidence
from app.watermark.engine import get_watermark_engine
from app.watermark.hash_slot import WatermarkError


router = APIRouter(prefix="/api/watermark", tags=["watermark"])


class ExtractBody(BaseModel):
    content_base64: str


@router.get("/list")
def watermark_list():
    items = list_exported_files()
    return ok({"items": items, "total": len(items)})


@router.post("/extract")
def extract(body: ExtractBody, request: Request):
    ip = client_ip(request)
    with TraceRecorder(
        "watermark.extract",
        actor="upload",
        target="base64",
        input={"encoding": "base64", "encoded_chars": len(body.content_base64), "ip": ip},
    ) as trace:
        try:
            data = base64.b64decode(body.content_base64, validate=True)
            trace.step("input.base64.decode", detail={"bytes": len(data)})
        except (binascii.Error, ValueError) as exc:
            trace.step("input.base64.decode", status="failed", detail={"reason": "invalid base64"})
            raise biz_error(4000, "content_base64 不是合法 Base64") from exc
        log_event(
            "watermark.extracting",
            source="base64",
            bytes=len(data),
            algorithm="hash-slot-v1",
            method="sha256_hash_slot+amount_lsb+3x_repeat+crc32",
            trace_id=trace.trace_id,
        )
        try:
            result = get_watermark_engine().extract(
                data,
                observer=lambda phase, detail, status: trace.step(phase, status=status, detail=detail),
            )
        except WatermarkError as exc:
            insert_audit("watermark.extract", "unknown", "base64", "failed", str(exc), ip)
            log_event(
                "watermark.extract.failed",
                source="base64",
                algorithm="hash-slot-v1",
                method="sha256_hash_slot+amount_lsb+3x_repeat+crc32",
                error=str(exc),
                trace_id=trace.trace_id,
            )
            raise biz_error(1006, str(exc)) from exc
        insert_audit(
            "watermark.extract",
            result.payload.get("user_id", "unknown"),
            result.watermark_id,
            "success",
            f"match={result.match_score}%",
            ip,
        )
        log_event(
            "watermark.extracted",
            watermark_id=result.watermark_id,
            user=result.payload.get("user_id", "unknown"),
            source="base64",
            algorithm="hash-slot-v1",
            method="sha256_hash_slot+amount_lsb+3x_repeat+crc32",
            match_score=result.match_score,
            unknown_bits=result.unknown_bits,
            corrected_bits=result.corrected_bits,
            trace_id=trace.trace_id,
        )
        trace.step("audit.write", detail={"action": "watermark.extract", "result": "success"})
        response = {
            "payload": result.payload,
            "watermark_id": result.watermark_id,
            "match_score": result.match_score,
            "unknown_bits": result.unknown_bits,
            "diagnostics": result.diagnostics,
            "trace_id": trace.trace_id,
        }
        trace.set_result(response)
        return ok(response)


@router.get("/{watermark_id}/evidence")
def watermark_evidence(watermark_id: str):
    return ok(get_watermark_evidence(watermark_id))


@router.get("/{watermark_id}/download")
def download_watermark(watermark_id: str):
    with connection() as conn:
        mapping = conn.execute(
            "SELECT * FROM watermark_mappings WHERE watermark_id=?",
            (watermark_id,),
        ).fetchone()
        if mapping is None:
            raise biz_error(1003, "水印记录不存在")
        request = conn.execute(
            "SELECT * FROM export_requests WHERE id=?",
            (mapping["request_id"],),
        ).fetchone()
    if request is None:
        raise biz_error(1003, "关联申请不存在")
    filename = f"{watermark_id}_{Path(request['file_path']).name}"
    path = EXPORTS_DIR / filename
    if not path.exists():
        raise biz_error(1003, "导出文件已丢失")
    media_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return FileResponse(path, media_type=media_type, filename=filename)

