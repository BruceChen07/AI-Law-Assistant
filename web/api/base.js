export const API_BASE = import.meta.env.VITE_API_BASE || "http://127.0.0.1:8001"

// 长请求超时的下限，避免调用方传入过小的时间预算。
const API_REQUEST_TIMEOUT_MS = 8000
const API_LONG_REQUEST_TIMEOUT_MS = 15 * 60 * 1000
const API_CONTRACT_AUDIT_TIMEOUT_MS = 60 * 60 * 1000

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

export function getToken() {
  return localStorage.getItem("token")
}

export function getAuthHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export class ApiError extends Error {
  constructor(payload = {}) {
    const message = String(
      payload.user_message
      || payload.message
      || payload.title
      || "Request failed"
    )
    super(message)
    this.name = "ApiError"
    this.status = Number(payload.status || 0)
    this.code = String(payload.code || "")
    this.category = String(payload.category || "")
    this.title = String(payload.title || "")
    this.user_message = String(payload.user_message || "")
    this.operator_scenarios = Array.isArray(payload.operator_scenarios) ? payload.operator_scenarios : []
    this.operator_actions = Array.isArray(payload.operator_actions) ? payload.operator_actions : []
    this.retryable = typeof payload.retryable === "boolean" ? payload.retryable : true
    this.raw_reason = String(payload.raw_reason || "")
  }
}

function buildApiErrorPayload(status, text, parsed) {
  const detail = parsed?.detail
  if (detail && typeof detail === "object") {
    return {
      ...detail,
      status,
      message: String(detail.message || detail.user_message || text || "Request failed"),
      raw_reason: String(detail.raw_reason || text || detail.message || "")
    }
  }
  if (typeof detail === "string" && detail.trim()) {
    return {
      status,
      message: detail,
      raw_reason: detail
    }
  }
  if (typeof parsed?.message === "string" && parsed.message.trim()) {
    return {
      status,
      message: parsed.message,
      raw_reason: parsed.message
    }
  }
  return {
    status,
    message: String(text || "Request failed"),
    raw_reason: String(text || "")
  }
}

export async function ensureOk(res) {
  if (res.ok) return
  const text = await res.text()
  let parsed = null
  try {
    parsed = JSON.parse(text)
  } catch {
  }
  throw new ApiError(buildApiErrorPayload(res.status, text, parsed))
}

async function fetchWithTimeout(url, options = {}) {
  const { timeoutMs, ...fetchOptions } = options || {}
  const effectiveTimeoutMs = Number(timeoutMs)
  if (!Number.isFinite(effectiveTimeoutMs) || effectiveTimeoutMs <= 0) {
    return fetch(url, fetchOptions)
  }
  const controller = new AbortController()
  const upstreamSignal = fetchOptions.signal
  const forwardAbort = () => controller.abort()
  if (upstreamSignal) {
    if (upstreamSignal.aborted) controller.abort()
    else upstreamSignal.addEventListener("abort", forwardAbort, { once: true })
  }
  const timer = setTimeout(() => controller.abort(), effectiveTimeoutMs)
  try {
    return await fetch(url, { ...fetchOptions, signal: controller.signal })
  } finally {
    clearTimeout(timer)
    if (upstreamSignal) upstreamSignal.removeEventListener("abort", forwardAbort)
  }
}

export async function requestJson(url, options) {
  try {
    const res = await fetchWithTimeout(url, options)
    await ensureOk(res)
    return res.json()
  } catch (err) {
    if (err?.name === "AbortError") throw err
    if (err instanceof ApiError) throw err
    throw new ApiError({
      status: 0,
      code: "NETWORK_REQUEST_FAILED",
      category: "network",
      message: String(err?.message || err || "Failed to fetch"),
      raw_reason: String(err?.message || err || "Failed to fetch")
    })
  }
}

export async function requestBlob(url, options) {
  try {
    const res = await fetchWithTimeout(url, options)
    await ensureOk(res)
    return res.blob()
  } catch (err) {
    if (err?.name === "AbortError") throw err
    if (err instanceof ApiError) throw err
    throw new ApiError({
      status: 0,
      code: "NETWORK_REQUEST_FAILED",
      category: "network",
      message: String(err?.message || err || "Failed to fetch"),
      raw_reason: String(err?.message || err || "Failed to fetch")
    })
  }
}

function parseContentDispositionFilename(value) {
  const text = String(value || "").trim()
  if (!text) return ""
  const utf8Match = text.match(/filename\*=UTF-8''([^;]+)/i)
  if (utf8Match?.[1]) {
    try {
      return decodeURIComponent(String(utf8Match[1]).trim())
    } catch {
    }
  }
  const quotedMatch = text.match(/filename=\"([^\"]+)\"/i)
  if (quotedMatch?.[1]) return String(quotedMatch[1]).trim()
  const plainMatch = text.match(/filename=([^;]+)/i)
  return plainMatch?.[1] ? String(plainMatch[1]).trim() : ""
}

export async function requestBlobResponse(url, options) {
  try {
    const res = await fetchWithTimeout(url, options)
    await ensureOk(res)
    const blob = await res.blob()
    const contentDisposition = String(res.headers.get("content-disposition") || "")
    return {
      blob,
      filename: parseContentDispositionFilename(contentDisposition),
      contentType: String(res.headers.get("content-type") || blob.type || ""),
    }
  } catch (err) {
    if (err?.name === "AbortError") throw err
    if (err instanceof ApiError) throw err
    throw new ApiError({
      status: 0,
      code: "NETWORK_REQUEST_FAILED",
      category: "network",
      message: String(err?.message || err || "Failed to fetch"),
      raw_reason: String(err?.message || err || "Failed to fetch")
    })
  }
}
