import { useState } from "react"
import { login, register } from "./api"

const EyeIcon = ({ visible, onClick, title }) => (
  <button
    type="button"
    className="pwd-toggle"
    onClick={onClick}
    title={title}
    aria-label={title}
    tabIndex={-1}
  >
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      {visible ? (
        <>
          <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
          <circle cx="12" cy="12" r="3" />
        </>
      ) : (
        <>
          <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94" />
          <path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19" />
          <path d="M14.12 14.12a3 3 0 1 1-4.24-4.24" />
          <line x1="1" y1="1" x2="23" y2="23" />
        </>
      )}
    </svg>
  </button>
)

export default function Login({ onLogin }) {
  const [isRegister, setIsRegister] = useState(false)
  const [form, setForm] = useState({ username: "", email: "", password: "" })
  const [error, setError] = useState("")
  const [success, setSuccess] = useState("")
  const [loading, setLoading] = useState(false)
  const [showPassword, setShowPassword] = useState(false)

  const handleSubmit = async (e) => {
    e.preventDefault()
    setError("")
    setSuccess("")
    setLoading(true)
    try {
      if (isRegister) {
        await register(form.username, form.email, form.password)
        setIsRegister(false)
        setSuccess("Registration successful! Please login.")
        setForm({ username: "", email: "", password: "" })
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
        {success && <div className="success">{success}</div>}
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
            <div className="pwd-wrapper">
              <input
                placeholder="Password"
                type={showPassword ? "text" : "password"}
                value={form.password}
                onChange={e => setForm({ ...form, password: e.target.value })}
                required
              />
              <EyeIcon
                visible={showPassword}
                onClick={() => setShowPassword(!showPassword)}
                title={showPassword ? "Hide password" : "Show password"}
              />
            </div>
          </div>
          <div className="row">
            <button type="submit" disabled={loading}>
              {loading ? "Loading..." : (isRegister ? "Register" : "Login")}
            </button>
          </div>
        </form>
        <p style={{ marginTop: 16, textAlign: "center" }}>
          {isRegister ? "Already have account? " : "Don't have account? "}
          <a href="#" onClick={e => { e.preventDefault(); setIsRegister(!isRegister); setForm({ username: "", email: "", password: "" }) }}>
            {isRegister ? "Login" : "Register"}
          </a>
        </p>
      </div>
    </div>
  )
}
