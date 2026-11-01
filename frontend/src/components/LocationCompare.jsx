import { useEffect, useRef } from "react";
import L from "leaflet";
import "leaflet/dist/leaflet.css";
import { Badge } from "./common";

const fmt = (p) => (p && p.lat != null ? `${(+p.lat).toFixed(5)}, ${(+p.lon).toFixed(5)}` : "—");

/** Side-by-side claimed / extracted GPS / investigated location with a mini map, for human reviewers. */
export default function LocationCompare({ summary, finding }) {
  const el = useRef(null);
  useEffect(() => {
    if (!summary || !el.current) return;
    const m = L.map(el.current, { zoomControl: true, attributionControl: false, preferCanvas: true });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18 }).addTo(m);
    const pts = [];
    const c = summary.claimed, g = summary.extracted_gps, d = summary.device, inv = summary.investigated;
    if (c) {
      pts.push([c.lat, c.lon]);
      L.circle([c.lat, c.lon], { radius: (c.extent_km || 1) * 1000, color: "#eab308", weight: 1, fillOpacity: 0.08 }).addTo(m).bindTooltip("Approximate extent of claimed place");
      L.circle([c.lat, c.lon], { radius: (summary.thresholds_km?.agree || 15) * 1000, color: "#22c55e", weight: 1, dashArray: "4 4", fill: false }).addTo(m).bindTooltip("Agreement threshold");
      L.circleMarker([c.lat, c.lon], { radius: 7, color: "#eab308", fillOpacity: 0.9 }).addTo(m).bindTooltip("Claimed (geocoded)");
    }
    if (g) { pts.push([g.lat, g.lon]); L.circleMarker([g.lat, g.lon], { radius: 7, color: "#3b82f6", fillOpacity: 0.9 }).addTo(m).bindTooltip("EXIF GPS"); }
    if (d) { pts.push([d.lat, d.lon]); L.circleMarker([d.lat, d.lon], { radius: 6, color: "#a855f7", fillOpacity: 0.9 }).addTo(m).bindTooltip("Device at upload"); }
    if (c && g) L.polyline([[c.lat, c.lon], [g.lat, g.lon]], { color: "#94a3b8", dashArray: "5 5" }).addTo(m);
    if (inv) L.circleMarker([inv.lat, inv.lon], { radius: 11, color: "#fff", weight: 2, fill: false }).addTo(m).bindTooltip("Investigated location");
    if (pts.length) m.fitBounds(L.latLngBounds(pts).pad(0.5), { maxZoom: 12, animate: false });
    else m.setView([22.5, 79], 4);
    return () => { m.stop(); m.off(); m.eachLayer((l) => { if (!(l instanceof L.TileLayer) && !(l instanceof L.Renderer)) m.removeLayer(l); }); m.remove(); };  // vector layers first, then the map
  }, [summary]);

  if (!summary) return <div className="empty">No location summary recorded (report processed before this view existed). Re-run the pipeline from the Review tab to generate it.</div>;
  const c = summary.claimed, g = summary.extracted_gps, inv = summary.investigated;
  const dist = finding?.distance_km || {};
  return (
    <div>
      <div className="grid3">
        <div className="finding"><div className="sub">CLAIMED</div><b>{c?.label || "none given"}</b>
          <div className="sub">{c ? `${fmt(c)} · place extent ≈${c.extent_km ?? "?"} km${c.place_type ? ` (${c.place_type})` : ""}` : "No place name or pin"}</div>
          <div className="sub">Source: reporter's claim, geocoded by OpenStreetMap</div></div>
        <div className="finding"><div className="sub">EXTRACTED (EXIF GPS)</div><b>{g ? fmt(g) : "not available"}</b>
          <div className="sub">{g ? [g.dop != null && `DOP ${g.dop}`, g.altitude_m != null && `alt ${g.altitude_m} m`, g.utc_time && `GPS clock ${g.utc_time}`].filter(Boolean).join(" · ") || "no precision data" : "Stripped by platform, disabled, or never recorded. Missing GPS is NOT evidence of fakery."}</div>
          {dist.exif_vs_claim != null && <div className="sub"><b>{dist.exif_vs_claim} km</b> from claim</div>}</div>
        <div className="finding"><div className="sub">INVESTIGATED</div><b>{inv ? fmt(inv) : "unresolved"}</b>
          <div className="sub">{inv?.method || "No usable location evidence"}</div>
          <div className="row" style={{ marginTop: 4 }}><Badge cls={{ high: "clear", medium: "clear", low: "neutral", contradicted: "contradiction" }[inv?.confidence] || "info"}>confidence: {inv?.confidence || "unresolved"}</Badge>
            {finding && <Badge v={finding.severity}>{finding.finding.replaceAll("_", " ")}</Badge>}</div></div>
      </div>
      <div className="kv" style={{ margin: "8px 0" }}>
        <span>Device location</span><span>{summary.device ? `${fmt(summary.device)}${dist.device_vs_claim != null ? ` · ${dist.device_vs_claim} km from claim` : ""} (where the phone was at upload, not necessarily the scene)` : "not shared"}</span>
        <span>Visual landmark check</span><span>{summary.landmark_check}</span>
        <span>Thresholds</span><span>agree ≤ {summary.thresholds_km?.agree} km · contradict &gt; {summary.thresholds_km?.contradict} km (scaled to the claimed place's size)</span>
      </div>
      {finding?.notes?.length > 0 && <ul className="list">{finding.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
      <div ref={el} style={{ height: 260, borderRadius: 10, marginTop: 8 }} />
      <div className="map-legend" style={{ marginTop: 4 }}>
        <span><span className="dot" style={{ background: "#eab308" }} /> claimed</span><span><span className="dot" style={{ background: "#3b82f6" }} /> EXIF GPS</span>
        <span><span className="dot" style={{ background: "#a855f7" }} /> device</span><span>○ investigated</span><span>dashed ring = agreement threshold</span>
      </div>
    </div>
  );
}
