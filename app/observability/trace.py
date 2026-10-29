"""持久化的业务执行追踪。

与 HTTP 访问日志不同，这里记录一次业务操作内部的阶段、参数摘要和耗时。
沙箱初始化、代码执行、审批状态迁移以及水印算法的每一步都会写入
execution_traces / execution_steps，可在服务重启后继续查询。
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime
import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any, Iterator

from app.common import BizError, now_iso
from app.db import connection
from app.logging_config import trace_logger


HTTP_REQUEST_ID: ContextVar[str | None] = ContextVar("http_request_id", default=None)
MAX_JSON_CHARS = 40_000


def bind_http_request(request_id: str):
    """绑定当前 HTTP 请求，使业务 trace 能和访问日志互相定位。"""
    return HTTP_REQUEST_ID.set(request_id)


def reset_http_request(token) -> None:
    HTTP_REQUEST_ID.reset(token)


def _hash_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def jsonable(value: Any) -> Any:
    """把业务值转换为可审计的 JSON，不把大块原始文件内容写入数据库。"""
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {"type": "bytes", "size": len(value), "sha256": _hash_bytes(value)}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return str(value)


def _json_text(value: Any) -> str:
    text = json.dumps(jsonable(value), ensure_ascii=False, separators=(",", ":"), default=str)
    if len(text) <= MAX_JSON_CHARS:
        return text
    preview = text[: MAX_JSON_CHARS // 2]
    return json.dumps(
        {"truncated": True, "original_chars": len(text), "preview": preview},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _load_json(text: str | None, fallback: Any) -> Any:
    if not text:
        return fallback
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        return fallback


class StepScope:
    def __init__(self, recorder: "TraceRecorder", phase: str, detail: dict[str, Any]) -> None:
        self.recorder = recorder
        self.phase = phase
        self.detail = detail
        self.started = time.perf_counter()
        self.started_at = now_iso()

    def __setitem__(self, key: str, value: Any) -> None:
        self.detail[key] = value


class TraceRecorder:
    """一次业务操作的追踪器，支持上下文管理与失败落库。"""

    def __init__(
        self,
        operation: str,
        *,
        actor: str = "system",
        target: str = "",
        input: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> None:
        self.operation = operation
        self.actor = actor or "anonymous"
        self.target = target or "-"
        self.trace_id = trace_id or f"TRC-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:10]}"
        self.input = dict(input or {})
        http_request_id = HTTP_REQUEST_ID.get()
        if http_request_id:
            self.input.setdefault("http_request_id", http_request_id)
        self.result: Any = None
        self.status = "running"
        self.error: dict[str, Any] | None = None
        self.started_at = now_iso()
        self._started = time.perf_counter()
        self._last_mark = self._started
        self._seq = 0
        self._finished = False
        self._db_available = True

    def __enter__(self) -> "TraceRecorder":
        try:
            with connection() as conn:
                conn.execute(
                    """
                    INSERT INTO execution_traces
                    (trace_id, operation, status, actor, target, input_json, started_at)
                    VALUES (?, ?, 'running', ?, ?, ?, ?)
                    """,
                    (
                        self.trace_id,
                        self.operation,
                        self.actor,
                        self.target,
                        _json_text(self.input),
                        self.started_at,
                    ),
                )
        except Exception:
            self._db_available = False
            trace_logger.exception("trace.persist_failed trace_id=%s operation=%s", self.trace_id, self.operation)
        trace_logger.info(
            "trace.start trace_id=%s operation=%s actor=%s target=%s http_request_id=%s",
            self.trace_id,
            self.operation,
            self.actor,
            self.target,
            self.input.get("http_request_id", "-"),
        )
        return self

    def set_result(self, result: Any) -> "TraceRecorder":
        self.result = result
        return self

    @contextmanager
    def measure(self, phase: str, **initial_detail: Any) -> Iterator[StepScope]:
        scope = StepScope(self, phase, dict(initial_detail))
        try:
            yield scope
        except Exception as exc:
            self._write_step(
                scope.phase,
                "failed",
                scope.detail,
                scope.started_at,
                round((time.perf_counter() - scope.started) * 1000, 3),
                error={"type": type(exc).__name__, "message": str(exc)},
            )
            raise
        else:
            self._write_step(
                scope.phase,
                "success",
                scope.detail,
                scope.started_at,
                round((time.perf_counter() - scope.started) * 1000, 3),
            )

    def step(
        self,
        phase: str,
        *,
        status: str = "success",
        detail: dict[str, Any] | None = None,
        duration_ms: float | None = None,
    ) -> "TraceRecorder":
        if duration_ms is None:
            duration_ms = round((time.perf_counter() - self._last_mark) * 1000, 3)
        self._write_step(phase, status, detail or {}, now_iso(), duration_ms)
        return self

    def _write_step(
        self,
        phase: str,
        status: str,
        detail: dict[str, Any],
        started_at: str,
        duration_ms: float,
        *,
        error: dict[str, Any] | None = None,
    ) -> None:
        self._seq += 1
        if error:
            detail = {**detail, "error": error}
        self._last_mark = time.perf_counter()
        trace_logger.info(
            "trace.step trace_id=%s seq=%02d phase=%s status=%s duration_ms=%.3f detail=%s",
            self.trace_id,
            self._seq,
            phase,
            status,
            duration_ms,
            _json_text(detail),
        )
        if not self._db_available:
            return
        try:
            with connection() as conn:
                conn.execute(
                    """
                    INSERT INTO execution_steps
                    (trace_id, seq, phase, status, started_at, finished_at, duration_ms, detail_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        self.trace_id,
                        self._seq,
                        phase,
                        status,
                        started_at,
                        now_iso(),
                        round(duration_ms, 3),
                        _json_text(detail),
                    ),
                )
        except Exception:
            self._db_available = False
            trace_logger.exception("trace.step_persist_failed trace_id=%s phase=%s", self.trace_id, phase)

    def finish(self, status: str | None = None, error: dict[str, Any] | None = None) -> None:
        if self._finished:
            return
        self._finished = True
        self.status = status or ("success" if error is None else "failed")
        if error is not None:
            self.error = error
        duration_ms = round((time.perf_counter() - self._started) * 1000, 3)
        trace_logger.info(
            "trace.finish trace_id=%s operation=%s status=%s duration_ms=%.3f error=%s",
            self.trace_id,
            self.operation,
            self.status,
            duration_ms,
            self.error.get("message", "-") if self.error else "-",
        )
        if not self._db_available:
            return
        try:
            with connection() as conn:
                conn.execute(
                    """
                    UPDATE execution_traces
                    SET status=?, result_json=?, error=?, finished_at=?, duration_ms=?
                    WHERE trace_id=?
                    """,
                    (
                        self.status,
                        _json_text(self.result) if self.result is not None else None,
                        _json_text(self.error) if self.error is not None else None,
                        now_iso(),
                        duration_ms,
                        self.trace_id,
                    ),
                )
        except Exception:
            trace_logger.exception("trace.finish_persist_failed trace_id=%s", self.trace_id)

    def __exit__(self, exc_type, exc, traceback) -> bool:
        if exc is None:
            self.finish("success")
            return False
        if exc is not None:
            setattr(exc, "trace_id", self.trace_id)
        if isinstance(exc, BizError):
            status = "blocked" if exc.code in {1002, 1005} or exc.status_code == 403 else "failed"
            error = {"type": type(exc).__name__, "code": exc.code, "status_code": exc.status_code, "message": exc.message}
        else:
            status = "failed"
            error = {"type": type(exc).__name__, "message": str(exc)}
        self.finish(status, error)
        return False


def _trace_summary(row) -> dict[str, Any]:
    item = dict(row)
    item["input"] = _load_json(item.pop("input_json", "{}"), {})
    item["result"] = _load_json(item.pop("result_json", None), None)
    item["error"] = _load_json(item.get("error"), None)
    return item


def list_traces(
    *,
    operation: str | None = None,
    status: str | None = None,
    target: str | None = None,
    phase: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    sql = """
        SELECT t.*, COUNT(s.id) AS step_count
        FROM execution_traces t
        LEFT JOIN execution_steps s ON s.trace_id=t.trace_id
        WHERE 1=1
    """
    params: list[Any] = []
    if operation:
        sql += " AND t.operation=?"
        params.append(operation)
    if status:
        sql += " AND t.status=?"
        params.append(status)
    if target:
        sql += " AND t.target=?"
        params.append(target)
    if phase:
        sql += " AND EXISTS (SELECT 1 FROM execution_steps p WHERE p.trace_id=t.trace_id AND p.phase LIKE ?)"
        params.append(f"%{phase}%")
    sql += " GROUP BY t.trace_id ORDER BY t.started_at DESC, t.rowid DESC LIMIT ?"
    params.append(max(1, min(limit, 500)))
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_trace_summary(row) for row in rows]


def get_trace(trace_id: str) -> dict[str, Any] | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM execution_traces WHERE trace_id=?", (trace_id,)).fetchone()
        if row is None:
            return None
        steps = conn.execute(
            "SELECT * FROM execution_steps WHERE trace_id=? ORDER BY seq",
            (trace_id,),
        ).fetchall()
    trace = _trace_summary(row)
    trace["steps"] = [
        {
            **{key: value for key, value in dict(step).items() if key not in {"detail_json"}},
            "detail": _load_json(step["detail_json"], {}),
        }
        for step in steps
    ]
    trace["step_count"] = len(trace["steps"])
    return trace


def latest_trace_id(operation: str | None = None) -> str | None:
    traces = list_traces(operation=operation, limit=1)
    return traces[0]["trace_id"] if traces else None
