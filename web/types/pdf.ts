export type PdfPipelineModuleItem = {
  name: string
  entry: string
  service: string
  inputs: string[]
  outputs: string[]
  dependencies: string[]
  business_links: string[]
}

export type PdfPipelineSummary = {
  current_modules?: {
    version?: string
    runtime?: Record<string, unknown>
    modules?: PdfPipelineModuleItem[]
    effective_config?: Record<string, unknown>
  }
  target_requirements?: {
    core_features?: string[]
    performance?: Record<string, unknown>
    compatibility?: Record<string, unknown>
  }
  replacement_status?: Record<string, unknown>
}

const asArray = (value: unknown) => Array.isArray(value) ? value : []

export function normalizePdfPipelineSummary(raw: unknown): PdfPipelineSummary {
  const source = raw && typeof raw === "object" ? raw as PdfPipelineSummary : {}
  const current = source.current_modules && typeof source.current_modules === "object" ? source.current_modules : {}
  const target = source.target_requirements && typeof source.target_requirements === "object" ? source.target_requirements : {}
  const replacement = source.replacement_status && typeof source.replacement_status === "object" ? source.replacement_status : {}
  return {
    current_modules: {
      version: String(current.version || ""),
      runtime: current.runtime && typeof current.runtime === "object" ? current.runtime : {},
      modules: asArray(current.modules).map((item) => {
        const row = item && typeof item === "object" ? item as PdfPipelineModuleItem : {} as PdfPipelineModuleItem
        return {
          name: String(row.name || ""),
          entry: String(row.entry || ""),
          service: String(row.service || ""),
          inputs: asArray(row.inputs).map((v) => String(v || "")),
          outputs: asArray(row.outputs).map((v) => String(v || "")),
          dependencies: asArray(row.dependencies).map((v) => String(v || "")),
          business_links: asArray(row.business_links).map((v) => String(v || "")),
        }
      }),
      effective_config: current.effective_config && typeof current.effective_config === "object" ? current.effective_config : {},
    },
    target_requirements: {
      core_features: asArray(target.core_features).map((v) => String(v || "")),
      performance: target.performance && typeof target.performance === "object" ? target.performance : {},
      compatibility: target.compatibility && typeof target.compatibility === "object" ? target.compatibility : {},
    },
    replacement_status: replacement,
  }
}
