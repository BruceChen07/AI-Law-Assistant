import React, { useState, useEffect } from "react"
import { adminListDocuments, adminDeleteDocument, adminListUsers, adminUpdateUserRole, adminGetStats, getCurrentUser, logout } from "./api"

export default function Admin({ onBack, lang }) {
  const [tab, setTab] = useState("documents")
  const [documents, setDocuments] = useState([])
  const [users, setUsers] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(false)
  const [pagination, setPagination] = useState({ page: 1, page_size: 10, total: 0 })
  const [search, setSearch] = useState("")
  const [deleteConfirm, setDeleteConfirm] = useState(null)
  
  const user = getCurrentUser()
  const admin = !!(user && (user.role === "admin" || user.username === "admin"))
  const i18n = {
    zh: {
      title: "管理后台",
      back: "返回",
      welcome: "欢迎",
      tabStats: "统计",
      tabDocs: "文档",
      tabUsers: "用户",
      docMgmt: "文档管理",
      searchPlaceholder: "搜索文档...",
      searchBtn: "搜索",
      colFilename: "文件名",
      colSize: "大小",
      colUser: "用户",
      colCategory: "分类",
      colUploaded: "上传时间",
      colActions: "操作",
      loading: "加载中...",
      delete: "删除",
      confirm: "确认",
      cancel: "取消",
      previous: "上一页",
      next: "下一页",
      userMgmt: "用户管理",
      colUsername: "用户名",
      colEmail: "邮箱",
      colRole: "角色",
      colCreated: "创建时间",
      statsTitle: "系统统计",
      statsTotalDocs: "文档总数",
      statsTotalUsers: "用户总数",
      statsTotalStorage: "存储总量",
      statsByCategory: "按分类统计",
      statsByUser: "按用户统计",
      accessDenied: "访问受限，需要管理员权限。"
    },
    en: {
      title: "Admin Dashboard",
      back: "Back",
      welcome: "Welcome",
      tabStats: "Statistics",
      tabDocs: "Documents",
      tabUsers: "Users",
      docMgmt: "Document Management",
      searchPlaceholder: "Search documents...",
      searchBtn: "Search",
      colFilename: "Filename",
      colSize: "Size",
      colUser: "User",
      colCategory: "Category",
      colUploaded: "Uploaded",
      colActions: "Actions",
      loading: "Loading...",
      delete: "Delete",
      confirm: "Confirm",
      cancel: "Cancel",
      previous: "Previous",
      next: "Next",
      userMgmt: "User Management",
      colUsername: "Username",
      colEmail: "Email",
      colRole: "Role",
      colCreated: "Created",
      statsTitle: "System Statistics",
      statsTotalDocs: "Total Documents",
      statsTotalUsers: "Total Users",
      statsTotalStorage: "Total Storage",
      statsByCategory: "Documents by Category",
      statsByUser: "Documents by User",
      accessDenied: "Access denied. Admin permission required."
    }
  }
  const t = (i18n[lang] || i18n.zh)
  
  useEffect(() => {
    if (!admin) return
    if (tab === "documents") loadDocuments()
    if (tab === "users") loadUsers()
    if (tab === "stats") loadStats()
  }, [tab, pagination.page])
  
  const loadDocuments = async () => {
    setLoading(true)
    try {
      const data = await adminListDocuments({
        page: pagination.page,
        page_size: pagination.page_size,
        search: search
      })
      setDocuments(data.items)
      setPagination(prev => ({ ...prev, total: data.total }))
    } catch (err) {
      alert(err.message)
    } finally {
      setLoading(false)
    }
  }
  
  const loadUsers = async () => {
    setLoading(true)
    try {
      const data = await adminListUsers()
      setUsers(data)
    } catch (err) {
      alert(err.message)
    } finally {
      setLoading(false)
    }
  }
  
  const loadStats = async () => {
    setLoading(true)
    try {
      const data = await adminGetStats()
      setStats(data)
    } catch (err) {
      alert(err.message)
    } finally {
      setLoading(false)
    }
  }
  
  const handleDelete = async (docId) => {
    try {
      await adminDeleteDocument(docId)
      setDeleteConfirm(null)
      loadDocuments()
    } catch (err) {
      alert(err.message)
    }
  }
  
  const handleRoleChange = async (userId, newRole) => {
    try {
      await adminUpdateUserRole(userId, newRole)
      loadUsers()
    } catch (err) {
      alert(err.message)
    }
  }
  
  const formatSize = (bytes) => {
    if (bytes < 1024) return bytes + " B"
    if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB"
    return (bytes / (1024 * 1024)).toFixed(1) + " MB"
  }
  
  const formatDate = (dateStr) => {
    if (!dateStr) return "-"
    // Backend returns UTC time without 'Z'. Append 'Z' to treat as UTC.
    let utcStr = dateStr
    if (!utcStr.endsWith("Z") && !utcStr.includes("+")) {
      utcStr += "Z"
    }
    return new Date(utcStr).toLocaleString()
  }
  
  if (!admin) {
    return (
      <div className="page">
        <h1>{t.title}</h1>
        <div className="card">
          <p>{t.accessDenied}</p>
        </div>
      </div>
    )
  }
  
  return (
    <div className="page">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <h1>{t.title}</h1>
        <div>
          <button onClick={onBack || (() => window.history.back())} style={{ marginRight: 8 }}>{t.back}</button>
          <span>{t.welcome}, {user?.username} </span>
          <button onClick={logout}>Logout</button>
        </div>
      </div>
      
      <div className="tabs">
        <button className={tab === "stats" ? "active" : ""} onClick={() => setTab("stats")}>{t.tabStats}</button>
        <button className={tab === "documents" ? "active" : ""} onClick={() => setTab("documents")}>{t.tabDocs}</button>
        <button className={tab === "users" ? "active" : ""} onClick={() => setTab("users")}>{t.tabUsers}</button>
      </div>
      
      {tab === "stats" && stats && (
        <div className="card">
          <h2>{t.statsTitle}</h2>
          <div className="stats-grid">
            <div className="stat-card">
              <h3>{stats.total_documents}</h3>
              <p>{t.statsTotalDocs}</p>
            </div>
            <div className="stat-card">
              <h3>{stats.total_users}</h3>
              <p>{t.statsTotalUsers}</p>
            </div>
            <div className="stat-card">
              <h3>{formatSize(stats.total_size)}</h3>
              <p>{t.statsTotalStorage}</p>
            </div>
          </div>
          
          <h3>{t.statsByCategory}</h3>
          <ul>
            {Object.entries(stats.documents_by_category).map(([cat, cnt]) => (
              <li key={cat}>{cat}: {cnt}</li>
            ))}
          </ul>
          
          <h3>{t.statsByUser}</h3>
          <ul>
            {Object.entries(stats.documents_by_user).map(([user, cnt]) => (
              <li key={user}>{user}: {cnt}</li>
            ))}
          </ul>
        </div>
      )}
      
      {tab === "documents" && (
        <div className="card">
          <h2>{t.docMgmt}</h2>
          <div className="row">
            <input 
              placeholder={t.searchPlaceholder} 
              value={search}
              onChange={e => setSearch(e.target.value)}
              onKeyDown={e => e.key === "Enter" && loadDocuments()}
            />
            <button onClick={loadDocuments}>{t.searchBtn}</button>
          </div>
          
          {loading ? <p>{t.loading}</p> : (
            <table className="admin-table">
              <thead>
                <tr>
                  <th>{t.colFilename}</th>
                  <th>{t.colSize}</th>
                  <th>{t.colUser}</th>
                  <th>{t.colCategory}</th>
                  <th>{t.colUploaded}</th>
                  <th>{t.colActions}</th>
                </tr>
              </thead>
              <tbody>
                {documents.map(doc => (
                  <tr key={doc.id}>
                    <td>{doc.original_filename}</td>
                    <td>{formatSize(doc.file_size)}</td>
                    <td>{doc.username}</td>
                    <td>{doc.category || "-"}</td>
                    <td>{formatDate(doc.created_at)}</td>
                    <td>
                      {deleteConfirm === doc.id ? (
                        <span>
                          <button onClick={() => handleDelete(doc.id)}>{t.confirm}</button>
                          <button onClick={() => setDeleteConfirm(null)}>{t.cancel}</button>
                        </span>
                      ) : (
                        <button onClick={() => setDeleteConfirm(doc.id)}>{t.delete}</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          
          <div className="pagination">
            <button 
              disabled={pagination.page === 1}
              onClick={() => setPagination(prev => ({ ...prev, page: prev.page - 1 }))}
            >{t.previous}</button>
            <span>{lang === "zh" ? `第 ${pagination.page} / ${Math.ceil(pagination.total / pagination.page_size)} 页` : `Page ${pagination.page} of ${Math.ceil(pagination.total / pagination.page_size)}`}</span>
            <button 
              disabled={pagination.page * pagination.page_size >= pagination.total}
              onClick={() => setPagination(prev => ({ ...prev, page: prev.page + 1 }))}
            >{t.next}</button>
          </div>
        </div>
      )}
      
      {tab === "users" && (
        <div className="card">
          <h2>{t.userMgmt}</h2>
          {loading ? <p>{t.loading}</p> : (
            <table className="admin-table">
              <thead>
                <tr>
                  <th>{t.colUsername}</th>
                  <th>{t.colEmail}</th>
                  <th>{t.colRole}</th>
                  <th>{t.colCreated}</th>
                  <th>{t.colActions}</th>
                </tr>
              </thead>
              <tbody>
                {users.map(u => (
                  <tr key={u.id}>
                    <td>{u.username}</td>
                    <td>{u.email}</td>
                    <td>
                      <select 
                        value={u.role} 
                        onChange={e => handleRoleChange(u.id, e.target.value)}
                      >
                        <option value="user">User</option>
                        <option value="admin">Admin</option>
                      </select>
                    </td>
                    <td>{formatDate(u.created_at)}</td>
                    <td>{u.role}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}
    </div>
  )
}
