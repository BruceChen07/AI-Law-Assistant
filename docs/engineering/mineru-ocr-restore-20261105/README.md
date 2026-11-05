# MinerU OCR 恢复（本地 pipeline 模型）

- 日期：2026-11-05
- 现象：启动自检报 2 项 critical
  - `mineru_env`：MinerU 所在 Python 环境缺 `pyclipper / omegaconf / dill`
  - `mineru_models`：`models/mineru` 不完整（缺 `pipeline/`、`vlm/`）
- 影响面（核实结论）：
  | 输入 | 是否受影响 |
  | --- | --- |
  | DOCX | 否（走 python-docx） |
  | 电子版 PDF（文本层 ≥ `ocr_min_text_length`=200） | 否（不触发 OCR） |
  | 扫描件 / 图片型 PDF | **是**：`available_engines()` 实测只有 `['mineru']`（tesseract 二进制不在 PATH），且 `OCREngineManager.ocr_pdf` 无引擎回落 → 抛 `OCR_PARSE_FAILED` |

## 处理步骤（已执行）

1. 安装 MinerU pipeline 依赖（`app/.venv`）：
   ```
   pyclipper 1.4.0 / omegaconf 2.4.0 / dill 0.4.1
   ```
2. 下载本地模型到 `models/mineru`（HuggingFace/ModelScope 清单 + sha256 校验、断点续传）：
   - `pipeline/`：Layout(PP-DocLayoutV2) + OCR(paddleocr_torch, 25 个文件) 等，约 **1.24 GB**，必需项全部就位
   - `mineru.json`（models-dir 指向 pipeline/vlm）已自动生成
   - `vlm/` 未下载（见下）
3. `app/config.json` 的 `mineru` 段：
   - `mode: "auto" → "force_pipeline"`：只要求 pipeline 模型，运行期固定走 pipeline 后端；
     （当前 `formula=false`、`table=false`，pipeline 后端已覆盖版面/OCR 需求）
   - 下载参数调优：`read_timeout_sec=600`、`retries=5`、`max_workers=4`
     （ModelScope CDN 下载大文件时 120s 读超时会导致频繁重试）
4. `models/mineru/` 早已在 `.gitignore` 中，权重不会入库。

## 验证

| 项 | 结果 |
| --- | --- |
| `collect_report()` | `ok=True, critical=0, warnings=0` |
| `/health` | `"preflight":{"ok":true,"critical_count":0,"warning_count":0}` |
| 扫描件 PDF OCR 实测（1 页图片型 PDF） | `ocr_used=True, engine=mineru, pages=1`，文本内容与图片一致 |
| DOCX 审计（真实调用） | 不受影响，正常完成 |

## 后续可选：启用 VLM 后端

若需要 `mode=auto/force_hybrid`（VLM 对复杂版面更鲁棒），需补下 `vlm/` 权重：

```powershell
# 1) 先把 mode 临时改回 auto（否则检查只看 pipeline，不会触发 vlm 下载）
# 2) 执行下载（支持断点续传，已调大超时）
app\.venv\Scripts\python.exe -c "import sys;sys.path.insert(0,'.');from app.core.config import load_config;from app.core.mineru_models import ensure_local_models;print(ensure_local_models(load_config(), allow_download=True).get('ok'))"
```

注意：VLM 权重约 2 GB+，ModelScope CDN 在本机曾多次读超时；可改用
`mineru.download_source="huggingface"` 或保持 `force_pipeline`（推荐，够用且省内存）。

## 运维提示

- 自检默认只告警不阻断（`startup_dependency_strict=false`）；生产/CI 建议设 `true`，缺能力就不许启动。
- 服务重启会中断正在跑的审计：chunk 已落库，重试时按 `(source_hash, plan_hash)` 复用，实测可从中断点继续（本次 `resume-4ec07df3` 即复用 20 个已完成 chunk，plan_hash 一致）。
