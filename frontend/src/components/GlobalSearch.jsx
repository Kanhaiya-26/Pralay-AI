import { useEffect, useRef, useState } from "react";
import { ago, api, CAT_LABEL, fmtTime } from "../api";
import { Badge, Demo } from "./common";
import ImageCheckModal, { ImageIcon } from "./ImageCheck";

export default function GlobalSearch({ openIncident, openReport }) {
  const [q, setQ] = useState("");
  const [st, setSt] = useState({ loading: false, data: null, error: null });
  const generation = useRef(0);
  const input = useRef(null);
  const picker = useRef(null);
  const [imgFile, setImgFile] = useState(null);
  const change = (value) => {
    generation.current += 1; // invalidate immediately, including Clear and Escape
    setQ(value);
    setSt({ loading: !!value.trim(), data: null, error: null });
  };
  useEffect(() => {
    const text = q.trim();
    if (!text) return;
    const id = ++generation.current;
    let alive = true;
    // The existing endpoint accepts numeric IDs; retain the requested entity kind.
    const typed = /^(incident|report)\s*#?\s*(\d+)$/i.exec(text);
    const h = setTimeout(async () => {
      try {
        const result = await api.search(typed ? typed[2] : text);
        if (!alive || id !== generation.current) return;
        const kind = typed?.[1].toLowerCase();
        setSt({ loading: false, error: null, data: {
          incidents: kind === "report" ? [] : (result.incidents || []).filter((i) => !typed || i.id === +typed[2]),
          reports: kind === "incident" ? [] : (result.reports || []).filter((r) => !typed || r.id === +typed[2]),
        } });
      } catch (e) {
        if (alive && id === generation.current) setSt({ loading: false, data: null, error: e.message });
      }
    }, 300);
    return () => { alive = false; clearTimeout(h); };
  }, [q]);
  const d = st.data;
  return (
    <section className="panel dashboard-search" aria-label="Dashboard search">
      <div className="search-input-row">
        <span className="search-symbol" aria-hidden="true">⌕</span>
        <input ref={input} type="search" value={q} onChange={(e) => change(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Escape") change(""); }}
          placeholder="Search disasters, locations, incident IDs, reports..." aria-label="Global search"
          aria-controls="dashboard-search-results" aria-expanded={!!q.trim()} />
        {q && <button className="btn sm" onClick={() => { change(""); input.current?.focus(); }} aria-label="Clear search">Clear</button>}
        <button className="btn sm" onClick={() => picker.current?.click()} aria-label="Check an image" title="Check an image (claim mismatch, recycled media, manipulation signals)"><ImageIcon /></button>
        <input ref={picker} type="file" accept="image/*" hidden data-testid="image-check-input"
          onChange={(e) => { if (e.target.files[0]) setImgFile(e.target.files[0]); e.target.value = ""; }} />
      </div>
      {imgFile && <ImageCheckModal file={imgFile} onClose={() => setImgFile(null)} />}
      {q.trim() && <div id="dashboard-search-results" className="search-results" aria-label="Search results" aria-busy={st.loading}>
        {st.loading && <div className="search-message" role="status">Searching…</div>}
        {st.error && <div className="err search-message" role="alert">Search failed: {st.error}</div>}
        {d && !d.incidents.length && !d.reports.length && <div className="search-message" role="status">No incidents or reports match “{q}”.</div>}
        {d?.incidents.map((i) => <button key={`i${i.id}`} className="search-row" onClick={() => openIncident(i.id)}>
          <b>Incident #{i.id}</b> <span>{i.title}</span><Badge v={i.effective_status} />
          <span className="sub">{i.place_name || ""} · updated {ago(i.updated_at)}</span><Demo on={i.is_demo} />
        </button>)}
        {d?.reports.map((r) => <button key={`r${r.id}`} className="search-row" onClick={() => openReport(r.id)}>
          <b>Report #{r.id}</b> <span>{CAT_LABEL[r.category] || r.category} — {r.claimed_location_text || "no location"}</span>
          <Badge v={r.effective_status} /><span className="sub">received {fmtTime(r.upload_time)}</span><Demo on={r.is_demo} />
        </button>)}
      </div>}
    </section>
  );
}
