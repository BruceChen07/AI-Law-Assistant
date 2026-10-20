# 当前项目 PDF 模块梳理

更新时间：2026-10-15

## 1. 功能范围

当前项目中与 PDF 相关的能力主要分布在以下模块：

1. `app/core/utils.py`
   - 基础 PDF 文本抽取入口
   - 负责 `extract_text_with_config`
   - 对外输出：`text + meta`

2. `app/core/ocr.py`
   - OCR 引擎调度器
   - 按配置选择 `mineru` 等 OCR 引擎
   - 对外输出：`ocr_text + page_count + engine`

3. `app/core/mineru_ocr.py`
   - MinerU OCR / 版面解析执行器
   - 负责调用本地 MinerU CLI
   - 对外输出：Markdown、`middle.json`、`content_list.json` 等产物

4. `app/services/document_digitization.py`
   - PDF -> OCR/版面解析 -> DOCX 重建
   - 对外输出：数字化 DOCX、匹配率、段落/表格/图片统计

5. `app/services/contract_preview_assets.py`
   - 合同预览构建
   - 负责页面预览、文本块、坐标映射、缓存
   - 对外输出：预览 manifest、页面图片、文本块坐标

6. `app/api/routers/contracts.py`
   - 合同上传、审计、预览、导出
   - 负责将 PDF 模块与业务流程串联

## 2. 输入输出规范

### 2.1 文本抽取

- 输入：
  - `cfg`
  - `path`
- 输出：
  - `text: str`
  - `meta: { ext, ocr_used, page_count, ocr_engine, ... }`

### 2.2 数字化

- 输入：
  - `document_id`
  - `source_file_path`
  - `original_filename`
- 输出：
  - `digitized_docx_path`
  - `status`
  - `text_match_ratio`
  - `paragraph_count / table_count / image_count / page_count`

### 2.3 预览

- 输入：
  - `document_id`
  - `file_path`
  - `mime_type`
- 输出：
  - `manifest`
  - `pages`
  - `blocks`
  - `image_api`

## 3. 依赖组件

1. `pypdf`
   - PDF 文本层读取

2. `mineru`
   - 扫描件 OCR、版面识别、数字化 DOCX 重建基础

3. `python-docx`
   - DOCX 文本提取与数字化结果重建

4. `Pillow`
   - 预览图、文档页面渲染辅助

5. `pypdfium2`
   - 新版统一管线引入，用于 PDF 页面光栅预览

## 4. 与业务模块关联

1. 合同审计
   - `app/services/contract_audit.py`
   - `app/api/routers/contracts.py`

2. 税务法规解析
   - `app/services/tax_parser.py`

3. 税务合同解析
   - `app/services/tax_contract_parser.py`

4. 向量迁移 / 导入
   - `app/services/importer.py`
   - `app/services/vector_store_migration.py`

## 5. 当前问题

1. PDF 能力分散，缺少统一服务边界
2. 文本抽取、OCR、数字化、预览各自维护元数据
3. 预览与审计的 PDF 处理策略不一致
4. 旧逻辑对扫描版 PDF 的路径依赖较强，不易切换
5. 缺少统一能力摘要接口，不利于前后端和 AI agent 协同
