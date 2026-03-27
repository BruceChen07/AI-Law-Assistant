import { useState } from "react"
import { login, register } from "./api"

export default function Login({ onLogin }) {
  const [isRegister, setIsRegister] = useState(false)
  const [form, setForm] = useState({ username: "", email: "", password: "" })
  const [error, setError] = useState("")
  const [loading, setLoading] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError("")
    setLoading(true)
    try {
      if (isRegister) {
        await register(form.username, form.email, form.password)
        setIsRegister(false)
        setError("Registration successful! Please login.")
      } else {
        await login(form.username, form.password)
        onLogin()
      }
    } catch (err) {
      setError(err.message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="page">
      <div className="card" style={{ maxWidth: 400, margin: "100px auto" }}>
        <h2>{isRegister ? "Register" : "Login"}</h2>
        {error && <div className="error">{error}</div>}
        <form onSubmit={handleSubmit}>
          <div className="grid">
            <input
              placeholder="Username"
              value={form.username}
              onChange={e => setForm({ ...form, username: e.target.value })}
              required
            />
            {isRegister && (
              <input
                placeholder="Email"
                type="email"
                value={form.email}
                onChange={e => setForm({ ...form, email: e.target.value })}
                required
              />
            )}
            <input
              placeholder="Password"
              type="password"
              value={form.password}
              onChange={e => setForm({ ...form, password: e.target.value })}
              required
            />
          </div>
          <div className="row">
            <button type="submit" disabled={loading}>
              {loading ? "Loading..." : (isRegister ? "Register" : "Login")}
            </button>
          </div>
        </form>
        <p style={{ marginTop: 16, textAlign: "center" }}>
          {isRegister ? "Already have account? " : "Don't have account? "}
          <a href="#" onClick={e => { e.preventDefault(); setIsRegister(!isRegister) }}>
            {isRegister ? "Login" : "Register"}
          </a>
        </p>
      </div>
    </div>
  )
}
