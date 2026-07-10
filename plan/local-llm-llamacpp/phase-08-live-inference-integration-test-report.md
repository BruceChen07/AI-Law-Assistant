# Phase 08 - 双 llama.cpp 真机推理联调测试报告

## 1. 测试目标

- 验证双 `llama.cpp` 实例可成功启动
- 验证主模型与小模型的模型发现接口和最小推理请求
- 验证项目代码能够兼容 `reasoning_content` 场景

## 2. 测试环境

- 物理内存：约 `64 GB`
- 运行模式：CPU
- 主模型：
  - `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`
- 小模型：
  - `Qwen3.6-27B-Q4_0.gguf`
- 主端口：`18080`
- 小端口：`18081`

## 3. 已执行测试

### 3.1 双实例启动

执行：

```bash
python .\bin\start-local-llamacpp-stack.py --main-ctx-size 4096 --small-ctx-size 2048 --wait-seconds 300
```

结果：

- 通过
- 双实例均进入 listening 状态

### 3.2 端口监听检查

结果：

- `127.0.0.1:18080`：Listen
- `127.0.0.1:18081`：Listen

### 3.3 运行时校验

执行：

```bash
python .\bin\validate-local-llamacpp-runtime.py --config-path .\app\config.json
```

结果摘要：

- 主模型 `/v1/models`：通过
- 小模型 `/v1/models`：通过
- 烟测请求：通过
- 后端 `/health`：失败

失败原因：

- `ModuleNotFoundError: No module named 'fastapi'`

### 3.4 真实请求测试

主模型：

- HTTP 接口请求成功
- 耗时：
  - `17.44s`
  - `10.36s`

小模型：

- HTTP 接口请求成功
- 耗时：
  - `24.04s`
  - `21.16s`

### 3.5 项目调用链兼容性验证

修复后验证结果：

- `LLMService` 能识别 `reasoning_content`
- 当 `content` 为空时，不再返回空字符串
- 已记录 `_used_reasoning_content_fallback = true`

## 4. 问题与风险

### 问题 1：后端未启动

原因：

- 当前 Python 环境缺失：
  - `fastapi`
  - `structlog`

影响：

- 无法完成完整应用层 `/health` 和业务接口联调

### 问题 2：模型输出当前落在 reasoning_content

状态：

- 已做兼容修复

剩余风险：

- 当前 fallback 返回的是 reasoning 内容，不一定等于最终业务回答
- 后续仍建议继续研究：
  - 模板参数
  - 思考开关
  - Qwen3.6 在 `llama.cpp` 下的最终回答生成策略

## 5. 当前结论

本阶段可以确认：

1. 双 `llama.cpp` 实例已可在本机稳定运行
2. 真机推理链路已打通
3. 项目内 LLM 兼容层已避免“成功推理却返回空响应”

本阶段尚未完成：

1. 后端依赖安装
2. 基于完整应用接口的端到端业务联调
