import { API_BASE, getAuthHeaders, requestBlob, requestJson } from "./base"

export async function auditContract(formData) {
  return requestJson(`${API_BASE}/contracts/audit`, {
    method: "POST",
    body: formData,
    headers: { ...getAuthHeaders() }
  })
}

export async function getAuditProgress(auditId) {
  return requestJson(`${API_BASE}/contracts/audit/${auditId}/progress`, {
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
