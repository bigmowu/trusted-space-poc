from __future__ import annotations

import asyncio
import contextlib
import io
import json

import httpx

from app.components.registry import component_detail, list_components, run_self_test
from app.db import init_db
from app.main import app
from app.observability.trace import get_trace
from app.sandbox.runtimes import DockerSandbox, OpenSandboxSandbox, build_sandbox, docker_health, opensandbox_health


def test_component_registry_exposes_backends_and_contract() -> None:
    init_db()
    result = list_components()
    components = {item["id"]: item for item in result["items"]}
    assert {"sandbox-runtime", "watermark-engine", "export-gate", "trace-store", "metadata-db"} <= set(components)
    sandbox = components["sandbox-runtime"]
    assert sandbox["active_backend"] == "mock"
    assert sandbox["self_test"]["available"] is True
    variants = {item["backend"]: item for item in sandbox["runtime_variants"]}
    assert set(variants) == {"mock", "docker", "opensandbox"}
    assert variants["mock"]["active"] is True
    assert variants["opensandbox"]["checks"]
    assert variants["opensandbox"]["manifest"]["kind"] == "remote_sandbox_sdk"
    watermark = components["watermark-engine"]
    assert watermark["status"] == "ready"
    assert watermark["config"]["repeat"] == 3
    assert watermark["config"]["carrier_column"] == "amount"


def test_watermark_component_self_test_has_algorithm_trace() -> None:
    init_db()
    result = run_self_test("watermark-engine", actor="test")
    assert result["status"] == "success"
    assert result["watermark_id"] == result["extracted_watermark_id"]
    assert result["match_score"] >= 95
    assert all(result["assertions"].values())
    trace = get_trace(result["trace_id"])
    phases = {step["phase"] for step in trace["steps"]}
    assert {
        "component.watermark.lookup",
        "watermark.payload.build",
        "watermark.frame.pack",
        "watermark.bits.repeat",
        "watermark.slots.map",
        "watermark.amount.lsb",
        "watermark.majority.vote",
        "watermark.crc.repair",
        "watermark.payload.decode",
        "component.watermark.assert",
    } <= phases
    assert trace["status"] == "success"


def test_mock_sandbox_component_self_test_creates_and_destroys() -> None:
    init_db()
    result = run_self_test("sandbox-runtime", actor="test")
    assert result["status"] == "success"
    assert result["backend"] == "mock"
    assert result["sandbox_id"].startswith("SBX-")
    assert result["marker"] in result["stdout"]
    assert result["create_trace_id"] and result["write_trace_id"] and result["exec_trace_id"] and result["destroy_trace_id"]
    exec_trace = get_trace(result["exec_trace_id"])
    assert exec_trace["status"] == "success"
    assert any(step["phase"] == "sandbox.process.exit" for step in exec_trace["steps"])
    destroy_trace = get_trace(result["destroy_trace_id"])
    assert destroy_trace["status"] == "success"
    assert component_detail("sandbox-runtime")["last_self_test"]["trace_id"] == result["trace_id"]


def test_runtime_health_never_fakes_opensandbox_ready() -> None:
    open_health = opensandbox_health()
    assert open_health["status"] in {"ready", "degraded", "unavailable"}
    assert open_health["checks"]
    if open_health["status"] != "ready":
        assert open_health.get("remediation")
    docker = docker_health()
    assert docker["status"] in {"ready", "degraded", "unavailable"}
    assert docker["checks"]
    if docker["status"] != "ready":
        assert docker.get("remediation")
    docker_sandbox = build_sandbox("SBX-TEST-DOCKER", "test", "docker")
    open_sandbox = build_sandbox("SBX-TEST-OPEN", "test", "opensandbox")
    assert isinstance(docker_sandbox, DockerSandbox) and docker_sandbox.backend == "docker"
    assert isinstance(open_sandbox, OpenSandboxSandbox) and open_sandbox.backend == "opensandbox"


def test_component_api_and_cli() -> None:
    asyncio.run(_component_api_and_cli())


async def _component_api_and_cli() -> None:
    init_db()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=60) as client:
        listed = (await client.get("/api/components")).json()
        assert listed["code"] == 0
        assert listed["data"]["summary"]["total"] >= 6
        detail = (await client.get("/api/components/watermark-engine")).json()["data"]
        assert detail["id"] == "watermark-engine"
        tested = (await client.post("/api/components/watermark-engine/self-test", json={"actor": "api-test"})).json()
        assert tested["code"] == 0
        assert tested["data"]["trace_id"]

    from app.cli import main

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert main(["components", "--json"]) == 0
    payload = json.loads(output.getvalue())
    assert any(item["id"] == "watermark-engine" for item in payload["items"])

    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert main(["component", "watermark-engine", "--selftest", "--json"]) == 0
    self_test_payload = json.loads(output.getvalue())
    assert self_test_payload["assertions"]["watermark_id_roundtrip"] is True
