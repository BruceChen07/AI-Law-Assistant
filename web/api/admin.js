import { API_BASE, getAuthHeaders, requestBlob, requestJson } from "./base"

export async function adminListDocuments(params = {}) {
  const query = new URLSearchParams(params).toString()
  return requestJson(`${API_BASE}/api/admin/documents?${query}`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function adminDeleteDocument(docId) {
  return requestJson(`${API_BASE}/api/admin/documents/${docId}`, {
    method: "DELETE",
    headers: { ...getAuthHeaders() }
  })
}

export async function adminListUsers() {
  return requestJson(`${API_BASE}/api/admin/users`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function adminUpdateUserRole(userId, role) {
  return requestJson(`${API_BASE}/api/admin/users/${userId}/role?role=${role}`, {
    method: "PUT",
    headers: { ...getAuthHeaders() }
  })
}

export async function adminDeleteUser(userId) {
  return requestJson(`${API_BASE}/api/admin/users/${userId}`, {
    method: "DELETE",
    headers: { ...getAuthHeaders() }
  })
}

export async function adminGetStats() {
  return requestJson(`${API_BASE}/api/admin/stats`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function adminGetLLMConfig() {
  return requestJson(`${API_BASE}/api/admin/llm-config`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function adminUpdateLLMConfig(payload) {
  return requestJson(`${API_BASE}/api/admin/llm-config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(payload)
  })
}

export async function adminDeleteLLMApiKey() {
  return requestJson(`${API_BASE}/api/admin/llm-config/api-key`, {
    method: "DELETE",
    headers: { ...getAuthHeaders() }
  })
}

export async function adminGetUIConfig() {
  return requestJson(`${API_BASE}/api/admin/ui-config`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function adminUpdateUIConfig(payload) {
  return requestJson(`${API_BASE}/api/admin/ui-config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(payload)
  })
}

export async function adminGetVectorStoreConfig() {
  return requestJson(`${API_BASE}/api/admin/vector-store/config`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function adminUpdateVectorStoreConfig(payload) {
  return requestJson(`${API_BASE}/api/admin/vector-store/config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(payload)
  })
}

export async function adminCleanupVectorStore() {
  return requestJson(`${API_BASE}/api/admin/vector-store/cleanup`, {
    method: "POST",
    headers: { ...getAuthHeaders() }
  })
}

export async function adminGetTokenStats(params = {}) {
  const query = new URLSearchParams(params).toString()
  return requestJson(`${API_BASE}/api/admin/token-usage?${query}`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function adminExportTokenStats(params = {}) {
  const query = new URLSearchParams(params).toString()
  return requestBlob(`${API_BASE}/api/admin/token-usage/csv?${query}`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function adminTestLLM(payload) {
  return requestJson(`${API_BASE}/api/admin/llm-test`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(payload || {})
  })
}
