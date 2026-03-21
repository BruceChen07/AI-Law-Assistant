import { API_BASE, getAuthHeaders, requestJson } from "./base"

export async function importRegulation(formData) {
  return requestJson(`${API_BASE}/regulations/import`, {
    method: "POST",
    body: formData,
    headers: { ...getAuthHeaders() }
  })
}

export async function getJob(jobId) {
  return requestJson(`${API_BASE}/regulations/import/${jobId}`)
}

export async function searchRegulations(payload) {
  return requestJson(`${API_BASE}/regulations/search`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  })
}
