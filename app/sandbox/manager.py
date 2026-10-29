"""可信沙箱管理器。

管理器负责沙箱生命周期、卷持久化和业务追踪；具体执行隔离由可替换运行时
提供。SANDBOX_BACKEND 选择 mock、docker 或 opensandbox，三者共享相同的
文件、执行和销毁接口。
"""

from __future__ import annotations

import csv
import hashlib
import io
import random
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, ClassVar

from app.common import biz_error, now_iso
from app.config import SANDBOX_BACKEND, SANDBOXES_DIR, SANDBOX_EXEC_TIMEOUT
from app.db import connection, insert_audit, row_to_dict
from app.logging_config import log_event
from app.observability.trace import TraceRecorder


DEFAULT_CODE = '''import csv
import random

random.seed(42)
cities = ["上海", "北京", "深圳", "杭州", "成都"]
with open("/mnt/work/customer.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.writer(f)
    writer.writerow(["customer_id", "name", "phone", "city", "amount"])
    for i in range(1, 5001):
        writer.writerow([
            f"C{i:06d}",
            f"客户{i:04d}",
            f"1380000{i % 10000:04d}",
            random.choice(cities),
            100 + i % 900,
        ])

print("已生成 5000 行 customer.csv")
print("输出路径: /mnt/work/customer.csv")
'''


@dataclass
class MockSandbox:
    backend: ClassVar[str] = "mock"
    id: str
    task_id: str
    status: str = "RUNNING"
    created_at: str = field(default_factory=now_iso)
    files: dict[str, bytes] = field(default_factory=dict)
    mounts: dict[str, str] = field(default_factory=lambda: {"raw": "/mnt/raw", "work": "/mnt/work"})
    storage_dir: Path | None = None

    def ensure_storage(self) -> Path:
        root = self.storage_dir or (SANDBOXES_DIR / self.id)
        self.storage_dir = root
        (root / "raw").mkdir(parents=True, exist_ok=True)
        (root / "work").mkdir(parents=True, exist_ok=True)
        return root

    def _disk_path(self, logical_path: str) -> Path | None:
        if self.storage_dir is None:
            return None
        if logical_path.startswith("/mnt/raw/"):
            return self.storage_dir / "raw" / logical_path[len("/mnt/raw/") :]
        if logical_path.startswith("/mnt/work/"):
            return self.storage_dir / "work" / logical_path[len("/mnt/work/") :]
        return None

    def load_storage(self) -> int:
        """从卷目录恢复文件；返回恢复的文件数。"""
        root = self.ensure_storage()
        restored = 0
        for volume in ("raw", "work"):
            base = root / volume
            for file_path in base.rglob("*"):
                if not file_path.is_file() or "__pycache__" in file_path.parts:
                    continue
                relative = file_path.relative_to(base).as_posix()
                self.files[f"/mnt/{volume}/{relative}"] = file_path.read_bytes()
                restored += 1
        return restored

    def _sync_storage_file(self, logical_path: str, content: bytes) -> None:
        target = self._disk_path(logical_path)
        if target is None:
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)

    def reset_work_storage(self) -> None:
        if self.storage_dir is None:
            return
        work_root = self.storage_dir / "work"
        if work_root.exists():
            shutil.rmtree(work_root)
        work_root.mkdir(parents=True, exist_ok=True)

    def _require_running(self) -> None:
        if self.status != "RUNNING":
            raise biz_error(1001, "沙箱已销毁或不可用")

    @staticmethod
    def normalize_path(path: str) -> str:
        raw = (path or "").strip().replace("\\", "/")
        if raw.startswith("/mnt/raw"):
            normalized = raw
        elif raw.startswith("/mnt/work"):
            normalized = raw
        elif raw.startswith("mnt/"):
            normalized = "/" + raw
        else:
            normalized = "/mnt/work/" + raw.lstrip("/")
        parts: list[str] = []
        for part in normalized.split("/"):
            if part in {"", "."}:
                continue
            if part == "..":
                raise biz_error(1003, "非法文件路径")
            parts.append(part)
        final = "/" + "/".join(parts)
        if not (final == "/mnt/raw" or final.startswith("/mnt/raw/") or final == "/mnt/work" or final.startswith("/mnt/work/")):
            raise biz_error(1003, "文件不在允许的挂载卷内")
        return final

    @staticmethod
    def _raw_write_line(code: str) -> str | None:
        """在执行前识别写原始卷的代码行，阻止命令进入子进程。"""
        open_write = re.compile(r"open\s*\([^\n]*?/mnt/raw[^\n]*?,\s*[\"'][^\"']*[wax+][^\"']*[\"']")
        os_open_write = re.compile(r"os\.open\s*\([^\n]*?/mnt/raw[^\n]*?O_(?:WRONLY|RDWR|CREAT|TRUNC|APPEND)")
        writer_call = re.compile(r"(?:to_csv|to_excel|write_text|write_bytes|mkdir|makedirs|unlink|remove|rename|replace|touch)\s*\([^\n]*?/mnt/raw")
        shell_write = re.compile(r"(?:>>?|tee\s+-a?)\s*/mnt/raw")
        copy_dest = re.compile(r"shutil\.(?:copy|copyfile|copy2|move)\s*\([^\n]*?,\s*[\"'][^\"']*/mnt/raw")
        for line in code.splitlines():
            clean = line.split("#", 1)[0]
            if "/mnt/raw" not in clean:
                continue
            if any(pattern.search(clean) for pattern in (open_write, os_open_write, writer_call, shell_write, copy_dest)):
                return line.strip()
        return None

    def snapshot(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "task_id": self.task_id,
            "status": self.status,
            "backend": self.backend,
            "created_at": self.created_at,
            "mounts": self.mounts,
            "file_count": len([path for path in self.files if path.startswith("/mnt/work/")]),
            "storage_dir": str(self.storage_dir) if self.storage_dir else None,
        }

    def observe_runtime(self) -> dict[str, Any]:
        """返回运行时层面的存活证据。

        Mock 沙箱没有长驻进程，只在 exec 时启动 python3 子进程。因此这里的
        ``live`` 表示运行时可接受命令，而不是某个 PID 仍然存在。
        """
        running = self.status == "RUNNING"
        return {
            "mode": "on_demand",
            "live": running,
            "long_lived_process": False,
            "detail": "MockSandbox 按需启动 python3 子进程；RUNNING 表示已注册且可接受执行",
        }

    def write_file(self, path: str, content: bytes, actor: str = "sandbox", ip: str = "127.0.0.1") -> dict[str, Any]:
        with TraceRecorder(
            "sandbox.write",
            actor=actor,
            target=self.id,
            input={"path": path, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()},
        ) as trace:
            trace.step("sandbox.state.check", detail={"status": self.status})
            self._require_running()
            normalized = self.normalize_path(path)
            volume = "raw" if normalized.startswith("/mnt/raw") else "work"
            trace.step(
                "path.normalize",
                detail={"input": path, "normalized": normalized, "volume": volume},
            )
            if normalized == "/mnt/raw" or normalized.startswith("/mnt/raw/"):
                trace.step(
                    "policy.raw_volume.deny",
                    status="blocked",
                    detail={"reason": "原始卷声明为只读", "normalized": normalized},
                )
                insert_audit(
                    "raw_write_blocked",
                    actor,
                    normalized,
                    "blocked",
                    "尝试直接写入原始卷，策略已阻断",
                    ip,
                )
                raise biz_error(1002, "原始卷为只读卷，拒绝写入")
            if normalized == "/mnt/work":
                raise biz_error(1003, "必须指定工作卷文件路径")
            self.files[normalized] = content
            self._sync_storage_file(normalized, content)
            trace.step(
                "volume.write",
                detail={
                    "mount": "/mnt/work",
                    "path": normalized,
                    "bytes": len(content),
                    "storage_path": str(self._disk_path(normalized)),
                },
            )
            result = {"path": normalized, "size": len(content), "trace_id": trace.trace_id}
            trace.set_result(result)
            return result

    def read_file(self, path: str) -> bytes:
        self._require_running()
        normalized = self.normalize_path(path)
        if normalized not in self.files:
            raise biz_error(1003, "文件不存在")
        return self.files[normalized]

    def list_files(self) -> list[dict[str, Any]]:
        self._require_running()
        items = []
        for path, content in sorted(self.files.items()):
            if not path.startswith("/mnt/work/") or path == "/mnt/work":
                continue
            items.append({"path": path, "name": Path(path).name, "size": len(content)})
        return items

    def exec(self, code: str, actor: str = "sandbox", ip: str = "127.0.0.1") -> dict[str, Any]:
        code_hash = hashlib.sha256(code.encode("utf-8")).hexdigest()
        with TraceRecorder(
            "sandbox.exec",
            actor=actor,
            target=self.id,
            input={
                "code_bytes": len(code.encode("utf-8")),
                "code_lines": len(code.splitlines()),
                "code_sha256": code_hash,
                "timeout_seconds": SANDBOX_EXEC_TIMEOUT,
                "ip": ip,
            },
        ) as trace:
            trace.step("sandbox.state.check", detail={"status": self.status, "mounts": self.mounts})
            self._require_running()
            log_event(
                "sandbox.exec.started",
                sandbox_id=self.id,
                backend=self.backend,
                actor=actor,
                code_bytes=len(code.encode("utf-8")),
                code_lines=len(code.splitlines()),
                trace_id=trace.trace_id,
            )
            blocked_line = self._raw_write_line(code)
            trace.step(
                "sandbox.policy.scan",
                status="blocked" if blocked_line else "success",
                detail={
                    "rule": "静态检测 /mnt/raw 的 open/os.open/写文件/重定向/shutil 写操作",
                    "blocked_line": blocked_line,
                    "decision": "DENY" if blocked_line else "ALLOW",
                },
            )
            if blocked_line:
                log_event(
                    "sandbox.exec.blocked",
                    sandbox_id=self.id,
                    backend=self.backend,
                    actor=actor,
                    reason="raw_volume_write",
                    trace_id=trace.trace_id,
                )
                insert_audit(
                    "raw_write_blocked",
                    actor,
                    self.id,
                    "blocked",
                    f"代码尝试写 /mnt/raw，已提前拦截: {blocked_line[:160]}",
                    ip,
                )
                raise biz_error(1002, "原始卷为只读卷，写操作已阻断")

            started = time.perf_counter()
            root = Path(tempfile.mkdtemp(prefix=f"ts-{self.id}-"))
            work_dir = root / "mnt" / "work"
            raw_dir = root / "mnt" / "raw"
            work_dir.mkdir(parents=True)
            raw_dir.mkdir(parents=True)
            trace.step(
                "sandbox.workspace.allocate",
                detail={
                    "host_temp_root": str(root),
                    "logical_raw": "/mnt/raw",
                    "logical_work": "/mnt/work",
                    "actual_raw": str(raw_dir),
                    "actual_work": str(work_dir),
                },
            )
            try:
                materialized: list[dict[str, Any]] = []
                for path, content in self.files.items():
                    if path.startswith("/mnt/work/"):
                        target = work_dir / path[len("/mnt/work/") :]
                        volume = "/mnt/work"
                    elif path.startswith("/mnt/raw/"):
                        target = raw_dir / path[len("/mnt/raw/") :]
                        volume = "/mnt/raw"
                    else:
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
                    materialized.append(
                        {
                            "volume": volume,
                            "path": path,
                            "bytes": len(content),
                            "sha256": hashlib.sha256(content).hexdigest(),
                        }
                    )
                trace.step(
                    "sandbox.volumes.materialize",
                    detail={
                        "file_count": len(materialized),
                        "raw_files": sum(item["volume"] == "/mnt/raw" for item in materialized),
                        "work_files": sum(item["volume"] == "/mnt/work" for item in materialized),
                        "files": materialized,
                    },
                )

                # Mock 环境把绝对挂载路径映射到临时目录，真实沙箱则由 SDK 提供同名挂载。
                raw_replacements = code.count("/mnt/raw")
                work_replacements = code.count("/mnt/work")
                executable = code.replace("/mnt/raw", str(raw_dir)).replace("/mnt/work", str(work_dir))
                trace.step(
                    "sandbox.mount.rewrite",
                    detail={
                        "raw_replacements": raw_replacements,
                        "work_replacements": work_replacements,
                        "original_mounts": {"/mnt/raw": str(raw_dir), "/mnt/work": str(work_dir)},
                        "command_shape": ["python3", "-c", "<rewritten_code>"],
                    },
                )
                try:
                    completed = subprocess.run(
                        ["python3", "-c", executable],
                        cwd=work_dir,
                        capture_output=True,
                        timeout=SANDBOX_EXEC_TIMEOUT,
                        check=False,
                    )
                except subprocess.TimeoutExpired as exc:
                    log_event(
                        "sandbox.exec.timeout",
                        sandbox_id=self.id,
                        backend=self.backend,
                        actor=actor,
                        timeout_seconds=SANDBOX_EXEC_TIMEOUT,
                        trace_id=trace.trace_id,
                    )
                    trace.step(
                        "sandbox.process.exit",
                        status="failed",
                        detail={"timeout_seconds": SANDBOX_EXEC_TIMEOUT, "reason": "subprocess.TimeoutExpired"},
                    )
                    insert_audit("sandbox.exec_timeout", actor, self.id, "failed", "沙箱执行超过 10 秒", ip)
                    raise biz_error(1004, "沙箱执行超时") from exc

                # 子进程结束后同步工作卷；原始卷永不回写。
                previous_work_paths = [item for item in self.files if item.startswith("/mnt/work/")]
                for path in previous_work_paths:
                    self.files.pop(path, None)
                synced: list[dict[str, Any]] = []
                for file_path in work_dir.rglob("*"):
                    if file_path.is_file() and "__pycache__" not in file_path.parts:
                        relative = file_path.relative_to(work_dir).as_posix()
                        content = file_path.read_bytes()
                        self.files[f"/mnt/work/{relative}"] = content
                        synced.append(
                            {
                                "path": f"/mnt/work/{relative}",
                                "bytes": len(content),
                                "sha256": hashlib.sha256(content).hexdigest(),
                            }
                        )

                duration = round((time.perf_counter() - started) * 1000, 2)
                stdout = completed.stdout.decode("utf-8", errors="replace")
                stderr = completed.stderr.decode("utf-8", errors="replace")
                # 将临时目录还原成沙箱内的逻辑路径，避免演示输出泄露宿主路径。
                stdout = stdout.replace(str(work_dir), "/mnt/work").replace(str(raw_dir), "/mnt/raw")
                stderr = stderr.replace(str(work_dir), "/mnt/work").replace(str(raw_dir), "/mnt/raw")
                trace.step(
                    "sandbox.process.exit",
                    status="success" if completed.returncode == 0 else "failed",
                    detail={
                        "exit_code": completed.returncode,
                        "duration_ms": duration,
                        "stdout": stdout[:4000],
                        "stderr": stderr[:4000],
                    },
                )
                trace.step(
                    "sandbox.volume.sync",
                    detail={
                        "volume": "/mnt/work",
                        "removed_previous_files": len(previous_work_paths),
                        "synced_files": len(synced),
                        "files": synced,
                    },
                )
                self.reset_work_storage()
                for item in synced:
                    self._sync_storage_file(item["path"], self.files[item["path"]])
                trace.step(
                    "sandbox.storage.persist",
                    detail={
                        "storage_dir": str(self.storage_dir),
                        "persisted_files": len(synced),
                        "survives_restart": True,
                    },
                )
                insert_audit(
                    "sandbox.exec",
                    actor,
                    self.id,
                    "success" if completed.returncode == 0 else "failed",
                    f"exit={completed.returncode}; stdout={stdout[:240]}",
                    ip,
                )
                result = {
                    "sandbox_id": self.id,
                    "backend": self.backend,
                    "exit_code": completed.returncode,
                    "stdout": stdout,
                    "stderr": stderr,
                    "duration_ms": duration,
                    "files": self.list_files(),
                    "trace_id": trace.trace_id,
                }
                trace.set_result(result)
                log_event(
                    "sandbox.exec.completed",
                    sandbox_id=self.id,
                    backend=self.backend,
                    actor=actor,
                    status="success" if completed.returncode == 0 else "failed",
                    exit_code=completed.returncode,
                    duration_ms=duration,
                    files=len(synced),
                    trace_id=trace.trace_id,
                )
                if completed.returncode != 0:
                    trace.finish(
                        "failed",
                        {"type": "NonZeroExit", "message": f"python 进程退出码 {completed.returncode}"},
                    )
                return result
            finally:
                shutil.rmtree(root, ignore_errors=True)
                trace.step("sandbox.workspace.cleanup", detail={"host_temp_root": str(root), "removed": not root.exists()})

    def destroy(self) -> None:
        self.status = "DESTROYED"
        self.files.clear()
        if self.storage_dir and self.storage_dir.exists():
            shutil.rmtree(self.storage_dir, ignore_errors=True)


class SandboxManager:

    def __init__(self, backend: str | None = None) -> None:
        self._sandboxes: dict[str, MockSandbox] = {}
        self._lock = threading.RLock()
        self.backend = backend or SANDBOX_BACKEND

    def _build_runtime(
        self,
        sandbox_id: str,
        task_id: str,
        *,
        backend: str | None = None,
        created_at: str | None = None,
        storage_dir: Path | None = None,
    ) -> MockSandbox:
        # 延迟导入避免 runtimes -> manager -> runtimes 的循环依赖。
        from app.sandbox.runtimes import build_sandbox

        return build_sandbox(
            sandbox_id,
            task_id,
            backend or self.backend,
            created_at=created_at,
            storage_dir=storage_dir,
        )

    def create(self, task_id: str = "", actor: str = "alice", ip: str = "127.0.0.1") -> dict[str, Any]:
        sandbox_id = f"SBX-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:6]}"
        log_event(
            "sandbox.creating",
            sandbox_id=sandbox_id,
            backend=self.backend,
            task_id=task_id or "trusted-data-demo",
            actor=actor,
        )
        with TraceRecorder(
            "sandbox.create",
            actor=actor,
            target=sandbox_id,
            input={"task_id": task_id or "trusted-data-demo", "backend": self.backend, "ip": ip},
        ) as trace:
            trace.step("sandbox.id.allocate", detail={"sandbox_id": sandbox_id, "id_rule": "SBX-YYYYMMDD-random6"})
            sandbox = self._build_runtime(sandbox_id, task_id or "trusted-data-demo")
            trace.step(
                "sandbox.volumes.initialize",
                detail={
                    "backend": self.backend,
                    "raw": {"path": "/mnt/raw", "mode": "ro"},
                    "work": {"path": "/mnt/work", "mode": "rw"},
                },
            )
            sandbox.ensure_storage()
            trace.step(
                "sandbox.storage.initialize",
                detail={
                    "storage_dir": str(sandbox.storage_dir),
                    "volumes": [str(sandbox.storage_dir / "raw"), str(sandbox.storage_dir / "work")],
                },
            )
            if self.backend != "mock":
                try:
                    sandbox.initialize_runtime(trace)
                except Exception as exc:
                    log_event(
                        "sandbox.initialize.failed",
                        sandbox_id=sandbox_id,
                        backend=self.backend,
                        actor=actor,
                        error=str(exc),
                        trace_id=trace.trace_id,
                    )
                    trace.step("sandbox.runtime.initialize", status="failed", detail={"backend": self.backend, "error": str(exc)})
                    raise biz_error(1008, f"{self.backend} 沙箱初始化失败: {exc}") from exc
            else:
                trace.step("sandbox.runtime.initialize", detail={"backend": "mock", "implementation": "MockSandbox"})
            log_event(
                "sandbox.initialized",
                sandbox_id=sandbox_id,
                backend=self.backend,
                volumes="raw:ro,work:rw",
                storage_dir=sandbox.storage_dir,
                trace_id=trace.trace_id,
            )
            with self._lock:
                self._sandboxes[sandbox_id] = sandbox
            trace.step("sandbox.runtime.register", detail={"status": sandbox.status, "memory_key": sandbox_id})
            with connection() as conn:
                conn.execute(
                    "INSERT INTO sandboxes(id, task_id, status, backend, created_at) VALUES (?, ?, ?, ?, ?)",
                    (sandbox.id, sandbox.task_id, sandbox.status, sandbox.backend, sandbox.created_at),
                )
            trace.step("database.sandbox.persist", detail={"table": "sandboxes", "status": sandbox.status})
            insert_audit("sandbox.create", actor, sandbox.id, "success", f"task_id={sandbox.task_id}", ip)
            trace.step("audit.write", detail={"action": "sandbox.create", "result": "success"})
            log_event(
                "sandbox.created",
                sandbox_id=sandbox.id,
                backend=sandbox.backend,
                task_id=sandbox.task_id,
                actor=actor,
                status=sandbox.status,
                trace_id=trace.trace_id,
            )
            snapshot = {**sandbox.snapshot(), "trace_id": trace.trace_id}
            trace.set_result(snapshot)
            trace.step("sandbox.running", detail={"created_at": sandbox.created_at, "ready": True})
            return snapshot

    def get(self, sandbox_id: str) -> MockSandbox:
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)
        if sandbox is None or sandbox.status != "RUNNING":
            raise biz_error(1001, "沙箱不存在")
        return sandbox

    def recover_running(self) -> list[dict[str, Any]]:
        """按数据库状态恢复运行中的沙箱卷，避免服务重启丢失工作文件。"""
        with connection() as conn:
            rows = conn.execute("SELECT * FROM sandboxes WHERE status='RUNNING' ORDER BY created_at").fetchall()
        recovered: list[dict[str, Any]] = []
        with self._lock:
            for row in rows:
                if row["id"] in self._sandboxes:
                    continue
                root = SANDBOXES_DIR / row["id"]
                backend = row["backend"] if "backend" in row.keys() else self.backend
                sandbox = self._build_runtime(
                    row["id"],
                    row["task_id"] or "recovered",
                    backend=backend,
                    created_at=row["created_at"],
                    storage_dir=root,
                )
                restored = sandbox.load_storage()
                self._sandboxes[sandbox.id] = sandbox
                log_event(
                    "sandbox.recovered",
                    sandbox_id=sandbox.id,
                    backend=backend,
                    restored_files=restored,
                    storage_dir=root,
                )
                recovered.append(
                    {
                        "sandbox_id": sandbox.id,
                        "backend": backend,
                        "restored_files": restored,
                        "storage_dir": str(root),
                    }
                )
        return recovered

    def list(self) -> list[dict[str, Any]]:
        with self._lock:
            return [sandbox.snapshot() for sandbox in self._sandboxes.values() if sandbox.status == "RUNNING"]

    @staticmethod
    def _storage_observation(storage_dir: Path | None) -> dict[str, Any]:
        result: dict[str, Any] = {
            "path": str(storage_dir) if storage_dir else None,
            "exists": False,
            "raw_exists": False,
            "work_exists": False,
            "raw_files": 0,
            "work_files": 0,
            "total_bytes": 0,
            "latest_modified_at": None,
        }
        if storage_dir is None or not storage_dir.exists():
            return result
        result["exists"] = True
        latest_mtime = 0.0
        for volume in ("raw", "work"):
            base = storage_dir / volume
            result[f"{volume}_exists"] = base.is_dir()
            if not base.is_dir():
                continue
            count = 0
            for file_path in base.rglob("*"):
                if not file_path.is_file() or "__pycache__" in file_path.parts:
                    continue
                count += 1
                stat = file_path.stat()
                result["total_bytes"] += stat.st_size
                latest_mtime = max(latest_mtime, stat.st_mtime)
            result[f"{volume}_files"] = count
        if latest_mtime:
            result["latest_modified_at"] = datetime.fromtimestamp(latest_mtime).astimezone().isoformat(timespec="seconds")
        return result

    def _last_activity(self, sandbox_id: str) -> dict[str, Any] | None:
        with connection() as conn:
            row = conn.execute(
                """
                SELECT action, result, detail, created_at
                FROM audit_logs
                WHERE target=?
                ORDER BY id DESC LIMIT 1
                """,
                (sandbox_id,),
            ).fetchone()
        return dict(row) if row else None

    def observe(self, sandbox_id: str, *, service_local: bool = True) -> dict[str, Any]:
        """联合核对数据库、进程内注册、运行时与卷目录。

        仅凭数据库里的 RUNNING 不能证明沙箱仍可执行，因此状态接口会同时
        返回每个证据项和最终 verdict。
        """
        with connection() as conn:
            row = conn.execute("SELECT * FROM sandboxes WHERE id=?", (sandbox_id,)).fetchone()
        with self._lock:
            sandbox = self._sandboxes.get(sandbox_id)

        # 即使数据库和内存都没有记录，也要检查卷目录，才能识别 ORPHANED_STORAGE。
        storage_dir = (sandbox.storage_dir if sandbox and sandbox.storage_dir else SANDBOXES_DIR / sandbox_id)
        storage = self._storage_observation(storage_dir)
        runtime = (
            sandbox.observe_runtime()
            if sandbox is not None
            else {
                "mode": "not_loaded" if service_local else "external_process",
                "live": False,
                "long_lived_process": False,
                "detail": (
                    "运行时未在当前进程注册"
                    if service_local
                    else "CLI 为独立进程；Mock 运行时需通过服务端状态 API 核验"
                ),
            }
        )
        db_record = dict(row) if row is not None else None
        memory_registered = sandbox is not None and sandbox.status == "RUNNING"
        db_running = bool(db_record and db_record["status"] == "RUNNING")
        runtime_live = bool(runtime.get("live"))
        storage_present = bool(storage["exists"] and storage["raw_exists"] and storage["work_exists"])
        registry_check = {
            "name": "process.registry",
            "ok": memory_registered if service_local else None,
            "skipped": not service_local,
            "detail": "registered" if memory_registered else ("not registered" if service_local else "外部进程不可观测"),
        }
        runtime_check = {
            "name": "runtime.live",
            "ok": runtime_live if service_local else None,
            "skipped": not service_local,
            "detail": runtime.get("detail", runtime.get("mode")),
        }
        checks = [
            {"name": "database.record", "ok": db_record is not None, "detail": db_record["status"] if db_record else "missing"},
            {"name": "database.running", "ok": db_running, "detail": db_record["status"] if db_record else "missing"},
            registry_check,
            runtime_check,
            {"name": "storage.volumes", "ok": storage_present, "detail": storage["path"]},
        ]
        if service_local and memory_registered and db_running and runtime_live and storage_present:
            verdict = "RUNNING_VERIFIED"
        elif not service_local and db_running and storage_present:
            verdict = "EXTERNAL_CONSISTENCY"
        elif db_running:
            verdict = "STALE_RUNNING"
        elif memory_registered:
            verdict = "ORPHANED_RUNTIME"
        elif db_record is not None:
            verdict = "NOT_RUNNING"
        elif storage["exists"]:
            verdict = "ORPHANED_STORAGE"
        else:
            raise biz_error(1003, "沙箱不存在")

        return {
            "sandbox_id": sandbox_id,
            "verdict": verdict,
            "verdict_label": {
                "RUNNING_VERIFIED": "运行事实已核验",
                "EXTERNAL_CONSISTENCY": "数据库与卷一致；运行时请以服务端 API 为准",
                "STALE_RUNNING": "数据库显示 RUNNING，但运行时不可用或未注册",
                "ORPHANED_RUNTIME": "运行时已注册，但数据库或卷不一致",
                "NOT_RUNNING": "已停止或已销毁",
                "ORPHANED_STORAGE": "仅残留卷目录，无运行时记录",
            }[verdict],
            "observed_at": now_iso(),
            "probe_scope": "service_process" if service_local else "external_process",
            "checks": checks,
            "checks_passed": sum(1 for item in checks if item["ok"] is True),
            "database": db_record,
            "memory_registered": memory_registered,
            "runtime": runtime,
            "storage": storage,
            "last_activity": self._last_activity(sandbox_id),
            "snapshot": sandbox.snapshot() if sandbox is not None else None,
        }

    def observe_all(self, *, service_local: bool = True) -> list[dict[str, Any]]:
        with connection() as conn:
            ids = [row["id"] for row in conn.execute("SELECT id FROM sandboxes ORDER BY created_at DESC").fetchall()]
        # 只扫描卷目录也能发现“数据库记录已丢失、目录仍残留”的孤儿沙箱。
        if SANDBOXES_DIR.exists():
            ids.extend(sorted(path.name for path in SANDBOXES_DIR.iterdir() if path.is_dir()))
        with self._lock:
            ids.extend(item_id for item_id in self._sandboxes if item_id not in ids)
        seen: set[str] = set()
        items: list[dict[str, Any]] = []
        for sandbox_id in ids:
            if sandbox_id in seen:
                continue
            seen.add(sandbox_id)
            try:
                items.append(self.observe(sandbox_id, service_local=service_local))
            except Exception:
                continue
        return items

    def destroy(self, sandbox_id: str, actor: str = "alice", ip: str = "127.0.0.1") -> dict[str, Any]:
        with TraceRecorder(
            "sandbox.destroy",
            actor=actor,
            target=sandbox_id,
            input={"ip": ip},
        ) as trace:
            trace.step("sandbox.state.check", detail={"sandbox_id": sandbox_id})
            sandbox = self.get(sandbox_id)
            backend = sandbox.backend
            work_files = len(sandbox.list_files())
            trace.step("sandbox.volume.purge", detail={"work_files": len(sandbox.list_files()), "raw_mode": "ro"})
            sandbox.destroy()
            with connection() as conn:
                conn.execute(
                    "UPDATE sandboxes SET status='DESTROYED', destroyed_at=? WHERE id=?",
                    (now_iso(), sandbox_id),
                )
            trace.step("database.sandbox.update", detail={"status": "DESTROYED"})
            insert_audit("sandbox.destroy", actor, sandbox_id, "success", "沙箱与工作卷已销毁", ip)
            log_event(
                "sandbox.destroyed",
                sandbox_id=sandbox_id,
                backend=backend,
                actor=actor,
                purged_work_files=work_files,
                trace_id=trace.trace_id,
            )
            result = {"id": sandbox_id, "status": "DESTROYED", "trace_id": trace.trace_id}
            trace.set_result(result)
            return result

    def seed_demo(self) -> dict[str, Any]:
        with TraceRecorder(
            "sandbox.seed",
            actor="system",
            target="sbx-demo",
            input={
                "phase": "application_startup",
                "backend": self.backend,
                "seed_dataset": ["customer.csv", "order.csv"],
            },
        ) as trace:
            trace.step("database.ready", detail={"table": "sandboxes", "startup_order": "init_db -> seed_demo"})
            recovered = self.recover_running()
            trace.step(
                "sandbox.recovery.scan",
                detail={
                    "database_status": "RUNNING",
                    "recovered_from_disk": len(recovered),
                    "items": recovered,
                    "storage_root": str(SANDBOXES_DIR),
                },
            )
            with self._lock:
                existing = self._sandboxes.get("sbx-demo")
                if existing and existing.status == "RUNNING" and existing.backend != self.backend:
                    # 后端切换后同一 sandbox id 不能再使用旧运行时；卷文件仍在磁盘上。
                    previous_backend = existing.backend
                    existing.destroy()
                    self._sandboxes.pop(existing.id, None)
                    log_event(
                        "sandbox.backend.replaced",
                        sandbox_id=existing.id,
                        from_backend=previous_backend,
                        to_backend=self.backend,
                        trace_id=trace.trace_id,
                    )
                    trace.step(
                        "sandbox.runtime.replace",
                        detail={"sandbox_id": existing.id, "from_backend": existing.backend, "to_backend": self.backend},
                    )
                    existing = None
                if existing and existing.status == "RUNNING":
                    customer = _seed_csv("customer", 120)
                    order = _seed_csv("order", 200)
                    repaired: list[str] = []
                    existing.ensure_storage()
                    if "/mnt/raw/customer.csv" not in existing.files:
                        existing.files["/mnt/raw/customer.csv"] = customer
                        existing._sync_storage_file("/mnt/raw/customer.csv", customer)
                        repaired.append("/mnt/raw/customer.csv")
                    if "/mnt/raw/order.csv" not in existing.files:
                        existing.files["/mnt/raw/order.csv"] = order
                        existing._sync_storage_file("/mnt/raw/order.csv", order)
                        repaired.append("/mnt/raw/order.csv")
                    trace.step(
                        "sandbox.reuse",
                        detail={
                            "sandbox_id": existing.id,
                            "created_at": existing.created_at,
                            "file_count": len(existing.files),
                            "storage_dir": str(existing.storage_dir),
                            "repaired_seed_files": repaired,
                        },
                    )
                    log_event(
                        "sandbox.demo.reused",
                        sandbox_id=existing.id,
                        backend=existing.backend,
                        files=len(existing.files),
                        repaired_files=len(repaired),
                        trace_id=trace.trace_id,
                    )
                    snapshot = {**existing.snapshot(), "trace_id": trace.trace_id}
                    trace.set_result(snapshot)
                    return snapshot
                sandbox = self._build_runtime("sbx-demo", "demo-raw-data")
                sandbox.ensure_storage()
                restored_files = sandbox.load_storage()
                if self.backend != "mock":
                    try:
                        sandbox.initialize_runtime(trace)
                    except Exception as exc:
                        trace.step("sandbox.runtime.initialize", status="failed", detail={"backend": self.backend, "error": str(exc)})
                        raise biz_error(1008, f"{self.backend} 演示沙箱初始化失败: {exc}") from exc
                trace.step(
                    "sandbox.runtime.initialize",
                    detail={
                        "sandbox_id": sandbox.id,
                        "backend": self.backend,
                        "implementation": type(sandbox).__name__,
                        "restored_files": restored_files,
                        "status": sandbox.status,
                    },
                )
                log_event(
                    "sandbox.initialized",
                    sandbox_id=sandbox.id,
                    backend=sandbox.backend,
                    volumes="raw:ro,work:rw",
                    restored_files=restored_files,
                    source="demo_seed",
                    trace_id=trace.trace_id,
                )
                trace.step(
                    "sandbox.mounts.configure",
                    detail={
                        "raw": {"path": "/mnt/raw", "mode": "ro"},
                        "work": {"path": "/mnt/work", "mode": "rw"},
                    },
                )
                customer = _seed_csv("customer", 120)
                order = _seed_csv("order", 200)
                sandbox.files["/mnt/raw/customer.csv"] = customer
                sandbox.files["/mnt/raw/order.csv"] = order
                sandbox._sync_storage_file("/mnt/raw/customer.csv", customer)
                sandbox._sync_storage_file("/mnt/raw/order.csv", order)
                self._sandboxes[sandbox.id] = sandbox
            trace.step(
                "sandbox.seed.raw_volume",
                detail={
                    "files": [
                        {"path": "/mnt/raw/customer.csv", "bytes": len(customer), "sha256": hashlib.sha256(customer).hexdigest()},
                        {"path": "/mnt/raw/order.csv", "bytes": len(order), "sha256": hashlib.sha256(order).hexdigest()},
                    ]
                },
            )
            trace.step(
                "sandbox.storage.initialize",
                detail={
                    "storage_dir": str(sandbox.storage_dir),
                    "raw_volume": str(sandbox.storage_dir / "raw"),
                    "work_volume": str(sandbox.storage_dir / "work"),
                },
            )
            with connection() as conn:
                conn.execute(
                    """
                    INSERT INTO sandboxes(id, task_id, status, backend, created_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET status='RUNNING', backend=excluded.backend, destroyed_at=NULL
                    """,
                    (sandbox.id, sandbox.task_id, sandbox.status, sandbox.backend, sandbox.created_at),
                )
            trace.step("database.sandbox.persist", detail={"id": sandbox.id, "status": "RUNNING"})
            log_event(
                "sandbox.created",
                sandbox_id=sandbox.id,
                backend=sandbox.backend,
                task_id=sandbox.task_id,
                actor="system",
                status=sandbox.status,
                trace_id=trace.trace_id,
            )
            log_event("sandbox.demo.ready", sandbox_id=sandbox.id, backend=sandbox.backend, trace_id=trace.trace_id)
            snapshot = {**sandbox.snapshot(), "trace_id": trace.trace_id}
            trace.set_result(snapshot)
            trace.step("sandbox.running", detail={"initialized_at": sandbox.created_at, "ready": True})
            return snapshot

    def preview(self, sandbox_id: str, file_path: str, limit: int = 5) -> list[dict[str, str]]:
        sandbox = self.get(sandbox_id)
        data = sandbox.read_file(file_path)
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return []
        rows = list(csv.reader(io.StringIO(text, newline="")))[: limit + 1]
        if not rows:
            return []
        header = rows[0]
        return [dict(zip(header, row)) for row in rows[1:]]


def generate_dataset(table_name: str, rows: int, seed: int = 42) -> bytes:
    """生成用于沙箱演示的通用 CSV 数据。"""
    rng = random.Random(seed)
    table = re.sub(r"[^a-zA-Z0-9_-]", "_", table_name or "dataset")
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["customer_id", "name", "phone", "city", "amount"])
    cities = ["上海", "北京", "深圳", "杭州", "成都", "武汉"]
    for index in range(1, max(1, min(rows, 50_000)) + 1):
        writer.writerow(
            [
                f"C{index:06d}",
                f"{table}_{index:04d}",
                f"1380000{index % 10000:04d}",
                rng.choice(cities),
                100 + index % 900,
            ]
        )
    return output.getvalue().encode("utf-8")


def _seed_csv(kind: str, rows: int) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["customer_id", "name", "phone", "city", "amount"])
    for index in range(1, rows + 1):
        writer.writerow([f"C{index:06d}", f"{kind}_{index:04d}", f"1390000{index % 10000:04d}", "上海", 50 + index])
    return output.getvalue().encode("utf-8")


manager = SandboxManager()


def get_sandbox_manager() -> SandboxManager:
    return manager


def demo_file_list() -> list[dict[str, Any]]:
    with connection() as conn:
        rows = conn.execute("SELECT * FROM sandboxes ORDER BY created_at DESC").fetchall()
    return [row_to_dict(row) for row in rows if row["status"] == "RUNNING"]

