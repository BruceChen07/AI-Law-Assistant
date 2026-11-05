import React from "react"

/**
 * Last line of defence: a render-time exception must never leave the user with a
 * blank page (that is what happened when a stale login made the app crash on boot).
 */
export default class ErrorBoundary extends React.Component {
  constructor(props) {
    super(props)
    this.state = { error: null }
  }

  static getDerivedStateFromError(error) {
    return { error }
  }

  componentDidCatch(error, info) {
    // eslint-disable-next-line no-console
    console.error("app_render_failed", error, info)
  }

  onResetSession = () => {
    try {
      localStorage.removeItem("token")
      localStorage.removeItem("user")
    } catch {
      // ignore storage errors
    }
    window.location.hash = "#main"
    window.location.reload()
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    const message = String(error?.message || error || "Unknown error")
    return (
      <div className="page contract-shell">
        <section className="card">
          <div className="panel-title">页面渲染失败 / Page failed to render</div>
          <div className="error">{message}</div>
          <div className="audit-progress-subnote">
            这通常是因为登录状态已失效或前端缓存过期。可以清空本地登录信息后重试。
          </div>
          <div className="history-item-actions">
            <button className="primary" onClick={() => window.location.reload()}>
              刷新页面 / Reload
            </button>
            <button className="ghost" onClick={this.onResetSession}>
              清空登录信息并重试 / Reset session
            </button>
          </div>
        </section>
      </div>
    )
  }
}
