"""应用日志配置。

日志按用途拆成三个独立通道，避免算法执行步骤淹没业务生命周期：

* ``logs/events.log``：INFO 级业务事件，例如沙箱创建/销毁、水印嵌入、审批迁移。
* ``logs/trace.log``：每次执行的阶段、参数摘要和耗时，用于排查算法细节。
* ``logs/http.log``：HTTP 请求、状态码、耗时和 request_id。

只有业务事件同时输出到终端；详细 trace 与 HTTP 访问记录按需通过 API/CLI 查看。
"""

from __future__ import annotations

import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.config import LOGS_DIR


EVENTS_FILE = LOGS_DIR / "events.log"
TRACE_FILE = LOGS_DIR / "trace.log"
HTTP_FILE = LOGS_DIR / "http.log"

# 兼容旧调用方：默认日志现在是纯业务事件，不再是混合过的 backend.log。
LOG_FILE = EVENTS_FILE
LOG_FILES: dict[str, Path] = {
    "events": EVENTS_FILE,
    "trace": TRACE_FILE,
    "http": HTTP_FILE,
}
LOG_LABELS: dict[str, str] = {
    "events": "业务生命周期事件",
    "trace": "执行细节 / 算法阶段",
    "http": "HTTP 访问日志",
}


def _formatter() -> logging.Formatter:
    return logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def _file_handler(path: Path) -> RotatingFileHandler:
    handler = RotatingFileHandler(
        path,
        maxBytes=2 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    handler.setFormatter(_formatter())
    return handler


def _configure(name: str, path: Path, *, console: bool) -> logging.Logger:
    target = logging.getLogger(name)
    if target.handlers:
        return target
    target.setLevel(logging.INFO)
    target.propagate = False
    if console:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(_formatter())
        target.addHandler(stream_handler)
    target.addHandler(_file_handler(path))
    return target


logger = _configure("trusted_space.events", EVENTS_FILE, console=True)
trace_logger = _configure("trusted_space.trace", TRACE_FILE, console=False)
http_logger = _configure("trusted_space.http", HTTP_FILE, console=False)


def _format_value(value: Any) -> str:
    """保留单行与可检索性：复杂或含空格的值使用 JSON 双引号。"""
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Path):
        value = str(value)
    if not isinstance(value, str):
        value = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    clean = value.replace("\r", "\\r").replace("\n", "\\n")
    if not clean or any(char.isspace() for char in clean) or '"' in clean:
        return json.dumps(clean, ensure_ascii=False)
    return clean


def log_event(event: str, **fields: Any) -> None:
    """写一条单行业务事件。

    使用稳定的 ``事件名 + key=value`` 结构，既方便终端阅读，也能直接 grep：

    ``event=sandbox.created sandbox_id=SBX-... backend=mock actor=alice``
    """
    details = " ".join(f"{key}={_format_value(value)}" for key, value in fields.items())
    logger.info("event=%s%s", event, f" {details}" if details else "")

