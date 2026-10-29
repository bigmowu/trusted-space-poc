"""可信数据空间本地运维 CLI。

示例：
    python -m app.cli traces --operation sandbox.exec --limit 10
    python -m app.cli trace --last --operation sandbox.seed
    python -m app.cli trace TRC-20261028-xxxxxxxxxx
    python -m app.cli watermark WM-20261028-xxxxxxxx
    python -m app.cli logs -f                      # 业务事件
    python -m app.cli logs --source trace -f       # 执行细节
    python -m app.cli logs --source http -f        # HTTP 访问
    python -m app.cli sandboxes                    # 沙箱存在性联合观测
    python -m app.cli sandbox sbx-demo             # 单个沙箱的运行事实
    python -m app.cli components
    python -m app.cli component sandbox-runtime --selftest
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

from app.common import BizError
from app.db import init_db
from app.components.registry import component_detail, list_components, run_self_test
from app.logging_config import LOG_FILES
from app.observability.format import format_trace, format_trace_row
from app.observability.trace import get_trace, list_traces, latest_trace_id
from app.sandbox.manager import get_sandbox_manager
from app.trace.repository import watermark_evidence


OPERATIONS = {
    "app.startup": "FastAPI 启动、数据库初始化与演示沙箱准备",
    "sandbox.seed": "启动时创建或复用 sbx-demo，挂载卷并写入原始种子数据",
    "sandbox.create": "分配 ID、初始化卷、登记数据库并进入 RUNNING",
    "sandbox.exec": "静态策略检查、物化挂载卷、执行 Python、同步工作卷、清理",
    "sandbox.write": "路径规范化、只读策略检查、写入工作卷",
    "sandbox.destroy": "销毁内存沙箱并更新数据库状态",
    "export.request": "文件存在/原始卷/数据分级检查并创建申请单",
    "export.approval": "Owner → 安全 → 合规状态机迁移",
    "export.execute": "审批校验、读取文件、水印嵌入、落盘与映射登记",
    "watermark.extract": "CSV 解析、哈希槽回收、多数投票、CRC 修复与载荷解码",
    "trace.query": "按水印 ID 构建七步溯源时间线",
    "component.sandbox.selftest": "沙箱创建→写卷→执行→回读→销毁闭环自检",
    "component.watermark.selftest": "确定性 CSV 的嵌入→提取→校验闭环自检",
}


def _print_traces(items: list[dict[str, Any]], as_json: bool) -> None:
    if as_json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return
    if not items:
        print("没有匹配的执行追踪。")
        return
    print("STARTED_AT                 STATUS    OPERATION             TARGET                              STEPS  TRACE_ID")
    for item in items:
        print(format_trace_row(item))


def _print_trace(trace: dict[str, Any], as_json: bool) -> None:
    if as_json:
        print(json.dumps(trace, ensure_ascii=False, indent=2))
    else:
        print(format_trace(trace), end="")


def _resolve_trace(args: argparse.Namespace) -> dict[str, Any] | None:
    if getattr(args, "trace_id", None):
        return get_trace(args.trace_id)
    trace_id = latest_trace_id(getattr(args, "operation", None) or None)
    return get_trace(trace_id) if trace_id else None


def command_traces(args: argparse.Namespace) -> int:
    init_db()
    seen: set[str] = set()
    first = True
    while True:
        items = list_traces(
            operation=args.operation or None,
            status=args.status or None,
            target=args.target or None,
            phase=args.phase or None,
            limit=args.limit,
        )
        fresh = [item for item in items if item["trace_id"] not in seen]
        if first or fresh:
            _print_traces(fresh if not first else items, args.json)
        seen.update(item["trace_id"] for item in items)
        first = False
        if not args.follow:
            return 0
        time.sleep(args.interval)


def command_trace(args: argparse.Namespace) -> int:
    init_db()
    trace = _resolve_trace(args)
    if trace is None:
        print("没有找到执行追踪。")
        return 2
    _print_trace(trace, args.json)
    if not args.follow:
        return 0
    seen = {trace["trace_id"]}
    while True:
        time.sleep(args.interval)
        items = list_traces(operation=args.operation or None, limit=50)
        for item in reversed(items):
            if item["trace_id"] in seen:
                continue
            full = get_trace(item["trace_id"])
            if full:
                print("\n" + "=" * 100 + "\n")
                _print_trace(full, args.json)
            seen.add(item["trace_id"])


def command_watermark(args: argparse.Namespace) -> int:
    init_db()
    evidence = watermark_evidence(args.watermark_id)
    if args.json:
        print(json.dumps(evidence, ensure_ascii=False, indent=2))
        return 0
    if not evidence.get("available"):
        print(evidence.get("message", "该水印没有写入证据档案"))
        return 2
    algorithm = evidence["algorithm"]
    frame = evidence["frame"]
    files = evidence["files"]
    diff = evidence["diff"]
    verification = evidence.get("verification", {})
    print(f"WATERMARK  {evidence['watermark_id']}")
    print(f"METHOD     {algorithm['method']}")
    print(f"CARRIER    {algorithm['carrier_column']} column; max delta={diff['max_amount_delta']}")
    print(f"FRAME      {frame['frame_bytes']} bytes / CRC32 {frame['crc32']} / repeat {frame['repeat']}x / {frame['total_slots']} slots")
    print(f"SOURCE     {files['source']['bytes']} bytes sha256={files['source']['sha256']}")
    print(f"OUTPUT     {files['output']['bytes']} bytes sha256={files['output']['sha256']}")
    print(
        f"DIFF       rows={diff['rows_total']} changed={diff['changed_rows']} "
        f"unchanged={diff['unchanged_rows']} changed_ratio={diff['changed_ratio']}%"
    )
    print(
        f"VERIFY     match={verification.get('match_score', '-')}% "
        f"unknown_bits={verification.get('unknown_bits', '-')} "
        f"corrected_bits={verification.get('corrected_bits', '-')} crc32_verified={verification.get('crc32_verified', '-')}"
    )
    print("\nCHANGED ROW SAMPLES")
    print("CUSTOMER_ID  AMOUNT_BEFORE -> AMOUNT_AFTER  DELTA  LSB  SLOT  REPLICA  HASH_PREFIX")
    for item in diff["changed_samples"]:
        print(
            f"{item['customer_id']:<12} {item['amount_before']:>13} -> {item['amount_after']:<13} "
            f"{item['delta']:>5}  {item['lsb_before']}->{item['lsb_after']}  {item['slot']:>5}  "
            f"{item['replica']:>7}  {item['sha256_prefix']}"
        )
    return 0


def _tail_lines(path: Path, count: int) -> list[str]:
    from collections import deque

    with path.open("r", encoding="utf-8", errors="replace") as handle:
        return [line.rstrip("\n") for line in deque(handle, maxlen=count)]


def command_logs(args: argparse.Namespace) -> int:
    path = LOG_FILES[args.source]
    if not path.exists():
        print(f"日志文件尚不存在: {path}")
        return 2
    print(f"# {args.source} | {path}")
    for line in _tail_lines(path, args.lines):
        print(line)
    if not args.follow:
        return 0
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        handle.seek(0, 2)
        try:
            while True:
                line = handle.readline()
                if line:
                    print(line, end="")
                    continue
                time.sleep(0.5)
        except KeyboardInterrupt:
            return 0


def command_operations(_: argparse.Namespace) -> int:
    for operation, description in OPERATIONS.items():
        print(f"{operation:<22} {description}")
    return 0


def _check_state(item: dict[str, Any], name: str) -> str:
    for check in item.get("checks", []):
        if check.get("name") == name:
            if check.get("skipped"):
                return "N/A"
            return "OK" if check.get("ok") else "FAIL"
    return "FAIL"


def command_sandboxes(args: argparse.Namespace) -> int:
    init_db()
    items = get_sandbox_manager().observe_all(service_local=False)
    if args.running:
        items = [item for item in items if item["verdict"] == "RUNNING_VERIFIED"]
    if args.json:
        print(json.dumps(items, ensure_ascii=False, indent=2))
        return 0
    if not items:
        print("没有匹配的沙箱。")
        return 0
    print(
        "SANDBOX_ID                 VERDICT             BACKEND   CHECKS  DB  MEM  RUNTIME  STORAGE  LAST_ACTIVITY"
    )
    for item in items:
        database = item.get("database") or {}
        last = item.get("last_activity") or {}
        print(
            f"{item['sandbox_id']:<26} {item['verdict']:<19} "
            f"{database.get('backend', item.get('runtime', {}).get('mode', '-')):<9} "
            f"{item['checks_passed']}/{len(item['checks']):<6} "
            f"{_check_state(item, 'database.record'):<3} {_check_state(item, 'process.registry'):<4} "
            f"{_check_state(item, 'runtime.live'):<8} {_check_state(item, 'storage.volumes'):<8} "
            f"{last.get('created_at', '-')}"
        )
    return 0


def command_sandbox(args: argparse.Namespace) -> int:
    init_db()
    try:
        item = get_sandbox_manager().observe(args.sandbox_id, service_local=False)
    except BizError as exc:
        print(f"观测失败: {exc.message}")
        return 2
    if args.json:
        print(json.dumps(item, ensure_ascii=False, indent=2))
        return 0
    runtime = item.get("runtime", {})
    storage = item.get("storage", {})
    database = item.get("database") or {}
    last = item.get("last_activity") or {}
    print(f"SANDBOX    {item['sandbox_id']}")
    print(f"PROBE      {item['probe_scope']} · CLI 只核对数据库与卷；运行时以 GET /api/sandbox/{item['sandbox_id']}/status 为准")
    print(f"VERDICT    {item['verdict']} · {item['verdict_label']}")
    print(f"CHECKS     {item['checks_passed']}/{len(item['checks'])}")
    for check in item["checks"]:
        state = "N/A" if check.get("skipped") else ("OK" if check.get("ok") else "FAIL")
        print(f"  [{state:<4}] {check['name']:<20} {check.get('detail', '-')}")
    print(f"DATABASE   status={database.get('status', '-')} backend={database.get('backend', '-')} created={database.get('created_at', '-')}")
    print(
        f"RUNTIME    mode={runtime.get('mode', '-')} live={runtime.get('live', False)} "
        f"long_lived_process={runtime.get('long_lived_process', False)}"
    )
    print(f"           {runtime.get('detail', '-')}")
    print(
        f"STORAGE    exists={storage.get('exists', False)} raw={storage.get('raw_files', 0)} files "
        f"work={storage.get('work_files', 0)} files bytes={storage.get('total_bytes', 0)}"
    )
    print(f"           {storage.get('path', '-')}")
    print(f"LAST       {last.get('action', '-')} {last.get('result', '-')} {last.get('created_at', '-')}")
    return 0


def _print_component_detail(item: dict[str, Any]) -> None:
    print(f"{item['name']} ({item['id']})")
    print(f"  status      : {item['status']}")
    print(f"  provider    : {item.get('provider', '-')}")
    print(f"  version     : {item.get('version', '-')}")
    print(f"  description : {item.get('description', '-')}")
    if item.get("active_backend"):
        print(f"  active      : {item['active_backend']}")
    print(f"  dependencies: {', '.join(item.get('dependencies', [])) or '-'}")
    print("  checks:")
    for check in item.get("checks", []):
        print(f"    [{'OK' if check.get('ok') else 'FAIL'}] {check.get('name')}: {check.get('detail')}")
    print("  config:")
    print(json.dumps(item.get("config", {}), ensure_ascii=False, indent=4))
    if item.get("capabilities"):
        print("  capabilities:")
        print(json.dumps(item["capabilities"], ensure_ascii=False, indent=4))
    if item.get("runtime_variants"):
        print("  runtime variants:")
        for variant in item["runtime_variants"]:
            active = " *active" if variant.get("active") else ""
            print(f"    {variant.get('backend'):<12} {variant.get('status'):<12}{active} {variant.get('message', '')}")
    if item.get("remediation"):
        print(f"  remediation : {item['remediation']}")
    if item.get("last_self_test"):
        last = item["last_self_test"]
        print(f"  last selftest: {last['status']} {last['trace_id']}")
    if item.get("self_test", {}).get("available"):
        print(f"  selftest    : {item['self_test']['command']}")


def command_components(args: argparse.Namespace) -> int:
    init_db()
    result = list_components()
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    summary = result["summary"]
    print(
        f"组件 {summary['total']} 个 · ready={summary['ready']} degraded={summary['degraded']} unavailable={summary['unavailable']} · "
        f"active_sandbox={result['active_sandbox_backend']}"
    )
    print(f"{'ID':<22} {'STATUS':<13} {'PROVIDER':<26} DESCRIPTION")
    for item in result["items"]:
        print(f"{item['id']:<22} {item['status']:<13} {item.get('provider', '-'):<26} {item.get('message', '')}")
    return 0


def command_component(args: argparse.Namespace) -> int:
    init_db()
    if args.selftest:
        try:
            result = run_self_test(args.component_id, actor="cli")
        except BizError as exc:
            trace_id = getattr(exc, "trace_id", None)
            print(f"自检失败: {exc.message}")
            if trace_id:
                print(f"失败链路: {trace_id}")
                print(f"查看详情: python -m app.cli trace {trace_id}")
            return 1
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(f"自检成功: {result['component']} · trace={result.get('trace_id')}")
            for key in ("backend", "watermark_id", "extracted_watermark_id", "match_score", "changed_rows", "frame_bytes", "sandbox_id", "stdout"):
                if key in result:
                    print(f"  {key}: {result[key]}")
            nested = {key: value for key, value in result.items() if key.endswith("_trace_id")}
            if nested:
                print("  nested traces:")
                for key, value in nested.items():
                    print(f"    {key}: {value}")
            if result.get("assertions"):
                print(f"  assertions: {json.dumps(result['assertions'], ensure_ascii=False)}")
            print(f"  查看完整链路: python -m app.cli trace {result.get('trace_id')}")
        return 0
    item = component_detail(args.component_id)
    if args.json:
        print(json.dumps(item, ensure_ascii=False, indent=2))
    else:
        _print_component_detail(item)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="可信数据空间执行追踪 CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    traces = subparsers.add_parser("traces", help="列出执行追踪")
    traces.add_argument("--operation", default="", help="按操作名精确筛选")
    traces.add_argument("--status", default="", choices=["", "running", "success", "failed", "blocked"])
    traces.add_argument("--target", default="", help="按沙箱/申请单/水印 ID 筛选")
    traces.add_argument("--phase", default="", help="筛选包含指定阶段的 trace")
    traces.add_argument("--limit", type=int, default=30)
    traces.add_argument("--json", action="store_true")
    traces.add_argument("--follow", action="store_true", help="持续等待新追踪")
    traces.add_argument("--interval", type=float, default=1.0)
    traces.set_defaults(func=command_traces)

    trace = subparsers.add_parser("trace", help="查看单条追踪及全部阶段")
    trace.add_argument("trace_id", nargs="?")
    trace.add_argument("--last", action="store_true", help="查看最近一条追踪")
    trace.add_argument("--operation", default="", help="--last 时按操作名筛选")
    trace.add_argument("--json", action="store_true")
    trace.add_argument("--follow", action="store_true", help="持续打印后续新追踪")
    trace.add_argument("--interval", type=float, default=1.0)
    trace.set_defaults(func=command_trace)

    watermark = subparsers.add_parser("watermark", help="查看水印写入证据与逐行差异")
    watermark.add_argument("watermark_id")
    watermark.add_argument("--json", action="store_true")
    watermark.set_defaults(func=command_watermark)

    logs = subparsers.add_parser("logs", help="查看业务事件、执行细节或 HTTP 日志")
    logs.add_argument("--source", choices=tuple(LOG_FILES), default="events", help="默认 events")
    logs.add_argument("-n", "--lines", type=int, default=80)
    logs.add_argument("-f", "--follow", action="store_true")
    logs.set_defaults(func=command_logs)

    operations = subparsers.add_parser("operations", help="列出可追踪的业务操作")
    operations.set_defaults(func=command_operations)

    sandboxes = subparsers.add_parser("sandboxes", help="联合核对所有沙箱的记录、运行时与卷")
    sandboxes.add_argument("--running", action="store_true", help="只显示五项检查全部通过的运行沙箱")
    sandboxes.add_argument("--json", action="store_true")
    sandboxes.set_defaults(func=command_sandboxes)

    sandbox = subparsers.add_parser("sandbox", help="查看单个沙箱是否存在及是否可执行")
    sandbox.add_argument("sandbox_id")
    sandbox.add_argument("--json", action="store_true")
    sandbox.set_defaults(func=command_sandbox)

    components = subparsers.add_parser("components", help="列出组件状态、依赖与能力")
    components.add_argument("--json", action="store_true")
    components.set_defaults(func=command_components)

    component = subparsers.add_parser("component", help="查看单个组件或运行自检")
    component.add_argument("component_id")
    component.add_argument("--selftest", action="store_true", help="执行真实组件闭环自检")
    component.add_argument("--json", action="store_true")
    component.set_defaults(func=command_component)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
