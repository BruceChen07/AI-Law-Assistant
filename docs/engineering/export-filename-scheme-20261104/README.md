# 导出文件命名方案（与输入文件保持一致）

- 实施日期：2026-11-04
- 问题：导出的文件名是 `contract_20261104_180833_contract_with_comments.docx`，用户既看不出是哪份合同，也不方便归档维护。

## 1. 根因（三个）

| 编号 | 根因 | 位置 |
| --- | --- | --- |
| N-01 | 税务报告导出读取的是 `report.get("contract_filename")`，而该字段实际在 `report.overview.contract_filename` 下 → 永远取不到 → 落到硬编码 fallback `contract` | `app/services/tax_report.py` |
| N-02 | 前端跨域拿不到 `Content-Disposition`（CORS 未 `expose_headers`）→ 服务端算好的文件名被丢弃，浏览器改用前端兜底名 `contract_<导出时间>_<类型>` | `app/app_factory.py` CORS、`web/api/base.js` `requestBlob` |
| N-03 | 文件名清洗先 `os.path.basename` 再替换非法字符：含 `/` 或 `:` 的名字被截成最后一段（如 `…/2026?.docx` → `2026`），并可能整体退化成 fallback | `app/services/export_filenames.py` |

## 2. 新命名规则

```
{输入文件名（去扩展名、保留中文）}_{导出时间 YYYYMMDD_HHMMSS}_{内容类型}.{ext}
```

示例：

| 场景 | 文件名 |
| --- | --- |
| MSA 合同 + 审计报告(docx, zh) | `#MSA_Kellanova成品仓库管理服务协议_20261104_181500_审计报告.docx` |
| 同一份 + JSON 数据 | `#MSA_Kellanova成品仓库管理服务协议_20261104_181500_审计数据.json` |
| 同一份 + 带批注原文 | `#MSA_Kellanova成品仓库管理服务协议_20261104_181500_批注版.docx` |
| Markdown 导出 | `#MSA_Kellanova成品仓库管理服务协议_20261104_181500_报告.md` |
| 税务审计报告 | `采购合同_20261104_181500_税务审计报告.docx` |
| 英文界面 | `MSA_20261104_181500_audit_report.docx` |

要点：

1. **主体与输入文件名一致**：保留中文、括号、`#` 等可读字符，只把 `<>:"/\|?*` 与控制字符替换为 `_`；
2. **时间戳是导出时刻**（不是审计完成时刻），同一合同多次导出不会互相覆盖；
3. **类型后缀随界面语言**：中文 → `审计报告 / 审计数据 / 批注版 / 报告 / 税务审计报告 / 税务审计数据`；英文 → `audit_report / audit_data / commented / report / tax_audit_report / tax_audit_data`；
4. 归一化：空白与重复下划线合并、去掉首尾 `._ `、长度上限 80、规避 Windows 保留名（`CON/PRN/AUX/NUL/COM1-9/LPT1-9` 追加 `_`）；
5. 真正没有名字时（空名或只有扩展名）才回退 `合同`（英文界面 `contract`）；
6. 前端兜底名同样使用**已加载文档的真实文件名**（来自预览清单/上传文件/审计结果），不再出现 `contract_*`。

## 3. 改动清单

| 文件 | 改动 |
| --- | --- |
| `app/services/export_filenames.py` | 重写清洗逻辑（先替换非法字符再切扩展名）、新增 `localized_export_suffix()` / `export_fallback_stem()`、保留名与长度处理 |
| `app/api/routers/contracts.py` | 报告/数据/批注版/Markdown 导出统一走新命名；时间戳改为导出时刻；fallback stem 随 locale |
| `app/services/tax_report.py` | 修复 `overview.contract_filename` 读取 + 兜底查合同文档，后缀与 fallback 本地化 |
| `app/app_factory.py` | CORS 增加 `expose_headers=["Content-Disposition"]` |
| `web/App.jsx` | 记录已加载文档文件名（上传 / 预览清单 / 归档恢复），前端兜底命名本地化且基于真实文件名 |
| `tests/test_export_filenames.py` | 重写用例：中文保留、非法字符、只有扩展名、Windows 保留名、超长截断、本地化后缀、默认时间戳 |
| `tests/test_tax_report.py` | 断言更新为 `采购合同_<ts>_税务审计数据.json`（此前一直是失败的） |

## 4. 验证

- `pytest tests/test_export_filenames.py tests/test_tax_report.py tests/test_export_jobs.py tests/test_tax_lifecycle.py` → **14 passed**（其中 `test_build_and_export_tax_audit_report` 由长期失败转为通过）。
- 真机：`POST /contracts/{doc}/report/export` 返回 `Content-Disposition: attachment; filename*=utf-8''%23MSA_Kellanova…_审计报告.docx`，浏览器可直接保存为中文名。

## 5. 兼容性

- 旧导出文件不会被重命名，历史文件仍在 `data/files/contract_reports/`；
- 文件名格式从 `<stem>_<ts>_contract_audit_report` 变为 `<stem>_<ts>_审计报告`，若外部脚本按旧后缀匹配需同步调整（后缀词表集中在 `_EXPORT_KIND_LABELS`，可一处改动）。
