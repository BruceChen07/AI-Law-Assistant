# MinerU 轻量化部署验证报告

## 1. 目标

验证当前仓库已从混合 OCR 方案切换为 `mineru-only` 原生部署方案，并确认轻量化目标已落地。

## 2. 代码改造结论

- `app/core/ocr.py`
  - 移除 `TesseractEngine` 默认装配
  - OCR 管理器统一收口到 `ocr_document`
  - 默认自动补充 `mineru` 引擎

- `app/core/mineru_ocr.py`
  - 新增 `ocr_document()`
  - 图片文件先转临时 PDF，再复用 MinerU 抽取

- `app/services/tax_parser.py`
  - 删除 `pytesseract` 直连分支
  - 图片 OCR 统一走 `OCREngineManager`

- `app/services/contract_preview_assets.py`
  - 去除 `pdf2image` 导入
  - PDF 栅格预览默认禁用，自动回退文本预览

- `app/requirements.txt`
  - 删除 `pytesseract`
  - 删除 `pdf2image`
  - 保留 `mineru`、`pillow`、`pypdf`

- `app/config.example.json`
  - 默认 OCR 引擎改为 `mineru`
  - `pdf/image` 路由统一指向 `mineru`

## 3. 轻量化结果

已删除的冗余链路：

- `tesseract`
- `pytesseract`
- `pdf2image`
- `poppler`

保留的核心链路：

- `mineru`
- `pillow`
- `pypdf`

预期收益：

- 减少系统级安装项
- 降低企业桌面环境对白名单软件的依赖
- 降低 OCR 维护复杂度
- 将 OCR 入口统一为单引擎，简化排障

## 4. 验证项

### 4.1 静态检查

- Python 语法校验：应通过
- VS Code Diagnostics：应无新增错误

### 4.2 单元测试覆盖点

- `tests/test_ocr_manager.py`
  - 校验引擎选择与依赖检测口径已切为 `mineru`

- `tests/test_tax_parser.py`
  - 校验图片 OCR 已改为走统一 OCR 管理器

- `tests/test_contract_preview_assets.py`
  - 校验无 PDF 栅格后端时可回退文本预览

## 5. 运行时行为变化

- 扫描 PDF：继续可走 OCR，但引擎仅为 `mineru`
- 图片法规/图片合同：通过“图片转临时 PDF”后交给 `mineru`
- PDF 视觉预览：在无额外渲染依赖时不再生成图片页，降级为文本预览
- DOCX 预览：保留现有纯 Python 栅格渲染回退能力

## 6. 风险与边界

- 当前轻量化方案优先保证“文本可抽取”，不保证 PDF 原图视觉预览
- `mineru` 仍可能需要模型缓存；企业内网需准备镜像或离线包
- 若未来业务要求高保真 PDF 视觉预览，需要单独补一个轻量渲染后端

## 7. 建议结论

- 若企业目标是“最少系统安装 + 原生部署 + OCR 可用”，当前 `mineru-only` 方案可作为默认交付
- 若企业未来要求高保真页图预览，应在不恢复 `tesseract` 的前提下补单独渲染能力
