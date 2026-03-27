import { useEffect, useMemo, useRef, useState } from "react"
import { auditContract, exportContractReport, getContractPreview, getContractPreviewManifest, getContractPreviewPageImage, getCurrentUser, getMe, getUIConfig, logout } from "./api"
import Login from "./Login"
import Admin from "./Admin"
import { appI18n } from "./i18n/appI18n"

const THEME_STORAGE_KEY = "ui_theme"

const normalizeTheme = (value) => (String(value || "").toLowerCase() === "light" ? "light" : "dark")
const normalizeAppLang = (value) => (String(value || "").toLowerCase() === "en" ? "en" : "zh")

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
  const [uiConfig, setUiConfig] = useState({ showCitationSource: false, defaultTheme: "dark", previewContinuousEnabled: true })
  const [previewLoading, setPreviewLoading] = useState(false)
  const [previewError, setPreviewError] = useState("")
  const [previewText, setPreviewText] = useState("")
  const [previewMeta, setPreviewMeta] = useState(null)
  const [previewMode, setPreviewMode] = useState("text")
  const [previewContinuous, setPreviewContinuous] = useState(true)
  const [previewPages, setPreviewPages] = useState([])
  const [previewPageUrls, setPreviewPageUrls] = useState({})
  const [streamVisiblePages, setStreamVisiblePages] = useState({})
  const [previewZoom, setPreviewZoom] = useState(1)
  const [previewScrollPercent, setPreviewScrollPercent] = useState(0)
  const [activePreviewPage, setActivePreviewPage] = useState(1)
  const [activePreviewHighlight, setActivePreviewHighlight] = useState(null)
  const [riskLocateMeta, setRiskLocateMeta] = useState({})
  const [activeRiskIndex, setActiveRiskIndex] = useState(-1)
  const [riskFilter, setRiskFilter] = useState("all")
  const [expandedEvidence, setExpandedEvidence] = useState({})
  const [contractProgress, setContractProgress] = useState(0)
  const [contractProgressStage, setContractProgressStage] = useState("working")
  const [exportingFormat, setExportingFormat] = useState("")
  const [exportError, setExportError] = useState("")
  const previewScrollRef = useRef(null)
  const previewMainRef = useRef(null)
  const previewThumbRefs = useRef({})
  const previewPageRefs = useRef({})
  const previewPageUrlsRef = useRef({})
  const previewPageObserverRef = useRef(null)
  const previewScrollRatioRef = useRef(0)
  const previewLocateSeqRef = useRef(0)
  const previewLastBlockByPageRef = useRef({})
  const previewFailedPagesRef = useRef({})
  const previewLoadingPagesRef = useRef({})

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
  const previewSource = String(previewMeta?.source || "").toLowerCase()
  const previewCoordProvider = String(previewMeta?.coord_provider || "").toLowerCase()
  const previewConversion = previewMeta?.docx_pdf_conversion && typeof previewMeta.docx_pdf_conversion === "object"
    ? previewMeta.docx_pdf_conversion
    : null
  const previewQuality = previewConversion?.quality && typeof previewConversion.quality === "object"
    ? previewConversion.quality
    : null
  const previewCoordProviderLabel = ({
    mineru: t.previewProviderMineru,
    docx_layout: t.previewProviderDocxLayout,
    text_fallback: t.previewProviderTextFallback,
    ratio_estimate: t.previewProviderRatioEstimate
  })[previewCoordProvider] || (previewCoordProvider || "-")
  const previewPipelineLabel = ({
    docx_pdf_mineru: t.previewPipelineDocxPdfMineru,
    docx_raster: t.previewPipelineDocxRaster,
    pdf_raster_mineru: t.previewPipelinePdfMineru,
    pdf_raster: t.previewPipelinePdfRaster,
    text_fallback: t.previewPipelineTextFallback
  })[previewSource] || (previewSource || "-")
  const previewGateLabel = !previewConversion
    ? "-"
    : previewConversion.fallback
      ? t.previewGateFallback
      : t.previewGatePassed
  const previewGateRatio = previewQuality ? Number(previewQuality.text_ratio || 0) : 0
  const previewGateRatioLabel = previewQuality ? `${Math.max(0, Math.round(previewGateRatio * 1000) / 10)}%` : "-"
  const bumpProgress = (percent, stage) => {
    const next = Math.max(0, Math.min(100, Number(percent) || 0))
    setContractProgress((p) => Math.max(p, next))
    if (stage) setContractProgressStage(stage)
  }

  useEffect(() => {
    setActiveRiskIndex(-1)
    setExpandedEvidence({})
    setActivePreviewHighlight(null)
    setRiskLocateMeta({})
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
        const allowContinuous = true
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

  const formatRiskLevel = (value) => {
    const level = String(value || "").trim().toLowerCase()
    if (level === "high") return t.riskHigh
    if (level === "medium") return t.riskMedium
    if (level === "low") return t.riskLow
    return value || "N/A"
  }

  const formatAnchorStrategy = (value) => {
    if (value === "quote_exact") return t.locateExact
    if (value === "quote_fuzzy") return t.locateFuzzy
    if (value === "semantic") return t.locateSemantic
    if (value === "clause_fallback") return t.locateClauseFallback
    return t.locateUnknown
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

  const isLikelyHeadingLine = (value) => {
    const text = String(value || "").trim()
    if (!text) return false
    if (text.length <= 40 && /^第[一二三四五六七八九十百千万0-9]+[章节条款]/.test(text)) return true
    const numbered = text.match(/^([一二三四五六七八九十]+[、.．]|[0-9]+(?:\.[0-9]+){0,3}[、.．]?)(.*)$/)
    if (numbered) {
      const tail = String(numbered[2] || "").trim()
      if (!tail) return true
      if (tail.length <= 12 && !/[：:，,。；;]/.test(tail)) return true
      return false
    }
    if (
      text.length <= 28
      && /^[（(]?[一二三四五六七八九十0-9]+[)）][^。；;，,:：]{0,24}$/.test(text)
    ) return true
    if (
      text.length <= 24
      && !/[。；;，,:：]/.test(text)
      && /(合同|条款|价款|支付|期限|服务|违约|保密|发票|税率)/.test(text)
    ) return true
    return false
  }

  const pickPreviewHighlight = (risk, blocks, pageNo) => {
    const list = Array.isArray(blocks) ? blocks : []
    if (list.length === 0) return null
    const rawPara = String(risk?.location?.paragraph_no || "").trim()
    const paraDigits = rawPara.match(/\d+/)
    const paraIndex = paraDigits ? (Number(paraDigits[0]) - 1) : -1
    const clausePath = String(risk?.location?.clause_path || "").trim()
    const evidenceText = String(risk?.location?.quote || risk?.evidence || "").trim()
    const weakText = [risk?.issue, risk?.suggestion, risk?.evidence, risk?.basis, risk?.law_reference].join(" ")
    const evidenceNorm = normalizeMatchText(evidenceText)
    const evidenceTerms = extractMatchTerms(evidenceText)
    const weakNorm = normalizeMatchText(weakText)
    const weakTerms = extractMatchTerms(weakText)
    const clausePathNorm = normalizeMatchText(clausePath)
    const recentIndex = Number(previewLastBlockByPageRef.current[String(pageNo)])
    let bestScore = -1
    let best = null
    let bestBodyScore = -1
    let bestBody = null
    const candidatesByIndex = {}
    list.forEach((b, idx) => {
      const bbox = Array.isArray(b?.bbox) && b.bbox.length === 4 ? b.bbox : null
      if (!bbox) return
      const lineRaw = String(b?.text || "")
      const lineNorm = normalizeMatchText(b?.text)
      if (!lineNorm) return
      const lineIsHeading = typeof b?.is_heading === "boolean" ? b.is_heading : isLikelyHeadingLine(lineRaw)
      let score = 0
      let strongHit = 0
      if (evidenceNorm) {
        const headA = lineNorm.slice(0, Math.min(36, lineNorm.length))
        const headB = evidenceNorm.slice(0, Math.min(36, evidenceNorm.length))
        if (lineNorm.includes(evidenceNorm)) score += 30
        if (evidenceNorm.includes(lineNorm) && lineNorm.length >= 12) score += 13
        if (headA && evidenceNorm.includes(headA)) score += 6
        if (headB && lineNorm.includes(headB)) score += 5
        if (evidenceTerms.length > 0) {
          strongHit = evidenceTerms.reduce((n, term) => n + (lineNorm.includes(term) ? 1 : 0), 0)
          const strongRatio = strongHit / Math.max(evidenceTerms.length, 1)
          score += strongHit * 2.4 + strongRatio * 8.4
        }
      }
      if (weakNorm) {
        const headA = lineNorm.slice(0, Math.min(24, lineNorm.length))
        const headB = weakNorm.slice(0, Math.min(24, weakNorm.length))
        if (headA && weakNorm.includes(headA)) score += 2.6
        if (headB && lineNorm.includes(headB)) score += 2
      }
      if (weakTerms.length > 0) {
        const hit = weakTerms.reduce((n, term) => n + (lineNorm.includes(term) ? 1 : 0), 0)
        const ratio = hit / Math.max(weakTerms.length, 1)
        score += hit * 0.9 + ratio * 2.2
      }
      if (clausePathNorm && lineNorm.includes(clausePathNorm)) score += 0.4
      if (lineNorm.length >= 14) score += 0.8
      if (/[。；;，,:：]/.test(lineRaw)) score += 0.6
      if (lineIsHeading) {
        score -= evidenceNorm ? 7.6 : 6.0
        if (clausePathNorm && (lineNorm === clausePathNorm || clausePathNorm.includes(lineNorm))) score -= 6.4
        if (evidenceNorm && strongHit === 0) score -= 4.0
      }
      if (paraIndex >= 0) score += Math.max(0, 0.9 - Math.abs(idx - paraIndex) * 0.08)
      if (Number.isFinite(recentIndex)) score += Math.max(0, 1.2 - Math.abs(idx - recentIndex) * 0.18)
      const exactContain = evidenceNorm && lineNorm.includes(evidenceNorm)
      const fuzzyContain = evidenceNorm && !exactContain && ((evidenceNorm.includes(lineNorm) && lineNorm.length >= 12) || strongHit >= Math.max(2, Math.ceil(evidenceTerms.length * 0.5)))
      const strategy = exactContain ? "quote_exact" : (fuzzyContain ? "quote_fuzzy" : "semantic")
      const confidenceRaw = exactContain ? 0.96 : (fuzzyContain ? 0.8 : Math.max(0.36, Math.min(0.78, score / 24)))
      const candidate = {
        bbox,
        blockId: String(b?.block_id || `p${pageNo}-b${idx + 1}`),
        blockIndex: idx,
        strategy,
        confidence: Number(confidenceRaw.toFixed(2)),
        score,
        isHeading: lineIsHeading,
      }
      candidatesByIndex[idx] = candidate
      if (score > bestScore) {
        bestScore = score
        best = candidate
      }
      if (!lineIsHeading && score > bestBodyScore) {
        bestBodyScore = score
        bestBody = candidate
      }
    })
    if (best && best.isHeading) {
      const nextCandidates = [1, 2, 3]
        .map((step) => candidatesByIndex[best.blockIndex + step])
        .filter((c) => c && !c.isHeading)
      const nearbyBody = nextCandidates.find((c) => c.score >= best.score - 8 && c.blockIndex === best.blockIndex + 1)
        || nextCandidates.find((c) => c.score >= best.score - 4.5)
      if (nearbyBody) best = nearbyBody
    }
    if (best && best.isHeading && bestBody) {
      const bodyCloseEnough = bestBody.score >= best.score - 3.2
      const bodyClearlyStronger = bestBody.score >= best.score - 1.2
      if (bodyCloseEnough || bodyClearlyStronger) best = bestBody
    }
    if (best && best.score > 0) {
      const picked = { ...best }
      delete picked.score
      delete picked.isHeading
      return picked
    }
    return null
  }

  const clearPreviewUrls = () => {
    setPreviewPageUrls((prev) => {
      Object.values(prev).forEach((u) => {
        if (u) URL.revokeObjectURL(u)
      })
      return {}
    })
    previewFailedPagesRef.current = {}
    previewLoadingPagesRef.current = {}
  }

  const getAvailablePreviewPageNos = () => {
    const out = []
    ;(previewPages || []).forEach((p) => {
      const no = Number(p?.page_no || 0)
      if (no > 0) out.push(no)
    })
    return out
  }

  const ensurePreviewPageUrl = async (nextDocumentId, pageNo) => {
    const no = Number(pageNo || 0)
    const key = String(no)
    if (!nextDocumentId || no <= 0) return ""
    if (previewPageUrlsRef.current[key]) return previewPageUrlsRef.current[key]
    if (previewFailedPagesRef.current[key]) return ""
    if (previewLoadingPagesRef.current[key]) return ""
    previewLoadingPagesRef.current[key] = true
    try {
      const blob = await getContractPreviewPageImage(nextDocumentId, no)
      const objectUrl = URL.createObjectURL(blob)
      setPreviewPageUrls((prev) => {
        const old = prev[key]
        if (old && old !== objectUrl) URL.revokeObjectURL(old)
        return { ...prev, [key]: objectUrl }
      })
      return objectUrl
    } catch {
      previewFailedPagesRef.current[key] = true
      return ""
    } finally {
      delete previewLoadingPagesRef.current[key]
    }
  }

  const ensurePreviewPageRange = (nextDocumentId, centerPageNo, radius = 1) => {
    const c = Number(centerPageNo || 0)
    if (!nextDocumentId || c <= 0) return
    const available = new Set(getAvailablePreviewPageNos())
    const r = Math.max(0, Number(radius || 0))
    for (let i = c - r; i <= c + r; i += 1) {
      if (i > 0 && available.has(i)) ensurePreviewPageUrl(nextDocumentId, i)
    }
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
      setPreviewZoom(1)
      setPreviewScrollPercent(0)
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
      const meta = manifest?.meta && typeof manifest.meta === "object" ? manifest.meta : {}
      setPreviewMeta({ ...meta, source: String(manifest?.source || "") })
      setPreviewZoom(1)
      setPreviewScrollPercent(0)
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
    const requestedPageNo = Number(risk?.location?.page_no || 0)
    const available = getAvailablePreviewPageNos()
    const availableSet = new Set(available)
    const pageNo = availableSet.has(requestedPageNo) ? requestedPageNo : (available[0] || 0)
    setRiskLocateMeta((prev) => {
      const next = { ...prev }
      next[String(idx)] = {
        strategy: "clause_fallback",
        confidence: pageNo > 0 ? 0.35 : 0,
      }
      return next
    })
    if (previewMode === "visual" && pageNo > 0) {
      setActivePreviewPage(pageNo)
      ensurePreviewPageRange(documentId, pageNo, previewContinuous ? 2 : 0)
      const candidates = requestedPageNo > 0 && availableSet.has(requestedPageNo) ? [requestedPageNo] : available
      let best = null
      let bestPageNo = pageNo
      candidates.forEach((no) => {
        const page = previewPages.find((p) => Number(p?.page_no || 0) === no)
        const blocks = Array.isArray(page?.blocks) ? page.blocks : []
        const picked = pickPreviewHighlight(risk, blocks, no)
        if (!picked) return
        const conf = Math.max(0, Math.min(1, Number(picked.confidence || 0)))
        if (!best || conf > Number(best.confidence || 0)) {
          best = picked
          bestPageNo = no
        }
      })
      if (best) {
        setActivePreviewPage(bestPageNo)
        ensurePreviewPageRange(documentId, bestPageNo, previewContinuous ? 2 : 0)
        previewLastBlockByPageRef.current[String(bestPageNo)] = best.blockIndex
        previewLocateSeqRef.current += 1
        setActivePreviewHighlight({ pageNo: bestPageNo, bbox: best.bbox, blockId: best.blockId, seq: previewLocateSeqRef.current })
        setRiskLocateMeta((prev) => ({ ...prev, [String(idx)]: { strategy: String(best.strategy || "semantic"), confidence: Math.max(0, Math.min(1, Number(best.confidence || 0))) } }))
      } else {
        setActivePreviewHighlight(null)
      }
      const thumb = previewThumbRefs.current[String(bestPageNo)]
      if (thumb && typeof thumb.scrollIntoView === "function") {
        thumb.scrollIntoView({ behavior: "smooth", block: "nearest" })
      }
      if (previewContinuous) {
        const pageEl = previewPageRefs.current[String(bestPageNo)]
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
    setExportError("")
    setExportingFormat("")
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
      const detectedLang = normalizeAppLang(res?.meta?.language || contract.language)
      setUiLang(detectedLang)
      setContract(prev => ({ ...prev, language: detectedLang }))
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

  const onExportReport = async (format, mode = "report") => {
    if (!documentId || !contractResult) return
    const fmt = String(format || "json").toLowerCase() === "docx" ? "docx" : "json"
    setExportError("")
    setExportingFormat(`${fmt}_${mode}`)
    try {
      const blob = await exportContractReport(documentId, {
        export_format: fmt,
        export_mode: mode,
        locale: uiLang === "zh" ? "zh-CN" : "en-US",
        template_version: "v1.0",
        include_appendix: true,
      })
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement("a")
      const ext = fmt === "docx" ? "docx" : "json"
      const prefix = mode === "comments" ? "contract_with_comments" : "contract_audit_report"
      const fileName = `${prefix}_${documentId}.${ext}`
      a.href = url
      a.download = fileName
      document.body.appendChild(a)
      a.click()
      a.remove()
      window.URL.revokeObjectURL(url)
    } catch (err) {
      setExportError(String(err?.message || err || t.exportFailed))
    } finally {
      setExportingFormat("")
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
              <div className="preview-toolbar" />
            )}
            {previewMeta && (
              <div className="preview-meta">
                <span>{t.pageMeta}: {previewMeta.page_count ?? previewPages.length ?? "-"}</span>
                <span>{t.lineCount}: {previewMeta.line_total ?? 0}</span>
                <span>{previewMode === "visual" ? t.previewVisualMode : t.previewTextMode}</span>
                <span>{t.previewCoordSource}: {previewCoordProviderLabel}</span>
                <span>{t.previewPipeline}: {previewPipelineLabel}</span>
                <span className={`preview-gate ${previewConversion?.fallback ? "is-fallback" : "is-pass"}`}>{t.previewGate}: {previewGateLabel}</span>
                {previewConversion && (
                  <span>{t.previewGateRatio}: {previewGateRatioLabel}</span>
                )}
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
            <div className="preview-visual">
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
          <div className="result-head">
            <div className="panel-title">{t.auditResult}</div>
            {contractResult && (
              <div className="result-export-actions">
                <button
                  type="button"
                  className="ghost"
                  onClick={() => onExportReport("json", "report")}
                  disabled={!!exportingFormat}
                >
                  {exportingFormat === "json_report" ? t.exporting : t.exportJson}
                </button>
                <button
                  type="button"
                  className="primary"
                  onClick={() => onExportReport("docx", "report")}
                  disabled={!!exportingFormat}
                >
                  {exportingFormat === "docx_report" ? t.exporting : t.exportDocx}
                </button>
                <button
                  type="button"
                  className="primary outline"
                  onClick={() => onExportReport("docx", "comments")}
                  disabled={!!exportingFormat}
                >
                  {exportingFormat === "docx_comments" ? t.exporting : t.exportCommentedDocx}
                </button>
              </div>
            )}
          </div>
          {exportError && <div className="error">{t.exportFailed}: {exportError}</div>}
          {!contractResult && (
            <div className="empty-state">
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
                              <strong>{formatRiskLevel(r.level)}</strong>
                              <span>{r.type || "-"}</span>
                              <p>{r.issue || "-"}</p>
                              <div className="risk-location">{t.location}: {formatRiskLocation(location)}</div>
                              {(() => {
                                const key = String(idx)
                                const meta = riskLocateMeta[key]
                                if (!meta || !meta.strategy) return null
                                const confidence = Math.max(0, Math.min(1, Number(meta.confidence || 0)))
                                const confidencePercent = Math.round(confidence * 100)
                                return (
                                  <div className={`risk-locate-quality strategy-${meta.strategy}`}>
                                    {t.locateMode}: {formatAnchorStrategy(meta.strategy)} · {t.locateConfidence} {confidencePercent}%
                                  </div>
                                )
                              })()}
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
