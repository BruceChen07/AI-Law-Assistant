import { describe, expect, it } from "vitest"

import { CONTRACT_AUDIT_ERROR_CODES, normalizeContractAuditError } from "./errorCatalog"


describe("normalizeContractAuditError", () => {
  it("uses structured backend payload when present", () => {
    const result = normalizeContractAuditError({
      code: "AUDIT_PDF_DIGITIZATION_FAILED",
      category: "pdf_digitization",
      title: "PDF 数字化失败",
      user_message: "PDF 数字化失败，请确认 OCR 组件与 MinerU 环境可用后重试。",
      operator_scenarios: ["MinerU CLI 未安装"],
      operator_actions: ["检查项目 .venv 中的 mineru 可执行文件"],
      retryable: true,
      status: 500
    }, "zh")

    expect(result.code).toBe("AUDIT_PDF_DIGITIZATION_FAILED")
    expect(result.categoryLabel).toBe("PDF 数字化")
    expect(result.scenarios).toEqual(["MinerU CLI 未安装"])
    expect(result.actions).toEqual(["检查项目 .venv 中的 mineru 可执行文件"])
  })

  it("classifies network fetch failures into a stable code", () => {
    const result = normalizeContractAuditError({
      code: CONTRACT_AUDIT_ERROR_CODES.NetworkRequestFailed,
      message: "Failed to fetch",
      status: 0
    }, "zh")

    expect(result.code).toBe(CONTRACT_AUDIT_ERROR_CODES.NetworkRequestFailed)
    expect(result.title).toBe("无法连接审计服务")
    expect(result.scenarios.length).toBeGreaterThan(0)
  })

  it("classifies storage permission errors into a specific maintenance code", () => {
    const result = normalizeContractAuditError({
      message: "[WinError 5] 拒绝访问。: 'D:\\Workspace\\data\\memory\\debug\\rag_retrieval\\2026-10-15'",
      status: 500
    }, "zh")

    expect(result.code).toBe(CONTRACT_AUDIT_ERROR_CODES.StoragePermissionDenied)
    expect(result.categoryLabel).toBe("目录权限")
  })

  it("infers timeout errors from plain text when the backend only returns a string", () => {
    const result = normalizeContractAuditError({
      message: "LLM request timed out. Check provider availability.",
      status: 500
    }, "en")

    expect(result.code).toBe(CONTRACT_AUDIT_ERROR_CODES.LlmTimeout)
    expect(result.categoryLabel).toBe("Model Timeout")
  })

  it("classifies connection refused errors into a specific model connection code", () => {
    const result = normalizeContractAuditError({
      message: "llm request failed: httpx.ConnectError: [WinError 10061] 由于目标计算机积极拒绝，无法连接。",
      status: 500
    }, "zh")

    expect(result.code).toBe(CONTRACT_AUDIT_ERROR_CODES.LlmConnectionFailed)
    expect(result.categoryLabel).toBe("模型连接")
  })
})
