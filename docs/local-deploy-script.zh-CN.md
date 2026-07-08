# 本地自动化部署脚本说明

本文档说明如何使用 `bin/deploy_local.py` 在本机完成 AI Law Assistant 的完整本地部署，包括环境预检、系统依赖安装、虚拟环境创建、Python 与前端依赖安装、本地模型准备、配置写入、服务启动与连通性验证。

## 1. 适用范围

- 目标系统：Windows / Linux / macOS
- 目标模式：本地能力完整可用
- 默认运行栈：
  - 后端：FastAPI + Uvicorn
  - 前端：Vite
  - 本地 LLM：Ollama
  - OCR：MinerU 原生方案
  - 向量 / 重排 / 翻译模型：`ensure_local_models.py` 自动准备

## 2. 前置要求

### 2.1 软件版本

| 项 | 最低要求 | 用途 | 获取渠道 |
|---|---|---|---|
| Python | 3.12+ | 后端运行、虚拟环境 | python.org / winget / homebrew / 系统包管理器 |
| Node.js | 22+ | 前端依赖安装与开发服务 | nodejs.org / winget / homebrew / NodeSource |
| Ollama | 0.31+ | 本地大模型运行时 | winget / brew / 官方安装脚本 |
| MinerU | 3.1.5 | PDF / 图片 OCR 唯一引擎 | PyPI / 企业镜像源 |

### 2.2 硬件建议

| 档位 | 适用场景 | CPU | 内存 | 存储 | 主模型 |
|---|---|---|---|---|---|
| `lite` | 资源受限开发机 | 8 物理核 | 32 GB | 200 GB+ 可用空间 | `qwen3.5:9b` |
| `balanced` | 常规开发 / 联调 | 12-16 物理核 | 48-64 GB | 300 GB+ 可用空间 | `qwen2.5-coder:14b` |
| `full` | 完整本地能力验证 | 16+ 物理核 | 64 GB+ | 400 GB+ 可用空间 | `qwen3.6:27b` |

说明：

- 轻量侧车模型默认使用 `llama3.2:3b`
- 脚本 `--profile auto` 会根据本机内存与物理核心自动选择 `lite / balanced / full`
- 当前项目要求记忆模块保持关闭，部署脚本会自动写入 `classic` 回退配置

## 3. 脚本能力

`bin/deploy_local.py` 会按顺序执行以下工作：

1. 识别当前操作系统、Python 版本、Node 版本、CPU、内存、磁盘、GPU 信息
2. 生成缺失项清单，标记当前未就绪的系统依赖与模型资源
3. 自动安装 MinerU、Node.js、Ollama 等核心依赖
4. 创建 `app/.venv` 虚拟环境并安装 `app/requirements.txt`
5. 安装 `web` 前端依赖
6. 自动生成或更新 `app/config.json`
7. 自动关闭 memory 模块，开启本地 Ollama 路由，按需开启本地翻译模型
8. 下载 embedding / reranker / translation 模型
9. 拉起 Ollama 并下载主模型、轻量模型
10. 启动前后端服务并验证：
   - `http://127.0.0.1:8000/health`
   - `http://127.0.0.1:5173`
11. 输出部署报告到 `.runtime/reports/local_deploy_report.json`

## 4. 快速开始

### 4.1 仅检查当前机器缺失项

```bash
python .\bin\deploy_local.py --check-only
```

执行后会输出：

- 当前机器的环境信息
- 当前缺失项清单
- 推荐模型档位
- JSON 报告路径

### 4.2 一键完整部署

```bash
python .\bin\deploy_local.py --profile auto --keep-running
```

推荐含义：

- `--profile auto`：按硬件自动选型
- `--keep-running`：部署通过后保持前后端继续运行

### 4.3 指定完整能力档

```bash
python .\bin\deploy_local.py --profile full --keep-running
```

适合 64 GB 以上内存且希望启用较完整本地能力的机器。

### 4.4 指定轻量档

```bash
python .\bin\deploy_local.py --profile lite --keep-running
```

适合开发机资源有限但仍希望完成联调。

## 5. 常用参数

| 参数 | 说明 |
|---|---|
| `--profile auto|full|balanced|lite` | 模型规格策略 |
| `--main-model <tag>` | 手动覆盖主模型，例如 `qwen3.5:9b` |
| `--small-model <tag>` | 手动覆盖轻量模型，例如 `llama3.2:3b` |
| `--backend-port <port>` | 指定后端端口，默认 `8000` |
| `--frontend-port <port>` | 指定前端端口，默认 `5173` |
| `--check-only` | 只检查，不安装 |
| `--skip-system-deps` | 跳过系统依赖安装 |
| `--skip-python-deps` | 跳过 Python 依赖安装 |
| `--skip-frontend-deps` | 跳过前端依赖安装 |
| `--skip-models` | 跳过模型准备 |
| `--skip-start` | 跳过服务启动验证 |
| `--disable-translation` | 不下载、不启用本地翻译模型 |
| `--keep-running` | 验证完成后保持服务运行 |
| `--config-path <path>` | 自定义配置文件路径 |
| `--venv-dir <path>` | 自定义虚拟环境路径 |
| `--report-json <path>` | 自定义部署报告输出路径 |

## 6. 典型场景

### 6.1 机器上已有 Python/Node/Ollama，只想补模型和配置

```bash
python .\bin\deploy_local.py --skip-system-deps --keep-running
```

### 6.2 已手动安装依赖，只想做最终验通

```bash
python .\bin\deploy_local.py --skip-system-deps --skip-python-deps --skip-frontend-deps
```

### 6.3 只想生成配置，不下载翻译模型

```bash
python .\bin\deploy_local.py --disable-translation --skip-start
```

## 7. 生成的关键产物

| 路径 | 说明 |
|---|---|
| `app/config.json` | 最终生效配置 |
| `app/.venv` | 后端虚拟环境 |
| `.runtime/reports/local_deploy_report.json` | 部署报告 |
| `.runtime/logs/backend.log` | 后端启动日志 |
| `.runtime/logs/frontend.log` | 前端启动日志 |
| `.runtime/services.pids.json` | 前后端 PID 记录 |

## 8. 启动与停止

如果部署命令带了 `--keep-running`，服务会保持运行：

- 后端：`http://127.0.0.1:8000`
- 前端：`http://127.0.0.1:5173`

停止服务：

```bash
python .\bin\stop-services.py
```

## 9. 部署结果验证

脚本会自动检查以下地址：

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:5173`

你也可以手动复查：

```bash
curl http://127.0.0.1:8000/health
```

前端可直接浏览器访问：

- [http://127.0.0.1:5173](http://127.0.0.1:5173)

## 10. 常见问题排查

### 10.1 Python 版本不足

现象：

- 脚本提示 `需 3.12+`

处理：

- 安装 Python 3.12 或更高版本
- 重新打开终端后再次执行脚本

### 10.2 Node.js 版本不足

现象：

- 脚本提示 `Node.js 版本仍未达到 22+`

处理：

- Windows：优先使用 `winget`
- macOS：优先使用 `brew install node@22`
- Linux：建议使用 NodeSource 22.x

### 10.3 MinerU 缺失

现象：

- OCR 相关依赖检测失败
- 启动后扫描 PDF 处理异常

处理：

- Windows：脚本会调用 `bin/install_ocr_windows.bat`
- macOS：脚本会调用 `bin/install_ocr_macos.sh`
- Linux：脚本会调用 `bin/install_ocr_deps.sh`

### 10.4 MinerU 已安装但命令不可用

现象：

- `mineru --version` 无法执行

处理：

- 激活虚拟环境后重新打开终端
- 检查虚拟环境 `Scripts/bin` 是否在 `PATH`
- 确认 `python -m pip show mineru` 可见

### 10.5 Ollama 已安装但命令不可用

现象：

- `ollama --version` 无法执行

处理：

- 重新打开终端
- 检查 PATH
- Windows 常见路径：`C:\Program Files\Ollama\ollama.exe`

### 10.6 模型下载慢或失败

现象：

- `ensure_local_models.py` 或 `ollama pull` 失败

处理：

- 检查网络访问
- 再次运行部署脚本即可续传
- 先单独验证：

```bash
python .\bin\ensure_local_models.py --types all --include-optional
python .\bin\download-local-llm-models.py
```

### 10.7 27B 主模型过慢

现象：

- 启动成功，但合同审计响应过慢

处理：

- 改用更轻的主模型：

```bash
python .\bin\deploy_local.py --main-model qwen3.5:9b --small-model llama3.2:3b --keep-running
```

或直接：

```bash
python .\bin\deploy_local.py --profile lite --keep-running
```

## 11. 推荐执行顺序

对于第一次部署，建议按下面顺序：

1. `python .\bin\deploy_local.py --check-only`
2. 查看 `.runtime/reports/local_deploy_report.json`
3. 执行 `python .\bin\deploy_local.py --profile auto --keep-running`
4. 打开前端页面验证上传、审计、OCR 与检索功能
5. 使用 `python .\bin\stop-services.py` 停止服务

## 12. 补充说明

- 脚本会强制把 memory 审计切回 `classic`，这是当前项目约束
- 脚本会把 OCR 路由强制切到 `mineru-only`
- 轻量化部署下，PDF 视觉预览允许降级为文本预览，这是为规避 `poppler/pdf2image` 带来的额外系统依赖
- 若你只希望快速跑通，可用 `--profile lite`
- 若你希望尽量贴近完整本地能力，优先使用 `--profile full`
- 建议保留 `.runtime/reports/local_deploy_report.json`，便于后续问题排查与反馈
