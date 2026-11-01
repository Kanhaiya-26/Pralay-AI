import { useEffect, useState } from "react";
import { api, CAT_LABEL, fmtTime } from "../api";
import { Badge, Err, Stages } from "../components/common";
import { reportPrefill } from "../components/ImageCheck";
import { getDeviceFingerprint } from "../utils/fingerprint";

const EMPTY = { category: "flood", description: "", claimed_location: "", claimed_event_time: "", source_type: "citizen", source_platform: "", source_url: "", original_publication_time: "", reporter_ref: "" };
const TYPES = /^(image\/(jpeg|png|webp)|video\/(mp4|quicktime|webm))$/;

function checkFile(f, maxMb) {
  if (!TYPES.test(f.type)) return `unsupported type (${f.type || "unknown"}) — use JPEG, PNG, WEBP, MP4, MOV or WEBM`;
  if (f.size > maxMb * 1024 * 1024) return `too large (${(f.size / 1048576).toFixed(1)} MB > ${maxMb} MB)`;
  if (f.size === 0) return "empty file";
  return null;
}

export default function Submit({ meta, openReport, version }) {
  // "Report this" from the image check pre-fills the form once.
  const [pre] = useState(() => { const p = reportPrefill.current; reportPrefill.current = null; return p; });
  const [f, setF] = useState(() => pre ? { ...EMPTY, category: pre.category || EMPTY.category, description: pre.description || "" } : EMPTY);
  const [files, setFiles] = useState(() => pre ? [pre.file] : []);
  const [geo, setGeo] = useState(null);
  const [geoMsg, setGeoMsg] = useState(null);
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState(null);
  const [err, setErr] = useState(null);
  const [done, setDone] = useState(null);
  const [inputKey, setInputKey] = useState(0);  // re-mount the native file input after a successful submit
  const maxMb = meta?.max_upload_mb || 25;
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const fileErrors = files.map((x) => checkFile(x, maxMb));

  const askGeo = () => {
    setGeoMsg("Requesting permission…");
    if (!navigator.geolocation) return setGeoMsg("Browser geolocation not supported.");
    navigator.geolocation.getCurrentPosition(
      (p) => { setGeo({ lat: p.coords.latitude, lon: p.coords.longitude, acc: p.coords.accuracy }); setGeoMsg(null); },
      () => setGeoMsg("Device location unavailable. Please type location or ensure image has EXIF."), { enableHighAccuracy: true, timeout: 10000 }
    );
  };

  const submit = async (e) => {
    e.preventDefault();
    setErr(null); setDone(null);
    if (fileErrors.some(Boolean)) return setErr("Fix or remove the files marked in red before submitting.");
    if (!f.claimed_location.trim()) return setErr("Location is required to submit a report.");
    if (f.claimed_event_time && new Date(f.claimed_event_time) > new Date(Date.now() + 3600e3)) return setErr("Event time cannot be in the future.");
    const fd = new FormData();
    Object.entries(f).forEach(([k, v]) => v && fd.append(k, k.endsWith("_time") ? new Date(v).toISOString() : v));
    if (geo) { fd.append("browser_lat", geo.lat); fd.append("browser_lon", geo.lon); fd.append("browser_accuracy_m", geo.acc); }
    try { fd.append("device_fingerprint", await getDeviceFingerprint()); } catch { /* fingerprint unavailable: IP rule still applies */ }
    files.forEach((x) => { fd.append("files", x); fd.append("file_last_modified", new Date(x.lastModified).toISOString()); });
    setBusy(true); setProgress(0);
    try {
      const r = await api.submitWithProgress(fd, setProgress);
      setDone(r);
      setF(EMPTY); setFiles([]); setInputKey((k) => k + 1);
    } catch (e2) { setErr(e2.message); } finally { setBusy(false); setProgress(null); }
  };

  return (
    <div style={{ maxWidth: 960, width: "100%", margin: "0 auto" }}>
      <form className="panel section" onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <h2>Submit a disaster report</h2>
        <p className="sub">Reports enter the automated verification pipeline immediately. Provide what you know: missing details count as unavailable evidence, not as signs of fakery.</p>
        <label>Photos / videos (JPEG, PNG, WEBP, MP4, MOV, WEBM · max {maxMb} MB each, up to 5)
          <input key={inputKey} type="file" multiple accept="image/jpeg,image/png,image/webp,video/mp4,video/quicktime,video/webm"
            onChange={(e) => { setFiles([...e.target.files].slice(0, 5)); if (e.target.files.length && !geo) askGeo(); }} />
        </label>
        {files.length > 0 && (
          <table><tbody>{files.map((x, i) => (
            <tr key={i}>
              <td style={{ width: 64 }}>{x.type.startsWith("image") && !fileErrors[i] ? <img className="thumb" src={URL.createObjectURL(x)} /> : <div className="thumb">{x.type.startsWith("video") ? "🎞" : "?"}</div>}</td>
              <td>{x.name}<div className="sub">{x.type || "unknown type"} · {(x.size / 1048576).toFixed(2)} MB · modified {fmtTime(new Date(x.lastModified).toISOString())}</div></td>
              <td>{fileErrors[i] ? <span className="badge b-contradiction">✖ {fileErrors[i]}</span> : <span className="badge b-clear">✔ ready</span>}</td>
              <td><button type="button" className="btn sm" onClick={() => setFiles(files.filter((_, j) => j !== i))}>remove</button></td>
            </tr>))}</tbody></table>
        )}
        <div className="grid2">
          <label>Disaster category<select value={f.category} onChange={set("category")}>{meta?.categories.map((c) => <option key={c} value={c}>{CAT_LABEL[c]}</option>)}</select></label>
          <label><span>Claimed location (place name) <span style={{ color: "#dc2626" }} aria-hidden="true">*</span></span><input value={f.claimed_location} onChange={set("claimed_location")} placeholder="e.g. Phase 7, Mohali, Punjab" aria-required="true" /></label>
        </div>
        <label>Description<textarea rows={3} value={f.description} onChange={set("description")} placeholder="What do you see? Water depth, damage, road status…" /></label>
        <div className="grid2">
          <label>When did it happen? (optional)<input type="datetime-local" value={f.claimed_event_time} onChange={set("claimed_event_time")} /></label>
          <label>Reporter reference (optional)<input value={f.reporter_ref} onChange={set("reporter_ref")} placeholder="name / phone / handle" /></label>
        </div>
        <details>
          <summary className="sub" style={{ cursor: "pointer" }}>Source details (for a social media / news post you are forwarding)</summary>
          <div className="grid2" style={{ marginTop: 8 }}>
            <label>Source type<select value={f.source_type} onChange={set("source_type")}>{meta?.source_types.filter((s) => s !== "demo").map((s) => <option key={s}>{s}</option>)}</select></label>
            <label>Platform<input value={f.source_platform} onChange={set("source_platform")} placeholder="e.g. X, Instagram, news site" /></label>
            <label>Source URL<input value={f.source_url} onChange={set("source_url")} placeholder="https://…" /></label>
            <label>Original publication time<input type="datetime-local" value={f.original_publication_time} onChange={set("original_publication_time")} /></label>
          </div>
        </details>
        <div className="finding">
          <div className="row"><b style={{ flex: 1 }}>Share device location (optional)</b><button type="button" className="btn sm" onClick={askGeo}>Use my location</button></div>
          <p className="sub">Records where your device is now, which is not necessarily where the photo was taken. Only sent with your explicit permission.</p>
          {geo && <p className="sub" data-testid="geo-ok" style={{ color: "#16a34a" }}>📍 Live device location captured: {geo.lat.toFixed(5)}, {geo.lon.toFixed(5)} (±{Math.round(geo.acc)} m) <a href="#" onClick={(e) => { e.preventDefault(); setGeo(null); }}>remove</a></p>}
          {geoMsg && <p className="sub" data-testid="geo-msg" style={{ color: "#d97706" }}>{geoMsg}</p>}
          <p className="sub">Location priority: photo EXIF GPS first, then this device location, then the typed place name.</p>
        </div>
        <p className="sub">To prevent abuse, an anonymous device identifier and your IP address are recorded with each report and never shown publicly. Repeated mismatched reports block submissions from this device for 24 hours.</p>
        <Err e={err} />
        {progress !== null && <div><div className="bar" style={{ height: 8 }}><i style={{ width: `${progress}%` }} /></div><span className="sub">Uploading… {progress}%</span></div>}
        <div className="row"><button className="btn primary" disabled={busy}>{busy ? "Uploading…" : "Submit report"}</button></div>
        {done && <Tracker done={done} version={version} openReport={openReport} stages={meta?.pipeline_stages || []} />}
      </form>
    </div>
  );
}

function Tracker({ done, version, openReport, stages }) {
  const [r, setR] = useState(null);
  useEffect(() => { api.report(done.id).then(setR).catch(() => {}); }, [done.id, version]);
  const finished = r && ["PROCESSED", "MAPPED", "FAILED_NEEDS_REVIEW"].includes(r.processing_state);
  return (
    <div className="finding">
      <div className="row"><b style={{ flex: 1 }}>✔ Report #{done.id} received {fmtTime(done.received_at)}</b>
        {done.priority === 1 && <span className="badge b-accent">PRIORITY — near an active incident</span>}</div>
      <div className="sub">{done.files?.length || 0} file(s) stored: {(done.files || []).map((x) => `${x.name} (${x.kind}, ${(x.size_bytes / 1048576).toFixed(2)} MB)`).join(", ") || "none"}</div>
      {r && <>
        <div className="row" style={{ margin: "8px 0" }}><Badge v={r.processing_state} />{finished && <Badge v={r.effective_status} />}
          {r.grid_mapped ? <span className="badge b-MAPPED">MAPPED · {r.grid_cell_id}</span> : null}
          {r.processing_ms != null && <span className="sub">processed in {(r.processing_ms / 1000).toFixed(1)} s (measured)</span>}
          {r.attempts > 1 && <span className="sub">attempt {r.attempts}</span>}</div>
        <Stages stages={stages} current={r.pipeline_stage} state={r.processing_state} />
        {finished && r.assessment && <p className="sub" style={{ marginTop: 6 }}>{r.assessment.reason}</p>}
        {r.error && <div className="err">{r.processing_state === "RECEIVED" ? "Retry scheduled after error: " : "Error: "}{r.error}</div>}
      </>}
      <button type="button" className="btn sm" style={{ marginTop: 6 }} onClick={() => openReport(done.id)}>Open full investigation →</button>
    </div>
  );
}
