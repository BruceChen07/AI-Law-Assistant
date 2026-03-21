# 合同预览性能基准测试报告（评审版）

## 1. 基线对象
- 前端分页实现：[App.jsx:L789-L845](file:///d:/Workspace/AI-Law-Assistant/web/App.jsx#L789-L845)
- 预览加载策略：[App.jsx:L473-L533](file:///d:/Workspace/AI-Law-Assistant/web/App.jsx#L473-L533)
- 后端资产生成：[contract_preview_assets.py:L302-L446](file:///d:/Workspace/AI-Law-Assistant/app/services/contract_preview_assets.py#L302-L446)

## 2. 当前基线（代码评估）
- 仅主图单页渲染，DOM压力低，但跨页阅读割裂
- 默认仅预加载后续3页
- 无虚拟窗口与URL池上限，长时会话存在内存上涨风险
- 预览资源已有缓存键（文件签名+参数签名），后端复用良好

## 3. 测试场景定义
- S1: 30页 PDF，正常滚动阅读
- S2: 100页 PDF，快速滚动到底
- S3: 80页 DOCX，频繁风险定位（20次）
- S4: 移动端 4G 网络，冷启动预览

## 4. 指标体系
- TTFP（首屏可视时间）
- TTI-Preview（可交互时间）
- FPS（滚动帧率）
- Locate Latency（风险定位时延）
- Memory Peak（峰值内存）
- Error Rate（图片加载失败率）

## 5. 连续滚动方案目标值
- TTFP <= 1.2s（局域网）
- 滚动 FPS P50 >= 55，P95 >= 45
- 定位时延 P95 <= 350ms
- 100页文档峰值内存下降 25%+（相较无限制URL缓存策略）
- 错误率 <= 0.5%

## 6. 优化项与预期收益
- 虚拟滚动窗口：DOM数量下降 70%+（长文档）
- 定向预取：定位白屏概率显著下降
- LRU URL池：峰值内存可控
- 节流+rAF：滚动抖动减少

## 7. 回归测试矩阵
- 文档格式：PDF / DOCX / OCR PDF / fallback text
- 终端：Windows Chrome / Edge / Android Chrome / iOS Safari
- 网络：LAN / 4G / 慢网（高延迟）

## 8. 风险与缓解
- 风险：页高估计不准导致滚动跳动
- 缓解：首图加载后回填高度并平滑校正滚动偏移
- 风险：快速滚动请求风暴
- 缓解：请求取消 + 优先级队列 + 并发上限