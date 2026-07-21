import { API_BASE, getAuthHeaders, getLongRequestTimeoutMs, requestBlob, requestJson } from "./base"

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

export async function adminListSkills(params = {}) {
  const query = new URLSearchParams(params).toString()
  const suffix = query ? `?${query}` : ""
  return requestJson(`${API_BASE}/api/admin/skills${suffix}`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function adminGetSkill(skillId) {
  return requestJson(`${API_BASE}/api/admin/skills/${encodeURIComponent(skillId)}`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function adminCreateSkill(payload) {
  return requestJson(`${API_BASE}/api/admin/skills`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(payload || {})
  })
}

export async function adminUpdateSkill(skillId, payload) {
  return requestJson(`${API_BASE}/api/admin/skills/${encodeURIComponent(skillId)}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(payload || {})
  })
}

export async function adminSetSkillStatus(skillId, status) {
  return requestJson(`${API_BASE}/api/admin/skills/${encodeURIComponent(skillId)}/status`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify({ status })
  })
}

export async function adminDeleteSkill(skillId) {
  return requestJson(`${API_BASE}/api/admin/skills/${encodeURIComponent(skillId)}`, {
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

export async function adminGetMemoryConfig() {
  return requestJson(`${API_BASE}/api/admin/memory-config`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function adminUpdateMemoryConfig(payload) {
  return requestJson(`${API_BASE}/api/admin/memory-config`, {
    method: "PUT",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(payload || {})
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

export async function adminTestLLM(payload, timeoutSec) {
  return requestJson(`${API_BASE}/api/admin/llm-test`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(payload || {}),
    timeoutMs: getLongRequestTimeoutMs(timeoutSec)
  })
}

export async function adminGetOllamaModels(params = {}) {
  const query = new URLSearchParams(params).toString()
  const suffix = query ? `?${query}` : ""
  return requestJson(`${API_BASE}/api/admin/ollama/models${suffix}`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function adminGetLlamaCppModels(params = {}) {
  const query = new URLSearchParams(params).toString()
  const suffix = query ? `?${query}` : ""
  return requestJson(`${API_BASE}/api/admin/llama-cpp/models${suffix}`, {
    headers: { ...getAuthHeaders() }
  })
}

// ---- LLM Trace 全链路日志接口 ----

export async function adminListLLMTraces(params = {}) {
  const query = new URLSearchParams(params).toString()
  const suffix = query ? `?${query}` : ""
  return requestJson(`${API_BASE}/api/admin/llm-traces${suffix}`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function adminGetLLMTraceDetail(spanId) {
  return requestJson(`${API_BASE}/api/admin/llm-traces/${spanId}`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function adminGetLLMTraceStats(params = {}) {
  const query = new URLSearchParams(params).toString()
  const suffix = query ? `?${query}` : ""
  return requestJson(`${API_BASE}/api/admin/llm-traces/stats/summary${suffix}`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function adminCleanupLLMTraces(beforeDate) {
  return requestJson(`${API_BASE}/api/admin/llm-traces?before_date=${encodeURIComponent(beforeDate)}`, {
    method: "DELETE",
    headers: { ...getAuthHeaders() }
  })
}
