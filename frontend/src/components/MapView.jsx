import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { api, CAT_COLOR, CAT_LABEL } from "../api";
import { validPoint } from "../hotspots";

// Leaflet tooltips render HTML strings: every interpolated value must be escaped (titles derive from user input).
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (ch) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[ch]);

const STATUS_COLOR = { CORROBORATED: "#22c55e", UNVERIFIED: "#eab308", CONTRADICTED: "#ef4444" };
const CELL_COLOR = { VERIFIED: "#22c55e", UNVERIFIED: "#eab308", CONTRADICTED_ONLY: "#ef4444" };

/** Leaflet map: incident markers (category fill, status ring) + DEMO grid overlay (lattice + cells). */
export default function MapView({ incidents = [], cells = [], showGrid = true, selectedIncident, selectedCell, onIncident, onCell, height,
  hotspots = [], showHotspots = false, onHotspot, warnings = null, showWarnings = false }) {
  const el = useRef(null);
  const map = useRef(null);
  const layers = useRef({});
  const fitted = useRef(false);

  useEffect(() => {
    const m = L.map(el.current, { zoomControl: true, preferCanvas: true }).setView([22.5, 79], 5);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
      maxZoom: 18, attribution: "&copy; OpenStreetMap contributors",
    }).addTo(m);
    // Separate panes keep status markers above density/grid. SVG incident paths
    // let clicks outside the markers reach the hotspot layer underneath.
    m.createPane("pralay-cells").style.zIndex = 410;
    m.createPane("pralay-warnings").style.zIndex = 405;
    m.createPane("pralay-hotspots").style.zIndex = 430;
    m.createPane("pralay-incidents").style.zIndex = 450;
    layers.current = {
      lattice: L.layerGroup().addTo(m), cells: L.layerGroup().addTo(m), inc: L.layerGroup().addTo(m),
      warnings: L.layerGroup().addTo(m), warnRenderer: L.svg({ pane: "pralay-warnings" }),
      hotspots: L.layerGroup().addTo(m), hotspotRenderer: L.svg({ pane: "pralay-hotspots" }),
      cellRenderer: L.canvas({ pane: "pralay-cells" }), incRenderer: L.svg({ pane: "pralay-incidents" }),
    };
    map.current = m;
    const ro = new ResizeObserver(() => m.invalidateSize());
    ro.observe(el.current);
    // stop() cancels any running pan/zoom animation; removing a map mid-animation throws "_leaflet_pos" errors on tab switches
    return () => {
      ro.disconnect(); m.stop(); m.off();
      // Remove vector layers while their canvas renderers still exist; otherwise Leaflet may schedule a redraw on a
      // renderer that map.remove() already tore down ("Cannot read properties of undefined (reading 'save')").
      Object.values(layers.current).forEach((l) => l && l.clearLayers && l.clearLayers());
      m.remove(); map.current = null; layers.current = {};  // late draws become no-ops
    };  // late draws become no-ops
  }, []);

  // lattice lines for the current viewport
  useEffect(() => {
    const m = map.current;
    if (!m) return;
    let active = true;
    let sequence = 0;
    const draw = () => {
      const request = ++sequence;
      const g = layers.current.lattice;
      g.clearLayers();
      if (!showGrid || m.getZoom() < 7) return;
      const b = m.getBounds();
      api.lattice([b.getSouth(), b.getWest(), b.getNorth(), b.getEast()].map((x) => x.toFixed(3)).join(","))
        .then((lt) => {
          if (!map.current || map.current !== m) return;  // map was destroyed while the lattice request was in flight
          if (!active || request !== sequence) return;
          g.clearLayers();
          const style = { color: "#cbd5e1", weight: 1.2, opacity: 0.75, interactive: false, dashArray: "6 5" };
          lt.lat_lines.forEach((la) => L.polyline([[la, b.getWest() - 1], [la, b.getEast() + 1]], style).addTo(g));
          lt.lon_lines.forEach((lo) => L.polyline([[b.getSouth() - 1, lo], [b.getNorth() + 1, lo]], style).addTo(g));
        })
        .catch(() => {});
    };
    draw();
    m.on("moveend", draw);
    return () => { active = false; m.off("moveend", draw); };
  }, [showGrid]);

  // Density is visually separate from status and never changes the incident markers.
  useEffect(() => {
    const g = layers.current.hotspots;
    if (!g) return;
    g.clearLayers();
    if (!showHotspots) return;
    hotspots.forEach((h) => {
      if (!validPoint(h.lat, h.lon)) return;
      const circle = L.circleMarker([h.lat, h.lon], {
        pane: "pralay-hotspots", renderer: layers.current.hotspotRenderer, className: "density-hotspot",
        radius: 25 + Math.min(22, Math.log2(h.reports.length) * 6),
        color: "#22d3ee", weight: 2, dashArray: "5 4", fillColor: "#22d3ee", fillOpacity: 0.15,
      }).addTo(g);
      circle.bindTooltip(`<b>Report concentration</b><br/>${esc(h.reports.length)} non-duplicate reports · ${esc(h.incidentIds.length)} incident(s)<br/>Not disaster severity. Click to inspect reports and evidence.`);
      const inspect = () => onHotspot?.(h.id);
      circle.on("click", inspect);
      const path = circle.getElement();
      if (path) {
        path.setAttribute("role", "button");
        path.setAttribute("tabindex", "0");
        path.setAttribute("aria-label", `Report hotspot: ${h.reports.length} reports, ${h.incidentIds.length} incidents`);
        path.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); inspect(); } });
      }
    });
    if (!fitted.current && hotspots.length && !incidents.some((i) => validPoint(i.center_lat, i.center_lon))) {
      const pts = hotspots.filter((h) => validPoint(h.lat, h.lon)).map((h) => [h.lat, h.lon]);
      if (pts.length) { map.current.fitBounds(L.latLngBounds(pts).pad(0.6), { maxZoom: 10, animate: false }); fitted.current = true; }
    }
  }, [hotspots, showHotspots, onHotspot, incidents]);

  // IMD official district warnings: official IMD district polygons only (unmatched districts are never drawn).
  useEffect(() => {
    const g = layers.current.warnings;
    if (!g) return;
    g.clearLayers();
    if (!showWarnings || !warnings?.features?.length) return;
    L.geoJSON(warnings, {
      pane: "pralay-warnings", renderer: layers.current.warnRenderer,
      style: (ft) => ft.properties.active
        ? { color: "#f97316", weight: 1.5, fillColor: "#f97316", fillOpacity: 0.25 }
        : { color: "#94a3b8", weight: 1, dashArray: "4 4", fillColor: "#94a3b8", fillOpacity: 0.08 },
      onEachFeature: (ft, layer) => {
        const p = ft.properties;
        layer.bindTooltip(`<b>${esc(p.district)}</b> · OFFICIAL_WARNING (${p.active ? "active" : "expired"})<br/>${esc(p.description).replace(/,\s*/g, "<br/>")}` +
          `Issued ${esc(new Date(p.issued_at).toLocaleString())} (${esc(Math.round((p.age_min || 0) / 6) / 10)} h ago) · expires ${esc(new Date(p.expires).toLocaleString())}<br/>` +
          `Source: ${esc(p.source)} · polygon: ${esc(p.geometry_source)}<br/><i>${esc(p.note)}</i>`, { sticky: true });
      },
    }).addTo(g);
  }, [warnings, showWarnings]);

  // grid cells
  useEffect(() => {
    const g = layers.current.cells;
    if (!g) return;
    g.clearLayers();
    if (!showGrid) return;
    cells.forEach((c) => {
      const [la0, lo0, la1, lo1] = c.bounds;
      const col = CELL_COLOR[c.cell_status] || "#eab308";
      const sel = selectedCell === c.cell_id;
      const shape = c.polygon ? (o) => L.polygon(c.polygon, o) : (o) => L.rectangle([[la0, lo0], [la1, lo1]], o);
      const r = shape({
        pane: "pralay-cells", renderer: layers.current.cellRenderer,
        color: sel ? "#ffffff" : col, weight: sel ? 3 : 1.5, fillColor: col, fillOpacity: c.mapped_verified ? 0.32 : 0.14,
      }).addTo(g);
      r.bindTooltip(
        `<b>${esc(c.cell_id)}</b><br/><i>${esc(c.grid_label || "DEMO grid")}</i><br/>${esc(c.reports)} report(s) · ${esc(c.mapped_verified)} verified-mapped<br/>` +
          `Reports ✔ ${esc(c.CORROBORATED || 0)} · ? ${esc(c.UNVERIFIED || 0)} · ✖ ${esc(c.CONTRADICTED || 0)}<br/>` +
          `Incidents: ${Object.entries(c.incident_status || {}).map(([k, v]) => `${esc(k.toLowerCase())} ${esc(v)}`).join(", ") || "none"}<br/>` +
          Object.entries(c.categories).map(([k, v]) => `${esc(CAT_LABEL[k] || k)}: ${esc(v)}`).join(", "),
        { sticky: true }
      );
      r.on("click", () => onCell && onCell(c.cell_id));
    });
  }, [cells, showGrid, selectedCell, onCell]);

  // incidents
  useEffect(() => {
    const g = layers.current.inc;
    if (!g) return;
    g.clearLayers();
    const pts = [];
    incidents.forEach((i) => {
      if (!validPoint(i.center_lat, i.center_lon)) return;
      pts.push([i.center_lat, i.center_lon]);
      const sel = selectedIncident === i.id;
      const mk = L.circleMarker([i.center_lat, i.center_lon], {
        pane: "pralay-incidents", renderer: layers.current.incRenderer,
        radius: sel ? 10 : 8, color: sel ? "#fff" : STATUS_COLOR[i.effective_status] || "#999",
        weight: sel ? 4 : 3, fillColor: CAT_COLOR[i.category] || "#64748b", fillOpacity: 0.85,
      }).addTo(g);
      mk.bindTooltip(`<b>#${esc(i.id)} ${esc(i.title)}</b><br/>${esc(i.effective_status)} · ${esc(i.report_count)} report(s)<br/>Cell: ${esc(i.grid_cell_id || "—")}<br/>
        Evidence: ${esc(i.freshness?.state || "unknown")}<br/><i>Incident centroid; marker size does not indicate severity</i>`);
      mk.on("click", () => onIncident && onIncident(i.id));
    });
    if (!fitted.current && pts.length && map.current) {
      map.current.fitBounds(L.latLngBounds(pts).pad(0.6), { maxZoom: 10, animate: false });
      fitted.current = true;
    }
  }, [incidents, selectedIncident, onIncident]);

  // pan to selection
  useEffect(() => {
    const i = incidents.find((x) => x.id === selectedIncident);
    if (i && validPoint(i.center_lat, i.center_lon) && map.current) map.current.panTo([i.center_lat, i.center_lon]);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIncident]);

  return <div ref={el} className="map" style={height ? { height, flex: "none" } : undefined} />;
}

export function Legend() {
  return (
    <div className="map-legend">
      <span>Ring:</span>
      {Object.entries(STATUS_COLOR).map(([k, c]) => (
        <span key={k} className="row" style={{ gap: 4 }}><span className="dot" style={{ border: `3px solid ${c}`, background: "transparent" }} />{k.toLowerCase()}</span>
      ))}
      <span>· Cell:</span>
      {Object.entries(CELL_COLOR).map(([k, c]) => (
        <span key={k} className="row" style={{ gap: 4 }}><span className="dot" style={{ background: c, borderRadius: 2 }} />{k.replace("_", " ").toLowerCase()}</span>
      ))}
    </div>
  );
}
