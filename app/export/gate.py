"""出域申请、分级判定、审批和强制水印导出。"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.common import biz_error, mask_value, now_iso
from app.config import EXPORTS_DIR
from app.db import connection, insert_audit
from app.logging_config import log_event
from app.observability.trace import TraceRecorder
from app.sandbox.manager import get_sandbox_manager
from app.watermark.engine import get_watermark_engine
from app.watermark.hash_slot import WatermarkError, build_embedding_evidence


VALID_LEVELS = {"L1", "L2", "L3", "L4"}
VALID_DECISIONS = {"approve", "reject", "transfer"}


def decide_channel(data_level: str) -> str:
    if data_level not in VALID_LEVELS:
        raise biz_error(4000, "数据分级必须为 L1-L4")
    return "fast" if data_level in {"L1", "L2"} else "approval"


def _get_request(request_id: str) -> dict[str, Any]:
    with connection() as conn:
        row = conn.execute("SELECT * FROM export_requests WHERE id=?", (request_id,)).fetchone()
    if row is None:
        raise biz_error(1003, "出域申请不存在")
    return dict(row)


def request_export(
    *,
    sandbox_id: str,
    file_path: str,
    applicant: str,
    purpose: str,
    receiver: str,
    data_level: str,
    ip: str,
) -> dict[str, Any]:
    request_id = f"EXP-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6].upper()}"
    with TraceRecorder(
        "export.request",
        actor=applicant,
        target=request_id,
        input={
            "sandbox_id": sandbox_id,
            "file_path": file_path,
            "applicant": applicant,
            "receiver": receiver,
            "data_level": data_level,
            "purpose": purpose,
            "ip": ip,
        },
    ) as trace:
        trace.step(
            "export.request.allocate",
            detail={"request_id": request_id, "id_rule": "EXP-YYYYMMDD-random6"},
        )
        manager = get_sandbox_manager()
        sandbox = manager.get(sandbox_id)
        trace.step(
            "sandbox.lookup",
            detail={"sandbox_id": sandbox_id, "status": sandbox.status, "mounts": sandbox.mounts},
        )
        normalized = sandbox.normalize_path(file_path)
        volume = "raw" if normalized.startswith("/mnt/raw") else "work"
        trace.step(
            "export.path.normalize",
            detail={"input": file_path, "normalized": normalized, "volume": volume},
        )
        if normalized == "/mnt/raw" or normalized.startswith("/mnt/raw/"):
            trace.step(
                "export.policy.raw_volume",
                status="blocked",
                detail={"reason": "原始卷只读，禁止直接出域", "normalized": normalized},
            )
            insert_audit("unauthorized_export", applicant, normalized, "blocked", "原始卷文件禁止直接导出", ip)
            log_event(
                "export.blocked",
                request_id=request_id,
                actor=applicant,
                sandbox_id=sandbox_id,
                reason="raw_volume_export",
                trace_id=trace.trace_id,
            )
            raise biz_error(1005, "原始卷文件禁止直接导出")
        trace.step(
            "export.policy.raw_volume",
            detail={"decision": "ALLOW", "reason": "目标文件位于读写工作卷", "normalized": normalized},
        )
        if normalized not in sandbox.files:
            trace.step("export.policy.file_exists", status="failed", detail={"normalized": normalized})
            raise biz_error(1003, "待出域文件不存在")
        source_size = len(sandbox.files[normalized])
        trace.step(
            "export.policy.file_exists",
            detail={"normalized": normalized, "bytes": source_size, "volume": "work"},
        )
        if not applicant.strip() or not receiver.strip() or not purpose.strip():
            trace.step(
                "export.validation.fields",
                status="failed",
                detail={"applicant": bool(applicant.strip()), "receiver": bool(receiver.strip()), "purpose": bool(purpose.strip())},
            )
            raise biz_error(4000, "申请人、用途、接收方均不能为空")
        trace.step("export.validation.fields", detail={"required": ["applicant", "receiver", "purpose"], "valid": True})

        channel = decide_channel(data_level)
        status = "approved" if channel == "fast" else "pending"
        trace.step(
            "export.policy.classification",
            detail={
                "data_level": data_level,
                "channel": channel,
                "rule": "L1/L2 -> fast; L3/L4 -> approval",
                "initial_status": status,
                "approval_chain": "免审批" if channel == "fast" else "Owner → 安全 → 合规",
            },
        )
        created_at = now_iso()
        approved_at = created_at if channel == "fast" else None

        with connection() as conn:
            conn.execute(
                """
                INSERT INTO export_requests
                (id, sandbox_id, file_path, applicant, purpose, receiver, data_level,
                 channel, status, watermark_id, approver, approval_comment, created_at, approved_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?, ?)
                """,
                (
                    request_id,
                    sandbox_id,
                    normalized,
                    applicant.strip(),
                    purpose.strip(),
                    receiver.strip(),
                    data_level,
                    channel,
                    status,
                    created_at,
                    approved_at,
                ),
            )
        trace.step(
            "database.export_request.persist",
            detail={"table": "export_requests", "request_id": request_id, "status": status},
        )
        insert_audit(
            "export.request",
            applicant.strip(),
            request_id,
            "success",
            f"{data_level} / {channel} / {Path(normalized).name}",
            ip,
        )
        log_event(
            "export.requested",
            request_id=request_id,
            actor=applicant.strip(),
            sandbox_id=sandbox_id,
            file=Path(normalized).name,
            data_level=data_level,
            channel=channel,
            status=status,
            trace_id=trace.trace_id,
        )
        trace.step("audit.write", detail={"action": "export.request", "result": "success"})
        if channel == "fast":
            insert_audit("export.fast_approved", "系统策略", request_id, "success", "L1/L2 命中快速通道", ip)
            log_event(
                "export.approved",
                request_id=request_id,
                actor="system-policy",
                stage=0,
                status="approved",
                route="fast",
                trace_id=trace.trace_id,
            )
            trace.step("export.approval.fast_pass", detail={"policy": "L1/L2", "decision": "approved"})
        else:
            trace.step(
                "export.approval.enqueue",
                detail={"status": "pending", "stage": 0, "next_approver": "Owner"},
            )
        result = {**_get_request(request_id), "trace_id": trace.trace_id}
        trace.set_result(result)
        return result


def approve_export(
    request_id: str,
    *,
    approver: str,
    decision: str,
    comment: str,
    ip: str,
) -> dict[str, Any]:
    with TraceRecorder(
        "export.approval",
        actor=approver,
        target=request_id,
        input={"request_id": request_id, "decision": decision, "comment": comment, "ip": ip},
    ) as trace:
        trace.step("export.request.load", detail={"request_id": request_id})
        request = _get_request(request_id)
        stage = int(request.get("approval_stage") or 0)
        trace.step(
            "approval.policy.channel",
            detail={"channel": request["channel"], "status": request["status"], "stage": stage},
        )
        if request["channel"] != "approval":
            raise biz_error(4000, "快速通道申请无需审批")
        if request["status"] != "pending":
            raise biz_error(4000, "当前申请不在待审批状态")
        if decision not in VALID_DECISIONS:
            raise biz_error(4000, "不支持的审批结论")
        if not approver.strip():
            raise biz_error(4000, "审批人不能为空")

        stage_names = ["Owner", "Security", "Compliance"]
        current_node = stage_names[min(stage, 2)]
        now = now_iso()
        if decision == "approve":
            if stage >= 2:
                new_status = "approved"
                next_stage = 3
                action = "export.approve"
                detail = "合规审批通过，三级会签完成"
                next_node = "EXPORT"
            else:
                new_status = "pending"
                next_stage = stage + 1
                action = "export.approve"
                detail = ["Owner 审批通过，转安全审批", "安全审批通过，转合规审批"][stage]
                next_node = stage_names[next_stage]
        elif decision == "reject":
            new_status = "rejected"
            next_stage = stage
            action = "export.reject"
            detail = comment.strip() or "审批驳回"
            next_node = "REJECTED"
        else:
            new_status = "pending"
            next_stage = stage
            action = "export.transfer"
            detail = comment.strip() or "转交下一审批人"
            next_node = stage_names[min(stage + 1, 2)]
        trace.step(
            "approval.stage.transition",
            detail={
                "from_stage": stage,
                "from_node": current_node,
                "decision": decision,
                "to_stage": next_stage,
                "to_node": next_node,
                "new_status": new_status,
                "detail": detail,
            },
        )

        with connection() as conn:
            conn.execute(
                """
                UPDATE export_requests
                SET status=?, approver=?, approval_comment=?, approval_stage=?, approved_at=?
                WHERE id=?
                """,
                (
                    new_status,
                    approver.strip(),
                    comment.strip(),
                    next_stage,
                    now if new_status == "approved" else None,
                    request_id,
                ),
            )
        trace.step(
            "database.export_request.update",
            detail={"status": new_status, "approval_stage": next_stage, "approved_at": now if new_status == "approved" else None},
        )
        insert_audit(action, approver.strip(), request_id, "success", detail, ip)
        event_name = {
            "approve": "export.approved",
            "reject": "export.rejected",
            "transfer": "export.transferred",
        }[decision]
        log_event(
            event_name,
            request_id=request_id,
            actor=approver.strip(),
            decision=decision,
            stage=next_stage,
            status=new_status,
            next_node=next_node,
            trace_id=trace.trace_id,
        )
        trace.step("audit.write", detail={"action": action, "result": "success"})
        result = {**_get_request(request_id), "trace_id": trace.trace_id}
        trace.set_result(result)
        return result


def execute_export(request_id: str, *, actor: str = "system", ip: str = "127.0.0.1") -> dict[str, Any]:
    with TraceRecorder(
        "export.execute",
        actor=actor,
        target=request_id,
        input={"request_id": request_id, "actor": actor, "ip": ip},
    ) as trace:
        trace.step("export.request.load", detail={"request_id": request_id})
        request = _get_request(request_id)
        trace.step(
            "export.state.check",
            detail={
                "status": request["status"],
                "channel": request["channel"],
                "data_level": request["data_level"],
                "approval_stage": request["approval_stage"],
            },
        )
        if request["status"] == "exported" and request["watermark_id"]:
            trace.step("export.idempotent_reuse", detail={"watermark_id": request["watermark_id"]})
            log_event(
                "export.reused",
                request_id=request_id,
                actor=actor,
                watermark_id=request["watermark_id"],
                trace_id=trace.trace_id,
            )
            result = {**_export_result(request), "trace_id": trace.trace_id}
            trace.set_result(result)
            return result
        if request["status"] != "approved":
            trace.step(
                "export.policy.approval",
                status="blocked",
                detail={"reason": "申请未通过审批", "status": request["status"]},
            )
            insert_audit("unauthorized_export", actor, request_id, "blocked", "未审批申请尝试执行导出", ip)
            log_event(
                "export.blocked",
                request_id=request_id,
                actor=actor,
                reason="not_approved",
                status=request["status"],
                trace_id=trace.trace_id,
            )
            raise biz_error(1005, "申请未通过审批，禁止导出")
        trace.step("export.policy.approval", detail={"approved": True, "channel": request["channel"]})

        manager = get_sandbox_manager()
        sandbox = manager.get(request["sandbox_id"])
        trace.step(
            "export.sandbox.lookup",
            detail={"sandbox_id": sandbox.id, "status": sandbox.status, "path": request["file_path"]},
        )
        try:
            source = sandbox.read_file(request["file_path"])
        except Exception as exc:
            if hasattr(exc, "code"):
                raise
            raise biz_error(1003, "待导出文件不存在") from exc
        source_hash = hashlib.sha256(source).hexdigest()
        trace.step(
            "export.source.read",
            detail={"path": request["file_path"], "bytes": len(source), "sha256": source_hash},
        )

        now = datetime.now().astimezone()
        file_id = source_hash[:12]

        def watermark_observer(phase: str, detail: dict[str, Any], status: str) -> None:
            trace.step(phase, status=status, detail=detail)

        engine = get_watermark_engine()
        try:
            # 强制出口：任何文件都必须先经过水印引擎，不能原样落盘。
            log_event(
                "watermark.embedding",
                request_id=request_id,
                actor=actor,
                algorithm="hash-slot-v1",
                method="sha256_hash_slot+amount_lsb+3x_repeat+crc32",
                source_bytes=len(source),
                trace_id=trace.trace_id,
            )
            embedded = engine.embed(
                source,
                user_id=request["applicant"],
                export_time=now,
                file_id=file_id,
                request_id=request_id,
                observer=watermark_observer,
            )
            evidence = build_embedding_evidence(source, embedded)
            extracted = engine.extract(embedded.data)
            payload_match = extracted.payload == embedded.payload
            if extracted.watermark_id != embedded.watermark_id or not payload_match:
                raise WatermarkError(
                    f"导出后水印回读不一致: expected={embedded.watermark_id} actual={extracted.watermark_id}"
                )
            evidence["verification"] = {
                "status": "success",
                "expected_watermark_id": embedded.watermark_id,
                "extracted_watermark_id": extracted.watermark_id,
                "watermark_id_match": extracted.watermark_id == embedded.watermark_id,
                "payload_match": payload_match,
                "match_score": extracted.match_score,
                "unknown_bits": extracted.unknown_bits,
                "corrected_bits": extracted.corrected_bits,
                "crc32_verified": True,
            }
        except WatermarkError as exc:
            insert_audit("watermark.embed", actor, request_id, "failed", str(exc), ip)
            log_event(
                "watermark.embed.failed",
                request_id=request_id,
                actor=actor,
                algorithm="hash-slot-v1",
                error=str(exc),
                trace_id=trace.trace_id,
            )
            raise biz_error(1006, f"水印嵌入失败: {exc}") from exc

        trace.step(
            "watermark.roundtrip.verify",
            detail={
                "watermark_id": extracted.watermark_id,
                "watermark_id_match": True,
                "payload_match": payload_match,
                "match_score": extracted.match_score,
                "unknown_bits": extracted.unknown_bits,
                "corrected_bits": extracted.corrected_bits,
                "changed_rows": evidence["diff"]["changed_rows"],
                "max_amount_delta": evidence["diff"]["max_amount_delta"],
            },
        )

        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        source_name = Path(request["file_path"]).name
        output_name = f"{embedded.watermark_id}_{source_name}"
        output_path = EXPORTS_DIR / output_name
        output_path.write_bytes(embedded.data)
        file_hash = hashlib.sha256(embedded.data).hexdigest()
        trace.step(
            "export.file.write",
            detail={
                "path": str(output_path),
                "filename": output_name,
                "bytes": len(embedded.data),
                "sha256": file_hash,
                "watermark_id": embedded.watermark_id,
                "changed_rows": embedded.changed_rows,
            },
        )
        export_time = now.isoformat(timespec="seconds")
        approval_chain = (
            "快速通道（L1/L2 免审批）"
            if request["channel"] == "fast"
            else "Owner → 安全 → 合规"
        )

        with connection() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO watermark_mappings
                (watermark_id, request_id, user_id, export_time, file_id, file_hash,
                 sandbox_id, data_scope, approval_chain, evidence_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    embedded.watermark_id,
                    request_id,
                    request["applicant"],
                    export_time,
                    file_id,
                    file_hash,
                    request["sandbox_id"],
                    f"{request['sandbox_id']}:{request['file_path']}",
                    approval_chain,
                    json.dumps(evidence, ensure_ascii=False, separators=(",", ":"), default=str),
                ),
            )
            conn.execute(
                "UPDATE export_requests SET status='exported', watermark_id=? WHERE id=?",
                (embedded.watermark_id, request_id),
            )
        trace.step(
            "database.export.persist",
            detail={
                "tables": ["watermark_mappings", "export_requests"],
                "watermark_id": embedded.watermark_id,
                "status": "exported",
                "approval_chain": approval_chain,
            },
        )
        insert_audit(
            "watermark.embed",
            actor,
            embedded.watermark_id,
            "success",
            f"{embedded.frame_bytes} bytes frame / {embedded.changed_rows} rows adjusted",
            ip,
        )
        insert_audit("export.execute", actor, request_id, "success", f"output={output_name}", ip)
        log_event(
            "watermark.embedded",
            watermark_id=embedded.watermark_id,
            request_id=request_id,
            actor=actor,
            algorithm="hash-slot-v1",
            method="sha256_hash_slot+amount_lsb+3x_repeat+crc32",
            rows=embedded.diagnostics.get("rows", 0),
            changed_rows=embedded.changed_rows,
            frame_bytes=embedded.frame_bytes,
            verified=True,
            match_score=extracted.match_score,
            output=output_name,
            trace_id=trace.trace_id,
        )
        log_event(
            "export.completed",
            request_id=request_id,
            watermark_id=embedded.watermark_id,
            actor=actor,
            output=output_name,
            bytes=len(embedded.data),
            trace_id=trace.trace_id,
        )
        trace.step(
            "audit.write",
            detail={"actions": ["watermark.embed", "export.execute"], "result": "success"},
        )
        result = {**_export_result(_get_request(request_id)), "trace_id": trace.trace_id}
        trace.set_result(result)
        return result


def _export_result(request: dict[str, Any]) -> dict[str, Any]:
    with connection() as conn:
        mapping = conn.execute(
            "SELECT * FROM watermark_mappings WHERE watermark_id=?",
            (request["watermark_id"],),
        ).fetchone()
    if mapping is None:
        raise biz_error(1006, "水印映射记录缺失")
    watermark_id = request["watermark_id"]
    filename = f"{watermark_id}_{Path(request['file_path']).name}"
    return {
        "request_id": request["id"],
        "watermark_id": watermark_id,
        "filename": filename,
        "download_url": f"/api/watermark/{watermark_id}/download",
        "mapping": dict(mapping),
    }


def request_detail(request_id: str, *, include_preview: bool = True) -> dict[str, Any]:
    request = _get_request(request_id)
    result: dict[str, Any] = {"request": request}
    if include_preview:
        try:
            rows = get_sandbox_manager().preview(request["sandbox_id"], request["file_path"], 5)
        except Exception:
            rows = []
        masked: list[dict[str, str]] = []
        for row in rows:
            masked.append(
                {
                    key: (
                        mask_value(value, 3)
                        if key.lower() in {"name", "phone", "customer_id", "receiver"}
                        else value
                    )
                    for key, value in row.items()
                }
            )
        result["preview"] = masked
    return result


def list_requests(status: str | None = None) -> list[dict[str, Any]]:
    sql = "SELECT * FROM export_requests"
    params: tuple[Any, ...] = ()
    if status and status != "all":
        sql += " WHERE status=?"
        params = (status,)
    sql += " ORDER BY created_at DESC"
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(row) for row in rows]


def risk_summary(request: dict[str, Any]) -> str:
    level = request["data_level"]
    if level == "L4":
        return "高风险：涉及敏感评分与受限字段，建议最小授权"
    if level == "L3":
        return "中高风险：包含个人信息，需核验接收方用途"
    return "低风险"


def approval_deadline(created_at: str) -> str:
    try:
        created = datetime.fromisoformat(created_at)
        return (created + timedelta(hours=24)).isoformat(timespec="seconds")
    except ValueError:
        return created_at


def parse_level(value: str) -> str:
    normalized = (value or "").upper()
    if not re.fullmatch(r"L[1-4]", normalized):
        raise biz_error(4000, "无效的数据分级")
    return normalized

