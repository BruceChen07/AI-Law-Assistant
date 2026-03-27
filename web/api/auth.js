import { API_BASE, getAuthHeaders, requestJson } from "./base"

export async function login(username, password) {
  const data = await requestJson(`${API_BASE}/api/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  })
  localStorage.setItem("token", data.access_token)
  localStorage.setItem("user", JSON.stringify(data.user))
  return data
}

export async function register(username, email, password) {
  return requestJson(`${API_BASE}/api/auth/register`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, email, password })
  })
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
  return requestJson(`${API_BASE}/api/auth/me`, {
    headers: { ...getAuthHeaders() }
  })
}
