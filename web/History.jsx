import { useCallback, useEffect, useMemo, useState } from "react"
import {
  downloadAuditArtifact,
  getAuditHistory,
  rebuildAuditArtifacts
} from "./api"
import { appI18n } from "./i18n/appI18n"

const STATUS_LABEL_KEY = {
  done: "historyStatusDone",
  failed: "historyStatusFailed",
  running: "historyStatusRunning",
  interrupted: "historyStatusInterrupted",
  unknown: "historyStatusUnknown"
}

const formatTime = (value) => {
  const raw = String(value || "").trim()
  if (!raw) return "-"
  const parsed = new Date(raw)
  if (Number.isNaN(parsed.getTime())) return raw
  return parsed.toLocaleString()
}

const formatSize = (bytes) => {
  const n = Number(bytes) || 0
  if (n < 1024) return `${n} B`
  if (n < 1024 * 1024) return `${Math.round((n / 1024) * 10) / 10} KB`
  return `${Math.round((n / (1024 * 1024)) * 10) / 10} MB`
}

const saveBlob = (blob, filename) => {
  const url = window.URL.createObjectURL(blob)
  const link = document.createElement("a")
  link.href = url
  link.download = filename || "audit-artifact"
  document.body.appendChild(link)
  link.click()
  link.remove()
  window.URL.revokeObjectURL(url)
}

export default function History({ lang, onOpenAudit, onBack }) {
  const t = appI18n[lang] || appI18n.zh
  const [items, setItems] = useState([])
  const [artifactRoot, setArtifactRoot] = useState("")
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState("")
  const [busyId, setBusyId] = useState("")
  const [statusFilter, setStatusFilter] = useState("")

  const load = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const data = await getAuditHistory({ limit: 60, status: statusFilter })
      setItems(Array.isArray(data?.items) ? data.items : [])
      setArtifactRoot(String(data?.artifact_root || ""))
    } catch (err) {
      setError(String(err?.message || err || t.historyLoadFailed))
      setItems([])
    } finally {
      setLoading(false)
    }
  }, [statusFilter, t.historyLoadFailed])

  useEffect(() => {
    load()
  }, [load])

  const statusLabel = (status) => {
    const key = STATUS_LABEL_KEY[String(status || "").toLowerCase()] || STATUS_LABEL_KEY.unknown
    return t[key] || status
  }

  const totals = useMemo(() => {
    const counts = { done: 0, failed: 0, running: 0 }
    items.forEach(item => {
      const status = String(item?.status || "").toLowerCase()
      if (status === "done") counts.done += 1
      else if (status === "failed") counts.failed += 1
      else counts.running += 1
    })
    return counts
  }, [items])

  const onDownload = async (item, name) => {
    if (!item?.audit_id) return
    setBusyId(String(item.audit_id))
    try {
      const result = await downloadAuditArtifact(item.audit_id, name)
      saveBlob(result?.blob || result, result?.filename || `${item.audit_id}-${name}`)
    } catch (err) {
      setError(String(err?.message || err || t.historyDownloadFailed))
    } finally {
      setBusyId("")
    }
  }

  const onRebuild = async (item) => {
    if (!item?.audit_id) return
    setBusyId(String(item.audit_id))
    try {
      await rebuildAuditArtifacts(item.audit_id)
      await load()
    } catch (err) {
      setError(String(err?.message || err || t.historyRebuildFailed))
    } finally {
      setBusyId("")
    }
  }

  return (
    <div className="page contract-shell">
      <header className="contract-header">
        <div className="contract-heading">
          <div className="contract-eyebrow">Audit Archive</div>
          <h1>{t.auditHistory}</h1>
          <p>{t.auditHistorySubtitle}</p>
        </div>
        <div className="contract-actions">
          <div className="user-pill">
            <button className="ghost" onClick={onBack}>{t.auditHistoryBack}</button>
          </div>
        </div>
      </header>
      <section className="card">
        <div className="panel-title">{t.auditHistoryList}</div>
        <div className="preview-meta">
          <span>{t.auditHistoryTotals}: {totals.done} / {totals.failed} / {totals.running}</span>
          <span>{t.auditHistoryArtifactRoot}: {artifactRoot || "-"}</span>
        </div>
        <div className="history-toolbar">
          <select value={statusFilter} onChange={e => setStatusFilter(e.target.value)}>
            <option value="">{t.historyFilterAll}</option>
            <option value="done">{t.historyStatusDone}</option>
            <option value="failed">{t.historyStatusFailed}</option>
            <option value="running">{t.historyStatusRunning}</option>
            <option value="interrupted">{t.historyStatusInterrupted}</option>
          </select>
          <button className="ghost" onClick={load} disabled={loading}>{t.historyRefresh}</button>
        </div>
        {loading && <div className="empty-state"><p>{t.historyLoading}</p></div>}
        {!loading && error && <div className="error">{error}</div>}
        {!loading && !error && items.length === 0 && (
          <div className="empty-state"><p>{t.historyEmpty}</p></div>
        )}
        {!loading && items.length > 0 && (
          <div className="history-list">
            {items.map(item => {
              const auditId = String(item?.audit_id || "")
              const summary = item?.risk_summary || {}
              const busy = busyId === auditId
              return (
                <div key={auditId} className="risk-item history-item">
                  <div className="history-item-head">
                    <strong>{item?.filename || item?.document_id || auditId}</strong>
                    <span className={`history-status history-status-${String(item?.status || "").toLowerCase()}`}>
                      {statusLabel(item?.status)}
                    </span>
                    <span>{formatTime(item?.created_at)}</span>
                    <span>
                      {t.historyRisk}: {summary.high ?? 0}/{summary.medium ?? 0}/{summary.low ?? 0}
                    </span>
                  </div>
                  <div className="audit-progress-subnote">
                    {auditId} · {item?.artifact_dir || "-"}
                    {item?.message ? ` · ${item.message}` : ""}
                  </div>
                  <div className="history-item-actions">
                    <button
                      className="primary"
                      disabled={!item?.has_result || busy}
                      onClick={() => onOpenAudit(auditId)}
                    >
                      {item?.has_result ? t.historyOpenResult : t.historyNoResult}
                    </button>
                    <button className="ghost" disabled={busy} onClick={() => onDownload(item, "report.md")}>
                      {t.historyDownloadMd}
                    </button>
                    <button className="ghost" disabled={busy} onClick={() => onDownload(item, "report.json")}>
                      {t.historyDownloadJson}
                    </button>
                    <button className="ghost" disabled={busy} onClick={() => onRebuild(item)}>
                      {t.historyRebuild}
                    </button>
                  </div>
                  {(item?.artifact_files || []).length > 0 && (
                    <div className="audit-progress-subnote">
                      {(item.artifact_files || [])
                        .map(file => `${file.name} (${formatSize(file.size)})`)
                        .join(" · ")}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>
    </div>
  )
}
