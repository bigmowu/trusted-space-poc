"""组件注册表与运行时自检。

每个组件返回稳定 JSON 契约：实现方、状态、能力、配置、依赖、探测项、
修复建议和自检入口。探测结果直接来自当前进程与磁盘，不读取演示文案。
"""

from __future__ import annotations

import hashlib
import importlib.util
import os
import shutil
import time
from pathlib import Path
from typing import Any

from app.common import biz_error, now_iso
from app.config import (
    DB_PATH,
    EXPORTS_DIR,
    OPENSANDBOX_ADAPTER,
    SANDBOXES_DIR,
    SANDBOX_EXEC_TIMEOUT,
)
from app.db import connection
from app.logging_config import log_event
from app.observability.trace import TraceRecorder, get_trace, latest_trace_id
from app.sandbox.manager import get_sandbox_manager
from app.sandbox.runtimes import RUNTIME_MANIFESTS, backend_health, opensandbox_health
from app.watermark.engine import get_watermark_engine


COMPONENT_IDS = (
    "sandbox-runtime",
    "watermark-engine",
    "export-gate",
    "trace-store",
    "metadata-db",
    "artifact-store",
)


def _checkbox(name: str, ok: bool, detail: Any) -> dict[str, Any]:
    return {"name": name, "ok": ok, "detail": detail}


def _db_health() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    try:
        with connection() as conn:
            integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {
                row[0]
                for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            }
        checks.append(_checkbox("sqlite.integrity", integrity == "ok", integrity))
        required = {"sandboxes", "export_requests", "watermark_mappings", "audit_logs", "execution_traces"}
        checks.append(_checkbox("sqlite.schema", required <= tables, sorted(required & tables)))
        status = "ready" if all(item["ok"] for item in checks) else "unavailable"
        message = "SQLite 元数据存储可用" if status == "ready" else "SQLite 元数据校验失败"
    except Exception as exc:
        checks.append(_checkbox("sqlite.open", False, str(exc)))
        status, message = "unavailable", f"SQLite 不可用: {exc}"
    return {
        "id": "metadata-db",
        "name": "元数据数据库",
        "category": "storage",
        "version": "sqlite-3",
        "provider": "SQLite WAL-less local file",
        "description": "保存沙箱、出域申请、水印映射、审计与执行追踪；所有业务状态以这里为准。",
        "status": status,
        "message": message,
        "capabilities": {
            "transactional": True,
            "persistent": True,
            "schema_migration": True,
            "tables": ["sandboxes", "export_requests", "watermark_mappings", "audit_logs", "execution_traces", "execution_steps"],
        },
        "config": {"path": str(DB_PATH), "journal_mode": "default"},
        "dependencies": [],
        "checks": checks,
        "remediation": None if status == "ready" else "检查 TRUSTED_SPACE_DB 路径与目录写权限后重启服务。",
        "self_test": {"available": False},
    }


def _trace_store_health() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    counts = {"traces": 0, "steps": 0}
    latest = None
    try:
        with connection() as conn:
            counts["traces"] = conn.execute("SELECT COUNT(*) FROM execution_traces").fetchone()[0]
            counts["steps"] = conn.execute("SELECT COUNT(*) FROM execution_steps").fetchone()[0]
            row = conn.execute(
                "SELECT trace_id, operation, status, started_at FROM execution_traces ORDER BY started_at DESC LIMIT 1"
            ).fetchone()
            latest = dict(row) if row else None
        checks.append(_checkbox("trace.tables.readable", True, counts))
        checks.append(
            _checkbox(
                "trace.latest",
                True,
                latest or "尚无业务追踪；首次启动或执行后会写入",
            )
        )
        status = "ready"
        message = "执行追踪持久化可用"
    except Exception as exc:
        checks.append(_checkbox("trace.tables.readable", False, str(exc)))
        status, message = "unavailable", f"执行追踪不可用: {exc}"
    return {
        "id": "trace-store",
        "name": "执行追踪存储",
        "category": "observability",
        "version": "trace-v1",
        "provider": "TraceRecorder",
        "description": "记录一次操作的每个后端阶段、参数、耗时与失败原因，支持 HTTP request_id 关联。",
        "status": status,
        "message": message,
        "capabilities": {
            "persistent_steps": True,
            "http_request_correlation": True,
            "exception_capture": True,
            "follow_cli": True,
            "storage_counts": counts,
        },
        "config": {"db_path": str(DB_PATH), "max_json_chars": 40000},
        "dependencies": ["metadata-db"],
        "checks": checks,
        "remediation": None if status == "ready" else "恢复元数据库后重启；检查 execution_traces / execution_steps 表。",
        "self_test": {"available": False},
    }


def _artifact_store_health() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    counts = {"sandbox_dirs": 0, "export_files": 0, "export_bytes": 0}
    try:
        SANDBOXES_DIR.mkdir(parents=True, exist_ok=True)
        EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
        counts["sandbox_dirs"] = sum(path.is_dir() for path in SANDBOXES_DIR.iterdir())
        export_files = [path for path in EXPORTS_DIR.iterdir() if path.is_file()]
        counts["export_files"] = len(export_files)
        counts["export_bytes"] = sum(path.stat().st_size for path in export_files)
        checks.append(_checkbox("storage.sandbox_root.writable", os.access(SANDBOXES_DIR, os.W_OK), str(SANDBOXES_DIR)))
        checks.append(_checkbox("storage.exports.writable", os.access(EXPORTS_DIR, os.W_OK), str(EXPORTS_DIR)))
        usage = shutil.disk_usage(SANDBOXES_DIR)
        checks.append(_checkbox("storage.disk_space", usage.free > 64 * 1024 * 1024, {"free_bytes": usage.free}))
        status = "ready" if all(item["ok"] for item in checks) else "unavailable"
        message = "沙箱卷与导出文件存储可用" if status == "ready" else "文件存储不可写或空间不足"
    except Exception as exc:
        checks.append(_checkbox("storage.root", False, str(exc)))
        status, message = "unavailable", f"文件存储不可用: {exc}"
    return {
        "id": "artifact-store",
        "name": "文件与卷存储",
        "category": "storage",
        "version": "filesystem-v1",
        "provider": "Local filesystem",
        "description": "持久化 sandbox raw/work 卷与带水印导出物；Docker 通过 bind mount 使用同一目录。",
        "status": status,
        "message": message,
        "capabilities": {
            "raw_volume_readonly": True,
            "work_volume_readwrite": True,
            "restart_recovery": True,
            "content_addressed_trace": "SHA256",
            "counts": counts,
        },
        "config": {"sandbox_root": str(SANDBOXES_DIR), "exports_dir": str(EXPORTS_DIR)},
        "dependencies": [],
        "checks": checks,
        "remediation": None if status == "ready" else "修复目录权限或设置 TRUSTED_SPACE_SANDBOXES / TRUSTED_SPACE_EXPORTS。",
        "self_test": {"available": False},
    }


def _sandbox_component() -> dict[str, Any]:
    manager = get_sandbox_manager()
    active = backend_health(manager.backend)
    variants: list[dict[str, Any]] = []
    for backend in ("mock", "docker", "opensandbox"):
        try:
            item = backend_health(backend)
        except Exception as exc:
            item = {
                "backend": backend,
                "status": "unavailable",
                "message": str(exc),
                "checks": [_checkbox(f"{backend}.probe", False, str(exc))],
                "remediation": "检查运行时依赖。",
            }
        item["active"] = backend == manager.backend
        item["manifest"] = RUNTIME_MANIFESTS[backend]
        variants.append(item)
    status = active.get("status", "unavailable")
    checks = list(active.get("checks", []))
    checks.append(
        _checkbox(
            "sandbox.active_backend",
            manager.backend in RUNTIME_MANIFESTS,
            {"selected": manager.backend, "implementation": active.get("implementation")},
        )
    )
    return {
        "id": "sandbox-runtime",
        "name": "沙箱运行时",
        "category": "runtime",
        "version": "runtime-v2",
        "provider": active.get("implementation", RUNTIME_MANIFESTS.get(manager.backend, {}).get("name", manager.backend)),
        "description": "统一 create/write/exec/read/destroy 接口；通过 SANDBOX_BACKEND 在 Mock、Docker、OpenSandbox 间替换。",
        "status": status,
        "message": active.get("message", "等待运行时探测"),
        "active_backend": manager.backend,
        "capabilities": active.get("capabilities", {}),
        "config": {
            "active_backend": manager.backend,
            "exec_timeout_seconds": SANDBOX_EXEC_TIMEOUT,
            "storage_root": str(SANDBOXES_DIR),
            **active.get("config", {}),
        },
        "dependencies": ["metadata-db", "trace-store", "artifact-store"],
        "checks": checks,
        "remediation": active.get("remediation"),
        "self_test": {
            "available": True,
            "operation": "component.sandbox.selftest",
            "command": "python -m app.cli component sandbox-runtime --selftest",
        },
        "runtime_variants": variants,
    }


def _watermark_component() -> dict[str, Any]:
    engine = get_watermark_engine()
    health = engine.health()
    return {
        "id": engine.component_id,
        "name": "哈希槽水印引擎",
        "category": "security",
        "version": engine.version,
        "provider": engine.provider,
        "description": "将导出人、时间、文件与申请号编码进 amount LSB；支持乱序、删行和 CRC 修复。",
        "status": health["status"],
        "message": health["message"],
        "capabilities": health["capabilities"],
        "config": health["config"],
        "dependencies": health["dependencies"],
        "checks": health["checks"],
        "remediation": None if health["status"] == "ready" else "检查 watermark/hash_slot.py 的帧构造与 CSV 数据约束。",
        "self_test": {
            "available": True,
            "operation": "component.watermark.selftest",
            "command": "python -m app.cli component watermark-engine --selftest",
        },
        "algorithm": health["algorithm"],
    }


def _export_gate_component() -> dict[str, Any]:
    watermark = get_watermark_engine().health()
    checks = [
        _checkbox("export.watermark.required", watermark["status"] == "ready", watermark["status"]),
        _checkbox("export.storage.writable", os.access(EXPORTS_DIR, os.W_OK), str(EXPORTS_DIR)),
    ]
    try:
        with connection() as conn:
            counts = {
                "requests": conn.execute("SELECT COUNT(*) FROM export_requests").fetchone()[0],
                "exports": conn.execute("SELECT COUNT(*) FROM watermark_mappings").fetchone()[0],
            }
        checks.append(_checkbox("export.db.readable", True, counts))
    except Exception as exc:
        counts = {"requests": 0, "exports": 0}
        checks.append(_checkbox("export.db.readable", False, str(exc)))
    status = "ready" if all(item["ok"] for item in checks) else "unavailable"
    return {
        "id": "export-gate",
        "name": "强制水印出域闸门",
        "category": "security",
        "version": "gate-v1",
        "provider": "ForcedExportGate",
        "description": "校验文件位于工作卷、按 L1-L4 分级放行，并在落盘前强制调用水印引擎。",
        "status": status,
        "message": "出域闸门可用" if status == "ready" else "出域闸门依赖未满足",
        "capabilities": {
            "raw_volume_export_denied": True,
            "classification_channels": {"L1/L2": "fast", "L3/L4": "Owner→Security→Compliance"},
            "watermark_mandatory": True,
            "unapproved_export_denied": True,
            "counts": counts,
        },
        "config": {"exports_dir": str(EXPORTS_DIR), "watermark_engine": watermark.get("version")},
        "dependencies": ["watermark-engine", "metadata-db", "sandbox-runtime", "trace-store"],
        "checks": checks,
        "remediation": None if status == "ready" else "先修复 watermark-engine 与导出目录写权限。",
        "self_test": {"available": False},
    }


BUILDERS = {
    "sandbox-runtime": _sandbox_component,
    "watermark-engine": _watermark_component,
    "export-gate": _export_gate_component,
    "trace-store": _trace_store_health,
    "metadata-db": _db_health,
    "artifact-store": _artifact_store_health,
}


def _status_summary(items: list[dict[str, Any]]) -> dict[str, int]:
    summary = {"ready": 0, "degraded": 0, "unavailable": 0, "total": len(items)}
    for item in items:
        status = item.get("status", "unavailable")
        summary[status] = summary.get(status, 0) + 1
    return summary


def list_components() -> dict[str, Any]:
    items = [BUILDERS[component_id]() for component_id in COMPONENT_IDS]
    return {
        "items": items,
        "summary": _status_summary(items),
        "active_sandbox_backend": get_sandbox_manager().backend,
        "probed_at": now_iso(),
    }


def component_detail(component_id: str) -> dict[str, Any]:
    builder = BUILDERS.get(component_id)
    if builder is None:
        raise biz_error(1003, f"组件不存在: {component_id}")
    item = builder()
    operation = item.get("self_test", {}).get("operation")
    if operation:
        trace_id = latest_trace_id(operation)
        trace = get_trace(trace_id) if trace_id else None
        item["last_self_test"] = (
            {
                "trace_id": trace["trace_id"],
                "status": trace["status"],
                "started_at": trace["started_at"],
                "duration_ms": trace["duration_ms"],
            }
            if trace
            else None
        )
    return item


def _sandbox_self_test(actor: str) -> dict[str, Any]:
    manager = get_sandbox_manager()
    health = backend_health(manager.backend)
    with TraceRecorder(
        "component.sandbox.selftest",
        actor=actor,
        target=f"backend:{manager.backend}",
        input={"backend": manager.backend, "implementation": health.get("implementation")},
    ) as trace:
        trace.step(
            "component.sandbox.health",
            status="success" if health.get("status") == "ready" else "failed",
            detail=health,
        )
        if health.get("status") != "ready":
            raise biz_error(1007, f"沙箱后端未就绪: {health.get('message')}；{health.get('remediation') or ''}")

        sandbox_id: str | None = None
        nested: dict[str, str | None] = {}
        result: dict[str, Any] | None = None
        try:
            created = manager.create("component-selftest", actor=actor)
            sandbox_id = created["id"]
            nested["create_trace_id"] = created.get("trace_id")
            trace.step(
                "component.sandbox.create",
                detail={"sandbox_id": sandbox_id, "backend": created.get("backend"), "trace_id": created.get("trace_id")},
            )
            marker = f"component-check:{manager.backend}:{int(time.time() * 1000)}"
            sandbox = manager.get(sandbox_id)
            written = sandbox.write_file("/mnt/work/component_check.txt", (marker + "\n").encode("utf-8"), actor=actor)
            nested["write_trace_id"] = written.get("trace_id")
            trace.step("component.sandbox.write", detail={"path": written["path"], "trace_id": written.get("trace_id")})

            code = 'print(open("/mnt/work/component_check.txt", encoding="utf-8").read().strip())'
            executed = sandbox.exec(code, actor=actor)
            nested["exec_trace_id"] = executed.get("trace_id")
            trace.step(
                "component.sandbox.exec",
                status="success" if executed.get("exit_code") == 0 else "failed",
                detail={
                    "exit_code": executed.get("exit_code"),
                    "stdout": executed.get("stdout", "").strip(),
                    "stderr": executed.get("stderr", "").strip(),
                    "trace_id": executed.get("trace_id"),
                },
            )
            stdout = executed.get("stdout", "").strip()
            if executed.get("exit_code") != 0 or marker not in stdout:
                raise RuntimeError(
                    f"沙箱执行结果不符合预期: exit={executed.get('exit_code')} stdout={stdout!r}"
                )
            result = {
                "status": "success",
                "component": "sandbox-runtime",
                "backend": manager.backend,
                "sandbox_id": sandbox_id,
                "marker": marker,
                "stdout": stdout,
                **nested,
            }
            trace.step("component.sandbox.assert", detail={"marker_roundtrip": True, "working_volume": "/mnt/work"})
        finally:
            if sandbox_id and sandbox_id in manager._sandboxes:
                try:
                    destroyed = manager.destroy(sandbox_id, actor=actor)
                    nested["destroy_trace_id"] = destroyed.get("trace_id")
                    trace.step(
                        "component.sandbox.destroy",
                        detail={"sandbox_id": sandbox_id, "trace_id": destroyed.get("trace_id")},
                    )
                except Exception as exc:
                    trace.step("component.sandbox.destroy", status="failed", detail={"sandbox_id": sandbox_id, "error": str(exc)})
        if result is None:
            raise RuntimeError("沙箱组件自检未产生结果")
        result.update(nested)
        result["trace_id"] = trace.trace_id
        trace.set_result(result)
        return result


def _watermark_self_test(actor: str) -> dict[str, Any]:
    engine = get_watermark_engine()
    with TraceRecorder(
        "component.watermark.selftest",
        actor=actor,
        target="watermark-engine",
        input={"rows": 6000, "algorithm": engine.version},
    ) as trace:
        trace.step("component.watermark.lookup", detail=engine.describe())

        def observer(phase: str, detail: dict[str, Any], status: str) -> None:
            trace.step(phase, status=status, detail=detail)

        result = engine.self_test(observer=observer)
        result["trace_id"] = trace.trace_id
        trace.step(
            "component.watermark.assert",
            detail={
                "watermark_id_roundtrip": result["assertions"]["watermark_id_roundtrip"],
                "match_score": result["match_score"],
                "changed_rows": result["changed_rows"],
            },
        )
        trace.set_result(result)
        return result


def run_self_test(component_id: str, *, actor: str = "component-registry") -> dict[str, Any]:
    if component_id not in BUILDERS:
        raise biz_error(1003, f"组件不存在: {component_id}")
    log_event("component.selftest.started", component=component_id, actor=actor)
    try:
        if component_id == "sandbox-runtime":
            result = _sandbox_self_test(actor)
        elif component_id == "watermark-engine":
            result = _watermark_self_test(actor)
        else:
            raise biz_error(1007, f"组件 {component_id} 未提供运行时自检")
    except Exception as exc:
        log_event("component.selftest.failed", component=component_id, actor=actor, error=str(exc))
        raise
    log_event(
        "component.selftest.completed",
        component=component_id,
        actor=actor,
        status=result.get("status", "success"),
        trace_id=result.get("trace_id", "-"),
    )
    return result
