export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000"

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
  const res = await fetch(url, options)
  await ensureOk(res)
  return res.json()
}

export async function requestBlob(url, options) {
  const res = await fetch(url, options)
  await ensureOk(res)
  return res.blob()
}
