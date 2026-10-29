"""命令行人读格式。"""

from __future__ import annotations

import json
from typing import Any


def format_trace(trace: dict[str, Any]) -> str:
    lines = [
        f"TRACE {trace['trace_id']}",
        f"operation : {trace['operation']}",
        f"status    : {trace['status']}",
        f"actor     : {trace['actor']}",
        f"target    : {trace['target']}",
        f"started   : {trace['started_at']}",
        f"finished  : {trace.get('finished_at') or '-'}",
        f"duration  : {trace.get('duration_ms', 0)} ms",
        f"input     : {json.dumps(trace.get('input', {}), ensure_ascii=False)}",
    ]
    if trace.get("error"):
        lines.append(f"error     : {json.dumps(trace['error'], ensure_ascii=False)}")
    lines.append("")
    lines.append("STEPS")
    for step in trace.get("steps", []):
        lines.append(
            f"[{step['seq']:02d}] {step['phase']} | {step['status']} | {step['duration_ms']} ms"
        )
        detail = json.dumps(step.get("detail", {}), ensure_ascii=False, indent=2)
        lines.extend(f"     {line}" for line in detail.splitlines())
        lines.append("")
    if trace.get("result") is not None:
        lines.append("RESULT")
        lines.append(json.dumps(trace["result"], ensure_ascii=False, indent=2))
    return "\n".join(lines).rstrip() + "\n"


def format_trace_row(trace: dict[str, Any]) -> str:
    return (
        f"{trace['started_at']}  {trace['status']:<8}  {trace['operation']:<20}  "
        f"{trace['target']:<32}  steps={trace.get('step_count', 0):<3}  {trace['trace_id']}"
    )
