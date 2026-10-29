#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
if [[ -z "${PYTHON:-}" && -x .venv/bin/python ]]; then
  PYTHON=.venv/bin/python
else
  PYTHON="${PYTHON:-python3}"
fi
TMP_DIR="$(mktemp -d)"
SERVER_PID=""

cleanup() {
  if [[ -n "${SERVER_PID}" ]]; then
    kill "${SERVER_PID}" >/dev/null 2>&1 || true
  fi
  rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

json_value() {
  "$PYTHON" -c 'import json,sys; data=json.load(sys.stdin); print(data'"$1"')'
}

if ! curl -sf "${BASE_URL}/api/health" >/dev/null 2>&1; then
  echo "▶ 启动本地服务 ${BASE_URL}"
  "$PYTHON" -m uvicorn app.main:app --host 127.0.0.1 --port 8000 >"${TMP_DIR}/server.log" 2>&1 &
  SERVER_PID=$!
  for _ in $(seq 1 40); do
    if curl -sf "${BASE_URL}/api/health" >/dev/null 2>&1; then break; fi
    sleep 0.25
  done
fi

echo "① 创建可信沙箱"
CREATE_RESPONSE="$(curl -sf -X POST "${BASE_URL}/api/sandbox" -H 'Content-Type: application/json' -d '{"task_id":"demo-flow"}')"
SANDBOX_ID="$(printf '%s' "${CREATE_RESPONSE}" | json_value "['data']['id']")"
CREATE_TRACE="$(printf '%s' "${CREATE_RESPONSE}" | json_value "['data']['trace_id']")"
echo "   sandbox_id=${SANDBOX_ID}"
echo "   trace_id=${CREATE_TRACE}"
echo "   预期：返回 RUNNING 状态沙箱"

cat >"${TMP_DIR}/demo_code.py" <<'PY'
import csv
import random

random.seed(42)
cities = ["上海", "北京", "深圳", "杭州", "成都"]
with open("/mnt/work/customer.csv", "w", newline="", encoding="utf-8") as file:
    writer = csv.writer(file)
    writer.writerow(["customer_id", "name", "phone", "city", "amount"])
    for index in range(1, 5001):
        writer.writerow([
            f"C{index:06d}",
            f"客户{index:04d}",
            f"1380000{index % 10000:04d}",
            random.choice(cities),
            100 + index % 900,
        ])
print("generated 5000 rows")
PY

"$PYTHON" - "${TMP_DIR}/demo_code.py" >"${TMP_DIR}/exec.json" <<'PY'
import json
import pathlib
import sys

code = pathlib.Path(sys.argv[1]).read_text(encoding="utf-8")
print(json.dumps({"code": code}, ensure_ascii=False))
PY

echo "② 在沙箱内生成 5000 行 CSV"
EXEC_RESPONSE="$(curl -sf -X POST "${BASE_URL}/api/sandbox/${SANDBOX_ID}/exec" -H 'Content-Type: application/json' --data-binary @"${TMP_DIR}/exec.json")"
printf '%s' "${EXEC_RESPONSE}" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin)["data"]; print("   exit_code=",d["exit_code"]," duration_ms=",d["duration_ms"]); print("   stdout=",d["stdout"].strip().replace("\n"," | "))'
EXEC_TRACE="$(printf '%s' "${EXEC_RESPONSE}" | json_value "['data']['trace_id']")"
echo "   trace_id=${EXEC_TRACE}"
echo "   预期：exit_code=0，工作卷出现 /mnt/work/customer.csv"

echo "③ 申请 L1 快速通道出域"
REQUEST_RESPONSE="$(curl -sf -X POST "${BASE_URL}/api/export/request" -H 'Content-Type: application/json' -d "{\"sandbox_id\":\"${SANDBOX_ID}\",\"file_path\":\"/mnt/work/customer.csv\",\"applicant\":\"alice\",\"purpose\":\"联合分析\",\"receiver\":\"合作方A\",\"data_level\":\"L1\"}")"
REQUEST_ID="$(printf '%s' "${REQUEST_RESPONSE}" | json_value "['data']['id']")"
REQUEST_STATUS="$(printf '%s' "${REQUEST_RESPONSE}" | json_value "['data']['status']")"
REQUEST_TRACE="$(printf '%s' "${REQUEST_RESPONSE}" | json_value "['data']['trace_id']")"
echo "   request_id=${REQUEST_ID} status=${REQUEST_STATUS}"
echo "   trace_id=${REQUEST_TRACE}"
echo "   预期：channel=fast，status=approved"

echo "④ 自动执行水印导出"
EXPORT_RESPONSE="$(curl -sf -X POST "${BASE_URL}/api/export/${REQUEST_ID}/execute" -H 'Content-Type: application/json' -d '{"actor":"alice"}')"
WATERMARK_ID="$(printf '%s' "${EXPORT_RESPONSE}" | json_value "['data']['watermark_id']")"
DOWNLOAD_URL="$(printf '%s' "${EXPORT_RESPONSE}" | json_value "['data']['download_url']")"
EXPORT_TRACE="$(printf '%s' "${EXPORT_RESPONSE}" | json_value "['data']['trace_id']")"
echo "   watermark_id=${WATERMARK_ID}"
echo "   trace_id=${EXPORT_TRACE}"
echo "   预期：返回 WM-YYYYMMDD-XXXXXXXX 格式水印 ID"
EVIDENCE_RESPONSE="$(curl -sf "${BASE_URL}/api/watermark/${WATERMARK_ID}/evidence")"
printf '%s' "${EVIDENCE_RESPONSE}" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin)["data"]; diff=d["diff"]; verify=d["verification"]; sample=diff["changed_samples"][0]; print("   carrier=",d["algorithm"]["carrier_column"]," changed_rows=",diff["changed_rows"]," max_delta=",diff["max_amount_delta"]); print("   sample=",sample["customer_id"],sample["amount_before"],"->",sample["amount_after"],"slot=",sample["slot"],"lsb=",sample["lsb_before"],"->",sample["lsb_after"]); print("   roundtrip_match=",verify["match_score"]," crc32_verified=",verify["crc32_verified"])'
echo "   预期：约一半行只改 amount 的 LSB，最大数值差 1；导出后回读水印 ID 一致"

echo "⑤ 下载带水印文件"
curl -sf "${BASE_URL}${DOWNLOAD_URL}" -o "${TMP_DIR}/exported_customer.csv"
echo "   local_file=${TMP_DIR}/exported_customer.csv"
echo "   bytes=$(wc -c <"${TMP_DIR}/exported_customer.csv" | tr -d ' ')"
echo "   预期：文件名带水印 ID 前缀，CSV 可正常打开"

echo "⑥ 上传导出文件，内存提取水印"
TRACE_RESPONSE="$(curl -sf -X POST "${BASE_URL}/api/trace/file" -F "file=@${TMP_DIR}/exported_customer.csv;type=text/csv")"
printf '%s' "${TRACE_RESPONSE}" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin)["data"]; print("   extracted_watermark=",d["extracted"]["watermark_id"]); print("   exporter=",d["extracted"]["user_id"]); print("   match_score=",d["match_score"])'
echo "   预期：水印 ID 与导出人 alice 一致"

echo "⑦ 按水印 ID 查询完整时间线"
TRACE_BY_ID="$(curl -sf "${BASE_URL}/api/trace/${WATERMARK_ID}")"
printf '%s' "${TRACE_BY_ID}" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin)["data"]; print("   timeline_steps=",len(d["timeline"])); [print("   -",x["title"],x["time"]) for x in d["timeline"]]'
echo "   预期：入湖 → 创建沙箱 → 生成文件 → 申请 → 审批 → 水印 → 导出"

echo "⑧ 组件状态与真实闭环自检"
COMPONENTS_RESPONSE="$(curl -sf "${BASE_URL}/api/components")"
printf '%s' "${COMPONENTS_RESPONSE}" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin)["data"]; print("   active_sandbox_backend=",d["active_sandbox_backend"]); print("   summary=",d["summary"]); [print("   -",x["id"],x["status"],x["provider"]) for x in d["items"]]'
WATERMARK_SELFTEST="$(curl -sf -X POST "${BASE_URL}/api/components/watermark-engine/self-test" -H 'Content-Type: application/json' -d '{"actor":"demo"}')"
printf '%s' "${WATERMARK_SELFTEST}" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin)["data"]; print("   watermark_selftest=",d["status"]," match_score=",d["match_score"]," trace_id=",d["trace_id"])'
SANDBOX_SELFTEST="$(curl -sf -X POST "${BASE_URL}/api/components/sandbox-runtime/self-test" -H 'Content-Type: application/json' -d '{"actor":"demo"}')"
SANDBOX_SELFTEST_TRACE="$(printf '%s' "${SANDBOX_SELFTEST}" | json_value "['data']['trace_id']")"
printf '%s' "${SANDBOX_SELFTEST}" | "$PYTHON" -c 'import json,sys; d=json.load(sys.stdin)["data"]; print("   sandbox_selftest=",d["status"]," backend=",d["backend"]," trace_id=",d["trace_id"]); print("   nested=",{k:v for k,v in d.items() if k.endswith("_trace_id")})'
echo "   预期：组件状态来自真实探测；自检生成可独立查询的 execution_traces"

echo
echo "演示完成。服务地址：${BASE_URL}"
echo "后端执行证据：${PYTHON} -m app.cli trace ${EXPORT_TRACE}"
echo "水印逐行证据：${PYTHON} -m app.cli watermark ${WATERMARK_ID}"
echo "水印算法阶段：${PYTHON} -m app.cli trace ${EXPORT_TRACE} | sed -n '/watermark.payload.build/,/watermark.csv.serialize/p'"
echo "沙箱组件自检：${PYTHON} -m app.cli trace ${SANDBOX_SELFTEST_TRACE}"

