from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from app.common import ok
from app.config import FIELD_POLICY_COUNT, READONLY_VOLUME_COUNT
from app.db import connection


router = APIRouter(prefix="/api/overview", tags=["overview"])


@router.get("")
def overview():
    with connection() as conn:
        running = conn.execute("SELECT COUNT(*) FROM sandboxes WHERE status='RUNNING'").fetchone()[0]
        watermarks = conn.execute("SELECT COUNT(*) FROM watermark_mappings").fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM export_requests WHERE status='pending' AND channel='approval'"
        ).fetchone()[0]
        recent_rows = conn.execute(
            "SELECT * FROM export_requests ORDER BY created_at DESC LIMIT 6"
        ).fetchall()
        blocked = conn.execute(
            "SELECT * FROM audit_logs WHERE result='blocked' ORDER BY id DESC LIMIT 3"
        ).fetchall()
        embedded = conn.execute(
            "SELECT * FROM audit_logs WHERE action='watermark.embed' AND result='success' ORDER BY id DESC LIMIT 2"
        ).fetchall()

    recent = []
    for row in recent_rows:
        item = dict(row)
        item["filename"] = Path(item["file_path"]).name
        recent.append(item)

    alerts: list[dict[str, str]] = []
    if pending:
        alerts.append(
            {
                "tone": "warning",
                "title": "待审批出域申请",
                "detail": f"{pending} 个 L3/L4 申请等待安全审批",
                "time": "",
            }
        )
    for row in embedded:
        alerts.append(
            {
                "tone": "success",
                "title": "水印嵌入成功",
                "detail": f"{row['target']} 已完成出口水印",
                "time": row["created_at"],
            }
        )
    for row in blocked:
        alerts.append(
            {
                "tone": "danger",
                "title": "写原始卷被阻断" if row["action"] == "raw_write_blocked" else "未授权导出被阻断",
                "detail": row["detail"],
                "time": row["created_at"],
            }
        )
    if not alerts:
        alerts.append(
            {
                "tone": "info",
                "title": "安全基线正常",
                "detail": "暂无待处理告警，所有出口策略持续生效",
                "time": "",
            }
        )

    return ok(
        {
            "metrics": {
                "readonly_volumes": READONLY_VOLUME_COUNT,
                "running_sandboxes": running,
                "watermark_files": watermarks,
                "field_policies": FIELD_POLICY_COUNT,
            },
            "recent_exports": recent,
            "alerts": alerts[:6],
            "risk_bars": [
                {"name": "域失控", "before": 30, "after": 10, "label": "域可控", "score": 90},
                {"name": "境失控", "before": 20, "after": 15, "label": "境可信", "score": 85},
                {"name": "源失控", "before": 15, "after": 5, "label": "源可溯", "score": 95},
                {"name": "权失控", "before": 40, "after": 20, "label": "权可分", "score": 80},
            ],
        }
    )

