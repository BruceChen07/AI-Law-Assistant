export let API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000"
const API_REQUEST_TIMEOUT_MS = 8000
const API_LONG_REQUEST_TIMEOUT_MS = 15 * 60 * 1000
const API_CONTRACT_AUDIT_TIMEOUT_MS = 60 * 60 * 1000

function dedupe(items) {
  return [...new Set(items.filter(Boolean))]
}

function isAbsoluteUrl(value) {
  return /^https?:\/\//i.test(String(value || ""))
}

function toPath(url) {
  const raw = String(url || "")
  if (!isAbsoluteUrl(raw)) return raw
  try {
    const parsed = new URL(raw)
    return `${parsed.pathname}${parsed.search}${parsed.hash}`
  } catch {
    return raw
  }
}

function buildCandidateBases() {
  const configured = String(import.meta.env.VITE_API_BASE || "").trim()
  const currentOrigin = typeof window !== "undefined" ? window.location.origin : ""
  const currentPort = typeof window !== "undefined" ? window.location.port : ""
  return dedupe([
    configured,
    currentPort && currentPort !== "5173" ? currentOrigin : "",
    API_BASE,
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:8001",
    "http://127.0.0.1:8001"
  ])
}

function buildCandidateUrls(url) {
  const raw = String(url || "")
  if (!raw) return []
  const path = toPath(raw)
  const bases = buildCandidateBases()
  if (!path.startsWith("/")) {
    return dedupe([raw])
  }
  return dedupe(bases.map(base => `${String(base).replace(/\/$/, "")}${path}`))
}

async function fetchWithTimeout(url, options = {}) {
  const { timeoutMs, ...fetchOptions } = options || {}
  const controller = new AbortController()
  const effectiveTimeoutMs = Number(timeoutMs) > 0 ? Number(timeoutMs) : API_REQUEST_TIMEOUT_MS
  const timer = setTimeout(() => controller.abort(), effectiveTimeoutMs)
  try {
    return await fetch(url, { ...fetchOptions, signal: controller.signal })
  } catch (err) {
    if (err?.name === "AbortError") {
      const timeoutError = new Error(`request timeout after ${Math.ceil(effectiveTimeoutMs / 1000)}s`)
      timeoutError.name = "RequestTimeoutError"
      timeoutError.cause = err
      throw timeoutError
    }
    throw err
  } finally {
    clearTimeout(timer)
  }
}

async function requestWithFallback(url, options = {}) {
  const attemptUrls = buildCandidateUrls(url)
  let lastError = null
  for (const attemptUrl of attemptUrls) {
    try {
      const res = await fetchWithTimeout(attemptUrl, options)
      const ok = res.ok
      if (ok) {
        try {
          const parsed = new URL(attemptUrl)
          API_BASE = parsed.origin
        } catch {}
      }
      return res
    } catch (err) {
      if (err?.name === "RequestTimeoutError") {
        throw err
      }
      lastError = err
    }
  }
  throw lastError || new Error("request failed")
}

export function getToken() {
  return localStorage.getItem("token")
}

export function getAuthHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export async function ensureOk(res) {
  if (res.ok) return
  const text = await res.text()
  let message = text
  try {
    const parsed = JSON.parse(text)
    const detail = parsed?.detail
    if (typeof detail === "string" && detail.trim()) {
      message = detail
    } else if (detail && typeof detail === "object") {
      message = String(detail.message || detail.user_message || text)
    } else if (typeof parsed?.message === "string" && parsed.message.trim()) {
      message = parsed.message
    }
  } catch {
    message = text
  }
  throw new Error(message)
}

export async function requestJson(url, options) {
  const res = await requestWithFallback(url, options)
  await ensureOk(res)
  return res.json()
}

export async function requestBlob(url, options) {
  const res = await requestWithFallback(url, options)
  await ensureOk(res)
  return res.blob()
}

export function getLongRequestTimeoutMs(timeoutSec) {
  const seconds = Number(timeoutSec)
  if (Number.isFinite(seconds) && seconds > 0) {
    return Math.max(API_REQUEST_TIMEOUT_MS, seconds * 1000 + 5000)
  }
  return API_LONG_REQUEST_TIMEOUT_MS
}

export function getContractAuditTimeoutMs() {
  return API_CONTRACT_AUDIT_TIMEOUT_MS
}
