import { useEffect, useMemo, useState } from "react"
import { adminListLLMTraces, adminGetLLMTraceDetail, adminGetLLMTraceStats, adminCleanupLLMTraces } from "./api"

const STATUS_LABELS = { success: "成功", failed: "失败", pending: "进行中", thinking: "推理中" }
const STATUS_COLORS = { success: "#22c55e", failed: "#ef4444", pending: "#f59e0b", thinking: "#3b82f6" }
const STORAGE_KEY_FILTER = "llm_trace_filter"

function formatMs(ms) {
  if (!ms || ms <= 0) return "-"
  if (ms < 1000) return `${ms}ms`
  if (ms < 60000) return `${(ms / 1000).toFixed(1)}s`
  return `${(ms / 60000).toFixed(1)}min`
}

function formatDate(iso) {
  if (!iso) return "-"
  try {
    const d = new Date(iso)
    return d.toLocaleString("zh-CN", {
      month: "2-digit", day: "2-digit",
      hour: "2-digit", minute: "2-digit", second: "2-digit"
    })
  } catch { return iso }
}

function dedupeTags(list) {
  return [...new Set(list.filter(Boolean))]
}

export default function LlmTraceViewer({ lang }) {
  const t = lang === "en" ? {
    title: "LLM Trace Viewer",
    modelLabel: "Model",
    statusLabel: "Status",
    stageLabel: "Stage",
    auditLabel: "Audit ID",
    dateFrom: "From",
    dateTo: "To",
    search: "Search",
    reset: "Reset",
    detailTitle: "Trace Detail",
    close: "Close",
    noData: "No trace records yet. Run a contract audit to populate data.",
    loading: "Loading...",
    stats: "Statistics",
    cleanup: "Cleanup old logs",
    cleanupHint: "Delete records before date",
    totalRecords: "Total",
    successRate: "Success Rate",
    avgLatency: "Avg Latency",
    modelStats: "By Model",
    dailyStats: "Daily",
    requestContent: "Request Content",
    responseContent: "Response Content",
    thinkingContent: "Thinking Process",
    traceEvents: "Events",
    spanInfo: "Span Info",
    retry: "Retry",
    yes: "Yes",
    no: "No",
  } : {
    title: "LLM 全链路日志",
    modelLabel: "模型",
    statusLabel: "状态",
    stageLabel: "阶段",
    auditLabel: "审计ID",
    dateFrom: "开始日期",
    dateTo: "结束日期",
    search: "查询",
    reset: "重置",
    detailTitle: "追踪详情",
    close: "关闭",
    noData: "暂无追踪记录。执行一次合同审计即可采集数据。",
    loading: "加载中...",
    stats: "统计面板",
    cleanup: "清理旧日志",
    cleanupHint: "删除此日期之前的记录",
    totalRecords: "总记录",
    successRate: "成功率",
    avgLatency: "平均耗时",
    modelStats: "按模型统计",
    dailyStats: "每日统计",
    requestContent: "请求内容",
    responseContent: "响应内容",
    thinkingContent: "思考过程",
    traceEvents: "事件列表",
    spanInfo: "Span 信息",
    retry: "重试",
    yes: "是",
    no: "否",
  }

  const [traces, setTraces] = useState([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(1)
  const [loading, setLoading] = useState(false)
  const [detail, setDetail] = useState(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const [stats, setStats] = useState(null)
  const [showStats, setShowStats] = useState(false)
  const PAGE_SIZE = 30

  const initialFilters = useMemo(() => {
    try {
      const saved = JSON.parse(localStorage.getItem(STORAGE_KEY_FILTER) || "null")
      if (saved && typeof saved === "object") return saved
    } catch { }
    return { model_name: "", status: "", audit_id: "", date_from: "", date_to: "", stage: "" }
  }, [])

  const [filters, setFilters] = useState(initialFilters)
  const [cleanupDate, setCleanupDate] = useState("")

  useEffect(() => {
    localStorage.setItem(STORAGE_KEY_FILTER, JSON.stringify(filters))
  }, [filters])

  const loadTraces = async (p = 1) => {
    setLoading(true)
    try {
      const params = { page: p, page_size: PAGE_SIZE }
      for (const [k, v] of Object.entries(filters)) {
        if (v) params[k] = v
      }
      const res = await adminListLLMTraces(params)
      setTraces(res.items || [])
      setTotal(res.total || 0)
      setPage(p)
    } catch (err) {
      console.error("load traces failed", err)
      setTraces([])
      setTotal(0)
    } finally {
      setLoading(false)
    }
  }

  const loadStats = async () => {
    try {
      const params = {}
      if (filters.date_from) params.date_from = filters.date_from
      if (filters.date_to) params.date_to = filters.date_to
      const res = await adminGetLLMTraceStats(params)
      setStats(res)
      setShowStats(true)
    } catch (err) {
      console.error("load stats failed", err)
    }
  }

  const openDetail = async (spanId) => {
    setDetailLoading(true)
    setDetail(null)
    try {
      const res = await adminGetLLMTraceDetail(spanId)
      setDetail(res)
    } catch (err) {
      console.error("load detail failed", err)
    } finally {
      setDetailLoading(false)
    }
  }

  const handleSearch = () => { loadTraces(1) }

  const handleReset = () => {
    setFilters({ model_name: "", status: "", audit_id: "", date_from: "", date_to: "", stage: "" })
  }

  const stages = useMemo(() => dedupeTags(traces.map(r => r.stage)), [traces])
  const models = useMemo(() => dedupeTags(traces.map(r => r.model_name)), [traces])

  return (
    <div className="llm-trace-viewer">
      <h2>{t.title}</h2>

      {/* ---- 筛选栏 ---- */}
      <div className="trace-filters">
        <div className="trace-filter-row">
          <label>{t.modelLabel}: <input value={filters.model_name}
            onChange={e => setFilters(f => ({ ...f, model_name: e.target.value }))}
            placeholder="e.g. gemma4:12b" />
          </label>
          <label>{t.statusLabel}: <select value={filters.status}
            onChange={e => setFilters(f => ({ ...f, status: e.target.value }))}>
            <option value="">全部</option>
            <option value="success">成功</option>
            <option value="failed">失败</option>
            <option value="pending">进行中</option>
          </select>
          </label>
          <label>{t.stageLabel}: <input value={filters.stage}
            onChange={e => setFilters(f => ({ ...f, stage: e.target.value }))}
            placeholder="e.g. llm_call" />
          </label>
          <label>{t.auditLabel}: <input value={filters.audit_id}
            onChange={e => setFilters(f => ({ ...f, audit_id: e.target.value }))}
            placeholder="audit_..." />
          </label>
        </div>
        <div className="trace-filter-row">
          <label>{t.dateFrom}: <input type="date" value={filters.date_from}
            onChange={e => setFilters(f => ({ ...f, date_from: e.target.value }))} />
          </label>
          <label>{t.dateTo}: <input type="date" value={filters.date_to}
            onChange={e => setFilters(f => ({ ...f, date_to: e.target.value }))} />
          </label>
          <button onClick={handleSearch}>{t.search}</button>
          <button onClick={handleReset} className="btn-secondary">{t.reset}</button>
          <button onClick={loadStats} className="btn-secondary">{t.stats}</button>
        </div>
      </div>

      {/* ---- 统计面板 ---- */}
      {showStats && stats && (
        <div className="trace-stats-panel">
          <h3>{t.stats} <button onClick={() => setShowStats(false)}
            className="btn-close-small">x</button></h3>
          <div className="trace-stats-grid">
            <div className="trace-stat-card">
              <strong>{t.totalRecords}</strong>
              <span>{stats.by_model?.reduce((s, m) => s + (m.total_calls || 0), 0) || 0}</span>
            </div>
            <div className="trace-stat-card">
              <strong>{t.avgLatency}</strong>
              <span>{formatMs(stats.by_model?.[0]?.avg_latency_ms)}</span>
            </div>
          </div>
          {!!stats.by_model?.length && (
            <div className="trace-model-stats">
              <h4>{t.modelStats}</h4>
              <table>
                <thead>
                  <tr>
                    <th>{t.modelLabel}</th><th>{t.totalRecords}</th><th>成功</th><th>失败</th>
                    <th>{t.avgLatency}</th><th>Avg Tokens</th><th>Ollama Load</th><th>Ollama Eval</th>
                  </tr>
                </thead>
                <tbody>
                  {stats.by_model.map(m => (
                    <tr key={m.model_name}>
                      <td><code>{m.model_name}</code></td>
                      <td>{m.total_calls}</td>
                      <td className="text-success">{m.success_count}</td>
                      <td className="text-danger">{m.failed_count}</td>
                      <td>{formatMs(m.avg_latency_ms)}</td>
                      <td>{Math.round(m.avg_total_tokens || 0)}</td>
                      <td>{formatMs(m.avg_ollama_load_ms)}</td>
                      <td>{formatMs(m.avg_ollama_eval_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {!!stats.daily?.length && (
            <div className="trace-daily-stats">
              <h4>{t.dailyStats}</h4>
              <table>
                <thead><tr><th>日期</th><th>总数</th><th>成功</th><th>失败</th></tr></thead>
                <tbody>
                  {stats.daily.map(d => (
                    <tr key={d.day}><td>{d.day}</td><td>{d.cnt}</td>
                      <td className="text-success">{d.ok_count}</td>
                      <td className="text-danger">{d.fail_count}</td></tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* ---- 轨迹列表 ---- */}
      <div className="trace-list-container">
        {loading && <p className="trace-loading">{t.loading}</p>}
        {!loading && traces.length === 0 && <p className="trace-empty">{t.noData}</p>}
        {traces.length > 0 && (
          <table className="trace-table">
            <thead>
              <tr>
                <th>Time</th><th>{t.modelLabel}</th><th>{t.stageLabel}</th>
                <th>{t.statusLabel}</th><th>Tokens</th><th>Latency</th>
                <th>Ollama Load</th><th>Ollama Eval</th><th>{t.retry}</th>
                <th>{t.auditLabel}</th><th></th>
              </tr>
            </thead>
            <tbody>
              {traces.map(row => (
                <tr key={row.span_id} className={`trace-row trace-row-${row.status}`}>
                  <td className="trace-time">{formatDate(row.created_at)}</td>
                  <td><code className="trace-model">{row.model_name}</code></td>
                  <td><span className="trace-tag">{row.stage}</span></td>
                  <td>
                    <span className="trace-status" style={{ color: STATUS_COLORS[row.status] || "#888" }}>
                      {STATUS_LABELS[row.status] || row.status}
                    </span>
                  </td>
                  <td className="trace-number">{row.total_tokens}</td>
                  <td className="trace-number">{formatMs(row.total_latency_ms)}</td>
                  <td className="trace-number">{formatMs(row.ollama_load_duration_ms)}</td>
                  <td className="trace-number">{formatMs(row.ollama_eval_duration_ms)}</td>
                  <td>{row.retry_index > 0 ? `${t.retry} #${row.retry_index}` : t.no}</td>
                  <td className="trace-audit-id" title={row.audit_id}>
                    {row.audit_id ? `${row.audit_id.slice(0, 16)}...` : "-"}
                  </td>
                  <td>
                    <button className="btn-small" onClick={() => openDetail(row.span_id)}>
                      Detail
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {total > PAGE_SIZE && (
          <div className="trace-pagination">
            <button disabled={page <= 1} onClick={() => loadTraces(page - 1)}>上一页</button>
            <span>第 {page} / {Math.ceil(total / PAGE_SIZE)} 页（共 {total} 条）</span>
            <button disabled={page * PAGE_SIZE >= total} onClick={() => loadTraces(page + 1)}>下一页</button>
          </div>
        )}
      </div>

      {/* ---- 详情弹窗 ---- */}
      {detailLoading && <div className="trace-modal-overlay"><div className="trace-modal">{t.loading}</div></div>}
      {detail && !detailLoading && (
        <div className="trace-modal-overlay" onClick={() => setDetail(null)}>
          <div className="trace-modal" onClick={e => e.stopPropagation()}>
            <div className="trace-modal-header">
              <h3>{t.detailTitle}</h3>
              <button onClick={() => setDetail(null)}>{t.close}</button>
            </div>
            <div className="trace-modal-body">

              {/* Span 信息 */}
              <section>
                <h4>{t.spanInfo}</h4>
                <div className="trace-detail-grid">
                  <div><label>Trace ID</label><code>{detail.trace?.trace_id}</code></div>
                  <div><label>Span ID</label><code>{detail.trace?.span_id}</code></div>
                  <div><label>Audit ID</label><code>{detail.trace?.audit_id || "-"}</code></div>
                  <div><label>{t.modelLabel}</label><code>{detail.trace?.model_name}</code></div>
                  <div><label>{t.stageLabel}</label><span className="trace-tag">{detail.trace?.stage}</span></div>
                  <div><label>Task Profile</label><span className="trace-tag">{detail.trace?.task_profile}</span></div>
                  <div><label>Provider</label><span>{detail.trace?.provider}</span></div>
                  <div><label>{t.statusLabel}</label><span style={{ color: STATUS_COLORS[detail.trace?.status] || "#888" }}>{STATUS_LABELS[detail.trace?.status] || detail.trace?.status}</span></div>
                  <div><label>Latency</label><strong>{formatMs(detail.trace?.total_latency_ms)}</strong></div>
                  <div><label>Tokens (Prompt/Comp/Total)</label><span>{detail.trace?.prompt_tokens} / {detail.trace?.completion_tokens} / {detail.trace?.total_tokens}</span></div>
                  <div><label>Input Est</label><span>{detail.trace?.request_input_tokens_est}</span></div>
                  <div><label>Ollama Load</label><span>{formatMs(detail.trace?.ollama_load_duration_ms)}</span></div>
                  <div><label>Ollama Eval</label><span>{formatMs(detail.trace?.ollama_eval_duration_ms)}</span></div>
                  <div><label>Ollama Total</label><span>{formatMs(detail.trace?.ollama_total_duration_ms)}</span></div>
                  <div><label>{t.retry}</label><span>{detail.trace?.retry_index || 0} {detail.trace?.retry_strategy ? `(${detail.trace.retry_strategy})` : ""}</span></div>
                  {detail.trace?.error_message && (
                    <div className="trace-error-box">
                      <label>Error</label>
                      <pre>{detail.trace.error_type}: {detail.trace.error_message}</pre>
                    </div>
                  )}
                </div>
              </section>

              {/* 请求内容 */}
              {detail.full_request_messages?.length > 0 && (
                <section>
                  <h4>{t.requestContent}</h4>
                  {detail.full_request_messages.map((m, i) => (
                    <div key={i} className="trace-msg-block">
                      <span className="trace-msg-role">{m.role}</span>
                      <pre>{m.content}</pre>
                    </div>
                  ))}
                </section>
              )}

              {/* 思考过程 */}
              {detail.full_thinking_content && (
                <section>
                  <h4>{t.thinkingContent}</h4>
                  <pre className="trace-thinking-content">{detail.full_thinking_content}</pre>
                </section>
              )}

              {/* 响应内容 */}
              {detail.full_response_content && (
                <section>
                  <h4>{t.responseContent}</h4>
                  <pre className="trace-response-content">{detail.full_response_content}</pre>
                </section>
              )}

              {/* 事件时间线 */}
              {detail.jsonl_events?.length > 0 && (
                <section>
                  <h4>{t.traceEvents} ({detail.jsonl_events.length})</h4>
                  <div className="trace-event-timeline">
                    {detail.jsonl_events.map((evt, i) => (
                      <div key={i} className="trace-event-item">
                        <span className="trace-event-badge">{i + 1}</span>
                        <span className="trace-event-type">{evt.event}</span>
                        <span className="trace-event-time">{evt.created_at ? formatDate(evt.created_at) : "-"}</span>
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </div>
          </div>
        </div>
      )}

      {/* ---- 清理栏 ---- */}
      <div className="trace-cleanup-bar">
        <label>{t.cleanupHint}: <input type="date" value={cleanupDate}
          onChange={e => setCleanupDate(e.target.value)} /></label>
        <button className="btn-danger-small" disabled={!cleanupDate}
          onClick={async () => {
            if (!cleanupDate || !confirm(`确认删除 ${cleanupDate} 之前的所有日志？`)) return
            try {
              await adminCleanupLLMTraces(cleanupDate)
              loadTraces(1)
            } catch (e) { alert(String(e?.message || e)) }
          }}>
          {t.cleanup}
        </button>
      </div>

      {/* ---- 样式 (scoped via className) ---- */}
      <style>{`
        .llm-trace-viewer { padding: 0 0 32px 0; }
        .llm-trace-viewer h2 { margin: 0 0 16px 0; font-size: 20px; color: var(--text-primary, #e2e8f0); }
        .trace-filters { background: var(--bg-card, #1e293b); border-radius: 8px; padding: 12px 16px; margin-bottom: 16px; }
        .trace-filter-row { display: flex; flex-wrap: wrap; gap: 10px; align-items: flex-end; margin-bottom: 6px; }
        .trace-filter-row:last-child { margin-bottom: 0; }
        .trace-filter-row label { display: flex; flex-direction: column; gap: 4px; font-size: 12px; color: var(--text-secondary, #94a3b8); }
        .trace-filter-row input, .trace-filter-row select { padding: 6px 10px; border: 1px solid var(--border-color, #475569); border-radius: 4px; background: var(--bg-input, #0f172a); color: var(--text-primary, #e2e8f0); font-size: 13px; min-width: 140px; }
        .trace-filter-row button { padding: 6px 16px; border: none; border-radius: 4px; background: var(--accent, #3b82f6); color: #fff; font-size: 13px; cursor: pointer; }
        .trace-filter-row button.btn-secondary { background: var(--bg-secondary, #334155); }
        .trace-filter-row button:hover { opacity: 0.85; }
        .trace-stats-panel { background: var(--bg-card, #1e293b); border-radius: 8px; padding: 16px; margin-bottom: 16px; }
        .trace-stats-panel h3 { margin: 0 0 12px 0; display: flex; justify-content: space-between; align-items: center; }
        .trace-stats-grid { display: flex; gap: 16px; margin-bottom: 16px; }
        .trace-stat-card { flex: 1; background: var(--bg-input, #0f172a); border-radius: 6px; padding: 12px; text-align: center; }
        .trace-stat-card strong { display: block; font-size: 24px; color: var(--accent, #3b82f6); }
        .trace-stat-card span { font-size: 12px; color: var(--text-secondary, #94a3b8); }
        .trace-model-stats table, .trace-daily-stats table { width: 100%; border-collapse: collapse; font-size: 12px; margin-top: 8px; }
        .trace-model-stats th, .trace-model-stats td, .trace-daily-stats th, .trace-daily-stats td { padding: 6px 8px; border-bottom: 1px solid var(--border-color, #334155); text-align: left; }
        .trace-model-stats th, .trace-daily-stats th { color: var(--text-secondary, #94a3b8); font-weight: 600; }
        .text-success { color: #22c55e !important; }
        .text-danger { color: #ef4444 !important; }
        .trace-list-container { background: var(--bg-card, #1e293b); border-radius: 8px; padding: 8px 0 0 0; margin-bottom: 16px; overflow-x: auto; }
        .trace-loading, .trace-empty { padding: 32px; text-align: center; color: var(--text-secondary, #94a3b8); }
        .trace-table { width: 100%; border-collapse: collapse; font-size: 13px; }
        .trace-table th { padding: 10px 8px; border-bottom: 2px solid var(--border-color, #334155); text-align: left; font-size: 11px; color: var(--text-secondary, #94a3b8); text-transform: uppercase; white-space: nowrap; }
        .trace-table td { padding: 8px; border-bottom: 1px solid var(--border-color, #1e293b); white-space: nowrap; }
        .trace-row-success:hover, .trace-row-failed:hover { background: rgba(255,255,255,0.03); }
        .trace-model { font-size: 12px; color: var(--accent, #3b82f6); }
        .trace-tag { display: inline-block; padding: 2px 8px; border-radius: 10px; font-size: 11px; background: var(--bg-secondary, #334155); color: var(--text-secondary, #cbd5e1); }
        .trace-status { font-weight: 600; font-size: 12px; }
        .trace-number { font-family: monospace; font-size: 12px; color: var(--text-secondary, #94a3b8); }
        .trace-time { font-size: 11px; color: var(--text-tertiary, #64748b); }
        .trace-audit-id { font-family: monospace; font-size: 11px; color: var(--text-tertiary, #64748b); }
        .btn-small { padding: 3px 10px; font-size: 11px; border: 1px solid var(--border-color, #475569); border-radius: 4px; background: var(--bg-secondary, #334155); color: var(--text-primary, #e2e8f0); cursor: pointer; }
        .btn-small:hover { background: var(--accent, #3b82f6); }
        .btn-close-small { border: none; background: transparent; color: var(--text-secondary, #94a3b8); font-size: 16px; cursor: pointer; }
        .trace-pagination { display: flex; align-items: center; justify-content: center; gap: 12px; padding: 12px 0; font-size: 13px; color: var(--text-secondary, #94a3b8); }
        .trace-pagination button { padding: 6px 14px; border: 1px solid var(--border-color, #475569); border-radius: 4px; background: var(--bg-secondary, #334155); color: var(--text-primary, #e2e8f0); cursor: pointer; font-size: 12px; }
        .trace-pagination button:disabled { opacity: 0.4; cursor: default; }
        .trace-modal-overlay { position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.7); z-index: 1000; display: flex; align-items: center; justify-content: center; }
        .trace-modal { background: var(--bg-primary, #0f172a); border-radius: 12px; max-width: 900px; max-height: 85vh; width: 90%; overflow-y: auto; padding: 0; box-shadow: 0 8px 32px rgba(0,0,0,0.5); }
        .trace-modal-header { display: flex; justify-content: space-between; align-items: center; padding: 16px 20px; border-bottom: 1px solid var(--border-color, #334155); position: sticky; top: 0; background: inherit; z-index: 1; }
        .trace-modal-header h3 { margin: 0; }
        .trace-modal-header button { border: none; background: var(--bg-secondary, #334155); color: var(--text-primary, #e2e8f0); padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 13px; }
        .trace-modal-body { padding: 16px 20px; }
        .trace-modal-body section { margin-bottom: 20px; }
        .trace-modal-body h4 { margin: 0 0 10px 0; font-size: 14px; color: var(--text-secondary, #94a3b8); border-bottom: 1px solid var(--border-color, #334155); padding-bottom: 6px; }
        .trace-detail-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px 16px; }
        .trace-detail-grid div { display: flex; flex-direction: column; gap: 2px; }
        .trace-detail-grid label { font-size: 10px; text-transform: uppercase; color: var(--text-tertiary, #64748b); }
        .trace-detail-grid code { font-size: 12px; color: var(--accent, #3b82f6); word-break: break-all; }
        .trace-error-box { grid-column: 1 / -1; }
        .trace-error-box pre { margin: 4px 0 0; padding: 8px; background: rgba(239,68,68,0.1); border: 1px solid rgba(239,68,68,0.3); border-radius: 4px; font-size: 12px; color: #fca5a5; white-space: pre-wrap; word-break: break-all; }
        .trace-msg-block { margin-bottom: 10px; }
        .trace-msg-role { display: inline-block; padding: 2px 8px; border-radius: 4px; font-size: 11px; background: var(--accent, #3b82f6); color: #fff; margin-bottom: 4px; }
        .trace-msg-block pre, .trace-thinking-content, .trace-response-content { margin: 0; padding: 10px; background: var(--bg-input, #0f172a); border: 1px solid var(--border-color, #334155); border-radius: 4px; font-size: 12px; color: var(--text-primary, #e2e8f0); white-space: pre-wrap; word-break: break-all; max-height: 300px; overflow-y: auto; font-family: monospace; line-height: 1.5; }
        .trace-thinking-content { background: rgba(59,130,246,0.05); border-color: rgba(59,130,246,0.2); }
        .trace-event-timeline { display: flex; flex-direction: column; gap: 4px; }
        .trace-event-item { display: flex; align-items: center; gap: 8px; padding: 4px 0; font-size: 12px; }
        .trace-event-badge { display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px; border-radius: 50%; background: var(--accent, #3b82f6); color: #fff; font-size: 10px; font-weight: 600; }
        .trace-event-type { color: var(--text-primary, #e2e8f0); }
        .trace-event-time { color: var(--text-tertiary, #64748b); margin-left: auto; font-size: 11px; }
        .trace-cleanup-bar { display: flex; align-items: flex-end; gap: 12px; padding: 12px 16px; background: var(--bg-card, #1e293b); border-radius: 8px; font-size: 13px; }
        .trace-cleanup-bar label { display: flex; flex-direction: column; gap: 4px; font-size: 11px; color: var(--text-secondary, #94a3b8); }
        .trace-cleanup-bar input { padding: 5px 8px; border: 1px solid var(--border-color, #475569); border-radius: 4px; background: var(--bg-input, #0f172a); color: var(--text-primary, #e2e8f0); font-size: 13px; }
        .btn-danger-small { padding: 6px 14px; border: none; border-radius: 4px; background: rgba(239,68,68,0.2); color: #fca5a5; cursor: pointer; font-size: 12px; }
        .btn-danger-small:hover { background: rgba(239,68,68,0.4); }
        .btn-danger-small:disabled { opacity: 0.4; cursor: default; }
      `}</style>
    </div>
  )
}
