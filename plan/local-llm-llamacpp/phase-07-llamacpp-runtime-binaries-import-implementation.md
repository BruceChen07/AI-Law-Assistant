# Phase 07 - llama.cpp 运行时二进制导入实施记录

## 1. 阶段目标

- 将外部 `llama.cpp` Windows 二进制安装目录完整复制到当前项目内
- 确保所有可执行文件、DLL、批处理脚本等依赖组件完整迁移
- 验证项目内副本可直接调用
- 将项目启动脚本默认查找路径切到仓库内二进制目录

## 2. 源目录与目标目录

### 2.1 源目录

- `E:\Tool\llama-b9940-bin-win-cpu-x64`

### 2.2 项目内目标目录

- `E:\workspace\AI-Law-Assistant\third_party\llama.cpp\bin-win-cpu-x64`

## 3. 迁移方式

执行方式：

- 使用 `robocopy /E` 完整复制目录内容

说明：

- 本次源目录为扁平发行版目录，没有额外子目录
- 但复制策略仍按“完整目录树”执行，确保未来即使源目录增加子目录也能被整体迁移

## 4. 导入内容摘要

已导入的核心文件包括但不限于：

- `llama-server.exe`
- `llama-cli.exe`
- `llama.dll`
- `llama-common.dll`
- `llama-server-impl.dll`
- `ggml*.dll`
- `libomp140.x86_64.dll`
- `start_service.bat`
- `start_new_service.bat`

## 5. 启动路径收敛

文件：`bin/start-local-llamacpp-server.py`

调整内容：

- 在 `_find_server_executable()` 中新增项目内优先查找路径：
  - `third_party/llama.cpp/bin-win-cpu-x64/llama-server.exe`

效果：

- 后续项目启动 `llama.cpp` 时，可直接优先复用仓库内二进制副本
- 降低对系统 PATH 和外部工具目录的依赖

## 6. 本阶段结论

本阶段已完成：

- `llama.cpp` 运行时二进制完整导入到项目内
- 启动脚本已指向项目内二进制副本
- 为后续纯 `llama.cpp` 推理链路测试补齐了外部运行时前置条件
