from __future__ import annotations

import csv
import io
import random
import re
from datetime import datetime

from app.watermark.hash_slot import embed_csv, extract_csv


def make_csv(rows: int = 5000) -> bytes:
    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["customer_id", "name", "phone", "city", "amount"])
    for index in range(1, rows + 1):
        writer.writerow([f"C{index:06d}", f"客户{index}", f"1380000{index % 10000:04d}", "上海", 100 + index % 700])
    return output.getvalue().encode("utf-8")


def test_watermark_roundtrip_and_metadata() -> None:
    result = embed_csv(
        make_csv(),
        user_id="alice",
        export_time=datetime(2026, 10, 28, 10, 0, 0).astimezone(),
        file_id="abc123def456",
        request_id="EXP-20261028-000001",
    )
    assert re.fullmatch(r"WM-\d{8}-[0-9a-f]{8}", result.watermark_id)
    extracted = extract_csv(result.data)
    assert extracted.watermark_id == result.watermark_id
    assert extracted.payload["user_id"] == "alice"
    assert extracted.match_score >= 95

    original_rows = list(csv.DictReader(io.StringIO(make_csv().decode())))
    embedded_rows = list(csv.DictReader(io.StringIO(result.data.decode())))
    assert len(original_rows) == len(embedded_rows) == 5000
    for before, after in zip(original_rows, embedded_rows):
        assert before["customer_id"] == after["customer_id"]
        assert abs(float(before["amount"]) - float(after["amount"])) <= 1


def test_watermark_survives_row_deletion_and_shuffle() -> None:
    result = embed_csv(
        make_csv(),
        user_id="alice",
        export_time=datetime.now().astimezone(),
        file_id="abc123def456",
        request_id="EXP-20261028-000002",
    )
    lines = result.data.decode("utf-8").splitlines()
    header, data_rows = lines[0], lines[1:]
    kept = [row for index, row in enumerate(data_rows) if index % 10 != 0]
    random.Random(2026).shuffle(kept)
    damaged = ("\n".join([header, *kept]) + "\n").encode("utf-8")

    extracted = extract_csv(damaged)
    assert extracted.watermark_id == result.watermark_id
    assert extracted.payload["user_id"] == "alice"
    assert extracted.match_score >= 95

