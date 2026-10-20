import { normalizePdfPipelineSummary } from "./types/pdf"

const formatBool = (value, t) => (value ? t.pipelineOn : t.pipelineOff)

export default function PdfPipelinePanel({ summary, t }) {
  const data = normalizePdfPipelineSummary(summary)
  const current = data.current_modules || {}
  const runtime = current.runtime || {}
  const modules = Array.isArray(current.modules) ? current.modules : []
  const target = data.target_requirements || {}
  const features = Array.isArray(target.core_features) ? target.core_features : []
  const status = data.replacement_status && typeof data.replacement_status === "object" ? data.replacement_status : {}
  const config = current.effective_config && typeof current.effective_config === "object" ? current.effective_config : {}

  return (
    <section className="card pdf-pipeline-card">
      <div className="pdf-pipeline-head">
        <div>
          <div className="panel-title">{t.pipelineTitle}</div>
          <h3>{t.pipelineSubtitle}</h3>
        </div>
        <span className="pdf-pipeline-version">{current.version || "pdf_pipeline_v2"}</span>
      </div>
      <div className="pdf-pipeline-runtime">
        <span>{t.pipelineRender}: {formatBool(runtime.render_engine_available, t)}</span>
        <span>{t.pipelinePreview}: {formatBool(runtime.preview_render_enabled, t)}</span>
        <span>{t.pipelineOcr}: {formatBool(runtime.ocr_enabled, t)}</span>
        <span>{t.pipelineNetwork}: {formatBool(runtime.internal_network_ready, t)}</span>
      </div>
      <div className="pdf-pipeline-grid">
        <div className="pdf-pipeline-block">
          <div className="panel-title">{t.pipelineModules}</div>
          <div className="pdf-pipeline-list">
            {modules.map((item) => (
              <article key={item.name || item.entry} className="pdf-pipeline-module">
                <header>
                  <strong>{item.name || "-"}</strong>
                  <code>{item.service || "-"}</code>
                </header>
                <p>{item.entry || "-"}</p>
                <div className="pdf-pipeline-tags">
                  {item.dependencies.map((dep) => <span key={`${item.name}-${dep}`}>{dep}</span>)}
                </div>
              </article>
            ))}
          </div>
        </div>
        <div className="pdf-pipeline-block">
          <div className="panel-title">{t.pipelineTargets}</div>
          <ul className="pdf-pipeline-bullets">
            {features.map((feature, idx) => <li key={`${feature}-${idx}`}>{feature}</li>)}
          </ul>
          <div className="pdf-pipeline-config">
            <span>{t.pipelineAuditMode}: {config.prefer_digitized_for_audit ? t.pipelineDigitizedAudit : t.pipelineHybridAudit}</span>
            <span>{t.pipelineScannedThreshold}: {config.scan_ratio_for_ocr ?? "-"}</span>
            <span>{t.pipelineDigitizeThreshold}: {config.scan_ratio_for_digitize ?? "-"}</span>
            <span>{t.pipelineRenderEngine}: {String(config.preview_render_engine || "-")}</span>
          </div>
        </div>
      </div>
      <div className="pdf-pipeline-status">
        {Object.entries(status).map(([key, value]) => (
          <span key={key} className={value ? "is-ok" : "is-pending"}>
            {key}: {formatBool(Boolean(value), t)}
          </span>
        ))}
      </div>
    </section>
  )
}
