"""组件中心 API：状态探测与运行时自检。"""

from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.common import client_ip, ok
from app.components.registry import component_detail, list_components, run_self_test


router = APIRouter(prefix="/api/components", tags=["components"])


class SelfTestBody(BaseModel):
    actor: str = "component-registry"


@router.get("")
def components():
    return ok(list_components())


@router.get("/{component_id}")
def component(component_id: str):
    return ok(component_detail(component_id))


@router.post("/{component_id}/self-test")
def self_test(component_id: str, request: Request, body: SelfTestBody | None = None):
    actor = (body.actor if body else "") or client_ip(request)
    return ok(run_self_test(component_id, actor=actor))
