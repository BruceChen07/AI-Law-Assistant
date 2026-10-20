# PDF 管线改造进度清单

更新时间：2026-10-15

## 已完成

- [x] 梳理当前项目 PDF 模块范围、输入输出、依赖与业务关联
- [x] 研读目标文档并抽取核心需求
- [x] 新建统一后端服务 `pdf_pipeline`
- [x] 新建共享文本抽取器 `document_text_extractors`
- [x] 将 `extract_text_with_config` 接入统一 PDF 管线
- [x] 为合同审计新增 `pdf_pipeline summary` 能力摘要接口
- [x] 预留 `pdf_pipeline` 运行时配置项
- [x] 将合同审计 PDF 输入选择收口到统一 helper
- [x] 为合同原件/数字化稿补充后端下载接口
- [x] 将前端 PDF 预览工具栏补齐为缩略图、分页/连续、缩放、页跳转、原件下载

## 进行中

- [x] 将 PDF 页面预览完全切到统一渲染链路
- [x] 前端展示 PDF 管线能力摘要与运行态信息
- [x] 增补单元测试、集成测试、前端测试
- [ ] 更新切换说明与最终清理文档

## 待完成

- [ ] 完成新旧模块切换说明
- [ ] 清理旧的冗余 PDF 逻辑
- [ ] 进行一次完整回归验证

## 接手说明

后续 AI agent 优先从以下文件继续：

1. `app/services/pdf_pipeline.py`
2. `app/services/contract_preview_assets.py`
3. `app/api/routers/contracts.py`
4. `web/App.jsx`
5. `docs/pdf-pipeline/02-implementation-stages.zh-CN.md`
