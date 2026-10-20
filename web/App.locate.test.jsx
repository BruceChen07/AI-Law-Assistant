import { describe, expect, it } from "vitest"

import { resolveRiskLocateTarget } from "./App"

describe("resolveRiskLocateTarget", () => {
  it("corrects an estimated backend page when another page has a much stronger evidence match", () => {
    const risk = {
      location: {
        page_no: 3,
        paragraph_no: "74",
      },
      evidence: "华润万家有权从应付供应商的货款中扣除费用",
    }
    const previewPages = [
      { page_no: 3, blocks: [{ text: "供应商应配合活动方案执行" }] },
      { page_no: 14, blocks: [{ text: "华润万家有权从应付供应商的货款中扣除费用" }] },
    ]

    const result = resolveRiskLocateTarget({
      risk,
      previewPages,
      pickHighlight: (_risk, blocks, pageNo) => {
        const text = String(blocks?.[0]?.text || "")
        if (pageNo === 14 && text.includes("扣除费用")) {
          return {
            strategy: "quote_exact",
            confidence: 0.96,
            bbox: [0, 0, 10, 10],
            blockId: "p14-b1",
            blockIndex: 0,
          }
        }
        if (pageNo === 3) {
          return {
            strategy: "semantic",
            confidence: 0.42,
            bbox: [0, 0, 10, 10],
            blockId: "p3-b1",
            blockIndex: 0,
          }
        }
        return null
      },
    })

    expect(result.pageNo).toBe(14)
    expect(result.corrected).toBe(true)
    expect(result.strategy).toBe("quote_exact")
  })

  it("keeps the requested page when its own match is already strong", () => {
    const risk = {
      location: {
        page_no: 14,
        paragraph_no: "74",
      },
    }
    const previewPages = [
      { page_no: 14, blocks: [{ text: "exact clause" }] },
      { page_no: 15, blocks: [{ text: "another similar clause" }] },
    ]

    const result = resolveRiskLocateTarget({
      risk,
      previewPages,
      pickHighlight: (_risk, _blocks, pageNo) => {
        if (pageNo === 14) {
          return {
            strategy: "quote_fuzzy",
            confidence: 0.84,
            bbox: [0, 0, 10, 10],
            blockId: "p14-b1",
            blockIndex: 0,
          }
        }
        if (pageNo === 15) {
          return {
            strategy: "semantic",
            confidence: 0.9,
            bbox: [0, 0, 10, 10],
            blockId: "p15-b1",
            blockIndex: 0,
          }
        }
        return null
      },
    })

    expect(result.pageNo).toBe(14)
    expect(result.corrected).toBe(false)
  })
})
