import { API_BASE, getAuthHeaders, getLongRequestTimeoutMs, isTransportError, requestBlob, requestJson } from "./base"

export { isTransportError }

export async function auditContract(formData) {
  // Submission only registers a background task, so this stays a short request.
  return requestJson(`${API_BASE}/contracts/audit`, {
    method: "POST",
    body: formData,
    headers: { ...getAuthHeaders() },
    timeoutMs: getLongRequestTimeoutMs(120)
  })
}

export async function getAuditHistory({ limit = 30, offset = 0, status = "" } = {}) {
  const query = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
    status: String(status || "")
  }).toString()
  return requestJson(`${API_BASE}/contracts/audits/history?${query}`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function getAuditArtifacts(auditId) {
  return requestJson(`${API_BASE}/contracts/audits/${auditId}/artifacts`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function rebuildAuditArtifacts(auditId) {
  return requestJson(`${API_BASE}/contracts/audits/${auditId}/artifacts/rebuild`, {
    method: "POST",
    headers: { ...getAuthHeaders() }
  })
}

export async function downloadAuditArtifact(auditId, name) {
  return requestBlob(`${API_BASE}/contracts/audits/${auditId}/artifacts/${name}`, {
    headers: { ...getAuthHeaders() },
    returnResponseMeta: true
  })
}

export async function getRunningAudits(limit = 20) {
  const query = new URLSearchParams({ limit: String(limit) }).toString()
  return requestJson(`${API_BASE}/contracts/audits/running?${query}`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function getAuditProgress(auditId) {
  return requestJson(`${API_BASE}/contracts/audit/${auditId}/progress`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function getAuditTask(auditId) {
  return requestJson(`${API_BASE}/contracts/audit/${auditId}/task`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function getUIConfig() {
  return requestJson(`${API_BASE}/contracts/ui-config`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function getContractPreview(documentId, clauseLimit = 2000) {
  const query = new URLSearchParams({ clause_limit: String(clauseLimit) }).toString()
  return requestJson(`${API_BASE}/contracts/${documentId}/preview?${query}`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function getContractPreviewManifest(documentId) {
  return requestJson(`${API_BASE}/contracts/${documentId}/preview-manifest`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function getContractPreviewPageImage(documentId, pageNo) {
  return requestBlob(`${API_BASE}/contracts/${documentId}/preview/pages/${pageNo}/image`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function exportContractReport(documentId, payload) {
  return requestBlob(`${API_BASE}/contracts/${documentId}/report/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(payload || {}),
    returnResponseMeta: true
  })
}

export async function getContractSummary(documentId, language = "zh") {
  // 摘要可能要跑一轮 LLM（CPU 上 1~2 分钟），用长超时。
  return requestJson(
    `${API_BASE}/contracts/${documentId}/summary?language=${encodeURIComponent(language)}`,
    {
      headers: { ...getAuthHeaders() },
      timeoutMs: getLongRequestTimeoutMs(300)
    }
  )
}

export async function getAuditQueue() {
  return requestJson(`${API_BASE}/contracts/audit/queue`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function cancelAuditQueueItem(auditId) {
  return requestJson(`${API_BASE}/contracts/audit/queue/${auditId}`, {
    method: "DELETE",
    headers: { ...getAuthHeaders() }
  })
}
