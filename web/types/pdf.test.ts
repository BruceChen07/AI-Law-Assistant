import { describe, expect, it } from "vitest"

import { normalizePdfPipelineSummary } from "./pdf"

describe("normalizePdfPipelineSummary", () => {
  it("normalizes missing and malformed fields", () => {
    const result = normalizePdfPipelineSummary({
      current_modules: {
        version: "pdf_pipeline_v2",
        modules: [{ name: "preview", dependencies: ["pypdfium2"] }],
      },
      replacement_status: { backend_unified_pipeline: true },
    })

    expect(result.current_modules?.version).toBe("pdf_pipeline_v2")
    expect(result.current_modules?.modules?.[0].name).toBe("preview")
    expect(result.current_modules?.modules?.[0].dependencies).toEqual(["pypdfium2"])
    expect(result.target_requirements?.core_features).toEqual([])
    expect(result.replacement_status?.backend_unified_pipeline).toBe(true)
  })
})
