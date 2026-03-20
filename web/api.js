const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000"

function getToken() {
  return localStorage.getItem("token")
}

function getAuthHeaders() {
  const token = getToken()
  return token ? { "Authorization": `Bearer ${token}` } : {}
}

export async function login(username, password) {
  const res = await fetch(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  })
  if (!res.ok) throw new Error(await res.text())
  const data = await res.json()
  localStorage.setItem("token", data.access_token)
  localStorage.setItem("user", JSON.stringify(data.user))
  return data
}

export async function register(username, email, password) {
  const res = await fetch(`${API_BASE}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, email, password })
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export function logout() {
  localStorage.removeItem("token")
  localStorage.removeItem("user")
}

export function getCurrentUser() {
  const userStr = localStorage.getItem("user")
  return userStr ? JSON.parse(userStr) : null
}

export function isAdmin() {
  const user = getCurrentUser()
  return user && user.role === "admin"
}

export async function getMe() {
  const res = await fetch(`${API_BASE}/api/auth/me`, {
    headers: { ...getAuthHeaders() }
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function adminListDocuments(params = {}) {
  const query = new URLSearchParams(params).toString()
  const res = await fetch(`${API_BASE}/api/admin/documents?${query}`, {
    headers: { ...getAuthHeaders() }
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function adminDeleteDocument(docId) {
  const res = await fetch(`${API_BASE}/api/admin/documents/${docId}`, {
    method: "DELETE",
    headers: { ...getAuthHeaders() }
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function adminListUsers() {
  const res = await fetch(`${API_BASE}/api/admin/users`, {
    headers: { ...getAuthHeaders() }
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function adminUpdateUserRole(userId, role) {
  const res = await fetch(`${API_BASE}/api/admin/users/${userId}/role?role=${role}`, {
    method: "PUT",
    headers: { ...getAuthHeaders() }
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function adminGetStats() {
  const res = await fetch(`${API_BASE}/api/admin/stats`, {
    headers: { ...getAuthHeaders() }
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function adminGetLLMConfig() {
  const res = await fetch(`${API_BASE}/api/admin/llm-config`, {
    headers: { ...getAuthHeaders() }
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function adminUpdateLLMConfig(payload) {
  const res = await fetch(`${API_BASE}/api/admin/llm-config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(payload)
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function adminGetUIConfig() {
  const res = await fetch(`${API_BASE}/api/admin/ui-config`, {
    headers: { ...getAuthHeaders() }
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function adminUpdateUIConfig(payload) {
  const res = await fetch(`${API_BASE}/api/admin/ui-config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(payload)
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function adminGetTokenStats(params = {}) {
  const query = new URLSearchParams(params).toString()
  const res = await fetch(`${API_BASE}/api/admin/token-usage?${query}`, {
    headers: { ...getAuthHeaders() }
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function adminExportTokenStats(params = {}) {
  const query = new URLSearchParams(params).toString()
  const res = await fetch(`${API_BASE}/api/admin/token-usage/csv?${query}`, {
    headers: { ...getAuthHeaders() }
  })
  if (!res.ok) throw new Error(await res.text())
  return res.blob()
}

export async function adminTestLLM(payload) {
  const res = await fetch(`${API_BASE}/api/admin/llm-test`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(payload || {})
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function importRegulation(formData) {
  const headers = { ...getAuthHeaders() }
  const res = await fetch(`${API_BASE}/regulations/import`, { method: "POST", body: formData, headers })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getJob(jobId) {
  const res = await fetch(`${API_BASE}/regulations/import/${jobId}`)
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function searchRegulations(payload) {
  const res = await fetch(`${API_BASE}/regulations/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function auditContract(formData) {
  const headers = { ...getAuthHeaders() }
  const res = await fetch(`${API_BASE}/contracts/audit`, { method: "POST", body: formData, headers })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getAuditProgress(auditId) {
  const headers = { ...getAuthHeaders() }
  const res = await fetch(`${API_BASE}/contracts/audit/${auditId}/progress`, { headers })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getUIConfig() {
  const headers = { ...getAuthHeaders() }
  const res = await fetch(`${API_BASE}/contracts/ui-config`, { headers })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getContractPreview(documentId, clauseLimit = 2000) {
  const headers = { ...getAuthHeaders() }
  const query = new URLSearchParams({ clause_limit: String(clauseLimit) }).toString()
  const res = await fetch(`${API_BASE}/contracts/${documentId}/preview?${query}`, { headers })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getContractPreviewManifest(documentId) {
  const headers = { ...getAuthHeaders() }
  const res = await fetch(`${API_BASE}/contracts/${documentId}/preview-manifest`, { headers })
  if (!res.ok) throw new Error(await res.text())
  return res.json()
}

export async function getContractPreviewPageImage(documentId, pageNo) {
  const headers = { ...getAuthHeaders() }
  const res = await fetch(`${API_BASE}/contracts/${documentId}/preview/pages/${pageNo}/image`, { headers })
  if (!res.ok) throw new Error(await res.text())
  return res.blob()
}
