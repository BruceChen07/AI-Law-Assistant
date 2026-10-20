import { useMemo } from "react"

const formatSnapshotTime = (value, lang) => {
  const ts = Date.parse(String(value || ""))
  if (!Number.isFinite(ts)) return "-"
  return new Intl.DateTimeFormat(lang === "en" ? "en-US" : "zh-CN", {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(ts))
}

const numberMetric = (value) => (Number.isFinite(Number(value)) ? Number(value) : 0)

const buildMetricRows = (selected, t) => {
  const first = selected[0]
  if (!first) return []
  const rows = [
    { key: "riskAll", label: t.snapshotMetricRiskAll, getValue: (item) => numberMetric(item.riskSummary?.all) },
    { key: "riskHigh", label: t.snapshotMetricRiskHigh, getValue: (item) => numberMetric(item.riskSummary?.high) },
    { key: "riskMedium", label: t.snapshotMetricRiskMedium, getValue: (item) => numberMetric(item.riskSummary?.medium) },
    { key: "riskLow", label: t.snapshotMetricRiskLow, getValue: (item) => numberMetric(item.riskSummary?.low) },
    { key: "pageCount", label: t.snapshotMetricPageCount, getValue: (item) => numberMetric(item.pageCount) },
    { key: "clauseCount", label: t.snapshotMetricClauseCount, getValue: (item) => numberMetric(item.clauseCount) },
    { key: "lineCount", label: t.snapshotMetricLineCount, getValue: (item) => numberMetric(item.lineCount) },
  ]
  return rows.map((row) => ({
    ...row,
    baseValue: row.getValue(first),
  }))
}

const extractRiskIssues = (summaryText) => {
  return String(summaryText || "")
    .split(/[;；\n]/)
    .map((item) => item.trim())
    .filter(Boolean)
    .slice(0, 4)
}

export default function AuditSnapshotHistoryPanel({
  t,
  uiLang,
  user,
  snapshots,
  currentTaskId,
  selectedTaskIds,
  onToggleSelect,
  onLoadSnapshot,
}) {
  const selectedSnapshots = useMemo(() => (
    snapshots.filter((item) => selectedTaskIds.includes(item.taskId))
  ), [snapshots, selectedTaskIds])

  const compareRows = useMemo(() => buildMetricRows(selectedSnapshots, t), [selectedSnapshots, t])
  const baseSnapshot = selectedSnapshots[0] || null
  const compareGridStyle = {
    gridTemplateColumns: `minmax(140px, 0.8fr) repeat(${Math.max(selectedSnapshots.length, 1)}, minmax(120px, 1fr))`,
  }

  return (
    <section className="card audit-history">
      <div className="audit-history-head">
        <div>
          <div className="panel-title">{t.snapshotHistoryTitle}</div>
          <h3>{t.snapshotHistorySubtitle}</h3>
          <p>{t.snapshotHistoryOwner.replace("{user}", String(user?.username || user?.email || "-"))}</p>
        </div>
        <div className="audit-history-stat">
          <strong>{snapshots.length}</strong>
          <span>{t.snapshotHistoryCount}</span>
        </div>
      </div>

      <div className="audit-history-grid">
        <div className="audit-history-list">
          {snapshots.length === 0 && <div className="empty-state snapshot-empty">{t.snapshotHistoryEmpty}</div>}
          {snapshots.map((snapshot) => {
            const selected = selectedTaskIds.includes(snapshot.taskId)
            const isCurrent = snapshot.taskId === currentTaskId
            return (
              <article key={snapshot.taskId} className={`snapshot-card${isCurrent ? " is-current" : ""}${selected ? " is-selected" : ""}`}>
                <div className="snapshot-card-top">
                  <label className="snapshot-compare-toggle">
                    <input
                      type="checkbox"
                      checked={selected}
                      onChange={() => onToggleSelect(snapshot.taskId)}
                    />
                    <span>{t.snapshotCompareToggle}</span>
                  </label>
                  <span className={`snapshot-status snapshot-status-${snapshot.status}`}>{t[`snapshotStatus${snapshot.status[0].toUpperCase()}${snapshot.status.slice(1)}`] || snapshot.status}</span>
                </div>
                <div className="snapshot-card-title">{snapshot.title}</div>
                <div className="snapshot-card-meta">
                  <span>{formatSnapshotTime(snapshot.updatedAt, uiLang)}</span>
                  <span>{snapshot.auditMode || "-"}</span>
                  <span>{snapshot.region || snapshot.industry || "-"}</span>
                </div>
                <div className="snapshot-card-metrics">
                  <span>{t.riskAll}: {snapshot.riskSummary?.all || 0}</span>
                  <span>{t.riskHigh}: {snapshot.riskSummary?.high || 0}</span>
                  <span>{t.riskMedium}: {snapshot.riskSummary?.medium || 0}</span>
                  <span>{t.riskLow}: {snapshot.riskSummary?.low || 0}</span>
                </div>
                <p className="snapshot-card-summary">{snapshot.summaryText || t.snapshotSummaryEmpty}</p>
                <div className="snapshot-card-bottom">
                  <div className="snapshot-card-tags">
                    <span>{t.pageMeta}: {snapshot.pageCount || 0}</span>
                    <span>{t.clauseCount}: {snapshot.clauseCount || 0}</span>
                    <span>{t.lineCount}: {snapshot.lineCount || 0}</span>
                  </div>
                  <button type="button" className="ghost" onClick={() => onLoadSnapshot(snapshot.taskId)}>
                    {t.snapshotLoadCurrent}
                  </button>
                </div>
              </article>
            )
          })}
        </div>

        <div className="snapshot-compare-panel">
          <div className="snapshot-compare-head">
            <div className="panel-title">{t.snapshotCompareTitle}</div>
            <span>{selectedSnapshots.length} / 3</span>
          </div>
          {selectedSnapshots.length < 2 ? (
            <div className="empty-state snapshot-empty">{t.snapshotCompareEmpty}</div>
          ) : (
            <>
              <div className="snapshot-compare-cards">
                {selectedSnapshots.map((snapshot, index) => (
                  <article key={snapshot.taskId} className={`snapshot-compare-card${index === 0 ? " is-base" : ""}`}>
                    <div className="snapshot-compare-card-top">
                      <strong>{snapshot.title}</strong>
                      <span>{index === 0 ? t.snapshotCompareBase : t.snapshotCompareTarget}</span>
                    </div>
                    <div className="snapshot-compare-card-meta">
                      <span>{formatSnapshotTime(snapshot.updatedAt, uiLang)}</span>
                      <span>{t.riskAll}: {snapshot.riskSummary?.all || 0}</span>
                      <span>{t.pageMeta}: {snapshot.pageCount || 0}</span>
                    </div>
                    <p>{snapshot.summaryText || t.snapshotSummaryEmpty}</p>
                  </article>
                ))}
              </div>

              <div className="snapshot-compare-table">
                <div className="snapshot-compare-row snapshot-compare-header" style={compareGridStyle}>
                  <span>{t.snapshotMetricLabel}</span>
                  {selectedSnapshots.map((snapshot) => (
                    <span key={`header-${snapshot.taskId}`}>{snapshot.title}</span>
                  ))}
                </div>
                {compareRows.map((row) => (
                  <div key={row.key} className="snapshot-compare-row" style={compareGridStyle}>
                    <span>{row.label}</span>
                    {selectedSnapshots.map((snapshot, index) => {
                      const value = row.getValue(snapshot)
                      const delta = value - row.baseValue
                      return (
                        <span key={`${row.key}-${snapshot.taskId}`} className={index === 0 ? "" : (delta === 0 ? "is-neutral" : delta > 0 ? "is-up" : "is-down")}>
                          {value}
                          {index > 0 && ` (${delta > 0 ? "+" : ""}${delta})`}
                        </span>
                      )
                    })}
                  </div>
                ))}
              </div>

              <div className="snapshot-diff-grid">
                {selectedSnapshots.slice(1).map((snapshot) => {
                  const baseIssues = new Set(extractRiskIssues(baseSnapshot?.summaryText))
                  const targetIssues = new Set(extractRiskIssues(snapshot.summaryText))
                  const added = [...targetIssues].filter((item) => !baseIssues.has(item))
                  const removed = [...baseIssues].filter((item) => !targetIssues.has(item))
                  return (
                    <article key={`diff-${snapshot.taskId}`} className="snapshot-diff-card">
                      <div className="snapshot-diff-head">
                        <strong>{snapshot.title}</strong>
                        <span>{t.snapshotCompareVsBase}</span>
                      </div>
                      <div className="snapshot-diff-column">
                        <label>{t.snapshotDiffAdded}</label>
                        <div className="snapshot-diff-tags">
                          {added.length > 0 ? added.map((item) => <span key={`added-${snapshot.taskId}-${item}`}>{item}</span>) : <em>{t.snapshotDiffNone}</em>}
                        </div>
                      </div>
                      <div className="snapshot-diff-column">
                        <label>{t.snapshotDiffRemoved}</label>
                        <div className="snapshot-diff-tags">
                          {removed.length > 0 ? removed.map((item) => <span key={`removed-${snapshot.taskId}-${item}`}>{item}</span>) : <em>{t.snapshotDiffNone}</em>}
                        </div>
                      </div>
                    </article>
                  )
                })}
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  )
}
