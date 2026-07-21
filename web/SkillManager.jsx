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

const TEMPLATE_SKILL_CARD = {
  overview: "Describe the core problem this skill solves in one or two concise sentences.",
  publisher_name: "AI-Law-Assistant",
  publisher_handle: "@your-team",
  version: "v0.1.0",
  license_name: "Internal Reference",
  geography: ["China"],
  use_type: "Internal / project use",
  use_case: "Explain who should use this skill and the business scenario it targets.",
  review_before_use: [
    {
      risk: "Summarize the main business or compliance risk.",
      mitigation: "Explain how operators should review or mitigate the risk."
    }
  ],
  ethical_considerations: "Note any human review, compliance, or policy constraints before production use.",
  output_behavior: {
    types: ["Structured JSON"],
    format: "Summarize the expected response format.",
    parameters: "2D",
    side_effects: ["List any file writes, notifications, or downstream actions."]
  },
  references: [
    {
      label: "Source or handbook",
      url: "https://example.com/skill-reference",
      type: "external"
    }
  ]
}

const TEMPLATE_SKILL_MD = `# Example AI Skill

## Overview
Describe the skill goal, user value, and expected output in concise language.

## Trigger Conditions
- Explain which tasks should route to this skill.
- Describe the minimum required inputs.

## Workflow
1. Validate the input context.
2. Read the required references or knowledge files.
3. Execute the skill logic.
4. Return structured output for downstream processing.

## Review Checklist
- Confirm the source material is up to date.
- Mark uncertain items for manual review.
- Keep the output aligned with project policy and scope.
`

function createEmptyFile(partial = {}) {
  return {
    path: "",
    entry_type: "file",
    content_text: "",
    branch_name: "main",
    sort_order: 100,
    ...partial
  }
}

function createEmptyVersion(partial = {}) {
  return {
    version_tag: "",
    release_label: "",
    published_at: "",
    is_latest: false,
    download_url: "",
    changelog_text: "",
    sort_order: 100,
    ...partial
  }
}

function syncSpecialFiles(files, skillMdText, skillCardText) {
  const nextFiles = Array.isArray(files) ? files.map(item => ({ ...item })) : []
  const replaceOrInsert = (path, contentText, sortOrder) => {
    const encodedSize = new Blob([String(contentText || "")]).size
    const index = nextFiles.findIndex(item => String(item.path || "") === path)
    const nextItem = {
      ...(index >= 0 ? nextFiles[index] : createEmptyFile()),
      path,
      entry_type: "file",
      content_text: String(contentText || ""),
      branch_name: String(index >= 0 ? nextFiles[index].branch_name || "main" : "main"),
      sort_order: Number(index >= 0 ? nextFiles[index].sort_order || sortOrder : sortOrder),
      size_bytes: encodedSize
    }
    if (index >= 0) nextFiles[index] = nextItem
    else nextFiles.push(nextItem)
  }
  replaceOrInsert("SKILL.md", skillMdText, 10)
  replaceOrInsert("skill-card.md", skillCardText, 20)
  return nextFiles
    .map(item => ({
      ...item,
      size_bytes: Number.isFinite(Number(item.size_bytes))
        ? Number(item.size_bytes)
        : new Blob([String(item.content_text || "")]).size
    }))
    .sort((a, b) => Number(a.sort_order || 0) - Number(b.sort_order || 0) || String(a.path || "").localeCompare(String(b.path || "")))
}

function createBlankForm() {
  return {
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
    config_schema_text: "{}",
    publisher_name: "AI-Law-Assistant",
    publisher_handle: "@system",
    install_command: "",
    skill_md_text: "",
    skill_card_text: JSON.stringify(TEMPLATE_SKILL_CARD, null, 2),
    current_version: "v0.1.0",
    license_name: "Internal Reference",
    security_audit_status: "draft",
    downloads_30d: 0,
    downloads_all_time: 0,
    last_published_at: "",
    files: syncSpecialFiles([], "", JSON.stringify(TEMPLATE_SKILL_CARD, null, 2)),
    versions: [createEmptyVersion({ version_tag: "v0.1.0", is_latest: true, release_label: "Draft", sort_order: 10 })]
  }
}

function createTemplateForm() {
  const skillCardText = JSON.stringify(TEMPLATE_SKILL_CARD, null, 2)
  const files = syncSpecialFiles(
    [
      createEmptyFile({
        path: "references/domain-notes.md",
        content_text: "# Domain Notes\nDescribe the authoritative sources, boundaries, and terminology used by this skill.\n",
        sort_order: 30
      }),
      createEmptyFile({
        path: "references/examples.md",
        content_text: "# Examples\nAdd sample prompts, expected inputs, and output snippets for quick validation.\n",
        sort_order: 40
      })
    ],
    TEMPLATE_SKILL_MD,
    skillCardText
  )
  return {
    ...createBlankForm(),
    display_name: "Example AI Skill",
    category: "knowledge",
    scene: "tax_contract_audit",
    description: "A reusable starter template for custom AI skills in the current project.",
    source_url: "https://example.com/skill-reference",
    reference_summary: "Explain the source material, validation approach, and maintenance owner for this skill.",
    tags_text: "template, custom-skill, starter",
    skill_md_text: TEMPLATE_SKILL_MD,
    skill_card_text: skillCardText,
    current_version: "v0.1.0",
    license_name: "Internal Reference",
    security_audit_status: "draft",
    last_published_at: new Date().toISOString().slice(0, 10),
    files,
    versions: [
      createEmptyVersion({
        version_tag: "v0.1.0",
        release_label: "Draft",
        published_at: new Date().toISOString().slice(0, 10),
        is_latest: true,
        changelog_text: "- Initial project-aligned template\n- Add your references and workflow before production use\n",
        sort_order: 10
      })
    ]
  }
}

function formatBytes(value) {
  const size = Number(value || 0)
  if (!Number.isFinite(size) || size <= 0) return "-"
  if (size < 1024) return `${size} B`
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`
  return `${(size / (1024 * 1024)).toFixed(1)} MB`
}

function parseJsonObject(text, fieldLabel) {
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

function normalizeSkillForm(skill) {
  if (!skill) return createBlankForm()
  const skillCardText = JSON.stringify(skill.skill_card || {}, null, 2)
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
    config_schema_text: JSON.stringify(skill.config_schema || {}, null, 2),
    publisher_name: String(skill.publisher_name || ""),
    publisher_handle: String(skill.publisher_handle || ""),
    install_command: String(skill.install_command || ""),
    skill_md_text: String(skill.skill_md_text || ""),
    skill_card_text: skillCardText,
    current_version: String(skill.current_version || ""),
    license_name: String(skill.license_name || ""),
    security_audit_status: String(skill.security_audit_status || ""),
    downloads_30d: Number(skill.downloads_30d || 0),
    downloads_all_time: Number(skill.downloads_all_time || 0),
    last_published_at: String(skill.last_published_at || ""),
    files: syncSpecialFiles(
      Array.isArray(skill.files) ? skill.files.map(item => createEmptyFile(item)) : [],
      String(skill.skill_md_text || ""),
      skillCardText
    ),
    versions: Array.isArray(skill.versions) && skill.versions.length > 0
      ? skill.versions.map((item, index) => createEmptyVersion({
        ...item,
        changelog_text: Array.isArray(item.changelog_items) && item.changelog_items.length > 0
          ? item.changelog_items.map(line => `- ${line}`).join("\n")
          : String(item.changelog_text || ""),
        sort_order: Number.isFinite(Number(item.sort_order)) ? Number(item.sort_order) : (index + 1) * 10
      }))
      : createBlankForm().versions
  }
}

function validateFiles(files, labels) {
  const nextErrors = {}
  const seen = new Set()
  const payloadFiles = []
  for (let index = 0; index < files.length; index += 1) {
    const item = files[index] || {}
    const path = String(item.path || "").trim()
    if (!path) {
      nextErrors.files = labels.filePathRequired
      continue
    }
    if (seen.has(path)) {
      nextErrors.files = labels.filePathDuplicate
      continue
    }
    seen.add(path)
    const entryType = String(item.entry_type || "file")
    const contentText = entryType === "dir" ? "" : String(item.content_text || "")
    payloadFiles.push({
      path,
      entry_type: entryType === "dir" ? "dir" : "file",
      content_text: contentText,
      branch_name: String(item.branch_name || ""),
      size_bytes: new Blob([contentText]).size,
      sort_order: Number(item.sort_order || (index + 1) * 10)
    })
  }
  return { nextErrors, payloadFiles }
}

function validateVersions(versions, labels) {
  const nextErrors = {}
  const seen = new Set()
  const payloadVersions = []
  for (let index = 0; index < versions.length; index += 1) {
    const item = versions[index] || {}
    const versionTag = String(item.version_tag || "").trim()
    if (!versionTag) {
      nextErrors.versions = labels.versionTagRequired
      continue
    }
    if (seen.has(versionTag)) {
      nextErrors.versions = labels.versionTagDuplicate
      continue
    }
    seen.add(versionTag)
    const changelogItems = String(item.changelog_text || "")
      .split(/\r?\n/)
      .map(line => line.trim())
      .filter(Boolean)
      .map(line => line.replace(/^-+\s*/, "").trim())
      .filter(Boolean)
    payloadVersions.push({
      version_tag: versionTag,
      release_label: String(item.release_label || "").trim(),
      published_at: String(item.published_at || "").trim(),
      is_latest: !!item.is_latest,
      download_url: String(item.download_url || "").trim(),
      changelog_items: changelogItems,
      sort_order: Number(item.sort_order || (index + 1) * 10)
    })
  }
  if (payloadVersions.length > 0 && !payloadVersions.some(item => item.is_latest)) {
    payloadVersions[0].is_latest = true
  }
  return { nextErrors, payloadVersions }
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
  let skillCard = {}
  try {
    inputSchema = parseJsonObject(form.input_schema_text, labels.inputSchema)
  } catch (err) {
    nextErrors.input_schema_text = err.message
  }
  try {
    outputSchema = parseJsonObject(form.output_schema_text, labels.outputSchema)
  } catch (err) {
    nextErrors.output_schema_text = err.message
  }
  try {
    configSchema = parseJsonObject(form.config_schema_text, labels.configSchema)
  } catch (err) {
    nextErrors.config_schema_text = err.message
  }
  try {
    skillCard = parseJsonObject(form.skill_card_text, labels.tab_skill_card)
  } catch (err) {
    nextErrors.skill_card_text = err.message
  }

  const syncedFiles = syncSpecialFiles(form.files, form.skill_md_text, form.skill_card_text)
  const { nextErrors: fileErrors, payloadFiles } = validateFiles(syncedFiles, labels)
  const { nextErrors: versionErrors, payloadVersions } = validateVersions(form.versions, labels)
  Object.assign(nextErrors, fileErrors, versionErrors)

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
      config_schema: configSchema,
      publisher_name: String(form.publisher_name || "").trim(),
      publisher_handle: String(form.publisher_handle || "").trim(),
      install_command: String(form.install_command || "").trim(),
      skill_md_text: String(form.skill_md_text || ""),
      skill_card: skillCard,
      current_version: String(form.current_version || "").trim(),
      license_name: String(form.license_name || "").trim(),
      security_audit_status: String(form.security_audit_status || "").trim(),
      downloads_30d: Number(form.downloads_30d || 0),
      downloads_all_time: Number(form.downloads_all_time || 0),
      last_published_at: String(form.last_published_at || "").trim(),
      files: payloadFiles,
      versions: payloadVersions
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
  const [editorTab, setEditorTab] = useState("basic")
  const [guideExpanded, setGuideExpanded] = useState(false)
  const [selectedFilePath, setSelectedFilePath] = useState("")
  const [confirmDeleteId, setConfirmDeleteId] = useState("")
  const [form, setForm] = useState(createBlankForm())
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

  useEffect(() => {
    const files = Array.isArray(form.files) ? form.files : []
    if (!editorOpen || files.length === 0) {
      setSelectedFilePath("")
      return
    }
    const previewable = files.find(file => String(file.path || "") === "SKILL.md")
      || files.find(file => file.entry_type === "file")
      || files[0]
    setSelectedFilePath(prev => {
      const stillExists = files.some(file => file.path === prev)
      return stillExists && prev ? prev : String(previewable.path || "")
    })
  }, [editorOpen, form.files])

  const openCreate = () => {
    setEditingId("")
    setEditorTab("basic")
    setGuideExpanded(false)
    setSelectedFilePath("")
    setForm(createBlankForm())
    setFormErrors({})
    setError("")
    setMessage("")
    setEditorOpen(true)
  }

  const openCreateWithTemplate = () => {
    setEditingId("")
    setEditorTab("basic")
    setGuideExpanded(false)
    setSelectedFilePath("SKILL.md")
    setForm(createTemplateForm())
    setFormErrors({})
    setError("")
    setMessage(labels.templateLoaded)
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
      setEditorTab("basic")
      setGuideExpanded(false)
      setSelectedFilePath("")
      setForm(normalizeSkillForm(detail))
      setEditorOpen(true)
    } catch (err) {
      setError(err.message || labels.loadDetailFailed)
    } finally {
      setDetailLoading(false)
    }
  }

  const updateFormField = (field, value) => {
    setForm(prev => ({ ...prev, [field]: value }))
  }

  const updateSkillMdText = value => {
    setForm(prev => ({
      ...prev,
      skill_md_text: value,
      files: syncSpecialFiles(prev.files, value, prev.skill_card_text)
    }))
    setSelectedFilePath("SKILL.md")
  }

  const updateSkillCardText = value => {
    setForm(prev => ({
      ...prev,
      skill_card_text: value,
      files: syncSpecialFiles(prev.files, prev.skill_md_text, value)
    }))
    setSelectedFilePath("skill-card.md")
  }

  const addFile = () => {
    setForm(prev => ({
      ...prev,
      files: [
        ...prev.files,
        createEmptyFile({
          path: `references/new-file-${prev.files.length + 1}.md`,
          sort_order: (prev.files.length + 1) * 10
        })
      ]
    }))
  }

  const removeFile = index => {
    setForm(prev => {
      const item = prev.files[index]
      if (!item) return prev
      if (["SKILL.md", "skill-card.md"].includes(String(item.path || ""))) return prev
      const nextFiles = prev.files.filter((_, fileIndex) => fileIndex !== index)
      if (selectedFilePath === item.path) setSelectedFilePath("")
      return { ...prev, files: nextFiles }
    })
  }

  const updateFile = (index, field, value) => {
    setForm(prev => {
      const nextFiles = prev.files.map((item, fileIndex) => {
        if (fileIndex !== index) return { ...item }
        const nextItem = { ...item, [field]: value }
        if (field === "entry_type" && value === "dir") nextItem.content_text = ""
        return nextItem
      })
      const target = nextFiles[index]
      let nextSkillMdText = prev.skill_md_text
      let nextSkillCardText = prev.skill_card_text
      const oldPath = String(prev.files[index]?.path || "")
      const nextPath = String(target?.path || "")
      if (field === "content_text" && nextPath === "SKILL.md") nextSkillMdText = String(value || "")
      if (field === "content_text" && nextPath === "skill-card.md") nextSkillCardText = String(value || "")
      const syncedFiles = syncSpecialFiles(nextFiles, nextSkillMdText, nextSkillCardText)
      if (field === "path" && oldPath === selectedFilePath) setSelectedFilePath(String(value || ""))
      return {
        ...prev,
        skill_md_text: nextSkillMdText,
        skill_card_text: nextSkillCardText,
        files: syncedFiles
      }
    })
  }

  const addVersion = () => {
    setForm(prev => ({
      ...prev,
      versions: [
        ...prev.versions,
        createEmptyVersion({ sort_order: (prev.versions.length + 1) * 10 })
      ]
    }))
  }

  const removeVersion = index => {
    setForm(prev => ({
      ...prev,
      versions: prev.versions.filter((_, versionIndex) => versionIndex !== index)
    }))
  }

  const updateVersion = (index, field, value) => {
    setForm(prev => {
      const nextVersions = prev.versions.map((item, versionIndex) => {
        const nextItem = { ...item }
        if (versionIndex === index) nextItem[field] = value
        if (field === "is_latest" && value && versionIndex !== index) nextItem.is_latest = false
        return nextItem
      })
      return {
        ...prev,
        versions: nextVersions
      }
    })
  }

  const onSubmit = async event => {
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
      setSelectedFilePath("")
      setForm(createBlankForm())
      setFormErrors({})
      await refreshList(1)
    } catch (err) {
      setError(err.message || labels.saveFailed)
    } finally {
      setSaving(false)
    }
  }

  const onToggleStatus = async item => {
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

  const onDelete = async skillId => {
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

  const selectedFile = useMemo(() => {
    if (!Array.isArray(form.files)) return null
    return form.files.find(file => file.path === selectedFilePath) || null
  }, [form.files, selectedFilePath])

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
          <button type="button" onClick={openCreateWithTemplate}>
            {labels.loadTemplate}
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
            <>
              <form onSubmit={onSubmit}>
                <div className="skill-editor-summary">
                  <div className="summary-item"><span className="meta">{labels.id}:</span> {form.id || "-"}</div>
                  <div className="summary-item"><span className="meta">{labels.category}:</span> {form.category || "-"}</div>
                  <div className="summary-item"><span className="meta">{labels.status}:</span> {form.status || "-"}</div>
                  <div className="summary-item"><span className="meta">{labels.currentVersion}:</span> {form.current_version || "-"}</div>
                </div>

                <div className={`skill-template-guide ${guideExpanded ? "" : "collapsed"}`}>
                  <div className="skill-guide-toggle" onClick={() => setGuideExpanded(prev => !prev)}>
                    <div className="title">{labels.templateGuide}</div>
                    <button type="button">{guideExpanded ? labels.guideCollapse : labels.guideExpand}</button>
                  </div>
                  <div className="skill-guide-body">
                    <div className="skill-row-subtext">{labels.templateGuideIntro}</div>
                    <div className="meta">{labels.templateDocPath}: `docs/AI_SKILL_TEMPLATE.md`</div>
                  </div>
                </div>

                <div className="skill-detail-tabs">
                  {["basic", "publishing", "schema", "skill_md", "skill_card", "files", "versions"].map(tabKey => (
                    <button
                      key={tabKey}
                      type="button"
                      className={editorTab === tabKey ? "active" : ""}
                      onClick={() => setEditorTab(tabKey)}
                    >
                      {labels[`tab_${tabKey}`]}
                    </button>
                  ))}
                </div>

                <div className="skill-detail-panel">
                  {editorTab === "basic" && (
                    <div className="skill-detail-block">
                      <div className="skill-form-grid">
                        <label>
                          <span>{labels.id}</span>
                  <input
                    value={form.id}
                    disabled={!!editingId}
                    placeholder="withholding_tax_checker"
                    onChange={e => updateFormField("id", e.target.value)}
                  />
                  <span className="meta">{labels.hintId}</span>
                  {formErrors.id && <span className="form-error">{formErrors.id}</span>}
                </label>

                <label>
                  <span>{labels.displayName}</span>
                  <input
                    value={form.display_name}
                    placeholder="Withholding Tax Checker"
                    onChange={e => updateFormField("display_name", e.target.value)}
                  />
                  <span className="meta">{labels.hintDisplayName}</span>
                  {formErrors.display_name && <span className="form-error">{formErrors.display_name}</span>}
                </label>

                <label>
                  <span>{labels.category}</span>
                  <input
                    list="skill-category-options"
                    value={form.category}
                    placeholder="knowledge"
                    onChange={e => updateFormField("category", e.target.value)}
                  />
                  <datalist id="skill-category-options">
                    {knownCategories.map(item => (
                      <option key={item} value={item} />
                    ))}
                  </datalist>
                  <span className="meta">{labels.hintCategory}</span>
                  {formErrors.category && <span className="form-error">{formErrors.category}</span>}
                </label>

                <label>
                  <span>{labels.scene}</span>
                  <input
                    list="skill-scene-options"
                    value={form.scene}
                    placeholder="tax_contract_audit"
                    onChange={e => updateFormField("scene", e.target.value)}
                  />
                  <span className="meta">{labels.hintScene}</span>
                  {formErrors.scene && <span className="form-error">{formErrors.scene}</span>}
                </label>

                <label>
                  <span>{labels.visibility}</span>
                  <select value={form.visibility} onChange={e => updateFormField("visibility", e.target.value)}>
                    <option value="public">{labels.visibilityPublic}</option>
                    <option value="private">{labels.visibilityPrivate}</option>
                  </select>
                  <span className="meta">{labels.hintVisibility}</span>
                </label>

                <label>
                  <span>{labels.status}</span>
                  <select value={form.status} onChange={e => updateFormField("status", e.target.value)}>
                    <option value="active">{labels.statusActive}</option>
                    <option value="disabled">{labels.statusDisabled}</option>
                  </select>
                  <span className="meta">{labels.hintStatus}</span>
                </label>

                <label>
                  <span>{labels.sortOrder}</span>
                  <input
                    type="number"
                    min="0"
                    max="100000"
                    value={form.sort_order}
                    onChange={e => updateFormField("sort_order", e.target.value)}
                  />
                  <span className="meta">{labels.hintSortOrder}</span>
                </label>

                <label className="wide">
                  <span>{labels.tags}</span>
                  <input
                    value={form.tags_text}
                    placeholder={labels.tagsPlaceholder}
                    onChange={e => updateFormField("tags_text", e.target.value)}
                  />
                  <span className="meta">{labels.hintTags}</span>
                </label>

                <label className="wide">
                  <span>{labels.description}</span>
                  <textarea
                    rows="3"
                    placeholder={labels.hintDescription}
                    value={form.description}
                    onChange={e => updateFormField("description", e.target.value)}
                  />
                </label>

                <label className="wide">
                  <span>{labels.sourceUrl}</span>
                  <input
                    value={form.source_url}
                    placeholder="https://example.com/skill-reference"
                    onChange={e => updateFormField("source_url", e.target.value)}
                  />
                  <span className="meta">{labels.hintSourceUrl}</span>
                </label>

                <label className="wide">
                  <span>{labels.referenceSummary}</span>
                  <textarea
                    rows="3"
                    placeholder={labels.hintReferenceSummary}
                    value={form.reference_summary}
                    onChange={e => updateFormField("reference_summary", e.target.value)}
                  />
                </label>

                      </div>
                    </div>
                  )}

                  {editorTab === "publishing" && (
                    <div className="skill-detail-block">
                      <div className="skill-form-grid">
                        <label>
                          <span>{labels.publisher}</span>
                          <input
                            value={form.publisher_name}
                            placeholder="AI-Law-Assistant"
                            onChange={e => updateFormField("publisher_name", e.target.value)}
                          />
                        </label>

                        <label>
                          <span>{labels.publisherHandle}</span>
                          <input
                            value={form.publisher_handle}
                            placeholder="@your-team"
                            onChange={e => updateFormField("publisher_handle", e.target.value)}
                          />
                        </label>

                        <label>
                          <span>{labels.currentVersion}</span>
                          <input
                            value={form.current_version}
                            placeholder="v0.1.0"
                            onChange={e => updateFormField("current_version", e.target.value)}
                          />
                        </label>

                        <label>
                          <span>{labels.license}</span>
                          <input
                            value={form.license_name}
                            placeholder="Internal Reference"
                            onChange={e => updateFormField("license_name", e.target.value)}
                          />
                        </label>

                        <label>
                          <span>{labels.securityAuditStatus}</span>
                          <input
                            value={form.security_audit_status}
                            placeholder="draft / internal / pass / pending-verification"
                            onChange={e => updateFormField("security_audit_status", e.target.value)}
                          />
                        </label>

                        <label>
                          <span>{labels.lastPublishedAt}</span>
                          <input
                            value={form.last_published_at}
                            placeholder="2026-07-21"
                            onChange={e => updateFormField("last_published_at", e.target.value)}
                          />
                        </label>

                        <label>
                          <span>{labels.downloads30d}</span>
                          <input
                            type="number"
                            min="0"
                            value={form.downloads_30d}
                            onChange={e => updateFormField("downloads_30d", e.target.value)}
                          />
                        </label>

                        <label>
                          <span>{labels.downloads}</span>
                          <input
                            type="number"
                            min="0"
                            value={form.downloads_all_time}
                            onChange={e => updateFormField("downloads_all_time", e.target.value)}
                          />
                        </label>

                        <label className="wide">
                          <span>{labels.installCommand}</span>
                          <input
                            value={form.install_command}
                            placeholder="openclaw skills install @your-team/withholding-tax-checker"
                            onChange={e => updateFormField("install_command", e.target.value)}
                          />
                          <span className="meta">{labels.hintInstallCommand}</span>
                        </label>
                      </div>
                    </div>
                  )}

                  {editorTab === "schema" && (
                    <div className="skill-detail-block">
                      <label className="wide">
                        <span>{labels.inputSchema}</span>
                        <textarea
                          className="skill-code-editor"
                          rows="8"
                          value={form.input_schema_text}
                          onChange={e => updateFormField("input_schema_text", e.target.value)}
                        />
                        <span className="meta">{labels.hintSchema}</span>
                        {formErrors.input_schema_text && <span className="form-error">{formErrors.input_schema_text}</span>}
                      </label>

                      <label className="wide">
                        <span>{labels.outputSchema}</span>
                        <textarea
                          className="skill-code-editor"
                          rows="8"
                          value={form.output_schema_text}
                          onChange={e => updateFormField("output_schema_text", e.target.value)}
                        />
                        {formErrors.output_schema_text && <span className="form-error">{formErrors.output_schema_text}</span>}
                      </label>

                      <label className="wide">
                        <span>{labels.configSchema}</span>
                        <textarea
                          className="skill-code-editor"
                          rows="8"
                          value={form.config_schema_text}
                          onChange={e => updateFormField("config_schema_text", e.target.value)}
                        />
                        {formErrors.config_schema_text && <span className="form-error">{formErrors.config_schema_text}</span>}
                      </label>
                    </div>
                  )}

                  {editorTab === "skill_md" && (
                    <div className="skill-detail-block">
                      <div className="meta">{labels.hintSkillMd}</div>
                      <textarea
                        className="skill-code-editor"
                        rows="20"
                        value={form.skill_md_text}
                        onChange={e => updateSkillMdText(e.target.value)}
                      />
                    </div>
                  )}

                  {editorTab === "skill_card" && (
                    <div className="skill-detail-block">
                      <div className="meta">{labels.hintSkillCard}</div>
                      <textarea
                        className="skill-code-editor"
                        rows="20"
                        value={form.skill_card_text}
                        onChange={e => updateSkillCardText(e.target.value)}
                      />
                      {formErrors.skill_card_text && <span className="form-error">{formErrors.skill_card_text}</span>}
                    </div>
                  )}

                  {editorTab === "files" && (
                    <div className="skill-detail-block">
                      <div className="skill-detail-inline-actions">
                        <div className="meta">{labels.hintFiles}</div>
                        <button type="button" onClick={addFile}>{labels.addFile}</button>
                      </div>
                      {formErrors.files && <div className="form-error">{formErrors.files}</div>}
                      <div className="skill-files-layout">
                        <div className="skill-detail-table-wrap">
                          <table className="admin-table">
                            <thead>
                              <tr>
                                <th>{labels.filePath}</th>
                                <th>{labels.fileType}</th>
                                <th>{labels.fileSize}</th>
                                <th>{labels.fileBranch}</th>
                                <th>{labels.colActions}</th>
                              </tr>
                            </thead>
                            <tbody>
                              {form.files.length === 0 ? (
                                <tr><td colSpan="5">{labels.empty}</td></tr>
                              ) : form.files.map((file, index) => {
                                const locked = ["SKILL.md", "skill-card.md"].includes(String(file.path || ""))
                                return (
                                  <tr
                                    key={`${file.path || "file"}-${index}`}
                                    className={selectedFilePath === file.path ? "skill-file-row-active" : ""}
                                    onClick={() => setSelectedFilePath(file.path)}
                                  >
                                    <td>{file.path}</td>
                                    <td>{file.entry_type}</td>
                                    <td>{formatBytes(file.size_bytes)}</td>
                                    <td>{file.branch_name || "-"}</td>
                                    <td>
                                      <button type="button" onClick={event => { event.stopPropagation(); removeFile(index) }} disabled={locked}>
                                        {labels.removeFile}
                                      </button>
                                    </td>
                                  </tr>
                                )
                              })}
                            </tbody>
                          </table>
                        </div>
                        <div className="skill-file-preview">
                          <div className="meta">{labels.filePreview}</div>
                          {selectedFile ? (
                            <div className="skill-detail-block">
                              <label>
                                <span>{labels.filePath}</span>
                                <input
                                  value={selectedFile.path}
                                  disabled={["SKILL.md", "skill-card.md"].includes(String(selectedFile.path || ""))}
                                  onChange={e => updateFile(form.files.findIndex(file => file.path === selectedFile.path), "path", e.target.value)}
                                />
                              </label>
                              <label>
                                <span>{labels.fileType}</span>
                                <select
                                  value={selectedFile.entry_type}
                                  disabled={["SKILL.md", "skill-card.md"].includes(String(selectedFile.path || ""))}
                                  onChange={e => updateFile(form.files.findIndex(file => file.path === selectedFile.path), "entry_type", e.target.value)}
                                >
                                  <option value="file">file</option>
                                  <option value="dir">dir</option>
                                </select>
                              </label>
                              <label>
                                <span>{labels.fileBranch}</span>
                                <input
                                  value={selectedFile.branch_name || ""}
                                  onChange={e => updateFile(form.files.findIndex(file => file.path === selectedFile.path), "branch_name", e.target.value)}
                                />
                              </label>
                              <label>
                                <span>{labels.sortOrder}</span>
                                <input
                                  type="number"
                                  value={selectedFile.sort_order || 0}
                                  onChange={e => updateFile(form.files.findIndex(file => file.path === selectedFile.path), "sort_order", e.target.value)}
                                />
                              </label>
                              {selectedFile.entry_type === "file" ? (
                                <label className="wide">
                                  <span>{labels.fileContent}</span>
                                  <textarea
                                    className="skill-code-editor"
                                    rows="16"
                                    value={selectedFile.content_text || ""}
                                    onChange={e => updateFile(form.files.findIndex(file => file.path === selectedFile.path), "content_text", e.target.value)}
                                  />
                                </label>
                              ) : (
                                <div className="skill-list-row">{labels.directoryHint}</div>
                              )}
                            </div>
                          ) : (
                            <div className="skill-list-row">{labels.noFilePreview}</div>
                          )}
                        </div>
                      </div>
                    </div>
                  )}

                  {editorTab === "versions" && (
                    <div className="skill-detail-block">
                      <div className="skill-detail-inline-actions">
                        <div className="meta">{labels.hintVersions}</div>
                        <button type="button" onClick={addVersion}>{labels.addVersion}</button>
                      </div>
                      {formErrors.versions && <div className="form-error">{formErrors.versions}</div>}
                      <div className="skill-version-editor-list">
                        {form.versions.length === 0 ? (
                          <div className="skill-list-row">{labels.empty}</div>
                        ) : form.versions.map((version, index) => (
                          <div key={`${version.version_tag || "version"}-${index}`} className="skill-version-editor-card">
                            <div className="skill-detail-inline-actions">
                              <div className="title">{version.version_tag || `${labels.version} ${index + 1}`}</div>
                              <button type="button" onClick={() => removeVersion(index)}>{labels.removeVersion}</button>
                            </div>
                            <div className="skill-form-grid">
                              <label>
                                <span>{labels.version}</span>
                                <input
                                  value={version.version_tag}
                                  placeholder="v0.1.0"
                                  onChange={e => updateVersion(index, "version_tag", e.target.value)}
                                />
                              </label>
                              <label>
                                <span>{labels.releaseLabel}</span>
                                <input
                                  value={version.release_label}
                                  placeholder="Latest"
                                  onChange={e => updateVersion(index, "release_label", e.target.value)}
                                />
                              </label>
                              <label>
                                <span>{labels.releaseDate}</span>
                                <input
                                  value={version.published_at}
                                  placeholder="2026-07-21"
                                  onChange={e => updateVersion(index, "published_at", e.target.value)}
                                />
                              </label>
                              <label>
                                <span>{labels.download}</span>
                                <input
                                  value={version.download_url}
                                  placeholder="https://example.com/download"
                                  onChange={e => updateVersion(index, "download_url", e.target.value)}
                                />
                              </label>
                              <label className="skill-checkbox-label">
                                <input
                                  type="checkbox"
                                  checked={!!version.is_latest}
                                  onChange={e => updateVersion(index, "is_latest", e.target.checked)}
                                />
                                <span>{labels.latest}</span>
                              </label>
                              <label>
                                <span>{labels.sortOrder}</span>
                                <input
                                  type="number"
                                  value={version.sort_order || 0}
                                  onChange={e => updateVersion(index, "sort_order", e.target.value)}
                                />
                              </label>
                              <label className="wide">
                                <span>{labels.changelog}</span>
                                <textarea
                                  rows="6"
                                  placeholder={labels.hintChangelog}
                                  value={version.changelog_text}
                                  onChange={e => updateVersion(index, "changelog_text", e.target.value)}
                                />
                              </label>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                <div className="skill-editor-footer">
                  <button type="submit" disabled={saving}>
                    {saving ? labels.saving : editingId ? labels.saveUpdate : labels.saveCreate}
                  </button>
                  <button type="button" onClick={openCreateWithTemplate} disabled={saving}>
                    {labels.loadTemplate}
                  </button>
                  <button type="button" onClick={() => { setEditorOpen(false) }} disabled={saving}>
                    {labels.cancel}
                  </button>
                </div>
              </form>
            </>
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
                  {item.current_version ? <div className="meta">{labels.currentVersion}: {item.current_version}</div> : null}
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
