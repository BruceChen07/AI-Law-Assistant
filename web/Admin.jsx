import React, { useState, useEffect } from "react"
import { adminListDocuments, adminDeleteDocument, adminListUsers, adminUpdateUserRole, adminGetStats, adminGetLLMConfig, adminUpdateLLMConfig, adminGetUIConfig, adminUpdateUIConfig, adminTestLLM, importRegulation, getJob, searchRegulations, getCurrentUser, logout } from "./api"

export default function Admin({ onBack, lang }) {
  const [tab, setTab] = useState("documents")
  const [documents, setDocuments] = useState([])
  const [users, setUsers] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(false)
  const [pagination, setPagination] = useState({ page: 1, page_size: 10, total: 0 })
  const [search, setSearch] = useState("")
  const [docCategory, setDocCategory] = useState("contract")
  const [deleteConfirm, setDeleteConfirm] = useState(null)
  const [llmConfig, setLlmConfig] = useState(null)
  const [llmHasApiKey, setLlmHasApiKey] = useState(false)
  const [llmSaving, setLlmSaving] = useState(false)
  const [llmError, setLlmError] = useState("")
  const [llmTestPrompt, setLlmTestPrompt] = useState("")
  const [llmTestResult, setLlmTestResult] = useState("")
  const [llmTestError, setLlmTestError] = useState("")
  const [llmTesting, setLlmTesting] = useState(false)
  const [uiConfig, setUiConfig] = useState({ showCitationSource: false, defaultTheme: "dark" })
  const [uiSaving, setUiSaving] = useState(false)
  const [uiError, setUiError] = useState("")
  const [regUpload, setRegUpload] = useState({
    title: "",
    doc_no: "",
    issuer: "",
    status: "current",
    effective_date: "",
    expiry_date: "",
    region: "",
    industry: "",
    language: lang || "zh"
  })
  const [regFile, setRegFile] = useState(null)
  const [regFileError, setRegFileError] = useState("")
  const [jobId, setJobId] = useState("")
  const [jobStatus, setJobStatus] = useState("")
  const [regQuery, setRegQuery] = useState({ query: "", language: lang || "zh", top_k: 10, date: "", region: "", industry: "", use_semantic: true, semantic_weight: 0.6, bm25_weight: 0.4, candidate_size: 50, rerank_enabled: true, rerank_top_n: 50 })
  const [regResults, setRegResults] = useState([])
  const [regSearching, setRegSearching] = useState(false)
  const [regHasSearched, setRegHasSearched] = useState(false)
  const [regSearchError, setRegSearchError] = useState("")
  const [regShowAdvanced, setRegShowAdvanced] = useState(false)
  
  const user = getCurrentUser()
  const admin = !!(user && (user.role === "admin" || user.username === "admin"))
  const i18n = {
    zh: {
      title: "管理后台",
      back: "返回",
      welcome: "欢迎",
      tabStats: "统计",
      tabDocs: "文档",
      docsContract: "合同文件",
      docsLegal: "法律文件",
      tabUsers: "用户",
      docMgmt: "文档管理",
      searchPlaceholder: "搜索文档...",
      searchBtn: "搜索",
      colFilename: "文件名",
      colSize: "大小",
      colUser: "用户",
      colCategory: "分类",
      colUploaded: "上传时间",
      colActions: "操作",
      loading: "加载中...",
      delete: "删除",
      confirm: "确认",
      cancel: "取消",
      previous: "上一页",
      next: "下一页",
      userMgmt: "用户管理",
      colUsername: "用户名",
      colEmail: "邮箱",
      colRole: "角色",
      colCreated: "创建时间",
      statsTitle: "系统统计",
      statsTotalDocs: "文档总数",
      statsTotalUsers: "用户总数",
      statsTotalStorage: "存储总量",
      statsByCategory: "按分类统计",
      statsByUser: "按用户统计",
      tabModel: "模型配置",
      tabRegulations: "法规上传",
      importTitle: "法规导入",
      searchTitle: "法规检索",
      chooseFile: "选择文件",
      noFileChosen: "未选择任何文件",
      uploadBtn: "上传",
      checkJob: "查询任务",
      semantic: "语义检索",
      searching: "检索中...",
      result: "检索结果",
      noMatch: "未匹配到条款，请尝试更宽泛关键词或取消筛选。",
      invalidFileType: "文档格式不对，请上传 docx 或 pdf 文档。",
      semanticWeight: "语义权重",
      bm25Weight: "关键词权重",
      candidateSize: "召回数量",
      rerankEnabled: "精排启用",
      rerankTopN: "精排候选",
      advancedConfig: "高级配置",
      advancedToggle: "展开高级配置",
      llmTitle: "LLM 配置",
      provider: "厂商",
      apiBase: "API 端点",
      apiKey: "API 密钥",
      model: "模型",
      temperature: "温度",
      maxTokens: "最大 Token",
      timeout: "超时(秒)",
      llmTestPrompt: "测试问题",
      llmTestButton: "测试配置",
      llmTestRunning: "测试中...",
      llmTestResult: "测试结果",
      llmTestOk: "配置可用",
      llmTestDefaultPrompt: "你是什么模型？",
      llmApiKeySavedMask: "********（已保存，留空则不变）",
      llmApiKeySavedHint: "已保存 API 密钥；不修改可留空。",
      uiConfigTitle: "界面配置",
      showCitationSource: "显示证据来源",
      defaultTheme: "默认主题",
      themeDark: "深色",
      themeLight: "浅色",
      save: "保存",
      saved: "已保存",
      validateFailed: "请检查配置参数",
      accessDenied: "访问受限，需要管理员权限。"
    },
    en: {
      title: "Admin Dashboard",
      back: "Back",
      welcome: "Welcome",
      tabStats: "Statistics",
      tabDocs: "Documents",
      docsContract: "Contract Files",
      docsLegal: "Legal Files",
      tabUsers: "Users",
      docMgmt: "Document Management",
      searchPlaceholder: "Search documents...",
      searchBtn: "Search",
      colFilename: "Filename",
      colSize: "Size",
      colUser: "User",
      colCategory: "Category",
      colUploaded: "Uploaded",
      colActions: "Actions",
      loading: "Loading...",
      delete: "Delete",
      confirm: "Confirm",
      cancel: "Cancel",
      previous: "Previous",
      next: "Next",
      userMgmt: "User Management",
      colUsername: "Username",
      colEmail: "Email",
      colRole: "Role",
      colCreated: "Created",
      statsTitle: "System Statistics",
      statsTotalDocs: "Total Documents",
      statsTotalUsers: "Total Users",
      statsTotalStorage: "Total Storage",
      statsByCategory: "Documents by Category",
      statsByUser: "Documents by User",
      tabModel: "Model Config",
      tabRegulations: "Regulations",
      importTitle: "Regulation Import",
      searchTitle: "Search",
      chooseFile: "Choose File",
      noFileChosen: "No file chosen",
      uploadBtn: "Upload",
      checkJob: "Check Job",
      semantic: "Semantic Search",
      searching: "Searching...",
      result: "Search Result",
      noMatch: "No matched articles. Try broader keywords or disable filters.",
      invalidFileType: "Invalid file type. Please upload docx or pdf documents.",
      semanticWeight: "Semantic Weight",
      bm25Weight: "BM25 Weight",
      candidateSize: "Candidates",
      rerankEnabled: "Rerank Enabled",
      rerankTopN: "Rerank Candidates",
      advancedConfig: "Advanced Config",
      advancedToggle: "Show Advanced",
      llmTitle: "LLM Config",
      provider: "Provider",
      apiBase: "API Base",
      apiKey: "API Key",
      model: "Model",
      temperature: "Temperature",
      maxTokens: "Max Tokens",
      timeout: "Timeout (s)",
      llmTestPrompt: "Test Question",
      llmTestButton: "Test Config",
      llmTestRunning: "Testing...",
      llmTestResult: "Test Result",
      llmTestOk: "Config OK",
      llmTestDefaultPrompt: "What model are you? ",
      llmApiKeySavedMask: "******** (saved, keep empty to retain)",
      llmApiKeySavedHint: "API key already saved; leave empty to keep it.",
      uiConfigTitle: "UI Config",
      showCitationSource: "Show citation source",
      defaultTheme: "Default theme",
      themeDark: "Dark",
      themeLight: "Light",
      save: "Save",
      saved: "Saved",
      validateFailed: "Please check config fields",
      accessDenied: "Access denied. Admin permission required."
    }
  }
  const t = (i18n[lang] || i18n.zh)
  
  useEffect(() => {
    if (!admin) return
    if (tab === "documents") loadDocuments()
    if (tab === "users") loadUsers()
    if (tab === "stats") loadStats()
    if (tab === "model") {
      loadLLM()
      loadUIConfig()
    }
    if (tab === "regulations") {}
  }, [tab, pagination.page, docCategory])

  useEffect(() => {
    setRegUpload(prev => ({ ...prev, language: lang || "zh" }))
    setRegQuery(prev => ({ ...prev, language: lang || "zh" }))
    setLlmTestPrompt(prev => prev || t.llmTestDefaultPrompt)
  }, [lang])
  
  const loadDocuments = async () => {
    setLoading(true)
    try {
      const data = await adminListDocuments({
        page: pagination.page,
        page_size: pagination.page_size,
        search: search,
        category: docCategory
      })
      setDocuments(data.items)
      setPagination(prev => ({ ...prev, total: data.total }))
    } catch (err) {
      alert(err.message)
    } finally {
      setLoading(false)
    }
  }
  
  const loadUsers = async () => {
    setLoading(true)
    try {
      const data = await adminListUsers()
      setUsers(data)
    } catch (err) {
      alert(err.message)
    } finally {
      setLoading(false)
    }
  }
  
  const loadStats = async () => {
    setLoading(true)
    try {
      const data = await adminGetStats()
      setStats(data)
    } catch (err) {
      alert(err.message)
    } finally {
      setLoading(false)
    }
  }

  const loadLLM = async () => {
    setLoading(true)
    try {
      const data = await adminGetLLMConfig()
      setLlmConfig({
        provider: data.provider || "openai_compatible",
        api_base: data.api_base || "",
        api_key: "",
        model: data.model || "",
        temperature: data.temperature ?? 0.2,
        max_tokens: data.max_tokens ?? 2048,
        timeout: data.timeout ?? 60,
        headers: data.headers || {}
      })
      setLlmHasApiKey(!!data.has_api_key)
      setLlmTestPrompt(prev => prev || t.llmTestDefaultPrompt)
      setLlmTestResult("")
      setLlmTestError("")
      setLlmError("")
    } catch (err) {
      setLlmError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const loadUIConfig = async () => {
    try {
      const data = await adminGetUIConfig()
      setUiConfig({
        showCitationSource: !!data.show_citation_source,
        defaultTheme: String(data?.default_theme || "").toLowerCase() === "light" ? "light" : "dark"
      })
      setUiError("")
    } catch (err) {
      setUiError(err.message)
    }
  }

  const validateLLM = (cfg) => {
    if (!cfg.api_base || !cfg.api_base.startsWith("http")) return false
    if (!cfg.model) return false
    const temperature = Number(cfg.temperature)
    if (Number.isNaN(temperature) || temperature < 0 || temperature > 2) return false
    const maxTokens = Number(cfg.max_tokens)
    if (Number.isNaN(maxTokens) || maxTokens <= 0) return false
    const timeout = Number(cfg.timeout)
    if (Number.isNaN(timeout) || timeout <= 0) return false
    if (cfg.headers && typeof cfg.headers !== "object") return false
    return true
  }

  const saveLLM = async () => {
    if (!llmConfig) return
    if (!validateLLM(llmConfig)) {
      setLlmError(t.validateFailed)
      return
    }
    setLlmSaving(true)
    try {
      await adminUpdateLLMConfig({
        provider: llmConfig.provider,
        api_base: llmConfig.api_base,
        api_key: llmConfig.api_key || "",
        model: llmConfig.model,
        temperature: Number(llmConfig.temperature),
        max_tokens: Number(llmConfig.max_tokens),
        timeout: Number(llmConfig.timeout),
        headers: llmConfig.headers || {}
      })
      setLlmError(t.saved)
      setLlmHasApiKey(true)
      setLlmConfig(prev => ({ ...prev, api_key: "" }))
    } catch (err) {
      setLlmError(err.message)
    } finally {
      setLlmSaving(false)
    }
  }

  const testLLM = async () => {
    if (!llmConfig) return
    if (!validateLLM(llmConfig)) {
      setLlmTestError(t.validateFailed)
      return
    }
    const prompt = (llmTestPrompt || t.llmTestDefaultPrompt).trim()
    setLlmTesting(true)
    setLlmTestResult("")
    setLlmTestError("")
    try {
      const res = await adminTestLLM({ prompt })
      setLlmTestResult(res.answer || "")
    } catch (err) {
      setLlmTestError(err.message)
    } finally {
      setLlmTesting(false)
    }
  }

  const saveUIConfig = async () => {
    setUiSaving(true)
    try {
      await adminUpdateUIConfig({
        show_citation_source: !!uiConfig.showCitationSource,
        default_theme: uiConfig.defaultTheme === "light" ? "light" : "dark"
      })
      setUiError(t.saved)
    } catch (err) {
      setUiError(err.message)
    } finally {
      setUiSaving(false)
    }
  }

  const onRegUpload = async (e) => {
    e.preventDefault()
    if (!regFile) return
    const ext = regFile.name.toLowerCase().split(".").pop()
    if (!["docx", "pdf"].includes(ext)) {
      setRegFileError(t.invalidFileType)
      return
    }
    setRegFileError("")
    const form = new FormData()
    Object.entries(regUpload).forEach(([k, v]) => form.append(k, v))
    form.append("file", regFile)
    const res = await importRegulation(form)
    setJobId(res.job_id)
    setJobStatus("running")
  }

  const onRegCheck = async () => {
    if (!jobId) return
    const res = await getJob(jobId)
    setJobStatus(res.status)
  }

  const onRegSearch = async (e) => {
    e.preventDefault()
    const payload = { ...regQuery }
    if (!payload.date) delete payload.date
    if (!payload.region) delete payload.region
    if (!payload.industry) delete payload.industry
    setRegSearching(true)
    setRegHasSearched(true)
    setRegSearchError("")
    try {
      const res = await searchRegulations(payload)
      setRegResults(Array.isArray(res) ? res : [])
    } catch (err) {
      setRegResults([])
      setRegSearchError(String(err?.message || err || "Search failed"))
    } finally {
      setRegSearching(false)
    }
  }
  
  const handleDelete = async (docId) => {
    try {
      await adminDeleteDocument(docId)
      setDeleteConfirm(null)
      loadDocuments()
    } catch (err) {
      alert(err.message)
    }
  }
  
  const handleRoleChange = async (userId, newRole) => {
    try {
      await adminUpdateUserRole(userId, newRole)
      loadUsers()
    } catch (err) {
      alert(err.message)
    }
  }
  
  const formatSize = (bytes) => {
    if (bytes < 1024) return bytes + " B"
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB"
    return (bytes / (1024 * 1024)).toFixed(1) + " MB"
  }
  
  const formatDate = (dateStr) => {
    if (!dateStr) return "-"
    // Backend returns UTC time without 'Z'. Append 'Z' to treat as UTC.
    let utcStr = dateStr
    if (!utcStr.endsWith("Z") && !utcStr.includes("+")) {
      utcStr += "Z"
    }
    return new Date(utcStr).toLocaleString()
  }
  
  if (!admin) {
    return (
      <div className="page">
        <h1>{t.title}</h1>
        <div className="card">
          <p>{t.accessDenied}</p>
        </div>
      </div>
    )
  }
  
  return (
    <div className="page admin-shell">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>{t.title}</h1>
        <div>
          <button onClick={onBack || (() => window.history.back())} style={{ marginRight: 8 }}>{t.back}</button>
          <span>{t.welcome}, {user?.username} </span>
          <button onClick={logout}>Logout</button>
        </div>
      </div>
      
      <div className="tabs">
        <button className={tab === "stats" ? "active" : ""} onClick={() => setTab("stats")}>{t.tabStats}</button>
        <button className={tab === "documents" ? "active" : ""} onClick={() => setTab("documents")}>{t.tabDocs}</button>
        <button className={tab === "users" ? "active" : ""} onClick={() => setTab("users")}>{t.tabUsers}</button>
        <button className={tab === "model" ? "active" : ""} onClick={() => setTab("model")}>{t.tabModel}</button>
        <button className={tab === "regulations" ? "active" : ""} onClick={() => setTab("regulations")}>{t.tabRegulations}</button>
      </div>
      
      {tab === "stats" && stats && (
        <div className="card">
          <h2>{t.statsTitle}</h2>
          <div className="stats-grid">
            <div className="stat-card">
              <h3>{stats.total_documents}</h3>
              <p>{t.statsTotalDocs}</p>
            </div>
            <div className="stat-card">
              <h3>{stats.total_users}</h3>
              <p>{t.statsTotalUsers}</p>
            </div>
            <div className="stat-card">
              <h3>{formatSize(stats.total_size)}</h3>
              <p>{t.statsTotalStorage}</p>
            </div>
          </div>
          
          <h3>{t.statsByCategory}</h3>
          <ul>
            {Object.entries(stats.documents_by_category).map(([cat, cnt]) => (
              <li key={cat}>{cat}: {cnt}</li>
            ))}
          </ul>
          
          <h3>{t.statsByUser}</h3>
          <ul>
            {Object.entries(stats.documents_by_user).map(([user, cnt]) => (
              <li key={user}>{user}: {cnt}</li>
            ))}
          </ul>
        </div>
      )}
      
      {tab === "documents" && (
        <div className="card">
          <h2>{t.docMgmt}</h2>
          <div className="tabs" style={{ marginBottom: 12 }}>
            <button
              className={docCategory === "contract" ? "active" : ""}
              onClick={() => {
                setDocCategory("contract")
                setPagination(prev => ({ ...prev, page: 1 }))
              }}
            >
              {t.docsContract}
            </button>
            <button
              className={docCategory === "legal" ? "active" : ""}
              onClick={() => {
                setDocCategory("legal")
                setPagination(prev => ({ ...prev, page: 1 }))
              }}
            >
              {t.docsLegal}
            </button>
          </div>
          <div className="row">
            <input 
              placeholder={t.searchPlaceholder} 
              value={search}
              onChange={e => setSearch(e.target.value)}
              onKeyDown={e => e.key === "Enter" && loadDocuments()}
            />
            <button onClick={loadDocuments}>{t.searchBtn}</button>
          </div>
          
          {loading ? <p>{t.loading}</p> : (
            <table className="admin-table">
              <thead>
                <tr>
                  <th>{t.colFilename}</th>
                  <th>{t.colSize}</th>
                  <th>{t.colUser}</th>
                  <th>{t.colCategory}</th>
                  <th>{t.colUploaded}</th>
                  <th>{t.colActions}</th>
                </tr>
              </thead>
              <tbody>
                {documents.map(doc => (
                  <tr key={doc.id}>
                    <td>{doc.original_filename}</td>
                    <td>{formatSize(doc.file_size)}</td>
                    <td>{doc.username}</td>
                    <td>{doc.category || (docCategory === "legal" ? "legal" : "-")}</td>
                    <td>{formatDate(doc.created_at)}</td>
                    <td>
                      {deleteConfirm === doc.id ? (
                        <span>
                          <button onClick={() => handleDelete(doc.id)}>{t.confirm}</button>
                          <button onClick={() => setDeleteConfirm(null)}>{t.cancel}</button>
                        </span>
                      ) : (
                        <button onClick={() => setDeleteConfirm(doc.id)}>{t.delete}</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          
          <div className="pagination">
            <button 
              disabled={pagination.page === 1}
              onClick={() => setPagination(prev => ({ ...prev, page: prev.page - 1 }))}
            >{t.previous}</button>
            <span>{lang === "zh" ? `第 ${pagination.page} / ${Math.ceil(pagination.total / pagination.page_size)} 页` : `Page ${pagination.page} of ${Math.ceil(pagination.total / pagination.page_size)}`}</span>
            <button 
              disabled={pagination.page * pagination.page_size >= pagination.total}
              onClick={() => setPagination(prev => ({ ...prev, page: prev.page + 1 }))}
            >{t.next}</button>
          </div>
        </div>
      )}

      {tab === "model" && (
        <div className="card">
          <h2>{t.llmTitle}</h2>
          {llmConfig && (
            <div className="form">
              <div className="row">
                <label>{t.provider}</label>
                <select value={llmConfig.provider} onChange={e => setLlmConfig(prev => ({ ...prev, provider: e.target.value }))}>
                  <option value="openai_compatible">openai_compatible</option>
                  <option value="openai">openai</option>
                  <option value="qwen">qwen</option>
                  <option value="wenxin">wenxin</option>
                </select>
              </div>
              <div className="row">
                <label>{t.apiBase}</label>
                <input value={llmConfig.api_base} onChange={e => setLlmConfig(prev => ({ ...prev, api_base: e.target.value }))} />
              </div>
              <div className="row">
                <label>{t.apiKey}</label>
                <input
                  type="password"
                  value={llmConfig.api_key}
                  placeholder={llmHasApiKey && !llmConfig.api_key ? t.llmApiKeySavedMask : ""}
                  onChange={e => setLlmConfig(prev => ({ ...prev, api_key: e.target.value }))}
                />
                {llmHasApiKey && !llmConfig.api_key ? <span className="llm-key-hint">{t.llmApiKeySavedHint}</span> : null}
              </div>
              <div className="row">
                <label>{t.model}</label>
                <input value={llmConfig.model} onChange={e => setLlmConfig(prev => ({ ...prev, model: e.target.value }))} />
              </div>
              <div className="row">
                <label>{t.temperature}</label>
                <input type="number" step="0.1" value={llmConfig.temperature} onChange={e => setLlmConfig(prev => ({ ...prev, temperature: e.target.value }))} />
              </div>
              <div className="row">
                <label>{t.maxTokens}</label>
                <input type="number" value={llmConfig.max_tokens} onChange={e => setLlmConfig(prev => ({ ...prev, max_tokens: e.target.value }))} />
              </div>
              <div className="row">
                <label>{t.timeout}</label>
                <input type="number" value={llmConfig.timeout} onChange={e => setLlmConfig(prev => ({ ...prev, timeout: e.target.value }))} />
              </div>
              <div className="row">
                <button disabled={llmSaving} onClick={saveLLM}>{t.save}</button>
                {llmError && <span style={{ marginLeft: 12 }}>{llmError}</span>}
              </div>
              <div className="llm-test">
                <div className="row">
                  <label>{t.llmTestPrompt}</label>
                  <textarea
                    className="llm-test-input"
                    rows={2}
                    value={llmTestPrompt}
                    onChange={e => setLlmTestPrompt(e.target.value)}
                  />
                </div>
                <div className="row llm-test-actions">
                  <button disabled={llmTesting} onClick={testLLM}>{llmTesting ? t.llmTestRunning : t.llmTestButton}</button>
                  {llmTestError && <span className="llm-test-error">{llmTestError}</span>}
                  {!llmTestError && llmTestResult && <span className="llm-test-ok">{t.llmTestOk}</span>}
                </div>
                {llmTestResult && (
                  <div className="row">
                    <label>{t.llmTestResult}</label>
                    <div className="llm-test-result">{llmTestResult}</div>
                  </div>
                )}
              </div>
              <div className="llm-test">
                <div className="row">
                  <label>{t.uiConfigTitle}</label>
                </div>
                <div className="row">
                  <label className="toggle">
                    <input
                      type="checkbox"
                      checked={!!uiConfig.showCitationSource}
                      onChange={e => setUiConfig(prev => ({ ...prev, showCitationSource: e.target.checked }))}
                    />
                    {t.showCitationSource}
                  </label>
                </div>
                <div className="row">
                  <label>{t.defaultTheme}</label>
                  <select
                    value={uiConfig.defaultTheme}
                    onChange={e => setUiConfig(prev => ({ ...prev, defaultTheme: e.target.value === "light" ? "light" : "dark" }))}
                  >
                    <option value="dark">{t.themeDark}</option>
                    <option value="light">{t.themeLight}</option>
                  </select>
                </div>
                <div className="row">
                  <button disabled={uiSaving} onClick={saveUIConfig}>{t.save}</button>
                  {uiError && <span style={{ marginLeft: 12 }}>{uiError}</span>}
                </div>
              </div>
            </div>
          )}
        </div>
      )}
      
      {tab === "users" && (
        <div className="card">
          <h2>{t.userMgmt}</h2>
          {loading ? <p>{t.loading}</p> : (
            <table className="admin-table">
              <thead>
                <tr>
                  <th>{t.colUsername}</th>
                  <th>{t.colEmail}</th>
                  <th>{t.colRole}</th>
                  <th>{t.colCreated}</th>
                  <th>{t.colActions}</th>
                </tr>
              </thead>
              <tbody>
                {users.map(u => (
                  <tr key={u.id}>
                    <td>{u.username}</td>
                    <td>{u.email}</td>
                    <td>
                      <select 
                        value={u.role} 
                        onChange={e => handleRoleChange(u.id, e.target.value)}
                      >
                        <option value="user">User</option>
                        <option value="admin">Admin</option>
                      </select>
                    </td>
                    <td>{formatDate(u.created_at)}</td>
                    <td>{u.role}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {tab === "regulations" && (
        <div className="card">
          <h2>{t.importTitle}</h2>
          <form onSubmit={onRegUpload} className="grid">
            <input placeholder={lang === "zh" ? "标题" : "Title"} value={regUpload.title} onChange={e => setRegUpload({ ...regUpload, title: e.target.value })} />
            <input placeholder={lang === "zh" ? "Tag" : "Tag"} value={regUpload.doc_no} onChange={e => setRegUpload({ ...regUpload, doc_no: e.target.value })} />
            <input placeholder={lang === "zh" ? "发布机构" : "Issuer"} value={regUpload.issuer} onChange={e => setRegUpload({ ...regUpload, issuer: e.target.value })} />
            <input placeholder={lang === "zh" ? "生效日期 yyyy-mm-dd" : "Effective Date yyyy-mm-dd"} value={regUpload.effective_date} onChange={e => setRegUpload({ ...regUpload, effective_date: e.target.value })} />
            <input placeholder={lang === "zh" ? "失效日期 yyyy-mm-dd" : "Expiry Date yyyy-mm-dd"} value={regUpload.expiry_date} onChange={e => setRegUpload({ ...regUpload, expiry_date: e.target.value })} />
            <input placeholder={lang === "zh" ? "地区" : "Region"} value={regUpload.region} onChange={e => setRegUpload({ ...regUpload, region: e.target.value })} />
            <input placeholder={lang === "zh" ? "Sub-Tag" : "Sub-Tag"} value={regUpload.industry} onChange={e => setRegUpload({ ...regUpload, industry: e.target.value })} />
            <div className="row">
              <input
                type="file"
                accept=".docx,.pdf"
                onChange={e => {
                  const f = e.target.files?.[0] || null
                  setRegFile(f)
                  setRegFileError("")
                }}
              />
              <span className="meta">{regFile ? regFile.name : t.noFileChosen}</span>
            </div>
            {regFileError && <span className="error">{regFileError}</span>}
            <button type="submit">{t.uploadBtn}</button>
          </form>
          <div className="row">
            <button onClick={onRegCheck}>{t.checkJob}</button>
            <span>Job: {jobId} {jobStatus}</span>
          </div>
          <h2>{t.searchTitle}</h2>
          <form onSubmit={onRegSearch} className="grid">
            <input placeholder={lang === "zh" ? "问题" : "Query"} value={regQuery.query} onChange={e => setRegQuery({ ...regQuery, query: e.target.value })} />
            <input placeholder={lang === "zh" ? "日期 yyyy-mm-dd" : "Date yyyy-mm-dd"} value={regQuery.date} onChange={e => setRegQuery({ ...regQuery, date: e.target.value })} />
            <input placeholder={lang === "zh" ? "地区" : "Region"} value={regQuery.region} onChange={e => setRegQuery({ ...regQuery, region: e.target.value })} />
            <input placeholder={lang === "zh" ? "Sub-Tag" : "Sub-Tag"} value={regQuery.industry} onChange={e => setRegQuery({ ...regQuery, industry: e.target.value })} />
            <div className="advanced-panel">
              <div className="advanced-header">
                <div className="advanced-title">{t.advancedConfig}</div>
                <button type="button" onClick={() => setRegShowAdvanced(!regShowAdvanced)}>
                  {regShowAdvanced ? (lang === "zh" ? "收起" : "Hide") : t.advancedToggle}
                </button>
              </div>
              {regShowAdvanced && (
                <div className="advanced-body">
                  <label className="toggle">
                    <input type="checkbox" checked={regQuery.use_semantic} onChange={e => setRegQuery({ ...regQuery, use_semantic: e.target.checked })} />
                    {t.semantic}
                  </label>
                  <div className="range">
                    <label>{t.semanticWeight}: {regQuery.semantic_weight}</label>
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.1"
                      value={regQuery.semantic_weight}
                      onChange={e => setRegQuery({ ...regQuery, semantic_weight: parseFloat(e.target.value) })}
                    />
                  </div>
                  <div className="range">
                    <label>{t.bm25Weight}: {regQuery.bm25_weight}</label>
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.1"
                      value={regQuery.bm25_weight}
                      onChange={e => setRegQuery({ ...regQuery, bm25_weight: parseFloat(e.target.value) })}
                    />
                  </div>
                  <label className="toggle">
                    <input type="checkbox" checked={regQuery.rerank_enabled} onChange={e => setRegQuery({ ...regQuery, rerank_enabled: e.target.checked })} />
                    {t.rerankEnabled}
                  </label>
                  <div className="range">
                    <label>{t.candidateSize}: {regQuery.candidate_size}</label>
                    <input
                      type="range"
                      min="10"
                      max="200"
                      step="10"
                      value={regQuery.candidate_size}
                      onChange={e => setRegQuery({ ...regQuery, candidate_size: parseInt(e.target.value || "0") })}
                    />
                  </div>
                  <div className="range">
                    <label>{t.rerankTopN}: {regQuery.rerank_top_n}</label>
                    <input
                      type="range"
                      min="10"
                      max="200"
                      step="10"
                      value={regQuery.rerank_top_n}
                      onChange={e => setRegQuery({ ...regQuery, rerank_top_n: parseInt(e.target.value || "0") })}
                    />
                  </div>
                </div>
              )}
            </div>
            <button type="submit" className="wide">{t.searchBtn}</button>
          </form>
          <div className="row">
            <strong>{t.result}</strong>
            <span className="meta">{regSearching ? t.searching : `${regResults.length} item(s)`}</span>
          </div>
          {regSearchError && <div className="error">{regSearchError}</div>}
          {regHasSearched && !regSearchError && regResults.length === 0 && !regSearching && <div className="error">{t.noMatch}</div>}
          <ul className="list">
            {regResults.slice(0, 5).map((r, i) => (
              <li key={i}>
                <div className="title">{r.title} - {r.article_no}</div>
                <div className="meta">{r.effective_date} | {r.region} | {r.industry}</div>
                <div className="content">{r.content?.slice(0, 300)}...</div>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
