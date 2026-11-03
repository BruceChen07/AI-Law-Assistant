import { fireEvent, render, screen } from "@testing-library/react"
import { vi } from "vitest"

import ContractPreviewPanel from "./ContractPreviewPanel"
import { appI18n } from "./i18n/appI18n"


function buildProps(overrides = {}) {
  return {
    t: appI18n.en,
    documentId: "doc-1",
    previewLoading: false,
    previewError: "",
    previewMode: "visual",
    previewPages: [
      { page_no: 1, blocks: [] },
      { page_no: 2, blocks: [] }
    ],
    previewText: "",
    previewMeta: { page_count: 2, line_total: 18 },
    previewContinuous: true,
    previewContinuousEnabled: true,
    previewThumbsOpen: true,
    previewZoom: 1,
    previewScrollPercent: 24,
    activePreviewPage: 1,
    activePreviewHighlight: null,
    previewPageUrls: { "1": "blob:page-1", "2": "blob:page-2" },
    previewCoordProviderLabel: "MinerU Coordinates",
    previewPipelineLabel: "PDF→MinerU",
    previewGateLabel: "Passed",
    previewGateRatioLabel: "96%",
    previewConversion: null,
    previewMainRef: { current: null },
    previewScrollRef: { current: null },
    previewPageRefs: { current: {} },
    previewPageObserverRef: { current: null },
    previewThumbRefs: { current: {} },
    onToggleThumbs: vi.fn(),
    onLayoutChange: vi.fn(),
    onPageSelect: vi.fn(),
    onPageHover: vi.fn(),
    onZoomChange: vi.fn(),
    onDownloadSource: vi.fn(),
    sourceDownloading: false,
    ...overrides
  }
}


describe("ContractPreviewPanel", () => {
  it("wires preview toolbar actions", () => {
    const props = buildProps()
    const { container } = render(<ContractPreviewPanel {...props} />)

    fireEvent.click(screen.getByRole("button", { name: "Thumbnails" }))
    fireEvent.click(screen.getByRole("button", { name: "Paged" }))
    fireEvent.click(screen.getByRole("button", { name: "Continuous" }))
    fireEvent.click(screen.getByLabelText("Zoom Out"))
    fireEvent.click(screen.getByLabelText("Reset Zoom"))
    fireEvent.click(screen.getByLabelText("Zoom In"))
    fireEvent.click(screen.getByRole("button", { name: "Download Source" }))
    fireEvent.change(container.querySelector(".preview-page-picker select"), { target: { value: "2" } })

    expect(props.onToggleThumbs).toHaveBeenCalledTimes(1)
    expect(props.onLayoutChange).toHaveBeenNthCalledWith(1, false)
    expect(props.onLayoutChange).toHaveBeenNthCalledWith(2, true)
    expect(props.onZoomChange).toHaveBeenNthCalledWith(1, "out")
    expect(props.onZoomChange).toHaveBeenNthCalledWith(2, "reset")
    expect(props.onZoomChange).toHaveBeenNthCalledWith(3, "in")
    expect(props.onDownloadSource).toHaveBeenCalledTimes(1)
    expect(props.onPageSelect).toHaveBeenCalledWith(2)
  })

  it("renders plain text fallback", () => {
    render(
      <ContractPreviewPanel
        {...buildProps({
          previewMode: "text",
          previewPages: [],
          previewText: "fallback contract text",
          previewMeta: { page_count: 1, line_total: 1 },
          previewThumbsOpen: false
        })}
      />
    )

    expect(screen.getByText("fallback contract text")).toBeInTheDocument()
  })
})
