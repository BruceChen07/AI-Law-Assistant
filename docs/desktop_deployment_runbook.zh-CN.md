# 全新台式机部署运行手册

本文档对应新增桌面部署脚本体系：

- `deploy.sh`
- `deploy.ps1`
- `bin/deploy_desktop.py`
- `bin/detect_deployment_env.py`
- `bin/download_deployment_assets.py`
- `bin/verify_deployment_assets.py`
- `bin/verify_llamacpp_bundle.py`
- `deploy/desktop-deployment.manifest.json`

## 1. 前置准备

### 1.1 操作系统

支持以下桌面级环境：

- Windows 10/11
- Ubuntu 22.04 LTS

### 1.2 必备软件

- Git `2.40+`
- CMake `3.20+`
- Python `3.10+`
- pip
- Node.js `22+`
- Windows 下建议提供 MinGW-w64 / Visual Studio Build Tools
- 若启用 GPU：
  - NVIDIA：CUDA 12.x
  - AMD：ROCm 5.6+（Linux）

### 1.3 操作系统侧要求

部署前请人工完成以下事项：

1. 固定内网 IP
2. 放通部署需要的端口：`18080`、`18081`、`8000`、`5173`
3. 创建专用非管理员部署用户
4. 将仓库放在**不含中文和特殊字符**的工作目录

## 2. 资产清单

默认资产清单在：

`deploy/desktop-deployment.manifest.json`

当前清单已经带有以下校验值：

- 主模型 `Qwen3.6-35B-A3B-UD-Q4_K_M.gguf`
- 小模型 `Qwen3.6-27B-Q4_0.gguf`
- `llama-server.exe`

使用方式分两种：

1. **内网已下发模型/运行时**
   - 直接把文件放到 manifest 指定路径
   - 运行校验脚本即可
2. **需要自动下载**
   - 先把官方可信 URL 写入 manifest 中对应 `download_urls`
   - 再执行下载脚本

## 3. 环境检测

先执行环境识别：

```bash
python .\bin\detect_deployment_env.py
```

输出内容包括：

- OS 类型
- CPU 架构
- GPU 型号与驱动版本
- Git/CMake/Python/Node 版本
- 推荐的 `llama.cpp` 编译参数

## 4. 资源下载与校验

### 4.1 下载

```bash
python .\bin\download_deployment_assets.py --manifest-path .\deploy\desktop-deployment.manifest.json --asset-kind all
```

能力说明：

- 3 次自动重试
- 断点续传
- 下载进度输出
- 下载后自动 SHA256 校验

### 4.2 校验

```bash
python .\bin\verify_deployment_assets.py --manifest-path .\deploy\desktop-deployment.manifest.json --asset-kind all
```

## 5. llama.cpp 校验

### 5.1 运行时 bundle 校验

```bash
python .\bin\verify_llamacpp_bundle.py --manifest-path .\deploy\desktop-deployment.manifest.json
```

### 5.2 官方源码 checkout 校验

如果新机器使用官方源码自行编译，请在 manifest 中填好：

- `llama_cpp.source.repo`
- `llama_cpp.source.release_tag`
- `llama_cpp.source.expected_commit`

然后执行：

```bash
python .\bin\verify_llamacpp_bundle.py --manifest-path .\deploy\desktop-deployment.manifest.json --source-dir D:\tooling\llama.cpp
```

## 6. 一键部署

### 6.1 Windows

```powershell
.\deploy.ps1
```

### 6.2 Linux

```bash
chmod +x deploy.sh
./deploy.sh
```

### 6.3 一键部署执行内容

`bin/deploy_desktop.py` 会按顺序完成：

1. 环境检测
2. 版本前置校验
3. 创建 `app/.venv`
4. 安装后端依赖
5. 安装前端依赖
6. 初始化数据库与目录
7. 下载或校验模型与 `llama.cpp`
8. 应用双 `llama.cpp` 配置
9. 启动主/小两个 `llama.cpp` 服务
10. 启动后端
11. 启动前端
12. 进行 5 分钟可用性监控
13. 如发现异常，抓取日志并自动重启一次

## 7. 常用参数

### 7.1 跳过自动下载

```powershell
.\deploy.ps1 --skip-download
```

适用于模型和 runtime 已由内网制品库预先投放的情况。

### 7.2 跳过前端

```powershell
.\deploy.ps1 --skip-frontend
```

### 7.3 指定源码仓库做 commit 校验

```powershell
.\deploy.ps1 --source-dir D:\tooling\llama.cpp
```

## 8. 部署产物与日志

所有部署相关产物统一落在：

` .runtime/deploy/ `

重点文件包括：

- `env-detection-report.json`
- `download-report.json`
- `asset-verification-report.json`
- `llamacpp-verification-report.json`
- `desktop-deploy-report.json`
- `desktop-deploy-failure-report.json`
- `logs/*.log`

## 9. 故障排查

### 9.1 下载失败

优先检查：

1. `download_urls` 是否填写
2. 域名是否在 `allowed_download_hosts`
3. 网络是否支持断点续传

### 9.2 llama.cpp 校验失败

优先检查：

1. `llama-server.exe` 是否存在
2. SHA256 是否与 manifest 一致
3. 如走源码编译，`expected_commit` 是否正确

### 9.3 启动后端失败

优先检查：

1. `app/.venv` 是否创建成功
2. `pip install -r app/requirements.txt` 是否完整
3. `/health` 是否可访问

### 9.4 5 分钟可用性监控失败

查看：

- `.runtime/deploy/desktop-deploy-failure-report.json`
- `.runtime/deploy/logs/backend-runtime.log`
- `.runtime/deploy/logs/frontend-runtime.log`

## 10. 推荐落地方式

生产上建议采用以下流程：

1. 先在受控环境生成并审批 manifest
2. 固定官方模型版本、量化等级、SHA256
3. 固定 `llama.cpp` 版本与 commit
4. 新机器上线时只允许使用该 manifest
5. 部署完成后保留 `.runtime/deploy/*report.json` 作为验收记录
