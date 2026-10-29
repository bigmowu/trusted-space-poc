from __future__ import annotations

import asyncio
import contextlib
import io
import json

import httpx

from app.db import init_db
from app.main import app
from app.observability.trace import get_trace, list_traces
from app.sandbox.manager import DEFAULT_CODE, SandboxManager, get_sandbox_manager


def _step_map(trace: dict) -> dict[str, dict]:
    return {step["phase"]: step for step in trace["steps"]}


def test_trace_covers_sandbox_and_watermark_algorithm() -> None:
    asyncio.run(_trace_covers_sandbox_and_watermark_algorithm())


async def _trace_covers_sandbox_and_watermark_algorithm() -> None:
    init_db()
    get_sandbox_manager().seed_demo()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
        page = await client.get("/")
        assert page.status_code == 200 and "可信数据空间" in page.text
        frontend = await client.get("/static/app.js")
        assert frontend.status_code == 200 and "openTraceDrawer" in frontend.text

        seed_traces = (await client.get(
            "/api/traces?operation=sandbox.seed&phase=sandbox.running&limit=1"
        )).json()["data"]["items"]
        assert seed_traces
        seed_detail = (await client.get(f"/api/traces/{seed_traces[0]['trace_id']}")).json()["data"]
        seed_steps = _step_map(seed_detail)
        assert {
            "database.ready",
            "sandbox.recovery.scan",
            "sandbox.mounts.configure",
            "sandbox.storage.initialize",
            "sandbox.running",
        } <= set(seed_steps)

        created = (await client.post("/api/sandbox", json={"task_id": "trace-test"})).json()["data"]
        sandbox_id = created["id"]
        create_trace = get_trace(created["trace_id"])
        assert create_trace["status"] == "success"
        assert {"sandbox.id.allocate", "sandbox.volumes.initialize", "sandbox.storage.initialize", "sandbox.running"} <= set(_step_map(create_trace))

        executed = (await client.post(f"/api/sandbox/{sandbox_id}/exec", json={"code": DEFAULT_CODE})).json()["data"]
        exec_trace = get_trace(executed["trace_id"])
        exec_steps = _step_map(exec_trace)
        assert exec_trace["status"] == "success"
        assert {
            "sandbox.policy.scan",
            "sandbox.workspace.allocate",
            "sandbox.volumes.materialize",
            "sandbox.mount.rewrite",
            "sandbox.process.exit",
            "sandbox.volume.sync",
            "sandbox.storage.persist",
            "sandbox.workspace.cleanup",
        } <= set(exec_steps)
        assert exec_steps["sandbox.process.exit"]["detail"]["exit_code"] == 0
        assert exec_steps["sandbox.storage.persist"]["detail"]["survives_restart"] is True

        blocked = await client.post(
            f"/api/sandbox/{sandbox_id}/exec",
            json={"code": 'open("/mnt/raw/customer.csv", "w").write("blocked")'},
        )
        assert blocked.status_code == 403
        blocked_trace = get_trace(blocked.json()["trace_id"])
        assert blocked_trace["status"] == "blocked"
        assert _step_map(blocked_trace)["sandbox.policy.scan"]["detail"]["decision"] == "DENY"

        requested = (await client.post(
            "/api/export/request",
            json={
                "sandbox_id": sandbox_id,
                "file_path": "/mnt/work/customer.csv",
                "applicant": "trace-user",
                "purpose": "算法核验",
                "receiver": "audit-team",
                "data_level": "L1",
            },
        )).json()["data"]
        request_trace = get_trace(requested["trace_id"])
        assert {"export.policy.raw_volume", "export.policy.classification", "export.policy.file_exists"} <= set(_step_map(request_trace))

        exported = (await client.post(
            f"/api/export/{requested['id']}/execute",
            json={"actor": "trace-user"},
        )).json()["data"]
        export_trace = get_trace(exported["trace_id"])
        export_steps = _step_map(export_trace)
        assert export_trace["status"] == "success"
        assert {
            "watermark.payload.build",
            "watermark.frame.pack",
            "watermark.bits.repeat",
            "watermark.slots.map",
            "watermark.amount.lsb",
            "watermark.csv.serialize",
            "watermark.roundtrip.verify",
            "export.file.write",
            "database.export.persist",
        } <= set(export_steps)
        assert export_steps["watermark.payload.build"]["detail"]["payload"]["user_id"] == "trace-user"
        assert export_steps["watermark.frame.pack"]["detail"]["header_bytes"] == 9
        assert export_steps["watermark.bits.repeat"]["detail"]["repeat"] == 3
        assert export_steps["watermark.slots.map"]["detail"]["rows"] == 5000
        assert export_steps["watermark.amount.lsb"]["detail"]["changed_rows"] > 0
        assert export_steps["watermark.roundtrip.verify"]["detail"]["watermark_id_match"] is True

        evidence = (await client.get(f"/api/watermark/{exported['watermark_id']}/evidence")).json()["data"]
        assert evidence["diff"]["changed_rows"] == export_steps["watermark.amount.lsb"]["detail"]["changed_rows"]
        assert evidence["verification"]["extracted_watermark_id"] == exported["watermark_id"]

        from app.cli import main

        watermark_output = io.StringIO()
        with contextlib.redirect_stdout(watermark_output):
            assert main(["watermark", exported["watermark_id"]]) == 0
        assert "amount column; max delta=1" in watermark_output.getvalue()
        assert "CHANGED ROW SAMPLES" in watermark_output.getvalue()

        download = await client.get(f"/api/watermark/{exported['watermark_id']}/download")
        extracted = (await client.post(
            "/api/trace/file",
            files={"file": ("trace.csv", download.content, "text/csv")},
        )).json()["data"]
        extract_trace = get_trace(extracted["trace_id"])
        extract_steps = _step_map(extract_trace)
        assert {
            "watermark.input.read",
            "watermark.csv.parse",
            "watermark.bits.collect",
            "watermark.frame.search",
            "watermark.majority.vote",
            "watermark.crc.repair",
            "watermark.payload.decode",
        } <= set(extract_steps)
        assert extract_steps["watermark.payload.decode"]["detail"]["watermark_id"] == exported["watermark_id"]
        assert extract_trace["status"] == "success"


def test_mock_volumes_survive_manager_restart() -> None:
    init_db()
    manager = get_sandbox_manager()
    manager.seed_demo()
    created = manager.create("persistence-test")
    content = "customer_id,name,phone,city,amount\nC000001,持久化,13800000000,上海,100\n".encode("utf-8")
    manager.get(created["id"]).write_file("/mnt/work/persisted.csv", content, actor="test")

    recovered_manager = SandboxManager()
    recovered = recovered_manager.recover_running()
    assert any(item["sandbox_id"] == created["id"] for item in recovered)
    assert recovered_manager.get(created["id"]).read_file("/mnt/work/persisted.csv") == content


def test_cli_can_render_trace() -> None:
    from app.cli import main

    init_db()
    traces = list_traces(limit=1)
    assert traces
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        assert main(["trace", traces[0]["trace_id"], "--json"]) == 0
    payload = json.loads(output.getvalue())
    assert payload["trace_id"] == traces[0]["trace_id"]
    assert payload["steps"]
