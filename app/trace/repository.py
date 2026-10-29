"""水印映射查询与溯源时间线组装。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.common import biz_error
from app.db import connection


def get_mapping(watermark_id: str) -> dict[str, Any]:
    with connection() as conn:
        row = conn.execute(
            "SELECT * FROM watermark_mappings WHERE watermark_id=?",
            (watermark_id,),
        ).fetchone()
    if row is None:
        raise biz_error(1003, "水印 ID 不存在")
    return dict(row)


def watermark_evidence(watermark_id: str) -> dict[str, Any]:
    """返回导出时持久化的水印写入证据，供 API/CLI 共用。"""
    mapping = get_mapping(watermark_id)
    raw = mapping.get("evidence_json")
    if not raw:
        return {
            "available": False,
            "watermark_id": watermark_id,
            "message": "该水印生成于证据档案功能启用前，请重新执行一次导出",
            "mapping": {
                "user_id": mapping["user_id"],
                "export_time": mapping["export_time"],
                "file_id": mapping["file_id"],
                "file_hash": mapping["file_hash"],
                "approval_chain": mapping["approval_chain"],
            },
        }
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise biz_error(1006, "水印证据档案损坏") from exc


def _request(request_id: str) -> dict[str, Any]:
    with connection() as conn:
        row = conn.execute("SELECT * FROM export_requests WHERE id=?", (request_id,)).fetchone()
    if row is None:
        raise biz_error(1003, "关联出域申请不存在")
    return dict(row)


def _sandbox(sandbox_id: str) -> dict[str, Any]:
    with connection() as conn:
        row = conn.execute("SELECT * FROM sandboxes WHERE id=?", (sandbox_id,)).fetchone()
    return dict(row) if row else {"id": sandbox_id, "created_at": ""}


def _generation_time(sandbox_id: str, request_created_at: str) -> str:
    with connection() as conn:
        row = conn.execute(
            """
            SELECT created_at FROM audit_logs
            WHERE action='sandbox.exec' AND target=? AND result='success' AND created_at<=?
            ORDER BY id DESC LIMIT 1
            """,
            (sandbox_id, request_created_at),
        ).fetchone()
    return row["created_at"] if row else request_created_at


def _ingest_time(filename: str, fallback: str) -> str:
    table_name = Path(filename).stem
    with connection() as conn:
        row = conn.execute(
            "SELECT created_at FROM data_assets WHERE table_name=?",
            (table_name,),
        ).fetchone()
    return row["created_at"] if row else fallback


def trace_by_watermark(watermark_id: str, match_score: float = 100.0) -> dict[str, Any]:
    mapping = get_mapping(watermark_id)
    request = _request(mapping["request_id"])
    sandbox = _sandbox(mapping["sandbox_id"])
    filename = Path(request["file_path"]).name
    approved_time = request["approved_at"] or request["created_at"]
    generation_time = _generation_time(request["sandbox_id"], request["created_at"])

    timeline = [
        {
            "key": "ingest",
            "title": "数据入湖",
            "detail": f"{filename} 已完成资产登记与分级",
            "time": _ingest_time(filename, sandbox.get("created_at") or request["created_at"]),
            "status": "done",
        },
        {
            "key": "sandbox",
            "title": "创建可信沙箱",
            "detail": f"沙箱 {request['sandbox_id']} 挂载只读原始卷与读写工作卷",
            "time": sandbox.get("created_at") or request["created_at"],
            "status": "done",
        },
        {
            "key": "generate",
            "title": "生成工作文件",
            "detail": f"在沙箱内生成 {filename}",
            "time": generation_time,
            "status": "done",
        },
        {
            "key": "request",
            "title": "提交出域申请",
            "detail": f"{request['applicant']} → {request['receiver']}，分级 {request['data_level']}",
            "time": request["created_at"],
            "status": "done",
        },
        {
            "key": "approval",
            "title": "快速通道放行" if request["channel"] == "fast" else "审批通过",
            "detail": mapping["approval_chain"],
            "time": approved_time,
            "status": "done",
        },
        {
            "key": "watermark",
            "title": "嵌入哈希槽水印",
            "detail": f"水印帧已写入 {filename} 的 amount LSB",
            "time": mapping["export_time"],
            "status": "done",
        },
        {
            "key": "export",
            "title": "文件出域",
            "detail": f"导出给 {request['receiver']}，水印 ID {watermark_id}",
            "time": mapping["export_time"],
            "status": "done",
        },
    ]

    return {
        "watermark_id": watermark_id,
        "match_score": round(match_score, 2),
        "mapping": {
            "watermark_id": mapping["watermark_id"],
            "export_user": mapping["user_id"],
            "export_time": mapping["export_time"],
            "file_id": mapping["file_id"],
            "file_hash": mapping["file_hash"],
            "data_scope": mapping["data_scope"],
            "sandbox_id": mapping["sandbox_id"],
            "approval_chain": mapping["approval_chain"],
        },
        "request": request,
        "timeline": timeline,
        "filename": f"{watermark_id}_{filename}",
        "download_url": f"/api/watermark/{watermark_id}/download",
    }


def list_exported_files() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute(
            """
            SELECT m.*, r.file_path, r.receiver, r.data_level, r.purpose
            FROM watermark_mappings m
            JOIN export_requests r ON r.id=m.request_id
            ORDER BY m.export_time DESC
            """
        ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        filename = Path(item["file_path"]).name
        item["filename"] = f"{item['watermark_id']}_{filename}"
        item["status"] = "已导出"
        item["download_url"] = f"/api/watermark/{item['watermark_id']}/download"
        items.append(item)
    return items

