import React, { useState } from "react"
import { importRegulation, getJob, searchRegulations } from "./api"

export default function App() {
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
  const [jobId, setJobId] = useState("")
  const [jobStatus, setJobStatus] = useState("")
  const [query, setQuery] = useState({ query: "", language: "zh", top_k: 10, date: "", region: "", industry: "", use_semantic: true, semantic_weight: 0.6, bm25_weight: 0.4, candidate_size: 200 })
  const [results, setResults] = useState([])
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState("")

  const i18n = {
    zh: {
      appTitle: "法规助手",
      uiLanguage: "界面语言",
      importTitle: "法规导入",
      searchTitle: "检索",
      uploadBtn: "上传",
      checkJob: "查询任务",
      searchBtn: "搜索",
      semantic: "语义检索",
      searching: "检索中...",
      result: "检索结果",
      topAnswer: "最佳答案",
      noMatch: "未匹配到条款，请尝试更宽泛关键词或取消筛选。",
      noAnswer: "未提取到直接答案，请优化关键词。"
    },
    en: {
      appTitle: "Law Assistant",
      uiLanguage: "UI Language",
      importTitle: "Regulation Import",
      searchTitle: "Search",
      uploadBtn: "Upload",
      checkJob: "Check Job",
      searchBtn: "Search",
      semantic: "Semantic",
      searching: "Searching...",
      result: "Search Result",
      topAnswer: "Top Answer",
      noMatch: "No matched articles. Try broader keywords or disable filters.",
      noAnswer: "No direct answer extracted, please refine keywords."
    }
  }
  const t = i18n[uiLang]

  const onUpload = async (e) => {
    e.preventDefault()
    if (!file) return
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

  return (
    <div className="page">
      <h1>{t.appTitle}</h1>
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
          <input type="file" onChange={e => setFile(e.target.files?.[0] || null)} />
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
          <input placeholder={uiLang === "zh" ? "Tag" : "Tag"} value={query.industry} onChange={e => setQuery({ ...query, industry: e.target.value })} />
          <label><input type="checkbox" checked={query.use_semantic} onChange={e => setQuery({ ...query, use_semantic: e.target.checked })} /> {t.semantic}</label>
          <input placeholder="Semantic Weight (0-1)" value={query.semantic_weight} onChange={e => setQuery({ ...query, semantic_weight: parseFloat(e.target.value||"0") })} />
          <input placeholder="BM25 Weight (0-1)" value={query.bm25_weight} onChange={e => setQuery({ ...query, bm25_weight: parseFloat(e.target.value||"0") })} />
          <input placeholder="Candidates" value={query.candidate_size} onChange={e => setQuery({ ...query, candidate_size: parseInt(e.target.value||"200") })} />
          <button type="submit">{t.searchBtn}</button>
        </form>
        <div className="row">
          <strong>{t.result}</strong>
          <span className="meta">{searching ? t.searching : `${results.length} item(s)`}</span>
        </div>
        {searchError && <div className="error">{searchError}</div>}
        {!searching && !searchError && query.query.trim() && results.length === 0 && (
          <div className="meta">{t.noMatch}</div>
        )}
        {results.length > 0 && (
          <div className="card">
            <h3>{t.topAnswer}</h3>
            {(() => { const top = [...results].sort((a,b) => (b.answer_score||0)-(a.answer_score||0))[0]; return top?.answer ? (
              <div>
                <div className="content">{top.answer}</div>
                <div className="meta">citation: {top.citation_id} | effective: {top.effective_status}</div>
              </div>
            ) : <div className="meta">{t.noAnswer}</div>; })()}
          </div>
        )}
        <ul className="list">
          {results.map(r => (
            <li key={r.article_id}>
              <div className="title">{r.title} {r.article_no}</div>
              <div className="meta">effective: {r.effective_status} | region: {r.region || "-"}</div>
              <div className="content">{r.content}</div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  )
}