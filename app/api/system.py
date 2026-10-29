from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Query

from app.common import ok
from app.logging_config import LOG_FILES, LOG_LABELS


router = APIRouter(prefix="/api/system", tags=["system"])


@router.get("/logs")
def backend_logs(
    limit: int = Query(default=100, ge=1, le=500),
    source: Literal["events", "trace", "http"] = Query(default="events"),
):
    path = LOG_FILES[source]
    meta = {
        "source": source,
        "label": LOG_LABELS[source],
        "sources": [
            {"id": item, "label": LOG_LABELS[item], "path": str(LOG_FILES[item])}
            for item in LOG_FILES
        ],
    }
    if not path.exists():
        return ok({**meta, "path": str(path), "items": [], "updated_at": None})
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        lines = deque(handle, maxlen=limit)
    return ok(
        {
            **meta,
            "path": str(path),
            "items": [line.rstrip("\n") for line in lines],
            "updated_at": datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(timespec="seconds"),
        }
    )

