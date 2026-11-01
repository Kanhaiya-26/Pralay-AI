import { useState } from "react";
import { Err } from "./common";

export default function ReviewForm({ current, onSubmit }) {
  const [status, setStatus] = useState("CORROBORATED");
  const [reason, setReason] = useState("");
  const [reviewer, setReviewer] = useState("analyst");
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const go = async (st) => {
    setErr(null);
    if (reason.trim().length < 5) return setErr("A reason (min 5 characters) is required for every human override.");
    setBusy(true);
    try {
      await onSubmit({ status: st, reason, reviewer });
      setReason("");
    } catch (e) {
      setErr(e.message);
    } finally {
      setBusy(false);
    }
  };
  return (
    <div className="finding">
      <h4>Human review / override</h4>
      <p className="sub">Overrides take precedence over the automated status, are recorded with the reason, and trigger reassessment and grid re-check. Current override: <b>{current || "none"}</b></p>
      <div className="grid3">
        <label>New status<select value={status} onChange={(e) => setStatus(e.target.value)}>
          <option>CORROBORATED</option><option>UNVERIFIED</option><option>CONTRADICTED</option></select></label>
        <label>Reviewer<input value={reviewer} onChange={(e) => setReviewer(e.target.value)} /></label>
        <span />
      </div>
      <label style={{ marginTop: 8 }}>Reason (required)<textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} placeholder="e.g. Confirmed by district control room call at 14:10" /></label>
      <div className="row" style={{ marginTop: 8 }}>
        <button className="btn primary sm" disabled={busy} onClick={() => go(status)}>Apply override</button>
        {current && <button className="btn sm" disabled={busy} onClick={() => go(null)}>Clear override</button>}
      </div>
      <Err e={err} />
    </div>
  );
}
