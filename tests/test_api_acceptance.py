from __future__ import annotations

import asyncio
import random
import shutil

import httpx

from app.config import SANDBOXES_DIR
from app.db import init_db
from app.main import app
from app.sandbox.manager import DEFAULT_CODE, get_sandbox_manager


def test_acceptance_end_to_end() -> None:
    asyncio.run(_acceptance_end_to_end())


async def _acceptance_end_to_end() -> None:
    init_db()
    get_sandbox_manager().seed_demo()
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test", timeout=30) as client:
        created = (await client.post("/api/sandbox", json={"task_id": "pytest"})).json()
        assert created["code"] == 0
        sandbox_id = created["data"]["id"]

        observation = (await client.get(f"/api/sandbox/{sandbox_id}/status")).json()["data"]
        assert observation["verdict"] == "RUNNING_VERIFIED"
        assert observation["probe_scope"] == "service_process"
        assert observation["checks_passed"] == 5
        assert all(check["ok"] is True for check in observation["checks"])
        assert observation["runtime"]["mode"] == "on_demand"
        assert observation["storage"]["work_exists"] is True

        all_observations = (await client.get("/api/sandbox/status")).json()["data"]
        assert all_observations["running_verified"] >= 1
        assert any(item["sandbox_id"] == sandbox_id for item in all_observations["items"])

        backend_logs = (await client.get("/api/system/logs?limit=20")).json()["data"]
        assert backend_logs["source"] == "events"
        assert backend_logs["path"].endswith("events.log")
        assert any("event=sandbox.created" in line for line in backend_logs["items"])

        executed = (await client.post(f"/api/sandbox/{sandbox_id}/exec", json={"code": DEFAULT_CODE})).json()
        assert executed["code"] == 0 and executed["data"]["exit_code"] == 0
        files = (await client.get(f"/api/sandbox/{sandbox_id}/files")).json()["data"]["files"]
        assert any(item["path"] == "/mnt/work/customer.csv" for item in files)

        blocked = await client.post(
            f"/api/sandbox/{sandbox_id}/exec",
            json={"code": 'open("/mnt/raw/customer.csv", "w").write("blocked")'},
        )
        assert blocked.status_code == 403
        assert blocked.json()["code"] == 1002
        audits = (await client.get("/api/audit/logs?action=raw_write_blocked")).json()["data"]["items"]
        assert audits and audits[0]["result"] == "blocked"

        fast = (await client.post(
            "/api/export/request",
            json={
                "sandbox_id": sandbox_id,
                "file_path": "/mnt/work/customer.csv",
                "applicant": "alice",
                "purpose": "联合分析",
                "receiver": "合作方A",
                "data_level": "L1",
            },
        )).json()["data"]
        assert fast["channel"] == "fast" and fast["status"] == "approved"
        exported = (await client.post(f"/api/export/{fast['id']}/execute", json={"actor": "alice"})).json()["data"]
        watermark_id = exported["watermark_id"]
        assert exported["filename"].startswith(watermark_id)

        evidence = (await client.get(f"/api/watermark/{watermark_id}/evidence")).json()["data"]
        assert evidence["available"] is True
        assert evidence["algorithm"]["carrier_column"] == "amount"
        assert evidence["frame"]["repeat"] == 3
        assert evidence["diff"]["changed_rows"] > 0
        assert evidence["diff"]["changed_samples"]
        assert evidence["diff"]["max_amount_delta"] == "1"
        assert all(item["lsb_after"] == item["target_bit"] for item in evidence["diff"]["changed_samples"])
        assert evidence["verification"]["watermark_id_match"] is True
        assert evidence["verification"]["payload_match"] is True
        assert evidence["verification"]["match_score"] >= 95
        assert evidence["files"]["source"]["sha256"] != evidence["files"]["output"]["sha256"]

        download = await client.get(f"/api/watermark/{watermark_id}/download")
        assert download.status_code == 200
        raw = download.content
        extracted = (await client.post("/api/trace/file", files={"file": ("leak.csv", raw, "text/csv")})).json()
        assert extracted["code"] == 0
        assert extracted["data"]["trace"]["mapping"]["export_user"] == "alice"

        lines = raw.decode("utf-8").splitlines()
        kept = [lines[0], *[row for index, row in enumerate(lines[1:]) if index % 10 != 0]]
        random.Random(11).shuffle(kept[1:])
        damaged = ("\n".join(kept) + "\n").encode("utf-8")
        damaged_result = (await client.post(
            "/api/trace/file",
            files={"file": ("damaged.csv", damaged, "text/csv")},
        )).json()
        assert damaged_result["code"] == 0
        assert damaged_result["data"]["trace"]["watermark_id"] == watermark_id
        assert damaged_result["data"]["match_score"] >= 95

        pending = (await client.post(
            "/api/export/request",
            json={
                "sandbox_id": sandbox_id,
                "file_path": "/mnt/work/customer.csv",
                "applicant": "bob",
                "purpose": "风控建模",
                "receiver": "合作方B",
                "data_level": "L3",
            },
        )).json()["data"]
        assert pending["channel"] == "approval" and pending["status"] == "pending"
        for approver, expected_stage in (("owner", 1), ("security", 2), ("compliance", 3)):
            approved = (await client.post(
                f"/api/export/{pending['id']}/approve",
                json={"approver": approver, "decision": "approve", "comment": "核验通过"},
            )).json()["data"]
            assert approved["approval_stage"] == expected_stage
        assert approved["status"] == "approved"

        slow_export = (await client.post(
            f"/api/export/{pending['id']}/execute",
            json={"actor": "compliance"},
        )).json()["data"]
        trace = (await client.get(f"/api/trace/{slow_export['watermark_id']}")).json()["data"]
        assert len(trace["timeline"]) == 7
        assert trace["mapping"]["approval_chain"] == "Owner → 安全 → 合规"

        destroyed = await client.delete(f"/api/sandbox/{sandbox_id}")
        assert destroyed.status_code == 200
        after_destroy = (await client.get(f"/api/sandbox/{sandbox_id}/status")).json()["data"]
        assert after_destroy["verdict"] == "NOT_RUNNING"
        assert after_destroy["checks_passed"] == 1
        assert after_destroy["runtime"]["live"] is False
        assert after_destroy["storage"]["exists"] is False

        orphan_id = "SBX-ORPHAN-TEST"
        orphan_dir = SANDBOXES_DIR / orphan_id
        (orphan_dir / "raw").mkdir(parents=True)
        (orphan_dir / "work").mkdir(parents=True)
        try:
            orphan = (await client.get(f"/api/sandbox/{orphan_id}/status")).json()["data"]
            assert orphan["verdict"] == "ORPHANED_STORAGE"
            assert orphan["memory_registered"] is False
            assert orphan["database"] is None
            all_observations = (await client.get("/api/sandbox/status")).json()["data"]
            assert any(item["sandbox_id"] == orphan_id and item["verdict"] == "ORPHANED_STORAGE" for item in all_observations["items"])
        finally:
            shutil.rmtree(orphan_dir, ignore_errors=True)

        final_logs = (await client.get("/api/system/logs?limit=500&source=events")).json()["data"]
        event_text = "\n".join(final_logs["items"])
        for expected in (
            "event=sandbox.initialized",
            "event=sandbox.created",
            "event=sandbox.exec.started",
            "event=sandbox.exec.completed",
            "event=watermark.embedding",
            "event=watermark.embedded",
            "verified=true",
            "event=watermark.extracted",
            "event=export.requested",
            "event=export.approved",
            "event=export.completed",
            "event=sandbox.destroyed",
        ):
            assert expected in event_text
        assert "| INFO |" in event_text
        assert "trace.step" not in event_text

        http_logs = (await client.get("/api/system/logs?limit=500&source=http")).json()["data"]
        assert http_logs["path"].endswith("http.log")
        assert any("/api/sandbox" in line for line in http_logs["items"])
        assert any("/api/export" in line for line in http_logs["items"])

        detail_logs = (await client.get("/api/system/logs?limit=500&source=trace")).json()["data"]
        assert detail_logs["path"].endswith("trace.log")
        assert any("trace.step" in line for line in detail_logs["items"])

        traces = (await client.get("/api/traces?limit=100")).json()["data"]["items"]
        assert any(item["operation"] == "sandbox.exec" for item in traces)
        assert any(item["operation"] == "export.execute" for item in traces)
        assert any(item["operation"] == "watermark.extract" for item in traces)

