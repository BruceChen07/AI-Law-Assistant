import React, { useEffect, useMemo, useRef, useState } from "react"
import { auditContract, getContractPreview, getCurrentUser, getMe, getUIConfig, logout } from "./api"
import Login from "./Login"
import Admin from "./Admin"

export default function App() {
  const [view, setView] = useState("main")
  const [user, setUser] = useState(getCurrentUser())

  useEffect(() => {
    const syncUser = async () => {
      const localUser = getCurrentUser()
      if (!localUser) return
      try {
        const me = await getMe()
        localStorage.setItem("user", JSON.stringify(me))
        setUser(me)
      } catch {
        logout()
        setUser(null)
      }
    }
    syncUser()
  }, [])
  const [uiLang, setUiLang] = useState("zh")
  const [contract, setContract] = useState({
    title: "",
    language: "zh",
    auditMode: "rag",
    region: "",
    date: "",
    industry: "",
    taxFocus: true
  })
  const [contractFile, setContractFile] = useState(null)
  const [contractError, setContractError] = useState("")
  const [contractLoading, setContractLoading] = useState(false)
  const [contractResult, setContractResult] = useState(null)
  const [contractMeta, setContractMeta] = useState(null)
  const [documentId, setDocumentId] = useState("")
  const [uiConfig, setUiConfig] = useState({ showCitationSource: false })
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState("")
  const [previewText, setPreviewText] = useState("")
  const [previewMeta, setPreviewMeta] = useState(null)
  const [activeRiskIndex, setActiveRiskIndex] = useState(-1)
  const [riskFilter, setRiskFilter] = useState("all")
  const previewScrollRef = useRef(null)

  const i18n = {
    zh: {
      appTitle: "合同审计",
      appSubtitle: "上传合同，生成财税与法律风险审计",
      uiLanguage: "界面语言",
      chooseFile: "选择文件",
      noFileChosen: "未选择任何文件",
      uploadBtn: "开始审计",
      contractTitle: "合同标题",
      auditResult: "审计结果",
      summary: "概览",
      executiveOpinion: "税务团队优先处理",
      risks: "风险清单",
      emptyRisk: "未识别到明显风险",
      emptyOpinion: "暂无可执行审核意见",
      evidenceTitle: "关联法规证据",
      citationLabel: "证据来源",
      legalBasis: "法规依据",
      ocrMeta: "OCR",
      pageMeta: "页数",
      modelMeta: "模型",
      retrievalMode: "检索模式",
      evidenceMeta: "证据条数",
      queryMeta: "检索查询",
      promptMeta: "估算Token",
      taxFocus: "仅输出税务相关",
      taxFocusMeta: "税务聚焦",
      region: "地区",
      date: "日期",
      industry: "行业",
      auditMode: "审计模式",
      modeRag: "RAG增强",
      modeBaseline: "基础模式",
      invalidFileType: "文档格式不对，请上传 docx 或 pdf 文档。",
      uploading: "审计中...",
      uploadHint: "支持 docx / pdf / 扫描版 pdf",
      progressTitle: "处理进度",
      progressWorking: "处理中...",
      progressUploading: "上传文件",
      progressExtracting: "解析合同",
      progressRetrieval: "检索法规",
      progressAuditing: "风险识别",
      progressDone: "完成",
      progressFailed: "失败",
      contractPreview: "合同预览",
      previewLoading: "正在加载合同预览...",
      previewEmpty: "暂无可预览的合同原文",
      previewHint: "左侧仅展示原文预览，不做条款切分",
      previewError: "预览加载失败",
      location: "定位",
      pageParagraph: "页/段",
      noLocation: "未定位到具体条款",
      clauseCount: "条款数",
      lineCount: "行数",
      riskFilter: "风险筛选",
      riskAll: "全部",
      riskHigh: "高风险",
      riskMedium: "中风险",
      riskLow: "低风险",
      riskShown: "当前显示"
    },
    en: {
      appTitle: "Contract Audit",
      appSubtitle: "Upload contracts and generate compliance risk audit",
      uiLanguage: "UI Language",
      chooseFile: "Choose File",
      noFileChosen: "No file chosen",
      uploadBtn: "Audit",
      contractTitle: "Contract Title",
      auditResult: "Audit Result",
      summary: "Summary",
      executiveOpinion: "Tax Team Priorities",
      risks: "Risk List",
      emptyRisk: "No obvious risks detected",
      emptyOpinion: "No actionable opinions",
      evidenceTitle: "Linked Evidence",
      citationLabel: "Citation Source",
      legalBasis: "Legal Basis",
      ocrMeta: "OCR",
      pageMeta: "Pages",
      modelMeta: "Model",
      retrievalMode: "Mode",
      evidenceMeta: "Evidence",
      queryMeta: "Queries",
      promptMeta: "Est Tokens",
      taxFocus: "Tax-only output",
      taxFocusMeta: "Tax Focus",
      region: "Region",
      date: "Date",
      industry: "Industry",
      auditMode: "Audit Mode",
      modeRag: "RAG",
      modeBaseline: "Baseline",
      invalidFileType: "Invalid file type. Please upload docx or pdf documents.",
      uploading: "Auditing...",
      uploadHint: "Supports docx / pdf / scanned pdf",
      progressTitle: "Progress",
      progressWorking: "Working...",
      progressUploading: "Uploading",
      progressExtracting: "Parsing",
      progressRetrieval: "Retrieving",
      progressAuditing: "Auditing",
      progressDone: "Completed",
      progressFailed: "Failed",
      contractPreview: "Contract Preview",
      previewLoading: "Loading contract preview...",
      previewEmpty: "No previewable contract text available",
      previewHint: "Left panel only shows full-text preview without clause splitting",
      previewError: "Failed to load preview",
      location: "Location",
      pageParagraph: "Page/Para",
      noLocation: "No linked clause",
      clauseCount: "Clauses",
      lineCount: "Lines",
      riskFilter: "Risk Filter",
      riskAll: "All",
      riskHigh: "High",
      riskMedium: "Medium",
      riskLow: "Low",
      riskShown: "Showing"
    }
  }
  const t = i18n[uiLang] || i18n.zh
  const fileInputRef = useRef(null)
  const citationList = Array.isArray(contractResult?.citations) ? contractResult.citations : []
  const citationMap = citationList.reduce((acc, c) => {
    const key = String(c?.citation_id || "").trim()
    if (!key) return acc
    acc[key] = c
    return acc
  }, {})
  const normalizeArticleNo = (value) => {
    const v = String(value || "").trim()
    if (!v) return ""
    if (v.includes("条")) return v
    if (v.startsWith("第")) return `${v}条`
    return `第${v}条`
  }
  const buildCitationDisplay = (citation) => {
    if (!citation) return ""
    const lawTitle = String(citation.law_title || citation.title || "").trim()
    const articleNo = normalizeArticleNo(citation.article_no)
    const excerpt = String(citation.excerpt || citation.content || "").trim()
    const titlePart = lawTitle ? `《${lawTitle}》` : ""
    const articlePart = articleNo ? `${articleNo}` : ""
    const summary = excerpt ? `（摘要：${excerpt}）` : ""
    return `${titlePart}${articlePart}${summary}`.trim()
  }
  const toContractTitle = (filename) => {
    const name = String(filename || "").trim()
    if (!name) return ""
    return name.replace(/\.[^.]+$/, "")
  }
  const riskList = useMemo(() => (
    Array.isArray(contractResult?.risks) ? contractResult.risks : []
  ), [contractResult])
  const riskSummary = useMemo(() => {
    const summary = { high: 0, medium: 0, low: 0, all: riskList.length }
    riskList.forEach((risk) => {
      const level = String(risk?.level || "").toLowerCase()
      if (level === "high" || level === "medium" || level === "low") {
        summary[level] += 1
      }
    })
    return summary
  }, [riskList])
  const filteredRisks = useMemo(() => {
    if (riskFilter === "all") return riskList
    return riskList.filter((risk) => String(risk?.level || "").toLowerCase() === riskFilter)
  }, [riskList, riskFilter])

  useEffect(() => {
    setActiveRiskIndex(-1)
  }, [riskFilter, documentId])

  useEffect(() => {
    if (!user) return
    const loadUIConfig = async () => {
      try {
        const data = await getUIConfig()
        setUiConfig({ showCitationSource: !!data.show_citation_source })
      } catch {
      }
    }
    loadUIConfig()
  }, [user])

  const formatRiskLocation = (location) => {
    const pageNo = Number(location?.page_no || 0)
    const paragraphNo = String(location?.paragraph_no || "").trim()
    if (pageNo > 0 || paragraphNo) {
      if (uiLang === "zh") return `第${pageNo || "-"}页 / 第${paragraphNo || "-"}段`
      return `P${pageNo || "-"} / Para ${paragraphNo || "-"}`
    }
    return t.noLocation
  }

  const loadContractPreview = async (nextDocumentId) => {
    if (!nextDocumentId) {
      setPreviewText("")
      setPreviewMeta(null)
      setPreviewError("")
      return
    }
    setPreviewLoading(true)
    setPreviewError("")
    try {
      const preview = await getContractPreview(nextDocumentId)
      setPreviewText(String(preview?.text || ""))
      setPreviewMeta(preview?.meta || null)
    } catch (err) {
      setPreviewError(String(err?.message || err || "Preview failed"))
      setPreviewText("")
      setPreviewMeta(null)
    } finally {
      setPreviewLoading(false)
    }
  }

  const onRiskLocate = (_risk, idx) => {
    setActiveRiskIndex(idx)
    const container = previewScrollRef.current
    if (!container) return
    container.scrollTo({ top: 0, behavior: "smooth" })
  }

  const onContractUpload = async (e) => {
    e.preventDefault()
    if (!contractFile) return
    const ext = contractFile.name.toLowerCase().split(".").pop()
    if (!["docx", "pdf"].includes(ext)) {
      setContractError(t.invalidFileType)
      return
    }
    setContractError("")
    setContractLoading(true)
    setRiskFilter("all")
    setActiveRiskIndex(-1)
    try {
      const form = new FormData()
      form.append("title", toContractTitle(contractFile.name))
      form.append("language", contract.language)
      form.append("audit_mode", contract.auditMode)
      form.append("region", contract.region)
      form.append("date", contract.date)
      form.append("industry", contract.industry)
      form.append("tax_focus", String(contract.taxFocus))
      form.append("file", contractFile)
      const res = await auditContract(form)
      const nextDocumentId = String(res.document_id || "")
      setContractResult(res.result || null)
      setContractMeta(res.meta || null)
      setDocumentId(nextDocumentId)
      await loadContractPreview(nextDocumentId)
    } catch (err) {
      setContractError(String(err?.message || err || "Audit failed"))
      setContractResult(null)
      setContractMeta(null)
      setDocumentId("")
      setPreviewText("")
      setPreviewMeta(null)
      setPreviewError("")
    } finally {
      setContractLoading(false)
    }
  }

  const onUiLanguageChange = (next) => {
    setUiLang(next)
    setContract(prev => ({ ...prev, language: next }))
  }

  if (!user) {
    return <Login onLogin={() => { setUser(getCurrentUser()); setView("main") }} />
  }

  const canOpenAdmin = user.role === "admin" || user.username === "admin"

  if (view === "admin") {
    return <Admin lang={uiLang} onBack={() => setView("main")} />
  }

  return (
    <div className="page contract-shell">
      <header className="contract-header">
        <div className="contract-heading">
          <div className="contract-eyebrow">Tax & Compliance</div>
          <h1>{t.appTitle}</h1>
          <p>{t.appSubtitle}</p>
        </div>
        <div className="contract-actions">
          <div className="lang-pill">
            <span>{t.uiLanguage}</span>
            <select value={uiLang} onChange={e => onUiLanguageChange(e.target.value)}>
              <option value="zh">中文</option>
              <option value="en">English</option>
            </select>
          </div>
          <div className="user-pill">
            <span>{user.username}</span>
            <em>{user.role}</em>
            {canOpenAdmin && <button className="ghost" onClick={() => setView("admin")}>Admin</button>}
            <button className="ghost" onClick={() => { logout(); setUser(null); }}>Logout</button>
          </div>
        </div>
      </header>
      <section className="card contract-upload">
        <div className="contract-panel">
          <div className="panel-title">{t.contractTitle}</div>
          <input
            className="contract-input"
            placeholder={t.noFileChosen}
            value={contract.title}
            readOnly
          />
          <div className="grid">
            <label>
              <div className="panel-title">{t.auditMode}</div>
              <select
                value={contract.auditMode}
                onChange={e => setContract({ ...contract, auditMode: e.target.value })}
              >
                <option value="rag">{t.modeRag}</option>
                <option value="baseline">{t.modeBaseline}</option>
              </select>
            </label>
            <label>
              <div className="panel-title">{t.region}</div>
              <input
                value={contract.region}
                onChange={e => setContract({ ...contract, region: e.target.value })}
              />
            </label>
            <label>
              <div className="panel-title">{t.date}</div>
              <input
                value={contract.date}
                placeholder="YYYY-MM-DD"
                onChange={e => setContract({ ...contract, date: e.target.value })}
              />
            </label>
            <label>
              <div className="panel-title">{t.industry}</div>
              <input
                value={contract.industry}
                onChange={e => setContract({ ...contract, industry: e.target.value })}
              />
            </label>
            <label>
              <div className="panel-title">{t.taxFocus}</div>
              <select
                value={contract.taxFocus ? "true" : "false"}
                onChange={e => setContract({ ...contract, taxFocus: e.target.value === "true" })}
              >
                <option value="true">{uiLang === "zh" ? "开启" : "On"}</option>
                <option value="false">{uiLang === "zh" ? "关闭" : "Off"}</option>
              </select>
            </label>
          </div>
          <div className="contract-drop">
            <input
              ref={fileInputRef}
              type="file"
              accept=".docx,.pdf"
              onChange={e => {
                const f = e.target.files?.[0] || null
                setContractFile(f)
                setContract(prev => ({ ...prev, title: f ? toContractTitle(f.name) : "" }))
                setContractError("")
              }}
              style={{ display: "none" }}
            />
            <button type="button" className="ghost" onClick={() => fileInputRef.current && fileInputRef.current.click()}>
              {t.chooseFile}
            </button>
            <span>{contractFile ? contractFile.name : t.noFileChosen}</span>
            <em>{t.uploadHint}</em>
          </div>
          {contractError && <div className="error">{contractError}</div>}
          <button className="primary" onClick={onContractUpload} disabled={contractLoading}>
            {contractLoading ? t.uploading : t.uploadBtn}
          </button>
        </div>
      </section>
      <div className="workbench-grid">
        <section className="card contract-preview">
          <div className="preview-head">
            <div className="panel-title">{t.contractPreview}</div>
            {previewMeta && (
              <div className="preview-meta">
                <span>{t.pageMeta}: {previewMeta.page_count ?? "-"}</span>
                <span>{t.lineCount}: {previewMeta.line_total ?? 0}</span>
              </div>
            )}
          </div>
          {previewLoading && (
            <div className="empty-state preview-empty">
              <p>{t.previewLoading}</p>
            </div>
          )}
          {!previewLoading && previewError && <div className="error">{t.previewError}: {previewError}</div>}
          {!previewLoading && !previewError && !previewText && (
            <div className="empty-state preview-empty">
              <p>{t.previewEmpty}</p>
            </div>
          )}
          {!previewLoading && !previewError && !!previewText && (
            <div className="preview-scroll" ref={previewScrollRef}>
              <article className="preview-document">
                <pre>{previewText}</pre>
              </article>
            </div>
          )}
        </section>
        <section className="card contract-result">
          <div className="panel-title">{t.auditResult}</div>
          {!contractResult && (
            <div className="empty-state">
              <div className="empty-mark">A</div>
              <p>{t.appSubtitle}</p>
            </div>
          )}
          {contractResult && (
            <div className="result-body">
              <div className="result-opinion">
                <div className="result-title">{t.executiveOpinion}</div>
                {Array.isArray(contractResult.executive_opinion) && contractResult.executive_opinion.length > 0 ? (
                  <ol>
                    {contractResult.executive_opinion.map((item, idx) => (
                      <li key={idx}>{String(item || "")}</li>
                    ))}
                  </ol>
                ) : (
                  <div className="empty-risk">{t.emptyOpinion}</div>
                )}
              </div>
              <div className="result-summary">
                <div className="result-title">{t.summary}</div>
                <p>{contractResult.summary || "-"}</p>
              </div>
              <div className="result-risks">
                <div className="result-title">{t.risks}</div>
                {riskList.length > 0 ? (
                  <>
                    <div className="risk-filter-row">
                      <span>{t.riskFilter}</span>
                      <div className="risk-filter-actions">
                        <button
                          type="button"
                          className={`risk-chip${riskFilter === "all" ? " is-active" : ""}`}
                          onClick={() => setRiskFilter("all")}
                        >
                          {t.riskAll} ({riskSummary.all})
                        </button>
                        <button
                          type="button"
                          className={`risk-chip risk-high${riskFilter === "high" ? " is-active" : ""}`}
                          onClick={() => setRiskFilter("high")}
                        >
                          {t.riskHigh} ({riskSummary.high})
                        </button>
                        <button
                          type="button"
                          className={`risk-chip risk-medium${riskFilter === "medium" ? " is-active" : ""}`}
                          onClick={() => setRiskFilter("medium")}
                        >
                          {t.riskMedium} ({riskSummary.medium})
                        </button>
                        <button
                          type="button"
                          className={`risk-chip risk-low${riskFilter === "low" ? " is-active" : ""}`}
                          onClick={() => setRiskFilter("low")}
                        >
                          {t.riskLow} ({riskSummary.low})
                        </button>
                      </div>
                    </div>
                    <div className="risk-filter-meta">{t.riskShown}: {filteredRisks.length}</div>
                    {filteredRisks.length > 0 ? (
                      <ul>
                        {filteredRisks.map((r, idx) => {
                          const location = r?.location || {}
                          const hasLocation = Boolean(String(location.anchor_id || "").trim())
                          const isActive = idx === activeRiskIndex
                          return (
                            <li
                              key={idx}
                              className={`risk-item${isActive ? " is-active" : ""}${hasLocation ? " is-linkable" : " is-disabled"}`}
                              onClick={() => onRiskLocate(r, idx)}
                              onKeyDown={(e) => {
                                if (e.key === "Enter" || e.key === " ") {
                                  e.preventDefault()
                                  onRiskLocate(r, idx)
                                }
                              }}
                              role="button"
                              tabIndex={0}
                            >
                              <strong>{r.level || "N/A"}</strong>
                              <span>{r.type || "-"}</span>
                              <p>{r.issue || "-"}</p>
                              <div className="risk-location">{t.location}: {formatRiskLocation(location)}</div>
                              <em>{r.suggestion || ""}</em>
                              {(() => {
                                const cid = String(r.citation_id || "").trim()
                                const linked = cid ? citationMap[cid] : null
                                const basis = String(r.basis || r.law_reference || "").trim()
                                if (!cid && !linked && !basis) return null
                                return (
                                  <div className="risk-evidence">
                                    <div className="evidence-title">{t.evidenceTitle}</div>
                                    {linked ? (
                                      <div className="evidence-card">
                                        <p>{buildCitationDisplay(linked) || "-"}</p>
                                      </div>
                                    ) : null}
                                    {!linked && basis ? <div className="evidence-id">{t.legalBasis}: {basis}</div> : null}
                                    {uiConfig.showCitationSource && cid ? <div className="evidence-id">{t.citationLabel}: {cid}</div> : null}
                                  </div>
                                )
                              })()}
                            </li>
                          )
                        })}
                      </ul>
                    ) : (
                      <div className="empty-risk">{t.emptyRisk}</div>
                    )}
                  </>
                ) : (
                  <div className="empty-risk">{t.emptyRisk}</div>
                )}
              </div>
            </div>
          )}
          {contractMeta && (
            <div className="result-meta">
              <span>{t.ocrMeta}: {contractMeta.ocr_used ? contractMeta.ocr_engine || "on" : "off"}</span>
              <span>{t.pageMeta}: {contractMeta.page_count ?? "-"}</span>
              <span>{t.modelMeta}: {contractMeta.llm_model || "-"}</span>
              <span>{t.retrievalMode}: {contractMeta.retrieval_mode || "-"}</span>
              <span>{t.taxFocusMeta}: {contractMeta.tax_focus ? "on" : "off"}</span>
              <span>{t.evidenceMeta}: {contractMeta.evidence_count ?? 0}</span>
              <span>{t.queryMeta}: {contractMeta.retrieval_queries ?? 0}</span>
              <span>{t.promptMeta}: {contractMeta.prompt_tokens_est ?? "-"}</span>
              <span>{t.clauseCount}: {contractMeta.preview_clause_total ?? previewMeta?.clause_total ?? "-"}</span>
              <span>ID: {documentId || "-"}</span>
            </div>
          )}
        </section>
      </div>
    </div>
  )
}
