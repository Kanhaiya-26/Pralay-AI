import { useState } from "react";
import { api, fmtTime } from "../api";
import { Badge, Cat, Demo, Err, MismatchBadge, Score, Stages, useAsync } from "../components/common";
import ReviewForm from "../components/ReviewForm";
import LocationCompare from "../components/LocationCompare";

const SRC_LABEL = {
  claimed_text: "Claimed (text)", geocode: "Geocoded claim", claimed_pin: "Claimed pin", exif_gps: "EXIF GPS", browser: "Browser/device",
  landmark: "Visual landmark", upload: "Server upload time", gps_time: "GPS satellite clock", video_container: "Video container time",
  client_file_modified: "File modified (browser)", claimed_time: "Claimed event time", publication: "Original publication", exif_time: "EXIF capture time",
};

function ReporterProfile({ name }) {
  const [st, setSt] = useState({ open: false, data: null, error: null });
  const toggle = async () => {
    if (st.open) return setSt({ ...st, open: false });
    setSt({ open: true, data: null, error: null });
    try { setSt({ open: true, data: await api.userProfile(name), error: null }); } catch (e) { setSt({ open: true, data: null, error: e.message }); }
  };
  const d = st.data;
  return (
    <div style={{ marginTop: 4 }}>
      <button type="button" className="btn sm" onClick={toggle} data-testid="reporter-profile-btn">{st.open ? "Hide profile" : `View profile: ${name}`}</button>
      {st.open && <div className="finding" style={{ marginTop: 6 }} data-testid="reporter-profile">
        {st.error && <div className="err">Profile unavailable: {st.error}</div>}
        {!d && !st.error && <div className="sub">Loading…</div>}
        {d && <>
          <b>User Profile: {d.reporter_name}</b>
          <div>Credibility Score: {d.credibility_score ?? "—"} / 100 <span className="sub">(derived from the outcomes of their reports)</span></div>
          <div>Total Reports Submitted: {d.total_reports}</div>
          <ul style={{ margin: "6px 0 0" }}>{d.reports.map((x) => <li key={x.id}>#{x.id} · {x.category} · {x.location || "no location"} · {x.status}{x.claim_mismatch ? " · CLAIM MISMATCH" : ""}</li>)}</ul>
        </>}
      </div>}
    </div>
  );
}

export default function ReportDetail({ id, version, stages, openIncident }) {
  const [tab, setTab] = useState(() => new URLSearchParams(location.search).get("tab") || "assessment");
  const [bump, setBump] = useState(0);
  const { data: r, error } = useAsync(() => api.report(id), [id, version, bump]);
  if (error && !r) return <Err e={error} />;
  if (!r) return <div className="empty">Loading report…</div>;
  const a = r.assessment || {};
  const media = r.findings.filter((f) => !["location_consistency", "time_consistency"].includes(f.check_type));
  const lt = r.findings.filter((f) => ["location_consistency", "time_consistency"].includes(f.check_type));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="row">
        <Cat c={r.category} /> <Badge v={r.processing_state} /> <Badge v={r.effective_status} /> <MismatchBadge r={r} />
        {r.override_status && <span className="badge b-accent">HUMAN OVERRIDE</span>}
        {r.grid_mapped ? <span className="badge b-MAPPED">MAPPED · {r.grid_cell_id}</span> : null}
        {r.is_duplicate_of && <span className="badge b-concern">DUPLICATE OF #{r.is_duplicate_of}</span>}
        <Demo on={r.is_demo} />
        <span className="sub">Media: <b>{(r.media_classification || "").replaceAll("_", " ")}</b></span>
      </div>
      <Stages stages={stages} current={r.pipeline_stage} state={r.processing_state} />
      {r.error && <div className="err">Processing failed: {r.error}</div>}
      <div className="kv">
        <span>Description</span><span>{r.description || "—"}</span>
        <span>Source</span><span>{r.source_type}{r.source_platform ? ` · ${r.source_platform}` : ""}{r.source_url && <> · <a href={r.source_url} target="_blank" rel="noreferrer">source link</a></>}</span>
        <span>Incident</span><span>{r.incident_id ? <a href="#" onClick={(e) => { e.preventDefault(); openIncident(r.incident_id); }}>Incident #{r.incident_id}</a> : "Not associated"}</span>
        <span>Reporter credibility</span><span data-testid="reporter-cred">{(() => {
          const c = r.assessment?.fields?.reporter_credibility;
          if (!c) return <span className="sub">unavailable (anonymous report: no reporter name given)</span>;
          const col = c.score > 80 ? "#16a34a" : c.score < 30 ? "#dc2626" : undefined;
          return <><b style={{ color: col }}>{c.score} / 100</b>{r.reporter_ref && <ReporterProfile name={r.reporter_ref} />} <span className="sub">{c.score > 80 ? "Trusted reporter" : c.score < 30 ? "Untrusted reporter" : "Neutral"}{r.assessment?.fields?.reporter_review ? " · needs manual review" : ""} · from {c.reports} report(s); derived from their reports' outcomes</span></>;
        })()}</span>
        <span>Report confidence</span><span>{r.report_confidence != null ? <Score v={r.report_confidence} label="Report confidence" />
          : <span className="sub">unavailable: {["PROCESSED", "MAPPED"].includes(r.processing_state) ? "no assessment recorded for this report" : `not yet assessed (processing state ${r.processing_state})`}</span>}</span>
      </div>
      {r.media?.length > 0 && (
        <div className="media-grid">
          {r.media.map((m) => m.kind === "video"
            ? <video key={m.id} src={m.url} controls poster={m.thumb_url} />
            : <a key={m.id} href={m.url} target="_blank" rel="noreferrer"><img src={m.thumb_url || m.url} alt={m.original_filename} /></a>)}
        </div>
      )}
      <div className="tabs">
        {["assessment", "media", "location_time", "visual", "history", "review"].map((t) => (
          <button key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>{t.replace("_", " / ")}</button>
        ))}
      </div>
      {tab === "assessment" && (
        <div>
          <p><b>{a.status}</b> — {a.reason}</p>
          {r.override_status && <p className="sub">Human override → {r.override_status}: “{r.override_reason}”</p>}
          <div className="grid2">
            <div><h4>Supporting</h4><ul className="list">{(a.supporting || []).map((s, i) => <li key={i}>{s}</li>)}{!a.supporting?.length && <li className="sub">none</li>}</ul></div>
            <div><h4>Contradicting</h4><ul className="list">{(a.contradicting || []).map((s, i) => <li key={i}>{s}</li>)}{!a.contradicting?.length && <li className="sub">none</li>}</ul></div>
            <div><h4>Concerns</h4><ul className="list">{(a.concerns || []).map((s, i) => <li key={i}>{s}</li>)}{!a.concerns?.length && <li className="sub">none</li>}</ul></div>
            <div><h4>Missing / unavailable evidence</h4><ul className="list">{(a.missing || []).map((s, i) => <li key={i}>{s}</li>)}</ul></div>
          </div>
          <h4 style={{ marginTop: 12 }}>Grid eligibility</h4>
          <div className="row">{Object.entries(r.grid_eligibility || {}).map(([k, v]) => <Badge key={k} cls={v ? "clear" : "contradiction"}>{v ? "✔" : "✖"} {k.replaceAll("_", " ")}</Badge>)}</div>
          <p className="sub">{a.score_note} Last assessed {fmtTime(a.assessed_at)}.</p>
          {a.contributions && <p className="sub">Score factors: {a.contributions.map((c) => `${c.factor} (${c.weight > 0 ? "+" : ""}${c.weight})`).join(", ")}</p>}
        </div>
      )}
      {tab === "media" && (
        <div>
          {r.media_conclusion && (
            <div className="finding" style={{ borderColor: "var(--accent)" }}>
              <div className="sub">CONCLUSION (derived from the evidence below)</div>
              <div className="ft" style={{ margin: "4px 0" }}><span className="badge b-accent">{r.media_conclusion.classification.replaceAll("_", " ")}</span></div>
              <p style={{ margin: "4px 0" }}>{r.media_conclusion.conclusion}</p>
              {r.media_conclusion.triggered_by.length > 0 && <div className="sub">Triggered by: {r.media_conclusion.triggered_by.map((t) => `${t.check.replaceAll("_", " ")} → ${t.finding.replaceAll("_", " ").toLowerCase()} (confidence ${t.confidence}; ${t.method})`).join("; ")}</div>}
              {r.media_conclusion.other_concerns.length > 0 && <div className="sub">Other concerns: {r.media_conclusion.other_concerns.map((t) => t.finding.replaceAll("_", " ").toLowerCase()).join(", ")}</div>}
              {r.media_conclusion.checks_unavailable.length > 0 && <div className="sub">Checks unavailable: {r.media_conclusion.checks_unavailable.join("; ")}</div>}
              {r.media_conclusion.uncertainty.length > 0 && <><div className="sub" style={{ marginTop: 4 }}><b>Uncertainty</b></div><ul className="list sub">{r.media_conclusion.uncertainty.map((u, i) => <li key={i}>{u}</li>)}</ul></>}
              <div className="sub">{r.media_conclusion.never_fake_note}</div>
            </div>
          )}
          <h4 style={{ margin: "10px 0 6px" }}>Evidence: individual checks</h4>
          <Findings list={media} empty="No media submitted with this report." />
          {r.media?.map((m) => (
            <details key={m.id} style={{ marginTop: 6 }}><summary className="sub">Technical metadata — media #{m.id} ({m.kind}, {m.original_filename})</summary>
              <div className="kv" style={{ marginTop: 6 }}>
                <span>SHA-256 (file)</span><span style={{ wordBreak: "break-all" }}>{m.sha256}</span>
                <span>Pixel content hash</span><span style={{ wordBreak: "break-all" }}>{m.content_hash || "—"}</span>
                <span>pHash / dHash</span><span>{m.phash || "—"} / {m.dhash || "—"}</span>
                <span>Variant hashes</span><span>{Object.entries(m.phash_variants || {}).map(([k, v]) => `${k}:${v}`).join(" · ") || "—"}</span>
                {m.frame_phashes?.length > 0 && <><span>Video frame hashes</span><span>{m.frame_phashes.join(" · ")}</span></>}
                <span>Dimensions</span><span>{m.width}×{m.height}</span>
                {m.container_time && <><span>Container time</span><span>{fmtTime(m.container_time)}</span></>}
                {m.client_last_modified && <><span>File modified (browser)</span><span>{fmtTime(m.client_last_modified)}</span></>}
                {m.exif?.present && <><span>EXIF</span><span>{Object.entries(m.exif.tags || {}).map(([k, v]) => `${k}: ${v}`).join(" · ")}</span></>}
              </div>
            </details>))}
        </div>
      )}
      {tab === "location_time" && (
        <div>
          <LocationCompare summary={r.location_summary} finding={r.location_finding} />
          <h4 style={{ margin: "12px 0 6px" }}>Location & time findings</h4>
          <Findings list={lt} />
          <h4>Observations (each with its source and limitation)</h4>
          <table><thead><tr><th>Kind</th><th>Source</th><th>Value</th><th>Limitation</th></tr></thead><tbody>
            {r.observations.map((o) => (
              <tr key={o.id}><td>{o.kind}</td><td>{SRC_LABEL[o.source] || o.source}</td>
                <td>{o.lat != null ? `${o.lat.toFixed(5)}, ${o.lon.toFixed(5)}` : ""}{o.time_value ? fmtTime(o.time_value) : ""}{o.label && <div className="sub">{o.label}</div>}</td>
                <td className="sub">{o.limitation}</td></tr>))}
          </tbody></table>
          <div className="kv" style={{ marginTop: 10 }}>
            <span>Investigated location</span><span>{r.investigated_lat != null ? `${r.investigated_lat.toFixed(5)}, ${r.investigated_lon.toFixed(5)}` : "unresolved"} · confidence <b>{r.location_confidence}</b></span>
            <span>Method</span><span>{r.location_method || "—"}</span>
            <span>Time assessment</span><span>{r.time_assessment || "—"}</span>
          </div>
        </div>
      )}
      {tab === "visual" && (
        <div>
          {(() => {
            const vf = r.assessment?.fields || {};
            const va = r.visual_analysis[0];
            const running = ["pending", "running"].includes(vf.analysis_status) || ["pending", "running"].includes(va?.status);
            if (running) return (
              <div className="finding row" role="status" data-testid="visual-running" style={{ gap: 10 }}>
                <style>{"@keyframes pralay-spin{to{transform:rotate(360deg)}}"}</style>
                <span aria-hidden="true" style={{ width: 18, height: 18, border: "3px solid #94a3b8", borderTopColor: "transparent", borderRadius: "50%", animation: "pralay-spin 0.9s linear infinite", flex: "none" }} />
                <span>AI analysis is currently running in the background. This takes about 2-3 minutes...</span>
              </div>);
            if (vf.visual_relevance && vf.visual_relevance !== "NOT_ANALYSED") return (
              <div className="finding" data-testid="visual-verdict">
                <div className="row" style={{ gap: 8 }}><b>Visual relevance:</b>
                  <span className="badge" style={vf.needs_review ? { background: "#dc2626", color: "#fff", borderColor: "#dc2626" } : undefined}>{vf.visual_relevance}</span>
                  <span className="sub">claimed {vf.user_claimed_category} · AI saw {(vf.ai_detected_category || "—").replaceAll("_", " ")}</span></div>
                {vf.model_description && <p style={{ margin: "6px 0" }}>Model: “{vf.model_description}”</p>}
                <div className="sub">Analysis completed at {fmtTime(va?.created_at || r.updated_at)}</div>
              </div>);
            if (va?.status && va.status !== "completed") return <div className="finding sub">AI analysis did not complete ({va.status}{va.error ? `: ${va.error}` : ""}); the report stays unverified by the image.</div>;
            return null;
          })()}
          {r.visual_analysis.length === 0 && <div className="empty">No visual analysis recorded.</div>}
          {r.visual_analysis.map((v) => (
            <div key={v.id} className="finding">
              <div className="ft"><Badge v={v.status === "completed" ? "clear" : v.status === "pending" || v.status === "running" ? "PROCESSING" : "unavailable"}>{v.status}</Badge> {v.provider}
                {v.output?._meta && <span className="sub">· local model · {(v.output._meta.latency_ms / 1000).toFixed(1)} s</span>}</div>
              {v.error && <p className="sub">{v.error}</p>}
              {v.output && <>
                <p>{v.output.description}</p>
                <ul className="list">{(v.output.disaster_indicators || []).map((d, i) => <li key={i}>{d.category} ({d.strength}): {d.evidence}</li>)}</ul>
                {v.output.limitations?.length > 0 && <p className="sub">Model-stated limitations: {v.output.limitations.join("; ")}</p>}
              </>}
              {v.output?._meta?.sanitizer_notes?.length > 0 && <p className="sub">Sanitizer: {v.output._meta.sanitizer_notes.join("; ")}</p>}
              <p className="sub">Observations from a local vision model (no cloud). They are evidence, not proof that a disaster occurred, and never a verdict on authenticity.</p>
            </div>))}
        </div>
      )}
      {tab === "history" && (
        <div>
          <h4>Processing history</h4>
          <table><tbody>{r.status_history.map((h) => <tr key={h.id}><td className="sub">{fmtTime(h.created_at)}</td><td><Badge v={h.state} /></td><td>{h.stage}</td><td className="sub" style={{ wordBreak: "break-word" }}>{h.note}</td></tr>)}</tbody></table>
          <h4 style={{ marginTop: 12 }}>Assessment history</h4>
          <table><tbody>{r.assessment_history.map((h) => <tr key={h.id}><td className="sub">{fmtTime(h.created_at)}</td><td><Badge v={h.status} /></td><td>{h.score}</td><td className="sub">{h.trigger}</td></tr>)}</tbody></table>
          <h4 style={{ marginTop: 12 }}>Grid mapping history</h4>
          {r.grid_history.length ? <table><tbody>{r.grid_history.map((h) => <tr key={h.id}><td className="sub">{fmtTime(h.created_at)}</td><td>{h.action}</td><td>{h.cell_id}</td></tr>)}</tbody></table> : <p className="sub">Never mapped as verified.</p>}
        </div>
      )}
      {tab === "review" && (
        <div>
          <ReviewForm current={r.override_status} onSubmit={async (b) => { await api.reviewReport(r.id, b); setBump((x) => x + 1); }} />
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn sm" onClick={async () => { await api.reprocess(r.id); setBump((x) => x + 1); }}>Re-run pipeline</button>
          </div>
          <h4 style={{ marginTop: 12 }}>Review audit trail</h4>
          {r.review_history.length ? <table><tbody>{r.review_history.map((h) => <tr key={h.id}><td className="sub">{fmtTime(h.created_at)}</td><td>{h.reviewer}</td><td>{h.previous_status} → {h.new_status}</td><td>{h.reason}</td></tr>)}</tbody></table> : <p className="sub">No human reviews yet.</p>}
        </div>
      )}
    </div>
  );
}

export function Findings({ list, empty }) {
  if (!list.length) return <div className="empty">{empty || "No findings."}</div>;
  return list.map((f) => (
    <div key={f.id} className="finding">
      <div className="ft">
        <Badge v={f.severity} /> <span>{f.check_type.replaceAll("_", " ")}</span> → <span>{f.finding.replaceAll("_", " ")}</span>
        <span className="sub" style={{ marginLeft: "auto" }}>confidence: {f.confidence} · {f.status} · {fmtTime(f.created_at)}</span>
      </div>
      <div className="sub">Method: {f.method}</div>
      {f.limitation && <div className="sub">Limitation: {f.limitation}</div>}
      {f.related_report_id && <div className="sub">Related report #{f.related_report_id}</div>}
      {f.evidence && Object.keys(f.evidence).length > 0 && <pre>{JSON.stringify(f.evidence, null, 1)}</pre>}
    </div>
  ));
}
