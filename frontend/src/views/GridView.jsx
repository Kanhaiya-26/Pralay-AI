import { useState } from "react";
import { ago, api, CAT_LABEL, fmtTime } from "../api";
import { Badge, Err, Score, useAsync, useSplit } from "../components/common";
import MapView, { Legend } from "../components/MapView";

export default function GridView({ meta, version, openReport, openIncident }) {
  const [f, setF] = useState({ category: "", status: "", since: "", until: "", mapped_only: false, place: "" });
  const [bbox, setBbox] = useState(null);
  const [placeMsg, setPlaceMsg] = useState(null);
  const [cell, setCell] = useState(null);
  const [res, setRes] = useState("");
  const [cfgMsg, setCfgMsg] = useState(null);
  const [w, drag] = useSplit("grid", [560, 0]);
  const iso = (v) => (v ? new Date(v).toISOString() : "");
  const cells = useAsync(() => api.gridCells({ category: f.category, status: f.status, since: iso(f.since), until: iso(f.until), mapped_only: f.mapped_only || "", bbox }),
    [version, f.category, f.status, f.since, f.until, f.mapped_only, bbox]);
  const incs = useAsync(() => api.incidents({ category: f.category, status: f.status }), [version, f.category, f.status]);
  const detail = useAsync(() => (cell ? api.gridCell(cell) : Promise.resolve(null)), [cell, version]);
  const cfg = cells.data?.config;

  const applyPlace = async () => {
    if (!f.place.trim()) { setBbox(null); setPlaceMsg(null); return; }
    try {
      const g = await api.geocode(f.place);
      const d = 0.5;
      setBbox([g.lat - d, g.lon - d, g.lat + d, g.lon + d].map((x) => x.toFixed(3)).join(","));
      setPlaceMsg(`Showing cells within ±0.5° (~50 km) of ${(g.label || f.place).split(",").slice(0, 2).join(",")}`);
    } catch (e) { setPlaceMsg(e.message); }
  };

  const reconf = async () => {
    setCfgMsg(null);
    try { const c = await api.setGrid(parseFloat(res)); setCfgMsg(`Grid reconfigured to ${c.resolution_deg}° (version ${c.version}); all reports re-mapped.`); setCell(null); }
    catch (e) { setCfgMsg(e.message); }
  };

  const list = cells.data?.cells || [];
  return (
    <>
      <div className="panel section" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <div className="row"><h2 style={{ flex: 1 }}>Disaster Control Grid Intelligence</h2>
          {cfg && <span className="sub">Provider: {cfg.provider === "regular_latlon" ? `regular lat/lon lattice, ${cfg.resolution_deg}°` : "GeoJSON polygon file"} · grid v{cfg.version} · extent {cfg.extent?.map((x) => (+x).toFixed(1)).join(", ")} · cell prefix {cfg.cell_prefix}</span>}</div>
        <div className="banner grid">⚠ {cells.data?.label || "CUSTOM / DEMO GRID — NOT AN OFFICIAL IMD/NDMA GRID"}. Official IMD/NDMA grid specifications were not available; this configurable grid is a stand-in. An official cell definition can be loaded later as a GeoJSON file (GRID_PROVIDER=geojson) without code changes.</div>
        <div className="filters">
          <select value={f.category} onChange={(e) => setF({ ...f, category: e.target.value })}><option value="">All disaster types</option>{meta?.categories.map((c) => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}</select>
          <select value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}><option value="">All report statuses</option>{meta?.statuses.map((s) => <option key={s}>{s}</option>)}</select>
          <label className="row" style={{ flexDirection: "row", gap: 4 }}>From<input type="datetime-local" value={f.since} onChange={(e) => setF({ ...f, since: e.target.value })} /></label>
          <label className="row" style={{ flexDirection: "row", gap: 4 }}>To<input type="datetime-local" value={f.until} onChange={(e) => setF({ ...f, until: e.target.value })} /></label>
          <input placeholder="Location filter (place)" value={f.place} onChange={(e) => setF({ ...f, place: e.target.value })} onKeyDown={(e) => e.key === "Enter" && applyPlace()} style={{ width: 190 }} />
          <button className="btn sm" onClick={applyPlace}>Apply location</button>
          <label className="row" style={{ flexDirection: "row", gap: 6 }}><input type="checkbox" style={{ width: "auto" }} checked={f.mapped_only} onChange={(e) => setF({ ...f, mapped_only: e.target.checked })} />Verified-mapped only</label>
          <span style={{ flex: 1 }} />
          <input placeholder="Resolution °" value={res} onChange={(e) => setRes(e.target.value)} style={{ width: 110 }} />
          <button className="btn sm" onClick={reconf} disabled={!res || cfg?.provider !== "regular_latlon"} title={cfg?.provider !== "regular_latlon" ? "Resolution only applies to the regular demo grid" : ""}>Reconfigure grid</button>
        </div>
        {placeMsg && <span className="sub">{placeMsg}</span>}
        {cfgMsg && <span className="sub">{cfgMsg}</span>}
        <Legend />
      </div>
      <div className="split">
        <div className="pane panel" style={{ width: w[0] }}>
          <MapView incidents={incs.data || []} cells={list} selectedCell={cell} onCell={setCell} onIncident={openIncident} />
        </div>
        <div className="splitter" onMouseDown={drag(0)} />
        <div className="pane panel" style={{ flex: 1 }}>
          <div className="pane-head"><h3>Grid cells ({list.length})</h3></div>
          <div className="pane-body">
            <Err e={cells.error} />
            <table>
              <thead><tr><th>Cell</th><th>Status</th><th>Reports</th><th title="Report statuses: corroborated / unverified / contradicted">Reports ✔ / ? / ✖</th><th>Verified mapped</th><th>Incidents (by status)</th><th>Types</th><th>Last</th></tr></thead>
              <tbody>
                {list.map((c) => (
                  <tr key={c.cell_id} className="click" onClick={() => setCell(c.cell_id)} style={cell === c.cell_id ? { outline: "2px solid var(--accent)" } : null}>
                    <td><b>{c.cell_id}</b></td><td><Badge v={c.cell_status} /></td><td>{c.reports}</td>
                    <td>{c.CORROBORATED || 0} / {c.UNVERIFIED || 0} / {c.CONTRADICTED || 0}</td><td>{c.mapped_verified}</td><td>{c.incidents}<div className="row" style={{ gap: 3 }}>{Object.entries(c.incident_status || {}).map(([k, v]) => <Badge key={k} v={k}>{v} {k.toLowerCase()}</Badge>)}</div></td>
                    <td className="sub">{Object.entries(c.categories).map(([k, v]) => `${CAT_LABEL[k]} ${v}`).join(", ")}</td><td className="sub">{ago(c.last_update)}</td>
                  </tr>))}
                {!list.length && <tr><td colSpan={8} className="empty">No cells match the filters.</td></tr>}
              </tbody>
            </table>
            {detail.data && (
              <div className="finding" style={{ marginTop: 12 }}>
                <h3>Cell {detail.data.cell_id}</h3>
                {detail.data.current_grid_version === false && <p className="err">This cell belongs to an older grid version.</p>}
                {detail.data.bounds && <p className="sub">Bounds: {detail.data.bounds.map((x) => x.toFixed(3)).join(", ")} (lat_min, lon_min, lat_max, lon_max) — {detail.data.grid_label}</p>}
                <h4>Incidents</h4>
                <ul className="list">{detail.data.incidents.map((i) => <li key={i.id}><a href="#" onClick={(e) => { e.preventDefault(); openIncident(i.id); }}>#{i.id} {i.title}</a> — <Badge v={i.status} /></li>)}</ul>
                <h4 style={{ marginTop: 8 }}>Reports in this cell</h4>
                <table><tbody>{detail.data.reports.map((r) => (
                  <tr key={r.id} className="click" onClick={() => openReport(r.id)}><td>#{r.id}</td><td>{CAT_LABEL[r.category]}</td><td><Badge v={r.verification_status} /></td>
                    <td>{r.report_confidence != null ? <Score v={r.report_confidence} label="Report confidence" /> : <span className="sub">confidence unavailable: {r.confidence_unavailable_reason}</span>}</td>
                    <td>{r.grid_mapped ? <Badge v="MAPPED" /> : <span className="sub">not mapped</span>}</td><td className="sub">{r.claimed_location_text}</td><td className="sub">{fmtTime(r.upload_time)}</td></tr>))}</tbody></table>
                <h4 style={{ marginTop: 8 }}>Mapping history</h4>
                <table><tbody>{detail.data.mapping_history.map((h) => <tr key={h.id}><td className="sub">{fmtTime(h.created_at)}</td><td>report #{h.report_id}</td><td>{h.action}</td><td className="sub">grid v{h.grid_version}</td></tr>)}</tbody></table>
              </div>
            )}
          </div>
        </div>
      </div>
    </>
  );
}
