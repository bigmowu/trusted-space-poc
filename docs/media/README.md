# 可信数据空间宣传片

## 交付文件

| 文件 | 规格 | 用途 |
| --- | --- | --- |
| `trusted-space-promo-light.gif` | 1280×720，32.5 秒，10 fps，325 帧，约 6.0 MiB | **默认明亮主题**；README、聊天工具、演示 |
| `trusted-space-promo-light.webp` | 同规格，约 4.1 MiB | 支持 WebP 动图的网页 |
| `trusted-space-poster-light.png` | 1280×720，约 423 KiB | 明亮版封面、缩略图、静态汇报 |
| `trusted-space-aliyun-poster-light.png` | 1280×720，约 590 KiB | 开源栈 / 阿里云规划场景封面 |
| `generate_promo.py` | Pillow + NumPy | 可复现生成脚本 |

旧版暗色素材仍保留为 `trusted-space-promo.gif`、`trusted-space-promo.webp` 和 `trusted-space-poster.png`，仅用于历史对照。

宣传片共 9 个场景：开场、沙箱五项观测、出域审批、哈希槽水印、证据、泄露文件追踪、开源技术底座、阿里云目标架构、收尾。开源与阿里云场景明确标注了当前真实状态和规划边界。

## 数据来源

宣传片不是屏幕录制，也不是虚构的演示文案。画面中的关键数字来自本机真实运行记录：

| 画面信息 | 真实值 | 来源 |
| --- | --- | --- |
| 沙箱状态 | `RUNNING_VERIFIED`，5/5 检查 | `GET /api/sandbox/{id}/status` |
| 水印 ID | `WM-20261028-321f7a4a` | `watermark_mappings` |
| 出域申请 | `EXP-20261028-BCAD8C` | `watermark_mappings` / `export_requests` |
| 导出用户 | `alice` | `watermark_mappings.user_id` |
| 压缩前后 | JSON 149 B → zlib 125 B | `logs/trace.log: watermark.frame.pack` |
| 水印帧 | 134 B，1072 bit，3 份冗余，3216 槽位 | `watermark_evidence.frame` |
| CRC32 | `d93ea751` | `watermark_evidence.frame.crc32` |
| CSV 规模 | 5000 行，2583 行改动，51.66% | `watermark_evidence.diff` |
| 回读校验 | 99.16%，unknown 9，corrected 9 | `watermark_evidence.verification` |
| 逐行样本 | `C000001: 101→100, slot 2929` 等 | `watermark_evidence.diff.changed_samples` |
| 提取 trace | `TRC-20261028-11b4313c01` | `logs/events.log` / `watermark.extract` |

可直接复核：

```bash
cd /Users/bigmo/code/trusted-space-poc
curl -s http://127.0.0.1:8000/api/watermark/WM-20261028-321f7a4a/evidence \
  | ./.venv/bin/python -m json.tool
./.venv/bin/python -m app.cli watermark WM-20261028-321f7a4a
./.venv/bin/python -m app.cli trace TRC-20261028-11b4313c01
```

## 重新生成

渲染依赖已在项目 `.venv` 中可用，不需要全局安装：

```bash
cd /Users/bigmo/code/trusted-space-poc
./.venv/bin/python docs/media/generate_promo.py
```

脚本默认使用明亮主题，并写入带 `-light` 后缀的 GIF、WebP、主封面和阿里云场景封面。若要重新生成旧暗色版本：

```bash
PROMO_THEME=dark ./.venv/bin/python docs/media/generate_promo.py
```

脚本使用 2× 超采样绘制后缩放，GIF 使用 128 色全局调色板、无抖动和帧间优化，兼顾体积与文字清晰度。若要改文案或数值，编辑脚本中对应场景函数即可。

## 边界说明

- 这是“数据驱动的 Motion Graphic”，不是对浏览器操作过程的逐帧录像。
- 画面不展示真实客户数据；`customer_id`、金额和文件名均为原型生成物。
- 宣传片不能替代运行验证；沙箱是否真的可执行，应以 `/api/sandbox/{id}/status` 和一次真实的 `sandbox.exec.completed` 为准。
- 阿里云场景是目标架构（`ROADMAP · NOT IMPLEMENTED`），不代表云资源已经创建或联调通过；具体方案见 [`../ALIYUN_INTEGRATION.md`](../ALIYUN_INTEGRATION.md)。
- 当前默认运行时为 Mock；`opensandbox` SDK 未安装在 `.venv` 中，画面中的 OpenSandbox 仅作为可选接入点。
