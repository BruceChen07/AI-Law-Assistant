# MinerU Hugging Face Fallback to ModelScope 设计与验证报告

## 1. 背景

当前项目在 PDF 数字化链路中通过 [`app/core/mineru_ocr.py`](file:///d:/Workspace/AI-Law-Assistant/app/core/mineru_ocr.py) 调用 `mineru.exe`。当 MinerU 运行时需要从 Hugging Face 读取模型缓存或远端资源时，网络抖动、DNS 失败、429/5xx 等问题可能导致整个抽取流程直接失败。

本次改造目标是：

- 保持现有上层业务接口不变
- 在 Hugging Face 网络异常时自动切换到 ModelScope
- 允许通过配置控制是否启用 fallback、重试次数和超时
- 为 CI / 单测提供可验证的失败模拟用例

## 2. Hugging Face 相关触点梳理

### 2.1 运行时主链路

1. [`app/core/mineru_ocr.py`](file:///d:/Workspace/AI-Law-Assistant/app/core/mineru_ocr.py)
   - 调用 `mineru.exe`
   - 通过环境变量 `MINERU_MODEL_SOURCE` 指定模型源
   - 新增 Hugging Face 网络异常识别、重试和 ModelScope fallback

2. [`app/services/document_digitization.py`](file:///d:/Workspace/AI-Law-Assistant/app/services/document_digitization.py)
   - 调用 `run_mineru_extract`
   - 不直接感知模型源切换

3. [`app/services/contract_preview_assets.py`](file:///d:/Workspace/AI-Law-Assistant/app/services/contract_preview_assets.py)
   - 调用 `run_mineru_extract`
   - 不直接感知模型源切换

### 2.2 启动预检 / 下载链路

1. [`bin/ensure_local_models.py`](file:///d:/Workspace/AI-Law-Assistant/bin/ensure_local_models.py)
   - 翻译模型下载仍直接使用 `huggingface_hub.snapshot_download`
   - 嵌入和 reranker 下载使用 ModelScope
   - 该脚本不是本次 MinerU 运行时 fallback 的核心入口，但属于仓库内 Hugging Face 触点

2. 已安装 MinerU 包内部
   - `mineru.utils.models_download_utils`
   - `mineru.cli.models_download`
   - 内置支持 `huggingface` / `modelscope`

## 3. ModelScope 资源映射

本次没有自定义一套资源表，而是复用 MinerU 官方包内置映射：

| 模型用途 | Hugging Face | ModelScope |
|---|---|---|
| Pipeline 主仓库 | `opendatalab/PDF-Extract-Kit-1.0` | `OpenDataLab/PDF-Extract-Kit-1.0` |
| VLM 主仓库 | `opendatalab/MinerU2.5-Pro-2604-1.2B` | `OpenDataLab/MinerU2.5-Pro-2604-1.2B` |

上述映射来自已安装包：

- `mineru.utils.enum_class.ModelPath`
- `mineru.utils.models_download_utils.auto_download_and_get_model_root_path`

这意味着 fallback 到 ModelScope 时，模型目录结构、相对资源路径、配置文件布局与 MinerU 官方逻辑保持一致，对上层业务透明。

## 4. 方案设计

### 4.1 运行时策略

在 [`app/core/mineru_ocr.py`](file:///d:/Workspace/AI-Law-Assistant/app/core/mineru_ocr.py) 中新增：

1. Hugging Face 网络异常特征识别
   - `ConnectionError`
   - `ConnectTimeout`
   - `ReadTimeout`
   - `HTTPSConnectionPool`
   - `getaddrinfo failed`
   - `connection refused`
   - `429 / Too Many Requests`
   - `502 / 503 / 504`

2. 模型源尝试顺序构建
   - 默认：`huggingface -> huggingface(retry) -> modelscope`
   - 非 Hugging Face 主源时，不做额外源切换

3. 运行时环境注入
   - `MINERU_MODEL_SOURCE`
   - `HF_HUB_ETAG_TIMEOUT`
   - `HF_HUB_DOWNLOAD_TIMEOUT`

### 4.2 配置项

已新增到以下配置入口：

- [`app/config.json`](file:///d:/Workspace/AI-Law-Assistant/app/config.json)
- [`app/config.example.json`](file:///d:/Workspace/AI-Law-Assistant/app/config.example.json)
- [`bin/deploy_local.py`](file:///d:/Workspace/AI-Law-Assistant/bin/deploy_local.py)
- [`docs/Configuration Reference.md`](file:///d:/Workspace/AI-Law-Assistant/docs/Configuration%20Reference.md)

新增字段：

```json
{
  "mineru": {
    "model_source": "huggingface",
    "model_source_fallback_enabled": true,
    "fallback_model_source": "modelscope",
    "hf_retry_count": 1,
    "hf_connect_timeout_sec": 10,
    "hf_download_timeout_sec": 30
  }
}
```

## 5. 验证设计

### 5.1 已实现自动化测试

测试文件：[`tests/test_ocr_manager.py`](file:///d:/Workspace/AI-Law-Assistant/tests/test_ocr_manager.py)

覆盖场景：

1. Hugging Face 主源 + 重试 + ModelScope fallback 尝试序列构建
2. 典型 Hugging Face 网络异常字符串识别
3. 当 Hugging Face 返回网络异常时，自动切换到 ModelScope 并成功产生产物
4. 当错误并非网络异常时，不错误触发 ModelScope fallback

### 5.2 本次实际验证边界

本次在当前 Windows 开发环境中完成了：

- 代码级改造
- 目标单测执行
- 已安装 MinerU 包的官方映射取证

本次**未在当前会话中直接完成**以下真实环境验证，因此不能宣称已经达到 99.9% 生产级切换成功率：

- 真实断网 / DNS 污染 / 代理拒绝下的 MinerU 端到端实机测试
- Linux / macOS 多系统实机回归
- 对比 Hugging Face 与 ModelScope 产物在大样本合同上的一致性统计

## 6. 建议的后续验证

1. 在 Windows 本机执行：

```powershell
pytest tests/test_ocr_manager.py -q
```

2. 在一份真实 PDF 上执行两组对照：
   - `model_source = huggingface`
   - `model_source = huggingface` 且人为阻断 `huggingface.co`，观察是否自动回退到 `modelscope`

3. 记录两组输出：
   - `_content_list.json`
   - `_middle.json`
   - `.md`
   - 页面数 / 文本长度 / 表格数 / 耗时

4. 若需要严格生产兜底，建议再补：
   - 本地预下载 ModelScope pipeline 模型
   - 启动前缓存完整性预检
   - 失败时把 MinerU 原始 stdout/stderr 单独归档到专用日志文件

## 7. 结论

这次改造已经把 Hugging Face 偶发网络异常从“直接失败”提升为“可重试、可自动切换到 ModelScope 的受控失败路径”，并且不需要上层合同审计、预览、数字化流程做额外适配。

目前这套方案适合作为项目默认容错基线；若要把可靠性进一步拉高到强生产标准，还需要补做真实网络故障注入和跨系统回归。
