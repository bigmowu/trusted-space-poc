"""水印组件的稳定边界。

算法实现仍位于 hash_slot.py；本模块把算法包装为可探测、可自检的组件，
供 API、CLI 和出域闸门复用同一实现，避免演示页面与真实导出路径各写一套。
"""

from __future__ import annotations

import csv
import hashlib
import io
import random
from datetime import datetime
from typing import Any, Callable

from app.watermark.hash_slot import (
    HEADER_SIZE,
    MAX_FRAME_BYTES,
    REPEAT,
    EmbedResult,
    ExtractResult,
    WatermarkError,
    _build_frame,
    embed_csv,
    extract_csv,
)


TraceObserver = Callable[[str, dict[str, Any], str], None]


class HashSlotWatermarkEngine:
    """CSV 哈希槽水印引擎；对外暴露算法配置、嵌入、提取与自检。"""

    component_id = "watermark-engine"
    version = "hash-slot-v1"
    provider = "HashSlotWatermarkEngine"

    def describe(self) -> dict[str, Any]:
        return {
            "algorithm": "SHA256 哈希槽 + amount LSB + 三份冗余 + CRC32 纠错",
            "version": self.version,
            "provider": self.provider,
            "capabilities": {
                "embed": True,
                "extract": True,
                "row_order_independent": True,
                "row_deletion_tolerant": True,
                "integrity_check": "CRC32",
            },
            "config": {
                "repeat": REPEAT,
                "header_bytes": HEADER_SIZE,
                "max_frame_bytes": MAX_FRAME_BYTES,
                "carrier_column": "amount",
                "slot_key": "SHA256(customer_id)[:8]",
                "bit_carrier": "amount 整数最低有效位",
            },
            "dependencies": ["metadata-db", "trace-store"],
        }

    def health(self) -> dict[str, Any]:
        checks: list[dict[str, Any]] = []
        try:
            payload = {
                "watermark_id": "WM-HEALTH-CHECK",
                "user_id": "health",
                "export_time": "20260101T000000",
                "file_id": "health",
                "request_id": "health",
            }
            frame, _, compressed = _build_frame(payload)
            crc_ok = len(frame) == HEADER_SIZE + len(compressed)
            checks.append(
                {
                    "name": "watermark.frame",
                    "ok": crc_ok,
                    "detail": {"frame_bytes": len(frame), "compressed_bytes": len(compressed)},
                }
            )
        except Exception as exc:
            checks.append({"name": "watermark.frame", "ok": False, "detail": str(exc)})
        checks.append(
            {
                "name": "watermark.csv.schema",
                "ok": True,
                "detail": "需要 customer_id 与 amount 两列，UTF-8 CSV",
            }
        )
        ready = all(item["ok"] for item in checks)
        return {
            "status": "ready" if ready else "unavailable",
            "message": "哈希槽水印引擎可用" if ready else "水印帧构造失败",
            "checks": checks,
            **self.describe(),
        }

    def embed(
        self,
        data: bytes,
        *,
        user_id: str,
        export_time: datetime,
        file_id: str,
        request_id: str,
        observer: TraceObserver | None = None,
    ) -> EmbedResult:
        return embed_csv(
            data,
            user_id=user_id,
            export_time=export_time,
            file_id=file_id,
            request_id=request_id,
            observer=observer,
        )

    def extract(self, data: bytes, observer: TraceObserver | None = None) -> ExtractResult:
        return extract_csv(data, observer=observer)

    def self_test(self, observer: TraceObserver | None = None) -> dict[str, Any]:
        """生成确定性数据并完成嵌入→提取闭环，供组件中心和 CLI 调用。"""
        # 自检载荷故意使用较长的用户号与申请号，6000 行可给三份冗余留出稳定余量。
        source = self._fixture_csv(rows=6000, seed=42)
        now = datetime.now().astimezone()
        request_id = f"SELFTEST-{now.strftime('%Y%m%dT%H%M%S')}"
        file_id = hashlib.sha256(source).hexdigest()[:12]

        embedded = self.embed(
            source,
            user_id="component-selftest",
            export_time=now,
            file_id=file_id,
            request_id=request_id,
            observer=observer,
        )
        extracted = self.extract(embedded.data, observer=observer)
        assertions = {
            "watermark_id_roundtrip": extracted.watermark_id == embedded.watermark_id,
            "user_roundtrip": extracted.payload.get("user_id") == "component-selftest",
            "request_roundtrip": extracted.payload.get("request_id") == request_id,
            "match_score": extracted.match_score >= 95.0,
            "changed_rows": embedded.changed_rows > 0,
        }
        passed = all(assertions.values())
        if not passed:
            raise WatermarkError(f"水印组件自检断言失败: {assertions}")
        return {
            "status": "success",
            "component": self.component_id,
            "provider": self.provider,
            "version": self.version,
            "input": {"rows": 6000, "bytes": len(source), "sha256": hashlib.sha256(source).hexdigest()},
            "watermark_id": embedded.watermark_id,
            "extracted_watermark_id": extracted.watermark_id,
            "match_score": extracted.match_score,
            "changed_rows": embedded.changed_rows,
            "frame_bytes": embedded.frame_bytes,
            "unknown_bits": extracted.unknown_bits,
            "corrected_bits": extracted.corrected_bits,
            "assertions": assertions,
            "algorithm": self.describe()["algorithm"],
        }

    @staticmethod
    def _fixture_csv(rows: int, seed: int) -> bytes:
        rng = random.Random(seed)
        output = io.StringIO(newline="")
        writer = csv.writer(output, lineterminator="\n")
        writer.writerow(["customer_id", "name", "phone", "city", "amount"])
        cities = ["上海", "北京", "深圳", "杭州", "成都"]
        for index in range(1, rows + 1):
            writer.writerow(
                [
                    f"C{index:06d}",
                    f"selftest_{index:04d}",
                    f"1380000{index % 10000:04d}",
                    rng.choice(cities),
                    100 + index % 900,
                ]
            )
        return output.getvalue().encode("utf-8")


watermark_engine = HashSlotWatermarkEngine()


def get_watermark_engine() -> HashSlotWatermarkEngine:
    return watermark_engine
