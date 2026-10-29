"""可替换的沙箱运行时实现。

MockSandbox 用于零依赖演示；DockerSandbox 提供本机真实容器隔离；
OpenSandboxSandbox 通过官方 SDK 或自定义 adapter 接入远端沙箱服务。
三者共用 MockSandbox 的路径、卷和业务接口，上层出域闸门无需感知差异。
"""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import importlib
import importlib.util
import inspect
import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, ClassVar

from app.common import biz_error, now_iso
from app.config import (
    DOCKER_BIN,
    DOCKER_CPUS,
    DOCKER_MEMORY,
    DOCKER_SANDBOX_IMAGE,
    OPENSANDBOX_ADAPTER,
    OPENSANDBOX_IMAGE,
    SANDBOXES_DIR,
    SANDBOX_EXEC_TIMEOUT,
)
from app.db import insert_audit
from app.logging_config import log_event
from app.observability.trace import TraceRecorder
from app.sandbox.manager import MockSandbox


RUNTIME_MANIFESTS = {
    "mock": {
        "name": "MockSandbox",
        "kind": "in_process_tempdir",
        "description": "零依赖演示后端：临时目录物化卷，python3 -c 执行，策略检查后同步工作卷。",
        "isolation": ["进程隔离", "临时目录挂载映射", "原始卷只读策略", "执行超时"],
        "not_for_production": True,
    },
    "docker": {
        "name": "DockerSandbox",
        "kind": "oci_container",
        "description": "本机真实容器：禁用网络、cap-drop ALL、只读根文件系统、资源限额、卷绑定。",
        "isolation": ["容器隔离", "network=none", "cap-drop=ALL", "no-new-privileges", "只读根文件系统", "内存/CPU/PID 限额"],
        "not_for_production": False,
    },
    "opensandbox": {
        "name": "OpenSandboxSandbox",
        "kind": "remote_sandbox_sdk",
        "description": "远端沙箱服务适配器；支持官方 SDK 自动桥接或 OPENSANDBOX_ADAPTER 自定义工厂。",
        "isolation": ["服务端沙箱隔离", "服务端资源策略", "远端镜像与生命周期"],
        "not_for_production": False,
    },
}


def _docker_command(args: list[str], *, timeout: float = 5, input_text: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [DOCKER_BIN, *args],
        input=input_text,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def _clean_cli_error(result: subprocess.CompletedProcess) -> str:
    text = (result.stderr or result.stdout or "").strip()
    if not text:
        text = (result.stdout or "").strip()
    return text.splitlines()[-1][:400] if text else f"exit={result.returncode}"


def docker_health() -> dict[str, Any]:
    binary = shutil.which(DOCKER_BIN) or (DOCKER_BIN if Path(DOCKER_BIN).exists() else None)
    manifest = RUNTIME_MANIFESTS["docker"]
    base = {
        "backend": "docker",
        "implementation": manifest["name"],
        "kind": manifest["kind"],
        "config": {"docker_bin": DOCKER_BIN, "image": DOCKER_SANDBOX_IMAGE, "memory": DOCKER_MEMORY, "cpus": DOCKER_CPUS},
        "capabilities": {
            "network_isolation": True,
            "readonly_raw_volume": True,
            "resource_limits": True,
            "persistent_work_volume": True,
            "remote_execution": False,
        },
        "checks": [],
    }
    if binary is None:
        base.update(status="unavailable", message="未找到 docker 可执行文件")
        base["checks"].append({"name": "docker.binary", "ok": False, "detail": DOCKER_BIN})
        base["remediation"] = "安装 Docker Desktop 或设置 DOCKER_BIN。"
        return base
    base["checks"].append({"name": "docker.binary", "ok": True, "detail": binary})
    try:
        info = _docker_command(["info", "--format", "{{.ServerVersion}}"], timeout=4)
    except (OSError, subprocess.TimeoutExpired) as exc:
        info = None
        base["checks"].append({"name": "docker.daemon", "ok": False, "detail": str(exc)})
    else:
        base["checks"].append(
            {
                "name": "docker.daemon",
                "ok": info.returncode == 0,
                "detail": (info.stdout or "").strip() if info.returncode == 0 else _clean_cli_error(info),
            }
        )
    if info is None or info.returncode != 0:
        base.update(status="unavailable", message="Docker daemon 不可达或无权限")
        base["remediation"] = "在系统终端确认 `docker info` 成功，并给当前用户 Docker socket 权限。"
        return base
    try:
        image = _docker_command(["image", "inspect", DOCKER_SANDBOX_IMAGE, "--format", "{{.Id}}"], timeout=4)
    except (OSError, subprocess.TimeoutExpired) as exc:
        image = None
        base["checks"].append({"name": "docker.image", "ok": False, "detail": str(exc)})
    else:
        base["checks"].append(
            {
                "name": "docker.image",
                "ok": image.returncode == 0,
                "detail": (image.stdout or "").strip() if image.returncode == 0 else _clean_cli_error(image),
            }
        )
    if image is None or image.returncode != 0:
        base.update(status="degraded", message=f"镜像 {DOCKER_SANDBOX_IMAGE} 尚未准备")
        base["remediation"] = f"网络可用时执行 `docker pull {DOCKER_SANDBOX_IMAGE}`，或设置 DOCKER_SANDBOX_IMAGE 为本地已有镜像。"
        return base
    base.update(status="ready", message="Docker 容器沙箱可用")
    return base


@dataclass
class DockerSandbox(MockSandbox):
    backend: ClassVar[str] = "docker"
    container_name: str = ""
    _container_ready: bool = False

    def __post_init__(self) -> None:
        if not self.container_name:
            self.container_name = f"ts-{self.id.lower()}"

    def _sync_all_to_storage(self) -> None:
        self.ensure_storage()
        for logical_path, content in self.files.items():
            self._sync_storage_file(logical_path, content)

    def _reload_work_from_storage(self) -> list[dict[str, Any]]:
        for path in [item for item in self.files if item.startswith("/mnt/work/")]:
            self.files.pop(path, None)
        root = self.ensure_storage() / "work"
        items: list[dict[str, Any]] = []
        for file_path in root.rglob("*"):
            if not file_path.is_file() or "__pycache__" in file_path.parts:
                continue
            relative = file_path.relative_to(root).as_posix()
            content = file_path.read_bytes()
            logical = f"/mnt/work/{relative}"
            self.files[logical] = content
            items.append({"path": logical, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        return items

    def initialize_runtime(self, trace: TraceRecorder | None = None) -> None:
        health = docker_health()
        if health["status"] != "ready":
            raise RuntimeError(health["message"])
        if self._container_ready:
            return
        root = self.ensure_storage()
        self._sync_all_to_storage()
        inspected = _docker_command(["inspect", self.container_name, "--format", "{{.State.Running}}"], timeout=5)
        if inspected.returncode == 0:
            if (inspected.stdout or "").strip().lower() != "true":
                started = _docker_command(["start", self.container_name], timeout=10)
                if started.returncode != 0:
                    raise RuntimeError(f"Docker 容器启动失败: {_clean_cli_error(started)}")
            self._container_ready = True
            if trace:
                trace.step("sandbox.runtime.ensure", detail={"backend": "docker", "container": self.container_name, "reused": True})
            return
        command = [
            "run",
            "-d",
            "--name",
            self.container_name,
            "--network",
            "none",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            "256",
            "--memory",
            DOCKER_MEMORY,
            "--cpus",
            DOCKER_CPUS,
            "--read-only",
            "--tmpfs",
            "/tmp:rw,nosuid,nodev,size=64m",
            "--mount",
            f"type=bind,src={root / 'raw'},dst=/mnt/raw,readonly",
            "--mount",
            f"type=bind,src={root / 'work'},dst=/mnt/work",
            "--workdir",
            "/mnt/work",
            "--entrypoint",
            "/bin/sleep",
            DOCKER_SANDBOX_IMAGE,
            "infinity",
        ]
        created = _docker_command(command, timeout=60)
        if created.returncode != 0:
            raise RuntimeError(f"Docker 容器创建失败: {_clean_cli_error(created)}")
        self._container_ready = True
        if trace:
            trace.step(
                "sandbox.runtime.ensure",
                detail={
                    "backend": "docker",
                    "container": self.container_name,
                    "container_id": (created.stdout or "").strip()[:12],
                    "image": DOCKER_SANDBOX_IMAGE,
                    "network": "none",
                    "rootfs": "read-only",
                    "mounts": {"raw": "ro", "work": "rw"},
                },
            )

    def exec(self, code: str, actor: str = "sandbox", ip: str = "127.0.0.1") -> dict[str, Any]:
        with TraceRecorder(
            "sandbox.exec",
            actor=actor,
            target=self.id,
            input={
                "backend": "docker",
                "container": self.container_name,
                "code_bytes": len(code.encode("utf-8")),
                "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
                "timeout_seconds": SANDBOX_EXEC_TIMEOUT,
                "ip": ip,
            },
        ) as trace:
            trace.step("sandbox.state.check", detail={"status": self.status, "backend": "docker"})
            self._require_running()
            log_event(
                "sandbox.exec.started",
                sandbox_id=self.id,
                backend="docker",
                actor=actor,
                code_bytes=len(code.encode("utf-8")),
                trace_id=trace.trace_id,
            )
            blocked_line = self._raw_write_line(code)
            trace.step(
                "sandbox.policy.scan",
                status="blocked" if blocked_line else "success",
                detail={"rule": "静态检测 /mnt/raw 写操作", "blocked_line": blocked_line, "decision": "DENY" if blocked_line else "ALLOW"},
            )
            if blocked_line:
                log_event(
                    "sandbox.exec.blocked",
                    sandbox_id=self.id,
                    backend="docker",
                    actor=actor,
                    reason="raw_volume_write",
                    trace_id=trace.trace_id,
                )
                insert_audit("raw_write_blocked", actor, self.id, "blocked", f"代码尝试写 /mnt/raw，已提前拦截: {blocked_line[:160]}", ip)
                raise biz_error(1002, "原始卷为只读卷，写操作已阻断")
            try:
                self.initialize_runtime(trace)
            except Exception as exc:
                log_event(
                    "sandbox.exec.failed",
                    sandbox_id=self.id,
                    backend="docker",
                    actor=actor,
                    error=str(exc),
                    trace_id=trace.trace_id,
                )
                trace.step("sandbox.runtime.unavailable", status="failed", detail={"backend": "docker", "error": str(exc)})
                raise biz_error(1003, f"Docker 沙箱不可用: {exc}") from exc
            self._sync_all_to_storage()
            trace.step(
                "sandbox.volume.materialize",
                detail={
                    "backend": "docker",
                    "raw_files": sum(path.startswith("/mnt/raw/") for path in self.files),
                    "work_files": sum(path.startswith("/mnt/work/") for path in self.files),
                },
            )
            started = time.perf_counter()
            try:
                completed = _docker_command(
                    ["exec", "-i", self.container_name, "python", "-c", code],
                    timeout=SANDBOX_EXEC_TIMEOUT + 5,
                )
            except subprocess.TimeoutExpired as exc:
                log_event(
                    "sandbox.exec.timeout",
                    sandbox_id=self.id,
                    backend="docker",
                    actor=actor,
                    timeout_seconds=SANDBOX_EXEC_TIMEOUT,
                    trace_id=trace.trace_id,
                )
                trace.step("sandbox.process.exit", status="failed", detail={"backend": "docker", "reason": "timeout"})
                insert_audit("sandbox.exec_timeout", actor, self.id, "failed", "Docker 沙箱执行超时", ip)
                raise biz_error(1004, "沙箱执行超时") from exc
            duration = round((time.perf_counter() - started) * 1000, 2)
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
            trace.step(
                "sandbox.process.exit",
                status="success" if completed.returncode == 0 else "failed",
                detail={"backend": "docker", "exit_code": completed.returncode, "duration_ms": duration, "stdout": stdout[:4000], "stderr": stderr[:4000]},
            )
            synced = self._reload_work_from_storage()
            trace.step("sandbox.volume.sync", detail={"backend": "docker", "volume": "/mnt/work", "synced_files": len(synced), "files": synced})
            insert_audit(
                "sandbox.exec",
                actor,
                self.id,
                "success" if completed.returncode == 0 else "failed",
                f"backend=docker; exit={completed.returncode}; stdout={stdout[:240]}",
                ip,
            )
            result = {
                "sandbox_id": self.id,
                "backend": "docker",
                "container": self.container_name,
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
                backend="docker",
                actor=actor,
                status="success" if completed.returncode == 0 else "failed",
                exit_code=completed.returncode,
                duration_ms=duration,
                files=len(synced),
                trace_id=trace.trace_id,
            )
            return result

    def observe_runtime(self) -> dict[str, Any]:
        try:
            inspected = _docker_command(
                ["inspect", self.container_name, "--format", "{{json .State}}"],
                timeout=4,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {
                "mode": "container",
                "live": False,
                "long_lived_process": True,
                "container": self.container_name,
                "detail": f"Docker inspect 失败: {exc}",
            }
        if inspected.returncode != 0:
            return {
                "mode": "container",
                "live": False,
                "long_lived_process": True,
                "container": self.container_name,
                "detail": _clean_cli_error(inspected),
            }
        try:
            state = json.loads((inspected.stdout or "{}").strip() or "{}")
        except json.JSONDecodeError:
            state = {}
        running = bool(state.get("Running"))
        return {
            "mode": "container",
            "live": running,
            "long_lived_process": True,
            "container": self.container_name,
            "status": state.get("Status"),
            "started_at": state.get("StartedAt"),
            "finished_at": state.get("FinishedAt"),
            "restarting": state.get("Restarting"),
            "detail": "docker inspect .State.Running" if running else f"container status={state.get('Status', 'unknown')}",
        }

    def destroy(self) -> None:
        try:
            _docker_command(["rm", "-f", self.container_name], timeout=15)
        except (OSError, subprocess.TimeoutExpired):
            pass
        super().destroy()


def _module_supports(module: Any, names: tuple[str, ...]) -> str | None:
    for name in names:
        if hasattr(module, name):
            return name
    return None


def _filtered_call(callable_obj: Any, **kwargs: Any) -> Any:
    signature = inspect.signature(callable_obj)
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return callable_obj(**kwargs)
    accepted = {key: value for key, value in kwargs.items() if key in signature.parameters}
    return callable_obj(**accepted)


class SDKOpenSandboxAdapter:
    """对 opensandbox SDK 的薄适配层。

    不同版本 SDK 的方法名存在差异，因此这里只做有限反射；生产接入可通过
    OPENSANDBOX_ADAPTER=module:factory 提供严格实现的适配器。
    """

    def __init__(self, sandbox: "OpenSandboxSandbox") -> None:
        self.sandbox = sandbox
        self.handle: Any = None

    def _sdk(self) -> Any:
        return importlib.import_module("opensandbox")

    def create(self) -> None:
        sdk = self._sdk()
        factory_name = _module_supports(sdk, ("Sandbox", "SandboxClient", "Client"))
        if factory_name is None:
            raise RuntimeError("opensandbox SDK 未暴露 Sandbox/SandboxClient 工厂")
        factory = getattr(sdk, factory_name)
        kwargs = {
            "image": OPENSANDBOX_IMAGE,
            "timeout": SANDBOX_EXEC_TIMEOUT,
            "name": self.sandbox.container_name,
            "sandbox_id": self.sandbox.id,
        }
        if hasattr(factory, "create"):
            self.handle = _filtered_call(factory.create, **kwargs)
        else:
            self.handle = _filtered_call(factory, **kwargs)
        if hasattr(self.handle, "create_sandbox") and not _module_supports(self.handle, ("exec", "run", "run_command", "execute")):
            self.handle = _filtered_call(self.handle.create_sandbox, **kwargs)
        self.upload_files(self.sandbox.files)

    def upload_files(self, files: dict[str, bytes]) -> int:
        target = self.handle
        if target is None:
            return 0
        method_name = _module_supports(target, ("write_file", "upload_file", "put_file"))
        if method_name is None:
            return 0
        method = getattr(target, method_name)
        uploaded = 0
        for path, content in files.items():
            try:
                method(path, content)
                uploaded += 1
            except TypeError:
                method(path, content.decode("utf-8", errors="replace"))
                uploaded += 1
        return uploaded

    def execute(self, code: str) -> dict[str, Any]:
        target = self.handle
        method_name = _module_supports(target, ("run_command", "exec", "execute", "run"))
        if method_name is None and hasattr(target, "commands"):
            commands = getattr(target, "commands")
            method_name = _module_supports(commands, ("run", "exec"))
            target = commands
        if method_name is None:
            raise RuntimeError("opensandbox SDK 未暴露命令执行方法")
        result = getattr(target, method_name)(code)
        if isinstance(result, str):
            return {"exit_code": 0, "stdout": result, "stderr": ""}
        if isinstance(result, dict):
            return {
                "exit_code": int(result.get("exit_code", result.get("returncode", 0))),
                "stdout": str(result.get("stdout", result.get("output", ""))),
                "stderr": str(result.get("stderr", "")),
            }
        return {
            "exit_code": int(getattr(result, "exit_code", getattr(result, "returncode", 0))),
            "stdout": str(getattr(result, "stdout", getattr(result, "output", ""))),
            "stderr": str(getattr(result, "stderr", "")),
        }

    def download_work(self) -> list[dict[str, Any]]:
        target = self.handle
        read_name = _module_supports(target, ("read_file", "download_file", "get_file"))
        if read_name is None:
            return []
        read = getattr(target, read_name)
        paths = [path for path in self.sandbox.files if path.startswith("/mnt/work/")]
        synced: list[dict[str, Any]] = []
        for path in paths:
            try:
                content = read(path)
            except Exception:
                continue
            if isinstance(content, str):
                content = content.encode("utf-8")
            if isinstance(content, bytes):
                self.sandbox.files[path] = content
                self.sandbox._sync_storage_file(path, content)
                synced.append({"path": path, "bytes": len(content), "sha256": hashlib.sha256(content).hexdigest()})
        return synced

    def destroy(self) -> None:
        target = self.handle
        method_name = _module_supports(target, ("destroy", "close", "kill", "terminate"))
        if method_name:
            try:
                getattr(target, method_name)()
            except Exception:
                pass


def opensandbox_health() -> dict[str, Any]:
    manifest = RUNTIME_MANIFESTS["opensandbox"]
    result = {
        "backend": "opensandbox",
        "implementation": manifest["name"],
        "kind": manifest["kind"],
        "config": {"image": OPENSANDBOX_IMAGE, "adapter": OPENSANDBOX_ADAPTER or "sdk-auto"},
        "capabilities": {
            "remote_execution": True,
            "network_isolation": "由 OpenSandbox 服务端策略决定",
            "readonly_raw_volume": "由适配器卷映射决定",
            "persistent_work_volume": "由适配器卷映射决定",
            "file_sync": "需 SDK 提供 write_file/read_file",
        },
        "checks": [],
    }
    spec = importlib.util.find_spec("opensandbox")
    result["checks"].append({"name": "opensandbox.sdk", "ok": spec is not None, "detail": spec.origin if spec else "not installed"})
    if spec is None:
        result.update(status="unavailable", message="未安装 opensandbox SDK")
        result["remediation"] = "在项目 venv 中执行 `pip install -r requirements.txt`，并配置可访问的 OpenSandbox 服务。"
        return result
    try:
        sdk = importlib.import_module("opensandbox")
    except Exception as exc:
        result.update(status="unavailable", message=f"opensandbox 导入失败: {exc}")
        result["remediation"] = "检查 SDK 与 Python 版本兼容性。"
        return result
    version = getattr(sdk, "__version__", "unknown")
    factory_name = _module_supports(sdk, ("Sandbox", "SandboxClient", "Client"))
    result["checks"].append({"name": "opensandbox.factory", "ok": factory_name is not None, "detail": factory_name or "Sandbox/SandboxClient/Client 均未找到"})
    if OPENSANDBOX_ADAPTER:
        try:
            module_name, factory = OPENSANDBOX_ADAPTER.split(":", 1)
            importlib.import_module(module_name)
            adapter_ok = bool(factory)
        except Exception as exc:
            adapter_ok = False
            result["checks"].append({"name": "opensandbox.adapter", "ok": False, "detail": str(exc)})
        else:
            result["checks"].append({"name": "opensandbox.adapter", "ok": adapter_ok, "detail": OPENSANDBOX_ADAPTER})
    else:
        adapter_ok = True
    result["version"] = version
    result.update(
        status="degraded" if factory_name is None or not adapter_ok or not OPENSANDBOX_ADAPTER else "ready",
        message=(
            "SDK 已安装；自动反射适配器未经服务端验证，请用组件自检验证连通性"
            if not OPENSANDBOX_ADAPTER
            else "SDK 已安装，首次创建沙箱时连接服务"
        ),
    )
    if result["status"] != "ready":
        result["remediation"] = "设置可用的 OPENSANDBOX_ADAPTER，或安装匹配当前 SDK 版本的适配器。"
    return result


@dataclass
class OpenSandboxSandbox(MockSandbox):
    backend: ClassVar[str] = "opensandbox"
    container_name: str = ""
    _adapter: Any = field(default=None, repr=False)

    def __post_init__(self) -> None:
        if not self.container_name:
            self.container_name = f"ts-{self.id.lower()}"

    def _build_adapter(self) -> Any:
        if OPENSANDBOX_ADAPTER:
            module_name, factory_name = OPENSANDBOX_ADAPTER.split(":", 1)
            factory = getattr(importlib.import_module(module_name), factory_name)
            return factory(self)
        return SDKOpenSandboxAdapter(self)

    def initialize_runtime(self, trace: TraceRecorder | None = None) -> None:
        health = opensandbox_health()
        if health["status"] == "unavailable":
            raise RuntimeError(health["message"])
        self.ensure_storage()
        if self._adapter is None:
            self._adapter = self._build_adapter()
            self._adapter.create()
        if trace:
            trace.step(
                "sandbox.runtime.ensure",
                detail={"backend": "opensandbox", "adapter": type(self._adapter).__name__, "remote_name": self.container_name},
            )

    def observe_runtime(self) -> dict[str, Any]:
        live = self.status == "RUNNING" and self._adapter is not None
        return {
            "mode": "remote",
            "live": live,
            "long_lived_process": True,
            "remote_name": self.container_name,
            "adapter": type(self._adapter).__name__ if self._adapter is not None else None,
            "detail": (
                "远端 adapter 已建立；健康探测仍需服务端 SDK/API 支持"
                if live
                else "远端 adapter 尚未建立"
            ),
        }

    def exec(self, code: str, actor: str = "sandbox", ip: str = "127.0.0.1") -> dict[str, Any]:
        with TraceRecorder(
            "sandbox.exec",
            actor=actor,
            target=self.id,
            input={
                "backend": "opensandbox",
                "remote_name": self.container_name,
                "code_bytes": len(code.encode("utf-8")),
                "code_sha256": hashlib.sha256(code.encode("utf-8")).hexdigest(),
                "timeout_seconds": SANDBOX_EXEC_TIMEOUT,
                "ip": ip,
            },
        ) as trace:
            trace.step("sandbox.state.check", detail={"status": self.status, "backend": "opensandbox"})
            self._require_running()
            log_event(
                "sandbox.exec.started",
                sandbox_id=self.id,
                backend="opensandbox",
                actor=actor,
                code_bytes=len(code.encode("utf-8")),
                trace_id=trace.trace_id,
            )
            blocked_line = self._raw_write_line(code)
            trace.step(
                "sandbox.policy.scan",
                status="blocked" if blocked_line else "success",
                detail={"rule": "静态检测 /mnt/raw 写操作", "blocked_line": blocked_line, "decision": "DENY" if blocked_line else "ALLOW"},
            )
            if blocked_line:
                log_event(
                    "sandbox.exec.blocked",
                    sandbox_id=self.id,
                    backend="opensandbox",
                    actor=actor,
                    reason="raw_volume_write",
                    trace_id=trace.trace_id,
                )
                insert_audit("raw_write_blocked", actor, self.id, "blocked", f"代码尝试写 /mnt/raw，已提前拦截: {blocked_line[:160]}", ip)
                raise biz_error(1002, "原始卷为只读卷，写操作已阻断")
            try:
                self.initialize_runtime(trace)
                uploaded = self._adapter.upload_files(self.files)
                trace.step("sandbox.volume.materialize", detail={"backend": "opensandbox", "uploaded_files": uploaded})
                started = time.perf_counter()
                executed = self._adapter.execute(code)
                duration = round((time.perf_counter() - started) * 1000, 2)
            except Exception as exc:
                log_event(
                    "sandbox.exec.failed",
                    sandbox_id=self.id,
                    backend="opensandbox",
                    actor=actor,
                    error=str(exc),
                    trace_id=trace.trace_id,
                )
                trace.step("sandbox.runtime.unavailable", status="failed", detail={"backend": "opensandbox", "error": str(exc)})
                raise biz_error(1003, f"OpenSandbox 不可用: {exc}") from exc
            trace.step(
                "sandbox.process.exit",
                status="success" if executed["exit_code"] == 0 else "failed",
                detail={"backend": "opensandbox", **executed, "duration_ms": duration},
            )
            synced = self._adapter.download_work()
            trace.step("sandbox.volume.sync", detail={"backend": "opensandbox", "synced_files": len(synced), "files": synced})
            insert_audit(
                "sandbox.exec",
                actor,
                self.id,
                "success" if executed["exit_code"] == 0 else "failed",
                f"backend=opensandbox; exit={executed['exit_code']}; stdout={executed['stdout'][:240]}",
                ip,
            )
            result = {
                "sandbox_id": self.id,
                "backend": "opensandbox",
                "remote_name": self.container_name,
                "exit_code": executed["exit_code"],
                "stdout": executed["stdout"],
                "stderr": executed["stderr"],
                "duration_ms": duration,
                "files": self.list_files(),
                "trace_id": trace.trace_id,
            }
            trace.set_result(result)
            log_event(
                "sandbox.exec.completed",
                sandbox_id=self.id,
                backend="opensandbox",
                actor=actor,
                status="success" if executed["exit_code"] == 0 else "failed",
                exit_code=executed["exit_code"],
                duration_ms=duration,
                files=len(synced),
                trace_id=trace.trace_id,
            )
            return result

    def destroy(self) -> None:
        if self._adapter is not None:
            try:
                self._adapter.destroy()
            except Exception:
                pass
        super().destroy()


def build_sandbox(sandbox_id: str, task_id: str, backend: str, *, created_at: str | None = None, storage_dir: Path | None = None) -> MockSandbox:
    kwargs: dict[str, Any] = {
        "id": sandbox_id,
        "task_id": task_id,
        "storage_dir": storage_dir,
    }
    if created_at:
        kwargs["created_at"] = created_at
    if backend == "docker":
        return DockerSandbox(**kwargs)
    if backend == "opensandbox":
        return OpenSandboxSandbox(**kwargs)
    return MockSandbox(**kwargs)


def backend_health(backend: str) -> dict[str, Any]:
    if backend == "docker":
        return docker_health()
    if backend == "opensandbox":
        return opensandbox_health()
    manifest = RUNTIME_MANIFESTS["mock"]
    return {
        "backend": "mock",
        "implementation": manifest["name"],
        "kind": manifest["kind"],
        "status": "ready",
        "message": "Mock 沙箱可用（非生产隔离）",
        "config": {"timeout_seconds": SANDBOX_EXEC_TIMEOUT, "storage_root": str(SANDBOXES_DIR)},
        "capabilities": {
            "network_isolation": False,
            "readonly_raw_volume": True,
            "resource_limits": False,
            "persistent_work_volume": True,
            "remote_execution": False,
        },
        "checks": [{"name": "sandbox.runtime", "ok": True, "detail": "python3 subprocess + tempdir"}],
    }
