# MinerU 原生部署手册

## 1. 目标

本手册面向不支持 Docker、且需要规避 `tesseract/poppler` 系统级安装的企业环境，说明如何以 `mineru-only` 方式完成项目部署。

## 2. 方案说明

- OCR 唯一引擎：`mineru`
- 删除的运行时依赖：`tesseract`、`pytesseract`、`pdf2image`、`poppler`
- 图片 OCR 路径：图片文件先转换为临时 PDF，再交由 `mineru` 统一抽取
- PDF 视觉预览：在无额外栅格化依赖时自动回退为文本预览

## 3. 前置要求

- Python 3.12+
- Node.js 22+
- Ollama 0.31+
- 网络可访问 PyPI 或企业镜像源，或已准备 `mineru` 离线 wheel 包

## 4. 安装步骤

### 4.1 安装 Python 依赖

```bash
python -m venv app/.venv
app/.venv/Scripts/python -m pip install -U pip setuptools wheel
app/.venv/Scripts/python -m pip install -r app/requirements.txt
```

Windows PowerShell 可用：

```powershell
.\app\.venv\Scripts\python.exe -m pip install -r .\app\requirements.txt
```

### 4.2 安装 MinerU OCR 依赖

Windows：

```bat
bin\install_ocr_windows.bat
```

macOS：

```bash
bash bin/install_ocr_macos.sh
```

Linux：

```bash
bash bin/install_ocr_deps.sh
```

### 4.3 初始化配置

```powershell
Copy-Item .\app\config.example.json .\app\config.json
```

确保以下配置生效：

```json
{
  "ocr_engine": "mineru",
  "ocr_engine_order": ["mineru"],
  "ocr_engine_by_type": {
    "pdf": "mineru",
    "image": "mineru"
  },
  "ocr_engines": {
    "mineru": {
      "module": "app.core.mineru_ocr",
      "function": "ocr_document"
    }
  }
}
```

### 4.4 启动服务

```powershell
python .\bin\deploy_local.py --profile lite --disable-translation --keep-running
```

## 5. 验证步骤

### 5.1 OCR 依赖验证

```powershell
python .\bin\verify_ocr_env.py --output .\reports\ocr_report.json
```

预期结果：

- `dependencies.mineru.module = true`
- `dependencies.mineru.cli = true`
- `ocr_stack = "mineru_only"`

### 5.2 服务验证

```powershell
curl http://127.0.0.1:8000/health
```

浏览器访问：

- `http://127.0.0.1:5173`

### 5.3 业务验证

- 上传扫描 PDF，确认合同文本可抽取
- 上传图片法规，确认税务规则可解析
- 打开合同预览，确认至少可进入文本预览模式

## 6. 常见问题

### 6.1 mineru 命令找不到

- 激活虚拟环境后重试
- 检查虚拟环境的 `Scripts/bin` 是否在 `PATH`

### 6.2 预览不是图片模式

- 这是 `mineru-only` 轻量方案的预期结果
- 当前方案以文本抽取和 OCR 可用性优先，不再依赖 `pdf2image/poppler`

### 6.3 图片 OCR 失败

- 检查 `Pillow` 是否正常安装
- 检查图片文件是否损坏，是否可转换为 PDF

## 7. 建议

- 开发机优先使用 `--profile lite`
- 企业内网优先准备 `mineru` 的离线包和缓存
- 若后续需要恢复高保真图片预览，可单独评估引入轻量渲染组件，但不建议重新引入 `tesseract` 链路
