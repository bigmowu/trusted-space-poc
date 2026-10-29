"""SQLite 初始化、种子数据与审计日志写入。"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

from app.common import now_iso
from app.config import DB_PATH


SCHEMA = """
CREATE TABLE IF NOT EXISTS sandboxes (
    id TEXT PRIMARY KEY,
    task_id TEXT,
    status TEXT NOT NULL,
    backend TEXT NOT NULL DEFAULT 'mock',
    created_at TEXT NOT NULL,
    destroyed_at TEXT
);

CREATE TABLE IF NOT EXISTS data_assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT NOT NULL UNIQUE,
    owner TEXT NOT NULL,
    data_level TEXT NOT NULL,
    is_secret INTEGER NOT NULL DEFAULT 0,
    field_count INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    share_status TEXT NOT NULL DEFAULT '未共享',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS export_requests (
    id TEXT PRIMARY KEY,
    sandbox_id TEXT NOT NULL,
    file_path TEXT NOT NULL,
    applicant TEXT NOT NULL,
    purpose TEXT NOT NULL,
    receiver TEXT NOT NULL,
    data_level TEXT NOT NULL,
    channel TEXT NOT NULL,
    status TEXT NOT NULL,
    watermark_id TEXT,
    approver TEXT,
    approval_comment TEXT,
    approval_stage INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    approved_at TEXT
);

CREATE TABLE IF NOT EXISTS watermark_mappings (
    watermark_id TEXT PRIMARY KEY,
    request_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    export_time TEXT NOT NULL,
    file_id TEXT NOT NULL,
    file_hash TEXT NOT NULL,
    sandbox_id TEXT NOT NULL,
    data_scope TEXT NOT NULL,
    approval_chain TEXT NOT NULL,
    evidence_json TEXT
);

CREATE TABLE IF NOT EXISTS audit_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    actor TEXT NOT NULL,
    target TEXT NOT NULL,
    result TEXT NOT NULL,
    detail TEXT NOT NULL,
    ip TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS execution_traces (
    trace_id TEXT PRIMARY KEY,
    operation TEXT NOT NULL,
    status TEXT NOT NULL,
    actor TEXT NOT NULL,
    target TEXT NOT NULL,
    input_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    error TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms REAL
);

CREATE TABLE IF NOT EXISTS execution_steps (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trace_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    phase TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms REAL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY(trace_id) REFERENCES execution_traces(trace_id)
);

CREATE INDEX IF NOT EXISTS idx_export_status ON export_requests(status);
CREATE INDEX IF NOT EXISTS idx_audit_created ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_logs(action);
CREATE INDEX IF NOT EXISTS idx_trace_started ON execution_traces(started_at DESC);
CREATE INDEX IF NOT EXISTS idx_trace_operation ON execution_traces(operation, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_trace_status ON execution_traces(status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_trace_target ON execution_traces(target, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_steps_trace ON execution_steps(trace_id, seq);
CREATE INDEX IF NOT EXISTS idx_steps_phase ON execution_steps(phase);
"""


SEED_ASSETS = [
    ("customer", "客户运营部", "L3", 1, 32, 128_640, "已共享"),
    ("order", "交易平台部", "L2", 0, 24, 986_320, "已共享"),
    ("product", "商品中心", "L1", 0, 18, 42_800, "公开"),
    ("risk_score", "风控中心", "L4", 1, 41, 68_500, "未共享"),
    ("log", "平台运维部", "L2", 0, 16, 3_210_000, "已共享"),
]


@contextmanager
def connection() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with connection() as conn:
        conn.executescript(SCHEMA)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(export_requests)").fetchall()}
        if "approval_stage" not in columns:
            conn.execute("ALTER TABLE export_requests ADD COLUMN approval_stage INTEGER NOT NULL DEFAULT 0")
        sandbox_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sandboxes)").fetchall()}
        if "backend" not in sandbox_columns:
            # 兼容旧数据库：历史沙箱均由 MockSandbox 创建。
            conn.execute("ALTER TABLE sandboxes ADD COLUMN backend TEXT NOT NULL DEFAULT 'mock'")
        mapping_columns = {row["name"] for row in conn.execute("PRAGMA table_info(watermark_mappings)").fetchall()}
        if "evidence_json" not in mapping_columns:
            conn.execute("ALTER TABLE watermark_mappings ADD COLUMN evidence_json TEXT")
        for name, owner, level, secret, fields, rows, status in SEED_ASSETS:
            conn.execute(
                """
                INSERT OR IGNORE INTO data_assets
                (table_name, owner, data_level, is_secret, field_count, row_count, share_status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (name, owner, level, secret, fields, rows, status, now_iso()),
            )


def insert_audit(
    action: str,
    actor: str,
    target: str,
    result: str,
    detail: str,
    ip: str = "127.0.0.1",
) -> None:
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO audit_logs(action, actor, target, result, detail, ip, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (action, actor or "anonymous", target, result, detail, ip, now_iso()),
        )


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None

