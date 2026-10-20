import { API_BASE, getAuthHeaders, requestBlob, requestBlobResponse, requestJson } from "./base"

export async function auditContract(formData) {
  return requestJson(`${API_BASE}/contracts/audit`, {
    method: "POST",
    body: formData,
    headers: { ...getAuthHeaders() }
  })
}

export async function getAuditProgress(auditId, options = {}) {
  return requestJson(`${API_BASE}/contracts/audit/${auditId}/progress`, {
    headers: { ...getAuthHeaders() },
    signal: options.signal,
  })
}

export async function getUIConfig() {
  return requestJson(`${API_BASE}/contracts/ui-config`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function getPdfPipelineSummary() {
  return requestJson(`${API_BASE}/contracts/pdf-pipeline/summary`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function getContractSummary(documentId, language = "zh") {
  const query = new URLSearchParams({ language: String(language || "zh") }).toString()
  return requestJson(`${API_BASE}/contracts/${documentId}/summary?${query}`, {
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

export async function downloadContractSource(documentId, variant = "original") {
  const query = new URLSearchParams({ variant: String(variant || "original") }).toString()
  return requestBlob(`${API_BASE}/contracts/${documentId}/source?${query}`, {
    headers: { ...getAuthHeaders() }
  })
}

export async function exportContractReport(documentId, payload) {
  return requestBlobResponse(`${API_BASE}/contracts/${documentId}/report/export`, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...getAuthHeaders() },
    body: JSON.stringify(payload || {})
  })
}
