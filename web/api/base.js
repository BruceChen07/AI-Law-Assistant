export const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000"

export function getToken() {
  return localStorage.getItem("token")
}

export function getAuthHeaders() {
  const token = getToken()
  return token ? { Authorization: `Bearer ${token}` } : {}
}

export async function ensureOk(res) {
  if (!res.ok) throw new Error(await res.text())
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
