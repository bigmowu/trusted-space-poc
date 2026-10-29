# 可信数据空间原型

一个可在本地完整跑通的「可信数据空间」Web 原型：在隔离沙箱中运行 Python 生成 CSV，经分级闸门申请出域，完成 L3/L4 三级审批后强制嵌入哈希槽水印，再将泄露文件上传反查到导出人、审批链与全链路时间线。

核心机制、算法参数、沙箱生命周期、出域状态机、日志与验证命令的完整说明见 [`docs/CORE_IMPLEMENTATION.md`](docs/CORE_IMPLEMENTATION.md)。

## 动态演示

![可信数据空间宣传片（明亮版）](docs/media/trusted-space-promo-light.gif)

`1280×720 · 32.5 秒 · 10 fps · 明亮主题`。这是一部**基于真实运行数据生成的动态宣传片，不是屏幕录制**：沙箱 `5/5` 证据、`3216` 个水印槽位、`2583` 行实际改动、`99.16%` 回读匹配度均来自本项目的真实导出记录。片中共 9 个场景，包含开源技术栈和阿里云目标架构。另提供 [`WebP 动图`](docs/media/trusted-space-promo-light.webp)、[`明亮版封面`](docs/media/trusted-space-poster-light.png)、[`阿里云场景封面`](docs/media/trusted-space-aliyun-poster-light.png) 与[生成脚本](docs/media/generate_promo.py)。

## 开源技术底座

| 组件 | 在本项目中的作用 | 许可证 / 当前状态 |
| --- | --- | --- |
| FastAPI | HTTP API、路由与依赖注入 | MIT；当前运行依赖 |
| Uvicorn | ASGI 应用服务器 | BSD-3-Clause；当前运行依赖 |
| Pydantic | 请求模型与校验 | MIT；当前运行依赖 |
| python-multipart | 表单与文件上传 | Apache-2.0；当前运行依赖 |
| Pandas | CSV 生成与数据处理 | BSD-3-Clause；当前运行依赖 |
| SQLite | 沙箱、审批、水印与 trace 元数据 | Public Domain；当前运行依赖 |
| Python 标准库 | `hashlib`、`zlib`、`struct`、`csv` 等水印基础 | PSF；当前运行依赖 |
| Docker | 可选的容器沙箱后端 | Docker Engine 为 Apache-2.0；Docker Desktop 另有许可；当前为可选后端 |
| OpenSandbox | 可选的远端沙箱 SDK/服务 | **未安装、未联调**；本机 `.venv` 无 `opensandbox`，不能视为已接入 |
| 原生 HTML/CSS/JS | 前端页面与 Hash 路由 | 项目自有代码；无前端构建链 |
| Pillow + NumPy | 仅用于生成宣传片素材 | Pillow MIT-CMU；NumPy BSD-3-Clause 等组合许可；不进入 Web 运行时 |

`requirements.txt` 声明了可选的 `opensandbox>=0.1.17.dev0`，但当前实际后端是 `mock`。组件页和 `GET /api/components` 会如实显示可用性，不会用“SDK 存在于依赖文件”代替真实连通性。

## 阿里云演进

阿里云部分目前是**目标设计和迁移计划，尚未在阿里云账号中部署**，宣传片中的云场景明确标注为 `ROADMAP · NOT IMPLEMENTED`。完整方案见 [`docs/ALIYUN_INTEGRATION.md`](docs/ALIYUN_INTEGRATION.md)。

核心映射是：SQLite → RDS PostgreSQL；本地 `exports/` → OSS + SSE-KMS + 版本控制/保留策略；本地日志 → SLS/ARMS；Mock/Docker 运行时 → ACK Job、ECI 或私有 VPC 内的 OpenSandbox；RAM + 企业 OIDC/SAML 提供身份；KMS 对文件摘要 manifest 签名；ACR 托管镜像；WAF + ALB 控制入口。

落地原则是先抽象存储、元数据、日志、身份、签名和运行时接口，再逐项替换；保持 `/api`、审批状态机、水印载荷和 `trace_id` 不变。沙箱不持有长期 AK/SK，原始数据只读，销毁时必须清理私有工作卷；RDS、OSS、KMS 任一失败都拒绝产生未登记的可下载文件。

## 快速启动

推荐 Python 3.11。首次启动：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./start.sh
```

也可以直接运行 `./start.sh`。脚本优先使用项目内 `.venv/bin/python`，不会修改 conda base；设置 `RELOAD=1 ./start.sh` 可开启热重载。

日志按用途拆成三个文件，避免算法步骤和 HTTP 请求淹没业务生命周期：

| 日志 | 内容 | 查看方式 |
| --- | --- | --- |
| `logs/events.log` | INFO 级业务事件：应用启动、沙箱创建/初始化/执行/销毁、水印嵌入/提取、审批与导出 | 终端同步输出；`#/audit` 默认视图；`python -m app.cli logs -f` |
| `logs/trace.log` | 单次操作内部的 `trace.start`、每个 `trace.step`、`trace.finish`，包含参数摘要和耗时 | `python -m app.cli logs --source trace -f`；`GET /api/traces` |
| `logs/http.log` | HTTP 方法、路径、状态码、耗时、IP 与 `request_id` | `python -m app.cli logs --source http -f` |

业务事件的终端输出示例：

```text
2026-10-28 14:30:01 | INFO | event=sandbox.creating sandbox_id=SBX-20261028-abc123 backend=mock task_id=demo actor=alice
2026-10-28 14:30:01 | INFO | event=sandbox.initialized sandbox_id=SBX-20261028-abc123 backend=mock volumes=raw:ro,work:rw trace_id=TRC-20261028-...
2026-10-28 14:30:01 | INFO | event=sandbox.created sandbox_id=SBX-20261028-abc123 backend=mock actor=alice status=RUNNING trace_id=TRC-20261028-...
2026-10-28 14:30:02 | INFO | event=sandbox.exec.started sandbox_id=SBX-20261028-abc123 backend=mock actor=alice code_bytes=588 trace_id=TRC-20261028-...
2026-10-28 14:30:02 | INFO | event=sandbox.exec.completed sandbox_id=SBX-20261028-abc123 backend=mock status=success exit_code=0 duration_ms=23.48 files=1 trace_id=TRC-20261028-...
2026-10-28 14:30:03 | INFO | event=watermark.embedding request_id=EXP-20261028-ABC123 actor=alice algorithm=hash-slot-v1 source_bytes=215036 trace_id=TRC-20261028-...
2026-10-28 14:30:03 | INFO | event=watermark.embedded watermark_id=WM-20261028-... request_id=EXP-20261028-ABC123 rows=5000 changed_rows=2500 frame_bytes=134 trace_id=TRC-20261028-...
2026-10-28 14:30:04 | INFO | event=sandbox.destroyed sandbox_id=SBX-20261028-abc123 backend=mock actor=alice purged_work_files=1 trace_id=TRC-20261028-...
```

业务事件字典（`event=` 后为稳定检索名）：

| 域 | 事件 |
| --- | --- |
| 应用/数据库 | `application.starting`、`database.initialized`、`application.ready` |
| 沙箱生命周期 | `sandbox.creating`、`sandbox.initialized`、`sandbox.created`、`sandbox.recovered`、`sandbox.demo.reused`、`sandbox.demo.ready`、`sandbox.destroyed` |
| 沙箱执行 | `sandbox.exec.started`、`sandbox.exec.completed`、`sandbox.exec.blocked`、`sandbox.exec.timeout`、`sandbox.exec.failed` |
| 水印 | `watermark.embedding`、`watermark.embedded`、`watermark.extracting`、`watermark.extracted`、`watermark.embed.failed`、`watermark.extract.failed` |
| 出域/审批 | `export.requested`、`export.approved`、`export.rejected`、`export.transferred`、`export.blocked`、`export.reused`、`export.completed` |
| 组件自检 | `component.selftest.started`、`component.selftest.completed`、`component.selftest.failed` |

水印事件同时写入 `algorithm=hash-slot-v1` 与 `method=sha256_hash_slot+amount_lsb+3x_repeat+crc32`；具体哈希槽碰撞、LSB 改动行数、CRC 修复等步骤写入 `logs/trace.log`，可用同一 `trace_id` 关联。

三条链路通过 `trace_id` 及 trace 输入中的 `http_request_id` 关联。`#/audit` 可切换「业务事件」「HTTP 访问」「执行细节」；终端和 API 都能独立核验，不依赖页面。

访问 <http://localhost:8000>。默认使用内置 `MockSandbox`，不需要 Docker、OpenSandbox 服务或任何外部数据库。实际后端由 `SANDBOX_BACKEND=mock|docker|opensandbox` 选择，可在 `#/components` 或 `GET /api/components` 核验。

如需从干净演示状态开始：

```bash
rm -f data/trusted_space.db exports/*.csv
./.venv/bin/python -m uvicorn app.main:app --reload
```

启动时自动创建 SQLite 表、插入 5 条数据资产，并创建 `sbx-demo` 沙箱，挂载只读 `/mnt/raw/customer.csv`、`/mnt/raw/order.csv`。

## 运行机制：完整执行链

### 隔离环境

推荐通过项目内虚拟环境启动，`start.sh` 优先使用 `.venv/bin/python`，不会安装或修改 conda base 包：

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
./start.sh
```

若复用已装好依赖的 conda 环境，可只创建带 `--system-site-packages` 的项目 venv，不向 base 写入新包：`python3 -m venv --system-site-packages .venv`。

### 持久化模型

除原有业务表和审计表外，新增：

- `execution_traces`：一次业务操作一行，记录 `trace_id / operation / status / actor / target / input_json / result_json / error / duration_ms`。
- `execution_steps`：操作内部阶段，按 `seq` 排序，记录阶段名、状态、开始/结束时间、耗时和参数 JSON。

数据库在 `data/trusted_space.db`；沙箱卷在 `data/sandboxes/<sandbox_id>/{raw,work}`；导出文件在 `exports/`。这些位置都支持环境变量覆盖。

### 沙箱何时、如何初始化

1. FastAPI `lifespan` 先调用 `init_db()`，创建/迁移 SQLite 表。
2. 随后调用 `SandboxManager.seed_demo()`，先扫描数据库中的 `RUNNING` 沙箱，按 id 从 `data/sandboxes/` 恢复 raw/work 卷文件。
3. 如果 `sbx-demo` 不存在，则初始化 `raw`（只读）和 `work`（读写）卷、写入 `customer.csv` 与 `order.csv`、登记 `sandboxes` 表，最后进入 `RUNNING`。
4. 用户点击「新建沙箱」时走同一套流程：分配 `SBX-YYYYMMDD-xxxxxx`、初始化磁盘卷、登记内存与 SQLite、进入 `RUNNING`。
5. 服务重启时不会丢失用户工作文件：卷目录保留，启动时按数据库 `RUNNING` 状态恢复，并在 `sandbox.seed / sandbox.recovery.scan` trace 中记录恢复数量。

```text
sandbox.seed trace 典型阶段
database.ready
sandbox.recovery.scan
sandbox.runtime.initialize
sandbox.mounts.configure
sandbox.seed.raw_volume
sandbox.storage.initialize
database.sandbox.persist
sandbox.running
```

### 沙箱代码如何执行

`POST /api/sandbox/{id}/exec` 进入 `sandbox.exec` trace，按顺序记录：

| 阶段 | 实际动作 |
| --- | --- |
| `sandbox.state.check` | 校验沙箱为 `RUNNING` 并读取挂载配置 |
| `sandbox.policy.scan` | 逐行静态检测 `/mnt/raw` 写操作；命中直接返回 HTTP 403，不进入子进程 |
| `sandbox.workspace.allocate` | 在宿主机创建一次性临时根目录及 `mnt/raw`、`mnt/work` |
| `sandbox.volumes.materialize` | 将卷文件写入临时目录，记录文件数、大小和 SHA256 |
| `sandbox.mount.rewrite` | 把代码中的 `/mnt/raw`、`/mnt/work` 重写到临时挂载路径，记录替换次数 |
| `sandbox.process.exit` | 以 `python3 -c` 执行，记录退出码、stdout/stderr 和执行耗时 |
| `sandbox.volume.sync` | 只同步工作卷回内存，原始卷永不回写 |
| `sandbox.storage.persist` | 重建并持久化 `data/sandboxes/<id>/work`，保证重启可恢复 |
| `sandbox.workspace.cleanup` | 删除临时目录，记录是否清理完成 |

### 如何确认沙箱真的存在并正在运行

数据库里的 `RUNNING` 只代表“登记过”，不能单独证明当前进程仍持有运行时、卷仍完整。状态接口会联合核对五项证据：

| 证据 | 来源 |
| --- | --- |
| `database.record` | SQLite 创建记录 |
| `database.running` | 数据库状态是否为 `RUNNING` |
| `process.registry` | 当前 FastAPI 进程的内存注册表 |
| `runtime.live` | Mock 可执行状态 / Docker `inspect` 状态 / OpenSandbox adapter |
| `storage.volumes` | `data/sandboxes/<id>/{raw,work}` 是否都存在 |

五项全部通过才返回 `RUNNING_VERIFIED`。任意一项不一致会返回 `STALE_RUNNING`、`ORPHANED_RUNTIME`、`NOT_RUNNING` 或 `ORPHANED_STORAGE`，不再用单一布尔值掩盖问题。`#/sandbox` 页面顶部会直接显示这五项事实，也可用命令核验：

```bash
curl -s http://127.0.0.1:8000/api/sandbox/status | ./.venv/bin/python -m json.tool
curl -s http://127.0.0.1:8000/api/sandbox/sbx-demo/status | ./.venv/bin/python -m json.tool
./.venv/bin/python -m app.cli sandboxes
./.venv/bin/python -m app.cli sandbox sbx-demo
```

Mock 后端是**按需执行**模型：没有长驻沙箱 PID；`EXTERNAL_CONSISTENCY` 只是 CLI 独立进程看不到服务内存，需再调用 `/api/sandbox/{id}/status`。详细判定规则见 [`docs/CORE_IMPLEMENTATION.md`](docs/CORE_IMPLEMENTATION.md#67-如何观测沙箱是否真的存在是否正在运行)。

### 水印方法与逐阶段证据

导出执行 trace 为 `export.execute`，水印内部阶段如下：

| 阶段 | 算法动作与记录内容 |
| --- | --- |
| `watermark.payload.build` | 构造 `{watermark_id,user_id,export_time,file_id,request_id}`；记录 JSON 字节数和 SHA256 |
| `watermark.frame.pack` | `MAGIC(2B) + VERSION(1B) + 压缩长度(2B) + CRC32(4B) + zlib(payload)`；记录帧长度、CRC 与帧摘要 |
| `watermark.bits.repeat` | 帧字节转比特串并完整重复 3 次；记录总槽位数、重复次数和样例比特 |
| `watermark.slots.map` | 对每行 `customer_id` 取 SHA256 前 8 位转整数，对总槽位数取模；记录占用/空闲槽、冲突数和样例 |
| `watermark.amount.lsb` | 槽位 bit 写到该行 `amount` 整数最低位，偶数=0、奇数=1；记录改行数、未改行数、前后值和最大误差 |
| `watermark.csv.serialize` | 重新输出 UTF-8 CSV；记录字节数和输出 SHA256 |
| `watermark.roundtrip.verify` | 导出前立即从内存中的新文件再次提取水印，核对 ID、业务载荷、匹配度、未知位与 CRC 修复 |
| `export.file.write` | 将水印文件写入 `exports/<watermark_id>_<filename>`，并记录落盘摘要 |
| `database.export.persist` | 同时写入 `watermark_mappings`（含水印证据 JSON）和更新申请单状态 |

提取 trace 为 `watermark.extract`：

| 阶段 | 算法动作与记录内容 |
| --- | --- |
| `watermark.input.read` | 记录上传/下载文件字节数和 SHA256 |
| `watermark.csv.parse` | 解析表头，定位 `customer_id` 与 `amount` 列，记录有效/跳过行数 |
| `watermark.bits.collect` | 按每行 `amount` 奇偶回收 bit，记录奇偶数量 |
| `watermark.frame.search` | 在每个候选帧长度下用 MAGIC、长度、CRC、JSON 结构校验，记录尝试次数和命中长度 |
| `watermark.majority.vote` | 三份冗余按槽位回收，多数投票恢复比特；记录已恢复槽、已知位、未知位 |
| `watermark.crc.repair` | 枚举少量全缺失位，并用 CRC32 约束确认；记录候选数和实际修复位数 |
| `watermark.payload.decode` | 解压、JSON 解码并返回水印载荷；记录匹配度和水印 ID |

### 页面如何直观看到水印

打开 `#/trace`，查询水印 ID 或上传导出 CSV，结果中的「水印写入证据」直接展示：

1. **承载位置**：算法把水印帧写入 `amount` 列的整数 LSB；`bit=0` 对应偶数，`bit=1` 对应奇数。
2. **逐行样本**：列出 `customer_id → SHA256 前 8 位 → 槽位 → 3 份冗余中的副本 → amount 原值/导出值 → LSB 变化`。
3. **扰动范围**：显示总行数、真实改动行、未改动行、改动比例和 `amount` 最大扰动；当前样本最大扰动为 1。
4. **回读校验**：导出后立即重提取，展示水印 ID、业务载荷、匹配度、未知位、CRC32 修复数，证明不是只写了一个展示字段。

> 水印 ID 不会以 `WM-...` 明文出现在 CSV 中，所以直接搜索文件找不到。文件里只有 `amount` 奇偶位承载的编码帧；系统先从这些位恢复载荷，再用载荷中的 `watermark_id` 与 `watermark_mappings` 交叉验证。

同一份证据可直接通过 API/CLI 读取：

```bash
curl -s http://localhost:8000/api/watermark/<WM-ID>/evidence | python -m json.tool
python -m app.cli watermark <WM-ID>
python -m app.cli watermark <WM-ID> --json
```

页面或命令中的 `changed_samples` 会给出类似 `C000005: 105 → 104, LSB 1→0, slot=467, replica=1/3` 的实际记录；这些值来自导出文件与原工作文件的逐行复算，不是固定演示文案。

### 审批状态机

`export.request` 根据分级决定通道：`L1/L2 -> fast` 直接 `approved`；`L3/L4 -> approval` 进入 `pending`。`export.approval` trace 每次记录 `from_stage / from_node / decision / to_stage / to_node / new_status`，节点固定为 `Owner → Security → Compliance`。只有最终 `approved` 才能进入 `export.execute`，否则 `export.policy.approval` 阶段标记 `blocked` 并返回 HTTP 403。

### API 与 CLI 独立核验

```bash
# 最近 20 条业务执行追踪
curl -s 'http://localhost:8000/api/traces?limit=20' | python -m json.tool

# 只看沙箱执行，或只看水印算法阶段
curl -s 'http://localhost:8000/api/traces?operation=sandbox.exec&limit=10' | python -m json.tool
curl -s 'http://localhost:8000/api/traces?operation=export.execute&phase=watermark&limit=10' | python -m json.tool

# 单条 trace 的完整 JSON / 人读文本
curl -s 'http://localhost:8000/api/traces/<TRACE_ID>' | python -m json.tool
curl -s 'http://localhost:8000/api/traces/<TRACE_ID>/text'
```

```bash
# CLI：列出可追踪操作
python -m app.cli operations

# 最近执行链；--follow 持续等待新追踪
python -m app.cli traces --limit 20
python -m app.cli traces --operation sandbox.exec --follow

# 查看最近沙箱初始化、最近水印导出，或指定 trace
python -m app.cli trace --last --operation sandbox.seed
python -m app.cli trace --last --operation export.execute
python -m app.cli trace TRC-20261028-xxxxxxxxxx

# 跟随业务事件（默认）；也可切换 trace / http
python -m app.cli logs -f
python -m app.cli logs --source trace -f
python -m app.cli logs --source http -f

# 组件状态与闭环自检
python -m app.cli components
python -m app.cli component sandbox-runtime
python -m app.cli component sandbox-runtime --selftest
python -m app.cli component watermark-engine --selftest
```

## 一键演示链路

启动服务后执行：

```bash
./demo.sh
```

脚本也可以用 `BASE_URL=http://127.0.0.1:8000 ./demo.sh` 指向已经启动的服务。若本地 8000 端口未启动服务，脚本会自行拉起一个 Uvicorn 进程并在结束时清理。每步都会打印实际值与预期说明：

1. 创建可信沙箱。
2. 在沙箱内生成 5000 行 `customer.csv`。
3. 提交 L1 快速通道出域申请，状态直接为 `approved`。
4. 执行水印导出，生成 `WM-YYYYMMDD-XXXXXXXX`。
5. 读取水印证据：承载列、改动行数、最大扰动、槽位与 amount 前后值。
6. 下载带水印 ID 前缀的文件。
7. 上传导出文件，在内存中提取水印与导出人。
8. 按水印 ID 查询七步溯源时间线。
9. 读取组件注册表，真实执行水印与沙箱闭环自检，并打印嵌套 trace ID。

## 核心链路

```text
创建沙箱 → 网页写 Python → 生成 CSV → 申请出域 → 分级判定
       → 快速通道/三级审批 → 强制水印嵌入 → 下载 → 上传泄漏文件 → 溯源到人
```

- **L1/L2**：`fast` 通道，申请单直接为 `approved`，前端提交后自动执行水印导出。
- **L3/L4**：`approval` 通道，真实经过 Owner → 安全 → 合规三个阶段，最终审批通过后才可导出。
- **原始卷**：`/mnt/raw` 只读。代码中的写操作在执行前被字符串检测拦截并记录 `raw_write_blocked`；即使绕过代码检测，`write_file` 仍拒绝写原始卷。
- **统一出口**：只有 `approved` 申请可以执行；执行时必然先调用水印引擎，写入 `exports/` 并登记 `watermark_mappings`。

## 8 个页面与截图说明

顶部品牌区、220px 固定导航和右侧内容区组成单页应用，Hash 路由切换页面不刷新。

| 路由 | 页面 | 截图重点 |
| --- | --- | --- |
| `#/overview` | 安全概览 | 域可控/境可信/源可溯/权可分 4 张指标卡；最近出域事件；实时告警；四失风险收敛条形图 |
| `#/assets` | 数据资产 | 搜索与 L1–L4 筛选；种子表 `customer/order/product/risk_score/log`；涉密标识；申请共享 |
| `#/sandbox` | 可信沙箱 | 只读/读写挂载树；深色代码编辑器；运行/保存/清空；控制台与文件列表；操作日志时间线 |
| `#/components` | 组件中心 | 6 个组件的真实实现、探测项、能力/依赖与修复建议；Mock/Docker/OpenSandbox 候选状态；水印与沙箱闭环自检 |
| `#/export` | 出域申请 | 状态筛选；申请单表格；四步弹窗：选择文件、申请信息、分级判定、提交结果 |
| `#/approval` | 审批中心 | 仅 L3/L4 待办；风险提示；脱敏预览；水印策略；右侧抽屉与三级审批链 |
| `#/trace` | 水印溯源 | 已导出文件；按 ID/上传文件查询；逐行 amount LSB 证据、槽位/冗余/CRC 校验；七步时间线；匹配度圆环 |
| `#/audit` | 审计日志 | 时间/操作/结果筛选；阻断记录红色高亮；原始卷写尝试、未授权导出、审批超时统计 |

更详细的截图取景建议见 `docs/screenshots/README.md`。页面全部由 API 实时渲染，不使用静态假数据。

## 目录结构

```text
trusted-space/
├── app/
│   ├── main.py                 # FastAPI 入口、异常处理、静态挂载
│   ├── config.py               # 路径、Mock 开关、上传限制
│   ├── db.py                   # SQLite 建表、种子资产、审计写入
│   ├── common.py               # BizError、统一响应、IP
│   ├── cli.py                  # traces / trace / logs 运维命令
│   ├── observability/          # trace 记录器、JSON 脱敏与 CLI 格式化
│   ├── sandbox/manager.py      # MockSandbox、路径映射、原始卷写拦截
│   ├── sandbox/runtimes.py     # Docker / OpenSandbox / 运行时探测
│   ├── components/registry.py  # 组件状态契约与自检编排
│   ├── watermark/engine.py     # 水印组件边界与闭环自检
│   ├── watermark/hash_slot.py  # 哈希槽水印嵌入/提取
│   ├── export/gate.py          # 分级、审批、强制水印导出
│   ├── trace/repository.py     # 映射查询与溯源时间线
│   └── api/                    # sandbox/data/export/watermark/trace/traces/audit/overview
├── static/                     # index.html + app.js + style.css
├── tests/                      # 水印单测 + API 验收测试
├── docs/                       # 核心实现、阿里云对接方案、宣传片与截图说明
├── exports/                    # 带水印导出文件
├── data/                       # SQLite + sandboxes/<id>/{raw,work} 持久化卷
├── demo.sh
└── requirements.txt
```

## API 一览

所有 JSON 接口统一返回 `{"code": 0, "message": "ok", "data": ...}`。业务异常返回 `code != 0` 和 HTTP 业务状态码。

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| POST | `/api/sandbox` | 创建沙箱 `{task_id}` |
| GET | `/api/sandbox` | 沙箱列表与默认代码 |
| POST | `/api/sandbox/{id}/exec` | 执行 `{code}`，超时 10 秒 |
| POST | `/api/sandbox/{id}/write` | 保存工作卷文件 |
| GET | `/api/sandbox/{id}/files` | 工作卷文件列表 |
| GET | `/api/sandbox/{id}/files/{path}` | 下载工作卷文件 |
| GET | `/api/sandbox/{id}/mounts` | 挂载信息 |
| GET | `/api/sandbox/status` | 全量沙箱五项事实与聚合数量 |
| GET | `/api/sandbox/{id}/status` | 单沙箱运行事实、证据与 verdict |
| DELETE | `/api/sandbox/{id}` | 销毁沙箱 |
| GET | `/api/data/assets` | 资产查询 `q/level` |
| POST | `/api/data/generate` | 生成 CSV `{table_name, rows, sandbox_id}` |
| POST | `/api/export/request` | 创建出域申请并判定通道 |
| GET | `/api/export/requests` | 申请列表 `status` |
| GET | `/api/export/{id}` | 申请详情与脱敏预览 |
| POST | `/api/export/{id}/approve` | 逐级审批 `{approver, decision, comment}` |
| POST | `/api/export/{id}/execute` | 强制执行水印导出 |
| GET | `/api/export/{id}/download` | 下载导出文件 |
| GET | `/api/watermark/list` | 已导出文件档案 |
| POST | `/api/watermark/extract` | Base64 CSV 提取水印 |
| GET | `/api/watermark/{wm}/evidence` | 水印写入证据：载荷帧、逐行 amount LSB 差异、槽位与回读校验 |
| GET | `/api/watermark/{wm}/download` | 按水印 ID 下载 |
| GET | `/api/trace/{wm}` | 按水印 ID 查询时间线 |
| POST | `/api/trace/file` | multipart 上传 CSV，内存提取，不落盘 |
| GET | `/api/traces` | 业务执行追踪列表，支持 `operation/status/target/phase/limit` |
| GET | `/api/traces/{trace_id}` | 单条追踪及全部阶段 JSON |
| GET | `/api/traces/{trace_id}/text` | 单条追踪的人读文本 |
| GET | `/api/components` | 组件状态、能力、依赖、探测项、自检入口与运行时候选 |
| GET | `/api/components/{id}` | 单组件详情及最近一次自检 trace |
| POST | `/api/components/{id}/self-test` | 执行水印或沙箱闭环自检，返回 trace 与嵌套 trace ID |
| GET | `/api/audit/logs` | 日志筛选与风险统计 |
| GET | `/api/system/logs` | 日志读取，`source=events|trace|http`，默认 events；支持 `limit` |
| GET | `/api/overview` | 概览指标、告警与风险收敛数据 |
| GET | `/api/health` | 健康检查 |

错误码：`1001` 沙箱不存在、`1002` 只读卷写、`1003` 文件不存在、`1004` 执行超时、`1005` 未审批导出、`1006` 水印失败、`1007` 组件不可自检或未就绪、`1008` 真实沙箱后端初始化失败。

## 水印算法

1. 载荷字段：`watermark_id`、`user_id`、`export_time`、`file_id`、`request_id`。
2. JSON 序列化并压缩为带魔数、版本、长度和 CRC32 的帧，再转成比特串。
3. 比特串完整重复 3 次，总长度为 `frame_bits × 3`。
4. 每行 `customer_id` 做 SHA256，取 hex 前 8 位转整数，对总比特数取模得到槽位。
5. 把槽位值写入该行 `amount` 的最低有效位；保留小数位，数值变化不超过 1。
6. 提取时按同样规则回收比特，对三份副本多数投票；对丢行导致的三副本全缺失位进行 CRC 约束纠错。
7. 帧长度由提取器自动搜索，因此删行不会造成后续槽位整体偏移，行序打乱也不影响定位。

`watermark_id` 使用 `WM-YYYYMMDD-<8位摘要>` 格式。由于 ID 自身包含在载荷中，摘要基于除 ID 外的业务字段计算，避免循环依赖。

设计目标：5000 行数据正常提取成功率 > 95%；删除不超过 10% 行并打乱顺序后仍可恢复水印 ID 和导出人。

## 组件化落地

组件中心不是静态说明页。`GET /api/components` 每次请求都会读取当前进程配置、数据库、目录权限和运行时探测结果；`POST /api/components/{id}/self-test` 会真实执行闭环并写入 `execution_traces`。

### 六个可核验组件

| 组件 ID | 当前实现 | 真实职责 | 自检方式 |
| --- | --- | --- | --- |
| `sandbox-runtime` | `MockSandbox` / `DockerSandbox` / `OpenSandboxSandbox` | 统一 create/write/exec/read/destroy；卷物化与结果回写 | 创建临时沙箱 → 写 `/mnt/work/component_check.txt` → 执行读取 → 校验 stdout → 销毁 |
| `watermark-engine` | `HashSlotWatermarkEngine` | 强制导出水印嵌入、泄露文件提取、命令/API/CLI 共用同一实现 | 6000 行确定性 CSV 嵌入 → 提取 → 校验水印 ID、用户、申请号与匹配度 |
| `export-gate` | `ForcedExportGate` | 工作卷检查、L1–L4 分级、审批状态机、导出前强制水印 | 由 L1/L3 端到端验收覆盖 |
| `trace-store` | `TraceRecorder` | 持久化每个后端阶段、参数、耗时、异常和 HTTP request_id 关联 | trace/step 可读性与最近追踪探测 |
| `metadata-db` | SQLite | 沙箱、申请、水印映射、审计、执行追踪的唯一元数据源 | `PRAGMA integrity_check` 与必需表检查 |
| `artifact-store` | 本地文件系统 | `data/sandboxes/<id>/{raw,work}` 与 `exports/` 持久化 | 可写性、磁盘空间、目录计数 |

`sandbox-runtime` 详情额外返回 `runtime_variants`，同时列出 `mock`、`docker`、`opensandbox` 的独立探测结果。未安装或未连通的后端会原样显示 `unavailable`/`degraded`，不会回退并伪装成成功。

### 沙箱后端配置

| 环境变量 | 默认值 | 说明 |
| --- | --- | --- |
| `SANDBOX_BACKEND` | `mock` | `mock`、`docker`、`opensandbox`；未设置时兼容旧 `MOCK_SANDBOX=true/false` |
| `DOCKER_BIN` | `docker` | Docker CLI 路径 |
| `DOCKER_SANDBOX_IMAGE` | `python:3.11-slim` | 容器镜像，需提前 pull 或本地已有 |
| `DOCKER_MEMORY` / `DOCKER_CPUS` | `512m` / `1` | 容器资源上限 |
| `OPENSANDBOX_IMAGE` | `python:3.11-slim` | 远端沙箱镜像请求参数 |
| `OPENSANDBOX_ADAPTER` | 空 | 严格适配器工厂，格式 `module:factory` |
| `SANDBOX_EXEC_TIMEOUT` | `10` | 执行超时秒数 |

### Docker 后端

先确认 Docker Desktop / daemon 在系统终端可用，并准备镜像：

```bash
docker info
docker pull python:3.11-slim
SANDBOX_BACKEND=docker ./start.sh
```

每次运行使用独立容器，安全参数包括：

- `--network none`：无网络。
- `--cap-drop ALL`、`--security-opt no-new-privileges`：移除默认能力并禁止提权。
- `--read-only` 根文件系统，仅 `/tmp` 为受限 tmpfs。
- `--memory`、`--cpus`、`--pids-limit 256`：资源限额。
- `data/sandboxes/<id>/raw` 只读绑定到 `/mnt/raw`，`work` 读写绑定到 `/mnt/work`。

Docker 探测会分别检查 CLI、daemon 和镜像；任一失败都会返回具体 check 与 remediation。当前 Codex 执行沙箱内 Docker socket 被宿主策略拦截，因此本会话只能验证“正确报告 unavailable”，不能在容器内代替系统终端验证 running 状态。

### OpenSandbox 适配契约

两种接入方式：

1. 安装兼容版本的 `opensandbox` SDK，设置 `SANDBOX_BACKEND=opensandbox`。SDK 自动适配器只做方法反射，状态会标为 `degraded`，直到自检真正连通服务。
2. 生产环境推荐设置 `OPENSANDBOX_ADAPTER=your_module:factory`，由工厂返回严格适配器实例，实现：

```python
class Adapter:
    def create(self) -> None: ...
    def upload_files(self, files: dict[str, bytes]) -> int: ...
    def execute(self, code: str) -> dict: ...  # exit_code/stdout/stderr
    def download_work(self) -> list[dict]: ... # path/bytes/sha256
    def destroy(self) -> None: ...
```

执行前后仍由本系统统一记录 `sandbox.policy.scan`、`sandbox.runtime.ensure`、`sandbox.process.exit`、`sandbox.volume.sync` 等 trace；出域闸门不感知底层实现。当前环境未安装 `opensandbox` SDK，因此探测结果为 `unavailable: 未安装 opensandbox SDK`。

### 现场核验

```bash
# 列出组件和候选运行时（含 checks / remediation）
curl -s http://localhost:8000/api/components | python -m json.tool

# 查看沙箱运行时详情与三种后端的探测结果
curl -s http://localhost:8000/api/components/sandbox-runtime | python -m json.tool

# 真实跑一条水印闭环并返回 trace_id
curl -s -X POST http://localhost:8000/api/components/watermark-engine/self-test \
  -H 'Content-Type: application/json' -d '{"actor":"alice"}' | python -m json.tool

# 真实创建/写卷/执行/销毁临时沙箱
curl -s -X POST http://localhost:8000/api/components/sandbox-runtime/self-test \
  -H 'Content-Type: application/json' -d '{"actor":"alice"}' | python -m json.tool

# 阅读自检链路
python -m app.cli trace <SELF_TEST_TRACE_ID>
```

沙箱自检返回 `create_trace_id`、`write_trace_id`、`exec_trace_id`、`destroy_trace_id`；水印自检返回 15 个阶段，包含载荷、压缩帧、三份冗余、槽位映射、LSB 改写、多数投票、CRC 修复和断言。

## 测试与验收

```bash
pytest -q
```

测试覆盖：

- 5000 行水印往返、水印 ID 与业务字段、行/列结构与 amount 误差。
- 删除 10% 行、打乱顺序后再次提取。
- 创建沙箱、生成 5000 行 CSV、文件下载。
- 原始卷写返回 403 并产生 blocked 审计。
- L1 直出、L3 三级审批、水印导出、文件名前缀。
- 上传文件溯源、删行后溯源、七步时间线。
- 沙箱初始化、执行、卷同步阶段的 trace 完整性。
- 水印嵌入/提取各算法阶段、CRC 修复与匹配度证据。
- 水印证据档案：逐行 amount 前后值、槽位/冗余、最大扰动、导出后回读与 CLI/API 一致性。
- API/CLI 查询 trace，以及沙箱卷在 Manager 重启后的恢复。
- 组件注册表、Mock/Docker/OpenSandbox 探测契约、水印和沙箱闭环自检、组件 API/CLI。

| 验收项 | 自动化或验证方式 |
| --- | --- |
| AC-01 首页 8 菜单切换 | 浏览器访问首页；Hash 路由 |
| AC-02 新建沙箱 | `POST /api/sandbox` |
| AC-03 运行代码生成 5000 行 | `pytest tests/test_api_acceptance.py` |
| AC-04 文件列表与下载 | `GET /api/sandbox/{id}/files` |
| AC-05 写原始卷 403 + 审计 | `test_api_acceptance.py` |
| AC-06 L1 直出 / L3 pending | `test_api_acceptance.py` |
| AC-07 审批后导出水印 | `test_api_acceptance.py` |
| AC-08 下载文件带 WM 前缀 | `test_api_acceptance.py` |
| AC-09 上传识别水印与导出人 | `test_api_acceptance.py` |
| AC-10 按 ID 返回完整时间线 | `test_api_acceptance.py` |
| AC-11 删除 10% 行仍可提取 | `test_watermark.py` |
| AC-12 审计可查、阻断红色高亮 | API 测试 + 页面 `#/audit` |
| AC-13 组件状态不伪装 | `GET /api/components`；OpenSandbox 未安装时显示 `unavailable` |
| AC-14 水印组件闭环 | `python -m app.cli component watermark-engine --selftest` |
| AC-15 沙箱组件闭环 | `python -m app.cli component sandbox-runtime --selftest` |

### Task 验证命令

| Task | 验证方式 |
| --- | --- |
| Task1 骨架 | `curl -s http://localhost:8000/api/health`，再访问 `/` 检查首页 |
| Task2 Mock 沙箱 | `pytest -q tests/test_api_acceptance.py -k acceptance` |
| Task3 Web 骨架 | 浏览器依次点击 7 个菜单，确认 URL Hash 变化且页面不刷新 |
| Task4 概览+资产 | `curl -s http://localhost:8000/api/overview`；访问 `#/overview`、`#/assets` |
| Task5 沙箱页 | 访问 `#/sandbox`，运行默认代码，确认生成 5000 行并出现下载按钮 |
| Task6 水印引擎 | `pytest -q tests/test_watermark.py` |
| Task7 出域+审批 | `pytest -q tests/test_api_acceptance.py`；页面完成 L1 与 L3 两条链路 |
| Task8 溯源页 | 导出后访问 `#/trace`，按 WM ID 查询并上传 CSV 查询 |
| Task9 审计页 | 触发一次 `/mnt/raw` 写操作，访问 `#/audit` 查看红色 blocked 行 |
| Task10 联调 | `./demo.sh`，确认七步输出与七步溯源时间线 |

常用单项命令：

```bash
# 健康检查与资产种子
curl -s http://localhost:8000/api/health
curl -s http://localhost:8000/api/data/assets

# 水印算法
pytest -q tests/test_watermark.py

# 完整接口验收
pytest -q tests/test_api_acceptance.py

# 前后端演示链路
./demo.sh
```

## 安全说明

该项目是本地可操作原型，用于展示数据最小授权、隔离计算、分级审批、强制出口水印和审计留痕。生产化还需补充身份认证、RBAC/ABAC、密钥管理、审批人角色绑定、真实沙箱隔离、对象存储加密、速率限制与不可篡改审计存储。

