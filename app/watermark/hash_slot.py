"""哈希槽水印引擎。

编码思路：
1. 业务载荷使用 JSON 序列化，压缩后放入带长度和 CRC 的固定帧；
2. 帧字节展开为比特串，完整重复 3 次；
3. 每个 customer_id 的 SHA256 前 8 位决定它承载比特串中的哪个槽；
4. 将槽值写入该行 amount 的最低有效位；
5. 提取时按同一规则回收比特，三份副本多数投票，并利用 CRC 修复少量丢行造成的未知位。

这种定位方式天然不依赖行序，删除部分行后也不会让后续行的槽位整体偏移。
"""

from __future__ import annotations

import csv
import hashlib
import io
import itertools
import json
import random
import struct
import zlib
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable


MAGIC = b"TS"
VERSION = 1
REPEAT = 3
HEADER = struct.Struct(">2sBHI")  # magic, version, compressed length, crc32(compressed)
HEADER_SIZE = HEADER.size
MAX_FRAME_BYTES = 512
MAX_UNKNOWN_BITS = 20

TraceObserver = Callable[[str, dict[str, Any], str], None]


class WatermarkError(Exception):
    pass


@dataclass
class EmbedResult:
    data: bytes
    payload: dict[str, Any]
    watermark_id: str
    frame_bytes: int
    changed_rows: int
    diagnostics: dict[str, Any]


@dataclass
class ExtractResult:
    payload: dict[str, Any]
    watermark_id: str
    match_score: float
    frame_bytes: int
    unknown_bits: int
    corrected_bits: int
    diagnostics: dict[str, Any]


def _emit(observer: TraceObserver | None, phase: str, detail: dict[str, Any], status: str = "success") -> None:
    if observer is not None:
        observer(phase, detail, status)


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _compact_time(value: datetime) -> str:
    return value.astimezone().strftime("%Y%m%dT%H%M%S")


def _make_payload(
    user_id: str,
    export_time: datetime,
    file_id: str,
    request_id: str,
) -> tuple[dict[str, Any], str]:
    """生成水印 ID。ID 摘要基于除自身外的全部业务字段，避免循环依赖。"""
    base = {
        "user_id": user_id,
        "export_time": _compact_time(export_time),
        "file_id": file_id,
        "request_id": request_id,
    }
    digest = hashlib.sha256(_json_bytes(base)).hexdigest()
    watermark_id = f"WM-{export_time.strftime('%Y%m%d')}-{digest[:8]}"
    payload = {"watermark_id": watermark_id, **base}
    return payload, watermark_id


def _build_frame(payload: dict[str, Any]) -> tuple[bytes, bytes, bytes]:
    raw = _json_bytes(payload)
    compressed = zlib.compress(raw, 9)
    if len(compressed) > 0xFFFF:
        raise WatermarkError("水印载荷过大")
    header = HEADER.pack(MAGIC, VERSION, len(compressed), zlib.crc32(compressed) & 0xFFFFFFFF)
    frame = header + compressed
    if len(frame) > MAX_FRAME_BYTES:
        raise WatermarkError("水印载荷过长，无法嵌入当前文件")
    return frame, raw, compressed


def _bytes_to_bits(data: bytes) -> list[int]:
    return [(byte >> (7 - offset)) & 1 for byte in data for offset in range(8)]


def _bits_to_bytes(bits: list[int]) -> bytes:
    result = bytearray((len(bits) + 7) // 8)
    for index, bit in enumerate(bits):
        if bit:
            result[index // 8] |= 1 << (7 - index % 8)
    return bytes(result)


def _customer_slot(customer_id: str, total_bits: int) -> int:
    digest = hashlib.sha256(customer_id.encode("utf-8")).hexdigest()
    return int(digest[:8], 16) % total_bits


def _amount_parts(value: str) -> tuple[int, int, str]:
    """返回 (缩放后的整数, 小数位数, 原始符号前缀)。"""
    text = value.strip()
    if not text:
        raise WatermarkError("amount 字段为空")
    try:
        decimal = Decimal(text)
    except InvalidOperation as exc:
        raise WatermarkError(f"amount 不是合法数值: {value!r}") from exc
    exponent = -decimal.as_tuple().exponent
    decimals = max(0, exponent)
    scale = 10**decimals
    integer = int(decimal * scale)
    return integer, decimals, text


def _amount_lsb(value: str) -> int:
    integer, _, _ = _amount_parts(value)
    return abs(integer) & 1


def _set_amount_lsb(value: str, bit: int) -> str:
    integer, decimals, text = _amount_parts(value)
    sign = -1 if integer < 0 else 1
    magnitude = abs(integer)
    adjusted = (magnitude & ~1) | (bit & 1)
    if decimals:
        formatted = f"{Decimal(adjusted) / (10**decimals):.{decimals}f}"
    else:
        formatted = str(adjusted)
    if sign < 0 and adjusted != 0:
        formatted = "-" + formatted
    # 保留正数原有的 + 号形式（虽然少见）。
    return text if formatted == text else formatted


def _read_csv(data: bytes) -> tuple[list[str], list[list[str]]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise WatermarkError("仅支持 UTF-8 编码的 CSV") from exc
    try:
        rows = list(csv.reader(io.StringIO(text, newline="")))
    except csv.Error as exc:
        raise WatermarkError(f"CSV 解析失败: {exc}") from exc
    if len(rows) < 2:
        raise WatermarkError("CSV 至少需要表头和一行数据")
    header = [cell.strip() for cell in rows[0]]
    return header, rows[1:]


def _column_index(header: list[str], name: str) -> int:
    lowered = [cell.lower() for cell in header]
    try:
        return lowered.index(name)
    except ValueError as exc:
        raise WatermarkError(f"CSV 缺少 {name} 列") from exc


def _prepare_rows(data: bytes) -> tuple[list[str], list[list[str]], list[tuple[int, int]]]:
    header, rows = _read_csv(data)
    customer_index = _column_index(header, "customer_id")
    amount_index = _column_index(header, "amount")
    prepared: list[tuple[int, int]] = []
    for row in rows:
        if len(row) <= max(customer_index, amount_index):
            raise WatermarkError("CSV 存在列数不足的数据行")
        prepared.append((customer_index, amount_index))
    return header, rows, prepared


def embed_csv(
    data: bytes,
    *,
    user_id: str,
    export_time: datetime,
    file_id: str,
    request_id: str,
    observer: TraceObserver | None = None,
) -> EmbedResult:
    """将水印写入 CSV 的 amount LSB，返回新文件内容和载荷。"""
    if not user_id.strip():
        raise WatermarkError("user_id 不能为空")
    payload, watermark_id = _make_payload(user_id.strip(), export_time, file_id, request_id)
    payload_json = _json_bytes(payload)
    _emit(
        observer,
        "watermark.payload.build",
        {
            "payload": payload,
            "json_bytes": len(payload_json),
            "json_sha256": hashlib.sha256(payload_json).hexdigest(),
            "watermark_id_rule": "WM-YYYYMMDD-SHA256(user_id,export_time,file_id,request_id)[:8]",
        },
    )
    frame, raw, compressed = _build_frame(payload)
    magic, version, compressed_len, crc = HEADER.unpack(frame[:HEADER_SIZE])
    _emit(
        observer,
        "watermark.frame.pack",
        {
            "format": "MAGIC(2) + VERSION(1) + LENGTH(2) + CRC32(4) + ZLIB_PAYLOAD",
            "magic": magic.decode("ascii"),
            "version": version,
            "raw_bytes": len(raw),
            "compressed_bytes": compressed_len,
            "header_bytes": HEADER_SIZE,
            "crc32": f"{crc:08x}",
            "frame_bytes": len(frame),
            "frame_sha256": hashlib.sha256(frame).hexdigest(),
        },
    )
    bits = _bytes_to_bits(frame) * REPEAT
    total_bits = len(bits)
    _emit(
        observer,
        "watermark.bits.repeat",
        {
            "frame_bits": len(frame) * 8,
            "repeat": REPEAT,
            "total_slots": total_bits,
            "slot_rule": "sha256(customer_id)[:8] as int % total_slots",
            "sample_bits": bits[:64],
        },
    )

    header, rows, prepared = _prepare_rows(data)
    customer_index = _column_index(header, "customer_id")
    amount_index = _column_index(header, "amount")
    changed = 0
    occupied_slots: set[int] = set()
    collisions = 0
    slot_samples: list[dict[str, Any]] = []
    lsb_samples: list[dict[str, Any]] = []

    for row, _ in zip(rows, prepared):
        customer_id = row[customer_index].strip()
        if not customer_id:
            raise WatermarkError("customer_id 不能为空")
        digest = hashlib.sha256(customer_id.encode("utf-8")).hexdigest()
        digest_prefix = int(digest[:8], 16)
        slot = digest_prefix % total_bits
        if slot in occupied_slots:
            collisions += 1
        occupied_slots.add(slot)
        if len(slot_samples) < 5:
            slot_samples.append(
                {
                    "customer_id": customer_id,
                    "sha256_prefix_8hex": digest[:8],
                    "slot": slot,
                    "bit": bits[slot],
                }
            )
        target_bit = bits[slot]
        before = row[amount_index]
        embedded = _set_amount_lsb(before, target_bit)
        if embedded != row[amount_index]:
            changed += 1
            row[amount_index] = embedded
            if len(lsb_samples) < 5:
                lsb_samples.append({"customer_id": customer_id, "before": before, "after": embedded, "bit": target_bit})

    _emit(
        observer,
        "watermark.slots.map",
        {
            "rows": len(rows),
            "total_slots": total_bits,
            "occupied_slots": len(occupied_slots),
            "uncovered_slots": total_bits - len(occupied_slots),
            "slot_collisions": collisions,
            "samples": slot_samples,
        },
    )
    _emit(
        observer,
        "watermark.amount.lsb",
        {
            "rule": "偶数 amount 表示 bit=0，奇数 amount 表示 bit=1；只修改整数最低位",
            "rows": len(rows),
            "changed_rows": changed,
            "unchanged_rows": len(rows) - changed,
            "max_numeric_delta": 1,
            "samples": lsb_samples,
        },
    )

    output = io.StringIO(newline="")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(header)
    writer.writerows(rows)
    output_data = output.getvalue().encode("utf-8")
    diagnostics = {
        "payload": payload,
        "frame_bytes": len(frame),
        "frame_bits": len(frame) * 8,
        "repeat": REPEAT,
        "total_slots": total_bits,
        "rows": len(rows),
        "occupied_slots": len(occupied_slots),
        "uncovered_slots": total_bits - len(occupied_slots),
        "changed_rows": changed,
        "output_bytes": len(output_data),
        "output_sha256": hashlib.sha256(output_data).hexdigest(),
    }
    _emit(observer, "watermark.csv.serialize", diagnostics)
    return EmbedResult(
        data=output_data,
        payload=payload,
        watermark_id=watermark_id,
        frame_bytes=len(frame),
        changed_rows=changed,
        diagnostics=diagnostics,
    )


def build_embedding_evidence(
    original: bytes,
    embedded: EmbedResult,
    *,
    sample_limit: int = 12,
) -> dict[str, Any]:
    """对比原始/导出 CSV，生成可直接展示的水印写入证据。

    这里不是重新解释算法，而是逐行复算哈希槽并核对导出文件的 amount LSB，
    同时保留少量真实改动行供页面或 CLI 展示。
    """
    original_header, original_rows = _read_csv(original)
    output_header, output_rows = _read_csv(embedded.data)
    if original_header != output_header:
        raise WatermarkError("水印前后 CSV 表头不一致")
    if len(original_rows) != len(output_rows):
        raise WatermarkError("水印前后 CSV 行数不一致")

    customer_index = _column_index(original_header, "customer_id")
    amount_index = _column_index(original_header, "amount")
    frame, _, _ = _build_frame(embedded.payload)
    magic, version, compressed_len, crc = HEADER.unpack(frame[:HEADER_SIZE])
    frame_bits = len(frame) * 8
    total_bits = frame_bits * REPEAT
    bits = _bytes_to_bits(frame) * REPEAT

    changed_rows = 0
    unchanged_rows = 0
    max_delta = Decimal(0)
    changed_samples: list[dict[str, Any]] = []
    unchanged_samples: list[dict[str, Any]] = []
    for row_number, (before_row, after_row) in enumerate(zip(original_rows, output_rows), start=2):
        if len(before_row) <= max(customer_index, amount_index) or len(after_row) <= max(customer_index, amount_index):
            raise WatermarkError(f"CSV 第 {row_number} 行列数不足")
        customer_id = after_row[customer_index].strip()
        if not customer_id or before_row[customer_index].strip() != customer_id:
            raise WatermarkError(f"CSV 第 {row_number} 行 customer_id 在水印前后发生变化")
        amount_before = before_row[amount_index].strip()
        amount_after = after_row[amount_index].strip()
        digest = hashlib.sha256(customer_id.encode("utf-8")).hexdigest()
        slot = _customer_slot(customer_id, total_bits)
        target_bit = bits[slot]
        lsb_before = _amount_lsb(amount_before)
        lsb_after = _amount_lsb(amount_after)
        if lsb_after != target_bit:
            raise WatermarkError(f"CSV 第 {row_number} 行 amount LSB 与目标槽位不一致")
        try:
            delta = abs(Decimal(amount_after) - Decimal(amount_before))
        except InvalidOperation as exc:
            raise WatermarkError(f"CSV 第 {row_number} 行 amount 不是合法数值") from exc
        max_delta = max(max_delta, delta)
        sample = {
            "row_number": row_number,
            "customer_id": customer_id,
            "sha256_prefix": digest[:8],
            "slot": slot,
            "replica": slot // frame_bits,
            "frame_bit": slot % frame_bits,
            "target_bit": target_bit,
            "lsb_before": lsb_before,
            "lsb_after": lsb_after,
            "amount_before": amount_before,
            "amount_after": amount_after,
            "delta": str(delta),
        }
        if amount_before != amount_after:
            changed_rows += 1
            if len(changed_samples) < sample_limit:
                changed_samples.append(sample)
        else:
            unchanged_rows += 1
            if len(unchanged_samples) < 3:
                unchanged_samples.append(sample)

    if changed_rows != embedded.changed_rows:
        raise WatermarkError(
            f"水印改动行数校验失败: replay={changed_rows} embed={embedded.changed_rows}"
        )
    return {
        "available": True,
        "watermark_id": embedded.watermark_id,
        "algorithm": {
            "version": "hash-slot-v1",
            "method": "sha256_hash_slot+amount_lsb+3x_repeat+crc32",
            "steps": [
                "业务载荷 JSON → zlib 压缩 → 9 字节头（MAGIC/VERSION/LENGTH/CRC32）",
                "帧字节展开为比特串，完整复制 3 份",
                "customer_id 的 SHA256 前 8 位对总槽位取模，决定承载槽位",
                "槽位 bit 写入对应行 amount 整数最低位；偶数=0，奇数=1",
            ],
            "carrier_column": "amount",
            "slot_rule": "int(sha256(customer_id)[:8],16) % total_slots",
        },
        "frame": {
            "format": "MAGIC(2B)+VERSION(1B)+LENGTH(2B)+CRC32(4B)+ZLIB_PAYLOAD",
            "magic": magic.decode("ascii"),
            "version": version,
            "compressed_bytes": compressed_len,
            "frame_bytes": len(frame),
            "frame_bits": frame_bits,
            "repeat": REPEAT,
            "total_slots": total_bits,
            "crc32": f"{crc:08x}",
            "sha256": hashlib.sha256(frame).hexdigest(),
        },
        "payload": embedded.payload,
        "files": {
            "source": {
                "bytes": len(original),
                "sha256": hashlib.sha256(original).hexdigest(),
            },
            "output": {
                "bytes": len(embedded.data),
                "sha256": hashlib.sha256(embedded.data).hexdigest(),
            },
        },
        "diff": {
            "rows_total": len(original_rows),
            "changed_rows": changed_rows,
            "unchanged_rows": unchanged_rows,
            "changed_ratio": round(changed_rows / len(original_rows) * 100, 2) if original_rows else 0,
            "max_amount_delta": str(max_delta),
            "changed_samples": changed_samples,
            "unchanged_samples": unchanged_samples,
        },
    }


def _try_decode_frame(candidate: bytes) -> dict[str, Any] | None:
    if len(candidate) < HEADER_SIZE:
        return None
    magic, version, compressed_len, expected_crc = HEADER.unpack(candidate[:HEADER_SIZE])
    if magic != MAGIC or version != VERSION:
        return None
    compressed = candidate[HEADER_SIZE:]
    if compressed_len != len(compressed):
        return None
    if (zlib.crc32(compressed) & 0xFFFFFFFF) != expected_crc:
        return None
    try:
        raw = zlib.decompress(compressed)
        payload = json.loads(raw.decode("utf-8"))
    except (zlib.error, UnicodeDecodeError, json.JSONDecodeError):
        return None
    required = {"watermark_id", "user_id", "export_time", "file_id", "request_id"}
    return payload if required.issubset(payload) else None


def _extract_for_length(
    hashes: list[int],
    parities: list[int],
    frame_len: int,
) -> tuple[dict[str, Any], int, int, dict[str, Any]] | None:
    message_bits = frame_len * 8
    total_bits = message_bits * REPEAT
    slots: list[int | None] = [None] * total_bits
    for digest, parity in zip(hashes, parities):
        slots[digest % total_bits] = parity

    message: list[int] = []
    unknown_positions: list[int] = []
    known_count = 0
    for index in range(message_bits):
        copies = (slots[index], slots[index + message_bits], slots[index + 2 * message_bits])
        values = [value for value in copies if value is not None]
        if not values:
            message.append(0)
            unknown_positions.append(index)
            continue
        known_count += 1
        message.append(1 if sum(values) * 2 >= len(values) else 0)

    if len(unknown_positions) > MAX_UNKNOWN_BITS:
        return None

    # 先用已确定的魔数位排除绝大多数错误候选，减少组合搜索。
    magic_bits = _bytes_to_bits(MAGIC)
    for bit_index in range(len(magic_bits)):
        if bit_index not in unknown_positions and message[bit_index] != magic_bits[bit_index]:
            return None

    base = bytearray(frame_len)
    for index, bit in enumerate(message):
        if bit:
            base[index // 8] |= 1 << (7 - index % 8)

    found: dict[str, Any] | None = None
    corrected = 0
    for assignment in itertools.product((0, 1), repeat=len(unknown_positions)):
        candidate = bytearray(base)
        for bit_index, bit in zip(unknown_positions, assignment):
            if bit:
                candidate[bit_index // 8] |= 1 << (7 - bit_index % 8)
            else:
                candidate[bit_index // 8] &= ~(1 << (7 - bit_index % 8))
        payload = _try_decode_frame(bytes(candidate))
        if payload is not None:
            found = payload
            corrected = len(unknown_positions)
            break

    if found is None:
        return None
    score = 100.0 * known_count / message_bits
    return (
        found,
        score,
        corrected,
        {
            "frame_bytes": frame_len,
            "message_bits": message_bits,
            "total_slots": total_bits,
            "recovered_slots": sum(value is not None for value in slots),
            "known_bit_positions": known_count,
            "unknown_bit_positions": len(unknown_positions),
            "unknown_positions": unknown_positions[:MAX_UNKNOWN_BITS],
            "crc_candidates": 2 ** len(unknown_positions),
            "corrected_bits": corrected,
            "match_score": round(score, 2),
        },
    )


def extract_csv(data: bytes, observer: TraceObserver | None = None) -> ExtractResult:
    """从 CSV 中提取水印；自动搜索水印帧长度，兼容删行和乱序。"""
    _emit(
        observer,
        "watermark.input.read",
        {"bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()},
    )
    header, rows = _read_csv(data)
    customer_index = _column_index(header, "customer_id")
    amount_index = _column_index(header, "amount")

    hashes: list[int] = []
    parities: list[int] = []
    skipped_rows = 0
    for row in rows:
        if len(row) <= max(customer_index, amount_index):
            skipped_rows += 1
            continue
        customer_id = row[customer_index].strip()
        if not customer_id:
            skipped_rows += 1
            continue
        digest = hashlib.sha256(customer_id.encode("utf-8")).hexdigest()
        hashes.append(int(digest[:8], 16))
        parities.append(_amount_lsb(row[amount_index]))

    if len(hashes) < 32:
        _emit(observer, "watermark.csv.parse", {"rows": len(rows), "valid_rows": len(hashes)}, "failed")
        raise WatermarkError("有效数据行过少，无法提取水印")
    _emit(
        observer,
        "watermark.csv.parse",
        {
            "header": header,
            "rows": len(rows),
            "valid_rows": len(hashes),
            "skipped_rows": skipped_rows,
            "customer_id_column": customer_index,
            "amount_column": amount_index,
        },
    )
    _emit(
        observer,
        "watermark.bits.collect",
        {
            "collected_bits": len(parities),
            "odd_amount_bits": sum(parities),
            "even_amount_bits": len(parities) - sum(parities),
            "rule": "amount 整数最低位即该行承载的 bit",
        },
    )

    # 每个帧字节占 24 个槽（8 位 × 3 份）。长度上限结合总行数限制，
    # 避免无意义地搜索一个必然无法覆盖的帧。
    max_frame = min(MAX_FRAME_BYTES, max(HEADER_SIZE + 1, len(hashes) // 6))
    attempts = 0
    for frame_len in range(HEADER_SIZE + 1, max_frame + 1):
        attempts += 1
        result = _extract_for_length(hashes, parities, frame_len)
        if result is not None:
            payload, score, corrected, diagnostics = result
            _emit(
                observer,
                "watermark.frame.search",
                {
                    "search_range": [HEADER_SIZE + 1, max_frame],
                    "candidates_checked": attempts,
                    "selected_frame_bytes": frame_len,
                    "selection_rule": "首长度通过 MAGIC + 长度 + CRC32 + JSON 载荷校验",
                },
            )
            _emit(observer, "watermark.majority.vote", diagnostics)
            _emit(
                observer,
                "watermark.crc.repair",
                {
                    "unknown_bits": diagnostics["unknown_positions"],
                    "enumerated_candidates": diagnostics["crc_candidates"],
                    "corrected_bits": diagnostics["corrected_bits"],
                    "crc32_verified": True,
                },
            )
            _emit(
                observer,
                "watermark.payload.decode",
                {
                    "payload": payload,
                    "watermark_id": payload["watermark_id"],
                    "match_score": round(score, 2),
                },
            )
            return ExtractResult(
                payload=payload,
                watermark_id=str(payload["watermark_id"]),
                match_score=round(score, 2),
                frame_bytes=frame_len,
                unknown_bits=corrected,
                corrected_bits=corrected,
                diagnostics=diagnostics,
            )
    _emit(
        observer,
        "watermark.frame.search",
        {"search_range": [HEADER_SIZE + 1, max_frame], "candidates_checked": attempts},
        "failed",
    )
    raise WatermarkError("未检测到有效水印，文件可能被篡改或并非本系统导出")


def add_random_customer_ids(rows: int, seed: int = 7) -> list[str]:
    """测试/演示辅助：生成稳定且可复现的客户编号。"""
    rng = random.Random(seed)
    return [f"C{index:06d}{rng.randrange(1000):03d}" for index in range(rows)]

