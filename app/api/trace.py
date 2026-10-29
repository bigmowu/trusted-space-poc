from __future__ import annotations

from fastapi import APIRouter, File, Request, UploadFile

from app.common import biz_error, client_ip, ok
from app.config import UPLOAD_MAX_BYTES
from app.db import insert_audit
from app.logging_config import log_event
from app.observability.trace import TraceRecorder
from app.trace.repository import trace_by_watermark
from app.watermark.engine import get_watermark_engine
from app.watermark.hash_slot import WatermarkError


router = APIRouter(prefix="/api/trace", tags=["trace"])


@router.get("/{watermark_id}")
def trace(watermark_id: str, request: Request):
    with TraceRecorder(
        "trace.query",
        actor="alice",
        target=watermark_id,
        input={"watermark_id": watermark_id, "ip": client_ip(request)},
    ) as trace_recorder:
        trace_recorder.step("watermark.mapping.lookup", detail={"watermark_id": watermark_id})
        result = trace_by_watermark(watermark_id, 100.0)
        trace_recorder.step(
            "provenance.timeline.build",
            detail={
                "request_id": result["request"]["id"],
                "sandbox_id": result["mapping"]["sandbox_id"],
                "timeline_steps": len(result["timeline"]),
                "approval_chain": result["mapping"]["approval_chain"],
            },
        )
        insert_audit("trace.query", "alice", watermark_id, "success", "按水印 ID 查询完整链路", client_ip(request))
        trace_recorder.step("audit.write", detail={"action": "trace.query", "result": "success"})
        response = {**result, "trace_id": trace_recorder.trace_id}
        trace_recorder.set_result(response)
        return ok(response)


@router.post("/file")
async def trace_file(request: Request, file: UploadFile = File(...)):
    filename = file.filename or "upload.csv"
    if not filename.lower().endswith(".csv"):
        raise biz_error(4000, "仅支持 .csv 文件")
    content = await file.read()
    if len(content) > UPLOAD_MAX_BYTES:
        raise biz_error(4000, "文件超过 20MB 限制")
    ip = client_ip(request)
    with TraceRecorder(
        "watermark.extract",
        actor="upload",
        target=filename,
        input={"filename": filename, "bytes": len(content), "mode": "multipart_upload", "ip": ip},
    ) as trace_recorder:
        log_event(
            "watermark.extracting",
            source="multipart",
            filename=filename,
            bytes=len(content),
            algorithm="hash-slot-v1",
            method="sha256_hash_slot+amount_lsb+3x_repeat+crc32",
            trace_id=trace_recorder.trace_id,
        )
        try:
            extracted = get_watermark_engine().extract(
                content,
                observer=lambda phase, detail, status: trace_recorder.step(phase, status=status, detail=detail),
            )
        except WatermarkError as exc:
            insert_audit("trace.file", "unknown", filename, "failed", str(exc), ip)
            log_event(
                "watermark.extract.failed",
                source="multipart",
                filename=filename,
                algorithm="hash-slot-v1",
                method="sha256_hash_slot+amount_lsb+3x_repeat+crc32",
                error=str(exc),
                trace_id=trace_recorder.trace_id,
            )
            raise biz_error(1006, str(exc)) from exc
        trace_recorder.step(
            "watermark.mapping.lookup",
            detail={"watermark_id": extracted.watermark_id, "match_score": extracted.match_score},
        )
        result = trace_by_watermark(extracted.watermark_id, extracted.match_score)
        trace_recorder.step(
            "provenance.timeline.build",
            detail={
                "request_id": result["request"]["id"],
                "sandbox_id": result["mapping"]["sandbox_id"],
                "timeline_steps": len(result["timeline"]),
            },
        )
        insert_audit(
            "trace.file",
            extracted.payload.get("user_id", "unknown"),
            filename,
            "success",
            f"识别水印 {extracted.watermark_id}，匹配度 {extracted.match_score}%",
            ip,
        )
        log_event(
            "watermark.extracted",
            watermark_id=extracted.watermark_id,
            user=extracted.payload.get("user_id", "unknown"),
            source="multipart",
            filename=filename,
            algorithm="hash-slot-v1",
            method="sha256_hash_slot+amount_lsb+3x_repeat+crc32",
            match_score=extracted.match_score,
            unknown_bits=extracted.unknown_bits,
            corrected_bits=extracted.corrected_bits,
            trace_id=trace_recorder.trace_id,
        )
        trace_recorder.step("audit.write", detail={"action": "trace.file", "result": "success"})
        response = {
            "extracted": extracted.payload,
            "match_score": extracted.match_score,
            "unknown_bits": extracted.unknown_bits,
            "diagnostics": extracted.diagnostics,
            "trace": result,
            "trace_id": trace_recorder.trace_id,
        }
        trace_recorder.set_result(response)
        return ok(response)

