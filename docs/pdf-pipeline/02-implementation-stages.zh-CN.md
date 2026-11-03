# PDF 管线多阶段实施文档

更新时间：2026-10-15

## 阶段 1：统一服务边界

目标：

1. 收口 PDF 文本抽取、OCR、数字化、预览能力
2. 建立统一 `pdf_pipeline` 服务
3. 让旧业务模块通过兼容适配层继续工作

交付物：

1. `app/services/pdf_pipeline.py`
2. `app/services/document_text_extractors.py`
3. `GET /contracts/pdf-pipeline/summary`

## 阶段 2：后端业务接线

目标：

1. 将 `extract_text_with_config` 接入统一管线
2. 将合同审计 PDF 路径改为受 `pdf_pipeline` 配置控制
3. 将 PDF 预览改为走可用的页面渲染能力

交付物：

1. `app/core/utils.py`
2. `app/api/routers/contracts.py`
3. `app/services/contract_preview_assets.py`

## 阶段 3：前端能力对齐

目标：

1. 展示 PDF 管线能力摘要
2. 保持上传、预览、导出体验稳定
3. 对数字化/预览/路由元信息做可视化呈现

交付物：

1. `web/api/contracts.js`
2. `web/PdfPipelinePanel.jsx`
3. `web/types/pdf.ts`
4. `web/App.jsx`

## 阶段 4：测试与切换

目标：

1. 补齐单元测试
2. 补齐集成测试
3. 补齐前端组件测试
4. 更新配置与技术文档

交付物：

1. `tests/test_pdf_pipeline.py`
2. `tests/test_contracts_pdf_pipeline_summary.py`
3. `web/types/pdf.test.ts`
4. `README.zh-CN.md` / `Configuration Reference.md`

## 阶段 5：后续优化项

1. 进一步接近页面级 text/scanned 路由
2. 接入更丰富的图表/图片块元数据
3. 为大 PDF 增加分段预渲染和后台任务化能力
