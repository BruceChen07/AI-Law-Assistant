# llama-server 空闲自动释放内存

- 实施日期：2026-11-04
- 问题：审计任务结束后 llama-server 仍占用大量内存，影响同机其他服务。

## 1. 结论

本仓库内置的 llama.cpp 版本**原生支持空闲卸载**，无需自研定时器：

```
--sleep-idle-seconds SECONDS   number of seconds of idleness after which the server will sleep
                               (default: -1; -1 = disabled)
```

空闲超时后服务端会把模型卸载（内存回落），下个请求到来时自动重新加载。

## 2. 真机实测（Qwen3.6-35B-A3B Q4_K_M，CPU 推理，ctx 16384）

| 阶段 | llama-server 工作集 |
| --- | --- |
| 忙时（加载完成，常驻） | **34.28 GB** |
| 空闲 60s 后（sleep 触发） | **0.09 GB** |
| 收到首个请求（唤醒） | 唤醒耗时 **12 s**，内存回到 **26.82 GB** |
| 再空闲 80s | 回落到 **0.09 GB** |

结论：空闲时几乎不占内存（34 GB → 90 MB），冷启动代价仅十几秒（模型文件在系统页缓存里），完全可接受。

## 3. 配置方式

### 配置项（推荐）
`app/config.json` → `llm_config.sleep_idle_seconds`：

```json
"llm_config": {
  "provider": "llama_cpp",
  "sleep_idle_seconds": 1200
}
```

- `1200`：空闲 20 分钟后释放（默认推荐值）；
- `0`：永不释放（模型常驻，等同旧行为）；
- 更小的值（如 `300`）内存更早释放，但中断后首个请求要等冷启动。

### 启动参数（覆盖配置）
```powershell
bin\start-services.ps1 -LlamaCppSleepIdleSeconds 600
```

`-1`（默认）= 读取配置；`0` = 关闭；其它正整数 = 直接使用。启动日志会打印当前策略：
`Idle unload: 1200s without requests releases model memory`。

## 4. 行为说明

1. **只在空闲时触发**：审计进行中请求连续到达，空闲计时器持续重置，因此不会在任务中途卸载；
2. **唤醒是阻塞的**：休眠后的第一个请求会等待模型重新加载（实测约 12 s），仍在 `llm_config.timeout=600s` 之内；
3. **/v1/models 仍返回 200**：休眠不等于进程退出，健康探测、预检都正常；
4. **进程不退出**：只卸载模型权重与 KV；如需彻底回收进程本身，仍需停止服务（`bin/stop-services.ps1`）。

## 5. 相关参数（可选进一步省内存）

| 参数 | 作用 |
| --- | --- |
| `--ctx-size`（默认 16384） | 上下文越大，KV cache 越大；按需下调 |
| `--parallel 1`（已设置） | 每增加一个 slot 就复制一份 KV cache |
| `--cache-type-k/v` | 量化 KV cache（如 q8_0）进一步降低占用 |
| `--no-mmap` | 不建议：会强制整模型读入匿名内存，失去按需分页 |

## 6. 验证命令

```powershell
# 当前内存
Get-Process llama-server | Select-Object Id, @{n='WS_GB';e={[math]::Round($_.WorkingSet64/1GB,2)}}
# 触发唤醒（会等待重载）
curl.exe -s -X POST http://127.0.0.1:18080/v1/chat/completions -H "Content-Type: application/json" ^
  -d "{\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":8}"
```

## 7. 已知限制

1. 卸载粒度是“整个模型”，无法只保留一部分；
2. 冷启动期间的第一个请求延迟明显（实测 12 s，磁盘较慢时会更高）；
3. 若同机其它服务需要内存立即可用，可把 `sleep_idle_seconds` 调小，或在任务结束后手动 `bin/stop-services.ps1` 彻底退出进程。
