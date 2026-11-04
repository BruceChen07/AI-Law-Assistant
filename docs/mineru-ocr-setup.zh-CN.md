# MinerU OCR 依赖与排障

## 症状

上传扫描版 PDF 后审计失败，前端提示：

> OCR 解析失败，未能正确抽取 PDF 文本，审计流程已中止。请检查 OCR 依赖（如 torchvision）和 MinerU 配置后重试。

后端日志里能看到 MinerU 子进程返回非 0：

```
subprocess.CalledProcessError: Command '[...\app\.venv\Scripts\mineru.exe', '-p', ...]' returned non-zero exit status 1
```

注意提示里的 "torchvision" 只是错误文案的泛指。真正的原因通常是 **MinerU 的 pipeline 可选依赖缺失**（`pyclipper` / `omegaconf` / `dill` 等），而 torchvision 本身是装着的。

## 为什么会出现

`mineru` 基础包不含 pipeline 后端的依赖，它们属于 `mineru[pipeline]` extra。本项目通过子进程调用 `mineru.exe`，而该可执行文件绑定**它自己所在虚拟环境的 Python**：

- 服务进程用哪个 Python 启动，不影响 MinerU 用哪个解释器；
- `app/core/mineru_ocr.py` 的 `_candidate_mineru_paths()` 会先看当前解释器目录，再回退到 PATH；
- 仓库里同时存在 `app/venv` 与 `app/.venv`，极易装错一边。

所以典型情况是：服务跑在系统 Python 上，MinerU 却解析到 `app/.venv`，而那个环境只装了 `mineru` 基础包。

## 修复

先确认 MinerU 实际用的是哪个解释器（看报错里的 `mineru.exe` 路径），再给对应环境补依赖：

```powershell
# 假设报错指向 app\.venv
app\.venv\Scripts\python.exe -m pip install "pyclipper>=1.3.0" "omegaconf>=2.3,<3" "dill>=0.3.8,<1"
```

或者直接装 extras：

```powershell
app\.venv\Scripts\python.exe -m pip install "mineru[pipeline]==3.1.5"
```

验证：

```powershell
app\.venv\Scripts\python.exe -c "import pyclipper, omegaconf, dill; print('ok')"
```

补装后**不需要重启后端**：每次审计都会新起 MinerU 子进程，新依赖立即生效。

## 长期防复发

- `app/requirements.txt` 已声明 `pyclipper` / `omegaconf` / `dill`，重建环境时请用该文件安装；
- 部署时尽量只用一套虚拟环境，避免 `app/venv` 与 `app/.venv` 混用；
- 更换机器后先跑一次扫描件 PDF 的抽取验证，再批量审计。

## 启动自检与体检命令

服务启动时会自动做一次依赖自检（`app/core/dependency_preflight.py`），检查：

| 检查项 | 说明 | 不通过时 |
|---|---|---|
| 服务依赖 | 服务进程自身懒加载的包（onnxruntime/jieba/numpy/httpx） | 阻塞 |
| MinerU 命令行 | 能否找到并执行 `mineru` | 阻塞 |
| MinerU 运行环境 | **MinerU 所在解释器**是否装了 `pyclipper`/`omegaconf`/`dill` | 阻塞 |
| MinerU 本地模型 | `model_source=local` 时权重是否完整 | 阻塞 |
| 向量模型 | `embedding_profiles` 指向的文件是否存在 | 阻塞 |
| LLM 服务 | `llm_config.api_base` 是否可达 | 仅告警 |

不通过时会在控制台打印一段可读报告（含修复命令），日志里记 `startup_dependency_check` / `startup_dependency_issue`，同时 `/health` 返回：

```json
{ "preflight": { "ok": false, "critical_count": 1, "warning_count": 1, "issues": ["mineru_env"] } }
```

也可以不启动服务、单独体检：

```powershell
python bin/doctor.py          # 人类可读，关键项失败时退出码 1
python bin/doctor.py --json   # 机器可读
```

相关配置：

```json
{
  "startup_dependency_check": true,
  "startup_dependency_strict": false
}
```

`startup_dependency_strict=true` 时，关键项不通过会直接中止启动（适合部署流水线），默认只告警并继续启动。
