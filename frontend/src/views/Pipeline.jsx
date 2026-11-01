import { useState } from "react";
import { ago, api, CAT_LABEL, fmtTime } from "../api";
import { Badge, Cat, Demo, Err, MismatchBadge, Score, Stages, useAsync } from "../components/common";

const STATES = ["RECEIVED", "PROCESSING", "PROCESSED", "MAPPED", "FAILED_NEEDS_REVIEW"];

export default function Pipeline({ meta, version, summary, openReport }) {
  const [f, setF] = useState({ state: "", status: "" });
  const reports = useAsync(() => api.reports(f), [version, f.state, f.status]);
  const pl = useAsync(() => api.pipeline(), [version]);
  const cache = useAsync(() => api.cache(), [version]);
  const R = summary?.reports;
  return (
    <>
      <div className="panel section">
        <h2>Active disaster processing</h2>
        <p className="sub">Every new report is processed automatically by background workers (persistent DB-backed queue, reports near active incidents first). Workflow state and verification status are tracked separately.</p>
        {R && (
          <div className="stats" style={{ marginTop: 10 }}>
            {[["Received", R.received_total], ["Awaiting", R.awaiting], ["Processing", R.processing], ["Processed", R.processed],
              ["Unverified", R.unverified], ["Corroborated", R.corroborated], ["Contradicted", R.contradicted], ["Mapped to grid", R.mapped], ["Failed / needs review", R.failed]]
              .map(([k, v]) => <div key={k} className="stat" style={{ border: "1px solid var(--line)", borderRadius: 10 }}><div className="k">{k}</div><div className="v">{v}</div></div>)}
          </div>
        )}
      </div>
      <div className="panel section resizable" style={{ minHeight: 300 }}>
        <div className="row" style={{ marginBottom: 8 }}>
          <h3 style={{ flex: 1 }}>Reports</h3>
          <div className="filters">
            <select value={f.state} onChange={(e) => setF({ ...f, state: e.target.value })}><option value="">All workflow states</option>{STATES.map((s) => <option key={s}>{s}</option>)}</select>
            <select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">All verification statuses</option>{meta?.statuses.map((s) => <option key={s}>{s}</option>)}</select>
          </div>
        </div>
        <Err e={reports.error} />
        <table>
          <thead><tr><th></th><th>#</th><th>Category</th><th>Claimed location</th><th>Workflow</th><th>Verification</th><th>Report conf.</th><th>Location conf.</th><th>Pipeline</th><th>Received</th><th>Processed in</th></tr></thead>
          <tbody>
            {(reports.data || []).map((r) => (
              <tr key={r.id} className="click" onClick={() => openReport(r.id)}>
                <td>{r.thumb_url ? <img className="thumb" style={{ width: 40, height: 40 }} src={r.thumb_url} /> : null}</td>
                <td>{r.id}<div><Demo on={r.is_demo} /></div></td><td><Cat c={r.category} /></td>
                <td>{r.claimed_location_text}<div className="sub">{r.incident_id ? `incident #${r.incident_id}` : "no incident"}{r.grid_cell_id ? ` · ${r.grid_cell_id}` : ""}</div></td>
                <td><Badge v={r.processing_state} /></td>
                <td><Badge v={r.effective_status} /> <MismatchBadge r={r} />{r.override_status && <div className="sub">human override</div>}{r.is_duplicate_of && <div className="sub">duplicate of #{r.is_duplicate_of}</div>}</td>
                <td><Score v={r.report_confidence} />{r.assessment?.fields?.needs_review && <span className="sub" style={{ color: "#dc2626" }}>(mismatch)</span>}</td><td>{r.location_confidence || "—"}</td>
                <td style={{ maxWidth: 260 }}>{r.processing_state === "PROCESSING" ? <Stages stages={meta?.pipeline_stages || []} current={r.pipeline_stage} state={r.processing_state} /> : <span className="sub">{(r.pipeline_stage || "queued").toLowerCase()}</span>}
                  {r.error && <div className="err">{r.error}</div>}</td>
                <td className="sub">{fmtTime(r.upload_time)}<div>{r.source_type}{r.ingest_connector ? ` · ${r.ingest_connector}` : ""}</div></td>
                <td className="sub">{r.processing_ms != null ? `${(r.processing_ms / 1000).toFixed(1)} s` : "—"}{r.attempts > 1 && <div>attempts: {r.attempts}</div>}{r.next_attempt_at && <div>retry at {fmtTime(r.next_attempt_at)}</div>}</td>
              </tr>))}
            {!reports.data?.length && <tr><td colSpan={11} className="empty">{reports.loading ? "Loading…" : "No reports match."}</td></tr>}
          </tbody>
        </table>
      </div>
      <div className="grid2" style={{ alignItems: "start" }}>
        <div className="panel section resizable" style={{ maxHeight: 520 }}>
          <h3>Live processing log</h3>
          <table><tbody>{(pl.data?.recent || []).map((h) => (
            <tr key={h.id}><td className="sub">{fmtTime(h.created_at)}</td><td>#{h.report_id}</td><td><Badge v={h.state} /></td><td>{h.stage}</td>
              <td className="sub" style={{ maxWidth: 260, wordBreak: "break-word" }}>{(h.note || "").slice(0, 160)}</td></tr>))}</tbody></table>
        </div>
        <div className="panel section resizable" style={{ maxHeight: 520 }}>
          <h3>Shared intelligence cache</h3>
          <p className="sub">Evidence cache: {cache.data?.http_cache?.entries ?? "—"} fetched resources, {cache.data?.http_cache?.hits ?? 0} reuse hits · geocode cache {cache.data?.geocode_cache?.entries ?? "—"} places.</p>
          <h4>Incident evidence freshness</h4>
          <table><tbody>{(cache.data?.incident_freshness || []).map((i) => (
            <tr key={i.id}><td>#{i.id}</td><td>{i.title}</td><td><Badge v={i.state} /></td><td className="sub">checked {ago(i.last_checked)} · TTL {i.ttl_min}m</td></tr>))}</tbody></table>
          <h4 style={{ marginTop: 10 }}>Exact query cache</h4>
          <table><tbody>{(cache.data?.query_cache || []).map((q) => (
            <tr key={q.norm_key}><td>{q.raw_query}<div className="sub">{q.norm_key}</div></td><td>{q.hit_count} hit(s)</td><td className="sub">expires {fmtTime(q.expires_at)}</td></tr>))}
            {!cache.data?.query_cache?.length && <tr><td className="sub">No investigations yet — ask the chatbot.</td></tr>}</tbody></table>
        </div>
      </div>
    </>
  );
}
