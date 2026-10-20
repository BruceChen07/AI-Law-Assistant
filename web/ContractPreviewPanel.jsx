export default function ContractPreviewPanel({
  t,
  documentId,
  previewLoading,
  previewError,
  previewMode,
  previewPages,
  previewText,
  previewMeta,
  previewContinuous,
  previewContinuousEnabled,
  previewThumbsOpen,
  previewZoom,
  previewScrollPercent,
  activePreviewPage,
  activePreviewHighlight,
  previewPageUrls,
  previewCoordProviderLabel,
  previewPipelineLabel,
  previewGateLabel,
  previewGateRatioLabel,
  previewConversion,
  previewMainRef,
  previewScrollRef,
  previewPageRefs,
  previewPageObserverRef,
  previewThumbRefs,
  onToggleThumbs,
  onLayoutChange,
  onPageSelect,
  onPageHover,
  onZoomChange,
  onDownloadSource,
  sourceDownloading
}) {
  const pageCount = Number(previewMeta?.page_count || previewPages.length || 0)
  const renderPageCanvas = (pageNo, url, active = false) => (
    <div className={`preview-main-media${active ? " is-active" : ""}`}>
      {url ? (
        <div className="preview-page-canvas" style={{ width: `${Math.round(previewZoom * 100)}%` }}>
          <img loading="lazy" decoding="async" src={url} alt={`page-${pageNo}`} />
          {Array.isArray(activePreviewHighlight?.bbox) && activePreviewHighlight.bbox.length === 4 && Number(activePreviewHighlight?.pageNo || 0) === pageNo ? (
            <div
              key={`hl-${activePreviewHighlight.seq || 0}`}
              className="preview-highlight"
              style={{
                left: `${Math.max(0, Math.min(1, Number(activePreviewHighlight.bbox[0]) || 0)) * 100}%`,
                top: `${Math.max(0, Math.min(1, Number(activePreviewHighlight.bbox[1]) || 0)) * 100}%`,
                width: `${Math.max(0, Math.min(1, Number(activePreviewHighlight.bbox[2]) || 0)) * 100}%`,
                height: `${Math.max(0, Math.min(1, Number(activePreviewHighlight.bbox[3]) || 0)) * 100}%`,
              }}
            />
          ) : null}
        </div>
      ) : <div className="preview-main-loading">{t.previewLoading}</div>}
    </div>
  )

  return (
    <section className="card contract-preview">
      <div className="preview-head">
        <div className="panel-title">{t.contractPreview}</div>
        {previewMode === "visual" && previewPages.length > 0 && (
          <div className="preview-toolbar">
            <button
              type="button"
              className={`preview-thumb-toggle${previewThumbsOpen ? " is-active" : ""}`}
              onClick={onToggleThumbs}
            >
              {t.previewThumbs}
            </button>
            <div className="preview-layout-toggle">
              <span>{t.previewLayout}</span>
              <button
                type="button"
                className={!previewContinuous ? "is-active" : ""}
                onClick={() => onLayoutChange(false)}
              >
                {t.previewPaged}
              </button>
              <button
                type="button"
                className={previewContinuous ? "is-active" : ""}
                onClick={() => onLayoutChange(true)}
                disabled={!previewContinuousEnabled}
              >
                {t.previewContinuous}
              </button>
            </div>
            <div className="preview-zoom">
              <span>{t.previewZoom}</span>
              <button type="button" onClick={() => onZoomChange("out")} aria-label={t.previewZoomOut}>-</button>
              <button type="button" onClick={() => onZoomChange("reset")} aria-label={t.previewZoomReset}>
                {Math.round(previewZoom * 100)}%
              </button>
              <button type="button" onClick={() => onZoomChange("in")} aria-label={t.previewZoomIn}>+</button>
            </div>
            <label className="preview-page-picker">
              <span>{t.previewPageJump}</span>
              <select value={String(activePreviewPage || 1)} onChange={(e) => onPageSelect(Number(e.target.value || 1))}>
                {previewPages.map((page) => {
                  const pageNo = Number(page?.page_no || 0)
                  return (
                    <option key={`page-${pageNo}`} value={String(pageNo)}>
                      P{pageNo}
                    </option>
                  )
                })}
              </select>
            </label>
            <button
              type="button"
              className="preview-thumb-toggle"
              disabled={!documentId || sourceDownloading}
              onClick={onDownloadSource}
            >
              {sourceDownloading ? t.exporting : t.previewDownloadSource}
            </button>
          </div>
        )}
        {previewMeta && (
          <div className="preview-meta">
            <span>{t.pageMeta}: {pageCount || "-"}</span>
            <span>{t.lineCount}: {previewMeta.line_total ?? 0}</span>
            <span>{previewMode === "visual" ? t.previewVisualMode : t.previewTextMode}</span>
            <span>{t.previewCoordSource}: {previewCoordProviderLabel}</span>
            <span>{t.previewPipeline}: {previewPipelineLabel}</span>
            <span className={`preview-gate ${previewConversion?.fallback ? "is-fallback" : "is-pass"}`}>{t.previewGate}: {previewGateLabel}</span>
            {previewConversion && (
              <span>{t.previewGateRatio}: {previewGateRatioLabel}</span>
            )}
          </div>
        )}
      </div>

      {!previewLoading && !previewError && (
        <div className="preview-hint">{t.previewHint}</div>
      )}

      {previewLoading && (
        <div className="empty-state preview-empty">
          <p>{t.previewLoading}</p>
        </div>
      )}
      {!previewLoading && previewError && <div className="error">{t.previewError}: {previewError}</div>}
      {!previewLoading && !previewError && previewMode === "visual" && previewPages.length === 0 && (
        <div className="empty-state preview-empty">
          <p>{t.previewEmpty}</p>
        </div>
      )}
      {!previewLoading && !previewError && previewMode === "visual" && previewPages.length > 0 && (
        <div className={`preview-visual${previewThumbsOpen ? " is-thumbs-open" : ""}`}>
          {previewThumbsOpen && (
            <aside className="preview-thumbs">
              {previewPages.map((page) => {
                const pageNo = Number(page?.page_no || 0)
                const key = String(pageNo)
                const url = previewPageUrls[key] || ""
                return (
                  <button
                    key={`thumb-${key}`}
                    type="button"
                    ref={(el) => {
                      previewThumbRefs.current[key] = el
                    }}
                    className={`preview-thumb${pageNo === activePreviewPage ? " is-active" : ""}`}
                    onClick={() => onPageSelect(pageNo)}
                  >
                    <div className="preview-thumb-media">
                      {url ? <img loading="lazy" decoding="async" src={url} alt={`thumbnail-${pageNo}`} /> : <span>{t.previewLoading}</span>}
                    </div>
                    <div className="preview-thumb-no">P{pageNo}</div>
                  </button>
                )
              })}
            </aside>
          )}
          <div className={`preview-main${previewContinuous ? " preview-main-scroll" : ""}`} ref={previewMainRef}>
            <div className="preview-float-indicator">P{activePreviewPage} / {pageCount || previewPages.length} · {t.previewProgress} {previewScrollPercent}%</div>
            {previewContinuous ? (
              <div className="preview-stream">
                {previewPages.map((page) => {
                  const pageNo = Number(page?.page_no || 0)
                  const key = String(pageNo)
                  const active = pageNo === activePreviewPage
                  const url = previewPageUrls[key] || ""
                  return (
                    <article
                      key={`stream-${key}`}
                      data-page-no={key}
                      ref={(el) => {
                        previewPageRefs.current[key] = el
                        if (el && previewPageObserverRef.current) previewPageObserverRef.current.observe(el)
                      }}
                      className={`preview-stream-page${active ? " is-active" : ""}`}
                      onMouseEnter={() => onPageHover(pageNo)}
                    >
                      {renderPageCanvas(pageNo, url, active)}
                    </article>
                  )
                })}
              </div>
            ) : (() => {
              const key = String(activePreviewPage || 1)
              const url = previewPageUrls[key] || ""
              return renderPageCanvas(Number(activePreviewPage || 1), url, true)
            })()}
          </div>
        </div>
      )}
      {!previewLoading && !previewError && previewMode !== "visual" && !previewText && (
        <div className="empty-state preview-empty">
          <p>{t.previewEmpty}</p>
        </div>
      )}
      {!previewLoading && !previewError && previewMode !== "visual" && !!previewText && (
        <div className="preview-scroll" ref={previewScrollRef}>
          <article className="preview-document">
            <pre>{previewText}</pre>
          </article>
        </div>
      )}
    </section>
  )
}
