# Phase 06 - 纯 llama.cpp 切换测试报告

## 1. 测试目标

- 验证模型文件已复制到项目目录
- 验证项目实际配置已切换为双 `llama.cpp`
- 验证新的纯 `llama.cpp` 启动脚本、校验脚本和管理页改动可正常工作
- 明确功能测试未通过时是代码问题还是环境阻塞

## 2. 已执行验证

### 2.1 模型文件复制校验

源目录：

- `E:\models`

目标目录：

- `E:\workspace\AI-Law-Assistant\models\llm`

结果：

- 三个 GGUF 文件均已复制完成
- 文件名、大小、时间戳与源目录一致

### 2.2 Python 语法编译

执行命令：

```bash
python -m py_compile bin/apply-local-llm-config.py bin/start-local-llamacpp-stack.py bin/validate-local-llamacpp-runtime.py bin/benchmark-ollama-vs-llamacpp.py
```

结果：

- 通过

### 2.3 前端构建

执行命令：

```bash
cd web
npm run build
```

结果：

- 通过
- Vite 构建成功
- 存在 500 kB 以上 chunk 警告，但不影响本次功能切换

### 2.4 真实配置切换

执行命令：

```bash
python .\bin\apply-local-llm-config.py --provider llama_cpp --small-provider llama_cpp --main-model Qwen3.6-35B-A3B-UD-Q4_K_M.gguf --small-model Qwen3.6-27B-Q4_0.gguf --llama-cpp-host http://127.0.0.1:18080 --small-llama-cpp-host http://127.0.0.1:18081
```

结果：

- 通过
- `app/config.json` 已切为双 `llama.cpp`

### 2.5 双 llama.cpp 启动脚本执行

执行命令：

```bash
python .\bin\start-local-llamacpp-stack.py
```

结果：

- 失败
- 原因：`llama-server executable was not found`

结论：

- 失败点在本机缺少 `llama-server` 可执行文件
- 不是 GGUF 文件缺失
- 不是项目代码仍依赖 Ollama

### 2.6 运行时校验脚本

执行命令：

```bash
python .\bin\validate-local-llamacpp-runtime.py --config-path .\app\config.json --skip-backend
```

结果：

- 已生成报告：
  - `plan/local-llm-llamacpp/reports/runtime-validation-20260710-005638.json`
- 配置检查全部通过
- 服务探活失败：
  - `llama.cpp main /v1/models` 不可达
  - `llama.cpp small /v1/models` 不可达
- 基础对话烟测失败

失败原因：

- 两个 `llama.cpp` 服务均未启动
- 根因仍为 `llama-server` 二进制未就绪

## 3. 当前阻塞项

唯一外部阻塞项：

1. 在本机安装或提供 `llama-server.exe`
2. 将其加入 PATH，或在启动脚本中通过 `--server-exe` 指定完整路径

## 4. 当前结论

本阶段已经证明：

- 模型资产已进入项目目录
- 真实项目配置已完成纯 `llama.cpp` 切换
- 前端与脚本改动均可通过语法/构建级验证
- 当前无法完成真机推理功能测试的唯一原因是环境缺少 `llama-server`

## 5. 下一步建议

在补齐 `llama-server.exe` 后，按以下顺序继续：

1. 执行 `python .\bin\start-local-llamacpp-stack.py --server-exe <full-path>`
2. 重跑 `python .\bin\validate-local-llamacpp-runtime.py --config-path .\app\config.json --skip-backend`
3. 启动后端后执行完整联调
4. 补跑合同审计真实样本测试并记录耗时、内存、GPU 指标
