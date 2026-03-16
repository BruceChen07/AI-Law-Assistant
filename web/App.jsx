import React, { useEffect, useState, useRef } from "react"
import { importRegulation, getJob, searchRegulations, getCurrentUser, getMe, logout } from "./api"
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
  const [upload, setUpload] = useState({
    title: "",
    doc_no: "",
    issuer: "",
    reg_type: "",
    status: "current",
    effective_date: "",
    expiry_date: "",
    region: "",
    industry: "",
    language: "zh"
  })
  const [file, setFile] = useState(null)
  const [fileError, setFileError] = useState("")
  const [jobId, setJobId] = useState("")
  const [jobStatus, setJobStatus] = useState("")
  const [query, setQuery] = useState({ query: "", language: "zh", top_k: 10, date: "", region: "", industry: "", use_semantic: true, semantic_weight: 0.6, bm25_weight: 0.4, candidate_size: 200 })
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [hasSearched, setHasSearched] = useState(false)
  const [searchError, setSearchError] = useState("")

  const i18n = {
    zh: {
      appTitle: "法规助手",
      uiLanguage: "界面语言",
      importTitle: "法规导入",
      chooseFile: "选择文件",
      noFileChosen: "未选择任何文件",
      searchTitle: "检索",
      uploadBtn: "上传",
      checkJob: "查询任务",
      searchBtn: "搜索",
      semantic: "语义检索",
      searching: "检索中...",
      result: "检索结果",
      topAnswer: "最佳答案",
      noMatch: "未匹配到条款，请尝试更宽泛关键词或取消筛选。",
      noAnswer: "未提取到直接答案，请优化关键词。",
      invalidFileType: "文档格式不对，请上传 docx 或 pdf 文档。"
    },
    en: {
      appTitle: "Law Assistant",
      uiLanguage: "UI Language",
      importTitle: "Regulation Import",
      chooseFile: "Choose File",
      noFileChosen: "No file chosen",
      searchTitle: "Search",
      uploadBtn: "Upload",
      checkJob: "Check Job",
      searchBtn: "Search",
      semantic: "Semantic Search",
      searching: "Searching...",
      result: "Search Result",
      topAnswer: "Top Answer",
      noMatch: "No matched articles. Try broader keywords or disable filters.",
      noAnswer: "No direct answer extracted, please refine keywords.",
      invalidFileType: "Invalid file type. Please upload docx or pdf documents.",
      semanticWeight: "Semantic Weight",
      bm25Weight: "BM25 Weight",
      candidateSize: "Candidates",
      advancedConfig: "Advanced Config"
    }
  }
  const t = i18n[uiLang] || i18n.zh
  // Ensure we have missing translations for zh
  if (uiLang === "zh" && !t.semanticWeight) {
    t.semanticWeight = "语义权重"
    t.bm25Weight = "关键词权重"
    t.candidateSize = "召回数量"
    t.advancedConfig = "高级配置"
  }
  const fileInputRef = useRef(null)

  const onUpload = async (e) => {
    e.preventDefault()
    if (!file) return
    const ext = file.name.toLowerCase().split('.').pop()
    if (!['docx', 'pdf'].includes(ext)) {
      setFileError(t.invalidFileType)
      return
    }
    setFileError("")
    const form = new FormData()
    Object.entries(upload).forEach(([k, v]) => form.append(k, v))
    form.append("file", file)
    const res = await importRegulation(form)
    setJobId(res.job_id)
    setJobStatus("running")
  }

  const onCheck = async () => {
    if (!jobId) return
    const res = await getJob(jobId)
    setJobStatus(res.status)
  }

  const onUiLanguageChange = (next) => {
    setUiLang(next)
    setUpload(prev => ({ ...prev, language: next }))
    setQuery(prev => ({ ...prev, language: next }))
  }

  const onSearch = async (e) => {
    e.preventDefault()
    const payload = { ...query }
    if (!payload.date) delete payload.date
    if (!payload.region) delete payload.region
    if (!payload.industry) delete payload.industry
    setSearching(true)
    setHasSearched(true)
    setSearchError("")
    try {
      const res = await searchRegulations(payload)
      setResults(Array.isArray(res) ? res : [])
    } catch (err) {
      setResults([])
      setSearchError(String(err?.message || err || "Search failed"))
    } finally {
      setSearching(false)
    }
  }

  if (!user) {
    return <Login onLogin={() => { setUser(getCurrentUser()); setView("main") }} />
  }

  const canOpenAdmin = user.role === "admin" || user.username === "admin"

  return (
    <div className="page">
      <div className="row" style={{ justifyContent: 'space-between', marginBottom: 20 }}>
        <h1>{t.appTitle}</h1>
        <div>
          <span>Welcome, {user.username} ({user.role}) </span>
          {canOpenAdmin && <button onClick={() => setView("admin")}>Admin</button>}
          <button onClick={() => { logout(); setUser(null); }}>Logout</button>
        </div>
      </div>
      
      {view === "admin" ? (
        <Admin lang={uiLang} onBack={() => setView("main")} />
      ) : (
        <>
          <section className="card">
            <div className="row">
              <strong>{t.uiLanguage}</strong>
              <select value={uiLang} onChange={e => onUiLanguageChange(e.target.value)}>
                <option value="zh">中文</option>
                <option value="en">English</option>
              </select>
            </div>
          </section>
          <section className="card">
            <h2>{t.importTitle}</h2>
            <form onSubmit={onUpload} className="grid">
              <input placeholder={uiLang === "zh" ? "标题" : "Title"} value={upload.title} onChange={e => setUpload({ ...upload, title: e.target.value })} />
              <input placeholder={uiLang === "zh" ? "Tag" : "Tag"} value={upload.doc_no} onChange={e => setUpload({ ...upload, doc_no: e.target.value })} />
              <input placeholder={uiLang === "zh" ? "发布机构" : "Issuer"} value={upload.issuer} onChange={e => setUpload({ ...upload, issuer: e.target.value })} />
              <input placeholder={uiLang === "zh" ? "类型" : "Type"} value={upload.reg_type} onChange={e => setUpload({ ...upload, reg_type: e.target.value })} />
              <input placeholder={uiLang === "zh" ? "生效日期 yyyy-mm-dd" : "Effective Date yyyy-mm-dd"} value={upload.effective_date} onChange={e => setUpload({ ...upload, effective_date: e.target.value })} />
              <input placeholder={uiLang === "zh" ? "失效日期 yyyy-mm-dd" : "Expiry Date yyyy-mm-dd"} value={upload.expiry_date} onChange={e => setUpload({ ...upload, expiry_date: e.target.value })} />
              <input placeholder={uiLang === "zh" ? "地区" : "Region"} value={upload.region} onChange={e => setUpload({ ...upload, region: e.target.value })} />
              <input placeholder={uiLang === "zh" ? "Sub-Tag" : "Sub-Tag"} value={upload.industry} onChange={e => setUpload({ ...upload, industry: e.target.value })} />
              <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                <input
                  ref={fileInputRef}
                  type="file"
                  accept=".docx,.pdf"
                  onChange={e => {
                    const f = e.target.files?.[0] || null
                    setFile(f)
                    setFileError("")
                  }}
                  style={{ display: 'none' }}
                />
                <button type="button" onClick={() => fileInputRef.current && fileInputRef.current.click()}>{t.chooseFile}</button>
                <span className="meta">{file ? file.name : t.noFileChosen}</span>
              </div>
              {fileError && <span style={{color: 'red'}}>{fileError}</span>}
              <button type="submit">{t.uploadBtn}</button>
            </form>
            <div className="row">
              <button onClick={onCheck}>{t.checkJob}</button>
              <span>Job: {jobId} {jobStatus}</span>
            </div>
          </section>

          <section className="card">
            <h2>{t.searchTitle}</h2>
            <form onSubmit={onSearch} className="grid">
              <input placeholder={uiLang === "zh" ? "问题" : "Query"} value={query.query} onChange={e => setQuery({ ...query, query: e.target.value })} />
              <input placeholder={uiLang === "zh" ? "日期 yyyy-mm-dd" : "Date yyyy-mm-dd"} value={query.date} onChange={e => setQuery({ ...query, date: e.target.value })} />
              <input placeholder={uiLang === "zh" ? "地区" : "Region"} value={query.region} onChange={e => setQuery({ ...query, region: e.target.value })} />
              <input placeholder={uiLang === "zh" ? "Sub-Tag" : "Sub-Tag"} value={query.industry} onChange={e => setQuery({ ...query, industry: e.target.value })} />
              
              <div style={{ gridColumn: "1 / -1", border: "1px solid #eee", padding: "10px", borderRadius: "4px", marginTop: "10px" }}>
                <div style={{ marginBottom: "10px", fontWeight: "bold" }}>{t.advancedConfig}</div>
                <div style={{ display: "flex", flexWrap: "wrap", gap: "20px", alignItems: "center" }}>
                  <label style={{ display: "flex", alignItems: "center", gap: "5px" }}>
                    <input type="checkbox" checked={query.use_semantic} onChange={e => setQuery({ ...query, use_semantic: e.target.checked })} /> 
                    {t.semantic}
                  </label>
                  
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <label>{t.semanticWeight}: {query.semantic_weight}</label>
                    <input 
                      type="range" 
                      min="0" 
                      max="1" 
                      step="0.1" 
                      value={query.semantic_weight} 
                      onChange={e => setQuery({ ...query, semantic_weight: parseFloat(e.target.value) })} 
                      style={{ width: "100px" }}
                    />
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <label>{t.bm25Weight}: {query.bm25_weight}</label>
                    <input 
                      type="range" 
                      min="0" 
                      max="1" 
                      step="0.1" 
                      value={query.bm25_weight} 
                      onChange={e => setQuery({ ...query, bm25_weight: parseFloat(e.target.value) })} 
                      style={{ width: "100px" }}
                    />
                  </div>

                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <label>{t.candidateSize}:</label>
                    <input 
                      type="number" 
                      value={query.candidate_size} 
                      onChange={e => setQuery({ ...query, candidate_size: parseInt(e.target.value||"0") })} 
                      style={{ width: "80px" }}
                    />
                  </div>
                </div>
              </div>

              <button type="submit" style={{ gridColumn: "1 / -1" }}>{t.searchBtn}</button>
            </form>
            <div className="row">
              <strong>{t.result}</strong>
              <span className="meta">{searching ? t.searching : `${results.length} item(s)`}</span>
            </div>
            {searchError && <div className="error">{searchError}</div>}
            {hasSearched && !searchError && results.length === 0 && !searching && <div className="error">{t.noMatch}</div>}
            <ul className="list">
              {results.slice(0, 5).map((r, i) => (
                <li key={i}>
                  <div className="title">{r.title} - {r.article_no}</div>
                  <div className="meta">{r.effective_date} | {r.region} | {r.industry}</div>
                  <div className="content">{r.content?.slice(0, 300)}...</div>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  )
}
