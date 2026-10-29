from __future__ import annotations

from fastapi import APIRouter, Query
from fastapi.responses import PlainTextResponse

from app.common import biz_error, ok
from app.observability.format import format_trace
from app.observability.trace import get_trace, list_traces, latest_trace_id


router = APIRouter(prefix="/api/traces", tags=["execution-traces"])


@router.get("")
def traces(
    operation: str = Query(default=""),
    status: str = Query(default=""),
    target: str = Query(default=""),
    phase: str = Query(default=""),
    limit: int = Query(default=50, ge=1, le=500),
):
    items = list_traces(
        operation=operation or None,
        status=status or None,
        target=target or None,
        phase=phase or None,
        limit=limit,
    )
    return ok({"items": items, "total": len(items)})


@router.get("/latest")
def latest(operation: str = Query(default="")):
    trace_id = latest_trace_id(operation or None)
    if not trace_id:
        raise biz_error(1003, "尚无执行追踪记录")
    trace = get_trace(trace_id)
    return ok(trace)


@router.get("/{trace_id}/text", response_class=PlainTextResponse)
def trace_text(trace_id: str):
    trace = get_trace(trace_id)
    if trace is None:
        raise biz_error(1003, "执行追踪不存在")
    return PlainTextResponse(format_trace(trace), media_type="text/plain; charset=utf-8")


@router.get("/{trace_id}")
def trace_detail(trace_id: str):
    trace = get_trace(trace_id)
    if trace is None:
        raise biz_error(1003, "执行追踪不存在")
    return ok(trace)
