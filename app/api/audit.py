from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Query

from app.common import ok
from app.db import connection


router = APIRouter(prefix="/api/audit", tags=["audit"])


def _normalize_date(value: str, *, end: bool = False) -> str:
    if not value:
        return value
    if "T" not in value:
        value = f"{value}T{'23:59:59' if end else '00:00:00'}"
    return value


@router.get("/logs")
def audit_logs(
    action: str = Query(default=""),
    result: str = Query(default=""),
    start: str = Query(default=""),
    end: str = Query(default=""),
    target: str = Query(default=""),
    limit: int = Query(default=200, ge=1, le=1000),
):
    sql = "SELECT * FROM audit_logs WHERE 1=1"
    params: list[str | int] = []
    if action and action != "all":
        sql += " AND action=?"
        params.append(action)
    if result and result != "all":
        sql += " AND result=?"
        params.append(result)
    if start:
        sql += " AND created_at>=?"
        params.append(_normalize_date(start))
    if end:
        sql += " AND created_at<=?"
        params.append(_normalize_date(end, end=True))
    if target:
        sql += " AND target=?"
        params.append(target)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
        raw_write = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action='raw_write_blocked'"
        ).fetchone()[0]
        unauthorized = conn.execute(
            "SELECT COUNT(*) FROM audit_logs WHERE action='unauthorized_export'"
        ).fetchone()[0]
        timeout_cutoff = (datetime.now().astimezone() - timedelta(hours=24)).isoformat(timespec="seconds")
        approval_timeout = conn.execute(
            "SELECT COUNT(*) FROM export_requests WHERE status='pending' AND created_at<?",
            (timeout_cutoff,),
        ).fetchone()[0]
    return ok(
        {
            "items": [dict(row) for row in rows],
            "total": len(rows),
            "stats": {
                "raw_write_attempts": raw_write,
                "unauthorized_exports": unauthorized,
                "approval_timeouts": approval_timeout,
            },
        }
    )

