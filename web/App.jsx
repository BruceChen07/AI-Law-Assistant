import React, { useEffect, useState, useRef } from "react"
import { auditContract, getCurrentUser, getMe, logout } from "./api"
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
    language: "zh"
  })
  const [contractFile, setContractFile] = useState(null)
  const [contractError, setContractError] = useState("")
  const [contractLoading, setContractLoading] = useState(false)
  const [contractResult, setContractResult] = useState(null)
  const [contractMeta, setContractMeta] = useState(null)

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
      risks: "风险清单",
      emptyRisk: "未识别到明显风险",
      ocrMeta: "OCR",
      pageMeta: "页数",
      modelMeta: "模型",
      invalidFileType: "文档格式不对，请上传 docx 或 pdf 文档。",
      uploading: "审计中...",
      uploadHint: "支持 docx / pdf / 扫描版 pdf"
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
      risks: "Risk List",
      emptyRisk: "No obvious risks detected",
      ocrMeta: "OCR",
      pageMeta: "Pages",
      modelMeta: "Model",
      invalidFileType: "Invalid file type. Please upload docx or pdf documents.",
      uploading: "Auditing...",
      uploadHint: "Supports docx / pdf / scanned pdf"
    }
  }
  const t = i18n[uiLang] || i18n.zh
  const fileInputRef = useRef(null)

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
    try {
      const form = new FormData()
      form.append("title", contract.title || contractFile.name)
      form.append("language", contract.language)
      form.append("file", contractFile)
      const res = await auditContract(form)
      setContractResult(res.result || null)
      setContractMeta(res.meta || null)
    } catch (err) {
      setContractError(String(err?.message || err || "Audit failed"))
      setContractResult(null)
      setContractMeta(null)
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
        <div className="contract-grid">
          <section className="card contract-upload">
            <div className="contract-panel">
              <div className="panel-title">{t.contractTitle}</div>
              <input
                className="contract-input"
                placeholder={t.contractTitle}
                value={contract.title}
                onChange={e => setContract({ ...contract, title: e.target.value })}
              />
              <div className="contract-drop">
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".docx,.pdf"
                  onChange={e => {
                    const f = e.target.files?.[0] || null
                    setContractFile(f)
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
                <div className="result-summary">
                  <div className="result-title">{t.summary}</div>
                  <p>{contractResult.summary || "-"}</p>
                </div>
                <div className="result-risks">
                  <div className="result-title">{t.risks}</div>
                  {Array.isArray(contractResult.risks) && contractResult.risks.length > 0 ? (
                    <ul>
                      {contractResult.risks.map((r, idx) => (
                        <li key={idx}>
                          <strong>{r.level || "N/A"}</strong>
                          <span>{r.type || "-"}</span>
                          <p>{r.issue || "-"}</p>
                          <em>{r.suggestion || ""}</em>
                        </li>
                      ))}
                    </ul>
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
              </div>
            )}
          </section>
        </div>
    </div>
  )
}
