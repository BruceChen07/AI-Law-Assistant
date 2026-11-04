# MinerU 本地模型部署与离线运行

本文说明如何把 MinerU 的模型权重固化到本机目录，并让服务在 `model_source=local` 下完全脱离 Hugging Face / ModelScope 运行。

## 1. 目标与边界

| 目标 | 实现方式 |
|---|---|
| 模型离线可用 | 权重落地到 `models/mineru`，`mineru.json` 的 `models-dir` 指向本地目录 |
| 首次启动自动初始化 | 启动钩子检测缺失 → 后台下载 → 生成配置 → 校验 |
| 下载可靠 | HTTP Range 断点续传、大小 + sha256 校验、按文件重试、磁盘空间预检 |
| 运行期不触网 | `MINERU_MODEL_SOURCE=local` + `MINERU_TOOLS_CONFIG_JSON`，OCR 前只做本地检查，不回退 Hugging Face |
| 可观测 | 结构化日志 + `/health`、`/health/mineru` 接口 |

唯一的联网动作发生在**首次下载**阶段。下载完成后，服务启动与 OCR 调用都不再访问模型站。

> 前置依赖：CPU 环境走 `pipeline` 后端，需要 MinerU 的 pipeline 可选依赖（`pyclipper`、`omegaconf`、`dill`、`albumentations` 等）。安装脚本已改为 `mineru[pipeline]==3.1.5`；只装基础包会在 OCR 阶段报 `No module named 'pyclipper'` / `'omegaconf'`。

## 2. 目录结构

```
models/mineru/
├── mineru.json        # MinerU 工具链配置，models-dir 指向 pipeline/ 与 vlm/
├── manifest.json      # 下载清单：每个文件的 size 与 sha256
├── pipeline/          # PDF-Extract-Kit 仓库内容（models/Layout、models/OCR、...）
└── vlm/               # MinerU VLM 仓库内容（config.json、model.safetensors、tokenizer...）
```

`mineru.json` 由程序生成，不需要手工维护：

```json
{
  "models-dir": {
    "pipeline": "<绝对路径>/models/mineru/pipeline",
    "vlm": "<绝对路径>/models/mineru/vlm"
  },
  "config_version": "1.3.1"
}
```

## 3. 配置项

在 `app/config.json` 的 `mineru` 块中设置（`app/config.example.json` 已提供同样默认值）：

```json
{
  "mineru": {
    "model_source": "local",
    "local_models_dir": "../models/mineru",
    "local_config_path": "",
    "auto_download_on_startup": true,
    "auto_download_background": true,
    "download_source": "modelscope",
    "download_source_fallback": "huggingface",
    "download_endpoints": {
      "modelscope": "https://www.modelscope.cn",
      "huggingface": "https://huggingface.co"
    },
    "download_max_workers": 3,
    "download_retries": 8
  }
}
```

| 字段 | 说明 |
|---|---|
| `model_source` | `local` 表示只读本地目录；`huggingface` / `modelscope` 为原有的在线方式 |
| `local_models_dir` | 相对路径基于配置文件所在目录解析（即 `app/`），默认 `../models/mineru` |
| `local_config_path` | 留空则使用 `<local_models_dir>/mineru.json` |
| `auto_download_on_startup` | 首次启动缺少模型时是否自动下载 |
| `auto_download_background` | `true` 后台下载不阻塞启动；`false` 启动阶段同步下载 |
| `download_source` | 首选下载源，国内环境建议 `modelscope` |
| `download_source_fallback` | 首选源失败后的备用源；严格离线主机可留空 |
| `download_max_workers` / `download_retries` | 并发数与单文件重试次数 |
| `download_revisions` / `download_patterns` | 可选，覆盖仓库分支与文件匹配规则 |

> `model_source_fallback_enabled` / `fallback_model_source` 只对在线模式生效。`local` 模式下模型缺失会直接报错，不会回退到 Hugging Face。

## 4. 首次启动流程

1. 服务启动，`app_factory` 调用 `ensure_models_on_startup`。
2. `model_source != local` → 记录 `mineru_local_models_skipped` 并跳过。
3. `local` 且模型齐全 → 幂等写入 `mineru.json`，记录 `mineru_local_models_ready`。
4. `local` 且缺文件：
   - `auto_download_on_startup=false` → 打印缺失清单与手动命令，继续启动；
   - 否则启动后台线程下载，记录 `mineru_local_models_download_started`。
5. 下载完成后写入 `manifest.json` 与 `mineru.json`，并做一次深度校验。

下载细节：

- 先拉取仓库文件清单（含 size / sha256），按 `download_patterns` 过滤。
- 每个文件下载到 `<file>.part`，失败重试时带 `Range: bytes=<offset>-` 续传；服务中断后重启会继续。
- 完成后校验大小与 sha256，任一不符则删除 `.part` 重下；`manifest.json` 记录校验依据。
- 下载前检查磁盘剩余空间，不足直接报错，避免写满磁盘。

## 5. 手动部署与校验

```powershell
# 只下载缺失模型（断点续传 + 校验）
python bin/ensure_local_models.py --types mineru --download

# 只检查，不联网
python bin/ensure_local_models.py --types mineru --check-only

# 按 manifest 逐文件校验 sha256
python bin/ensure_local_models.py --types mineru --check-only --verify-sha256
```

## 6. 离线验收

### 6.1 断网条件下验证模型加载

```powershell
# 阻断出站代理并开启 HF 离线开关，跑一次真实文档
python bin/verify_mineru_local.py `
  --pdf "data/contracts/sample.pdf" `
  --output-dir "data/verify/mineru-ocr" `
  --block-network
```

预期输出 `RESULT=OK`，并给出 `pages`、`text_len`。脚本会先把出站代理指向不可用端口，因此任何外网访问都会快速失败。

### 6.2 验证服务启动

重启服务后确认日志出现：

```
mineru_models | mineru_local_models_ready root=...\models\mineru config=...\models\mineru\mineru.json kinds=['pipeline', 'vlm']
app_factory  | mineru_models_startup ok=True skipped=False root=...\models\mineru
```

同一次启动中不应出现 `mineru_local_models_download_started`。

### 6.3 健康检查

```powershell
curl --noproxy "*" http://127.0.0.1:8000/health
curl --noproxy "*" http://127.0.0.1:8000/health/mineru
curl --noproxy "*" "http://127.0.0.1:8000/health/mineru?deep=true"
```

- `/health` 的 `mineru` 块直接给出 `local_mode`、`local_ready`、`missing`、`download.stage`。
- `/health/mineru?deep=true` 会按 manifest 校验 sha256，适合巡检与故障定位（4 GB 量级约需数秒到数十秒）。

## 7. 告警与可观测

日志事件（logger 名 `law_assistant.mineru_models`）：

| 事件 | 级别 | 含义 |
|---|---|---|
| `mineru_local_models_ready` | INFO | 本地模型齐备 |
| `mineru_local_models_missing` | WARNING | 缺少模型，附缺失清单 |
| `mineru_local_models_download_started` | WARNING | 已触发自动下载 |
| `mineru_model_download_retry` | WARNING | 单文件重试（含已下载字节数） |
| `mineru_model_sha256_mismatch` | WARNING | 校验失败，将删除并重下 |
| `mineru_local_models_download_done` | WARNING | 下载完成（成功也保留 WARNING 便于检索） |
| `mineru_local_models_download_failed` | ERROR | 下载失败，含原因 |
| `mineru_local_models_unavailable` | 异常 | OCR 前校验失败，直接抛错不回退 |

建议告警规则：

- `mineru_local_models_download_failed` 出现即告警（首次部署阶段）。
- `/health/mineru` 连续返回 `status=unavailable` 时告警。
- `mineru_model_sha256_mismatch` 频繁出现说明磁盘或网络异常，需人工介入。

## 8. 异常处理矩阵

| 场景 | 行为 |
|---|---|
| 首次启动网络不可用 | 下载重试后失败，记录 ERROR；服务继续启动，OCR 时明确报错 |
| 下载中断/进程被杀 | 保留 `.part`，下次从断点续传 |
| 文件损坏（大小/sha256 不符） | 删除 `.part` 重下，超过重试次数抛 `MineruModelIntegrityError` |
| 模型目录无写权限 | 抛 `MineruModelPathError`，日志包含具体路径 |
| 磁盘空间不足 | 下载前预检，直接报错并给出所需/可用空间 |
| 模型缺失但 `model_source=local` | OCR 前抛 `mineru_local_models_unavailable`，不联网 |
| `mineru.json` 缺失 | 模型齐全时启动即自动生成 |
| 下载源不可用 | 按 `download_source_fallback` 切换；无备用源则失败 |

## 9. 离线交付注意

- `models/` 在 `.gitignore` 中，**模型权重不入库**。交付到内网时需单独打包 `models/mineru`（约 4 GB）随安装包分发，或在内网准备一次可访问模型站的初始化窗口。
- `mineru.json` 内是绝对路径，跨机器复制后由程序按 `local_models_dir` 自动重写；如手工拷贝，务必确认 `models-dir` 指向新机器上的实际路径。
- 严格离线主机建议把 `download_source_fallback` 置空，避免初始化阶段无谓等待。

## 10. 验收记录（本机实测）

环境：Windows + CPU（torch 2.11.0+cpu）、MinerU 3.1.5、`models/mineru` 约 4.01 GB（pipeline 39 个文件 + vlm 13 个文件）。

| 项目 | 命令/入口 | 结果 |
|---|---|---|
| 模型完整性 | `inspect_local_models(deep=True, verify_sha256=True)` | `ok=true`，`missing=[]` |
| 重启加载 | 重启后端，`HF_HUB_OFFLINE=1` | 日志出现 `mineru_local_models_ready`，无下载事件 |
| 健康检查 | `GET /health`、`GET /health/mineru` | `local_ready=true`，`status=ok` |
| 离线 OCR | `verify_mineru_local.py --block-network` | 7 页、5169 字符、94.5 s，`RESULT=OK` |
| 单元测试 | `pytest tests/test_mineru_models.py tests/test_health_mineru.py ...` | 42 passed |

离线 OCR 产物（markdown / middle.json / content_list.json / model.json）落在 `--output-dir` 下，可直接抽样核对识别质量。

## 11. 常见问题

**Q：启动日志一直是 `skipped`？**
`model_source` 不是 `local`。检查 `app/config.json` 的 `mineru.model_source`。

**Q：`/health/mineru` 返回 `unavailable` 但文件看着都在？**
看 `missing` 字段。`formula` / `table` 为 `true` 时要求公式、表格模型齐备；关闭这两个开关可以只部署必需子集。

**Q：OCR 报 `mineru_local_models_unavailable`？**
本地目录与 `required_kinds` 不匹配（`mode=auto` 时 pipeline 与 vlm 都要）。按第 5 节补齐或调整 `mode`。

**Q：下载很慢？**
调小 `download_max_workers`（部分 CDN 对并发敏感），或换 `download_source`；已下载部分会保留，重跑不会从零开始。

**Q：机器装了独显，为什么还是走 pipeline 后端？**
后端选择看的是 `torch.cuda.is_available()`，而不是 `nvidia-smi` 是否存在。CPU 版 torch 配独显时会被判定为无可用 GPU，自动选择 pipeline；若要启用 VLM/hybrid 推理，需要安装 CUDA 版 torch 并补齐 `accelerate`，此时日志会出现 `mineru_gpu_probe driver_present=True torch_cuda=False` 之外的正常探测记录。
