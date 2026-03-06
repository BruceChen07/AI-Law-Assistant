const API_BASE = import.meta.env.VITE_API_BASE || "http://localhost:8000"

export async function importRegulation(formData) {
  const res = await fetch(`${API_BASE}/regulations/import`, { method: "POST", body: formData })
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