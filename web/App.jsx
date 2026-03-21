import { useEffect, useMemo, useRef, useState } from "react"
import { auditContract, getContractPreview, getContractPreviewManifest, getContractPreviewPageImage, getCurrentUser, getMe, getUIConfig, logout } from "./api"
import Login from "./Login"
import Admin from "./Admin"
import { appI18n } from "./i18n/appI18n"

const THEME_STORAGE_KEY = "ui_theme"

const normalizeTheme = (value) => (String(value || "").toLowerCase() === "light" ? "light" : "dark")

const getStoredTheme = () => {
  const v = String(localStorage.getItem(THEME_STORAGE_KEY) || "").toLowerCase()
  return v === "light" || v === "dark" ? v : ""
}

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
  const [theme, setTheme] = useState("dark")
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
  const [uiConfig, setUiConfig] = useState({ showCitationSource: false, defaultTheme: "dark", previewContinuousEnabled: false })
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState("")
  const [previewText, setPreviewText] = useState("")
  const [previewMeta, setPreviewMeta] = useState(null)
  const [previewMode, setPreviewMode] = useState("text")
  const [previewContinuous, setPreviewContinuous] = useState(false)
  const [previewPages, setPreviewPages] = useState([])
  const [previewPageUrls, setPreviewPageUrls] = useState({})
  const [streamVisiblePages, setStreamVisiblePages] = useState({})
  const [previewZoom, setPreviewZoom] = useState(1)
  const [previewScrollPercent, setPreviewScrollPercent] = useState(0)
  const [previewThumbsOpen, setPreviewThumbsOpen] = useState(false)
  const [activePreviewPage, setActivePreviewPage] = useState(1)
  const [activePreviewHighlight, setActivePreviewHighlight] = useState(null)
  const [activeRiskIndex, setActiveRiskIndex] = useState(-1)
  const [riskFilter, setRiskFilter] = useState("all")
  const [expandedEvidence, setExpandedEvidence] = useState({})
  const [contractProgress, setContractProgress] = useState(0)
  const [contractProgressStage, setContractProgressStage] = useState("working")
  const previewScrollRef = useRef(null)
  const previewMainRef = useRef(null)
  const previewThumbRefs = useRef({})
  const previewPageRefs = useRef({})
  const previewPageUrlsRef = useRef({})
  const previewPageObserverRef = useRef(null)
  const previewScrollRatioRef = useRef(0)
  const previewLocateSeqRef = useRef(0)
  const previewLastBlockByPageRef = useRef({})

  const t = appI18n[uiLang] || appI18n.zh
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
  const normalizeLawTitle = (value) => String(value || "").replace(/[《》\s]/g, "").trim().toLowerCase()
  const buildLawArticleKey = (lawTitle, articleNo) => {
    const law = normalizeLawTitle(lawTitle)
    const article = normalizeArticleNo(articleNo).toLowerCase()
    if (!law || !article) return ""
    return `${law}##${article}`
  }
  const citationLawArticleMap = citationList.reduce((acc, c) => {
    const key = buildLawArticleKey(c?.law_title || c?.title, c?.article_no)
    if (!key) return acc
    acc[key] = c
    return acc
  }, {})
  const parseBasisLawArticle = (basis) => {
    const text = String(basis || "").replace(/[《》]/g, " ").replace(/\s+/g, " ").trim()
    if (!text) return { lawTitle: "", articleNo: "" }
    const matchedArticle = text.match(/第[一二三四五六七八九十百千万0-9]+条/)
    if (!matchedArticle) return { lawTitle: "", articleNo: "" }
    const articleNo = matchedArticle[0]
    const lawTitle = text.replace(articleNo, "").trim()
    return { lawTitle, articleNo }
  }
  const buildCitationTitle = (citation) => {
    if (!citation) return ""
    const lawTitle = String(citation.law_title || citation.title || "").trim()
    const articleNo = normalizeArticleNo(citation.article_no)
    const titlePart = lawTitle ? `《${lawTitle}》` : ""
    return `${titlePart}${articleNo}`.trim()
  }
  const getCitationContent = (citation) => String(citation?.content || citation?.excerpt || "").trim()
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
  const summaryText = String(contractResult?.summary || "").trim()
  const progressStageText = {
    uploading: t.progressUploading,
    extracting: t.progressExtracting,
    retrieval: t.progressRetrieval,
    auditing: t.progressAuditing,
    done: t.progressDone,
    failed: t.progressFailed,
    working: t.progressWorking
  }[contractProgressStage] || t.progressWorking
  const bumpProgress = (percent, stage) => {
    const next = Math.max(0, Math.min(100, Number(percent) || 0))
    setContractProgress((p) => Math.max(p, next))
    if (stage) setContractProgressStage(stage)
  }

  useEffect(() => {
    setActiveRiskIndex(-1)
    setExpandedEvidence({})
    setActivePreviewHighlight(null)
  }, [riskFilter, documentId])

  useEffect(() => {
    previewPageUrlsRef.current = previewPageUrls
  }, [previewPageUrls])

  useEffect(() => {
    if (previewMode !== "visual" || previewContinuous) return
    const bbox = Array.isArray(activePreviewHighlight?.bbox) ? activePreviewHighlight.bbox : null
    if (!bbox || bbox.length !== 4) return
    const box = previewMainRef.current
    if (!box) return
    const y = (Number(bbox[1]) || 0) + (Number(bbox[3]) || 0) / 2
    const target = Math.max(0, box.scrollHeight * y - box.clientHeight / 2)
    box.scrollTo({ top: target, behavior: "smooth" })
  }, [previewMode, previewContinuous, activePreviewPage, activePreviewHighlight])

  useEffect(() => {
    if (previewMode !== "visual" || !previewContinuous) return
    const root = previewMainRef.current
    if (!root) return
    if (previewPageObserverRef.current) {
      previewPageObserverRef.current.disconnect()
      previewPageObserverRef.current = null
    }
    const obs = new IntersectionObserver((entries) => {
      let topPage = 0
      let topRatio = 0
      const incoming = {}
      entries.forEach((entry) => {
        const el = entry.target
        const pageNo = Number(el?.dataset?.pageNo || 0)
        if (pageNo <= 0 || !entry.isIntersecting) return
        incoming[String(pageNo)] = true
        if (entry.intersectionRatio > topRatio) {
          topRatio = entry.intersectionRatio
          topPage = pageNo
        }
      })
      if (Object.keys(incoming).length > 0) {
        setStreamVisiblePages((prev) => ({ ...prev, ...incoming }))
      }
      if (topPage > 0) {
        setActivePreviewPage((prev) => (prev === topPage ? prev : topPage))
        ensurePreviewPageRange(documentId, topPage, 2)
      }
    }, { root, threshold: [0.4, 0.6, 0.85] })
    previewPageObserverRef.current = obs
    Object.values(previewPageRefs.current || {}).forEach((el) => {
      if (el) obs.observe(el)
    })
    return () => {
      obs.disconnect()
      previewPageObserverRef.current = null
    }
  }, [previewMode, previewContinuous, documentId, previewPages])

  useEffect(() => {
    if (previewMode !== "visual" || !previewContinuous || !documentId) return
    if (activePreviewPage > 0) ensurePreviewPageRange(documentId, activePreviewPage, 2)
    Object.keys(streamVisiblePages || {}).forEach((key) => {
      const pageNo = Number(key || 0)
      if (pageNo > 0) ensurePreviewPageRange(documentId, pageNo, 1)
    })
  }, [previewMode, previewContinuous, documentId, activePreviewPage, streamVisiblePages])

  useEffect(() => {
    if (previewMode !== "visual" || !previewContinuous) return
    const box = previewMainRef.current
    if (!box) return
    const onScroll = () => {
      const max = Math.max(1, box.scrollHeight - box.clientHeight)
      const ratio = box.scrollTop / max
      setPreviewScrollPercent(Math.max(0, Math.min(100, Math.round(ratio * 100))))
    }
    onScroll()
    box.addEventListener("scroll", onScroll, { passive: true })
    return () => box.removeEventListener("scroll", onScroll)
  }, [previewMode, previewContinuous, previewPages, previewZoom])

  useEffect(() => {
    if (previewMode !== "visual" || !previewContinuous) return
    const box = previewMainRef.current
    if (!box) return
    const max = Math.max(1, box.scrollHeight - box.clientHeight)
    const ratio = Math.max(0, Math.min(1, Number(previewScrollRatioRef.current || 0)))
    const top = ratio * max
    box.scrollTo({ top, behavior: "auto" })
  }, [previewZoom, previewMode, previewContinuous])

  useEffect(() => () => {
    Object.values(previewPageUrlsRef.current || {}).forEach((u) => {
      if (u) URL.revokeObjectURL(u)
    })
  }, [])

  useEffect(() => {
    if (!contractLoading) {
      setContractProgress(0)
      setContractProgressStage("working")
      return
    }
    bumpProgress(12, "uploading")
    const timers = [
      setTimeout(() => {
        bumpProgress(32, "extracting")
      }, 700),
      setTimeout(() => {
        bumpProgress(58, "retrieval")
      }, 1800),
      setTimeout(() => {
        bumpProgress(75, "auditing")
      }, 3200)
    ]
    const ticker = setInterval(() => {
      setContractProgress((p) => Math.min(p + 1, 75))
    }, 900)
    return () => {
      timers.forEach(clearTimeout)
      clearInterval(ticker)
    }
  }, [contractLoading])

  useEffect(() => {
    const stored = getStoredTheme()
    if (stored) setTheme(stored)
  }, [])

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", normalizeTheme(theme))
  }, [theme])

  useEffect(() => {
    if (!user) return
    const loadUIConfig = async () => {
      try {
        const data = await getUIConfig()
        const serverTheme = normalizeTheme(data?.default_theme)
        const allowContinuous = !!data.preview_continuous_enabled
        setUiConfig({
          showCitationSource: !!data.show_citation_source,
          defaultTheme: serverTheme,
          previewContinuousEnabled: allowContinuous
        })
        setPreviewContinuous(allowContinuous)
        const stored = getStoredTheme()
        if (!stored) setTheme(serverTheme)
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

  const normalizeMatchText = (value) => String(value || "")
    .toLowerCase()
    .replace(/[\s\r\n\t，。；：、“”‘’"'（）()【】\[\]{}<>《》,.!?;:|\\/]+/g, "")
    .trim()

  const extractMatchTerms = (value) => {
    const raw = String(value || "").toLowerCase()
    const matched = raw.match(/[\u4e00-\u9fa5]{2,}|[a-z0-9]{3,}/g) || []
    return Array.from(new Set(matched)).slice(0, 28)
  }

  const pickPreviewHighlight = (risk, blocks, pageNo) => {
    const list = Array.isArray(blocks) ? blocks : []
    if (list.length === 0) return null
    const rawPara = String(risk?.location?.paragraph_no || "").trim()
    const paraDigits = rawPara.match(/\d+/)
    const paraIndex = paraDigits ? (Number(paraDigits[0]) - 1) : -1
    const clausePath = String(risk?.location?.clause_path || "").trim()
    const riskText = [risk?.issue, risk?.suggestion, risk?.evidence, risk?.basis, risk?.law_reference, clausePath].join(" ")
    const riskNorm = normalizeMatchText(riskText)
    const terms = extractMatchTerms(riskText)
    const clausePathNorm = normalizeMatchText(clausePath)
    const recentIndex = Number(previewLastBlockByPageRef.current[String(pageNo)])
    let bestScore = -1
    let best = null
    list.forEach((b, idx) => {
      const bbox = Array.isArray(b?.bbox) && b.bbox.length === 4 ? b.bbox : null
      if (!bbox) return
      const lineNorm = normalizeMatchText(b?.text)
      if (!lineNorm) return
      let score = 0
      if (riskNorm) {
        const headA = lineNorm.slice(0, Math.min(28, lineNorm.length))
        const headB = riskNorm.slice(0, Math.min(28, riskNorm.length))
        if (headA && riskNorm.includes(headA)) score += 4
        if (headB && lineNorm.includes(headB)) score += 3
      }
      if (terms.length > 0) {
        const hit = terms.reduce((n, term) => n + (lineNorm.includes(term) ? 1 : 0), 0)
        const ratio = hit / Math.max(terms.length, 1)
        score += hit * 1.2 + ratio * 2.4
      }
      if (clausePathNorm && lineNorm.includes(clausePathNorm)) {
        score += 5.5
      }
      if (paraIndex >= 0) {
        score += Math.max(0, 0.9 - Math.abs(idx - paraIndex) * 0.08)
      }
      if (Number.isFinite(recentIndex)) {
        score += Math.max(0, 1.2 - Math.abs(idx - recentIndex) * 0.18)
      }
      if (score > bestScore) {
        bestScore = score
        best = {
          bbox,
          blockId: String(b?.block_id || `p${pageNo}-b${idx + 1}`),
          blockIndex: idx,
        }
      }
    })
    return bestScore > 0 ? best : null
  }

  const clearPreviewUrls = () => {
    setPreviewPageUrls((prev) => {
      Object.values(prev).forEach((u) => {
        if (u) URL.revokeObjectURL(u)
      })
      return {}
    })
  }

  const ensurePreviewPageUrl = async (nextDocumentId, pageNo) => {
    const key = String(pageNo)
    if (!nextDocumentId || !key) return ""
    if (previewPageUrlsRef.current[key]) return previewPageUrlsRef.current[key]
    const blob = await getContractPreviewPageImage(nextDocumentId, pageNo)
    const objectUrl = URL.createObjectURL(blob)
    setPreviewPageUrls((prev) => {
      const old = prev[key]
      if (old && old !== objectUrl) URL.revokeObjectURL(old)
      return { ...prev, [key]: objectUrl }
    })
    return objectUrl
  }

  const ensurePreviewPageRange = (nextDocumentId, centerPageNo, radius = 1) => {
    const c = Number(centerPageNo || 0)
    if (!nextDocumentId || c <= 0) return
    const r = Math.max(0, Number(radius || 0))
    for (let i = c - r; i <= c + r; i += 1) {
      if (i > 0) ensurePreviewPageUrl(nextDocumentId, i)
    }
  }

  const onPreviewZoom = (value) => {
    const next = Math.max(0.6, Math.min(1.8, Number(value || 1)))
    const box = previewMainRef.current
    if (box && previewContinuous) {
      const max = Math.max(1, box.scrollHeight - box.clientHeight)
      previewScrollRatioRef.current = box.scrollTop / max
    }
    setPreviewZoom(next)
  }

  const loadContractPreview = async (nextDocumentId) => {
    if (!nextDocumentId) {
      clearPreviewUrls()
      previewLastBlockByPageRef.current = {}
      setStreamVisiblePages({})
      setPreviewMode("text")
      setPreviewPages([])
      setStreamVisiblePages({})
      setActivePreviewPage(1)
      setActivePreviewHighlight(null)
      setPreviewText("")
      setPreviewMeta(null)
      setPreviewError("")
      setPreviewZoom(1)
      setPreviewScrollPercent(0)
      setPreviewThumbsOpen(false)
      setPreviewZoom(1)
      setPreviewScrollPercent(0)
      setPreviewThumbsOpen(false)
      return
    }
    setPreviewLoading(true)
    setPreviewError("")
    try {
      const manifest = await getContractPreviewManifest(nextDocumentId)
      const mode = String(manifest?.mode || "text") === "visual" ? "visual" : "text"
      const pages = Array.isArray(manifest?.pages) ? manifest.pages : []
      clearPreviewUrls()
      previewLastBlockByPageRef.current = {}
      setPreviewMode(mode)
      setPreviewPages(pages)
      setStreamVisiblePages({})
      setActivePreviewPage(1)
      setActivePreviewHighlight(null)
      setPreviewText(String(manifest?.text || ""))
      setPreviewMeta(manifest?.meta || null)
      setPreviewZoom(1)
      setPreviewScrollPercent(0)
      setPreviewThumbsOpen(false)
      if (mode === "visual" && pages.length > 0) {
        const first = Number(pages[0]?.page_no || 1)
        setActivePreviewPage(first)
        await ensurePreviewPageUrl(nextDocumentId, first)
      }
      if (mode !== "visual") return
      const preload = previewContinuous ? pages.slice(1, 7) : pages.slice(1, 4)
      preload.forEach((p) => {
        const no = Number(p?.page_no || 0)
        if (no > 0) ensurePreviewPageUrl(nextDocumentId, no)
      })
    } catch {
      try {
        const preview = await getContractPreview(nextDocumentId)
        clearPreviewUrls()
        previewLastBlockByPageRef.current = {}
        setPreviewMode("text")
        setPreviewPages([])
        setStreamVisiblePages({})
        setActivePreviewPage(1)
        setActivePreviewHighlight(null)
        setPreviewText(String(preview?.text || ""))
        setPreviewMeta(preview?.meta || null)
        setPreviewZoom(1)
        setPreviewScrollPercent(0)
        setPreviewThumbsOpen(false)
      } catch (err) {
        setPreviewError(String(err?.message || err || "Preview failed"))
        setPreviewMode("text")
        setPreviewPages([])
        setStreamVisiblePages({})
        setActivePreviewHighlight(null)
        setPreviewText("")
        setPreviewMeta(null)
      }
    } finally {
      setPreviewLoading(false)
    }
  }

  const onRiskLocate = (risk, idx) => {
    setActiveRiskIndex(idx)
    const pageNo = Number(risk?.location?.page_no || 0)
    if (previewMode === "visual" && pageNo > 0) {
      setActivePreviewPage(pageNo)
      ensurePreviewPageRange(documentId, pageNo, previewContinuous ? 2 : 0)
      const page = previewPages.find((p) => Number(p?.page_no || 0) === pageNo)
      const blocks = Array.isArray(page?.blocks) ? page.blocks : []
      const picked = pickPreviewHighlight(risk, blocks, pageNo)
      if (picked) {
        previewLastBlockByPageRef.current[String(pageNo)] = picked.blockIndex
        previewLocateSeqRef.current += 1
        setActivePreviewHighlight({
          pageNo,
          bbox: picked.bbox,
          blockId: picked.blockId,
          seq: previewLocateSeqRef.current,
        })
      } else {
        setActivePreviewHighlight(null)
      }
      const thumb = previewThumbRefs.current[String(pageNo)]
      if (thumb && typeof thumb.scrollIntoView === "function") {
        thumb.scrollIntoView({ behavior: "smooth", block: "nearest" })
      }
      if (previewContinuous) {
        const pageEl = previewPageRefs.current[String(pageNo)]
        if (pageEl && typeof pageEl.scrollIntoView === "function") {
          pageEl.scrollIntoView({ behavior: "smooth", block: "center" })
        }
      }
      return
    }
    setActivePreviewHighlight(null)
    const container = previewScrollRef.current
    if (!container) return
    container.scrollTo({ top: 0, behavior: "smooth" })
  }

  const toggleEvidence = (key) => {
    if (!key) return
    setExpandedEvidence(prev => ({ ...prev, [key]: !prev[key] }))
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
    bumpProgress(16, "uploading")
    try {
      const form = new FormData()
      const customTitle = String(contract.title || "").trim()
      form.append("title", customTitle || toContractTitle(contractFile.name))
      form.append("language", contract.language)
      form.append("audit_mode", contract.auditMode)
      form.append("region", contract.region)
      form.append("date", contract.date)
      form.append("industry", contract.industry)
      form.append("tax_focus", String(contract.taxFocus))
      form.append("file", contractFile)
      const res = await auditContract(form)
      bumpProgress(100, "done")
      const nextDocumentId = String(res.document_id || "")
      setContractResult(res.result || null)
      setContractMeta(res.meta || null)
      setDocumentId(nextDocumentId)
      await loadContractPreview(nextDocumentId)
    } catch (err) {
      bumpProgress(100, "failed")
      setContractError(String(err?.message || err || "Audit failed"))
      setContractResult(null)
      setContractMeta(null)
      setDocumentId("")
      clearPreviewUrls()
      previewLastBlockByPageRef.current = {}
      setPreviewMode("text")
      setPreviewPages([])
      setActivePreviewPage(1)
      setActivePreviewHighlight(null)
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

  const onThemeChange = (next) => {
    const normalized = normalizeTheme(next)
    setTheme(normalized)
    localStorage.setItem(THEME_STORAGE_KEY, normalized)
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
          <div className="lang-pill">
            <span>{t.themeLabel}</span>
            <select value={theme} onChange={e => onThemeChange(e.target.value)}>
              <option value="dark">{t.themeDark}</option>
              <option value="light">{t.themeLight}</option>
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
            onChange={e => setContract({ ...contract, title: e.target.value })}
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
          {contractLoading && (
            <div className="audit-progress">
              <div className="audit-progress-head">
                <span>{t.progressTitle}</span>
                <span>{contractProgress}%</span>
              </div>
              <div className="audit-progress-bar">
                <div className="audit-progress-fill" style={{ width: `${contractProgress}%` }} />
              </div>
              <div className="audit-progress-note">{progressStageText}</div>
            </div>
          )}
        </div>
      </section>
      <div className="workbench-grid">
        <section className="card contract-preview">
          <div className="preview-head">
            <div className="panel-title">{t.contractPreview}</div>
            {previewMode === "visual" && previewPages.length > 0 && (
              <div className="preview-toolbar">
                <div className="preview-layout-toggle">
                  <span>{t.previewLayout}</span>
                  <button type="button" className={!previewContinuous ? "is-active" : ""} onClick={() => setPreviewContinuous(false)}>{t.previewPaged}</button>
                  <button type="button" className={previewContinuous ? "is-active" : ""} onClick={() => setPreviewContinuous(true)}>{t.previewContinuous}</button>
                </div>
                <div className="preview-layout-toggle">
                  <span>{t.previewZoom}</span>
                  <button type="button" className={previewZoom === 0.75 ? "is-active" : ""} onClick={() => onPreviewZoom(0.75)}>75%</button>
                  <button type="button" className={previewZoom === 1 ? "is-active" : ""} onClick={() => onPreviewZoom(1)}>100%</button>
                  <button type="button" className={previewZoom === 1.25 ? "is-active" : ""} onClick={() => onPreviewZoom(1.25)}>125%</button>
                </div>
                <button type="button" className={`preview-thumb-toggle${previewThumbsOpen ? " is-active" : ""}`} onClick={() => setPreviewThumbsOpen((v) => !v)}>{t.previewThumbs}</button>
              </div>
            )}
            {previewMeta && (
              <div className="preview-meta">
                <span>{t.pageMeta}: {previewMeta.page_count ?? previewPages.length ?? "-"}</span>
                <span>{t.lineCount}: {previewMeta.line_total ?? 0}</span>
                <span>{previewMode === "visual" ? t.previewVisualMode : t.previewTextMode}</span>
              </div>
            )}
          </div>
          {previewLoading && (
            <div className="empty-state preview-empty">
              <p>{t.previewLoading}</p>
            </div>
          )}
          {!previewLoading && previewError && <div className="error">{t.previewError}: {previewError}</div>}
          {!previewLoading && !previewError && previewMode === "visual" && previewPages.length === 0 && (
            <div className="empty-state preview-empty">
              <p>{t.previewEmpty}</p>
            </div>
          )}
          {!previewLoading && !previewError && previewMode === "visual" && previewPages.length > 0 && (
            <div className={`preview-visual${previewThumbsOpen ? " is-thumbs-open" : ""}`}>
              <aside className="preview-thumbs" ref={previewScrollRef}>
                {previewPages.map((p) => {
                  const pageNo = Number(p?.page_no || 0)
                  const key = String(pageNo)
                  const active = pageNo === activePreviewPage
                  const url = previewPageUrls[key] || ""
                  return (
                    <button
                      key={key}
                      type="button"
                      ref={(el) => { previewThumbRefs.current[key] = el }}
                      className={`preview-thumb${active ? " is-active" : ""}`}
                      onClick={() => {
                        setActivePreviewPage(pageNo)
                        setActivePreviewHighlight(null)
                        ensurePreviewPageUrl(documentId, pageNo)
                      }}
                    >
                      <div className="preview-thumb-media">
                        {url ? <img src={url} alt={`page-${pageNo}`} /> : <span>{pageNo}</span>}
                      </div>
                      <div className="preview-thumb-no">P{pageNo}</div>
                    </button>
                  )
                })}
              </aside>
              <div className={`preview-main${previewContinuous ? " preview-main-scroll" : ""}`} ref={previewMainRef}>
                {previewMode === "visual" && (
                  <div className="preview-float-indicator">P{activePreviewPage} / {previewMeta?.page_count ?? previewPages.length} · {t.previewProgress} {previewScrollPercent}%</div>
                )}
                {previewContinuous ? (
                  <div className="preview-stream">
                    {previewPages.map((p) => {
                      const pageNo = Number(p?.page_no || 0)
                      const key = String(pageNo)
                      const active = pageNo === activePreviewPage
                      const url = previewPageUrls[key] || ""
                      return (
                        <article
                          key={`stream-${key}`}
                          data-page-no={key}
                          ref={(el) => {
                            previewPageRefs.current[key] = el
                            if (el && previewPageObserverRef.current) previewPageObserverRef.current.observe(el)
                          }}
                          className={`preview-main-media preview-stream-page${active ? " is-active" : ""}`}
                          onMouseEnter={() => {
                            setActivePreviewPage(pageNo)
                            ensurePreviewPageRange(documentId, pageNo, 2)
                          }}
                        >
                          {url ? <img loading="lazy" decoding="async" style={{ width: `${Math.round(previewZoom * 100)}%` }} src={url} alt={`page-${pageNo}`} /> : <div className="preview-main-loading">{t.previewLoading}</div>}
                          {Array.isArray(activePreviewHighlight?.bbox) && activePreviewHighlight.bbox.length === 4 && Number(activePreviewHighlight?.pageNo || 0) === pageNo ? (
                            <div
                              key={`hl-${activePreviewHighlight.seq || 0}`}
                              className="preview-highlight"
                              style={{
                                left: `${Math.max(0, Math.min(1, Number(activePreviewHighlight.bbox[0]) || 0)) * 100}%`,
                                top: `${Math.max(0, Math.min(1, Number(activePreviewHighlight.bbox[1]) || 0)) * 100}%`,
                                width: `${Math.max(0, Math.min(1, Number(activePreviewHighlight.bbox[2]) || 0)) * 100}%`,
                                height: `${Math.max(0, Math.min(1, Number(activePreviewHighlight.bbox[3]) || 0)) * 100}%`,
                              }}
                            />
                          ) : null}
                        </article>
                      )
                    })}
                  </div>
                ) : (() => {
                  const key = String(activePreviewPage)
                  const url = previewPageUrls[key] || ""
                  return (
                    <div className="preview-main-media">
                      {url ? (
                        <>
                          <img style={{ width: `${Math.round(previewZoom * 100)}%` }} src={url} alt={`page-${activePreviewPage}`} />
                          {Array.isArray(activePreviewHighlight?.bbox) && activePreviewHighlight.bbox.length === 4 && Number(activePreviewHighlight?.pageNo || 0) === activePreviewPage ? (
                            <div
                              key={`hl-${activePreviewHighlight.seq || 0}`}
                              className="preview-highlight"
                              style={{
                                left: `${Math.max(0, Math.min(1, Number(activePreviewHighlight.bbox[0]) || 0)) * 100}%`,
                                top: `${Math.max(0, Math.min(1, Number(activePreviewHighlight.bbox[1]) || 0)) * 100}%`,
                                width: `${Math.max(0, Math.min(1, Number(activePreviewHighlight.bbox[2]) || 0)) * 100}%`,
                                height: `${Math.max(0, Math.min(1, Number(activePreviewHighlight.bbox[3]) || 0)) * 100}%`,
                              }}
                            />
                          ) : null}
                        </>
                      ) : <div className="preview-main-loading">{t.previewLoading}</div>}
                    </div>
                  )
                })()}
              </div>
            </div>
          )}
          {!previewLoading && !previewError && previewMode !== "visual" && !previewText && (
            <div className="empty-state preview-empty">
              <p>{t.previewEmpty}</p>
            </div>
          )}
          {!previewLoading && !previewError && previewMode !== "visual" && !!previewText && (
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
              <div className="result-summary">
                <div className="result-title">{t.summary}</div>
                <div className="summary-meta">
                  <span>{t.riskAll}: {riskSummary.all}</span>
                  <span>{uiLang === "zh" ? `${summaryText.length} 字` : `${summaryText.length} chars`}</span>
                </div>
                <div className={`summary-content${summaryText ? "" : " is-empty"}`}>{summaryText || "-"}</div>
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
                                const basis = String(r.basis || r.law_reference || "").trim()
                                const linkedById = cid ? citationMap[cid] : null
                                const basisParsed = parseBasisLawArticle(basis)
                                const riskLawTitle = String(r.law_title || basisParsed.lawTitle || "").trim()
                                const riskArticleNo = String(r.article_no || basisParsed.articleNo || "").trim()
                                const linkedByLaw = linkedById ? null : citationLawArticleMap[buildLawArticleKey(riskLawTitle, riskArticleNo)]
                                const linked = linkedById || linkedByLaw
                                if (!cid && !linked && !basis) return null
                                const evidenceKey = `${idx}:${cid || basis}`
                                const citationTitle = linked ? buildCitationTitle(linked) : ""
                                const citationContent = linked ? getCitationContent(linked) : ""
                                const expanded = !!expandedEvidence[evidenceKey]
                                return (
                                  <div className="risk-evidence">
                                    <div className="evidence-title">{t.evidenceTitle}</div>
                                    {linked ? (
                                      <div className="evidence-card">
                                        <div className="evidence-law">{citationTitle || "-"}</div>
                                        {citationContent ? (
                                          <>
                                            <button
                                              type="button"
                                              className="evidence-toggle"
                                              onClick={(e) => {
                                                e.stopPropagation()
                                                toggleEvidence(evidenceKey)
                                              }}
                                            >
                                              {expanded ? t.collapseArticle : t.viewArticle}
                                            </button>
                                            {expanded ? <pre className="evidence-content">{citationContent}</pre> : null}
                                          </>
                                        ) : null}
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
