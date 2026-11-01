import { useCallback, useEffect, useRef, useState } from "react";
import { CAT_COLOR, CAT_LABEL } from "../api";

export const Badge = ({ v, children, cls }) => <span className={`badge b-${cls || v}`}>{children || String(v || "—").replaceAll("_", " ")}</span>;
export const Demo = ({ on }) => (on ? <span className="badge b-demo" title="Synthetic demonstration data">DEMO DATA</span> : null);
export const Cat = ({ c }) => (
  <span className="badge" style={{ color: CAT_COLOR[c], borderColor: CAT_COLOR[c] + "66" }}>
    <span className="dot" style={{ background: CAT_COLOR[c] }} /> {CAT_LABEL[c] || c}
  </span>
);

/** Loud flag when the image does not show the claimed hazard (backend: assessment.fields.needs_review). */
export function MismatchBadge({ r }) {
  const f = r?.assessment?.fields;
  if (f && ["pending", "running"].includes(f.analysis_status))
    return <span className="badge" data-testid="ai-pending" title="The local vision model checks every image in the background (about 2 min on this computer). This row updates automatically."
      style={{ background: "#475569", color: "#fff", borderColor: "#475569" }}>AI CHECK PENDING</span>;
  if (!f?.needs_review) return null;
  const seen = f.ai_detected_category === "none_detected" ? "no disaster" : (f.ai_detected_category || "something else").replaceAll("_", " ");
  return (
    <span className="badge" data-testid="claim-mismatch" style={{ background: "#dc2626", color: "#fff", borderColor: "#dc2626", fontWeight: 700 }}
      title={`Claimed '${f.user_claimed_category}', but the image shows ${seen}${f.model_description ? `: ${f.model_description}` : ""}. Not counted as evidence.`}>
      CLAIM MISMATCH
    </span>
  );
}

export function Score({ v, label }) {
  if (v === null || v === undefined) return <span className="sub">—</span>;
  return (
    <span className="row" style={{ gap: 6 }} title={`${label || "Score"}: transparent additive heuristic (0-100), not a calibrated probability`}>
      <span className="bar" style={{ width: 70 }}><i style={{ width: `${v}%` }} /></span>
      <b style={{ fontSize: "0.82rem" }}>{Math.round(v)}</b>
    </span>
  );
}

export function Stages({ stages, current, state }) {
  const idx = stages.indexOf(current);
  const done = state === "PROCESSED" || state === "MAPPED";
  return (
    <div className="stages">
      {stages.map((s, i) => (
        <span key={s} className={`stage ${done || i < idx ? "done" : i === idx && state === "PROCESSING" ? "cur" : ""}`} title={s}>
          {s.replaceAll("_", " ").toLowerCase()}
        </span>
      ))}
    </div>
  );
}

export function Drawer({ onClose, title, children, actions }) {
  useEffect(() => {
    const f = (e) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", f);
    return () => window.removeEventListener("keydown", f);
  }, [onClose]);
  return (
    <div className="drawer-bg" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="drawer panel">
        <div className="drawer-head">
          <h3 style={{ flex: 1 }}>{title}</h3>
          {actions}
          <button className="btn sm" onClick={onClose}>Close ✕</button>
        </div>
        <div style={{ padding: "14px 18px" }}>{children}</div>
      </div>
    </div>
  );
}

/** Horizontal resizable split; widths (px) persisted per key. */
export function useSplit(key, initial) {
  const [w, setW] = useState(() => {
    try { return JSON.parse(localStorage.getItem("pralay.split." + key)) || initial; } catch { return initial; }
  });
  useEffect(() => localStorage.setItem("pralay.split." + key, JSON.stringify(w)), [key, w]);
  const drag = useCallback((i) => (e) => {
    e.preventDefault();
    const x0 = e.clientX, w0 = [...w];
    const move = (ev) => {
      const nw = [...w0];
      nw[i] = Math.max(220, Math.min(900, w0[i] + (i === 0 ? 1 : -1) * (ev.clientX - x0)));
      setW(nw);
    };
    const up = () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
  }, [w]);
  return [w, drag];
}

export function useAsync(fn, deps) {
  const [state, set] = useState({ data: null, error: null, loading: true });
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    let cancelled = false;
    set((s) => ({ ...s, loading: true }));
    fn().then((data) => !cancelled && set({ data, error: null, loading: false }))
      .catch((error) => !cancelled && set((s) => ({ data: s.data, error: error.message, loading: false })));
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return state;
}

export const Err = ({ e }) => (e ? <div className="err">⚠ {e}</div> : null);
