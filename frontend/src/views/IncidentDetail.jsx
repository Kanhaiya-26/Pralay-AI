import { useState } from "react";
import { ago, api, fmtTime } from "../api";
import { Badge, Cat, Demo, Err, Score, useAsync } from "../components/common";
import ReviewForm from "../components/ReviewForm";

export default function IncidentDetail({ id, version, openReport, compact }) {
  const [bump, setBump] = useState(0);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);
  const { data: i, error } = useAsync(() => api.incident(id), [id, version, bump]);
  if (error && !i) return <Err e={error} />;
  if (!i) return <div className="empty">Loading incident…</div>;
  const a = i.assessment || {};
  const refresh = async () => {
    setBusy(true); setMsg(null);
    try {
      const g = await api.refreshIncident(i.id);
      setMsg(`External evidence ${g.action}${g.new_records !== undefined ? ` · ${g.new_records} new record(s)` : ""}`);
      setBump((x) => x + 1);
    } catch (e) { setMsg(e.message); } finally { setBusy(false); }
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div>
        <div className="row"><Cat c={i.category} /><Badge v={i.effective_status} />{i.override_status && <span className="badge b-accent">HUMAN OVERRIDE</span>}
          {i.active && <span className="badge b-accent">ACTIVE</span>}<Demo on={i.is_demo} /></div>
        <h3 style={{ marginTop: 8 }}>#{i.id} {i.title}</h3>
        <div className="meta"><span>{i.place_name}{i.state_name ? `, ${i.state_name}` : ""}</span><span>Event {fmtTime(i.event_time_start)} – {fmtTime(i.event_time_end)}</span>
          <span>Updated {ago(i.updated_at)}</span><span>Cell {i.grid_cell_id || "—"}</span></div>
      </div>
      <div className="kv">
        <span>Incident confidence</span><span><Score v={i.confidence_score} label="Incident confidence" /></span>
        <span>Reports</span><span>{i.report_count} associated · {i.mapped_count} mapped to grid · {a.independent_reports ?? "—"} independent · {a.duplicate_reports ?? 0} duplicate(s)</span>
        <span>Evidence freshness</span><span><Badge v={i.freshness.state} /> last checked {ago(i.freshness.last_checked)} (TTL {i.freshness.ttl_min} min, {i.active ? "active" : "inactive"} incident)</span>
        <span>Location</span><span>{i.center_lat?.toFixed(4)}, {i.center_lon?.toFixed(4)} <span className="sub">(centroid of report locations — not an affected-area boundary)</span></span>
      </div>
      <div className="finding">
        <b>{a.status}</b> — {a.reason}
        {a.rules && <div className="row" style={{ marginTop: 6 }}>{Object.entries(a.rules).map(([k, v]) => <Badge key={k} cls={v ? "clear" : "info"}>{v ? "✔" : "·"} {k.replaceAll("_", " ")}</Badge>)}</div>}
        <div className="grid2" style={{ marginTop: 8 }}>
          <div><h4>Supporting</h4><ul className="list">{(a.supporting || []).map((s, k) => <li key={k}>{s}</li>)}{!a.supporting?.length && <li className="sub">none</li>}</ul></div>
          <div><h4>Contradicting</h4><ul className="list">{(a.contradicting || []).map((s, k) => <li key={k}>{s}</li>)}{!a.contradicting?.length && <li className="sub">none</li>}</ul>
            <h4 style={{ marginTop: 8 }}>Missing</h4><ul className="list">{(a.missing || []).map((s, k) => <li key={k}>{s}</li>)}</ul>
            {a.sources_unavailable?.length > 0 && <p className="sub">Sources unavailable this check: {a.sources_unavailable.join(", ")}</p>}</div>
        </div>
        <p className="sub">{a.score_note}</p>
      </div>
      <div>
        <div className="row"><h4 style={{ flex: 1 }}>Associated reports (individual report confidence shown separately)</h4></div>
        <table><thead><tr><th></th><th>#</th><th>Status</th><th>Report conf.</th><th>Location</th><th>Media</th><th>Grid</th></tr></thead><tbody>
          {i.reports.map((r) => (
            <tr key={r.id} className="click" onClick={() => openReport(r.id)}>
              <td>{r.thumb_url ? <img className="thumb" style={{ width: 40, height: 40 }} src={r.thumb_url} /> : null}</td>
              <td>{r.id}</td><td><Badge v={r.effective_status} />{r.is_duplicate_of && <div className="sub">dup of #{r.is_duplicate_of}</div>}</td>
              <td><Score v={r.report_confidence} /></td><td>{r.location_confidence}</td><td>{r.media_count}</td>
              <td>{r.grid_mapped ? <Badge v="MAPPED" /> : <span className="sub">{r.processing_state.toLowerCase()}</span>}</td>
            </tr>))}
        </tbody></table>
      </div>
      {!compact && <>
        <div>
          <div className="row"><h4 style={{ flex: 1 }}>External evidence</h4><button className="btn sm" disabled={busy} onClick={refresh}>{busy ? "Checking sources…" : "Force re-check sources"}</button></div>
          {msg && <p className="sub">{msg}</p>}
          <p className="sub">Stance = what the item says about this incident. Result = whether the source answered with a relevant item (match), answered with nothing relevant (no match), or could not be reached (unavailable). Retrieved = when the data was actually fetched (cached copies keep their original fetch time).</p>
          <table><thead><tr><th>Stance</th><th>Result</th><th>Source</th><th>Summary</th><th>Published</th><th>Retrieved</th></tr></thead><tbody>
            {i.evidence.map((e) => (
              <tr key={e.id}><td><Badge v={e.stance} /></td><td><Badge cls={{ match: "clear", no_match: "info", unavailable: "unavailable" }[e.result_type] || "info"}>{(e.result_type || "—").replace("_", " ")}</Badge></td>
                <td>{e.source_name}<div className="sub">{e.publisher} · {e.source_origin}{e.is_trusted && <> <span className="badge b-clear" data-testid="trusted-outlet"
                  title="Established outlet. Counts extra only if this article also passes the place, date and event checks (see Stance).">✔ Verified source</span></>}</div><div className="sub">group: {e.independence_group || "—"}</div></td>
                <td>{e.summary}{e.source_url && <div><a href={e.source_url} target="_blank" rel="noreferrer">open source ↗</a></div>}</td>
                <td className="sub">{fmtTime(e.publication_time)}</td><td className="sub">{fmtTime(e.retrieval_time)}</td></tr>))}
            {!i.evidence.length && <tr><td colSpan={6} className="sub">No external evidence gathered yet.</td></tr>}
          </tbody></table>
        </div>
        <div>
          <h4>Verification history</h4>
          <table><tbody>{i.assessment_history.map((h) => <tr key={h.id}><td className="sub">{fmtTime(h.created_at)}</td><td><Badge v={h.status} /></td><td>{h.score}</td><td className="sub">{h.trigger}</td></tr>)}</tbody></table>
        </div>
        <ReviewForm current={i.override_status} onSubmit={async (b) => { await api.reviewIncident(i.id, b); setBump((x) => x + 1); }} />
        {i.review_history.length > 0 && <table><tbody>{i.review_history.map((h) => <tr key={h.id}><td className="sub">{fmtTime(h.created_at)}</td><td>{h.reviewer}</td><td>{h.previous_status} → {h.new_status}</td><td>{h.reason}</td></tr>)}</tbody></table>}
      </>}
    </div>
  );
}
