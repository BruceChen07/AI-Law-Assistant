# 企业内网严格离线部署 Runbook

## 1. 前提

- 已创建分支 `feature/private-offline-enterprise-deployment`
- 企业内网已准备以下资产：
  - 主模型服务地址
  - 轻量模型服务地址
  - embedding / reranker / translation 模型目录
  - MinerU OCR 模型目录
  - Python 与 Node 离线依赖包或内网镜像源

## 2. 配置步骤

1. 复制 `app/config.example.json` 为 `app/config.json`
2. 按企业内网地址填写：
   - `llm_config.api_base`
   - `local_llm.main_model.api_base`
   - `local_llm.small_model.api_base`
   - `network_policy.allowed_hosts`
   - `network_policy.allowed_domain_suffixes`
3. 将 translation 模型目录写入 `translation_config.model_dir`
4. 将 embedding / reranker / OCR 模型目录放置到 `models/` 约定路径

## 3. 校验步骤

```bash
python .\bin\ensure_local_models.py --check-only --include-optional
python .\bin\validate_offline_compliance.py
```

## 4. 启动顺序

```bash
python .\bin\apply-local-llm-config.py
python -m app.main
```

## 5. 判定标准

- `ensure_local_models.py` 返回 `all_ready=true`
- `validate_offline_compliance.py` 返回 `PASS`
- 应用健康检查通过且日志中不存在外联拦截错误
