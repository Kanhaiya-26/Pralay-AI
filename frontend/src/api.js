// Static showcase (GitHub Pages): no backend exists. GET requests are answered from a bundled, read-only snapshot

// of the SYNTHETIC demo data; every action (POST) is refused with an explanation. Never used by the local app.

export const SHOWCASE = import.meta.env.VITE_SHOWCASE === "1";

const SHOWCASE_MSG = "Static showcase: this action needs the local PRALAY AI backend (see README → Local Windows deployment).";



function snapshotKey(pathname, params) {

  const base = pathname.replace(/^\/api\//, "").replace(/\//g, "__");

  if (base === "grid__cells") {

    const k = ["category", "status", "mapped_only"].map((x) => `${x}=${params.get(x) || ""}`).join("&");

    return `grid__cells@${k}`;

  }

  return base;

}



function filterList(base, list, params) {

  if (!Array.isArray(list)) return list;

  const g = (k) => params.get(k);

  if (base === "incidents") {

    return list.filter((i) => (!g("category") || i.category === g("category")) && (!g("status") || i.effective_status === g("status"))

      && (!g("active") || String(i.active) === g("active")) && (!g("since") || (i.last_activity_at || "") >= g("since")));

  }

  if (base === "reports") {

    return list.filter((r) => (!g("state") || r.processing_state === g("state")) && (!g("status") || r.effective_status === g("status"))

      && (!g("incident_id") || String(r.incident_id) === g("incident_id")) && (!g("category") || r.category === g("category")));

  }

  return list;

}



async function showcaseReq(path, opts) {

  if ((opts.method || "GET").toUpperCase() !== "GET") throw new Error(SHOWCASE_MSG);

  const u = new URL(path, "http://showcase.local");

  if (u.pathname === "/api/grid/lattice") return { lat_lines: [], lon_lines: [], note: "Grid lines are not drawn in the static showcase" };

  if (u.pathname === "/api/geocode") throw new Error("Static showcase: place search needs the local backend.");

  const key = snapshotKey(u.pathname, u.searchParams);

  // Files are stored percent-encoded on disk; static hosts decode the URL once, so encode twice.
  const r = await fetch(`./snapshot/${encodeURIComponent(encodeURIComponent(key))}.json`);

  if (!r.ok) throw new Error("Not included in the static showcase snapshot.");

  return filterList(key, await r.json(), u.searchParams);

}



async function req(path, opts = {}) {

  if (SHOWCASE) return showcaseReq(path, opts);

  let r;

  try {

    r = await fetch(path, opts);

  } catch (e) {

    throw new Error("Backend unreachable — is the API server running on :8000?");

  }

  const text = await r.text();

  let body = null;

  try {

    body = text ? JSON.parse(text) : null;

  } catch {

    body = text;

  }

  if (!r.ok) {

    const d = body && body.detail;

    throw new Error(typeof d === "string" ? d : Array.isArray(d) ? d.map((x) => `${x.loc?.slice(-1)}: ${x.msg}`).join("; ") : `HTTP ${r.status}`);

  }

  return body;

}



const json = (method, body) => ({ method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });

const qs = (o) => {

  const p = Object.entries(o).filter(([, v]) => v !== undefined && v !== null && v !== "");

  return p.length ? "?" + new URLSearchParams(p).toString() : "";

};



export const api = {

  meta: () => req("/api/meta"),

  summary: () => req("/api/summary"),

  incidents: (f = {}) => req("/api/incidents" + qs(f)),

  incident: (id) => req(`/api/incidents/${id}`),

  refreshIncident: (id) => req(`/api/incidents/${id}/refresh`, { method: "POST" }),

  reviewIncident: (id, b) => req(`/api/incidents/${id}/review`, json("POST", b)),

  reports: (f = {}) => req("/api/reports" + qs(f)),

  report: (id) => req(`/api/reports/${id}`),

  reviewReport: (id, b) => req(`/api/reports/${id}/review`, json("POST", b)),

  reprocess: (id) => req(`/api/reports/${id}/reprocess`, { method: "POST" }),

  submit: (fd) => req("/api/reports", { method: "POST", body: fd }),

  users: () => req("/api/users"),

  userProfile: (name) => req(`/api/users/${encodeURIComponent(name)}`),

  analyzeImage: (file, category, description) => {
    const fd = new FormData();
    fd.append("file", file);
    if (category) fd.append("claimed_category", category);
    if (description) fd.append("claimed_description", description);
    return req("/api/analyze-image", { method: "POST", body: fd });
  },

  gridCells: (f = {}) => req("/api/grid/cells" + qs(f)),

  gridCell: (id) => req(`/api/grid/cells/${encodeURIComponent(id)}`),

  lattice: (bbox) => req("/api/grid/lattice" + qs({ bbox })),

  imdWarnings: (hours = 24) => req("/api/gis/imd-warnings" + qs({ hours })),
  acquisitionSources: () => req("/api/acquisition/sources"),

  setGrid: (res) => req("/api/grid/config", json("POST", { resolution_deg: res })),

  search: (q) => req("/api/search" + qs({ q })),
  geocode: (q) => req("/api/geocode" + qs({ q })),

  sources: () => req("/api/sources"),

  vision: () => req("/api/vision/status"),

  validateVision: () => req("/api/vision/validate", { method: "POST" }),

  connectors: () => req("/api/connectors"),

  connectorRuns: () => req("/api/connectors/runs"),

  runConnector: (name, body) => req(`/api/connectors/${name}/run`, json("POST", body || {})),

  archive: () => req("/api/archive"),

  addArchive: (fd) => req("/api/archive", { method: "POST", body: fd }),

  /** Multipart upload with progress callback (fetch has no upload progress). */

  submitWithProgress: (fd, onProgress) =>

    new Promise((resolve, reject) => {

      if (SHOWCASE) return reject(new Error(SHOWCASE_MSG));

      const x = new XMLHttpRequest();

      x.open("POST", "/api/reports");

      x.upload.onprogress = (e) => e.lengthComputable && onProgress && onProgress(Math.round((e.loaded / e.total) * 100));

      x.onerror = () => reject(new Error("Network error — backend unreachable"));

      x.onload = () => {

        let b = null;

        try { b = JSON.parse(x.responseText); } catch { b = null; }

        if (x.status >= 200 && x.status < 300) resolve(b);

        else reject(new Error(b && typeof b.detail === "string" ? b.detail : `Upload failed (HTTP ${x.status})`));

      };

      x.send(fd);

    }),

  chat: (q, context) => req("/api/chat", json("POST", { query: q, context: context || null })),

  cache: () => req("/api/cache/stats"),

  pipeline: () => req("/api/pipeline"),

};



export function fmtTime(s) {

  if (!s) return "—";

  const d = new Date(s);

  if (isNaN(d)) return s;

  return d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });

}



export function ago(s) {

  if (!s) return "never";

  const m = (Date.now() - new Date(s).getTime()) / 60000;

  if (m < 1) return "just now";

  if (m < 60) return `${Math.round(m)} min ago`;

  if (m < 1440) return `${Math.round(m / 60)} h ago`;

  return `${Math.round(m / 1440)} d ago`;

}



export const CAT_LABEL = {

  flood: "Flood", fire: "Fire", smoke: "Smoke", landslide: "Landslide", building_damage: "Building damage",

  road_blockage: "Road blockage", infrastructure_damage: "Infrastructure", cyclone_storm: "Cyclone / storm", other: "Other",

};

export const CAT_COLOR = {

  flood: "#3b82f6", fire: "#ef4444", smoke: "#9ca3af", landslide: "#a16207", building_damage: "#f97316",

  road_blockage: "#eab308", infrastructure_damage: "#8b5cf6", cyclone_storm: "#06b6d4", other: "#64748b",

};



