import { act, fireEvent, render, screen, waitFor } from "@testing-library/react"
import { beforeEach, describe, expect, it, vi } from "vitest"

import App from "./App"
import { ApiError } from "./api/base"
import { buildAuditSnapshot, getAuditSnapshotStorageKeys, loadCurrentAuditSnapshot, saveCurrentAuditSnapshot } from "./auditPersistence"
import { auditContract, getAuditProgress, getContractSummary } from "./api"

let currentUser = { id: 1, username: "admin", role: "admin" }

vi.mock("./api", () => ({
  auditContract: vi.fn(),
  downloadContractSource: vi.fn(),
  exportContractReport: vi.fn(),
  getAuditProgress: vi.fn(),
  getContractSummary: vi.fn(async () => ({
    summary: "",
    meta: {
      contract_summary_enabled: false,
      contract_summary_status: "disabled",
      contract_summary_language: "zh",
    },
  })),
  getContractPreview: vi.fn(),
  getContractPreviewManifest: vi.fn(),
  getContractPreviewPageImage: vi.fn(),
  getCurrentUser: vi.fn(() => currentUser),
  getMe: vi.fn(async () => currentUser),
  getUIConfig: vi.fn(async () => ({ show_citation_source: false, default_theme: "dark", preview_continuous_enabled: true })),
  logout: vi.fn(),
}))

vi.mock("./Login", () => ({
  default: function LoginMock() {
    return <div>login</div>
  }
}))

vi.mock("./Admin", () => ({
  default: function AdminMock({ onBack }) {
    return <button onClick={onBack}>BackToMain</button>
  }
}))

vi.mock("./ContractPreviewPanel", () => ({
  default: function ContractPreviewPanelMock({ previewText, previewError }) {
    return <div data-testid="preview-panel">{previewError ? `preview-error:${previewError}` : (previewText || "preview-empty")}</div>
  }
}))

describe("App audit persistence", () => {
  beforeEach(() => {
    localStorage.clear()
    currentUser = { id: 1, username: "admin", role: "admin" }
    vi.mocked(getContractSummary).mockResolvedValue({
      summary: "",
      meta: {
        contract_summary_enabled: false,
        contract_summary_status: "disabled",
        contract_summary_language: "zh",
      },
    })
  })

  it("restores audit result and preview text after remount", async () => {
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "task-restore",
      auditId: "task-restore",
      documentId: "doc-restore",
      contract: {
        title: "测试合同",
        language: "zh",
        auditMode: "rag",
        region: "CN",
        date: "2026-10-15",
        industry: "Retail",
        taxFocus: true,
      },
      contractResult: {
        summary: "恢复成功",
        risks: [{ level: "medium", issue: "发票类型不一致", suggestion: "统一开票要求" }],
      },
      previewMode: "text",
      previewText: "这是恢复的合同正文",
    }), currentUser)

    const { unmount } = render(<App />)
    await waitFor(() => expect(screen.getByText("发票类型不一致")).toBeInTheDocument())
    expect(screen.getByTestId("preview-panel")).toHaveTextContent("这是恢复的合同正文")
    unmount()

    render(<App />)
    await waitFor(() => expect(screen.getByText("发票类型不一致")).toBeInTheDocument())
    expect(screen.getByTestId("preview-panel")).toHaveTextContent("这是恢复的合同正文")
  })

  it("keeps restored content after opening admin and going back", async () => {
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "task-admin",
      auditId: "task-admin",
      documentId: "doc-admin",
      contractResult: {
        summary: "admin keep",
        risks: [{ level: "low", issue: "税率描述不清", suggestion: "补充税率口径" }],
      },
      previewMode: "text",
      previewText: "admin preview",
    }), currentUser)

    render(<App />)
    await waitFor(() => expect(screen.getByText("税率描述不清")).toBeInTheDocument())
    fireEvent.click(screen.getByText("Admin"))
    fireEvent.click(screen.getByText("BackToMain"))
    expect(screen.getByText("税率描述不清")).toBeInTheDocument()
    expect(screen.getByTestId("preview-panel")).toHaveTextContent("admin preview")
  })

  it("shows a friendly warning when persisted data is corrupted", async () => {
    const keys = getAuditSnapshotStorageKeys("broken-task", currentUser)
    localStorage.setItem(keys.currentTaskKey, "broken-task")
    localStorage.setItem(keys.taskKey, "{broken json")

    render(<App />)

    await waitFor(() => expect(screen.getByText("检测到本地审计缓存已损坏，系统已自动忽略该缓存，请重新开始或重新加载任务。")).toBeInTheDocument())
    expect(screen.getByText("合同审计")).toBeInTheDocument()
  })

  it("shows an incomplete-audit warning instead of a zero-risk message", async () => {
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "task-incomplete",
      auditId: "task-incomplete",
      documentId: "doc-incomplete",
      contractResult: {
        summary: "分块经典审计结果可能不完整：共处理 12 个片段，当前识别 0 项风险；其中 3 个片段解析失败，2 个片段输出被截断",
        risks: [],
        audit_incomplete: true,
        legal_validation: {
          ok: false,
          issues: [
            {
              risk_id: "classic_parse",
              message: "LLM 未返回可解析 JSON，审计结果可能不完整",
            },
          ],
        },
      },
      previewMode: "text",
      previewText: "这是恢复的合同正文",
    }), currentUser)

    render(<App />)

    await waitFor(() => expect(screen.getByText("LLM 未返回可解析 JSON，审计结果可能不完整")).toBeInTheDocument())
    expect(screen.getAllByText("本次审计结果不完整，当前结果可能漏检，请结合日志复核后重新审计。").length).toBeGreaterThan(0)
    expect(screen.queryByText("未识别到明显风险")).not.toBeInTheDocument()
  })

  it("uses PDF-specific export labels after restoring a digitized PDF audit", async () => {
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "task-pdf-export",
      auditId: "task-pdf-export",
      documentId: "doc-pdf-export",
      fileMeta: {
        name: "supplier-contract.pdf",
        size: 256,
        type: "application/pdf",
        lastModified: 1,
      },
      contractMeta: {
        digitized_docx_available: true,
        digitization_status: "done",
      },
      contractResult: {
        summary: "pdf export labels",
        risks: [{ level: "high", issue: "逾期退货违约责任过高" }],
      },
      previewMode: "text",
      previewText: "这是恢复的 PDF 合同正文",
    }), currentUser)

    render(<App />)

    await waitFor(() => expect(screen.getByText("逾期退货违约责任过高")).toBeInTheDocument())
    expect(screen.getByRole("button", { name: "导出Word（无批注）" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "导出带批注Word" })).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "导出批注版原件" })).not.toBeInTheDocument()
  })

  it("renders the whole-contract summary above the overview when available", async () => {
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "task-contract-summary",
      auditId: "task-contract-summary",
      documentId: "doc-contract-summary",
      contractMeta: {
        preview_clause_total: 18,
        contract_summary_enabled: true,
        contract_summary_status: "ok",
      },
      contractResult: {
        contract_summary: "合同内容简要摘要\n\n税务审核师快速阅读版\n\n共 1 份合同 / 补充协议\n\n使用说明\n\n本文件仅用于帮助审核人员迅速了解合同交易背景、核心商业安排及主要涉税关注点，不替代对合同原文、附件及实际业务流程的完整审核。\n\n1. 当前合同\n\n合同在说什么：\n本合同主要约定年度供货合作。\n\n核心安排：\n明确了供货、验收、付款开票以及违约终止机制。\n\n税务审核提示：\n需重点核对发票处理与交易实质是否一致。\n\n来源文件：当前合同.docx",
        summary: "共识别 2 项风险",
        risks: [{ level: "medium", issue: "付款节点描述偏粗" }],
      },
      previewMode: "text",
      previewText: "这是恢复的合同正文",
    }), currentUser)

    render(<App />)

    await waitFor(() => expect(screen.getByText(/合同内容简要摘要/)).toBeInTheDocument())
    const titles = screen.getAllByText(/整份合同总结|概览/)
    expect(titles[0]).toHaveTextContent("整份合同总结")
    expect(titles[1]).toHaveTextContent("概览")
    expect(screen.getByText(/摘要状态: 已生成/)).toBeInTheDocument()
  })

  it("shows contract summary status when the summary is empty", async () => {
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "task-contract-summary-empty",
      auditId: "task-contract-summary-empty",
      documentId: "doc-contract-summary-empty",
      contractMeta: {
        preview_clause_total: 9,
        contract_summary_enabled: true,
        contract_summary_status: "empty",
      },
      contractResult: {
        contract_summary: "",
        summary: "共识别 1 项风险",
        risks: [{ level: "low", issue: "发票描述需复核" }],
      },
      previewMode: "text",
      previewText: "这是恢复的合同正文",
    }), currentUser)

    render(<App />)

    await waitFor(() => expect(screen.getByText("整份合同总结")).toBeInTheDocument())
    expect(screen.getByText(/摘要状态: 未生成内容/)).toBeInTheDocument()
    expect(screen.getByText("本次未生成整份合同总结。请结合服务日志检查模型输出是否为空、被截断或解析后为空。")).toBeInTheDocument()
  })

  it("fetches and renders the whole-contract summary above the overview when the audit result does not include it", async () => {
    vi.mocked(getContractSummary).mockResolvedValue({
      summary: "合同内容简要摘要\n\n税务审核师快速阅读版\n\n共 1 份合同 / 补充协议\n\n使用说明\n\n本文件仅用于帮助审核人员迅速了解合同交易背景、核心商业安排及主要涉税关注点，不替代对合同原文、附件及实际业务流程的完整审核。\n\n1. 华润万家购销合同2026年修订版\n\n合同在说什么：\n大型零售商与供应商之间的年度商品购销主合同。\n\n核心安排：\n约定供货、对账、发票、返利及退货机制。\n\n税务审核提示：\n需核对返利折让与开票主体是否一致。\n\n来源文件：华润万家购销合同2026年修订-可编辑文字版.docx",
      meta: {
        contract_summary_enabled: true,
        contract_summary_status: "ok",
        contract_summary_language: "zh",
        preview_clause_total: 22,
      },
    })

    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "task-contract-summary-fetch",
      auditId: "task-contract-summary-fetch",
      documentId: "doc-contract-summary-fetch",
      contractResult: {
        summary: "共识别 1 项风险",
        risks: [{ level: "medium", issue: "返利折让条款需复核" }],
      },
      contractMeta: {
        preview_clause_total: 22,
      },
      previewMode: "text",
      previewText: "这是恢复的合同正文",
    }), currentUser)

    render(<App />)

    await waitFor(() => expect(screen.getByText(/合同内容简要摘要/)).toBeInTheDocument())
    expect(vi.mocked(getContractSummary)).toHaveBeenCalledWith("doc-contract-summary-fetch", "zh")
    const titles = screen.getAllByText(/整份合同总结|概览/)
    expect(titles[0]).toHaveTextContent("整份合同总结")
    expect(titles[1]).toHaveTextContent("概览")
  })

  it("shows only the current user's snapshot history and supports compare", async () => {
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "admin-1",
      auditId: "admin-1",
      documentId: "doc-admin-1",
      contract: { title: "管理员一审" },
      contractResult: {
        summary: "发票口径需要统一；付款节点需要前置复核",
        risks: [{ level: "medium", issue: "发票口径" }, { level: "low", issue: "付款节点" }],
      },
      previewMeta: { page_count: 12, line_total: 90 },
    }), currentUser)
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "admin-2",
      auditId: "admin-2",
      documentId: "doc-admin-2",
      contract: { title: "管理员二审" },
      contractResult: {
        summary: "发票口径需要统一；税率条款已增加特别说明",
        risks: [{ level: "high", issue: "发票口径" }, { level: "medium", issue: "税率条款" }],
      },
      previewMeta: { page_count: 15, line_total: 120 },
    }), currentUser)
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "other-1",
      auditId: "other-1",
      documentId: "doc-other-1",
      contract: { title: "其他用户审计" },
      contractResult: { summary: "should stay isolated", risks: [{ level: "low", issue: "隔离" }] },
    }), { id: 2, username: "auditor-b", role: "auditor" })

    render(<App />)

    await waitFor(() => expect(screen.getByText("管理员一审")).toBeInTheDocument())
    expect(screen.getByText("管理员二审")).toBeInTheDocument()
    expect(screen.queryByText("其他用户审计")).not.toBeInTheDocument()

    fireEvent.click(screen.getAllByLabelText("加入对比")[0])
    fireEvent.click(screen.getAllByLabelText("加入对比")[1])

    expect(await screen.findByText("多快照对比")).toBeInTheDocument()
    expect(screen.getByText("风险总数")).toBeInTheDocument()
    expect(screen.getByText("新增关注点")).toBeInTheDocument()
  })

  it("switches user scope cleanly between different accounts", async () => {
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "admin-only",
      auditId: "admin-only",
      documentId: "doc-admin-only",
      contract: { title: "管理员快照" },
      contractResult: { summary: "admin summary", risks: [{ level: "low", issue: "admin risk" }] },
    }), { id: 1, username: "admin", role: "admin" })
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "auditor-only",
      auditId: "auditor-only",
      documentId: "doc-auditor-only",
      contract: { title: "审计员快照" },
      contractResult: { summary: "auditor summary", risks: [{ level: "high", issue: "auditor risk" }] },
    }), { id: 2, username: "auditor-b", role: "auditor" })

    const { unmount } = render(<App />)
    await waitFor(() => expect(screen.getByText("管理员快照")).toBeInTheDocument())
    expect(screen.queryByText("审计员快照")).not.toBeInTheDocument()
    unmount()

    currentUser = { id: 2, username: "auditor-b", role: "auditor" }
    render(<App />)
    await waitFor(() => expect(screen.getByText("审计员快照")).toBeInTheDocument())
    expect(screen.queryByText("管理员快照")).not.toBeInTheDocument()
  })

  it("starts a new task by clearing visible errors and rotating the current task session", async () => {
    const oldTaskId = "task-with-error"
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: oldTaskId,
      auditId: oldTaskId,
      documentId: "doc-error",
      contract: {
        title: "旧任务",
        language: "zh",
        auditMode: "rag",
        region: "CN",
        date: "2026-10-16",
        industry: "Retail",
        taxFocus: true,
      },
      fileMeta: {
        name: "旧合同.pdf",
        size: 123,
        type: "application/pdf",
        lastModified: 1,
      },
      contractError: {
        title: "无法连接审计服务",
        message: "Failed to fetch",
        code: "NETWORK_REQUEST_FAILED",
        category: "network",
      },
      previewError: "Preview failed",
      exportError: "export failed",
      contractResult: {
        summary: "旧任务摘要",
        risks: [{ level: "high", issue: "旧风险" }],
      },
      previewMode: "text",
      previewText: "旧预览内容",
    }), currentUser)

    const confirmSpy = vi.spyOn(window, "confirm").mockReturnValue(true)

    render(<App />)

    await waitFor(() => expect(screen.getByText("无法连接审计服务")).toBeInTheDocument())
    expect(screen.getByText("已恢复上一次审计任务的本地缓存内容。")).toBeInTheDocument()
    expect(
      screen.getByText((_, node) =>
        String(node?.textContent || "").includes("export failed") && String(node?.className || "").includes("error")
      )
    ).toBeInTheDocument()
    expect(screen.getByTestId("preview-panel")).toHaveTextContent("preview-error:Preview failed")

    fireEvent.click(screen.getByRole("button", { name: "开始新任务" }))

    expect(confirmSpy).toHaveBeenCalled()
    await waitFor(() => expect(screen.queryByText("无法连接审计服务")).not.toBeInTheDocument())
    expect(
      screen.queryByText((_, node) =>
        String(node?.textContent || "").includes("export failed") && String(node?.className || "").includes("error")
      )
    ).not.toBeInTheDocument()
    expect(screen.queryByText("已恢复上一次审计任务的本地缓存内容。")).not.toBeInTheDocument()
    expect(screen.getByTestId("preview-panel")).toHaveTextContent("preview-empty")
    expect(screen.queryByText("旧风险")).not.toBeInTheDocument()

    await waitFor(() => {
      const restored = loadCurrentAuditSnapshot(currentUser)
      expect(restored.snapshot).not.toBeNull()
      expect(restored.snapshot.taskId).not.toBe(oldTaskId)
      expect(restored.snapshot.contractError).toBeNull()
      expect(restored.snapshot.previewError).toBe("")
      expect(restored.snapshot.exportError).toBe("")
      expect(restored.snapshot.contractResult).toBeNull()
    })

    confirmSpy.mockRestore()
  })

  it("disables start new task while the audit is still running", async () => {
    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "running-task",
      auditId: "running-task",
      contractLoading: true,
      contractProgress: 32,
      contractProgressStage: "extracting",
    }), currentUser)

    render(<App />)

    const startNewTaskButton = await screen.findByRole("button", { name: "开始新任务" })
    expect(startNewTaskButton).toBeDisabled()
  })

  it("stops polling when restored progress returns 404 and shows an expired-task notice", async () => {
    vi.useFakeTimers()
    const progressMock = vi.mocked(getAuditProgress)
    progressMock.mockRejectedValue(new ApiError({
      status: 404,
      message: "audit progress not found",
      raw_reason: "audit progress not found",
    }))

    saveCurrentAuditSnapshot(buildAuditSnapshot({
      taskId: "expired-task",
      auditId: "expired-audit",
      contractLoading: true,
      contractProgress: 48,
      contractProgressStage: "auditing",
    }), currentUser)

    render(<App />)

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })
    expect(screen.getAllByText("旧任务进度已失效，请重新发起审计。")).toHaveLength(2)
    const callCountAfter404 = progressMock.mock.calls.length
    expect(callCountAfter404).toBeGreaterThanOrEqual(1)

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(progressMock.mock.calls.length).toBe(callCountAfter404)
    expect(screen.getAllByText("旧任务进度已失效，请重新发起审计。")).toHaveLength(2)

    vi.useRealTimers()
    progressMock.mockReset()
  })

  it("waits through the startup grace period before treating new-task progress 404 as an error", async () => {
    vi.useFakeTimers()
    const progressMock = vi.mocked(getAuditProgress)
    const auditMock = vi.mocked(auditContract)
    progressMock.mockRejectedValue(new ApiError({
      status: 404,
      message: "audit progress not found",
      raw_reason: "audit progress not found",
    }))
    auditMock.mockImplementation(() => new Promise(() => {}))

    const { container } = render(<App />)
    const fileInput = container.querySelector('input[type="file"]')
    expect(fileInput).not.toBeNull()

    await act(async () => {
      fireEvent.change(fileInput, {
        target: {
          files: [new File(["demo"], "new-contract.pdf", { type: "application/pdf" })],
        },
      })
    })

    fireEvent.click(screen.getByRole("button", { name: "开始审计" }))

    await act(async () => {
      await Promise.resolve()
      await Promise.resolve()
    })

    expect(progressMock.mock.calls.length).toBeGreaterThanOrEqual(1)
    expect(screen.queryByText("旧任务进度已失效，请重新发起审计。")).not.toBeInTheDocument()
    expect(screen.queryByText("审计任务启动时间较长，暂未拿到进度信息。请稍候后重试；如果持续无响应，请检查服务日志。")).not.toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5000)
    })
    expect(screen.queryByText("旧任务进度已失效，请重新发起审计。")).not.toBeInTheDocument()
    expect(screen.queryByText("审计任务启动时间较长，暂未拿到进度信息。请稍候后重试；如果持续无响应，请检查服务日志。")).not.toBeInTheDocument()

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1500)
    })
    expect(screen.getByText("审计任务启动时间较长，暂未拿到进度信息。请稍候后重试；如果持续无响应，请检查服务日志。")).toBeInTheDocument()

    vi.useRealTimers()
    progressMock.mockReset()
    auditMock.mockReset()
  })
})
