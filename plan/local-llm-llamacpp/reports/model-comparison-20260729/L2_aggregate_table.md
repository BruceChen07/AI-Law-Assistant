# L2 任务级功能正确性层 —— 跑批聚合对比

> 数据来源：llm_trace.db(llm_trace_spans，按各轮 trace_window 精确切片) + 跑批日志(parse_failed_flag)

## 轮次总览

| 指标 | A-35b-a3b | B-14b |
|---|---|---|
| main_model | qwen3.6:35b-a3b | qwen3:14b |
| num_ctx_main | 8192 | 8192 |
| 合同运行数(runs) | 10 | 10 |
| 成功(ok) | 3 | 8 |
| 失败(failed) | 7 | 2 |
| 失败原因 | {"server_connection": 5, "ocr_unavailable": 2} | {"ocr_unavailable": 2} |
| 单份平均耗时(s) | 109.9 | 52.84 |
| 单份p95耗时(s) | 218.779 | 108.733 |
| 平均风险条目数 | 1.67 | 4.25 |
| 风险条目总数 | 5 | 34 |
| 平均引用有效率 | 0.0 | 0.0 |
| LLM span 总数 | 40 | 70 |
| span成功/失败 | {"success": 35, "failed": 5} | {"success": 70} |
| small模型调用数 | 0 | 0 |
| small→main回退 | 0 | 0 |
| 响应事件数(full-trace) | 35 | 70 |
| JSON总体有效率% | 100.0 | 100.0 |
| JSON一次成功率% | 100.0 | 100.0 |
| 重试响应数 | 0 | 0 |
| JSON需修复估计(次) | 0 | 0 |
| 总token消耗 | 91714 | 187439 |
| 单次平均token | 2620.4 | 2677.7 |
| 平均延迟ms | 12396.9 | 5913.4 |
| p95延迟ms | 28687 | 18214 |
| 平均prompt_eval ms | 3563.1 | 649.8 |
| 平均eval ms | 8293.8 | 4880.0 |

## A-35b-a3b 合同级明细

| 合同 | 遍 | ok | 耗时(s) | 风险数 | 引用有效率 | 路径 | 错误 |
|---|---|---|---|---|---|---|---|
| Employee_Meal_Subsidy_Platfo | 1 | True | 218.779 | 5 | 0.0 | multipass_classic_stage1 |  |
| 关爱通南京与致逸美妆产品返点补充协议.docx | 1 | True | 27.906 | 0 | None | multipass_classic_stage1 |  |
| 员工餐补平台服务合同格式24.3.7.docx | 1 | True | 83.007 | 0 | None | multipass_classic_stage1 |  |
| 薪资税务代理服务合同（最终版）_202500403.do | 1 | False | 111.901 | 0 | None |  | RuntimeError: llm request failed: ollama |
| 软件服务协议-红客厅（中智股份）.pdf | 1 | False | 0.234 | 0 | None |  | OCRExtractionError: OCR_PARSE_FAILED: Fi |
| Employee_Meal_Subsidy_Platfo | 2 | False | 3.992 | 0 | None |  | RuntimeError: llm request failed: [WinEr |
| 关爱通南京与致逸美妆产品返点补充协议.docx | 2 | False | 2.432 | 0 | None |  | RuntimeError: llm request failed: [WinEr |
| 员工餐补平台服务合同格式24.3.7.docx | 2 | False | 2.97 | 0 | None |  | RuntimeError: llm request failed: [WinEr |
| 薪资税务代理服务合同（最终版）_202500403.do | 2 | False | 3.192 | 0 | None |  | RuntimeError: llm request failed: [WinEr |
| 软件服务协议-红客厅（中智股份）.pdf | 2 | False | 0.122 | 0 | None |  | OCRExtractionError: OCR_PARSE_FAILED: Fi |

## B-14b 合同级明细

| 合同 | 遍 | ok | 耗时(s) | 风险数 | 引用有效率 | 路径 | 错误 |
|---|---|---|---|---|---|---|---|
| Employee_Meal_Subsidy_Platfo | 1 | True | 66.718 | 2 | 0.0 | multipass_classic_stage1 |  |
| 关爱通南京与致逸美妆产品返点补充协议.docx | 1 | True | 5.701 | 0 | None | multipass_classic_stage1 |  |
| 员工餐补平台服务合同格式24.3.7.docx | 1 | True | 42.04 | 1 | 0.0 | multipass_classic_stage1 |  |
| 薪资税务代理服务合同（最终版）_202500403.do | 1 | True | 108.733 | 16 | 0.0 | multipass_classic_stage1 |  |
| 软件服务协议-红客厅（中智股份）.pdf | 1 | False | 0.24 | 0 | None |  | OCRExtractionError: OCR_PARSE_FAILED: Fi |
| Employee_Meal_Subsidy_Platfo | 2 | True | 73.26 | 3 | 0.0 | multipass_classic_stage1 |  |
| 关爱通南京与致逸美妆产品返点补充协议.docx | 2 | True | 4.894 | 0 | None | multipass_classic_stage1 |  |
| 员工餐补平台服务合同格式24.3.7.docx | 2 | True | 38.338 | 0 | None | multipass_classic_stage1 |  |
| 薪资税务代理服务合同（最终版）_202500403.do | 2 | True | 82.996 | 12 | 0.0 | multipass_classic_stage1 |  |
| 软件服务协议-红客厅（中智股份）.pdf | 2 | False | 0.12 | 0 | None |  | OCRExtractionError: OCR_PARSE_FAILED: Fi |
