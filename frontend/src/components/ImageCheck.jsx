import { useEffect, useState } from "react";
import { api, CAT_LABEL } from "../api";

// One-shot hand-off to the Submit view ("Report this"): read once on mount, then cleared.
export const reportPrefill = { current: null };

const CHIP = { CLAIM_MISMATCH: "#dc2626", RECYCLED_MEDIA: "#dc2626", SUSPICIOUS_MANIPULATION: "#d97706", NO_STRONG_FAKE_SIGNAL: "#64748b" };

export function ImageIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="3" y="3" width="18" height="18" rx="2" /><circle cx="8.5" cy="8.5" r="1.5" /><path d="m21 15-5-5L5 21" />
    </svg>
  );
}

/** Quick image check: claim mismatch, recycled media, manipulation signal. Nothing is stored. */
export default function ImageCheckModal({ file, onClose }) {
  const [preview, setPreview] = useState(null);
  const [category, setCategory] = useState("");
  const [description, setDescription] = useState("");
  const [st, setSt] = useState({ busy: false, data: null, error: null });
  useEffect(() => {
    const u = URL.createObjectURL(file);
    setPreview(u);
    return () => URL.revokeObjectURL(u);
  }, [file]);
  useEffect(() => {
    const k = (e) => e.key === "Escape" && !st.busy && onClose();
    window.addEventListener("keydown", k);
    return () => window.removeEventListener("keydown", k);
  }, [st.busy, onClose]);
  const run = async () => {
    setSt({ busy: true, data: null, error: null });
    try {
      setSt({ busy: false, data: await api.analyzeImage(file, category, description), error: null });
    } catch (e) {
      setSt({ busy: false, data: null, error: e.message });
    }
  };
  const report = () => {
    reportPrefill.current = { file, category, description };
    onClose();
    location.hash = "submit";
  };
  const d = st.data;
  return (
    <div className="drawer-bg" style={{ justifyContent: "center", alignItems: "center" }} onClick={() => !st.busy && onClose()}>
      <div className="panel" role="dialog" aria-modal="true" aria-label="Image check" onClick={(e) => e.stopPropagation()}
        style={{ width: "min(760px, 94vw)", maxHeight: "90vh", overflow: "auto", padding: 18 }}>
        <div className="row" style={{ justifyContent: "space-between" }}>
          <h3 style={{ margin: 0 }}>Image check</h3>
          <button className="btn sm" onClick={onClose} disabled={st.busy} aria-label="Close image check">Close</button>
        </div>
        <div className="row" style={{ gap: 16, alignItems: "flex-start", marginTop: 12, flexWrap: "wrap" }}>
          <figure style={{ margin: 0, flex: "1 1 260px" }}>
            {preview && <img src={preview} alt="Selected image" style={{ width: "100%", borderRadius: 8 }} />}
            <figcaption className="sub">{file.name}</figcaption>
          </figure>
          {d && <figure style={{ margin: 0, flex: "1 1 260px" }}>
            <img src={`data:image/png;base64,${d.ela_png_b64}`} alt="Error Level Analysis heatmap" style={{ width: "100%", borderRadius: 8 }} />
            <figcaption className="sub">Error Level Analysis (×10). Bright, uneven patches can indicate local edits; this is a heuristic, not proof.</figcaption>
          </figure>}
        </div>
        {!d && <div style={{ display: "grid", gap: 8, marginTop: 12 }}>
          <label>Claimed category (optional)<select value={category} onChange={(e) => setCategory(e.target.value)} disabled={st.busy}>
            <option value="">Not specified</option>{Object.entries(CAT_LABEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label>Claimed description (optional)<input value={description} onChange={(e) => setDescription(e.target.value)} disabled={st.busy} /></label>
          <button className="btn primary" onClick={run} disabled={st.busy}>{st.busy ? "Analysing on this computer (can take ~2 min)…" : "Analyse image"}</button>
          {st.busy && <div className="sub" role="status">The local vision model runs on CPU; nothing is sent to external services.</div>}
        </div>}
        {st.error && <div className="err" role="alert" style={{ marginTop: 10 }}>Analysis failed: {st.error}</div>}
        {d && <div style={{ marginTop: 12 }}>
          <span className="badge" data-verdict={d.verdict} style={{ background: CHIP[d.verdict], color: "#fff", borderColor: CHIP[d.verdict] }}>{d.verdict}</span>
          {d.confidence != null && <span className="sub" style={{ marginLeft: 8 }}>signal strength {d.confidence}/100 (heuristic, not a probability)</span>}
          <ul style={{ marginTop: 8 }}>{d.reasons.map((r, i) => <li key={i}>{r}</li>)}</ul>
          <div className="sub">Signals: semantic {d.signals.semantic} · pHash match {String(d.signals.phash_match)} · EXIF {String(d.signals.exif_present)} · ELA high {String(d.signals.ela_high)} · model {d.model}</div>
          <p className="sub">These are signals, not a verdict on authenticity. NO_STRONG_FAKE_SIGNAL does not mean the image is real.</p>
          <div className="row" style={{ gap: 8 }}>
            <button className="btn primary" onClick={report}>Report this</button>
            <button className="btn" onClick={() => setSt({ busy: false, data: null, error: null })}>Analyse again</button>
          </div>
        </div>}
      </div>
    </div>
  );
}
