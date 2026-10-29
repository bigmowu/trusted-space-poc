"""跨模块共用的小工具与统一业务异常。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import Request


class BizError(Exception):
    """业务异常；HTTP 状态码用于文件拦截等需要浏览器直接感知的场景。"""

    def __init__(self, code: int, message: str, status_code: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


ERROR_STATUS: dict[int, int] = {
    1001: 404,
    1002: 403,
    1003: 404,
    1004: 504,
    1005: 403,
    1006: 500,
    1007: 409,
    1008: 503,
}


def biz_error(code: int, message: str) -> BizError:
    return BizError(code, message, ERROR_STATUS.get(code, 400))


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def today_compact() -> str:
    return datetime.now().astimezone().strftime("%Y%m%d")


def ok(data: Any = None) -> dict[str, Any]:
    return {"code": 0, "message": "ok", "data": data}


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",", 1)[0].strip()
    return request.client.host if request.client else "127.0.0.1"


def mask_value(value: str, keep: int = 3) -> str:
    value = value or ""
    if len(value) <= keep:
        return "*" * len(value)
    return value[:keep] + "*" * max(2, len(value) - keep)

