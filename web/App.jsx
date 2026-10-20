import { useEffect, useMemo, useRef, useState } from "react"
import { auditContract, downloadContractSource, exportContractReport, getAuditProgress, getContractPreview, getContractPreviewManifest, getContractPreviewPageImage, getContractSummary, getCurrentUser, getMe, getUIConfig, logout } from "./api"
import { ApiError } from "./api/base"
import { CONTRACT_AUDIT_ERROR_CODES, normalizeContractAuditError } from "./api/errorCatalog"
import { buildAuditSnapshot, clearCurrentAuditPointer, createAuditTaskId, getAuditFileMeta, getAuditUserScope, listAuditSnapshots, loadAuditSnapshotByTaskId, loadCurrentAuditSnapshot, saveCurrentAuditSnapshot } from "./auditPersistence"
import Login from "./Login"
import Admin from "./Admin"
import ContractPreviewPanel from "./ContractPreviewPanel"
import AuditSnapshotHistoryPanel from "./AuditSnapshotHistoryPanel"
import { appI18n } from "./i18n/appI18n"

const THEME_STORAGE_KEY = "ui_theme"
const APP_LANG_STORAGE_KEY = "ui_lang"
const AUDIT_PROGRESS_STARTUP_GRACE_MS = 6000
const RISK_LOCATE_STRONG_CONFIDENCE = 0.78
const RISK_LOCATE_OVERRIDE_DELTA = 0.12

const normalizeTheme = (value) => (String(value || "").toLowerCase() === "light" ? "light" : "dark")
const normalizeAppLang = (value) => (String(value || "").toLowerCase() === "en" ? "en" : "zh")

const getStoredTheme = () => {
  const v = String(localStorage.getItem(THEME_STORAGE_KEY) || "").toLowerCase()
  return v === "light" || v === "dark" ? v : ""
}

const getStoredAppLang = () => {
  const v = normalizeAppLang(localStorage.getItem(APP_LANG_STORAGE_KEY) || "")
  return v === "en" || v === "zh" ? v : "zh"
}

const getFileExtension = (name) => {
  const text = String(name || "").trim().toLowerCase()
  const idx = text.lastIndexOf(".")
  return idx >= 0 ? text.slice(idx) : ""
}

const escapeRegExp = (value) => String(value || "").replace(/[.*+?^${}()|[\]\\]/g, "\\$&")

const getContractSummarySectionLabels = (lang = "zh") => (
  normalizeAppLang(lang) === "en"
    ? {
        about: "What the contract is about",
        core: "Core arrangement",
        tax: "Tax review note",
        source: "Source file",
        legacyCutoffs: ["Page Count", "Clause Count", "Contract Content"],
      }
    : {
        about: "合同在说什么",
        core: "核心安排",
        tax: "税务审核提示",
        source: "来源文件",
        legacyCutoffs: ["合同页数", "条款数", "合同内容"],
      }
)

const normalizeSummarySectionText = (value) => String(value || "")
  .replace(/\r\n/g, "\n")
  .replace(/\n{3,}/g, "\n\n")
  .trim()

const extractSummarySectionText = (text, label, nextLabels = []) => {
  const source = normalizeSummarySectionText(text)
  if (!source) return ""
  const startRegex = new RegExp(`(?:^|\\n)\\s*${escapeRegExp(label)}\\s*[:：]\\s*`, "i")
  const startMatch = startRegex.exec(source)
  if (!startMatch) return ""
  const startIndex = startMatch.index + startMatch[0].length
  let endIndex = source.length
  nextLabels.forEach((nextLabel) => {
    const nextRegex = new RegExp(`(?:^|\\n)\\s*${escapeRegExp(nextLabel)}\\s*[:：]`, "im")
    const remainder = source.slice(startIndex)
    const nextMatch = nextRegex.exec(remainder)
    if (nextMatch) {
      const candidateEnd = startIndex + nextMatch.index
      if (candidateEnd < endIndex) endIndex = candidateEnd
    }
  })
  return normalizeSummarySectionText(source.slice(startIndex, endIndex))
}

const parseStructuredContractSummary = (text, lang = "zh") => {
  const source = normalizeSummarySectionText(text)
  if (!source) return null
  const labels = getContractSummarySectionLabels(lang)
  const itemTitleMatch = source.match(/(?:^|\n)\s*(\d+\.\s*[^\n]+)/)
  const about = extractSummarySectionText(source, labels.about, [labels.core, labels.tax, labels.source, ...labels.legacyCutoffs])
  const core = extractSummarySectionText(source, labels.core, [labels.tax, labels.source, ...labels.legacyCutoffs])
  const tax = extractSummarySectionText(source, labels.tax, [labels.source, ...labels.legacyCutoffs])
  const summarySource = extractSummarySectionText(source, labels.source, labels.legacyCutoffs)
  if (!about && !core && !tax && !summarySource) return null
  return {
    itemTitle: String(itemTitleMatch?.[1] || "").trim(),
    about,
    core,
    tax,
    source: summarySource,
  }
}

const createDefaultContract = (lang = getStoredAppLang()) => ({
  title: "",
  language: normalizeAppLang(lang),
  auditMode: "rag",
  region: "",
  date: "",
  industry: "",
  taxFocus: true
})

export function resolveRiskLocateTarget({ risk, previewPages, pickHighlight }) {
  const requestedPageNo = Number(risk?.location?.page_no || 0)
  const availablePages = Array.isArray(previewPages) ? previewPages : []
  const available = availablePages
    .map((page) => Number(page?.page_no || 0))
    .filter((pageNo) => pageNo > 0)
  const availableSet = new Set(available)
  const fallbackPageNo = availableSet.has(requestedPageNo) ? requestedPageNo : (available[0] || 0)
  const candidates = available
    .map((pageNo) => {
      const page = availablePages.find((item) => Number(item?.page_no || 0) === pageNo)
      const blocks = Array.isArray(page?.blocks) ? page.blocks : []
      const picked = typeof pickHighlight === "function" ? pickHighlight(risk, blocks, pageNo) : null
      if (!picked) return null
      return {
        pageNo,
        picked,
        confidence: Math.max(0, Math.min(1, Number(picked.confidence || 0))),
        strategy: String(picked.strategy || "semantic"),
      }
    })
    .filter(Boolean)
  const requestedCandidate = candidates.find((item) => item.pageNo === requestedPageNo) || null
  let globalBest = null
  candidates.forEach((item) => {
    if (!globalBest || item.confidence > globalBest.confidence) {
      globalBest = item
    }
  })
  let resolved = requestedCandidate || globalBest || null
  if (requestedCandidate && globalBest && globalBest.pageNo !== requestedCandidate.pageNo) {
    const requestedStrong = requestedCandidate.strategy !== "semantic"
      || requestedCandidate.confidence >= RISK_LOCATE_STRONG_CONFIDENCE
    const globalWinsClearly = globalBest.strategy !== "semantic"
      || globalBest.confidence >= requestedCandidate.confidence + RISK_LOCATE_OVERRIDE_DELTA
    resolved = (!requestedStrong && globalWinsClearly) ? globalBest : requestedCandidate
  }
  return {
    requestedPageNo,
    fallbackPageNo,
    pageNo: Number(resolved?.pageNo || fallbackPageNo || 0),
    picked: resolved?.picked || null,
    confidence: Number(resolved?.confidence || (fallbackPageNo > 0 ? 0.35 : 0)),
    strategy: String(resolved?.strategy || "clause_fallback"),
    corrected: !!resolved && requestedPageNo > 0 && resolved.pageNo !== requestedPageNo,
  }
}

export default function App() {
  const initialUserRef = useRef(getCurrentUser())
  const restoredAuditRef = useRef(loadCurrentAuditSnapshot(initialUserRef.current))
  const restoredAudit = restoredAuditRef.current.snapshot
  const [view, setView] = useState("main")
  const [user, setUser] = useState(initialUserRef.current)

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
  const [uiLang, setUiLang] = useState(() => getStoredAppLang())
  const [theme, setTheme] = useState("dark")
  const [currentTaskId, setCurrentTaskId] = useState(() => restoredAudit?.taskId || createAuditTaskId())
  const [currentAuditId, setCurrentAuditId] = useState(() => restoredAudit?.auditId || "")
  const [snapshotHistory, setSnapshotHistory] = useState(() => listAuditSnapshots(initialUserRef.current))
  const [snapshotCompareSelection, setSnapshotCompareSelection] = useState([])
  const [persistenceNoticeCode, setPersistenceNoticeCode] = useState(() => restoredAudit ? "restore_recovered" : restoredAuditRef.current.noticeCode || "")
  const [contract, setContract] = useState(() => restoredAudit?.contract ? { ...createDefaultContract(restoredAudit.contract.language), ...restoredAudit.contract } : createDefaultContract(getStoredAppLang()))
  const [contractFile, setContractFile] = useState(null)
  const [restoredFileMeta, setRestoredFileMeta] = useState(() => restoredAudit?.fileMeta || null)
  const [contractError, setContractError] = useState(() => restoredAudit?.contractError || null)
  const [contractLoading, setContractLoading] = useState(() => !!restoredAudit?.contractLoading)
  const [contractResult, setContractResult] = useState(() => restoredAudit?.contractResult || null)
  const [contractMeta, setContractMeta] = useState(() => restoredAudit?.contractMeta || null)
  const [documentId, setDocumentId] = useState(() => restoredAudit?.documentId || "")
  const [uiConfig, setUiConfig] = useState(() => restoredAudit?.uiConfig || { showCitationSource: false, defaultTheme: "dark", previewContinuousEnabled: true })
  const [previewLoading, setPreviewLoading] = useState(() => !!restoredAudit?.previewLoading)
  const [previewError, setPreviewError] = useState(() => restoredAudit?.previewError || "")
  const [previewText, setPreviewText] = useState(() => restoredAudit?.previewText || "")
  const [previewMeta, setPreviewMeta] = useState(() => restoredAudit?.previewMeta || null)
  const [previewMode, setPreviewMode] = useState(() => restoredAudit?.previewMode || "text")
  const [previewContinuous, setPreviewContinuous] = useState(() => typeof restoredAudit?.previewContinuous === "boolean" ? restoredAudit.previewContinuous : true)
  const [previewThumbsOpen, setPreviewThumbsOpen] = useState(() => !!restoredAudit?.previewThumbsOpen)
  const [previewPages, setPreviewPages] = useState(() => Array.isArray(restoredAudit?.previewPages) ? restoredAudit.previewPages : [])
  const [previewPageUrls, setPreviewPageUrls] = useState({})
  const [streamVisiblePages, setStreamVisiblePages] = useState({})
  const [previewZoom, setPreviewZoom] = useState(() => Number(restoredAudit?.previewZoom || 1) || 1)
  const [previewScrollPercent, setPreviewScrollPercent] = useState(() => Number(restoredAudit?.previewScrollPercent || 0) || 0)
  const [activePreviewPage, setActivePreviewPage] = useState(() => Number(restoredAudit?.activePreviewPage || 1) || 1)
  const [activePreviewHighlight, setActivePreviewHighlight] = useState(() => restoredAudit?.activePreviewHighlight || null)
  const [riskLocateMeta, setRiskLocateMeta] = useState(() => restoredAudit?.riskLocateMeta || {})
  const [activeRiskIndex, setActiveRiskIndex] = useState(() => Number.isFinite(Number(restoredAudit?.activeRiskIndex)) ? Number(restoredAudit.activeRiskIndex) : -1)
  const [riskFilter, setRiskFilter] = useState(() => restoredAudit?.riskFilter || "all")
  const [expandedEvidence, setExpandedEvidence] = useState(() => restoredAudit?.expandedEvidence || {})
  const [contractProgress, setContractProgress] = useState(() => Number(restoredAudit?.contractProgress || 0) || 0)
  const [contractProgressStage, setContractProgressStage] = useState(() => restoredAudit?.contractProgressStage || "working")
  const [exportingFormat, setExportingFormat] = useState(() => restoredAudit?.exportingFormat || "")
  const [sourceDownloading, setSourceDownloading] = useState(() => !!restoredAudit?.sourceDownloading)
  const [exportError, setExportError] = useState(() => restoredAudit?.exportError || "")
  const activeUserScope = getAuditUserScope(user)
  const lastUserScopeRef = useRef(activeUserScope)
  const previewScrollRef = useRef(null)
  const previewMainRef = useRef(null)
  const previewThumbRefs = useRef({})
  const previewPageRefs = useRef({})
  const previewPageUrlsRef = useRef({})
  const previewPageObserverRef = useRef(null)
  const progressAbortRef = useRef(null)
  const progressStartupGraceRef = useRef({ auditId: "", startedAt: 0 })
  const previewLoadedDocumentIdRef = useRef(
    restoredAudit?.documentId && (restoredAudit?.previewText || restoredAudit?.previewPages?.length > 0 || restoredAudit?.previewError)
      ? restoredAudit.documentId
      : ""
  )
  const previewScrollRatioRef = useRef(0)
  const previewLocateSeqRef = useRef(0)
  const previewLastBlockByPageRef = useRef({})
  const previewFailedPagesRef = useRef({})
  const previewLoadingPagesRef = useRef({})
  const previewRequestSeqRef = useRef(0)
  const summaryRequestSeqRef = useRef(0)
  const summaryLoadedDocumentIdRef = useRef(
    restoredAudit?.documentId && (
      String(restoredAudit?.contractResult?.contract_summary || "").trim()
      || String(restoredAudit?.contractMeta?.contract_summary_status || "").trim()
    )
      ? restoredAudit.documentId
      : ""
  )

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
    const enMatched = v.match(/^(?:article|art\.?)[\s.]*([0-9]{1,5})$/i)
    if (enMatched) return `Article ${enMatched[1]}`
    if (v.includes("条")) return v
    if (v.startsWith("第")) return `${v}条`
    if (/^[0-9]{1,5}$/.test(v)) return `第${v}条`
    return v
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
    const matchedArticle = text.match(/第[一二三四五六七八九十百千万0-9]+条|(?:Article|Art\.?)\s*[0-9]{1,5}/i)
    if (!matchedArticle) return { lawTitle: "", articleNo: "" }
    const articleNo = normalizeArticleNo(matchedArticle[0])
    const lawTitle = text.replace(matchedArticle[0], "").trim()
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
  const contractSummaryText = String(contractResult?.contract_summary || "").trim()
  const contractSummaryEnabled = Boolean(contractMeta?.contract_summary_enabled)
  const contractSummaryStatus = String(contractMeta?.contract_summary_status || "").trim().toLowerCase()
  const contractSummaryLanguage = normalizeAppLang(contractMeta?.contract_summary_language || uiLang)
  const structuredContractSummary = useMemo(
    () => parseStructuredContractSummary(contractSummaryText, contractSummaryLanguage),
    [contractSummaryLanguage, contractSummaryText]
  )
  const showContractSummary = contractSummaryEnabled || Boolean(contractSummaryText)
  const contractSummaryStatusLabel = useMemo(() => {
    if (contractSummaryStatus === "ok") return t.contractSummaryStatusOk
    if (contractSummaryStatus === "empty") return t.contractSummaryStatusEmpty
    if (contractSummaryStatus === "error") return t.contractSummaryStatusError
    if (contractSummaryStatus === "empty_input") return t.contractSummaryStatusEmptyInput
    if (contractSummaryStatus === "disabled") return t.contractSummaryStatusDisabled
    return contractSummaryEnabled ? t.progressWorking : t.contractSummaryStatusDisabled
  }, [contractSummaryEnabled, contractSummaryStatus, t.contractSummaryStatusDisabled, t.contractSummaryStatusEmpty, t.contractSummaryStatusEmptyInput, t.contractSummaryStatusError, t.contractSummaryStatusOk, t.progressWorking])
  const contractSummaryNotice = useMemo(() => {
    if (contractSummaryText) return ""
    if (contractSummaryStatus === "error") return t.contractSummaryErrorHint
    if (contractSummaryEnabled) return t.contractSummaryEmptyHint
    return t.emptyContractSummary
  }, [contractSummaryEnabled, contractSummaryStatus, contractSummaryText, t.contractSummaryEmptyHint, t.contractSummaryErrorHint, t.emptyContractSummary])
  const applyContractSummaryPayload = (payload) => {
    const summaryText = String(payload?.summary || "").trim()
    const summaryMeta = payload?.meta && typeof payload.meta === "object" ? payload.meta : {}
    setContractResult((prev) => {
      const next = prev && typeof prev === "object" ? { ...prev } : {}
      next.contract_summary = summaryText
      return next
    })
    setContractMeta((prev) => ({
      ...(prev && typeof prev === "object" ? prev : {}),
      ...summaryMeta,
    }))
  }
  const summaryText = String(contractResult?.summary || "").trim()
  const auditLegalValidation = contractResult?.legal_validation && typeof contractResult.legal_validation === "object"
    ? contractResult.legal_validation
    : null
  const auditIncompleteIssues = useMemo(() => (
    Array.isArray(auditLegalValidation?.issues) ? auditLegalValidation.issues.filter((item) => item && typeof item === "object") : []
  ), [auditLegalValidation])
  const auditIncomplete = useMemo(() => {
    if (Boolean(contractResult?.audit_incomplete)) return true
    if (Boolean(contractMeta?.audit_incomplete)) return true
    if (auditLegalValidation && auditLegalValidation.ok === false && auditIncompleteIssues.length > 0) return true
    return false
  }, [auditIncompleteIssues.length, auditLegalValidation, contractMeta?.audit_incomplete, contractResult?.audit_incomplete])
  const auditIncompleteNotice = useMemo(() => {
    if (!auditIncomplete) return ""
    const firstIssue = auditIncompleteIssues.find((item) => String(item?.message || "").trim())
    return String(firstIssue?.message || t.auditIncompleteResult).trim()
  }, [auditIncomplete, auditIncompleteIssues, t.auditIncompleteResult])
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
  const digitizationStatus = String(contractMeta?.digitization_status || "").toLowerCase()
  const digitizationUsed = Boolean(contractMeta?.digitization_used)
  const digitizedDocxAvailable = Boolean(contractMeta?.digitized_docx_available)
  const currentSourceMime = String(contractFile?.type || restoredFileMeta?.type || "").toLowerCase()
  const currentSourceExt = getFileExtension(contractFile?.name || restoredFileMeta?.name || "")
  const currentSourceIsPdf = currentSourceMime === "application/pdf" || currentSourceExt === ".pdf"
  const digitizedExportLabel = currentSourceIsPdf ? t.exportDigitizedDocxPlain : t.exportDigitizedDocx
  const commentedExportLabel = currentSourceIsPdf ? t.exportCommentedDigitizedDocx : t.exportCommentedDocx
  const digitizationStatusLabel = ({
    done: t.digitizationStatusDone,
    failed: t.digitizationStatusFailed,
    skipped: t.digitizationStatusSkipped,
    running: t.digitizationStatusRunning,
    pending: t.digitizationStatusPending,
  })[digitizationStatus] || (digitizationStatus || "-")
  const digitizationMatchRate = Number(contractMeta?.digitization_text_match_ratio || 0)
  const digitizationMatchRateLabel = digitizedDocxAvailable
    ? `${Math.max(0, Math.round(digitizationMatchRate * 1000) / 10)}%`
    : "-"
  const persistenceNoticeText = ({
    restore_recovered: t.auditRestoreRecovered,
    restore_invalid: t.auditRestoreInvalid,
    storage_pruned: t.auditRestoreStoragePruned,
    storage_full: t.auditRestoreStorageFull,
    restore_incomplete: t.auditRestoreIncomplete,
    restore_progress_missing: t.auditRestoreProgressMissing,
  })[persistenceNoticeCode] || ""
  const displayFileName = contractFile?.name || restoredFileMeta?.name || t.noFileChosen
  const newTaskDisabled = contractLoading
  const bumpProgress = (percent, stage) => {
    const next = Math.max(0, Math.min(100, Number(percent) || 0))
    setContractProgress((p) => Math.max(p, next))
    if (stage) setContractProgressStage(stage)
  }

  const refreshSnapshotHistory = (targetUser = user) => {
    setSnapshotHistory(listAuditSnapshots(targetUser))
  }

  const applyAuditSnapshot = (snapshot, options = {}) => {
    const nextSnapshot = snapshot || null
    progressStartupGraceRef.current = { auditId: "", startedAt: 0 }
    const nextContract = nextSnapshot?.contract
      ? { ...createDefaultContract(nextSnapshot.contract.language || uiLang), ...nextSnapshot.contract }
      : createDefaultContract(uiLang)
    previewRequestSeqRef.current += 1
    clearPreviewUrls()
    previewLastBlockByPageRef.current = {}
    previewFailedPagesRef.current = {}
    previewLoadingPagesRef.current = {}
    previewLoadedDocumentIdRef.current = nextSnapshot?.documentId && (nextSnapshot?.previewText || nextSnapshot?.previewPages?.length > 0 || nextSnapshot?.previewError)
      ? nextSnapshot.documentId
      : ""
    summaryLoadedDocumentIdRef.current = nextSnapshot?.documentId && (
      String(nextSnapshot?.contractResult?.contract_summary || "").trim()
      || String(nextSnapshot?.contractMeta?.contract_summary_status || "").trim()
    )
      ? `${nextSnapshot.documentId}:${normalizeAppLang(nextSnapshot?.contractMeta?.contract_summary_language || nextSnapshot?.contract?.language || uiLang)}`
      : ""
    setCurrentTaskId(nextSnapshot?.taskId || createAuditTaskId())
    setCurrentAuditId(nextSnapshot?.auditId || "")
    setContract(nextContract)
    setContractFile(null)
    setRestoredFileMeta(nextSnapshot?.fileMeta || null)
    setContractError(nextSnapshot?.contractError || null)
    setContractLoading(!!nextSnapshot?.contractLoading)
    setContractResult(nextSnapshot?.contractResult || null)
    setContractMeta(nextSnapshot?.contractMeta || null)
    setDocumentId(nextSnapshot?.documentId || "")
    setPreviewLoading(!!nextSnapshot?.previewLoading)
    setPreviewError(nextSnapshot?.previewError || "")
    setPreviewText(nextSnapshot?.previewText || "")
    setPreviewMeta(nextSnapshot?.previewMeta || null)
    setPreviewMode(nextSnapshot?.previewMode || "text")
    setPreviewContinuous(typeof nextSnapshot?.previewContinuous === "boolean" ? nextSnapshot.previewContinuous : !!uiConfig.previewContinuousEnabled)
    setPreviewThumbsOpen(!!nextSnapshot?.previewThumbsOpen)
    setPreviewPages(Array.isArray(nextSnapshot?.previewPages) ? nextSnapshot.previewPages : [])
    setPreviewPageUrls({})
    setStreamVisiblePages({})
    setPreviewZoom(Number(nextSnapshot?.previewZoom || 1) || 1)
    setPreviewScrollPercent(Number(nextSnapshot?.previewScrollPercent || 0) || 0)
    setActivePreviewPage(Number(nextSnapshot?.activePreviewPage || 1) || 1)
    setActivePreviewHighlight(nextSnapshot?.activePreviewHighlight || null)
    setRiskLocateMeta(nextSnapshot?.riskLocateMeta || {})
    setActiveRiskIndex(Number.isFinite(Number(nextSnapshot?.activeRiskIndex)) ? Number(nextSnapshot.activeRiskIndex) : -1)
    setRiskFilter(nextSnapshot?.riskFilter || "all")
    setExpandedEvidence(nextSnapshot?.expandedEvidence || {})
    setContractProgress(Number(nextSnapshot?.contractProgress || 0) || 0)
    setContractProgressStage(nextSnapshot?.contractProgressStage || "working")
    setExportingFormat(nextSnapshot?.exportingFormat || "")
    setSourceDownloading(!!nextSnapshot?.sourceDownloading)
    setExportError(nextSnapshot?.exportError || "")
    setPersistenceNoticeCode(options.noticeCode || (nextSnapshot ? "restore_recovered" : ""))
  }

  const resetAuditWorkspace = () => {
    const nextTaskId = createAuditTaskId()
    previewRequestSeqRef.current += 1
    progressStartupGraceRef.current = { auditId: "", startedAt: 0 }
    if (progressAbortRef.current) {
      progressAbortRef.current.abort()
      progressAbortRef.current = null
    }
    clearCurrentAuditPointer(user)
    clearPreviewUrls()
    previewLastBlockByPageRef.current = {}
    previewFailedPagesRef.current = {}
    previewLoadingPagesRef.current = {}
    previewLoadedDocumentIdRef.current = ""
    summaryLoadedDocumentIdRef.current = ""
    setCurrentTaskId(nextTaskId)
    setCurrentAuditId("")
    setSnapshotCompareSelection([])
    setPersistenceNoticeCode("")
    setContract(createDefaultContract(uiLang))
    setContractFile(null)
    setRestoredFileMeta(null)
    setContractError(null)
    setContractLoading(false)
    setContractResult(null)
    setContractMeta(null)
    setDocumentId("")
    setPreviewLoading(false)
    setPreviewError("")
    setPreviewText("")
    setPreviewMeta(null)
    setPreviewMode("text")
    setPreviewContinuous(!!uiConfig.previewContinuousEnabled)
    setPreviewThumbsOpen(false)
    setPreviewPages([])
    setPreviewPageUrls({})
    setStreamVisiblePages({})
    setPreviewZoom(1)
    setPreviewScrollPercent(0)
    setActivePreviewPage(1)
    setActivePreviewHighlight(null)
    setRiskLocateMeta({})
    setActiveRiskIndex(-1)
    setRiskFilter("all")
    setExpandedEvidence({})
    setContractProgress(0)
    setContractProgressStage("working")
    setExportingFormat("")
    setSourceDownloading(false)
    setExportError("")
    if (fileInputRef.current) {
      fileInputRef.current.value = ""
    }
    refreshSnapshotHistory(user)
  }

  const onStartNewTask = () => {
    if (newTaskDisabled) return
    const confirmed = window.confirm(t.startNewTaskConfirm)
    if (!confirmed) return
    resetAuditWorkspace()
  }

  const onToggleSnapshotCompare = (taskId) => {
    setSnapshotCompareSelection((prev) => {
      if (prev.includes(taskId)) return prev.filter((item) => item !== taskId)
      const next = [...prev, taskId]
      return next.length <= 3 ? next : next.slice(next.length - 3)
    })
  }

  const onLoadSnapshotHistory = (taskId) => {
    const restored = loadAuditSnapshotByTaskId(user, taskId)
    if (restored.snapshot) {
      applyAuditSnapshot(restored.snapshot, { noticeCode: "restore_recovered" })
      refreshSnapshotHistory(user)
      return
    }
    setPersistenceNoticeCode("restore_invalid")
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
    localStorage.setItem(APP_LANG_STORAGE_KEY, normalizeAppLang(uiLang))
  }, [uiLang])

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
        setPreviewContinuous((prev) => (allowContinuous ? prev : false))
        if (!allowContinuous) setPreviewThumbsOpen(true)
        const stored = getStoredTheme()
        if (!stored) setTheme(serverTheme)
      } catch {
      }
    }
    loadUIConfig()
  }, [user])

  useEffect(() => {
    if (!user) return
    const scopeChanged = lastUserScopeRef.current !== activeUserScope
    const restored = loadCurrentAuditSnapshot(user)
    refreshSnapshotHistory(user)
    setSnapshotCompareSelection([])
    if (scopeChanged) {
      lastUserScopeRef.current = activeUserScope
      applyAuditSnapshot(restored.snapshot, { noticeCode: restored.snapshot ? "restore_recovered" : restored.noticeCode || "" })
    }
  }, [user, activeUserScope])

  useEffect(() => {
    if (!currentTaskId) setCurrentTaskId(createAuditTaskId())
  }, [currentTaskId])

  useEffect(() => {
    setSnapshotCompareSelection((prev) => prev.filter((taskId) => snapshotHistory.some((item) => item.taskId === taskId)))
  }, [snapshotHistory])

  useEffect(() => {
    if (!documentId || previewLoading) return
    if (previewMode === "visual" && previewPages.length > 0) {
      ensurePreviewPageRange(documentId, activePreviewPage || Number(previewPages[0]?.page_no || 1), previewContinuous ? 2 : 0)
      return
    }
    if (previewLoadedDocumentIdRef.current !== documentId && !previewError) {
      void loadContractPreview(documentId)
    }
  }, [documentId, previewMode, previewPages, activePreviewPage, previewContinuous, previewLoading, previewText, previewError])

  useEffect(() => {
    if (!documentId || !contractResult || contractLoading) return
    const requestKey = `${documentId}:${uiLang}`
    const terminalStatuses = new Set(["ok", "empty", "error", "disabled", "empty_input"])
    if (contractSummaryText && contractSummaryLanguage === uiLang) {
      summaryLoadedDocumentIdRef.current = requestKey
      return
    }
    if (terminalStatuses.has(contractSummaryStatus) && contractSummaryLanguage === uiLang) {
      summaryLoadedDocumentIdRef.current = requestKey
      return
    }
    if (summaryLoadedDocumentIdRef.current === requestKey) return

    let cancelled = false
    const requestSeq = summaryRequestSeqRef.current + 1
    summaryRequestSeqRef.current = requestSeq
    setContractMeta((prev) => ({
      ...(prev && typeof prev === "object" ? prev : {}),
      contract_summary_enabled: true,
      contract_summary_status: "working",
      contract_summary_language: uiLang,
      contract_summary_error: "",
    }))

    const loadSummary = async () => {
      try {
        const payload = await getContractSummary(documentId, uiLang)
        if (cancelled || summaryRequestSeqRef.current !== requestSeq) return
        applyContractSummaryPayload(payload)
        summaryLoadedDocumentIdRef.current = requestKey
      } catch (err) {
        if (cancelled || summaryRequestSeqRef.current !== requestSeq) return
        const message = String(err?.message || err || "")
        applyContractSummaryPayload({
          summary: "",
          meta: {
            contract_summary_enabled: true,
            contract_summary_status: "error",
            contract_summary_error: message,
            contract_summary_language: uiLang,
          },
        })
        summaryLoadedDocumentIdRef.current = requestKey
      }
    }

    void loadSummary()
    return () => {
      cancelled = true
    }
  }, [documentId, contractLoading, contractResult, contractSummaryLanguage, contractSummaryStatus, contractSummaryText, uiLang])

  useEffect(() => {
    if (!user || !currentTaskId) return
    const snapshot = buildAuditSnapshot({
      taskId: currentTaskId,
      auditId: currentAuditId || currentTaskId,
      documentId,
      contract,
      fileMeta: getAuditFileMeta(contractFile) || restoredFileMeta,
      contractError,
      contractLoading,
      contractResult,
      contractMeta,
      uiConfig,
      previewLoading,
      previewError,
      previewText,
      previewMeta,
      previewMode,
      previewContinuous,
      previewThumbsOpen,
      previewPages,
      previewZoom,
      previewScrollPercent,
      activePreviewPage,
      activePreviewHighlight,
      riskLocateMeta,
      activeRiskIndex,
      riskFilter,
      expandedEvidence,
      contractProgress,
      contractProgressStage,
      exportingFormat,
      sourceDownloading,
      exportError,
    })
    const timer = window.setTimeout(() => {
      const result = saveCurrentAuditSnapshot(snapshot, user)
      refreshSnapshotHistory(user)
      if (result.noticeCode === "storage_pruned") {
        setPersistenceNoticeCode("storage_pruned")
      } else if (result.noticeCode === "storage_full") {
        setPersistenceNoticeCode("storage_full")
      }
    }, 180)
    return () => window.clearTimeout(timer)
  }, [
    user,
    currentTaskId,
    currentAuditId,
    documentId,
    contract,
    contractFile,
    restoredFileMeta,
    contractError,
    contractLoading,
    contractResult,
    contractMeta,
    uiConfig,
    previewLoading,
    previewError,
    previewText,
    previewMeta,
    previewMode,
    previewContinuous,
    previewThumbsOpen,
    previewPages,
    previewZoom,
    previewScrollPercent,
    activePreviewPage,
    activePreviewHighlight,
    riskLocateMeta,
    activeRiskIndex,
    riskFilter,
    expandedEvidence,
    contractProgress,
    contractProgressStage,
    exportingFormat,
    sourceDownloading,
    exportError,
  ])

  useEffect(() => {
    if (!user || !contractLoading || !currentAuditId) return
    let cancelled = false
    let timer = 0
    const syncProgress = async () => {
      const controller = new AbortController()
      progressAbortRef.current = controller
      try {
        const payload = await getAuditProgress(currentAuditId, { signal: controller.signal })
        if (progressAbortRef.current === controller) {
          progressAbortRef.current = null
        }
        if (cancelled || !payload) return
        const nextProgress = Math.max(0, Math.min(100, Number(payload.progress || 0)))
        setContractProgress(nextProgress)
        setContractProgressStage(String(payload.stage || "working"))
        if (String(payload.status || "").toLowerCase() === "failed") {
          setContractLoading(false)
          setPersistenceNoticeCode("restore_incomplete")
          setContractError(normalizeContractAuditError({
            code: CONTRACT_AUDIT_ERROR_CODES.RuntimeFailed,
            category: "runtime",
            message: String(payload.message || t.auditRestoreIncomplete),
          }, uiLang))
        }
        if (String(payload.status || "").toLowerCase() === "done" && !contractResult) {
          setContractLoading(false)
          setPersistenceNoticeCode("restore_incomplete")
        }
      } catch (err) {
        if (progressAbortRef.current === controller) {
          progressAbortRef.current = null
        }
        if (cancelled) return
        if (err?.name === "AbortError") return
        if (err instanceof ApiError && err.status === 404) {
          const startupGrace = progressStartupGraceRef.current
          const startupRequestMatched = startupGrace.auditId === currentAuditId
          const withinStartupGrace = startupRequestMatched
            && startupGrace.startedAt > 0
            && (Date.now() - startupGrace.startedAt) < AUDIT_PROGRESS_STARTUP_GRACE_MS
          if (withinStartupGrace) {
            setContractProgressStage("uploading")
            return
          }
          if (timer) window.clearInterval(timer)
          setContractLoading(false)
          setContractProgressStage("failed")
          if (!contractResult) {
            const startupTimeoutMessage = startupRequestMatched
              ? String(t.auditStartupProgressTimeout)
              : String(t.auditRestoreProgressMissing)
            setPersistenceNoticeCode(startupRequestMatched ? "" : "restore_progress_missing")
            setContractError(normalizeContractAuditError({
              code: CONTRACT_AUDIT_ERROR_CODES.RuntimeFailed,
              category: "runtime",
              message: startupTimeoutMessage,
            }, uiLang))
          }
        }
      }
    }
    void syncProgress()
    timer = window.setInterval(syncProgress, 2000)
    return () => {
      cancelled = true
      if (progressAbortRef.current) {
        progressAbortRef.current.abort()
        progressAbortRef.current = null
      }
      if (timer) window.clearInterval(timer)
    }
  }, [user, contractLoading, currentAuditId, contractResult, uiLang, t.auditRestoreIncomplete])

  const formatRiskLocation = (location, locateMeta = null) => {
    const pageNo = Number(locateMeta?.resolvedPageNo || location?.page_no || 0)
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
    const requestSeq = ++previewRequestSeqRef.current
    if (!nextDocumentId) {
      previewLoadedDocumentIdRef.current = ""
      clearPreviewUrls()
      previewLastBlockByPageRef.current = {}
      setStreamVisiblePages({})
      setPreviewMode("text")
      setPreviewPages([])
      setStreamVisiblePages({})
      setActivePreviewPage(1)
      setActivePreviewHighlight(null)
      setPreviewThumbsOpen(false)
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
      if (previewRequestSeqRef.current !== requestSeq) return
      const mode = String(manifest?.mode || "text") === "visual" ? "visual" : "text"
      const pages = Array.isArray(manifest?.pages) ? manifest.pages : []
      clearPreviewUrls()
      previewLastBlockByPageRef.current = {}
      setPreviewMode(mode)
      setPreviewPages(pages)
      setStreamVisiblePages({})
      setActivePreviewPage(1)
      setActivePreviewHighlight(null)
      setPreviewThumbsOpen(false)
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
        if (previewRequestSeqRef.current !== requestSeq) return
        clearPreviewUrls()
        previewLastBlockByPageRef.current = {}
        setPreviewMode("text")
        setPreviewPages([])
        setStreamVisiblePages({})
        setActivePreviewPage(1)
        setActivePreviewHighlight(null)
        setPreviewThumbsOpen(false)
        setPreviewText(String(preview?.text || ""))
        setPreviewMeta(preview?.meta || null)
        setPreviewZoom(1)
        setPreviewScrollPercent(0)
      } catch (err) {
        if (previewRequestSeqRef.current !== requestSeq) return
        setPreviewError(String(err?.message || err || "Preview failed"))
        setPreviewMode("text")
        setPreviewPages([])
        setStreamVisiblePages({})
        setActivePreviewHighlight(null)
        setPreviewThumbsOpen(false)
        setPreviewText("")
        setPreviewMeta(null)
      }
    } finally {
      if (previewRequestSeqRef.current === requestSeq) {
        previewLoadedDocumentIdRef.current = nextDocumentId
        setPreviewLoading(false)
      }
    }
  }

  const onRiskLocate = (risk, idx) => {
    setActiveRiskIndex(idx)
    const locateTarget = resolveRiskLocateTarget({
      risk,
      previewPages,
      pickHighlight: (nextRisk, blocks, pageNo) => pickPreviewHighlight(nextRisk, blocks, pageNo),
    })
    const pageNo = Number(locateTarget.pageNo || 0)
    setRiskLocateMeta((prev) => {
      const next = { ...prev }
      next[String(idx)] = {
        strategy: String(locateTarget.strategy || "clause_fallback"),
        confidence: Math.max(0, Math.min(1, Number(locateTarget.confidence || (pageNo > 0 ? 0.35 : 0)))),
        resolvedPageNo: pageNo,
        corrected: !!locateTarget.corrected,
      }
      return next
    })
    if (previewMode === "visual" && pageNo > 0) {
      setActivePreviewPage(pageNo)
      ensurePreviewPageRange(documentId, pageNo, previewContinuous ? 2 : 0)
      const best = locateTarget.picked
      let bestPageNo = pageNo
      if (best) {
        setActivePreviewPage(bestPageNo)
        ensurePreviewPageRange(documentId, bestPageNo, previewContinuous ? 2 : 0)
        previewLastBlockByPageRef.current[String(bestPageNo)] = best.blockIndex
        previewLocateSeqRef.current += 1
        setActivePreviewHighlight({ pageNo: bestPageNo, bbox: best.bbox, blockId: best.blockId, seq: previewLocateSeqRef.current })
        setRiskLocateMeta((prev) => ({
          ...prev,
          [String(idx)]: {
            strategy: String(best.strategy || locateTarget.strategy || "semantic"),
            confidence: Math.max(0, Math.min(1, Number(best.confidence || locateTarget.confidence || 0))),
            resolvedPageNo: bestPageNo,
            corrected: !!locateTarget.corrected,
          },
        }))
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
      setContractError(normalizeContractAuditError({
        code: CONTRACT_AUDIT_ERROR_CODES.UnsupportedFileType,
        category: "validation",
        message: t.invalidFileType
      }, uiLang))
      return
    }
    const nextAuditId = createAuditTaskId()
    progressStartupGraceRef.current = {
      auditId: nextAuditId,
      startedAt: Date.now(),
    }
    setCurrentTaskId(nextAuditId)
    setCurrentAuditId(nextAuditId)
    summaryLoadedDocumentIdRef.current = ""
    setSnapshotCompareSelection([])
    setPersistenceNoticeCode("")
    setContractError(null)
    setExportError("")
    setExportingFormat("")
    setContractResult(null)
    setContractMeta(null)
    setDocumentId("")
    setRestoredFileMeta(getAuditFileMeta(contractFile))
    clearPreviewUrls()
    previewLastBlockByPageRef.current = {}
    setPreviewMode("text")
    setPreviewPages([])
    setStreamVisiblePages({})
    setActivePreviewPage(1)
    setActivePreviewHighlight(null)
    setPreviewThumbsOpen(false)
    setPreviewText("")
    setPreviewMeta(null)
    setPreviewError("")
    setPreviewZoom(1)
    setPreviewScrollPercent(0)
    setContractLoading(true)
    setRiskFilter("all")
    setActiveRiskIndex(-1)
    bumpProgress(16, "uploading")
    try {
      const form = new FormData()
      form.append("audit_id", nextAuditId)
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
      const resolvedAuditId = String(res.audit_id || nextAuditId)
      progressStartupGraceRef.current = {
        auditId: resolvedAuditId,
        startedAt: progressStartupGraceRef.current.auditId === nextAuditId
          ? progressStartupGraceRef.current.startedAt
          : Date.now(),
      }
      const nextDocumentId = String(res.document_id || "")
      setCurrentTaskId(resolvedAuditId)
      setCurrentAuditId(resolvedAuditId)
      const detectedLang = normalizeAppLang(res?.meta?.language || contract.language)
      setUiLang(detectedLang)
      setContract(prev => ({ ...prev, language: detectedLang }))
      setContractResult(res.result || null)
      setContractMeta(res.meta || null)
      setDocumentId(nextDocumentId)
      // Preview loading can take longer than the audit itself, especially for
      // visual manifests with many pages. Do not keep the audit progress stuck
      // in the running state while preview assets are still loading.
      void loadContractPreview(nextDocumentId)
    } catch (err) {
      bumpProgress(100, "failed")
      setContractError(normalizeContractAuditError(err, contract.language || uiLang))
      setContractResult(null)
      setContractMeta(null)
      setDocumentId("")
      clearPreviewUrls()
      previewLastBlockByPageRef.current = {}
      setPreviewMode("text")
      setPreviewPages([])
      setActivePreviewPage(1)
      setActivePreviewHighlight(null)
      setPreviewThumbsOpen(false)
      setPreviewText("")
      setPreviewMeta(null)
      setPreviewError("")
    } finally {
      progressStartupGraceRef.current = { auditId: "", startedAt: 0 }
      setContractLoading(false)
    }
  }

  const downloadBlobFile = (blob, fileName) => {
    const url = window.URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = fileName
    document.body.appendChild(a)
    a.click()
    a.remove()
    window.URL.revokeObjectURL(url)
  }

  const onExportReport = async (format, mode = "report") => {
    if (!documentId || !contractResult) return
    const fmt = String(format || "json").toLowerCase() === "docx" ? "docx" : "json"
    setExportError("")
    setExportingFormat(`${fmt}_${mode}`)
    try {
      const response = await exportContractReport(documentId, {
        export_format: fmt,
        export_mode: mode,
        locale: uiLang === "zh" ? "zh-CN" : "en-US",
        template_version: "v1.0",
        include_appendix: true,
      })
      const blob = response?.blob
      if (!(blob instanceof Blob)) {
        throw new Error(t.exportFailed)
      }
      const ext = fmt === "docx" ? "docx" : "json"
      const prefix = mode === "comments"
        ? "contract_with_comments"
        : mode === "digitized_source"
          ? "contract_digitized_source"
          : "contract_audit_report"
      const responseFilename = String(response?.filename || "").trim()
      downloadBlobFile(blob, responseFilename || `${prefix}_${documentId}.${ext}`)
    } catch (err) {
      setExportError(String(err?.message || err || t.exportFailed))
    } finally {
      setExportingFormat("")
    }
  }

  const onDownloadSource = async () => {
    if (!documentId) return
    setExportError("")
    setSourceDownloading(true)
    try {
      const blob = await downloadContractSource(documentId, "original")
      const fallbackName = contractFile?.name || `contract_source_${documentId}${String(contractMeta?.source_document_ext || "").trim() || ""}`
      downloadBlobFile(blob, fallbackName)
    } catch (err) {
      setExportError(String(err?.message || err || t.exportFailed))
    } finally {
      setSourceDownloading(false)
    }
  }

  const onPreviewLayoutChange = (nextContinuous) => {
    if (nextContinuous && !uiConfig.previewContinuousEnabled) return
    setPreviewContinuous(!!nextContinuous)
    if (!nextContinuous) setPreviewThumbsOpen(true)
  }

  const onPreviewZoomChange = (direction) => {
    if (direction === "reset") {
      setPreviewZoom(1)
      return
    }
    setPreviewZoom((prev) => {
      const delta = direction === "in" ? 0.15 : -0.15
      const next = Math.max(0.7, Math.min(2.2, Number(prev || 1) + delta))
      return Math.round(next * 100) / 100
    })
  }

  const onPreviewPageSelect = (pageNo) => {
    const nextPage = Number(pageNo || 0)
    if (nextPage <= 0) return
    setActivePreviewPage(nextPage)
    ensurePreviewPageRange(documentId, nextPage, previewContinuous ? 2 : 0)
    if (previewContinuous) {
      const pageEl = previewPageRefs.current[String(nextPage)]
      if (pageEl && typeof pageEl.scrollIntoView === "function") {
        pageEl.scrollIntoView({ behavior: "smooth", block: "center" })
      }
    }
  }

  const onPreviewPageHover = (pageNo) => {
    const nextPage = Number(pageNo || 0)
    if (nextPage <= 0) return
    setActivePreviewPage(nextPage)
    ensurePreviewPageRange(documentId, nextPage, 2)
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
    return <Admin lang={uiLang} onLangChange={onUiLanguageChange} onBack={() => setView("main")} />
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
      {persistenceNoticeText && (
        <div className={`card audit-restore-banner${persistenceNoticeCode === "restore_invalid" || persistenceNoticeCode === "storage_full" ? " is-warning" : ""}`}>
          {persistenceNoticeText}
        </div>
      )}
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
                <option value="skill_orchestrated">{t.modeSkillOrchestrated}</option>
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
                setRestoredFileMeta(getAuditFileMeta(f))
                setContract(prev => ({ ...prev, title: f ? toContractTitle(f.name) : "" }))
                setContractError(null)
              }}
              style={{ display: "none" }}
            />
            <button type="button" className="ghost" onClick={() => fileInputRef.current && fileInputRef.current.click()}>
              {t.chooseFile}
            </button>
            <span>{displayFileName}</span>
            <em>{t.uploadHint}</em>
            {!contractFile && restoredFileMeta && (
              <small className="audit-restore-file-note">{t.restoredFileNotice}</small>
            )}
          </div>
          {contractError && (
            <div className="error contract-error-card">
              <div className="contract-error-head">
                <strong>{contractError.title || t.auditErrorTitle}</strong>
                {contractError.code && <span>{contractError.code}</span>}
              </div>
              <p className="contract-error-message">{contractError.message}</p>
              {(contractError.scenarios?.length > 0 || contractError.actions?.length > 0) && (
                <details className="contract-error-details">
                  <summary>{t.auditErrorOpsInfo}</summary>
                  <div className="contract-error-meta-list">
                    <div className="contract-error-meta-row">
                      <label>{t.auditErrorCode}</label>
                      <span>{contractError.code || "-"}</span>
                    </div>
                    <div className="contract-error-meta-row">
                      <label>{t.auditErrorCategory}</label>
                      <span>{contractError.categoryLabel || contractError.category || "-"}</span>
                    </div>
                  </div>
                  {contractError.scenarios?.length > 0 && (
                    <div className="contract-error-section">
                      <div className="contract-error-section-title">{t.auditErrorScenarios}</div>
                      <ul>
                        {contractError.scenarios.map((item) => (
                          <li key={`scenario-${item}`}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                  {contractError.actions?.length > 0 && (
                    <div className="contract-error-section">
                      <div className="contract-error-section-title">{t.auditErrorActions}</div>
                      <ul>
                        {contractError.actions.map((item) => (
                          <li key={`action-${item}`}>{item}</li>
                        ))}
                      </ul>
                    </div>
                  )}
                </details>
              )}
            </div>
          )}
          <div className="task-action-row">
            <button className="primary" onClick={onContractUpload} disabled={contractLoading}>
              {contractLoading ? t.uploading : t.uploadBtn}
            </button>
            <button
              type="button"
              className="ghost start-new-task-btn"
              onClick={onStartNewTask}
              disabled={newTaskDisabled}
            >
              {t.startNewTask}
            </button>
          </div>
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
        <ContractPreviewPanel
          t={t}
          documentId={documentId}
          previewLoading={previewLoading}
          previewError={previewError}
          previewMode={previewMode}
          previewPages={previewPages}
          previewText={previewText}
          previewMeta={previewMeta}
          previewContinuous={previewContinuous}
          previewContinuousEnabled={uiConfig.previewContinuousEnabled}
          previewThumbsOpen={previewThumbsOpen}
          previewZoom={previewZoom}
          previewScrollPercent={previewScrollPercent}
          activePreviewPage={activePreviewPage}
          activePreviewHighlight={activePreviewHighlight}
          previewPageUrls={previewPageUrls}
          previewCoordProviderLabel={previewCoordProviderLabel}
          previewPipelineLabel={previewPipelineLabel}
          previewGateLabel={previewGateLabel}
          previewGateRatioLabel={previewGateRatioLabel}
          previewConversion={previewConversion}
          previewMainRef={previewMainRef}
          previewScrollRef={previewScrollRef}
          previewPageRefs={previewPageRefs}
          previewPageObserverRef={previewPageObserverRef}
          previewThumbRefs={previewThumbRefs}
          onToggleThumbs={() => setPreviewThumbsOpen((prev) => !prev)}
          onLayoutChange={onPreviewLayoutChange}
          onPageSelect={onPreviewPageSelect}
          onPageHover={onPreviewPageHover}
          onZoomChange={onPreviewZoomChange}
          onDownloadSource={onDownloadSource}
          sourceDownloading={sourceDownloading}
        />
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
                {digitizedDocxAvailable && (
                  <button
                    type="button"
                    className="primary outline"
                    onClick={() => onExportReport("docx", "digitized_source")}
                    disabled={!!exportingFormat}
                  >
                    {exportingFormat === "docx_digitized_source" ? t.exporting : digitizedExportLabel}
                  </button>
                )}
                <button
                  type="button"
                  className="primary outline"
                  onClick={() => onExportReport("docx", "comments")}
                  disabled={!!exportingFormat}
                >
                  {exportingFormat === "docx_comments" ? t.exporting : commentedExportLabel}
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
              {showContractSummary && (
                <div className="result-summary">
                  <div className="result-title">{t.contractSummary}</div>
                  <div className="summary-meta">
                    <span>{t.clauseCount}: {contractMeta?.preview_clause_total ?? previewMeta?.clause_total ?? "-"}</span>
                    <span>{t.contractSummaryStatus}: {contractSummaryStatusLabel}</span>
                    <span>{uiLang === "zh" ? `${contractSummaryText.length} 字` : `${contractSummaryText.length} chars`}</span>
                  </div>
                  <div className={`summary-content${contractSummaryText ? "" : " is-empty"}${structuredContractSummary ? " is-structured" : ""}`}>
                    {contractSummaryText ? (
                      structuredContractSummary ? (
                        <div className="structured-summary">
                          {structuredContractSummary.itemTitle && (
                            <div className="structured-summary-title">{structuredContractSummary.itemTitle}</div>
                          )}
                          <p className="structured-summary-line">
                            <strong>{t.contractSummarySectionAbout}：</strong>
                            <span>{structuredContractSummary.about || "-"}</span>
                          </p>
                          <p className="structured-summary-line">
                            <strong>{t.contractSummarySectionCore}：</strong>
                            <span>{structuredContractSummary.core || "-"}</span>
                          </p>
                          <p className="structured-summary-line">
                            <strong>{t.contractSummarySectionTax}：</strong>
                            <span>{structuredContractSummary.tax || "-"}</span>
                          </p>
                          <p className="structured-summary-line">
                            <strong>{t.contractSummarySectionSource}：</strong>
                            <span>{structuredContractSummary.source || "-"}</span>
                          </p>
                        </div>
                      ) : (
                        contractSummaryText
                      )
                    ) : contractSummaryNotice}
                  </div>
                </div>
              )}
              <div className="result-summary">
                <div className="result-title">{t.summary}</div>
                <div className="summary-meta">
                  <span>{t.riskAll}: {riskSummary.all}</span>
                  <span>{uiLang === "zh" ? `${summaryText.length} 字` : `${summaryText.length} chars`}</span>
                </div>
                <div className={`summary-content${summaryText ? "" : " is-empty"}`}>{summaryText || "-"}</div>
              </div>
              {auditIncomplete && (
                <div className="audit-restore-banner is-warning">{auditIncompleteNotice}</div>
              )}
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
                          const locateMeta = riskLocateMeta[String(idx)]
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
                              <div className="risk-location">{t.location}: {formatRiskLocation(location, locateMeta)}</div>
                              {(() => {
                                const meta = locateMeta
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
                      <div className="empty-risk">{auditIncomplete ? t.auditIncompleteResult : t.emptyRisk}</div>
                    )}
                  </>
                ) : (
                  <div className="empty-risk">{auditIncomplete ? t.auditIncompleteResult : t.emptyRisk}</div>
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
              {(digitizationUsed || digitizedDocxAvailable) && (
                <>
                  <span>{t.digitizationStatus}: {digitizationStatusLabel}</span>
                  <span>{t.digitizationVersion}: {contractMeta.digitization_version ?? 0}</span>
                  <span>{t.digitizationMatchRate}: {digitizationMatchRateLabel}</span>
                  <span>{t.digitizationLayout}: {contractMeta.digitization_layout_retained ? "on" : "off"}</span>
                  <span>{t.digitizationParagraphs}: {contractMeta.digitization_paragraph_count ?? 0}</span>
                  <span>{t.digitizationTables}: {contractMeta.digitization_table_count ?? 0}</span>
                  <span>{t.digitizationImages}: {contractMeta.digitization_image_count ?? 0}</span>
                </>
              )}
              <span>ID: {documentId || "-"}</span>
            </div>
          )}
        </section>
      </div>
      <AuditSnapshotHistoryPanel
        t={t}
        uiLang={uiLang}
        user={user}
        snapshots={snapshotHistory}
        currentTaskId={currentTaskId}
        selectedTaskIds={snapshotCompareSelection}
        onToggleSelect={onToggleSnapshotCompare}
        onLoadSnapshot={onLoadSnapshotHistory}
      />
    </div>
  )
}
