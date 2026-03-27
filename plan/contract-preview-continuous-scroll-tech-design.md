# 合同预览改造技术设计（分页式 -> 单页连续滚动）

## 0. 目标与范围
目标：将当前“分页切换式预览”升级为“单页连续滚动阅读”，对齐 MinerU 的阅读心智与定位效率。
范围：前端预览架构、接口增强、性能优化、兼容策略、灰度迁移。

## 1. 现有架构评估
- 前端预览主流程：
  - manifest 拉取与模式判断：[App.jsx:L473-L533](file:///d:/Workspace/AI-Law-Assistant/web/App.jsx#L473-L533)
  - 分页视觉预览渲染：[App.jsx:L789-L845](file:///d:/Workspace/AI-Law-Assistant/web/App.jsx#L789-L845)
- 后端接口：
  - 页清单接口：[contracts.py:L218-L250](file:///d:/Workspace/AI-Law-Assistant/app/api/routers/contracts.py#L218-L250)
  - 单页图片接口：[contracts.py:L252-L274](file:///d:/Workspace/AI-Law-Assistant/app/api/routers/contracts.py#L252-L274)
- 资产构建：
  - PDF/DOCX 视觉页 + 文本 fallback + 缓存：[contract_preview_assets.py:L302-L446](file:///d:/Workspace/AI-Law-Assistant/app/services/contract_preview_assets.py#L302-L446)

结论：
1) 能力完整但交互模型是“页切换”；2) 只有前3页预加载，长文档跳转成本高；3) 无虚拟滚动窗口。

## 2. 与 MinerU 的关键差异
- 当前：页为主、缩略图导航；MinerU：流为主、阅读连续。
- 当前：定位后切页；MinerU：定位后滚动锚定并保持阅读上下文。
- 当前：单页主图；MinerU：多页连续视图 + 悬浮页码反馈。
- 当前：预加载固定3页；MinerU：按滚动方向动态预取。

## 3. 目标架构（连续滚动）
### 3.1 前端组件重构
新增组件：
- `ContractPreviewContinuous`
- `VirtualPageViewport`（虚拟窗口）
- `PageAnchorRail`（页码浮标）
- `ZoomController`
- `PreviewFetchScheduler`（请求调度/预取）

页面结构：
- 左侧缩略条保留（可折叠）
- 主区域改为连续滚动容器，按页垂直串联
- 右侧风险列表定位到“页+块”后滚动到对应锚点

### 3.2 数据与接口策略
复用现有接口，不破坏兼容：
- 保持 `preview-manifest` 与 `page-image` 不变
- 前端新增“窗口化请求策略”：
  - 可视区页面 V
  - 预取窗口：V 前后各 N 页（默认 N=2）
  - 快速滚动时仅保留最新窗口任务（取消过期请求）

可选增强（V2）：
- 新增 `/preview/pages/batch-image?page=1,2,3` 以减少 RTT（非首期必需）。

## 4. 单页渲染性能优化策略
### 4.1 虚拟滚动
- 仅渲染可视区 + 缓冲区页的 DOM（例如 6~10 页）
- 占位骨架保持总高度，避免滚动跳变
- 页高估计来自 manifest 的 `width/height`，图片加载后回填精确高度

### 4.2 懒加载
- `IntersectionObserver` 触发加载
- 图片优先级：当前页 > 下1页 > 上1页 > 其余
- 失败重试指数退避（最多2次）

### 4.3 分页预加载
- 初始：首屏页 + 下2页
- 滚动向下：持续预取后续2页
- 定位跳转：目标页前后各1页同步拉取，确保无白屏

### 4.4 内存与主线程控制
- ObjectURL LRU 池（例如最多40页，超出回收）
- 滚动事件节流 16ms
- 高亮计算在 `requestAnimationFrame` 执行

## 5. UI/UX 改造设计
- 滚动条：
  - 主滚动条增强可见性
  - 增加“阅读进度条（顶部细线）”
- 页码指示器：
  - 右下角悬浮 `P12 / 99`
  - 快速滚动时显示，停顿后淡出
- 缩放适配：
  - 50% / 75% / 100% / FitWidth
  - 缩放后保持当前阅读锚点不漂移
- 风险联动：
  - 点击风险 -> 平滑滚动到目标块 -> 2s 高亮脉冲

## 6. 兼容性保障
- 多格式：
  - PDF：继续走 raster + text blocks
  - DOCX：继续走 `_render_docx_pages`
  - text fallback：连续文本流模式
- 移动端：
  - 小屏默认隐藏缩略条（抽屉）
  - 手势缩放仅在主预览区生效
  - 低端机自动降级为“低清图 + 小窗口渲染”
- 浏览器：
  - 首选现代浏览器；无 `IntersectionObserver` 时回退到滚动阈值触发

## 7. 渐进式迁移与回滚
- 灰度开关：`ui_config.preview_continuous_enabled`（默认 false）
- 发布策略：
  - 内部管理员 10% -> 全员 30% -> 全量
- 回滚：
  - 开关关闭即回退旧分页组件
  - 接口完全兼容，无需数据库迁移
- 观测：
  - 关键指标：首屏时间、滚动帧率、定位成功率、预览错误率

## 8. 验收标准（DoD）
- 100页文档滚动无明显卡顿（主观流畅）
- 风险定位命中率 >= 99%
- 移动端可用性通过（上传-预览-定位闭环）
- 发生故障可在5分钟内开关回滚