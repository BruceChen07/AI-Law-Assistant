import { useState, useEffect, useRef } from "react"
import { adminListDocuments, adminDeleteDocument, adminListUsers, adminUpdateUserRole, adminDeleteUser, adminGetStats, adminGetLLMConfig, adminGetUIConfig, adminUpdateUIConfig, adminGetVectorStoreConfig, adminUpdateVectorStoreConfig, adminCleanupVectorStore, adminTestLLM, adminGetMemoryConfig, adminUpdateMemoryConfig, adminListPrompts, adminGetPrompt, adminCreatePrompt, adminUpdatePrompt, adminDeletePrompt, adminRestorePromptDefault, adminUpdatePromptStatus, adminPreviewPrompt, adminListPromptBindings, adminUpdatePromptBinding, importRegulation, getJob, searchRegulations, getCurrentUser, logout } from "./api"
import TokenMonitor from "./TokenMonitor"
import SkillManager from "./SkillManager"
import { adminI18n } from "./i18n/adminI18n"

const createEmptyPromptForm = () => ({
  prompt_key: "",
  name: "",
  description: "",
  display_system_text: "",
  display_focus_text: "",
  display_template_text: "",
  sample_input_json: '{\n  "contract_title": "Service Agreement",\n  "counterparty": "Acme Corp",\n  "effective_date": "2026-08-28"\n}'
})

const BUILTIN_PROMPT_LABELS = {
  "contract_audit.classic_json": {
    zh: "合同审计结果提示词",
    en: "Contract Audit Result Prompt"
  },
  "audit_prompt.tax_audit_json": {
    zh: "财税审计提示词",
    en: "Tax Audit Prompt"
  },
  "memory_pipeline.clause_audit": {
    zh: "条款审计提示词",
    en: "Clause Audit Prompt"
  },
  "memory_pipeline.flush_markdown": {
    zh: "记忆整理提示词",
    en: "Memory Summary Prompt"
  },
  "tax_risk.issue_generation": {
    zh: "税务风险建议提示词",
    en: "Tax Risk Suggestion Prompt"
  },
  "tax_matcher.clause_rule_match": {
    zh: "条款法规匹配提示词",
    en: "Clause Rule Match Prompt"
  },
  "tax_contract_parser.entity_extract": {
    zh: "合同实体抽取提示词",
    en: "Contract Entity Extraction Prompt"
  },
  "admin.llm_test": {
    zh: "模型测试提示词",
    en: "Model Test Prompt"
  }
}

export default function Admin({ onBack, lang, onLangChange }) {
  const [tab, setTab] = useState("documents")
  const [documents, setDocuments] = useState([])
  const [users, setUsers] = useState([])
  const [stats, setStats] = useState(null)
  const [loading, setLoading] = useState(false)
  const [pagination, setPagination] = useState({ page: 1, page_size: 10, total: 0 })
  const [search, setSearch] = useState("")
  const [docCategory, setDocCategory] = useState("contract")
  const [deleteConfirm, setDeleteConfirm] = useState(null)
  const [userDeleteConfirm, setUserDeleteConfirm] = useState(null)
  const [llmConfig, setLlmConfig] = useState(null)
  const [llmError, setLlmError] = useState("")
  const [llmTestPrompt, setLlmTestPrompt] = useState("")
  const [llmTestResult, setLlmTestResult] = useState("")
  const [llmTestError, setLlmTestError] = useState("")
  const [llmTesting, setLlmTesting] = useState(false)
  const [uiConfig, setUiConfig] = useState({ showCitationSource: false, defaultTheme: "dark" })
  const [uiSaving, setUiSaving] = useState(false)
  const [uiError, setUiError] = useState("")
  const [memoryConfig, setMemoryConfig] = useState({
    memory_module_enabled: true,
    memory_mode_when_disabled: "classic",
    memory_token_guard_enabled: true,
    memory_max_llm_calls_per_audit: 12,
    memory_max_prompt_chars_per_clause: 2400,
    risk_notice: ""
  })
  const [memorySaving, setMemorySaving] = useState(false)
  const [memoryMsg, setMemoryMsg] = useState("")
  const [vectorStoreConfig, setVectorStoreConfig] = useState(null)
  const [vectorStoreSaving, setVectorStoreSaving] = useState(false)
  const [vectorStoreMsg, setVectorStoreMsg] = useState("")
  const [regUpload, setRegUpload] = useState({
    title: "",
    doc_no: "",
    issuer: "",
    status: "current",
    effective_date: "",
    expiry_date: "",
    region: "",
    industry: "",
    language: lang || "zh"
  })
  const [regFile, setRegFile] = useState(null)
  const [regFileError, setRegFileError] = useState("")
  const [jobId, setJobId] = useState("")
  const [jobStatus, setJobStatus] = useState("")
  const [regQuery, setRegQuery] = useState({ query: "", language: lang || "zh", top_k: 10, date: "", region: "", industry: "", use_semantic: true, semantic_weight: 0.6, bm25_weight: 0.4, candidate_size: 50, rerank_enabled: true, rerank_top_n: 50 })
  const [regResults, setRegResults] = useState([])
  const [regSearching, setRegSearching] = useState(false)
  const [regHasSearched, setRegHasSearched] = useState(false)
  const [regSearchError, setRegSearchError] = useState("")
  const [regShowAdvanced, setRegShowAdvanced] = useState(false)
  const [regExpanded, setRegExpanded] = useState({})
  const [prompts, setPrompts] = useState([])
  const [promptPagination, setPromptPagination] = useState({ page: 1, page_size: 20, total: 0 })
  const [promptSearch, setPromptSearch] = useState("")
  const [promptStatusFilter, setPromptStatusFilter] = useState("all")
  const [promptLoading, setPromptLoading] = useState(false)
  const [promptSaving, setPromptSaving] = useState(false)
  const [promptMessage, setPromptMessage] = useState("")
  const [promptError, setPromptError] = useState("")
  const [promptForm, setPromptForm] = useState(createEmptyPromptForm)
  const [promptFormErrors, setPromptFormErrors] = useState({})
  const [promptEditingId, setPromptEditingId] = useState("")
  const [promptDeleteConfirm, setPromptDeleteConfirm] = useState("")
  const [promptStatusUpdatingId, setPromptStatusUpdatingId] = useState("")
  const [promptPreviewSystem, setPromptPreviewSystem] = useState("")
  const [promptPreviewUser, setPromptPreviewUser] = useState("")
  const [promptPreviewError, setPromptPreviewError] = useState("")
  const [promptPreviewLoading, setPromptPreviewLoading] = useState(false)
  const [promptRestoreLoading, setPromptRestoreLoading] = useState(false)
  const [promptBindings, setPromptBindings] = useState([])
  const [promptBindingUpdatingScene, setPromptBindingUpdatingScene] = useState("")
  const [promptEditingSource, setPromptEditingSource] = useState("custom")
  const [promptViewingId, setPromptViewingId] = useState("")
  const [promptViewDetail, setPromptViewDetail] = useState(null)
  const [promptViewLoading, setPromptViewLoading] = useState(false)
  const [promptViewError, setPromptViewError] = useState("")
  const [promptViewPreviewSystem, setPromptViewPreviewSystem] = useState("")
  const [promptViewPreviewUser, setPromptViewPreviewUser] = useState("")
  const [promptViewPreviewError, setPromptViewPreviewError] = useState("")
  const [promptViewPreviewLoading, setPromptViewPreviewLoading] = useState(false)
  
  const fileInputRef = useRef(null)

  const user = getCurrentUser()
  const admin = !!(user && (user.role === "admin" || user.username === "admin"))
  const t = adminI18n[lang] || adminI18n.zh
  const getPromptDisplayName = (prompt) => {
    const key = String(prompt?.prompt_key || prompt?.id || "").trim()
    const localized = BUILTIN_PROMPT_LABELS[key]
    return localized?.[lang || "zh"] || String(prompt?.name || "").trim() || "-"
  }
  const skillLabels = {
    title: t.skillTitle,
    subtitle: t.skillSubtitle,
    create: t.skillCreate,
    createTitle: t.skillCreateTitle,
    editTitle: t.skillEditTitle,
    close: t.skillClose,
    refresh: t.skillRefresh,
    refreshing: t.skillRefreshing,
    search: t.skillSearch,
    searchPlaceholder: t.skillSearchPlaceholder,
    searchButton: t.skillSearchButton,
    reset: t.skillReset,
    allCategories: t.skillAllCategories,
    allStatuses: t.skillAllStatuses,
    scene: t.skillScene,
    scenePlaceholder: t.skillScenePlaceholder,
    id: t.skillId,
    displayName: t.skillDisplayName,
    category: t.colCategory,
    visibility: t.skillVisibility,
    visibilityPublic: t.skillVisibilityPublic,
    visibilityPrivate: t.skillVisibilityPrivate,
    status: t.skillStatus,
    statusActive: t.skillStatusActive,
    statusDisabled: t.skillStatusDisabled,
    statusDeleted: t.skillStatusDeleted,
    sortOrder: t.skillSortOrder,
    tags: t.skillTags,
    tagsPlaceholder: t.skillTagsPlaceholder,
    description: t.skillDescription,
    sourceUrl: t.skillSourceUrl,
    referenceSummary: t.skillReferenceSummary,
    inputSchema: t.skillInputSchema,
    outputSchema: t.skillOutputSchema,
    configSchema: t.skillConfigSchema,
    references: t.skillReferences,
    updatedAt: t.skillUpdatedAt,
    empty: t.skillEmpty,
    edit: t.skillEdit,
    enable: t.skillEnable,
    disable: t.skillDisable,
    saveCreate: t.skillSaveCreate,
    saveUpdate: t.skillSaveUpdate,
    saving: t.skillSaving,
    createSuccess: t.skillCreateSuccess,
    updateSuccess: t.skillUpdateSuccess,
    deleteSuccess: t.skillDeleteSuccess,
    enableSuccess: t.skillEnableSuccess,
    disableSuccess: t.skillDisableSuccess,
    loadFailed: t.skillLoadFailed,
    loadDetailFailed: t.skillLoadDetailFailed,
    saveFailed: t.skillSaveFailed,
    deleteFailed: t.skillDeleteFailed,
    statusFailed: t.skillStatusFailed,
    loadingDetail: t.skillLoadingDetail,
    idRequired: t.skillIdRequired,
    idFormat: t.skillIdFormat,
    displayNameRequired: t.skillDisplayNameRequired,
    categoryRequired: t.skillCategoryRequired,
    sceneRequired: t.skillSceneRequired,
    pageInfo: t.skillPageInfo,
    colName: t.skillDisplayName,
    colActions: t.colActions,
    previous: t.previous,
    next: t.next,
    loading: t.loading,
    confirm: t.confirm,
    cancel: t.cancel,
    publisher: t.skillPublisher,
    currentVersion: t.skillCurrentVersion,
    license: t.skillLicense,
    downloads: t.skillDownloads,
    downloads30d: t.skillDownloads30d,
    loadTemplate: t.skillLoadTemplate,
    templateLoaded: t.skillTemplateLoaded,
    templateGuide: t.skillTemplateGuide,
    templateGuideIntro: t.skillTemplateGuideIntro,
    templateDocPath: t.skillTemplateDocPath,
    basicSection: t.skillBasicSection,
    detailSection: t.skillDetailSection,
    tab_basic: t.skillTabBasic,
    tab_publishing: t.skillTabPublishing,
    tab_schema: t.skillTabSchema,
    tab_overview: t.skillTab_overview,
    tab_skill_md: t.skillTab_skill_md,
    tab_skill_card: t.skillTab_skill_card,
    tab_files: t.skillTab_files,
    tab_versions: t.skillTab_versions,
    publisherHandle: t.skillPublisherHandle,
    securityAuditStatus: t.skillSecurityAuditStatus,
    reviewBeforeUse: t.skillReviewBeforeUse,
    risk: t.skillRisk,
    mitigation: t.skillMitigation,
    filePath: t.skillFilePath,
    fileType: t.skillFileType,
    fileSize: t.skillFileSize,
    fileBranch: t.skillFileBranch,
    fileContent: t.skillFileContent,
    directoryHint: t.skillDirectoryHint,
    filePathRequired: t.skillFilePathRequired,
    filePathDuplicate: t.skillFilePathDuplicate,
    version: t.skillVersion,
    releaseDate: t.skillReleaseDate,
    releaseLabel: t.skillReleaseLabel,
    changelog: t.skillChangelog,
    versionTagRequired: t.skillVersionTagRequired,
    versionTagDuplicate: t.skillVersionTagDuplicate,
    latest: t.skillLatest,
    lastPublishedAt: t.skillLastPublishedAt,
    useType: t.skillUseType,
    useCase: t.skillUseCase,
    referencesTitle: t.skillReferencesTitle,
    outputBehavior: t.skillOutputBehavior,
    filePreview: t.skillFilePreview,
    noFilePreview: t.skillNoFilePreview,
    download: t.skillDownload,
    installCommand: t.skillInstallCommand,
    addFile: t.skillAddFile,
    removeFile: t.skillRemoveFile,
    addVersion: t.skillAddVersion,
    removeVersion: t.skillRemoveVersion,
    hintId: t.skillHintId,
    hintDisplayName: t.skillHintDisplayName,
    hintCategory: t.skillHintCategory,
    hintScene: t.skillHintScene,
    hintVisibility: t.skillHintVisibility,
    hintStatus: t.skillHintStatus,
    hintSortOrder: t.skillHintSortOrder,
    hintTags: t.skillHintTags,
    hintDescription: t.skillHintDescription,
    hintSourceUrl: t.skillHintSourceUrl,
    hintReferenceSummary: t.skillHintReferenceSummary,
    hintInstallCommand: t.skillHintInstallCommand,
    hintSchema: t.skillHintSchema,
    hintSkillMd: t.skillHintSkillMd,
    hintSkillCard: t.skillHintSkillCard,
    hintFiles: t.skillHintFiles,
    hintVersions: t.skillHintVersions,
    hintChangelog: t.skillHintChangelog,
    guideExpand: t.skillGuideExpand,
    guideCollapse: t.skillGuideCollapse
  }
  
  useEffect(() => {
    if (!admin) return
    if (tab === "documents") loadDocuments()
    if (tab === "users") loadUsers()
    if (tab === "stats") loadStats()
    if (tab === "prompts") {
      loadPrompts()
      loadPromptBindings()
    }
    if (tab === "model") {
      loadLLM()
      loadUIConfig()
      loadVectorStoreConfig()
      loadMemoryConfig()
    }
    if (tab === "regulations") {}
  }, [tab, pagination.page, docCategory, promptPagination.page, promptStatusFilter])

  useEffect(() => {
    setRegUpload(prev => ({ ...prev, language: lang || "zh" }))
    setRegQuery(prev => ({ ...prev, language: lang || "zh" }))
    setLlmTestPrompt(prev => prev || t.llmTestDefaultPrompt)
  }, [lang])

  useEffect(() => {
    if (tab !== "prompts") return
    if (!promptForm.display_template_text.trim()) {
      setPromptPreviewSystem("")
      setPromptPreviewUser("")
      setPromptPreviewError("")
      setPromptPreviewLoading(false)
      return
    }
    const timer = setTimeout(async () => {
      setPromptPreviewLoading(true)
      setPromptPreviewError("")
      try {
        const res = await adminPreviewPrompt({
          prompt_id: promptEditingId,
          prompt_key: promptForm.prompt_key,
          editor_lang: lang || "zh",
          display_system_text: promptForm.display_system_text,
          display_focus_text: promptForm.display_focus_text,
          display_template_text: promptForm.display_template_text,
          sample_input_json: promptForm.sample_input_json
        })
        setPromptPreviewSystem(res.rendered_system_text || "")
        setPromptPreviewUser(res.rendered_text || "")
      } catch (err) {
        setPromptPreviewSystem("")
        setPromptPreviewUser("")
        setPromptPreviewError(err.message)
      } finally {
        setPromptPreviewLoading(false)
      }
    }, 350)
    return () => clearTimeout(timer)
  }, [tab, lang, promptEditingId, promptForm.prompt_key, promptForm.display_system_text, promptForm.display_focus_text, promptForm.display_template_text, promptForm.sample_input_json])

  useEffect(() => {
    if (tab !== "prompts" || !promptEditingId) return
    openPromptEdit(promptEditingId)
  }, [lang])

  useEffect(() => {
    if (tab !== "prompts") return undefined
    const timer = window.setInterval(() => {
      loadPromptBindings({ silent: true })
    }, 15000)
    return () => window.clearInterval(timer)
  }, [tab])
  
  const loadDocuments = async () => {
    setLoading(true)
    try {
      const data = await adminListDocuments({
        page: pagination.page,
        page_size: pagination.page_size,
        search: search,
        category: docCategory
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

  const loadLLM = async () => {
    setLoading(true)
    try {
      const data = await adminGetLLMConfig()
      setLlmConfig({
        provider: data.provider || "openai_compatible",
        provider_label: data.provider_label || data.provider || "openai_compatible",
        api_base: data.api_base || "",
        model: data.model || "",
        temperature: data.temperature ?? 0.2,
        max_tokens: data.max_tokens ?? 2048,
        timeout: data.timeout ?? 60,
        headers: data.headers || {},
        env_managed: data.env_managed !== false,
        supported_env_vars: Array.isArray(data.supported_env_vars) ? data.supported_env_vars : [],
        required_env_vars: Array.isArray(data.required_env_vars) ? data.required_env_vars : [],
        missing_env_vars: Array.isArray(data.missing_env_vars) ? data.missing_env_vars : [],
        env_sources: data.env_sources || {},
        security_issues: Array.isArray(data.security_issues) ? data.security_issues : [],
        route: data.route || {}
      })
      setLlmTestPrompt(prev => prev || t.llmTestDefaultPrompt)
      setLlmTestResult("")
      setLlmTestError("")
      setLlmError("")
    } catch (err) {
      setLlmError(err.message)
    } finally {
      setLoading(false)
    }
  }

  const loadUIConfig = async () => {
    try {
      const data = await adminGetUIConfig()
      setUiConfig({
        showCitationSource: !!data.show_citation_source,
        defaultTheme: String(data?.default_theme || "").toLowerCase() === "light" ? "light" : "dark"
      })
      setUiError("")
    } catch (err) {
      setUiError(err.message)
    }
  }

  const loadVectorStoreConfig = async () => {
    try {
      const data = await adminGetVectorStoreConfig()
      setVectorStoreConfig(data)
      setVectorStoreMsg("")
    } catch (err) {
      setVectorStoreMsg(err.message)
    }
  }

  const loadMemoryConfig = async () => {
    try {
      const data = await adminGetMemoryConfig()
      setMemoryConfig({
        memory_module_enabled: !!data.memory_module_enabled,
        memory_mode_when_disabled: String(data.memory_mode_when_disabled || "classic"),
        memory_token_guard_enabled: !!data.memory_token_guard_enabled,
        memory_max_llm_calls_per_audit: Number(data.memory_max_llm_calls_per_audit || 12),
        memory_max_prompt_chars_per_clause: Number(data.memory_max_prompt_chars_per_clause || 2400),
        risk_notice: String(data.risk_notice || "")
      })
      setMemoryMsg("")
    } catch (err) {
      setMemoryMsg(String(err?.message || err || "load memory config failed"))
    }
  }

  const loadPrompts = async (override = {}) => {
    setPromptLoading(true)
    setPromptError("")
    try {
      const page = override.page ?? promptPagination.page
      const searchValue = String(override.search ?? promptSearch).trim()
      const statusValue = override.status ?? promptStatusFilter
      const params = {
        page,
        page_size: promptPagination.page_size
      }
      if (searchValue) params.search = searchValue
      if (statusValue && statusValue !== "all") params.status = statusValue
      const data = await adminListPrompts(params)
      setPrompts(Array.isArray(data.items) ? data.items : [])
      setPromptPagination(prev => ({
        ...prev,
        page: Number(data.page || page),
        total: Number(data.total || 0)
      }))
    } catch (err) {
      setPromptError(err.message)
    } finally {
      setPromptLoading(false)
    }
  }

  const loadPromptBindings = async ({ silent = false } = {}) => {
    try {
      const data = await adminListPromptBindings()
      setPromptBindings(Array.isArray(data.items) ? data.items : [])
    } catch (err) {
      if (!silent) setPromptError(err.message)
    }
  }

  const refreshPromptWorkspace = async (override = {}) => {
    await Promise.all([
      loadPrompts(override),
      loadPromptBindings()
    ])
  }

  const resetPromptEditor = (nextPrompt = null) => {
    if (nextPrompt) {
      setPromptEditingId(nextPrompt.id || "")
      setPromptEditingSource(nextPrompt.source || "custom")
      setPromptForm({
        prompt_key: nextPrompt.prompt_key || "",
        name: nextPrompt.name || "",
        description: nextPrompt.description || "",
        display_system_text: nextPrompt.display_system_text || "",
        display_focus_text: nextPrompt.display_focus_text || "",
        display_template_text: nextPrompt.display_template_text || "",
        sample_input_json: nextPrompt.sample_input_json || "{}"
      })
      return
    }
    setPromptEditingId("")
    setPromptEditingSource("custom")
    setPromptForm(createEmptyPromptForm())
    setPromptPreviewSystem("")
    setPromptPreviewUser("")
    setPromptPreviewError("")
    setPromptDeleteConfirm("")
  }

  const validatePromptForm = () => {
    const nextErrors = {}
    if (!String(promptForm.name || "").trim()) nextErrors.name = t.promptRequired
    if (!String(promptForm.display_template_text || "").trim()) nextErrors.display_template_text = t.promptRequired
    setPromptFormErrors(nextErrors)
    return Object.keys(nextErrors).length === 0
  }

  const openPromptCreate = () => {
    setPromptMessage("")
    setPromptError("")
    setPromptFormErrors({})
    resetPromptEditor()
  }

  const openPromptEdit = async (promptId) => {
    setPromptLoading(true)
    setPromptError("")
    setPromptMessage("")
    setPromptFormErrors({})
    try {
      const data = await adminGetPrompt(promptId, lang || "zh")
      resetPromptEditor(data)
    } catch (err) {
      setPromptError(err.message)
    } finally {
      setPromptLoading(false)
    }
  }

  const closePromptView = () => {
    setPromptViewingId("")
    setPromptViewDetail(null)
    setPromptViewLoading(false)
    setPromptViewError("")
    setPromptViewPreviewSystem("")
    setPromptViewPreviewUser("")
    setPromptViewPreviewError("")
    setPromptViewPreviewLoading(false)
  }

  const openPromptView = async (promptId) => {
    setPromptViewingId(promptId)
    setPromptViewDetail(null)
    setPromptViewLoading(true)
    setPromptViewError("")
    setPromptViewPreviewSystem("")
    setPromptViewPreviewUser("")
    setPromptViewPreviewError("")
    setPromptViewPreviewLoading(false)
    try {
      const data = await adminGetPrompt(promptId, lang || "zh")
      setPromptViewDetail(data)
      setPromptViewPreviewLoading(true)
      try {
        const preview = await adminPreviewPrompt({
          prompt_id: data.id || promptId,
          prompt_key: data.prompt_key || "",
          editor_lang: lang || "zh",
          display_system_text: data.display_system_text || "",
          display_focus_text: data.display_focus_text || "",
          display_template_text: data.display_template_text || "",
          sample_input_json: data.sample_input_json || "{}"
        })
        setPromptViewPreviewSystem(preview.rendered_system_text || "")
        setPromptViewPreviewUser(preview.rendered_text || "")
      } catch (previewErr) {
        setPromptViewPreviewError(previewErr.message)
      } finally {
        setPromptViewPreviewLoading(false)
      }
    } catch (err) {
      setPromptViewError(err.message)
    } finally {
      setPromptViewLoading(false)
    }
  }

  const savePrompt = async () => {
    setPromptMessage("")
    setPromptError("")
    if (!validatePromptForm()) {
      setPromptError(t.promptFormInvalid)
      return
    }
    setPromptSaving(true)
    try {
      const payload = {
        name: String(promptForm.name || "").trim(),
        description: String(promptForm.description || "").trim(),
        prompt_key: String(promptForm.prompt_key || "").trim(),
        editor_lang: lang || "zh",
        display_system_text: String(promptForm.display_system_text || ""),
        display_focus_text: String(promptForm.display_focus_text || ""),
        display_template_text: String(promptForm.display_template_text || ""),
        sample_input_json: String(promptForm.sample_input_json || "{}").trim() || "{}"
      }
      const saved = promptEditingId
        ? await adminUpdatePrompt(promptEditingId, payload)
        : await adminCreatePrompt(payload)
      resetPromptEditor(saved)
      setPromptMessage(t.promptSaved)
      await refreshPromptWorkspace()
    } catch (err) {
      setPromptError(err.message)
    } finally {
      setPromptSaving(false)
    }
  }

  const restorePromptDefault = async () => {
    if (!promptEditingId) return
    setPromptRestoreLoading(true)
    setPromptMessage("")
    setPromptError("")
    try {
      const restored = await adminRestorePromptDefault(promptEditingId, lang || "zh")
      resetPromptEditor(restored)
      setPromptMessage(t.promptRestored)
      await refreshPromptWorkspace()
    } catch (err) {
      setPromptError(err.message)
    } finally {
      setPromptRestoreLoading(false)
    }
  }

  const togglePromptStatus = async (prompt) => {
    setPromptStatusUpdatingId(prompt.id)
    setPromptMessage("")
    setPromptError("")
    try {
      await adminUpdatePromptStatus(prompt.id, prompt.status !== "enabled")
      await refreshPromptWorkspace()
      if (promptEditingId === prompt.id) {
        const latest = await adminGetPrompt(prompt.id, lang || "zh")
        resetPromptEditor(latest)
      }
    } catch (err) {
      setPromptError(err.message)
    } finally {
      setPromptStatusUpdatingId("")
    }
  }

  const removePrompt = async (promptId) => {
    setPromptMessage("")
    setPromptError("")
    try {
      await adminDeletePrompt(promptId)
      setPromptDeleteConfirm("")
      if (promptEditingId === promptId) resetPromptEditor()
      setPromptMessage(t.promptDeleted)
      await refreshPromptWorkspace({
        page: prompts.length === 1 && promptPagination.page > 1
          ? promptPagination.page - 1
          : promptPagination.page
      })
    } catch (err) {
      setPromptError(err.message)
    }
  }

  const submitPromptSearch = async (e) => {
    e.preventDefault()
    setPromptPagination(prev => ({ ...prev, page: 1 }))
    await refreshPromptWorkspace({ page: 1 })
  }

  const updatePromptBinding = async (sceneKey, promptId = "") => {
    if (!sceneKey) return
    setPromptBindingUpdatingScene(sceneKey)
    setPromptMessage("")
    setPromptError("")
    try {
      await adminUpdatePromptBinding(sceneKey, promptId)
      setPromptMessage(t.promptBindingSaved)
      await refreshPromptWorkspace()
    } catch (err) {
      setPromptError(err.message)
    } finally {
      setPromptBindingUpdatingScene("")
    }
  }

  const saveVectorStoreConfig = async () => {
    setVectorStoreSaving(true)
    setVectorStoreMsg("")
    try {
      await adminUpdateVectorStoreConfig({ engine: vectorStoreConfig.engine })
      setVectorStoreMsg(lang === "zh" ? "向量库引擎配置已保存并开始异步迁移" : "Vector store config saved, async migration started")
    } catch (err) {
      setVectorStoreMsg(err.message)
    } finally {
      setVectorStoreSaving(false)
    }
  }

  const cleanupVectorStore = async () => {
    try {
      const res = await adminCleanupVectorStore()
      setVectorStoreMsg(lang === "zh" ? `清理完成，删除了 ${res.deleted_records} 条数据` : `Cleanup done, deleted ${res.deleted_records} records`)
    } catch (err) {
      setVectorStoreMsg(err.message)
    }
  }

  const testLLM = async () => {
    if (!llmConfig) return
    if (Array.isArray(llmConfig.missing_env_vars) && llmConfig.missing_env_vars.length > 0) {
      setLlmTestError(`${t.llmMissingEnv}: ${llmConfig.missing_env_vars.join(", ")}`)
      return
    }
    const prompt = (llmTestPrompt || t.llmTestDefaultPrompt).trim()
    setLlmTesting(true)
    setLlmTestResult("")
    setLlmTestError("")
    try {
      const res = await adminTestLLM({ prompt })
      setLlmTestResult(res.answer || "")
    } catch (err) {
      setLlmTestError(err.message)
    } finally {
      setLlmTesting(false)
    }
  }

  const saveUIConfig = async () => {
    setUiSaving(true)
    try {
      await adminUpdateUIConfig({
        show_citation_source: !!uiConfig.showCitationSource,
        default_theme: uiConfig.defaultTheme === "light" ? "light" : "dark"
      })
      setUiError(t.saved)
    } catch (err) {
      setUiError(err.message)
    } finally {
      setUiSaving(false)
    }
  }

  const saveMemoryConfig = async () => {
    setMemorySaving(true)
    setMemoryMsg("")
    try {
      const payload = {
        memory_module_enabled: !!memoryConfig.memory_module_enabled,
        memory_mode_when_disabled: "classic",
        memory_token_guard_enabled: !!memoryConfig.memory_token_guard_enabled,
        memory_max_llm_calls_per_audit: Number(memoryConfig.memory_max_llm_calls_per_audit || 12),
        memory_max_prompt_chars_per_clause: Number(memoryConfig.memory_max_prompt_chars_per_clause || 2400)
      }
      const next = await adminUpdateMemoryConfig(payload)
      setMemoryConfig(prev => ({
        ...prev,
        ...next,
        memory_max_llm_calls_per_audit: Number(next.memory_max_llm_calls_per_audit || prev.memory_max_llm_calls_per_audit),
        memory_max_prompt_chars_per_clause: Number(next.memory_max_prompt_chars_per_clause || prev.memory_max_prompt_chars_per_clause),
        risk_notice: String(next.risk_notice || prev.risk_notice || "")
      }))
      setMemoryMsg(t.memoryConfigSaved)
    } catch (err) {
      setMemoryMsg(String(err?.message || err || "save memory config failed"))
    } finally {
      setMemorySaving(false)
    }
  }

  const onRegUpload = async (e) => {
    e.preventDefault()
    if (!regFile) return
    const ext = regFile.name.toLowerCase().split(".").pop()
    if (!["docx", "pdf"].includes(ext)) {
      setRegFileError(t.invalidFileType)
      return
    }
    setRegFileError("")
    const form = new FormData()
    Object.entries(regUpload).forEach(([k, v]) => form.append(k, v))
    form.append("file", regFile)
    const res = await importRegulation(form)
    setJobId(res.job_id)
    setJobStatus("running")
  }

  const onRegCheck = async () => {
    if (!jobId) return
    const res = await getJob(jobId)
    setJobStatus(res.status)
  }

  const onRegSearch = async (e) => {
    e.preventDefault()
    const payload = { ...regQuery }
    if (!payload.date) delete payload.date
    if (!payload.region) delete payload.region
    if (!payload.industry) delete payload.industry
    setRegSearching(true)
    setRegHasSearched(true)
    setRegSearchError("")
    setRegExpanded({})
    try {
      const res = await searchRegulations(payload)
      setRegResults(Array.isArray(res) ? res : [])
    } catch (err) {
      setRegResults([])
      setRegSearchError(String(err?.message || err || "Search failed"))
    } finally {
      setRegSearching(false)
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

  const handleUserDelete = async (userId) => {
    try {
      await adminDeleteUser(userId)
      setUserDeleteConfirm(null)
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

  const getRegResultKey = (r, i) => String(r?.citation_id || `${r?.regulation_id || "reg"}:${r?.article_id || i}`)

  const getRegSummary = (text) => {
    const s = String(text || "")
    if (s.length <= 300) return s
    return `${s.slice(0, 300)}...`
  }

  const getPromptStatusLabel = (status) => (
    status === "enabled" ? t.promptStatusEnabled : t.promptStatusDisabled
  )

  const currentPromptBinding = Array.isArray(promptBindings) && promptBindings.length > 0
    ? promptBindings[0]
    : null
  const currentBindingSceneKey = currentPromptBinding?.scene_key || ""
  const currentBindingPromptId = currentPromptBinding?.active_prompt?.id || ""
  const promptEditingIsSystem = promptEditingSource === "system"
  const promptEditingStatus = prompts.find(item => item.id === promptEditingId)?.status || "enabled"
  const promptEditorLanguageLabel = lang === "en" ? t.promptLangEnglish : t.promptLangChinese
  const promptEditorDisplayName = getPromptDisplayName({
    id: promptEditingId,
    prompt_key: promptForm.prompt_key,
    name: promptForm.name
  })
  const promptViewStatus = promptViewDetail?.status || "enabled"
  const promptViewSourceLabel = promptViewDetail?.source === "system" || promptViewDetail?.managed_scope === "system"
    ? t.promptSourceSystem
    : t.promptSourceCustom
  
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
    <div className="page admin-shell">
      <div className="admin-topbar">
        <h1>{t.title}</h1>
        <div className="admin-topbar-actions">
          <label className="admin-toolbar-pill">
            <span>{t.uiLanguage}</span>
            <select value={lang || "zh"} onChange={e => onLangChange?.(e.target.value)}>
              <option value="zh">{t.langOptionZh}</option>
              <option value="en">{t.langOptionEn}</option>
            </select>
          </label>
          <button onClick={onBack || (() => window.history.back())}>{t.back}</button>
          <span>{t.welcome}, {user?.username} </span>
          <button onClick={logout}>{t.logout}</button>
        </div>
      </div>
      
      <div className="tabs">
        <button className={tab === "stats" ? "active" : ""} onClick={() => setTab("stats")}>{t.tabStats}</button>
        <button className={tab === "documents" ? "active" : ""} onClick={() => setTab("documents")}>{t.tabDocs}</button>
        <button className={tab === "users" ? "active" : ""} onClick={() => setTab("users")}>{t.tabUsers}</button>
        <button className={tab === "prompts" ? "active" : ""} onClick={() => setTab("prompts")}>{t.tabPrompts}</button>
        <button className={tab === "model" ? "active" : ""} onClick={() => setTab("model")}>{t.tabModel}</button>
        <button className={tab === "skills" ? "active" : ""} onClick={() => setTab("skills")}>{t.tabSkills}</button>
        <button className={tab === "regulations" ? "active" : ""} onClick={() => setTab("regulations")}>{t.tabRegulations}</button>
        <button className={tab === "token-monitor" ? "active" : ""} onClick={() => setTab("token-monitor")}>{t.tabTokenMonitor}</button>
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
          <div className="tabs" style={{ marginBottom: 12 }}>
            <button
              className={docCategory === "contract" ? "active" : ""}
              onClick={() => {
                setDocCategory("contract")
                setPagination(prev => ({ ...prev, page: 1 }))
              }}
            >
              {t.docsContract}
            </button>
            <button
              className={docCategory === "legal" ? "active" : ""}
              onClick={() => {
                setDocCategory("legal")
                setPagination(prev => ({ ...prev, page: 1 }))
              }}
            >
              {t.docsLegal}
            </button>
          </div>
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
                    <td>{doc.category || (docCategory === "legal" ? "legal" : "-")}</td>
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

      {tab === "prompts" && (
        <div className="card">
          <div className="prompt-header">
            <div>
              <h2>{t.promptMgmt}</h2>
              <p className="meta">{t.promptBusinessHint}</p>
            </div>
            <div className="row">
              <button onClick={openPromptCreate}>{t.promptCreate}</button>
              <button onClick={() => refreshPromptWorkspace()}>{t.promptRefresh}</button>
            </div>
          </div>

          <div className="prompt-layout">
            <div className="prompt-panel">
              <form className="prompt-toolbar" onSubmit={submitPromptSearch}>
                <input
                  placeholder={t.promptSearchPlaceholder}
                  value={promptSearch}
                  onChange={e => setPromptSearch(e.target.value)}
                />
                <select
                  value={promptStatusFilter}
                  onChange={e => {
                    setPromptStatusFilter(e.target.value)
                    setPromptPagination(prev => ({ ...prev, page: 1 }))
                  }}
                >
                  <option value="all">{t.promptSearchStatusAll}</option>
                  <option value="enabled">{t.promptSearchStatusEnabled}</option>
                  <option value="disabled">{t.promptSearchStatusDisabled}</option>
                </select>
                <button type="submit">{t.searchBtn}</button>
              </form>

              {promptError && <div className="prompt-banner error">{promptError}</div>}
              {promptMessage && <div className="prompt-banner success">{promptMessage}</div>}

              {promptLoading ? <p>{t.loading}</p> : prompts.length === 0 ? (
                <div className="prompt-empty">{t.promptEmpty}</div>
              ) : (
                <>
                  <table className="admin-table">
                    <thead>
                      <tr>
                        <th>{t.promptName}</th>
                        <th>{t.promptStatus}</th>
                        <th>{t.colActions}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {prompts.map(prompt => {
                        const isSystemPrompt = prompt.source === "system" || prompt.managed_scope === "system"
                        const isCurrentPrompt = currentBindingPromptId === prompt.id
                        return (
                          <tr key={prompt.id} className={promptEditingId === prompt.id || currentBindingPromptId === prompt.id || promptViewingId === prompt.id ? "prompt-row-active" : ""}>
                            <td>
                              <div className="title">{getPromptDisplayName(prompt)}</div>
                              {isCurrentPrompt && (
                                <div className="prompt-binding-actions">
                                  <span className="prompt-binding-chip">{t.promptBindingCurrentBadge}</span>
                                </div>
                              )}
                            </td>
                            <td>
                              <span className={`prompt-status-badge ${prompt.status}`}>
                                {getPromptStatusLabel(prompt.status)}
                              </span>
                            </td>
                            <td>
                              <div className="prompt-actions-inline">
                                <button type="button" onClick={() => openPromptEdit(prompt.id)}>{t.promptEdit}</button>
                                <button
                                  type="button"
                                  disabled={!currentBindingSceneKey || prompt.status !== "enabled" || promptBindingUpdatingScene === currentBindingSceneKey || currentBindingPromptId === prompt.id}
                                  onClick={() => updatePromptBinding(currentBindingSceneKey, prompt.id)}
                                >
                                  {currentBindingPromptId === prompt.id ? t.promptBindingCurrentBadge : t.promptBindingSetActive}
                                </button>
                                <button type="button" onClick={() => openPromptView(prompt.id)}>{t.promptViewAction}</button>
                                {!isSystemPrompt && (
                                  <>
                                    <button
                                      type="button"
                                      disabled={promptStatusUpdatingId === prompt.id}
                                      onClick={() => togglePromptStatus(prompt)}
                                    >
                                      {prompt.status === "enabled" ? t.promptDisable : t.promptEnable}
                                    </button>
                                    {promptDeleteConfirm === prompt.id ? (
                                      <>
                                        <button type="button" onClick={() => removePrompt(prompt.id)}>{t.confirm}</button>
                                        <button type="button" onClick={() => setPromptDeleteConfirm("")}>{t.cancel}</button>
                                      </>
                                    ) : (
                                      <button type="button" onClick={() => setPromptDeleteConfirm(prompt.id)}>{t.delete}</button>
                                    )}
                                  </>
                                )}
                              </div>
                            </td>
                          </tr>
                        )
                      })}
                    </tbody>
                  </table>

                  <div className="pagination">
                    <button
                      disabled={promptPagination.page === 1}
                      onClick={() => setPromptPagination(prev => ({ ...prev, page: prev.page - 1 }))}
                    >
                      {t.previous}
                    </button>
                    <span>
                      {lang === "zh"
                        ? `第 ${promptPagination.page} / ${Math.max(1, Math.ceil(promptPagination.total / promptPagination.page_size))} 页`
                        : `Page ${promptPagination.page} of ${Math.max(1, Math.ceil(promptPagination.total / promptPagination.page_size))}`}
                    </span>
                    <button
                      disabled={promptPagination.page * promptPagination.page_size >= promptPagination.total}
                      onClick={() => setPromptPagination(prev => ({ ...prev, page: prev.page + 1 }))}
                    >
                      {t.next}
                    </button>
                  </div>
                </>
              )}
            </div>

            <div className="prompt-panel prompt-editor-panel">
              <div className="prompt-editor-header">
                <div>
                  <h3>{t.promptEditorTitle}</h3>
                  {promptEditingId && <div className="meta">{promptEditorDisplayName}</div>}
                  <div className="meta">{t.promptLanguageLabel}: {promptEditorLanguageLabel}</div>
                  <div className="meta">{t.promptNoCodeHint}</div>
                  {promptEditingIsSystem && <div className="meta">{t.promptSystemHint}</div>}
                </div>
                {promptEditingId && (
                  <span className={`prompt-status-badge ${promptEditingStatus}`}>
                    {getPromptStatusLabel(promptEditingStatus)}
                  </span>
                )}
              </div>

              <div className="prompt-form-grid">
                {!promptEditingIsSystem && (
                  <label className="prompt-field">
                    <span>{t.promptName}</span>
                    <input
                      value={promptForm.name}
                      onChange={e => setPromptForm(prev => ({ ...prev, name: e.target.value }))}
                    />
                    {promptFormErrors.name && <span className="prompt-field-error">{promptFormErrors.name}</span>}
                  </label>
                )}

                <label className="prompt-field prompt-field-wide">
                  <span>{t.promptRoleText}</span>
                  <textarea
                    rows={6}
                    value={promptForm.display_system_text}
                    onChange={e => setPromptForm(prev => ({ ...prev, display_system_text: e.target.value }))}
                  />
                </label>

                <label className="prompt-field prompt-field-wide">
                  <span>{t.promptFocusText}</span>
                  <textarea
                    rows={4}
                    value={promptForm.display_focus_text}
                    onChange={e => setPromptForm(prev => ({ ...prev, display_focus_text: e.target.value }))}
                    placeholder={t.promptFocusPlaceholder}
                  />
                </label>

                <label className="prompt-field prompt-field-wide">
                  <span>{t.promptTaskText}</span>
                  <textarea
                    rows={12}
                    value={promptForm.display_template_text}
                    onChange={e => setPromptForm(prev => ({ ...prev, display_template_text: e.target.value }))}
                  />
                  {promptFormErrors.display_template_text && <span className="prompt-field-error">{promptFormErrors.display_template_text}</span>}
                </label>
              </div>

              <div className="prompt-actions">
                <button disabled={promptSaving} onClick={savePrompt}>
                  {promptSaving ? t.loading : (promptEditingId ? t.promptSaveUpdate : t.promptSaveCreate)}
                </button>
                <button type="button" onClick={openPromptCreate}>{t.promptCreate}</button>
                {promptEditingIsSystem && promptEditingId && (
                  <button type="button" disabled={promptRestoreLoading} onClick={restorePromptDefault}>
                    {promptRestoreLoading ? t.loading : t.promptRestoreDefault}
                  </button>
                )}
                {promptEditingId && promptDeleteConfirm === promptEditingId && (
                  <span className="meta">{t.promptDeleteConfirm}</span>
                )}
              </div>

              <div className="prompt-preview-box">
                <div className="prompt-preview-header">
                  <strong>{t.promptPreview}</strong>
                  <span className="meta">
                    {promptPreviewLoading ? t.loading : t.promptPreviewHint}
                  </span>
                </div>
                {promptPreviewError ? (
                  <div className="prompt-banner error">{t.promptPreviewFailed}: {promptPreviewError}</div>
                ) : (promptPreviewSystem || promptPreviewUser) ? (
                  <div className="prompt-preview-stack">
                    <div className="prompt-preview-section">
                      <div className="meta">{t.promptPreviewSystem}</div>
                      <pre className="prompt-preview-content">{promptPreviewSystem || "-"}</pre>
                    </div>
                    <div className="prompt-preview-section">
                      <div className="meta">{t.promptPreviewUser}</div>
                      <pre className="prompt-preview-content">{promptPreviewUser || "-"}</pre>
                    </div>
                  </div>
                ) : (
                  <div className="prompt-empty">{t.promptPreviewEmpty}</div>
                )}
              </div>
            </div>
          </div>

          {promptViewingId && (
            <div className="prompt-inspect-overlay" role="dialog" aria-modal="true">
              <div className="prompt-inspect-modal">
                <div className="prompt-inspect-header">
                  <div className="prompt-inspect-title">
                    <h3>{t.promptViewTitle}</h3>
                    <p className="meta">{t.promptViewHint}</p>
                  </div>
                  <button type="button" onClick={closePromptView}>{t.promptViewClose}</button>
                </div>

                {promptViewError ? (
                  <div className="prompt-banner error">{promptViewError}</div>
                ) : promptViewLoading ? (
                  <div className="prompt-empty">{t.loading}</div>
                ) : promptViewDetail ? (
                  <div className="prompt-inspect-body">
                    <div className="prompt-inspect-summary">
                      <div className="summary-item">
                        <span className="meta">{t.promptName}</span>
                        <strong>{getPromptDisplayName(promptViewDetail)}</strong>
                      </div>
                      <div className="summary-item">
                        <span className="meta">{t.promptLanguageLabel}</span>
                        <strong>{promptEditorLanguageLabel}</strong>
                      </div>
                      <div className="summary-item">
                        <span className="meta">{t.promptStatus}</span>
                        <span className={`prompt-status-badge ${promptViewStatus}`}>
                          {getPromptStatusLabel(promptViewStatus)}
                        </span>
                      </div>
                      <div className="summary-item">
                        <span className="meta">{t.promptSource}</span>
                        <strong>{promptViewSourceLabel}</strong>
                      </div>
                      {currentBindingPromptId === promptViewDetail.id && (
                        <div className="summary-item">
                          <span className="prompt-binding-chip">{t.promptBindingCurrentBadge}</span>
                        </div>
                      )}
                    </div>

                    <div className="prompt-inspect-grid">
                      <div className="prompt-inspect-section">
                        <div className="meta">{t.promptRoleText}</div>
                        <pre className="prompt-preview-content">{promptViewDetail.display_system_text || "-"}</pre>
                      </div>

                      <div className="prompt-inspect-section">
                        <div className="meta">{t.promptFocusText}</div>
                        <pre className="prompt-preview-content">{promptViewDetail.display_focus_text || "-"}</pre>
                      </div>

                      <div className="prompt-inspect-section prompt-inspect-section-wide">
                        <div className="meta">{t.promptTaskText}</div>
                        <pre className="prompt-preview-content">{promptViewDetail.display_template_text || "-"}</pre>
                      </div>
                    </div>

                    <div className="prompt-preview-box">
                      <div className="prompt-preview-header">
                        <strong>{t.promptViewRenderedTitle}</strong>
                        <span className="meta">
                          {promptViewPreviewLoading ? t.loading : t.promptViewRenderedHint}
                        </span>
                      </div>
                      {promptViewPreviewError ? (
                        <div className="prompt-banner error">{t.promptPreviewFailed}: {promptViewPreviewError}</div>
                      ) : (promptViewPreviewSystem || promptViewPreviewUser) ? (
                        <div className="prompt-preview-stack">
                          <div className="prompt-preview-section">
                            <div className="meta">{t.promptPreviewSystem}</div>
                            <pre className="prompt-preview-content">{promptViewPreviewSystem || "-"}</pre>
                          </div>
                          <div className="prompt-preview-section">
                            <div className="meta">{t.promptPreviewUser}</div>
                            <pre className="prompt-preview-content">{promptViewPreviewUser || "-"}</pre>
                          </div>
                        </div>
                      ) : (
                        <div className="prompt-empty">{t.promptPreviewEmpty}</div>
                      )}
                    </div>
                  </div>
                ) : null}
              </div>
            </div>
          )}
        </div>
      )}

      {tab === "model" && (
        <div className="card">
          <h2>{t.llmTitle}</h2>
          {llmConfig && (
            <div className="form">
              <div className="row">
                <label>{t.provider}</label>
                <div>{llmConfig.provider_label} ({llmConfig.provider})</div>
              </div>
              <div className="row">
                <label>{t.apiBase}</label>
                <div>{llmConfig.api_base || "-"}</div>
              </div>
              <div className="row">
                <label>{t.model}</label>
                <div>{llmConfig.model || "-"}</div>
              </div>
              <div className="row">
                <label>{t.temperature}</label>
                <div>{llmConfig.temperature}</div>
              </div>
              <div className="row">
                <label>{t.maxTokens}</label>
                <div>{llmConfig.max_tokens}</div>
              </div>
              <div className="row">
                <label>{t.timeout}</label>
                <div>{llmConfig.timeout}</div>
              </div>
              <div className="row">
                <label>{t.llmConfigMode}</label>
                <div>{llmConfig.env_managed ? t.llmConfigModeEnv : t.llmConfigModeMixed}</div>
              </div>
              {!!llmConfig.route?.selected_role && (
                <div className="row">
                  <label>{t.llmRoute}</label>
                  <div>{llmConfig.route.selected_role} / {llmConfig.route.selected_source || "-"}</div>
                </div>
              )}
              {Array.isArray(llmConfig.security_issues) && llmConfig.security_issues.length > 0 && (
                <div className="prompt-banner error">
                  {llmConfig.security_issues.join(" ")}
                </div>
              )}
              {Array.isArray(llmConfig.missing_env_vars) && llmConfig.missing_env_vars.length > 0 && (
                <div className="prompt-banner error">
                  {t.llmMissingEnv}: {llmConfig.missing_env_vars.join(", ")}
                </div>
              )}
              {llmError && <div className="prompt-banner error">{llmError}</div>}
              <div className="llm-test">
                <div className="row">
                  <label>{t.llmEnvSupported}</label>
                </div>
                <div className="content">
                  {(llmConfig.supported_env_vars || []).join(", ") || "-"}
                </div>
              </div>
              <div className="llm-test">
                <div className="row">
                  <label>{t.llmEnvResolved}</label>
                </div>
                <div className="content">
                  {Object.keys(llmConfig.env_sources || {}).length > 0
                    ? Object.entries(llmConfig.env_sources).map(([key, value]) => `${key}: ${value}`).join(" | ")
                    : t.llmEnvResolvedNone}
                </div>
              </div>
              <div className="llm-test">
                <div className="row">
                  <label>{t.llmTestPrompt}</label>
                  <textarea
                    className="llm-test-input"
                    rows={2}
                    value={llmTestPrompt}
                    onChange={e => setLlmTestPrompt(e.target.value)}
                  />
                </div>
                <div className="row llm-test-actions">
                  <button disabled={llmTesting} onClick={testLLM}>{llmTesting ? t.llmTestRunning : t.llmTestButton}</button>
                  {llmTestError && <span className="llm-test-error">{llmTestError}</span>}
                  {!llmTestError && llmTestResult && <span className="llm-test-ok">{t.llmTestOk}</span>}
                </div>
                {llmTestResult && (
                  <div className="row">
                    <label>{t.llmTestResult}</label>
                    <div className="llm-test-result">{llmTestResult}</div>
                  </div>
                )}
              </div>
              <div className="llm-test">
                <div className="row">
                  <label>{t.uiConfigTitle}</label>
                </div>
                <div className="row">
                  <label className="toggle">
                    <input
                      type="checkbox"
                      checked={!!uiConfig.showCitationSource}
                      onChange={e => setUiConfig(prev => ({ ...prev, showCitationSource: e.target.checked }))}
                    />
                    {t.showCitationSource}
                  </label>
                </div>
                <div className="row">
                  <label>{t.defaultTheme}</label>
                  <select
                    value={uiConfig.defaultTheme}
                    onChange={e => setUiConfig(prev => ({ ...prev, defaultTheme: e.target.value === "light" ? "light" : "dark" }))}
                  >
                    <option value="dark">{t.themeDark}</option>
                    <option value="light">{t.themeLight}</option>
                  </select>
                </div>
                <div className="row">
                  <button disabled={uiSaving} onClick={saveUIConfig}>{t.save}</button>
                  {uiError && <span style={{ marginLeft: 12 }}>{uiError}</span>}
                </div>
              </div>

              {vectorStoreConfig && (
                <div className="llm-test">
                  <div className="row">
                    <label>{lang === "zh" ? "向量库存储引擎" : "Vector Store Engine"}</label>
                  </div>
                  <div className="row">
                    <label>{lang === "zh" ? "当前引擎:" : "Current Engine:"}</label>
                    <select
                      value={vectorStoreConfig.engine}
                      onChange={e => setVectorStoreConfig(prev => ({ ...prev, engine: e.target.value }))}
                    >
                      <option value="sqlite">SQLite (Default)</option>
                      <option value="chromadb" disabled={!vectorStoreConfig.chroma_available}>
                        ChromaDB {!vectorStoreConfig.chroma_available && (lang === "zh" ? "(未安装依赖)" : "(Not Installed)")}
                      </option>
                    </select>
                  </div>
                  <div className="row">
                    <button disabled={vectorStoreSaving} onClick={saveVectorStoreConfig}>{t.save}</button>
                    <button onClick={cleanupVectorStore} style={{ marginLeft: 8 }}>{lang === "zh" ? "一键清理旧引擎数据" : "Clean Old Engine Data"}</button>
                    {vectorStoreMsg && <span style={{ marginLeft: 12 }}>{vectorStoreMsg}</span>}
                  </div>
                </div>
              )}

              <div className="llm-test">
                <div className="row">
                  <label>{t.memoryConfigTitle}</label>
                </div>
                <div className="row">
                  <label className="toggle">
                    <input
                      type="checkbox"
                      checked={!!memoryConfig.memory_module_enabled}
                      onChange={e => setMemoryConfig(prev => ({ ...prev, memory_module_enabled: e.target.checked }))}
                    />
                    {t.memoryModuleEnabled}
                  </label>
                </div>
                <div className="row">
                  <div style={{ color: "#b54708", fontSize: 13, lineHeight: 1.5 }}>
                    {memoryConfig.risk_notice || t.memoryRiskNotice}
                  </div>
                </div>
                <div className="row">
                  <label>{t.memoryModeWhenDisabled}</label>
                  <select
                    value={String(memoryConfig.memory_mode_when_disabled || "classic")}
                    onChange={e => setMemoryConfig(prev => ({ ...prev, memory_mode_when_disabled: e.target.value || "classic" }))}
                  >
                    <option value="classic">{t.memoryModeClassic}</option>
                  </select>
                </div>
                <div className="row">
                  <label className="toggle">
                    <input
                      type="checkbox"
                      checked={!!memoryConfig.memory_token_guard_enabled}
                      onChange={e => setMemoryConfig(prev => ({ ...prev, memory_token_guard_enabled: e.target.checked }))}
                    />
                    {t.memoryTokenGuardEnabled}
                  </label>
                </div>
                <div className="row">
                  <label>{t.memoryMaxCalls}</label>
                  <input
                    type="number"
                    min="1"
                    max="200"
                    value={memoryConfig.memory_max_llm_calls_per_audit}
                    onChange={e => setMemoryConfig(prev => ({ ...prev, memory_max_llm_calls_per_audit: Number(e.target.value || 12) }))}
                  />
                </div>
                <div className="row">
                  <label>{t.memoryMaxPromptChars}</label>
                  <input
                    type="number"
                    min="300"
                    max="12000"
                    value={memoryConfig.memory_max_prompt_chars_per_clause}
                    onChange={e => setMemoryConfig(prev => ({ ...prev, memory_max_prompt_chars_per_clause: Number(e.target.value || 2400) }))}
                  />
                </div>
                <div className="row">
                  <button disabled={memorySaving} onClick={saveMemoryConfig}>{t.save}</button>
                  {memoryMsg && <span style={{ marginLeft: 12 }}>{memoryMsg}</span>}
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {tab === "skills" && (
        <SkillManager lang={lang} labels={skillLabels} />
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
                    <td>
                      {u.id === user?.id ? (
                        <span>-</span>
                      ) : userDeleteConfirm === u.id ? (
                        <span>
                          <button onClick={() => handleUserDelete(u.id)}>{t.confirm}</button>
                          <button onClick={() => setUserDeleteConfirm(null)}>{t.cancel}</button>
                        </span>
                      ) : (
                        <button onClick={() => setUserDeleteConfirm(u.id)}>{t.delete}</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      )}

      {tab === "regulations" && (
        <div className="card">
          <h2>{t.importTitle}</h2>
          <form onSubmit={onRegUpload} className="grid">
            <input placeholder={lang === "zh" ? "标题" : "Title"} value={regUpload.title} onChange={e => setRegUpload({ ...regUpload, title: e.target.value })} />
            <input placeholder={lang === "zh" ? "Tag" : "Tag"} value={regUpload.doc_no} onChange={e => setRegUpload({ ...regUpload, doc_no: e.target.value })} />
            <input placeholder={lang === "zh" ? "发布机构" : "Issuer"} value={regUpload.issuer} onChange={e => setRegUpload({ ...regUpload, issuer: e.target.value })} />
            <input placeholder={lang === "zh" ? "生效日期 yyyy-mm-dd" : "Effective Date yyyy-mm-dd"} value={regUpload.effective_date} onChange={e => setRegUpload({ ...regUpload, effective_date: e.target.value })} />
            <input placeholder={lang === "zh" ? "失效日期 yyyy-mm-dd" : "Expiry Date yyyy-mm-dd"} value={regUpload.expiry_date} onChange={e => setRegUpload({ ...regUpload, expiry_date: e.target.value })} />
            <input placeholder={lang === "zh" ? "地区" : "Region"} value={regUpload.region} onChange={e => setRegUpload({ ...regUpload, region: e.target.value })} />
            <input placeholder={lang === "zh" ? "Sub-Tag" : "Sub-Tag"} value={regUpload.industry} onChange={e => setRegUpload({ ...regUpload, industry: e.target.value })} />
            <div className="row" style={{ alignItems: "center" }}>
              <select 
                value={regUpload.language} 
                onChange={e => setRegUpload({ ...regUpload, language: e.target.value })}
                style={{ marginRight: '12px' }}
              >
                <option value="zh">中文文档</option>
                <option value="en">English Document</option>
              </select>
              <input
                ref={fileInputRef}
                type="file"
                accept=".docx,.pdf"
                style={{ display: "none" }}
                onChange={e => {
                  const f = e.target.files?.[0] || null
                  setRegFile(f)
                  setRegFileError("")
                }}
              />
              <button type="button" onClick={() => fileInputRef.current && fileInputRef.current.click()} style={{ marginRight: '12px' }}>
                {t.chooseFile}
              </button>
              <span className="meta">{regFile ? regFile.name : t.noFileChosen}</span>
            </div>
            {regFileError && <span className="error">{regFileError}</span>}
            <button type="submit">{t.uploadBtn}</button>
          </form>
          <div className="row">
            <button onClick={onRegCheck}>{t.checkJob}</button>
            <span>Job: {jobId} {jobStatus}</span>
          </div>
          <h2>{t.searchTitle}</h2>
          <form onSubmit={onRegSearch} className="grid">
            <input placeholder={lang === "zh" ? "问题" : "Query"} value={regQuery.query} onChange={e => setRegQuery({ ...regQuery, query: e.target.value })} />
            <input placeholder={lang === "zh" ? "日期 yyyy-mm-dd" : "Date yyyy-mm-dd"} value={regQuery.date} onChange={e => setRegQuery({ ...regQuery, date: e.target.value })} />
            <input placeholder={lang === "zh" ? "地区" : "Region"} value={regQuery.region} onChange={e => setRegQuery({ ...regQuery, region: e.target.value })} />
            <input placeholder={lang === "zh" ? "Sub-Tag" : "Sub-Tag"} value={regQuery.industry} onChange={e => setRegQuery({ ...regQuery, industry: e.target.value })} />
            <div className="advanced-panel">
              <div className="advanced-header">
                <div className="advanced-title">{t.advancedConfig}</div>
                <button type="button" onClick={() => setRegShowAdvanced(!regShowAdvanced)}>
                  {regShowAdvanced ? (lang === "zh" ? "收起" : "Hide") : t.advancedToggle}
                </button>
              </div>
              {regShowAdvanced && (
                <div className="advanced-body">
                  <label className="toggle">
                    <input type="checkbox" checked={regQuery.use_semantic} onChange={e => setRegQuery({ ...regQuery, use_semantic: e.target.checked })} />
                    {t.semantic}
                  </label>
                  <div className="range">
                    <label>{t.semanticWeight}: {regQuery.semantic_weight}</label>
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.1"
                      value={regQuery.semantic_weight}
                      onChange={e => setRegQuery({ ...regQuery, semantic_weight: parseFloat(e.target.value) })}
                    />
                  </div>
                  <div className="range">
                    <label>{t.bm25Weight}: {regQuery.bm25_weight}</label>
                    <input
                      type="range"
                      min="0"
                      max="1"
                      step="0.1"
                      value={regQuery.bm25_weight}
                      onChange={e => setRegQuery({ ...regQuery, bm25_weight: parseFloat(e.target.value) })}
                    />
                  </div>
                  <label className="toggle">
                    <input type="checkbox" checked={regQuery.rerank_enabled} onChange={e => setRegQuery({ ...regQuery, rerank_enabled: e.target.checked })} />
                    {t.rerankEnabled}
                  </label>
                  <div className="range">
                    <label>{t.candidateSize}: {regQuery.candidate_size}</label>
                    <input
                      type="range"
                      min="10"
                      max="200"
                      step="10"
                      value={regQuery.candidate_size}
                      onChange={e => setRegQuery({ ...regQuery, candidate_size: parseInt(e.target.value || "0") })}
                    />
                  </div>
                  <div className="range">
                    <label>{t.rerankTopN}: {regQuery.rerank_top_n}</label>
                    <input
                      type="range"
                      min="10"
                      max="200"
                      step="10"
                      value={regQuery.rerank_top_n}
                      onChange={e => setRegQuery({ ...regQuery, rerank_top_n: parseInt(e.target.value || "0") })}
                    />
                  </div>
                </div>
              )}
            </div>
            <button type="submit" className="wide">{t.searchBtn}</button>
          </form>
          <div className="row">
            <strong>{t.result}</strong>
            <span className="meta">{regSearching ? t.searching : `${regResults.length} item(s)`}</span>
          </div>
          {regSearchError && <div className="error">{regSearchError}</div>}
          {regHasSearched && !regSearchError && regResults.length === 0 && !regSearching && <div className="error">{t.noMatch}</div>}
          <ul className="list">
            {regResults.slice(0, 5).map((r, i) => (
              <li key={getRegResultKey(r, i)}>
                <div className="title">{r.title} - {r.article_no}</div>
                <div className="meta">{r.effective_date} | {r.region} | {r.industry}</div>
                <div className="content">
                  {regExpanded[getRegResultKey(r, i)] ? String(r.content || "") : getRegSummary(r.content)}
                </div>
                <div className="row" style={{ marginTop: 8 }}>
                  <button
                    type="button"
                    onClick={() => {
                      const key = getRegResultKey(r, i)
                      setRegExpanded(prev => ({ ...prev, [key]: !prev[key] }))
                    }}
                  >
                    {regExpanded[getRegResultKey(r, i)]
                      ? (lang === "zh" ? "收起全文" : "Collapse")
                      : (lang === "zh" ? "展开全文" : "Expand")}
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      )}

      {tab === "token-monitor" && <TokenMonitor lang={lang} />}
    </div>
  )
}
