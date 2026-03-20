import React, { useState, useEffect, useCallback } from "react"
import { adminGetTokenStats, adminExportTokenStats } from "./api"
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer, LineChart, Line
} from "recharts"

export default function TokenMonitor({ lang }) {
  const [loading, setLoading] = useState(false)
  const [data, setData] = useState(null)
  const [error, setError] = useState("")
  
  // Filters
  const [granularity, setGranularity] = useState("day") // hour, day, week
  const [rankBy, setRankBy] = useState("file_path") // file_path, stage, model
  const [startDate, setStartDate] = useState("")
  const [endDate, setEndDate] = useState("")
  
  const i18n = {
    zh: {
      title: "Token 消耗监控",
      refresh: "刷新数据",
      export: "导出 CSV",
      granularity: "时间维度",
      hour: "小时",
      day: "天",
      week: "周",
      rankBy: "排名维度",
      rankFile: "文件",
      rankStage: "阶段",
      rankModel: "模型",
      startDate: "开始日期",
      endDate: "结束日期",
      totals: "消耗总量统计",
      totalInput: "总输入 Token",
      totalOutput: "总输出 Token",
      totalAll: "总消耗 Token",
      reqCount: "请求次数",
      trends: "消耗趋势",
      ranking: "消耗排名 (Top 10)",
      alerts: "异常消耗预警",
      level: "级别",
      reason: "原因",
      value: "值",
      threshold: "阈值",
      bucket: "时间块",
      meta: "元数据",
      loading: "加载中...",
      error: "加载失败",
      noData: "暂无数据",
      lastUpdated: "最后更新",
    },
    en: {
      title: "Token Usage Monitor",
      refresh: "Refresh Data",
      export: "Export CSV",
      granularity: "Granularity",
      hour: "Hour",
      day: "Day",
      week: "Week",
      rankBy: "Rank By",
      rankFile: "File",
      rankStage: "Stage",
      rankModel: "Model",
      startDate: "Start Date",
      endDate: "End Date",
      totals: "Usage Totals",
      totalInput: "Total Input",
      totalOutput: "Total Output",
      totalAll: "Total Tokens",
      reqCount: "Requests",
      trends: "Usage Trends",
      ranking: "Top Consumers (Top 10)",
      alerts: "Abnormal Usage Alerts",
      level: "Level",
      reason: "Reason",
      value: "Value",
      threshold: "Threshold",
      bucket: "Bucket",
      meta: "Meta",
      loading: "Loading...",
      error: "Error loading data",
      noData: "No data available",
      lastUpdated: "Last updated",
    }
  }
  
  const t = i18n[lang] || i18n.zh

  const loadData = useCallback(async () => {
    setLoading(true)
    setError("")
    try {
      const params = { granularity, rank_by: rankBy }
      if (startDate) params.start = startDate
      if (endDate) params.end = endDate
      const res = await adminGetTokenStats(params)
      setData(res)
    } catch (err) {
      setError(err.message || t.error)
    } finally {
      setLoading(false)
    }
  }, [granularity, rankBy, startDate, endDate, t.error])

  useEffect(() => {
    loadData()
    // Auto refresh every 5 minutes
    const timer = setInterval(() => {
      loadData()
    }, 5 * 60 * 1000)
    return () => clearInterval(timer)
  }, [loadData])

  const handleExport = async () => {
    try {
      const params = { granularity }
      if (startDate) params.start = startDate
      if (endDate) params.end = endDate
      const blob = await adminExportTokenStats(params)
      const url = window.URL.createObjectURL(blob)
      const a = document.createElement("a")
      a.href = url
      a.download = `token_usage_${granularity}.csv`
      document.body.appendChild(a)
      a.click()
      a.remove()
      window.URL.revokeObjectURL(url)
    } catch (err) {
      alert(t.error + ": " + err.message)
    }
  }

  const formatNumber = (num) => {
    return new Intl.NumberFormat().format(num)
  }

  return (
    <div className="card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 20 }}>
        <h2>{t.title}</h2>
        <div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
          {data && <span style={{ fontSize: "12px", color: "#888" }}>{t.lastUpdated}: {new Date(data.last_updated).toLocaleTimeString()}</span>}
          <button onClick={loadData} disabled={loading}>{loading ? t.loading : t.refresh}</button>
          <button onClick={handleExport}>{t.export}</button>
        </div>
      </div>

      <div style={{ display: "flex", gap: "20px", marginBottom: 20, flexWrap: "wrap" }} className="form">
        <div className="row" style={{ flex: "1 1 200px" }}>
          <label>{t.granularity}</label>
          <select value={granularity} onChange={e => setGranularity(e.target.value)}>
            <option value="hour">{t.hour}</option>
            <option value="day">{t.day}</option>
            <option value="week">{t.week}</option>
          </select>
        </div>
        <div className="row" style={{ flex: "1 1 200px" }}>
          <label>{t.rankBy}</label>
          <select value={rankBy} onChange={e => setRankBy(e.target.value)}>
            <option value="file_path">{t.rankFile}</option>
            <option value="stage">{t.rankStage}</option>
            <option value="model">{t.rankModel}</option>
          </select>
        </div>
        <div className="row" style={{ flex: "1 1 200px" }}>
          <label>{t.startDate}</label>
          <input type="date" value={startDate} onChange={e => setStartDate(e.target.value)} />
        </div>
        <div className="row" style={{ flex: "1 1 200px" }}>
          <label>{t.endDate}</label>
          <input type="date" value={endDate} onChange={e => setEndDate(e.target.value)} />
        </div>
      </div>

      {error && <div style={{ color: "red", marginBottom: 20 }}>{error}</div>}

      {data && (
        <>
          <div className="stats-grid" style={{ marginBottom: 30 }}>
            <div className="stat-card">
              <h3>{formatNumber(data.totals.total_tokens)}</h3>
              <p>{t.totalAll}</p>
            </div>
            <div className="stat-card">
              <h3>{formatNumber(data.totals.input_tokens)}</h3>
              <p>{t.totalInput}</p>
            </div>
            <div className="stat-card">
              <h3>{formatNumber(data.totals.output_tokens)}</h3>
              <p>{t.totalOutput}</p>
            </div>
            <div className="stat-card">
              <h3>{formatNumber(data.totals.request_count)}</h3>
              <p>{t.reqCount}</p>
            </div>
          </div>

          <h3 style={{ marginBottom: 15 }}>{t.trends}</h3>
          <div style={{ width: "100%", height: 300, marginBottom: 40, background: "var(--card-bg, #fff)", padding: 10, borderRadius: 8 }}>
            <ResponsiveContainer>
              <BarChart data={data.series}>
                <CartesianGrid strokeDasharray="3 3" />
                <XAxis dataKey="bucket" tick={{fontSize: 12}} />
                <YAxis tick={{fontSize: 12}} />
                <Tooltip />
                <Legend />
                <Bar dataKey="input_tokens" name="Input Tokens" stackId="a" fill="#8884d8" />
                <Bar dataKey="output_tokens" name="Output Tokens" stackId="a" fill="#82ca9d" />
              </BarChart>
            </ResponsiveContainer>
          </div>

          <div style={{ display: "flex", gap: "20px", flexWrap: "wrap" }}>
            <div style={{ flex: "1 1 45%", minWidth: "300px" }}>
              <h3 style={{ marginBottom: 15 }}>{t.ranking}</h3>
              {data.rankings.length > 0 ? (
                <table className="admin-table">
                  <thead>
                    <tr>
                      <th>{t.rankBy}</th>
                      <th>{t.totalAll}</th>
                      <th>{t.reqCount}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.rankings.map((r, i) => (
                      <tr key={i}>
                        <td title={r.key}>{r.key.length > 30 ? r.key.substring(0, 30) + '...' : r.key}</td>
                        <td>{formatNumber(r.total_tokens)}</td>
                        <td>{r.request_count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <p>{t.noData}</p>}
            </div>

            <div style={{ flex: "1 1 45%", minWidth: "300px" }}>
              <h3 style={{ marginBottom: 15 }}>{t.alerts}</h3>
              {data.alerts.length > 0 ? (
                <table className="admin-table" style={{ borderLeft: "3px solid #f44336" }}>
                  <thead>
                    <tr>
                      <th>{t.level}</th>
                      <th>{t.reason}</th>
                      <th>{t.value} / {t.threshold}</th>
                      <th>{t.bucket}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.alerts.map((a, i) => (
                      <tr key={i} style={{ backgroundColor: a.level === 'critical' ? 'rgba(244, 67, 54, 0.1)' : 'rgba(255, 152, 0, 0.1)' }}>
                        <td style={{ color: a.level === 'critical' ? '#d32f2f' : '#f57c00', fontWeight: 'bold' }}>
                          {a.level.toUpperCase()}
                        </td>
                        <td>{a.reason}</td>
                        <td>{formatNumber(a.value)} / {formatNumber(a.threshold)}</td>
                        <td>{a.bucket || '-'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              ) : <p>{t.noData}</p>}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
