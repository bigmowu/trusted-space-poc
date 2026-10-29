from __future__ import annotations

import mimetypes
from pathlib import Path

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from app.common import biz_error, client_ip, ok
from app.sandbox.manager import DEFAULT_CODE, get_sandbox_manager


router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])


class CreateSandboxBody(BaseModel):
    task_id: str = ""


class ExecBody(BaseModel):
    code: str = Field(min_length=1)


class WriteFileBody(BaseModel):
    path: str
    content: str


@router.get("")
def list_sandboxes():
    return ok({"items": get_sandbox_manager().list(), "default_code": DEFAULT_CODE})


@router.post("")
def create_sandbox(body: CreateSandboxBody, request: Request):
    sandbox = get_sandbox_manager().create(body.task_id, ip=client_ip(request))
    return ok(sandbox)


@router.get("/status")
def sandbox_statuses():
    items = get_sandbox_manager().observe_all()
    return ok(
        {
            "items": items,
            "total": len(items),
            "running_verified": sum(item["verdict"] == "RUNNING_VERIFIED" for item in items),
            "stale_running": sum(item["verdict"] == "STALE_RUNNING" for item in items),
        }
    )


@router.get("/{sandbox_id}/status")
def sandbox_status(sandbox_id: str):
    return ok(get_sandbox_manager().observe(sandbox_id))


@router.get("/{sandbox_id}/mounts")
def get_mounts(sandbox_id: str):
    sandbox = get_sandbox_manager().get(sandbox_id)
    return ok(
        {
            "sandbox_id": sandbox.id,
            "mounts": [
                {"name": "raw", "path": "/mnt/raw", "mode": "ro", "read_only": True},
                {"name": "work", "path": "/mnt/work", "mode": "rw", "read_only": False},
            ],
        }
    )


@router.post("/{sandbox_id}/exec")
def exec_code(sandbox_id: str, body: ExecBody, request: Request):
    result = get_sandbox_manager().get(sandbox_id).exec(body.code, ip=client_ip(request))
    return ok(result)


@router.post("/{sandbox_id}/write")
def write_work_file(sandbox_id: str, body: WriteFileBody, request: Request):
    sandbox = get_sandbox_manager().get(sandbox_id)
    result = sandbox.write_file(body.path, body.content.encode("utf-8"), ip=client_ip(request))
    return ok(result)


@router.get("/{sandbox_id}/files")
def list_files(sandbox_id: str):
    sandbox = get_sandbox_manager().get(sandbox_id)
    return ok({"sandbox_id": sandbox_id, "files": sandbox.list_files()})


@router.get("/{sandbox_id}/files/{path:path}")
def download_file(sandbox_id: str, path: str):
    sandbox = get_sandbox_manager().get(sandbox_id)
    normalized = sandbox.normalize_path(path)
    if normalized.startswith("/mnt/raw"):
        raise biz_error(1005, "原始卷文件不能从沙箱直接下载")
    content = sandbox.read_file(normalized)
    media_type = mimetypes.guess_type(normalized)[0] or "application/octet-stream"
    from fastapi.responses import Response

    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{Path(normalized).name}"'},
    )


@router.delete("/{sandbox_id}")
def destroy_sandbox(sandbox_id: str, request: Request):
    return ok(get_sandbox_manager().destroy(sandbox_id, ip=client_ip(request)))

