"""可信数据空间原型配置。

所有路径均支持环境变量覆盖，便于测试与本地演示。
"""

from __future__ import annotations

import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.getenv("TRUSTED_SPACE_DATA_DIR", BASE_DIR / "data"))
DB_PATH = Path(os.getenv("TRUSTED_SPACE_DB", DATA_DIR / "trusted_space.db"))
EXPORTS_DIR = Path(os.getenv("TRUSTED_SPACE_EXPORTS", BASE_DIR / "exports"))
LOGS_DIR = Path(os.getenv("TRUSTED_SPACE_LOGS", BASE_DIR / "logs"))
SANDBOXES_DIR = Path(os.getenv("TRUSTED_SPACE_SANDBOXES", DATA_DIR / "sandboxes"))
STATIC_DIR = BASE_DIR / "static"

HOST = os.getenv("TRUSTED_SPACE_HOST", "127.0.0.1")
PORT = int(os.getenv("TRUSTED_SPACE_PORT", "8000"))

SANDBOX_BACKEND = os.getenv("SANDBOX_BACKEND", "").strip().lower()
if not SANDBOX_BACKEND:
    SANDBOX_BACKEND = "mock" if os.getenv("MOCK_SANDBOX", "true").lower() not in {"0", "false", "no"} else "opensandbox"
if SANDBOX_BACKEND not in {"mock", "docker", "opensandbox"}:
    SANDBOX_BACKEND = "mock"
MOCK_SANDBOX = SANDBOX_BACKEND == "mock"

DOCKER_BIN = os.getenv("DOCKER_BIN", "docker")
DOCKER_SANDBOX_IMAGE = os.getenv("DOCKER_SANDBOX_IMAGE", "python:3.11-slim")
DOCKER_MEMORY = os.getenv("DOCKER_MEMORY", "512m")
DOCKER_CPUS = os.getenv("DOCKER_CPUS", "1")
OPENSANDBOX_IMAGE = os.getenv("OPENSANDBOX_IMAGE", "python:3.11-slim")
OPENSANDBOX_ADAPTER = os.getenv("OPENSANDBOX_ADAPTER", "").strip()

SANDBOX_EXEC_TIMEOUT = int(os.getenv("SANDBOX_EXEC_TIMEOUT", "10"))
UPLOAD_MAX_BYTES = 20 * 1024 * 1024

FIELD_POLICY_COUNT = 128
READONLY_VOLUME_COUNT = 3

for directory in (DATA_DIR, EXPORTS_DIR, LOGS_DIR, SANDBOXES_DIR):
    directory.mkdir(parents=True, exist_ok=True)

