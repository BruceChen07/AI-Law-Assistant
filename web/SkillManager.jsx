import { useEffect, useMemo, useState } from "react"
import {
  adminCreateSkill,
  adminDeleteSkill,
  adminGetSkill,
  adminListSkills,
  adminSetSkillStatus,
  adminUpdateSkill
} from "./api"

const DEFAULT_CATEGORY_OPTIONS = [
  "knowledge",
  "document_processing",
  "localization",
  "audit_pipeline"
]

const DEFAULT_SCENE_OPTIONS = [
  "tax_contract_audit",
  "receipt_audit",
  "tax_digital_localization"
]

const DEFAULT_FORM = {
  id: "",
  display_name: "",
  category: "audit_pipeline",
  scene: "tax_contract_audit",
  description: "",
  visibility: "public",
  status: "active",
  source_url: "",
  reference_summary: "",
  tags_text: "",
  sort_order: 100,
  input_schema_text: "{}",
  output_schema_text: "{}",
  config_schema_text: "{}"
}

function normalizeSkillForm(skill) {
  if (!skill) return { ...DEFAULT_FORM }
  return {
    id: String(skill.id || ""),
    display_name: String(skill.display_name || ""),
    category: String(skill.category || ""),
    scene: String(skill.scene || ""),
    description: String(skill.description || ""),
    visibility: String(skill.visibility || "public"),
    status: String(skill.status || "active"),
    source_url: String(skill.source_url || ""),
    reference_summary: String(skill.reference_summary || ""),
    tags_text: Array.isArray(skill.tags) ? skill.tags.join(", ") : "",
    sort_order: Number.isFinite(Number(skill.sort_order)) ? Number(skill.sort_order) : 100,
    input_schema_text: JSON.stringify(skill.input_schema || {}, null, 2),
    output_schema_text: JSON.stringify(skill.output_schema || {}, null, 2),
    config_schema_text: JSON.stringify(skill.config_schema || {}, null, 2)
  }
}

function parseJsonField(text, fieldLabel) {
  const source = String(text || "").trim()
  if (!source) return {}
  try {
    const parsed = JSON.parse(source)
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
      throw new Error(`${fieldLabel} must be a JSON object`)
    }
    return parsed
  } catch (err) {
    throw new Error(`${fieldLabel}: ${err.message}`)
  }
}

function validateSkillForm(form, labels) {
  const nextErrors = {}
  if (!String(form.id || "").trim()) nextErrors.id = labels.idRequired
  if (!/^[a-z0-9][a-z0-9_-]{2,63}$/.test(String(form.id || "").trim())) nextErrors.id = labels.idFormat
  if (!String(form.display_name || "").trim()) nextErrors.display_name = labels.displayNameRequired
  if (!String(form.category || "").trim()) nextErrors.category = labels.categoryRequired
  if (!String(form.scene || "").trim()) nextErrors.scene = labels.sceneRequired

  let inputSchema = {}
  let outputSchema = {}
  let configSchema = {}
  try {
    inputSchema = parseJsonField(form.input_schema_text, labels.inputSchema)
  } catch (err) {
    nextErrors.input_schema_text = err.message
  }
  try {
    outputSchema = parseJsonField(form.output_schema_text, labels.outputSchema)
  } catch (err) {
    nextErrors.output_schema_text = err.message
  }
  try {
    configSchema = parseJsonField(form.config_schema_text, labels.configSchema)
  } catch (err) {
    nextErrors.config_schema_text = err.message
  }

  if (Object.keys(nextErrors).length > 0) {
    return { errors: nextErrors, payload: null }
  }

  const tags = String(form.tags_text || "")
    .split(",")
    .map(item => item.trim())
    .filter(Boolean)

  return {
    errors: {},
    payload: {
      id: String(form.id || "").trim(),
      display_name: String(form.display_name || "").trim(),
      category: String(form.category || "").trim(),
      scene: String(form.scene || "").trim(),
      description: String(form.description || "").trim(),
      visibility: String(form.visibility || "public"),
      status: String(form.status || "active"),
      source_url: String(form.source_url || "").trim(),
      reference_summary: String(form.reference_summary || "").trim(),
      tags,
      sort_order: Number(form.sort_order || 0),
      input_schema: inputSchema,
      output_schema: outputSchema,
      config_schema: configSchema
    }
  }
}

export default function SkillManager({ labels }) {
  const [items, setItems] = useState([])
  const [loading, setLoading] = useState(false)
  const [detailLoading, setDetailLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [message, setMessage] = useState("")
  const [error, setError] = useState("")
  const [page, setPage] = useState(1)
  const [pageSize] = useState(8)
  const [total, setTotal] = useState(0)
  const [search, setSearch] = useState("")
  const [searchInput, setSearchInput] = useState("")
  const [category, setCategory] = useState("")
  const [status, setStatus] = useState("")
  const [scene, setScene] = useState("")
  const [editorOpen, setEditorOpen] = useState(false)
  const [editingId, setEditingId] = useState("")
  const [confirmDeleteId, setConfirmDeleteId] = useState("")
  const [form, setForm] = useState({ ...DEFAULT_FORM })
  const [formErrors, setFormErrors] = useState({})
  const [knownCategories, setKnownCategories] = useState(DEFAULT_CATEGORY_OPTIONS)
  const [knownScenes, setKnownScenes] = useState(DEFAULT_SCENE_OPTIONS)

  const totalPages = Math.max(1, Math.ceil(total / pageSize))

  const refreshList = async (targetPage = page) => {
    setLoading(true)
    setError("")
    try {
      const data = await adminListSkills({
        page: targetPage,
        page_size: pageSize,
        search: search.trim(),
        category,
        status,
        scene: scene.trim()
      })
      const nextItems = Array.isArray(data.items) ? data.items : []
      setItems(nextItems)
      setTotal(Number(data.total || 0))
      setPage(Number(data.page || targetPage))
      setKnownCategories(prev => Array.from(new Set([
        ...prev,
        ...nextItems.map(item => String(item.category || "").trim()).filter(Boolean)
      ])))
      setKnownScenes(prev => Array.from(new Set([
        ...prev,
        ...nextItems.map(item => String(item.scene || "").trim()).filter(Boolean)
      ])))
    } catch (err) {
      setError(err.message || labels.loadFailed)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refreshList(page)
  }, [page, search, category, status, scene])

  const openCreate = () => {
    setEditingId("")
    setForm({ ...DEFAULT_FORM })
    setFormErrors({})
    setError("")
    setMessage("")
    setEditorOpen(true)
  }

  const openEdit = async (skillId) => {
    setDetailLoading(true)
    setError("")
    setMessage("")
    setFormErrors({})
    try {
      const detail = await adminGetSkill(skillId)
      setEditingId(skillId)
      setForm(normalizeSkillForm(detail))
      setEditorOpen(true)
    } catch (err) {
      setError(err.message || labels.loadDetailFailed)
    } finally {
      setDetailLoading(false)
    }
  }

  const onSubmit = async (event) => {
    event.preventDefault()
    setMessage("")
    setError("")
    const { errors, payload } = validateSkillForm(form, labels)
    setFormErrors(errors)
    if (!payload) return

    setSaving(true)
    try {
      if (editingId) {
        const updatePayload = { ...payload }
        delete updatePayload.id
        await adminUpdateSkill(editingId, updatePayload)
        setMessage(labels.updateSuccess)
      } else {
        await adminCreateSkill(payload)
        setMessage(labels.createSuccess)
      }
      setEditorOpen(false)
      setEditingId("")
      setForm({ ...DEFAULT_FORM })
      setFormErrors({})
      await refreshList(1)
    } catch (err) {
      setError(err.message || labels.saveFailed)
    } finally {
      setSaving(false)
    }
  }

  const onToggleStatus = async (item) => {
    const nextStatus = item.status === "active" ? "disabled" : "active"
    setError("")
    setMessage("")
    try {
      await adminSetSkillStatus(item.id, nextStatus)
      setMessage(nextStatus === "active" ? labels.enableSuccess : labels.disableSuccess)
      await refreshList(page)
    } catch (err) {
      setError(err.message || labels.statusFailed)
    }
  }

  const onDelete = async (skillId) => {
    setError("")
    setMessage("")
    try {
      await adminDeleteSkill(skillId)
      setMessage(labels.deleteSuccess)
      setConfirmDeleteId("")
      const nextPage = items.length === 1 && page > 1 ? page - 1 : page
      await refreshList(nextPage)
    } catch (err) {
      setError(err.message || labels.deleteFailed)
    }
  }

  const rows = useMemo(() => items.map(item => ({
    ...item,
    tagsText: Array.isArray(item.tags) ? item.tags.join(", ") : ""
  })), [items])

  return (
    <section className="panel">
      <div className="skill-toolbar">
        <div>
          <h2>{labels.title}</h2>
          <p className="meta">{labels.subtitle}</p>
        </div>
        <div className="skill-toolbar-actions">
          <button type="button" onClick={() => refreshList(page)} disabled={loading}>
            {loading ? labels.refreshing : labels.refresh}
          </button>
          <button type="button" className="active" onClick={openCreate}>
            {labels.create}
          </button>
        </div>
      </div>

      {message && <div className="success">{message}</div>}
      {error && <div className="error">{error}</div>}

      <div className="skill-filters">
        <label>
          <span>{labels.search}</span>
          <input
            value={searchInput}
            placeholder={labels.searchPlaceholder}
            onChange={e => setSearchInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === "Enter") {
                setPage(1)
                setSearch(searchInput)
              }
            }}
          />
        </label>
        <label>
          <span>{labels.category}</span>
          <select value={category} onChange={e => { setPage(1); setCategory(e.target.value) }}>
            <option value="">{labels.allCategories}</option>
            {knownCategories.map(item => (
              <option key={item} value={item}>{item}</option>
            ))}
          </select>
        </label>
        <label>
          <span>{labels.status}</span>
          <select value={status} onChange={e => { setPage(1); setStatus(e.target.value) }}>
            <option value="">{labels.allStatuses}</option>
            <option value="active">{labels.statusActive}</option>
            <option value="disabled">{labels.statusDisabled}</option>
            <option value="deleted">{labels.statusDeleted}</option>
          </select>
        </label>
        <label>
          <span>{labels.scene}</span>
          <input
            list="skill-scene-options"
            value={scene}
            placeholder={labels.scenePlaceholder}
            onChange={e => { setPage(1); setScene(e.target.value) }}
          />
          <datalist id="skill-scene-options">
            {knownScenes.map(item => (
              <option key={item} value={item} />
            ))}
          </datalist>
        </label>
        <div className="skill-filter-actions">
          <button type="button" onClick={() => { setPage(1); setSearch(searchInput) }}>
            {labels.searchButton}
          </button>
          <button
            type="button"
            onClick={() => {
              setSearchInput("")
              setSearch("")
              setCategory("")
              setStatus("")
              setScene("")
              setPage(1)
            }}
          >
            {labels.reset}
          </button>
        </div>
      </div>

      {(editorOpen || detailLoading) && (
        <div className="skill-editor-card">
          <div className="advanced-header">
            <div className="advanced-title">
              {detailLoading ? labels.loadingDetail : editingId ? labels.editTitle : labels.createTitle}
            </div>
            <button type="button" onClick={() => { setEditorOpen(false); setDetailLoading(false) }}>
              {labels.close}
            </button>
          </div>
          {!detailLoading && (
            <form onSubmit={onSubmit} className="skill-form-grid">
              <label>
                <span>{labels.id}</span>
                <input
                  value={form.id}
                  disabled={!!editingId}
                  placeholder="example_skill_id"
                  onChange={e => setForm(prev => ({ ...prev, id: e.target.value }))}
                />
                {formErrors.id && <span className="form-error">{formErrors.id}</span>}
              </label>
              <label>
                <span>{labels.displayName}</span>
                <input
                  value={form.display_name}
                  onChange={e => setForm(prev => ({ ...prev, display_name: e.target.value }))}
                />
                {formErrors.display_name && <span className="form-error">{formErrors.display_name}</span>}
              </label>
              <label>
                <span>{labels.category}</span>
                <input
                  list="skill-category-options"
                  value={form.category}
                  onChange={e => setForm(prev => ({ ...prev, category: e.target.value }))}
                />
                <datalist id="skill-category-options">
                  {knownCategories.map(item => (
                    <option key={item} value={item} />
                  ))}
                </datalist>
                {formErrors.category && <span className="form-error">{formErrors.category}</span>}
              </label>
              <label>
                <span>{labels.scene}</span>
                <input
                  list="skill-scene-options"
                  value={form.scene}
                  onChange={e => setForm(prev => ({ ...prev, scene: e.target.value }))}
                />
                {formErrors.scene && <span className="form-error">{formErrors.scene}</span>}
              </label>
              <label>
                <span>{labels.visibility}</span>
                <select
                  value={form.visibility}
                  onChange={e => setForm(prev => ({ ...prev, visibility: e.target.value }))}
                >
                  <option value="public">{labels.visibilityPublic}</option>
                  <option value="private">{labels.visibilityPrivate}</option>
                </select>
              </label>
              <label>
                <span>{labels.status}</span>
                <select
                  value={form.status}
                  onChange={e => setForm(prev => ({ ...prev, status: e.target.value }))}
                >
                  <option value="active">{labels.statusActive}</option>
                  <option value="disabled">{labels.statusDisabled}</option>
                </select>
              </label>
              <label>
                <span>{labels.sortOrder}</span>
                <input
                  type="number"
                  min="0"
                  max="100000"
                  value={form.sort_order}
                  onChange={e => setForm(prev => ({ ...prev, sort_order: e.target.value }))}
                />
              </label>
              <label className="wide">
                <span>{labels.tags}</span>
                <input
                  value={form.tags_text}
                  placeholder={labels.tagsPlaceholder}
                  onChange={e => setForm(prev => ({ ...prev, tags_text: e.target.value }))}
                />
              </label>
              <label className="wide">
                <span>{labels.description}</span>
                <textarea
                  rows="3"
                  value={form.description}
                  onChange={e => setForm(prev => ({ ...prev, description: e.target.value }))}
                />
              </label>
              <label className="wide">
                <span>{labels.sourceUrl}</span>
                <input
                  value={form.source_url}
                  placeholder="https://example.com/skill-reference"
                  onChange={e => setForm(prev => ({ ...prev, source_url: e.target.value }))}
                />
              </label>
              <label className="wide">
                <span>{labels.referenceSummary}</span>
                <textarea
                  rows="3"
                  value={form.reference_summary}
                  onChange={e => setForm(prev => ({ ...prev, reference_summary: e.target.value }))}
                />
              </label>
              <label className="wide">
                <span>{labels.inputSchema}</span>
                <textarea
                  rows="6"
                  value={form.input_schema_text}
                  onChange={e => setForm(prev => ({ ...prev, input_schema_text: e.target.value }))}
                />
                {formErrors.input_schema_text && <span className="form-error">{formErrors.input_schema_text}</span>}
              </label>
              <label className="wide">
                <span>{labels.outputSchema}</span>
                <textarea
                  rows="6"
                  value={form.output_schema_text}
                  onChange={e => setForm(prev => ({ ...prev, output_schema_text: e.target.value }))}
                />
                {formErrors.output_schema_text && <span className="form-error">{formErrors.output_schema_text}</span>}
              </label>
              <label className="wide">
                <span>{labels.configSchema}</span>
                <textarea
                  rows="6"
                  value={form.config_schema_text}
                  onChange={e => setForm(prev => ({ ...prev, config_schema_text: e.target.value }))}
                />
                {formErrors.config_schema_text && <span className="form-error">{formErrors.config_schema_text}</span>}
              </label>
              <div className="skill-form-actions wide">
                <button type="submit" disabled={saving}>
                  {saving ? labels.saving : editingId ? labels.saveUpdate : labels.saveCreate}
                </button>
                <button type="button" onClick={() => setEditorOpen(false)} disabled={saving}>
                  {labels.cancel}
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      <div className="skill-table-wrap">
        <table className="admin-table">
          <thead>
            <tr>
              <th>{labels.colName}</th>
              <th>{labels.category}</th>
              <th>{labels.scene}</th>
              <th>{labels.status}</th>
              <th>{labels.visibility}</th>
              <th>{labels.references}</th>
              <th>{labels.updatedAt}</th>
              <th>{labels.colActions}</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan="8">{labels.loading}</td>
              </tr>
            ) : rows.length === 0 ? (
              <tr>
                <td colSpan="8">{labels.empty}</td>
              </tr>
            ) : rows.map(item => (
              <tr key={item.id}>
                <td>
                  <div className="title">{item.display_name}</div>
                  <div className="meta">{item.id}</div>
                  {item.description ? <div className="skill-row-subtext">{item.description}</div> : null}
                  {item.tagsText ? <div className="skill-tag-list">{item.tagsText}</div> : null}
                </td>
                <td>{item.category}</td>
                <td>{item.scene}</td>
                <td>
                  <span className={`skill-status-badge skill-status-${item.status}`}>
                    {item.status}
                  </span>
                </td>
                <td>{item.visibility}</td>
                <td>{item.template_ref_count}/{item.agent_ref_count}</td>
                <td>{item.updated_at || item.created_at}</td>
                <td>
                  <div className="skill-action-group">
                    <button type="button" onClick={() => openEdit(item.id)} disabled={detailLoading || saving}>
                      {labels.edit}
                    </button>
                    <button type="button" onClick={() => onToggleStatus(item)} disabled={saving || (item.in_use && item.status === "active")}>
                      {item.status === "active" ? labels.disable : labels.enable}
                    </button>
                    {confirmDeleteId === item.id ? (
                      <>
                        <button type="button" onClick={() => onDelete(item.id)} disabled={saving}>{labels.confirm}</button>
                        <button type="button" onClick={() => setConfirmDeleteId("")}>{labels.cancel}</button>
                      </>
                    ) : (
                      <button type="button" onClick={() => setConfirmDeleteId(item.id)} disabled={saving}>
                        {labels.delete}
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="pagination">
        <button type="button" disabled={page <= 1 || loading} onClick={() => setPage(prev => Math.max(1, prev - 1))}>
          {labels.previous}
        </button>
        <span className="meta">{labels.pageInfo.replace("{page}", String(page)).replace("{totalPages}", String(totalPages)).replace("{total}", String(total))}</span>
        <button type="button" disabled={page >= totalPages || loading} onClick={() => setPage(prev => Math.min(totalPages, prev + 1))}>
          {labels.next}
        </button>
      </div>
    </section>
  )
}
