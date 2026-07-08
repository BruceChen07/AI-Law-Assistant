# MinerU 原生 OCR 安装与排障指南

## 适用范围
- 目标方案：`mineru-only`
- 运行方式：原生安装，不使用 Docker
- 作用范围：PDF OCR、图片 OCR、合同文本回退抽取

## 核心依赖
- Python 3.12+
- `mineru==3.1.5`
- `pillow>=11.0.0`
- `pypdf>=5.6.0`

说明：
- 当前项目已移除 `tesseract`、`pytesseract`、`pdf2image`、`poppler` 的运行时强依赖
- 图片文件会先转换成临时 PDF，再统一交由 MinerU 提取
- PDF 视觉预览在没有额外栅格化依赖时会自动降级为文本预览

## Windows 安装
```bat
bin\install_ocr_windows.bat
```

## macOS 安装
```bash
bash bin/install_ocr_macos.sh
```

## Linux 安装
```bash
bash bin/install_ocr_deps.sh
```

## OCR 配置
在 `app/config.json` 中配置为：

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
  },
  "mineru": {
    "mode": "auto",
    "fallback_backends": ["pipeline"],
    "method": "auto",
    "device": "cpu",
    "formula": false,
    "table": false,
    "model_source": "huggingface",
    "timeout": 900
  }
}
```

## 验证命令
```bash
python bin/verify_ocr_env.py --pdf /path/to/sample.pdf --output reports/ocr_report.json
```

## 常见问题
1. `mineru` 命令找不到
- 确认当前终端已激活虚拟环境，或将虚拟环境的 `Scripts/bin` 加入 `PATH`

2. 图片 OCR 没有结果
- 检查 `Pillow` 是否安装成功
- 检查图片是否能正常转换为 PDF

3. PDF 预览不是图片模式
- 这是 `mineru-only` 轻量部署下的预期行为
- 当前方案优先保证文本抽取与坐标分析，不再强依赖系统级 PDF 栅格化工具

4. MinerU 下载模型失败
- 检查网络或内网镜像
- 企业环境建议预置离线 wheel 包和模型缓存
