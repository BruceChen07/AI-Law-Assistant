# local-llm-llamacpp 分支合入报告

## 1. 合入范围

- 目标分支：`feature/local-llm-llamacpp`
- 临时整合分支：`integration/local-llm-llamacpp-latest`
- llama.cpp 基线：`ca8da9d`
- 新特性基线：`e32dbe7`
- 共同祖先：`307d507`
- 分叉规模：llama.cpp 独有 46 个提交，新特性独有 41 个提交

## 2. 功能隔离清单

### 本地 llama.cpp 运行时

- `app/local_llm/`：本地运行策略与 llama.cpp provider adapter
- `bin/start-local-llamacpp-server.py`：单实例启动
- `bin/start-local-llamacpp-stack.py`：主/小模型实例编排
- `bin/apply-local-llm-config.py`：纯 llama.cpp 配置生成
- `bin/validate-local-llamacpp-runtime.py`：端点与推理验证
- `app/config.json`、`app/config.example.json`：仅允许 `llama_cpp` 和回环端点

### 云端 API 测试边界

- 生产配置禁止 `runtime_mode=cloud`。
- `ENABLE_CLOUD_API_TEST=true` 在本分支会直接报错。
- `LLM_PROVIDER`、云端模型和密钥环境变量不能覆盖路由后的 llama.cpp 目标。
- `ci/check_llm_isolation.py` 扫描本地运行目录中的云端 URL、云端凭据、Ollama 运行时和跨模块导入。

## 3. 合入的新特性

- 四段式合同摘要、map-reduce 抽取、摘要缓存与 partial 状态
- PDF 数字化、预览、来源下载和内容哈希去重
- Prompt 生命周期管理与运行时绑定
- Admin、Contracts、Tax Audit router 模块化
- 数据库 migration、审计能力包与检索模块拆分
- 审计进度日志、独立审计日志和完整性保护
- MinerU 回环代理隔离
- 后端优先启动和本地健康检查代理隔离
- 前端审计持久化、历史快照、PDF 预览和错误目录

## 4. 冲突处理记录

- 删除旧单文件 `admin.py`、`contracts.py`、`tax_audit.py`，采用新版模块化 router。
- 业务逻辑冲突采用新版实现，llama.cpp 运行时与离线部署逻辑采用目标分支实现。
- 修复自动合并产生的重复 `local_llm` 配置键和 Ollama 默认配置。
- 修复 `llm_router.py` 未定义 fallback 标志。
- 修复 `llm.py` 无效 trace 闭包、缺失 token 估算导入和 llama.cpp 请求适配。
- 修复 `contract_audit.py` 缺失进度、trace ID、最终报告和 prompt payload 逻辑。
- 将 llama.cpp 模型列表、配置更新和友好错误提示迁入新版模块化 Admin。

## 5. 自动化验证

| 验证项 | 结果 |
| --- | --- |
| LLM 隔离扫描 | 通过 |
| Python 编译检查 | 通过 |
| 纯 llama.cpp policy/runtime | 8 passed |
| llama.cpp Admin | 6 passed |
| 合同审计 | 24 passed |
| PDF、Prompt、审计日志新特性 | 11 passed |
| 合并验收集合 | 41 passed |
| 应用导入启动 | 通过，`Law Assistant` |
| 前端 Vitest | 未执行，独立 worktree 未安装 `web/node_modules` |
| llama.cpp 主端点 `18080` | 未验证，服务未启动 |
| llama.cpp 小端点 `18081` | 未验证，服务未启动 |

## 6. 质量结论

代码级隔离门禁和已执行的本地 llama.cpp 回归测试全部通过。当前不能宣称真实模型可用性达到 100%，因为验证时本机两个 llama-server 端点均未监听。完成真实验收需要启动已配置的 GGUF 模型后执行：

```powershell
python bin/start-local-llamacpp-stack.py
python bin/validate-local-llamacpp-runtime.py
```

前端完整验证还需先在 `web/` 安装锁定依赖，再运行 `npm test -- --run` 和 `npm run build`。
