export const CONTRACT_AUDIT_ERROR_CODES = Object.freeze({
  UnsupportedFileType: "AUDIT_UNSUPPORTED_FILE_TYPE",
  NetworkRequestFailed: "NETWORK_REQUEST_FAILED",
  Unauthorized: "AUDIT_UNAUTHORIZED",
  Forbidden: "AUDIT_FORBIDDEN",
  ResourceNotFound: "AUDIT_RESOURCE_NOT_FOUND",
  BadRequest: "AUDIT_BAD_REQUEST",
  StoragePermissionDenied: "AUDIT_STORAGE_PERMISSION_DENIED",
  ServiceUnavailable: "AUDIT_SERVICE_UNAVAILABLE",
  PdfDigitizationFailed: "AUDIT_PDF_DIGITIZATION_FAILED",
  LlmAuthFailed: "AUDIT_LLM_AUTH_FAILED",
  LlmConnectionFailed: "AUDIT_LLM_CONNECTION_FAILED",
  LlmTimeout: "AUDIT_LLM_TIMEOUT",
  OcrFailed: "AUDIT_OCR_FAILED",
  RuntimeFailed: "AUDIT_RUNTIME_FAILED"
})

const CATEGORY_LABELS = {
  zh: {
    validation: "输入校验",
    network: "网络连接",
    auth: "认证授权",
    storage_permission: "目录权限",
    pdf_digitization: "PDF 数字化",
    llm_auth: "模型鉴权",
    llm_connection: "模型连接",
    llm_timeout: "模型超时",
    ocr_runtime: "OCR 运行",
    service_unavailable: "依赖服务",
    runtime: "运行时"
  },
  en: {
    validation: "Validation",
    network: "Network",
    auth: "Authentication",
    storage_permission: "Storage Permission",
    pdf_digitization: "PDF Digitization",
    llm_auth: "Model Auth",
    llm_connection: "Model Connection",
    llm_timeout: "Model Timeout",
    ocr_runtime: "OCR Runtime",
    service_unavailable: "Dependencies",
    runtime: "Runtime"
  }
}

const ERROR_SPECS = {
  [CONTRACT_AUDIT_ERROR_CODES.UnsupportedFileType]: {
    category: "validation",
    title: {
      zh: "上传文件格式不支持",
      en: "Unsupported file type"
    },
    message: {
      zh: "请上传 docx 或 pdf 文档后重试。",
      en: "Upload a docx or pdf file and retry."
    },
    scenarios: {
      zh: [
        "上传了非 docx/pdf 文件。",
        "文件扩展名被改写，和真实内容不一致。"
      ],
      en: [
        "A non-docx/pdf file was uploaded.",
        "The file extension was renamed and no longer matches the actual content."
      ]
    },
    actions: {
      zh: [
        "确认文件真实格式是否为 Word 或 PDF。",
        "必要时重新导出合同文件后再上传。"
      ],
      en: [
        "Confirm the real file format is Word or PDF.",
        "If needed, export the contract again and upload the new file."
      ]
    }
  },
  [CONTRACT_AUDIT_ERROR_CODES.NetworkRequestFailed]: {
    category: "network",
    title: {
      zh: "无法连接审计服务",
      en: "Cannot reach the audit service"
    },
    message: {
      zh: "当前浏览器没有成功连接到后端服务，请稍后重试。",
      en: "The browser could not reach the backend service. Retry in a moment."
    },
    scenarios: {
      zh: [
        "后端服务未启动，或端口与前端配置不一致。",
        "浏览器请求被本机其他服务、代理或端口占用干扰。"
      ],
      en: [
        "The backend service is down, or the backend port does not match the frontend configuration.",
        "Another local service, proxy, or port conflict intercepted the browser request."
      ]
    },
    actions: {
      zh: [
        "检查后端健康地址与前端 API_BASE 是否一致。",
        "确认目标端口没有被其他本机服务占用。"
      ],
      en: [
        "Confirm the backend health URL matches the frontend API_BASE.",
        "Make sure the target port is not occupied by another local service."
      ]
    }
  },
  [CONTRACT_AUDIT_ERROR_CODES.Unauthorized]: {
    category: "auth",
    title: {
      zh: "登录状态失效",
      en: "Login session expired"
    },
    message: {
      zh: "当前登录状态无效，请重新登录后重试。",
      en: "The current login session is invalid. Sign in again and retry."
    },
    scenarios: {
      zh: [
        "登录 token 过期或未写入浏览器本地存储。",
        "当前请求打到了错误的后端地址，导致 token 无法识别。"
      ],
      en: [
        "The login token expired or was not persisted in local storage.",
        "The request reached the wrong backend address and the token was rejected."
      ]
    },
    actions: {
      zh: [
        "重新登录，确认当前浏览器保存了最新 token。",
        "核对前端请求地址是否指向正确的项目后端。"
      ],
      en: [
        "Sign in again and confirm the browser stores the latest token.",
        "Verify the frontend is sending requests to the correct project backend."
      ]
    }
  },
  [CONTRACT_AUDIT_ERROR_CODES.Forbidden]: {
    category: "auth",
    title: {
      zh: "当前账号无权执行该操作",
      en: "The current account cannot perform this action"
    },
    message: {
      zh: "当前账号权限不足，请联系管理员确认角色配置。",
      en: "The current account does not have permission. Contact an administrator to review the role settings."
    },
    scenarios: {
      zh: [
        "账号角色不满足接口要求。",
        "用户状态被停用或权限配置被调整。"
      ],
      en: [
        "The account role does not meet the endpoint requirement.",
        "The user was disabled or the permission configuration changed."
      ]
    },
    actions: {
      zh: [
        "检查当前用户角色与账号状态。",
        "必要时用管理员账号重新验证一次。"
      ],
      en: [
        "Inspect the current user role and account status.",
        "If needed, verify the flow again with an administrator account."
      ]
    }
  },
  [CONTRACT_AUDIT_ERROR_CODES.ResourceNotFound]: {
    category: "runtime",
    title: {
      zh: "请求的资源不存在",
      en: "Requested resource was not found"
    },
    message: {
      zh: "当前审计依赖的文档或中间结果不存在，请重新上传后再试。",
      en: "A required document or intermediate artifact is missing. Upload the file again and retry."
    },
    scenarios: {
      zh: [
        "文档已被删除或缓存目录中的文件被清理。",
        "数字化稿、预览图或导出文件尚未生成完成。"
      ],
      en: [
        "The document was deleted or files in the cache directory were cleaned up.",
        "The digitized file, preview image, or export artifact is not ready yet."
      ]
    },
    actions: {
      zh: [
        "确认原始合同文件仍存在于上传目录。",
        "必要时重新上传并重新跑一次审计。"
      ],
      en: [
        "Confirm the original contract file still exists in the upload directory.",
        "If needed, re-upload the file and run the audit again."
      ]
    }
  },
  [CONTRACT_AUDIT_ERROR_CODES.BadRequest]: {
    category: "validation",
    title: {
      zh: "请求参数不合法",
      en: "Invalid request parameters"
    },
    message: {
      zh: "提交参数没有通过校验，请检查表单内容后重试。",
      en: "The submitted parameters did not pass validation. Review the form values and retry."
    },
    scenarios: {
      zh: [
        "页码、导出格式、语言或其他表单字段取值不合法。",
        "前端与后端字段定义发生了偏差。"
      ],
      en: [
        "A page number, export format, language, or another form field is invalid.",
        "The frontend and backend field definitions have drifted apart."
      ]
    },
    actions: {
      zh: [
        "检查本次提交的表单参数。",
        "结合错误码确认是否需要同步前后端字段定义。"
      ],
      en: [
        "Inspect the submitted form parameters.",
        "Use the error code to determine whether frontend and backend field definitions need to be synchronized."
      ]
    }
  },
  [CONTRACT_AUDIT_ERROR_CODES.ServiceUnavailable]: {
    category: "service_unavailable",
    title: {
      zh: "依赖服务当前不可用",
      en: "A required service is unavailable"
    },
    message: {
      zh: "审计依赖的模型或检索服务当前不可用，请稍后重试。",
      en: "A model or retrieval dependency required by the audit is currently unavailable. Retry later."
    },
    scenarios: {
      zh: [
        "LLM、Embedding、Reranker 或 Translation 服务未启动。",
        "当前配置指向了错误的 provider、端口或模型角色。"
      ],
      en: [
        "The LLM, embedding, reranker, or translation service is not running.",
        "The current configuration points to the wrong provider, port, or model role."
      ]
    },
    actions: {
      zh: [
        "优先检查依赖服务的健康状态。",
        "确认项目当前读取到的 provider 与端口配置正确。"
      ],
      en: [
        "Check the health status of dependent services first.",
        "Confirm the project is reading the correct provider and port configuration."
      ]
    }
  },
  [CONTRACT_AUDIT_ERROR_CODES.StoragePermissionDenied]: {
    category: "storage_permission",
    title: {
      zh: "服务目录没有写入权限",
      en: "The service cannot write to the target directory"
    },
    message: {
      zh: "服务在写入缓存、日志或调试目录时被系统拒绝，本次审计已中止。",
      en: "The service was denied while writing cache, log, or debug artifacts, so the audit was stopped."
    },
    scenarios: {
      zh: [
        "调试目录、日志目录或缓存目录被解析到了项目外层路径，当前进程没有写权限。",
        "Windows 对当前运行用户限制了目标目录的创建或写入。"
      ],
      en: [
        "A debug, log, or cache directory was resolved outside the project tree and the current process cannot write there.",
        "Windows denied directory creation or write access for the current service user."
      ]
    },
    actions: {
      zh: [
        "确认 rag_trace_dir、llm_trace_dir、data_dir 都解析到了项目工作区内。",
        "检查当前服务运行用户是否拥有目标目录写权限。"
      ],
      en: [
        "Confirm rag_trace_dir, llm_trace_dir, and data_dir all resolve inside the project workspace.",
        "Check whether the current service user has write access to the target directory."
      ]
    }
  },
  [CONTRACT_AUDIT_ERROR_CODES.PdfDigitizationFailed]: {
    category: "pdf_digitization",
    title: {
      zh: "PDF 数字化失败",
      en: "PDF digitization failed"
    },
    message: {
      zh: "审计前的 PDF 数字化处理未完成，本次审计已中止。",
      en: "The PDF digitization step did not complete before the audit, so the audit was stopped."
    },
    scenarios: {
      zh: [
        "MinerU CLI 未安装，或服务没有命中项目虚拟环境中的 mineru。",
        "MinerU pipeline 缺少 doclayout_yolo 等布局依赖。",
        "MinerU 执行后没有生成 _content_list.json 等结构化结果文件。"
      ],
      en: [
        "MinerU CLI is missing or the service is not using the mineru executable from the project environment.",
        "The MinerU pipeline is missing layout dependencies such as doclayout_yolo.",
        "MinerU finished without generating structured outputs such as _content_list.json."
      ]
    },
    actions: {
      zh: [
        "确认服务实际调用的是项目 .venv 中的 mineru。",
        "检查 requirements 和本地虚拟环境中的 MinerU 依赖是否一致。",
        "查看 main.log 中本次 digitize_pdf_start 之后的 stdout/stderr。"
      ],
      en: [
        "Confirm the service is calling the mineru executable from the project .venv.",
        "Verify MinerU dependencies in requirements match the active virtual environment.",
        "Inspect stdout/stderr after digitize_pdf_start in main.log."
      ]
    }
  },
  [CONTRACT_AUDIT_ERROR_CODES.LlmAuthFailed]: {
    category: "llm_auth",
    title: {
      zh: "模型鉴权失败",
      en: "Model authentication failed"
    },
    message: {
      zh: "当前模型服务鉴权失败，本次审计未能继续执行。",
      en: "Authentication with the active model provider failed, so the audit could not continue."
    },
    scenarios: {
      zh: [
        "Windows 环境变量中的密钥缺失、过期或不匹配当前 provider。",
        "运行时命中了错误的 provider/base/model/key 组合。"
      ],
      en: [
        "The key from Windows environment variables is missing, expired, or does not match the current provider.",
        "The runtime selected the wrong provider/base/model/key combination."
      ]
    },
    actions: {
      zh: [
        "核对当前进程读到的 provider、api_base、model 和 key。",
        "确认测试环境与内网环境没有混用不同凭据。"
      ],
      en: [
        "Verify the provider, api_base, model, and key read by the current process.",
        "Make sure test and intranet credentials are not mixed."
      ]
    }
  },
  [CONTRACT_AUDIT_ERROR_CODES.LlmConnectionFailed]: {
    category: "llm_connection",
    title: {
      zh: "模型服务连接失败",
      en: "Cannot connect to the model service"
    },
    message: {
      zh: "服务未能连接到当前生效的模型地址，本次审计已中止。",
      en: "The service could not connect to the active model endpoint, so the audit was stopped."
    },
    scenarios: {
      zh: [
        "本地 Ollama 没有启动，或 11434 端口当前没有监听。",
        "运行时路由仍然命中了本地模型，但用户预期应使用 DeepSeek 等云端 provider。",
        "模型地址、端口或代理配置错误，导致 TCP 连接被拒绝。"
      ],
      en: [
        "Local Ollama is not running, or nothing is listening on port 11434.",
        "The runtime still routed to a local model even though a cloud provider such as DeepSeek was expected.",
        "The configured model endpoint, port, or proxy is incorrect, so the TCP connection was refused."
      ]
    },
    actions: {
      zh: [
        "查看 main.log 中 llm_request_start 的 provider、api_base 和 selected_role。",
        "如果是本地模型，确认 Ollama 进程和端口监听状态；如果是云端测试，确认合同审计是否已切到 cloud_fallback。",
        "检查环境变量与 app/config.json 中 local_llm 路由策略是否一致。"
      ],
      en: [
        "Inspect provider, api_base, and selected_role in llm_request_start inside main.log.",
        "If this is a local model, confirm the Ollama process and port listener; if it is a cloud test, confirm contract audit has switched to cloud_fallback.",
        "Verify environment variables and the local_llm routing policy in app/config.json are aligned."
      ]
    }
  },
  [CONTRACT_AUDIT_ERROR_CODES.LlmTimeout]: {
    category: "llm_timeout",
    title: {
      zh: "模型响应超时",
      en: "Model request timed out"
    },
    message: {
      zh: "模型在限定时间内未返回结果，本次审计已中止。",
      en: "The model did not return a result within the configured timeout, so the audit was stopped."
    },
    scenarios: {
      zh: [
        "长合同 multipass 审计导致单次请求耗时过长。",
        "当前模型可用，但负载或上下文长度导致推理超时。"
      ],
      en: [
        "A long-contract multipass audit caused an overly long single request.",
        "The current model is available, but load or context size caused a timeout."
      ]
    },
    actions: {
      zh: [
        "检查 timeout 与 timeout_retry 配置是否足够。",
        "必要时切换到更稳定的模型或降低长合同并发。"
      ],
      en: [
        "Check whether timeout and timeout_retry are high enough.",
        "If needed, switch to a more stable model or reduce long-contract concurrency."
      ]
    }
  },
  [CONTRACT_AUDIT_ERROR_CODES.OcrFailed]: {
    category: "ocr_runtime",
    title: {
      zh: "OCR 运行失败",
      en: "OCR runtime failed"
    },
    message: {
      zh: "OCR 未能正确抽取 PDF 文本，本次审计已中止。",
      en: "OCR failed to extract PDF text correctly, so the audit was stopped."
    },
    scenarios: {
      zh: [
        "torchvision、onnxruntime 等 OCR 依赖缺失或版本不兼容。",
        "扫描版 PDF 需要 OCR，但当前 OCR 引擎未正确初始化。"
      ],
      en: [
        "OCR dependencies such as torchvision or onnxruntime are missing or incompatible.",
        "The scanned PDF requires OCR, but the current OCR engine did not initialize correctly."
      ]
    },
    actions: {
      zh: [
        "检查 OCR 依赖版本与模型文件。",
        "在终端单独运行 OCR 或 MinerU 命令确认能否成功执行。"
      ],
      en: [
        "Check OCR dependency versions and model files.",
        "Run the OCR or MinerU command directly in a terminal to confirm it completes successfully."
      ]
    }
  },
  [CONTRACT_AUDIT_ERROR_CODES.RuntimeFailed]: {
    category: "runtime",
    title: {
      zh: "审计运行失败",
      en: "Audit runtime failed"
    },
    message: {
      zh: "审计过程中发生未归类异常，请结合日志继续排查。",
      en: "An uncategorized audit error occurred. Use the logs for further investigation."
    },
    scenarios: {
      zh: [
        "合同解析、检索、导出或审计编排链路中出现未覆盖异常。",
        "最近代码改动后，中间产物字段与下游预期不一致。"
      ],
      en: [
        "An uncovered exception occurred in parsing, retrieval, export, or orchestration.",
        "A recent code change introduced a mismatch between an intermediate payload and downstream expectations."
      ]
    },
    actions: {
      zh: [
        "查看 main.log 中本次请求对应的异常栈。",
        "按错误码补充分支分类或回退策略。"
      ],
      en: [
        "Inspect the exception stack for this request in main.log.",
        "Use the error code to add a more specific classification or fallback strategy."
      ]
    }
  }
}

const KEYWORD_RULES = [
  { code: CONTRACT_AUDIT_ERROR_CODES.PdfDigitizationFailed, keywords: ["digitize_", "content_list_missing", "content_list_empty", "mineru cli not found", "doclayout_yolo", "mineru"] },
  { code: CONTRACT_AUDIT_ERROR_CODES.LlmAuthFailed, keywords: ["invalid_api_key", "incorrect api key", "authentication", "unauthorized", "401"] },
  { code: CONTRACT_AUDIT_ERROR_CODES.LlmConnectionFailed, keywords: ["connecterror", "connection refused", "actively refused", "目标计算机积极拒绝", "winerror 10061", "failed to establish a new connection"] },
  { code: CONTRACT_AUDIT_ERROR_CODES.LlmTimeout, keywords: ["timeout", "timed out", "readtimeout", "read timeout"] },
  { code: CONTRACT_AUDIT_ERROR_CODES.OcrFailed, keywords: ["ocr_parse_failed", "ocr_failed", "torchvision", "onnxruntime"] },
  { code: CONTRACT_AUDIT_ERROR_CODES.ServiceUnavailable, keywords: ["service unavailable", "llm service unavailable", "embedding service unavailable", "reranker service unavailable", "translation service unavailable", "503"] },
  { code: CONTRACT_AUDIT_ERROR_CODES.StoragePermissionDenied, keywords: ["permissionerror", "permission denied", "winerror 5", "拒绝访问", "access is denied"] },
  { code: CONTRACT_AUDIT_ERROR_CODES.Unauthorized, keywords: ["not authenticated", "invalid or expired token", "401"] },
  { code: CONTRACT_AUDIT_ERROR_CODES.Forbidden, keywords: ["admin permission required", "403", "forbidden"] },
  { code: CONTRACT_AUDIT_ERROR_CODES.ResourceNotFound, keywords: ["not found", "404"] },
  { code: CONTRACT_AUDIT_ERROR_CODES.BadRequest, keywords: ["unsupported file type", "invalid page number", "variant must be", "400"] }
]

function normalizeLocale(locale) {
  return String(locale || "").toLowerCase().startsWith("en") ? "en" : "zh"
}

function cloneList(list) {
  return Array.isArray(list) ? list.filter((item) => String(item || "").trim()) : []
}

export function inferContractAuditErrorCode(input, status = 0) {
  const text = String(input || "").toLowerCase()
  if (status === 401) return CONTRACT_AUDIT_ERROR_CODES.Unauthorized
  if (status === 403) return CONTRACT_AUDIT_ERROR_CODES.Forbidden
  if (status === 404) return CONTRACT_AUDIT_ERROR_CODES.ResourceNotFound
  if (status === 400) return CONTRACT_AUDIT_ERROR_CODES.BadRequest
  if (status === 503) return CONTRACT_AUDIT_ERROR_CODES.ServiceUnavailable
  for (const rule of KEYWORD_RULES) {
    if (rule.keywords.some((keyword) => text.includes(keyword))) {
      return rule.code
    }
  }
  return CONTRACT_AUDIT_ERROR_CODES.RuntimeFailed
}

export function getContractAuditErrorSpec(code) {
  return ERROR_SPECS[code] || ERROR_SPECS[CONTRACT_AUDIT_ERROR_CODES.RuntimeFailed]
}

export function normalizeContractAuditError(input, locale = "zh") {
  const lang = normalizeLocale(locale)
  const raw = input && typeof input === "object" ? input : { message: String(input || "") }
  const rawText = [
    raw.code,
    raw.message,
    raw.user_message,
    raw.title,
    raw.raw_reason,
    raw.detail
  ].filter(Boolean).join(" | ")
  const code = String(raw.code || inferContractAuditErrorCode(rawText, Number(raw.status || 0)))
  const spec = getContractAuditErrorSpec(code)
  const category = String(raw.category || spec.category || "runtime")
  const scenarios = cloneList(raw.operator_scenarios).length > 0
    ? cloneList(raw.operator_scenarios)
    : cloneList(spec.scenarios?.[lang])
  const actions = cloneList(raw.operator_actions).length > 0
    ? cloneList(raw.operator_actions)
    : cloneList(spec.actions?.[lang])
  const title = String(raw.title || spec.title?.[lang] || spec.title?.zh || "")
  const message = String(
    raw.user_message
      || raw.message
      || spec.message?.[lang]
      || spec.message?.zh
      || ""
  ).trim()
  return {
    code,
    category,
    categoryLabel: CATEGORY_LABELS[lang]?.[category] || category,
    title,
    message,
    scenarios,
    actions,
    retryable: typeof raw.retryable === "boolean" ? raw.retryable : true,
    status: Number(raw.status || 0),
    rawReason: String(raw.raw_reason || raw.detail || raw.message || "").trim()
  }
}
