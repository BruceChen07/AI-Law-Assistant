# 合同连续滚动预览 - 原型交互稿（低保真）

## 1. 页面原型（Desktop）
[缩略条(可折叠)] [连续滚动主预览区........................................]
[ P1 ][ P2 ]        [ page-1 image ]
[ P3 ]              [ page-2 image + 风险高亮 ]
[ ... ]             [ page-3 image ]
                    [ ... ]
                    [ page-N image ]
                    [悬浮页码: P12/99] [缩放: Fit/75/100]

## 2. 页面原型（Mobile）
[顶部工具条: 返回 | 页码P12/99 | 缩放]
[连续滚动主预览]
[风险按钮] -> 上滑抽屉（风险列表）
[缩略条] -> 抽屉式侧栏

## 3. 核心交互流
### 3.1 上传后进入预览
1) 拉取 manifest
2) 渲染首屏占位
3) 加载首屏页图
4) 触发后续预取

### 3.2 风险定位
1) 用户点击风险卡
2) 解析 location(page_no + bbox)
3) 滚动到对应页锚点
4) 高亮脉冲 + 页码浮标更新

### 3.3 快速滚动
1) 进入“高速滚动态”
2) 仅渲染虚拟窗口页
3) 停止后补齐邻近页清晰图

## 4. 状态机
- Idle
- LoadingManifest
- RenderingSkeleton
- FetchingVisiblePages
- SmoothReading
- FastScrolling
- LocatingRisk
- ErrorFallback

转换：
- Idle -> LoadingManifest -> RenderingSkeleton -> FetchingVisiblePages -> SmoothReading
- SmoothReading <-> FastScrolling
- SmoothReading -> LocatingRisk -> SmoothReading
- 任意态 -> ErrorFallback

## 5. 关键微交互
- 页码浮标：滚动时显示，800ms 无滚动自动淡出
- 高亮动画：边框脉冲 2 次，避免“闪一下看不见”
- 缩放锚点：以当前视口中心为 anchor 重排，防止迷失

## 6. 可用性检查点
- 用户能在3秒内理解“连续滚动”而非翻页
- 用户能在1次点击内完成“风险定位到原文”
- 用户不会因缩放导致阅读位置丢失