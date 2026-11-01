import { useMemo, useState } from "react";
import { ago, api, CAT_LABEL, fmtTime } from "../api";
import { Badge, Cat, Demo, Err, Score, useAsync, useSplit } from "../components/common";
import MapView, { Legend } from "../components/MapView";
import IncidentDetail from "./IncidentDetail";
import { StateBadge } from "./Integrations";
import GlobalSearch from "../components/GlobalSearch";
import { buildHotspots } from "../hotspots";

const WINDOWS = { "": "All time", 6: "Last 6 h", 24: "Last 24 h", 72: "Last 72 h", 168: "Last 7 days" };

export function Stats({ s }) {
  if (!s) return null;
  const R = s.reports, I = s.incidents;
  const items = [
    ["Reports received", R.received_total, `${R.demo} demo`], ["Awaiting / processing", R.awaiting + R.processing, `${R.failed} failed`],
    ["Processed", R.processed, `${R.duplicates} duplicates`], ["Unverified reports", R.unverified, ""],
    ["Corroborated reports", R.corroborated, ""], ["Contradicted reports", R.contradicted, ""],
    ["Mapped to grid", R.mapped, `${s.grid.cells_verified} verified cell(s)`], ["Incidents", I.total, `${I.active} active`],
    ["Corroborated incidents", I.corroborated, `${I.unverified} unverified · ${I.contradicted} contradicted`],
  ];
  return (
    <div className="stats">
      {items.map(([k, v, sub]) => (
        <div key={k} className="panel stat"><div className="k">{k}</div><div className="v">{v}</div><div className="s">{sub || " "}</div></div>
      ))}
    </div>
  );
}

export function SystemStatus({ summary, cells, goto }) {
  if (!summary) return null;
  const P = summary.processing || {};
  const top = (cells || []).slice(0, 4);
  return (
    <div className="grid3" style={{ gridTemplateColumns: "1.1fr 1.4fr 1fr" }}>
      <div className="panel stat">
        <div className="k">Processing (measured)</div>
        {P.count ? <>
          <div className="row" style={{ gap: 14, marginTop: 4 }}>
            <span><b style={{ fontSize: "1.3rem" }}>{(P.median_ms / 1000).toFixed(1)} s</b> <span className="sub">median</span></span>
            <span><b style={{ fontSize: "1.3rem" }}>{(P.p90_ms / 1000).toFixed(1)} s</b> <span className="sub">p90</span></span>
            <span className="sub">max {(P.max_ms / 1000).toFixed(1)} s · n={P.count}</span>
          </div>
          <div className="s">Slowest stage: {P.stages?.[0] ? `${P.stages[0].stage.replaceAll("_", " ").toLowerCase()} (avg ${(P.stages[0].avg_ms / 1000).toFixed(1)} s)` : "—"} · retries {P.retries} · lease recoveries {P.lease_recoveries}</div>
        </> : <div className="s" style={{ marginTop: 6 }}>No reports processed yet.</div>}
      </div>
      <div className="panel stat" style={{ cursor: "pointer" }} onClick={() => goto("sources")} title="Open Sources & AI">
        <div className="k">External sources & AI</div>
        <div className="row" style={{ gap: 6, marginTop: 6 }}>
          {(summary.sources || []).filter((x) => x.source !== "mastodon").map((x) => <span key={x.source} className="row" style={{ gap: 4 }}><span className="sub">{x.label.split(" (")[0]}</span><StateBadge s={x.state} /></span>)}
          <span className="row" style={{ gap: 4 }}><span className="sub">Visual AI (local)</span><StateBadge s={summary.vision?.state} /></span>
        </div>
      </div>
      <div className="panel stat" style={{ cursor: "pointer" }} onClick={() => goto("grid")} title="Open Grid intelligence">
        <div className="k">Grid-cell activity (demo grid)</div>
        {top.length ? <table style={{ marginTop: 4 }}><tbody>{top.map((c) => (
          <tr key={c.cell_id}><td style={{ padding: "2px 4px" }}>{c.cell_id}</td><td style={{ padding: "2px 4px" }}><Badge v={c.cell_status} /></td>
            <td style={{ padding: "2px 4px" }} className="sub">{c.reports} rep · {c.mapped_verified} verified</td></tr>))}</tbody></table>
          : <div className="s" style={{ marginTop: 6 }}>No located reports yet.</div>}
      </div>
    </div>
  );
}

function MapTab({ summary, version, meta, openReport, openIncident, goto }) {
  const [f, setF] = useState({ category: "", status: "", active: "", window: "" });
  const since = f.window ? new Date(Date.now() - f.window * 3600e3).toISOString() : "";
  const [sel, setSel] = useState(null);
  const [showGrid, setShowGrid] = useState(true);
  const [showHotspots, setShowHotspots] = useState(true);
  const [showWarnings, setShowWarnings] = useState(true);
  const warn = useAsync(() => api.imdWarnings(24), [version]);
  const [hotspotId, setHotspotId] = useState(null);
  const [w, drag] = useSplit("dash", [340, 520]);
  const inc = useAsync(() => api.incidents({ category: f.category, status: f.status, active: f.active, since }), [version, f.category, f.status, f.active, f.window]);
  const cells = useAsync(() => api.gridCells({ category: f.category, since }), [version, f.category, f.window]);
  const reports = useAsync(() => api.reports({ limit: 500, category: f.category, status: f.status }), [version, f.category, f.status]);
  const list = inc.data || [];
  const selected = list.some((i) => i.id === sel) ? sel : list[0]?.id;
  const density = useMemo(() => buildHotspots((reports.data || []).filter((r) => !since || r.upload_time >= since), list), [reports.data, inc.data, f.window]);
  const hotspot = showHotspots ? density.clusters.find((h) => h.id === hotspotId) : null;
  return (
    <>
      <div className="split dashboard-map-split" style={{ minHeight: 680 }}>
        <div className="pane panel" style={{ width: w[0] }}>
          <div className="pane-head">
            <h3 style={{ flex: 1 }}>Incident feed</h3>
            <div className="filters">
              <select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}>
                <option value="">All types</option>{meta?.categories.map((c) => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}</select>
              <select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
                <option value="">All statuses</option>{meta?.statuses.map((c) => <option key={c}>{c}</option>)}</select>
              <select value={f.active} onChange={(e) => setF({ ...f, active: e.target.value })}>
                <option value="">Active + past</option><option value="true">Active only</option><option value="false">Past only</option></select>
              <select value={f.window} onChange={(e) => setF({ ...f, window: e.target.value })} title="Filters incidents by last activity and grid cells by report time">
                {Object.entries(WINDOWS).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
            </div>
          </div>
          <div className="pane-body">
            <Err e={inc.error} />
            {inc.loading && !inc.data && <div className="empty">Loading incidents…</div>}
            {!list.length && !inc.loading && <div className="empty">{f.category || f.status || f.active || f.window ? "No incidents match these filters." : "No incidents yet. Submit a report to begin."}</div>}
            {list.map((i) => (
              <div key={i.id} className={`feed-item ${selected === i.id ? "sel" : ""}`} onClick={() => setSel(i.id)}>
                {i.thumb_url ? <img className="thumb" src={i.thumb_url} /> : <div className="thumb">📍</div>}
                <div>
                  <div className="feed-title">#{i.id} {i.title}</div>
                  <div className="row" style={{ gap: 6, marginTop: 4 }}><Cat c={i.category} /><Badge v={i.effective_status} />{i.active && <span className="badge b-accent">ACTIVE</span>}<Demo on={i.is_demo} /></div>
                  <div className="meta"><span>{i.report_count} report(s)</span><span>event {fmtTime(i.event_time_start)}</span><span>updated {ago(i.updated_at)}</span></div>
                  <div className="row" style={{ gap: 6, marginTop: 4 }}><span className="sub">Incident confidence</span><Score v={i.confidence_score} label="Incident confidence" /></div>
                </div>
              </div>
            ))}
          </div>
        </div>
        <div className="splitter" onMouseDown={drag(0)} title="Drag to resize" />
        <div className="pane panel gis-pane" style={{ flex: 1 }}>
          <div className="pane-head">
            <h3 style={{ flex: 1 }}>GIS map</h3>
            <label className="row" style={{ flexDirection: "row", gap: 6 }}><input type="checkbox" style={{ width: "auto" }} checked={showGrid} onChange={(e) => setShowGrid(e.target.checked)} /> Grid overlay</label>
            <label className="row" style={{ flexDirection: "row", gap: 6 }}><input type="checkbox" style={{ width: "auto" }} checked={showHotspots} onChange={(e) => { setShowHotspots(e.target.checked); setHotspotId(null); }} /> Report hotspots</label>
            <label className="row" style={{ flexDirection: "row", gap: 6 }}><input type="checkbox" style={{ width: "auto" }} checked={showWarnings} onChange={(e) => setShowWarnings(e.target.checked)} /> IMD warnings</label>
          </div>
          <div style={{ padding: "6px 12px" }}>
            <div className="banner grid" style={{ padding: "4px 10px", fontSize: "0.75rem" }}>{summary?.grid.label}</div>
            <div style={{ marginTop: 6 }}><Legend /></div>
            <p className="sub density-note">Cyan circles show nearby report concentration (within 5 km of an anchor report), not disaster severity. Incident rings show recorded verification status; open details to inspect evidence.</p>
            {showWarnings && <div className="density-status" role="status">
              {warn.error ? <span className="err">IMD warnings unavailable: {warn.error}</span> : !warn.data ? "Loading IMD warnings…" : <>
                IMD district nowcast warnings (last 24 h): {warn.data.features.length} on official district polygons, {warn.data.active} active, {warn.data.unmapped.length} not mapped (district name not matched)
                <span className="sub"> · Orange = active, grey dashed = expired · OFFICIAL_WARNING, not a confirmed incident.</span></>}
            </div>}
            {showHotspots && <div className="density-status" role="status">
              {reports.loading ? "Loading report locations…" : reports.error ? <span className="err">Report density unavailable: {reports.error}</span> : <>
                {density.clusters.length ? `${density.clusters.length} report hotspot(s)` : "Insufficient nearby report coordinates for hotspots; showing available incident markers."}
                <span className="sub"> {density.located} located non-duplicate reports · {density.duplicates} duplicates excluded · {density.unlocated} missing/uncertain locations excluded. Coverage: up to 500 latest reports, within the selected incident filters.</span>
              </>}
            </div>}
          </div>
          <MapView incidents={list} cells={cells.data?.cells || []} showGrid={showGrid} selectedIncident={selected} onIncident={setSel}
            hotspots={reports.error ? [] : density.clusters} showHotspots={showHotspots && !reports.loading} onHotspot={setHotspotId}
            warnings={warn.data} showWarnings={showWarnings} />
          {showHotspots && !reports.error && !reports.loading && <div className="hotspot-links" aria-label="Report hotspots">
            {density.clusters.map((h, index) => <button key={h.id} className="btn sm" aria-pressed={hotspot?.id === h.id} onClick={() => setHotspotId(h.id)}>
              Hotspot {index + 1}: {h.reports.length} reports · {h.incidentIds.length} incident(s)
            </button>)}
          </div>}
          {hotspot && <section className="hotspot-inspector" aria-label="Hotspot details">
            <div className="row"><h4 className="grow">Report concentration</h4><button className="btn sm" onClick={() => setHotspotId(null)} aria-label="Close hotspot details">Close</button></div>
            <p className="sub">{hotspot.reports.length} non-duplicate reports across {hotspot.incidentIds.length} incident(s). Report count is not a severity rating or proof of independent sources.</p>
            <div className="chips">{hotspot.incidentIds.map((id) => { const i = list.find((item) => item.id === id); return <button key={id} className="chip" onClick={() => { setSel(id); openIncident(id); }}>
              Incident #{id} · {i?.title} · {i?.effective_status}
            </button>; })}</div>
            <div className="chips" style={{ marginTop: 8 }}>{hotspot.reports.map((r) => <button key={r.id} className="chip" onClick={() => openReport(r.id)}>
              Report #{r.id} · {r.effective_status} · {r.location_confidence || "unknown"} location{r.is_demo ? " · DEMO" : ""}
            </button>)}</div>
          </section>}
        </div>
        <div className="splitter" onMouseDown={drag(1)} title="Drag to resize" />
        <div className="pane panel" style={{ width: w[1] }}>
          <div className="pane-head"><h3 style={{ flex: 1 }}>Incident detail</h3>{selected && <button className="btn sm" onClick={() => openIncident(selected)}>Open full review</button>}</div>
          <div className="pane-body">
            {selected ? <IncidentDetail id={selected} version={version} openReport={openReport} compact /> : <div className="empty">Select an incident.</div>}
          </div>
        </div>
      </div>
    </>
  );
}


const TABS = [["overview", "Overview"], ["recent", "Recent Disasters"], ["map", "GIS Map"]];

export default function Dashboard(props) {
  // "dtab" (not "tab": the report drawer already uses ?tab=location_time). Unknown values fall back to Overview.
  const [tab, setTabState] = useState(() => {
    const t = new URLSearchParams(location.search).get("dtab") || sessionStorage.getItem("pralay.dashTab") || "overview";
    return TABS.some(([k]) => k === t) ? t : "overview";
  });
  const setTab = (t) => {
    setTabState(t);
    sessionStorage.setItem("pralay.dashTab", t);
    const u = new URL(location.href);  // keep the URL in sync so reloads and shared links keep the tab
    u.searchParams.set("dtab", t);
    history.replaceState(null, "", u);
  };
  return (
    <>
      <GlobalSearch openIncident={props.openIncident} openReport={props.openReport} />
      <div className="tabs dash-tabs" role="tablist">
        {TABS.map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={`tab ${tab === k ? "active" : ""}`} onClick={() => setTab(k)}>{l}</button>)}
      </div>
      {tab === "overview" && <Overview {...props} setTab={setTab} />}
      {tab === "recent" && <RecentDisasters {...props} />}
      {tab === "map" && <MapTab {...props} />}
    </>
  );
}

function ReporterCredibility({ version }) {
  const u = useAsync(() => api.users(), [version]);
  const [open, setOpen] = useState(true);
  return (
    <div className="panel section cred-widget" data-testid="reporter-credibility">
      <div className="row"><h3 style={{ flex: 1, margin: 0 }}>Reporter Credibility</h3>
        <button className="btn sm" onClick={() => setOpen(!open)} aria-label={open ? "Collapse reporter credibility" : "Expand reporter credibility"}>{open ? "–" : "+"}</button></div>
      {open && <div style={{ marginTop: 8 }}>
      {u.error && <div className="err">Unavailable: {u.error}</div>}
      {u.data && !u.data.length && <div className="empty">No named reporters yet. Use 'Demo Reporter A' on submit.</div>}
      {(u.data || []).map((x) => {
        const col = x.credibility_score > 80 ? "#16a34a" : x.credibility_score < 30 ? "#dc2626" : undefined;
        return (
          <div key={x.reporter_name} className="activity-row" style={{ cursor: "default" }}>
            <span style={{ color: col, fontWeight: col ? 700 : 400 }}>{x.reporter_name}</span>
            <span className="sub">{x.reports_count} report(s)</span>
            <b style={{ marginLeft: "auto", color: col }}>{x.credibility_score} / 100</b>
          </div>);
      })}
      <div className="sub" style={{ marginTop: 6 }}>Derived from each reporter's report outcomes (+5 corroborated, −10 contradicted or claim mismatch). Anonymous reports are not scored.</div>
      </div>}
    </div>
  );
}

function Overview({ summary, version, openIncident, setTab, goto }) {
  const inc = useAsync(() => api.incidents({}), [version]);
  const cells = useAsync(() => api.gridCells({}), [version]);
  if (!summary) return <div className="empty">Loading…</div>;
  const R = summary.reports, I = summary.incidents;
  const kpi = [["Total Reports", R.received_total, `${R.demo} synthetic demo · ${R.duplicates} duplicate(s)`], ["Active Incidents", I.active, `of ${I.total} incidents`],
    ["Corroborated Incidents", I.corroborated, "independent evidence agrees"], ["Unverified Incidents", I.unverified, "insufficient evidence — not false"]];
  const recent = [...(inc.data || [])].sort((a, b) => (b.updated_at || "").localeCompare(a.updated_at || "")).slice(0, 6);
  return (
    <>
      <div className="stats kpi4">{kpi.map(([k, v, sub]) => <div key={k} className="panel stat kpi"><div className="k">{k}</div><div className="v">{v}</div><div className="s">{sub}</div></div>)}</div>
      <div className="sub" style={{ margin: "-4px 2px 0" }}>Contradicted incidents: {I.contradicted} · Contradicted reports: {R.contradicted} · Mapped to demo grid: {R.mapped}</div>
      <div className="grid2" style={{ alignItems: "start" }}>
        <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div className="panel section">
          <h3>Recent Activity</h3>
          {!recent.length && <div className="empty">No incidents yet.</div>}
          {recent.map((i) => (
            <div key={i.id} className="activity-row" onClick={() => openIncident(i.id)}>
              <Cat c={i.category} /> <span className="activity-title">{i.place_name || i.title}</span> <Badge v={i.effective_status} />
              <span className="sub" style={{ marginLeft: "auto" }}>{ago(i.updated_at)}</span>{i.is_demo ? <Demo on /> : null}
            </div>))}
        </div>
        </div>
        <div className="panel section" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div className="row"><h3 style={{ flex: 1 }}>GIS Preview</h3><button className="btn sm" onClick={() => setTab("map")}>Open GIS Map →</button></div>
          <div className="sub">CUSTOM / DEMO GRID — NOT AN OFFICIAL IMD/NDMA GRID</div>
          <MapView incidents={inc.data || []} cells={cells.data?.cells || []} onIncident={openIncident} height={300} />
        </div>
      </div>
      <details className="panel section system-health">
        <summary><b>System Health</b> <span className="sub">— external sources, processing performance, local AI, grid activity</span></summary>
        <div style={{ marginTop: 10 }}><SystemStatus summary={summary} cells={cells.data?.cells} goto={goto} /></div>
      </details>
      <ReporterCredibility version={version} />
    </>
  );
}

const CLASS = (i) => (i.origin === "reports" ? "Reported incident" : "External evidence only");
const evTime = (i) => i.event_time_end || i.event_time_start || i.created_at;

function RecentDisasters({ meta, version, openIncident }) {
  const [f, setF] = useState({ category: "", status: "", period: "", place: "", cls: "" });
  const inc = useAsync(() => api.incidents({}), [version]);
  const now = Date.now();
  const rows = (inc.data || [])
    .filter((i) => (!f.category || i.category === f.category) && (!f.status || i.effective_status === f.status)
      && (!f.cls || CLASS(i) === f.cls) && (!f.place || `${i.place_name} ${i.state_name} ${i.title}`.toLowerCase().includes(f.place.toLowerCase()))
      && (!f.period || now - new Date(evTime(i)).getTime() <= f.period * 3600e3))
    .sort((a, b) => (evTime(b) || "").localeCompare(evTime(a) || ""));
  return (
    <div className="panel section">
      <div className="row" style={{ marginBottom: 10 }}>
        <h2 style={{ flex: 1 }}>Recent Disasters</h2>
        <div className="filters">
          <select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}><option value="">All types</option>{meta?.categories.map((c) => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}</select>
          <select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">All statuses</option>{meta?.statuses.map((c) => <option key={c}>{c}</option>)}</select>
          <select value={f.period} onChange={(e) => setF({ ...f, period: e.target.value })}>{Object.entries({ "": "Any time", 24: "Last 24 h", 72: "Last 72 h", 168: "Last 7 days", 720: "Last 30 days" }).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
          <select value={f.cls} onChange={(e) => setF({ ...f, cls: e.target.value })}><option value="">All classifications</option><option>Reported incident</option><option>External evidence only</option></select>
          <input placeholder="Location" value={f.place} onChange={(e) => setF({ ...f, place: e.target.value })} style={{ width: 150 }} />
        </div>
      </div>
      <p className="sub">Sorted by event time (newest first). Event, ingestion and last-update times are shown separately; warnings and forecasts found by live search are not listed here as incidents.</p>
      {inc.loading && !inc.data && <div className="empty">Loading…</div>}
      {inc.error && <div className="err">⚠ {inc.error}</div>}
      {inc.data && !rows.length && <div className="empty">No incidents match these filters.</div>}
      {rows.map((i) => (
        <div key={i.id} className="disaster-row">
          <div style={{ flex: 1, minWidth: 220 }}>
            <div className="row" style={{ gap: 6 }}><b>#{i.id} {i.title}</b>{i.is_demo ? <Demo on /> : null}</div>
            <div className="meta"><span>{i.place_name}{i.state_name ? `, ${i.state_name}` : ""}</span><span>{CLASS(i)}</span></div>
          </div>
          <div className="row" style={{ gap: 6 }}><Cat c={i.category} /><Badge v={i.effective_status} /><Badge v={i.freshness?.state}>{i.freshness?.state === "fresh" ? "evidence fresh" : `evidence ${String(i.freshness?.state || "").replace("_", " ")}`}</Badge></div>
          <div className="sub disaster-times">event {fmtTime(evTime(i))}<br />ingested {fmtTime(i.created_at)}<br />updated {ago(i.updated_at)}</div>
          <div className="sub">{i.report_count} report(s)</div>
          <button className="btn sm" onClick={() => openIncident(i.id)}>View Details</button>
        </div>))}
    </div>
  );
}
