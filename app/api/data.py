from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, Field

from app.common import client_ip, ok
from app.db import connection, insert_audit
from app.sandbox.manager import generate_dataset, get_sandbox_manager


router = APIRouter(prefix="/api/data", tags=["data"])


class GenerateBody(BaseModel):
    table_name: str = "customer"
    rows: int = Field(default=5000, ge=1, le=50_000)
    sandbox_id: str = "sbx-demo"


@router.get("/assets")
def list_assets(
    q: str = Query(default=""),
    level: str = Query(default=""),
):
    sql = "SELECT * FROM data_assets WHERE 1=1"
    params: list[str] = []
    if q:
        sql += " AND (table_name LIKE ? OR owner LIKE ?)"
        params.extend([f"%{q}%", f"%{q}%"])
    if level and level != "all":
        sql += " AND data_level=?"
        params.append(level)
    sql += " ORDER BY id"
    with connection() as conn:
        rows = conn.execute(sql, params).fetchall()
    items = []
    for row in rows:
        item = dict(row)
        item["is_secret"] = bool(item["is_secret"])
        items.append(item)
    return ok({"items": items, "total": len(items)})


@router.post("/generate")
def generate(body: GenerateBody, request: Request):
    sandbox = get_sandbox_manager().get(body.sandbox_id)
    safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", body.table_name or "dataset")
    path = f"/mnt/work/{safe_name}.csv"
    content = generate_dataset(safe_name, body.rows)
    write_result = sandbox.write_file(path, content, actor="data-generator", ip=client_ip(request))
    insert_audit(
        "data.generate",
        "data-generator",
        path,
        "success",
        f"生成 {body.rows} 行演示数据",
        client_ip(request),
    )
    return ok(
        {
            "sandbox_id": body.sandbox_id,
            "path": path,
            "name": Path(path).name,
            "rows": body.rows,
            "size": len(content),
            "preview": get_sandbox_manager().preview(body.sandbox_id, path, 5),
            "trace_id": write_result.get("trace_id"),
        }
    )

