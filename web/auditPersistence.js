const STORAGE_PREFIX = "contract_audit_runtime_v2"
const SNAPSHOT_VERSION = 2
const MAX_TASKS = 8
const MAX_TASK_AGE_MS = 14 * 24 * 60 * 60 * 1000

const isObject = (value) => !!value && typeof value === "object" && !Array.isArray(value)
const toBool = (value, fallback = false) => (typeof value === "boolean" ? value : fallback)
const toInt = (value, fallback = 0, min = 0, max = 999999) => {
  const num = Number(value)
  if (!Number.isFinite(num)) return fallback
  return Math.max(min, Math.min(max, Math.round(num)))
}
const toFloat = (value, fallback = 0, min = 0, max = 100) => {
  const num = Number(value)
  if (!Number.isFinite(num)) return fallback
  return Math.max(min, Math.min(max, num))
}
const toStringValue = (value, fallback = "") => (typeof value === "string" ? value : fallback)
const toPlainObject = (value, fallback = {}) => (isObject(value) ? value : fallback)
const toPlainArray = (value) => (Array.isArray(value) ? value : [])
const nowIso = () => new Date().toISOString()

const safeParse = (value) => {
  if (!value) return null
  try {
    return JSON.parse(value)
  } catch {
    return null
  }
}

const computeRiskSummary = (result) => {
  const risks = Array.isArray(result?.risks) ? result.risks : []
  const summary = { all: risks.length, high: 0, medium: 0, low: 0 }
  risks.forEach((risk) => {
    const level = String(risk?.level || "").toLowerCase()
    if (level === "high" || level === "medium" || level === "low") summary[level] += 1
  })
  return summary
}

const normalizeFileMeta = (value) => {
  if (!isObject(value)) return null
  const name = toStringValue(value.name).trim()
  if (!name) return null
  return {
    name,
    size: toInt(value.size, 0, 0, Number.MAX_SAFE_INTEGER),
    type: toStringValue(value.type),
    lastModified: toInt(value.lastModified, 0, 0, Number.MAX_SAFE_INTEGER),
  }
}

const normalizeContract = (value, fallbackLang = "zh") => {
  const raw = toPlainObject(value)
  return {
    title: toStringValue(raw.title),
    language: toStringValue(raw.language, fallbackLang || "zh"),
    auditMode: toStringValue(raw.auditMode, "rag"),
    region: toStringValue(raw.region),
    date: toStringValue(raw.date),
    industry: toStringValue(raw.industry),
    taxFocus: toBool(raw.taxFocus, true),
  }
}

const normalizeSnapshot = (value) => {
  if (!isObject(value)) return null
  const taskId = toStringValue(value.taskId).trim()
  if (!taskId) return null
  return {
    version: SNAPSHOT_VERSION,
    taskId,
    auditId: toStringValue(value.auditId, taskId),
    documentId: toStringValue(value.documentId),
    userScope: toStringValue(value.userScope),
    updatedAt: toStringValue(value.updatedAt, nowIso()),
    createdAt: toStringValue(value.createdAt, nowIso()),
    lastAccessedAt: toStringValue(value.lastAccessedAt, nowIso()),
    contract: normalizeContract(value.contract),
    fileMeta: normalizeFileMeta(value.fileMeta),
    contractError: value.contractError == null ? null : value.contractError,
    contractLoading: toBool(value.contractLoading, false),
    contractResult: value.contractResult == null ? null : value.contractResult,
    contractMeta: value.contractMeta == null ? null : value.contractMeta,
    uiConfig: toPlainObject(value.uiConfig, {}),
    previewLoading: toBool(value.previewLoading, false),
    previewError: toStringValue(value.previewError),
    previewText: toStringValue(value.previewText),
    previewMeta: value.previewMeta == null ? null : value.previewMeta,
    previewMode: toStringValue(value.previewMode, "text") === "visual" ? "visual" : "text",
    previewContinuous: toBool(value.previewContinuous, true),
    previewThumbsOpen: toBool(value.previewThumbsOpen, false),
    previewPages: toPlainArray(value.previewPages),
    previewZoom: toFloat(value.previewZoom, 1, 0.7, 2.2),
    previewScrollPercent: toInt(value.previewScrollPercent, 0, 0, 100),
    activePreviewPage: toInt(value.activePreviewPage, 1, 1, 100000),
    activePreviewHighlight: value.activePreviewHighlight == null ? null : value.activePreviewHighlight,
    riskLocateMeta: toPlainObject(value.riskLocateMeta, {}),
    activeRiskIndex: toInt(value.activeRiskIndex, -1, -1, 100000),
    riskFilter: toStringValue(value.riskFilter, "all"),
    expandedEvidence: toPlainObject(value.expandedEvidence, {}),
    contractProgress: toInt(value.contractProgress, 0, 0, 100),
    contractProgressStage: toStringValue(value.contractProgressStage, "working"),
    exportingFormat: toStringValue(value.exportingFormat),
    sourceDownloading: toBool(value.sourceDownloading, false),
    exportError: toStringValue(value.exportError),
  }
}

const getSnapshotMetrics = (snapshot) => {
  const riskSummary = computeRiskSummary(snapshot.contractResult)
  return {
    riskSummary,
    summaryText: toStringValue(snapshot.contractResult?.summary).trim(),
    pageCount: toInt(snapshot.previewMeta?.page_count, 0, 0, 100000),
    clauseCount: toInt(snapshot.contractMeta?.preview_clause_total ?? snapshot.previewMeta?.clause_total, 0, 0, 100000),
    lineCount: toInt(snapshot.previewMeta?.line_total, 0, 0, 100000),
    digitizationStatus: toStringValue(snapshot.contractMeta?.digitization_status).toLowerCase(),
  }
}

const buildSnapshotSummary = (snapshot) => {
  const metrics = getSnapshotMetrics(snapshot)
  return {
    taskId: snapshot.taskId,
    auditId: snapshot.auditId,
    documentId: snapshot.documentId,
    updatedAt: snapshot.updatedAt,
    createdAt: snapshot.createdAt,
    lastAccessedAt: snapshot.lastAccessedAt,
    title: toStringValue(snapshot.contract?.title).trim() || toStringValue(snapshot.fileMeta?.name).trim() || snapshot.documentId || snapshot.taskId,
    fileName: toStringValue(snapshot.fileMeta?.name),
    auditMode: toStringValue(snapshot.contract?.auditMode, "rag"),
    region: toStringValue(snapshot.contract?.region),
    industry: toStringValue(snapshot.contract?.industry),
    language: toStringValue(snapshot.contract?.language, "zh"),
    status: snapshot.contractLoading ? "running" : (snapshot.contractResult ? "done" : (snapshot.contractError ? "failed" : "draft")),
    progressStage: toStringValue(snapshot.contractProgressStage, "working"),
    progress: toInt(snapshot.contractProgress, 0, 0, 100),
    summaryText: metrics.summaryText,
    riskSummary: metrics.riskSummary,
    pageCount: metrics.pageCount,
    clauseCount: metrics.clauseCount,
    lineCount: metrics.lineCount,
    digitizationStatus: metrics.digitizationStatus,
    previewMode: snapshot.previewMode,
  }
}

export function getAuditUserScope(user) {
  const raw = isObject(user) ? user : {}
  const id = String(raw.id || raw.user_id || "").trim()
  if (id) return `id:${id}`
  const username = String(raw.username || "").trim().toLowerCase()
  if (username) return `username:${username}`
  const email = String(raw.email || "").trim().toLowerCase()
  if (email) return `email:${email}`
  return "anonymous"
}

const getScopedKeys = (user) => {
  const scope = getAuditUserScope(user)
  return {
    scope,
    currentTaskKey: `${STORAGE_PREFIX}:${scope}:current`,
    taskIndexKey: `${STORAGE_PREFIX}:${scope}:index`,
    taskKeyPrefix: `${STORAGE_PREFIX}:${scope}:task:`,
  }
}

const makeTaskKey = (user, taskId) => `${getScopedKeys(user).taskKeyPrefix}${String(taskId || "").trim()}`

const readIndex = (storage, user) => {
  const parsed = safeParse(storage.getItem(getScopedKeys(user).taskIndexKey))
  return Array.isArray(parsed) ? parsed.filter(isObject) : []
}

const writeIndex = (storage, user, list) => {
  storage.setItem(getScopedKeys(user).taskIndexKey, JSON.stringify(list))
}

const removeTaskRecord = (storage, user, taskId) => {
  const key = String(taskId || "").trim()
  if (!key) return
  storage.removeItem(makeTaskKey(user, key))
}

const cleanupTaskIndex = (storage, user, options = {}) => {
  const now = Number(options.now || Date.now())
  const keepTaskId = toStringValue(options.keepTaskId).trim()
  const rawIndex = readIndex(storage, user)
  const valid = []
  rawIndex.forEach((item) => {
    const taskId = toStringValue(item.taskId).trim()
    if (!taskId) return
    const touchedAt = Date.parse(toStringValue(item.updatedAt || item.lastAccessedAt))
    const expired = Number.isFinite(touchedAt) && now - touchedAt > MAX_TASK_AGE_MS
    if (expired && taskId !== keepTaskId) {
      removeTaskRecord(storage, user, taskId)
      return
    }
    if (!storage.getItem(makeTaskKey(user, taskId))) return
    valid.push(item)
  })
  valid.sort((a, b) => Date.parse(toStringValue(b.updatedAt, "")) - Date.parse(toStringValue(a.updatedAt, "")))
  const trimmed = []
  valid.forEach((item) => {
    if (trimmed.length < MAX_TASKS || String(item.taskId || "").trim() === keepTaskId) {
      trimmed.push(item)
      return
    }
    removeTaskRecord(storage, user, item.taskId)
  })
  writeIndex(storage, user, trimmed.slice(0, MAX_TASKS))
  return trimmed.slice(0, MAX_TASKS)
}

const isQuotaExceeded = (error) => {
  const name = String(error?.name || "")
  const message = String(error?.message || "")
  return name === "QuotaExceededError" || /quota/i.test(name) || /quota|storage/i.test(message)
}

export function createAuditTaskId() {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID()
  }
  return `audit-${Date.now()}-${Math.random().toString(16).slice(2, 10)}`
}

export function getAuditFileMeta(file) {
  if (!file) return null
  return normalizeFileMeta({
    name: file.name,
    size: file.size,
    type: file.type,
    lastModified: file.lastModified,
  })
}

export function buildAuditSnapshot(input) {
  const touchedAt = nowIso()
  return normalizeSnapshot({
    ...toPlainObject(input),
    version: SNAPSHOT_VERSION,
    updatedAt: touchedAt,
    lastAccessedAt: touchedAt,
  })
}

function persistCurrentTaskPointer(storage, user, taskId) {
  storage.setItem(getScopedKeys(user).currentTaskKey, String(taskId || "").trim())
}

export function listAuditSnapshots(user, storage = window.localStorage) {
  try {
    const index = cleanupTaskIndex(storage, user)
    return index.map((item) => {
      const parsed = safeParse(storage.getItem(makeTaskKey(user, item.taskId)))
      const snapshot = normalizeSnapshot(parsed)
      return snapshot ? buildSnapshotSummary(snapshot) : null
    }).filter(Boolean)
  } catch {
    return []
  }
}

export function loadCurrentAuditSnapshot(user, storage = window.localStorage) {
  try {
    cleanupTaskIndex(storage, user)
    const currentTaskId = toStringValue(storage.getItem(getScopedKeys(user).currentTaskKey)).trim()
    if (!currentTaskId) return { snapshot: null, noticeCode: "" }
    const parsed = safeParse(storage.getItem(makeTaskKey(user, currentTaskId)))
    const snapshot = normalizeSnapshot(parsed)
    if (!snapshot) {
      removeTaskRecord(storage, user, currentTaskId)
      storage.removeItem(getScopedKeys(user).currentTaskKey)
      cleanupTaskIndex(storage, user)
      return { snapshot: null, noticeCode: "restore_invalid" }
    }
    persistCurrentTaskPointer(storage, user, snapshot.taskId)
    return { snapshot, noticeCode: "" }
  } catch {
    return { snapshot: null, noticeCode: "restore_invalid" }
  }
}

export function loadAuditSnapshotByTaskId(user, taskId, storage = window.localStorage) {
  try {
    const key = String(taskId || "").trim()
    if (!key) return { snapshot: null, noticeCode: "restore_invalid" }
    const parsed = safeParse(storage.getItem(makeTaskKey(user, key)))
    const snapshot = normalizeSnapshot(parsed)
    if (!snapshot) return { snapshot: null, noticeCode: "restore_invalid" }
    persistCurrentTaskPointer(storage, user, key)
    return { snapshot, noticeCode: "" }
  } catch {
    return { snapshot: null, noticeCode: "restore_invalid" }
  }
}

export function saveCurrentAuditSnapshot(snapshot, user, storage = window.localStorage) {
  const normalized = normalizeSnapshot({ ...toPlainObject(snapshot), userScope: getAuditUserScope(user) })
  if (!normalized) return { ok: false, noticeCode: "restore_invalid", prunedCount: 0 }
  try {
    cleanupTaskIndex(storage, user, { keepTaskId: normalized.taskId })
    const payload = JSON.stringify(normalized)
    const save = () => {
      storage.setItem(makeTaskKey(user, normalized.taskId), payload)
      persistCurrentTaskPointer(storage, user, normalized.taskId)
      const index = readIndex(storage, user).filter((item) => String(item?.taskId || "").trim() !== normalized.taskId)
      index.unshift(buildSnapshotSummary(normalized))
      index.sort((a, b) => Date.parse(toStringValue(b.updatedAt, "")) - Date.parse(toStringValue(a.updatedAt, "")))
      const keep = index.slice(0, MAX_TASKS)
      index.slice(MAX_TASKS).forEach((item) => {
        const staleTaskId = String(item?.taskId || "").trim()
        if (staleTaskId && staleTaskId !== normalized.taskId) removeTaskRecord(storage, user, staleTaskId)
      })
      writeIndex(storage, user, keep)
    }
    try {
      save()
      return { ok: true, noticeCode: "", prunedCount: 0 }
    } catch (error) {
      if (!isQuotaExceeded(error)) throw error
      const index = cleanupTaskIndex(storage, user, { keepTaskId: normalized.taskId })
      let prunedCount = 0
      for (let i = index.length - 1; i >= 0; i -= 1) {
        const taskId = String(index[i]?.taskId || "").trim()
        if (!taskId || taskId === normalized.taskId) continue
        removeTaskRecord(storage, user, taskId)
        prunedCount += 1
        try {
          save()
          return { ok: true, noticeCode: "storage_pruned", prunedCount }
        } catch (retryError) {
          if (!isQuotaExceeded(retryError)) throw retryError
        }
      }
      return { ok: false, noticeCode: "storage_full", prunedCount }
    }
  } catch {
    return { ok: false, noticeCode: "storage_full", prunedCount: 0 }
  }
}

export function clearCurrentAuditSnapshot(user, storage = window.localStorage) {
  try {
    const currentTaskId = toStringValue(storage.getItem(getScopedKeys(user).currentTaskKey)).trim()
    if (currentTaskId) {
      removeTaskRecord(storage, user, currentTaskId)
      const nextIndex = readIndex(storage, user).filter((item) => String(item?.taskId || "").trim() !== currentTaskId)
      writeIndex(storage, user, nextIndex)
    }
    storage.removeItem(getScopedKeys(user).currentTaskKey)
  } catch {
  }
}

export function clearCurrentAuditPointer(user, storage = window.localStorage) {
  try {
    storage.removeItem(getScopedKeys(user).currentTaskKey)
  } catch {
  }
}

export function getAuditSnapshotStorageKeys(taskId, user) {
  const scoped = getScopedKeys(user)
  return {
    scope: scoped.scope,
    currentTaskKey: scoped.currentTaskKey,
    taskIndexKey: scoped.taskIndexKey,
    taskKey: `${scoped.taskKeyPrefix}${String(taskId || "").trim()}`,
  }
}
