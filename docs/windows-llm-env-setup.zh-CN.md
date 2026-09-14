# Windows 环境变量化大模型配置指南

本文档说明如何在 Windows 环境下为 AI Law Assistant 配置多厂商大模型服务，并确保 API Key 只在程序运行时从环境变量加载，不写入源码、配置文件、日志或其他持久化介质。

## 1. 设计原则

- API Key 只从 Windows 环境变量读取
- `app/config.json` / `app/config.example.json` 只保留非敏感参数
- Admin 后台仅展示当前生效的模型解析结果，不再保存密钥
- 程序启动时会执行环境变量预检查
- 调用失败时只提示缺失的变量名，不输出密钥值

## 2. 通用切换变量

以下变量用于切换当前启用的模型服务：

| 变量名 | 说明 |
|---|---|
| `LLM_PROVIDER` | 当前启用的 provider，可选：`openai`、`openai_compatible`、`qwen`、`wenxin`、`ollama` |
| `LLM_API_BASE` | 可选，显式覆盖当前 provider 的接口地址 |
| `LLM_MODEL` | 可选，显式覆盖当前 provider 的模型名 |
| `LLM_TEMPERATURE` | 可选，覆盖温度参数 |
| `LLM_MAX_TOKENS` | 可选，覆盖最大输出 token |
| `LLM_TIMEOUT` | 可选，覆盖请求超时时间 |

说明：

- 如果设置了 `LLM_PROVIDER`，系统会优先按该 provider 解析
- 如果没有设置 `LLM_PROVIDER`，系统会继续使用 `app/config.json` 中的非敏感默认 provider
- 如果同时设置了厂商变量和 `LLM_*` 通用变量，则以 `LLM_*` 为最高优先级

## 3. 各厂商环境变量

### 3.1 OpenAI

```powershell
[System.Environment]::SetEnvironmentVariable("LLM_PROVIDER", "openai", "User")
[System.Environment]::SetEnvironmentVariable("OPENAI_API_KEY", "your-openai-key", "User")
[System.Environment]::SetEnvironmentVariable("OPENAI_MODEL", "gpt-4o-mini", "User")
[System.Environment]::SetEnvironmentVariable("OPENAI_BASE_URL", "https://api.openai.com/v1", "User")
```

### 3.2 通义千问 / DashScope

```powershell
[System.Environment]::SetEnvironmentVariable("LLM_PROVIDER", "qwen", "User")
[System.Environment]::SetEnvironmentVariable("DASHSCOPE_API_KEY", "your-dashscope-key", "User")
[System.Environment]::SetEnvironmentVariable("DASHSCOPE_MODEL", "qwen-max", "User")
[System.Environment]::SetEnvironmentVariable("DASHSCOPE_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1", "User")
```

### 3.3 文心一言 / 千帆

推荐两种方式：

1. 直接提供 Bearer Token
2. 提供 `Access Key + Secret Key`，系统运行时动态换取临时访问令牌

#### 方式 A：Bearer Token

```powershell
[System.Environment]::SetEnvironmentVariable("LLM_PROVIDER", "wenxin", "User")
[System.Environment]::SetEnvironmentVariable("QIANFAN_BEARER_TOKEN", "your-bearer-token", "User")
[System.Environment]::SetEnvironmentVariable("QIANFAN_MODEL", "ernie-4.0-turbo", "User")
[System.Environment]::SetEnvironmentVariable("QIANFAN_BASE_URL", "https://qianfan.baidubce.com/v2", "User")
```

#### 方式 B：Access Key / Secret Key

```powershell
[System.Environment]::SetEnvironmentVariable("LLM_PROVIDER", "wenxin", "User")
[System.Environment]::SetEnvironmentVariable("QIANFAN_ACCESS_KEY", "your-access-key", "User")
[System.Environment]::SetEnvironmentVariable("QIANFAN_SECRET_KEY", "your-secret-key", "User")
[System.Environment]::SetEnvironmentVariable("QIANFAN_MODEL", "ernie-4.0-turbo", "User")
[System.Environment]::SetEnvironmentVariable("QIANFAN_BASE_URL", "https://qianfan.baidubce.com/v2", "User")
```

### 3.4 Ollama / 本地 OpenAI-Compatible 服务

```powershell
[System.Environment]::SetEnvironmentVariable("LLM_PROVIDER", "ollama", "User")
[System.Environment]::SetEnvironmentVariable("OLLAMA_HOST", "http://127.0.0.1:11434", "User")
[System.Environment]::SetEnvironmentVariable("OLLAMA_MODEL", "qwen3.6:27b", "User")
```

说明：

- `OLLAMA_HOST` 只需要填写主机和端口，系统会自动补全为 `/v1`
- Ollama 本地模式默认不需要 API Key

## 4. 模型切换方法

### 切换到 OpenAI

```powershell
[System.Environment]::SetEnvironmentVariable("LLM_PROVIDER", "openai", "User")
```

### 切换到通义千问

```powershell
[System.Environment]::SetEnvironmentVariable("LLM_PROVIDER", "qwen", "User")
```

### 切换到文心一言

```powershell
[System.Environment]::SetEnvironmentVariable("LLM_PROVIDER", "wenxin", "User")
```

### 切换到本地 Ollama

```powershell
[System.Environment]::SetEnvironmentVariable("LLM_PROVIDER", "ollama", "User")
```

设置完成后，请重新打开终端并重新启动服务：

```powershell
python -m app.main
```

## 5. Admin 后台说明

进入 `Admin -> 模型配置` 后，页面会展示：

- 当前生效的 provider / model / api_base
- 当前命中的环境变量来源
- 缺失的环境变量列表
- 当前路由角色（如 `main` / `small` / `cloud_fallback`）
- 连通性测试结果

注意：

- 后台不再提供 API Key 输入框
- 后台不再保存或清除密钥
- 所有密钥操作必须在 Windows 环境变量层完成

## 6. 安全要求

请遵守以下规则：

- 不要把 API Key 写入 `app/config.json`
- 不要把 API Key 提交到 Git 仓库
- 不要把 API Key 写到 `.env` 以外的任意临时测试文件中
- 不要在日志、截图、报错信息中主动粘贴真实密钥

项目已在 `.gitignore` 中忽略以下敏感文件模式：

- `.env`
- `.env.*`
- `*.env`
- `*.secret.json`
- `*.credentials.json`
- `secrets.*`
- `config.local.json`
- `app/config.local.json`

## 7. 常见问题排查

### 7.1 后台显示“缺少环境变量”

现象：

- `Admin -> 模型配置` 中出现 `缺少环境变量`

排查：

1. 确认是否重新打开了 PowerShell/Terminal
2. 执行以下命令确认变量存在：

```powershell
[System.Environment]::GetEnvironmentVariable("LLM_PROVIDER", "User")
[System.Environment]::GetEnvironmentVariable("OPENAI_API_KEY", "User")
```

3. 确认 provider 对应的变量名是否正确

### 7.2 明明切换了 provider，但程序仍使用旧模型

排查：

1. 确认已重启后端进程
2. 进入后台查看“已命中的环境变量”
3. 确认没有把 `LLM_MODEL` 设置成旧值覆盖了厂商变量

### 7.3 文心一言使用 AK/SK 仍报鉴权错误

排查：

1. 检查 `QIANFAN_ACCESS_KEY` / `QIANFAN_SECRET_KEY` 是否成对配置
2. 检查 `QIANFAN_BASE_URL` 是否为当前可用的兼容接口地址
3. 确认网络可以访问千帆 token 服务

### 7.4 日志中担心泄露密钥

当前实现已对以下内容做脱敏处理：

- `Bearer xxxxx`
- `sk-xxxx`
- `api_key`
- `access_key`
- `secret_key`

如果你在外部脚本中自行打印环境变量，这部分仍需要你自行避免。

## 8. 推荐上线检查清单

- [ ] `app/config.json` 中没有 `api_key`
- [ ] 本地 `.env` / `*.secret.json` 未加入 Git
- [ ] Admin 页面能正确显示当前 provider 和 model
- [ ] 缺失环境变量时，后台有友好提示
- [ ] 模型测试接口可正常返回结果
- [ ] 合同审计链路在当前 provider 下可正常执行

