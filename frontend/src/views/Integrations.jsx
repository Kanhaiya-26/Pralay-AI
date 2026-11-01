import { useState } from "react";
import { ago, api, fmtTime } from "../api";
import { Badge, Err, useAsync } from "../components/common";

const STATE_CLS = { OK: "clear", FAILING: "contradiction", COOLDOWN: "concern", NOT_USED_YET: "info", DISABLED: "unavailable",
  NOT_CONFIGURED: "unavailable", AVAILABLE_MANUAL: "clear", ENABLED_POLLING: "clear", CONFIGURED_NOT_IMPLEMENTED: "concern",
  CONFIGURED_UNTESTED: "info", READY: "clear", MODEL_NOT_INSTALLED: "concern", BLOCKED_NON_LOCAL: "contradiction", UNREACHABLE: "concern", ERROR: "contradiction" };

export const StateBadge = ({ s }) => <Badge cls={STATE_CLS[s] || "info"}>{String(s).replaceAll("_", " ")}</Badge>;

export default function Integrations({ version }) {
  const [bump, setBump] = useState(0);
  const src = useAsync(() => api.sources(), [version, bump]);
  const gem = useAsync(() => api.vision(), [bump]);
  const con = useAsync(() => api.connectors(), [version, bump]);
  const runs = useAsync(() => api.connectorRuns(), [version, bump]);
  const arch = useAsync(() => api.archive(), [version, bump]);
  const [gemBusy, setGemBusy] = useState(false);
  const [tags, setTags] = useState("");
  const [runRes, setRunRes] = useState(null);
  const [runBusy, setRunBusy] = useState(false);
  const [runErr, setRunErr] = useState(null);
  const [archMsg, setArchMsg] = useState(null);

  const validate = async () => { setGemBusy(true); try { await api.validateVision(); setBump((x) => x + 1); } finally { setGemBusy(false); } };
  const runMastodon = async () => {
    setRunBusy(true); setRunErr(null); setRunRes(null);
    try { setRunRes(await api.runConnector("mastodon_public_tags", { tags: tags ? tags.split(",") : null, limit: 20 })); setBump((x) => x + 1); }
    catch (e) { setRunErr(e.message); } finally { setRunBusy(false); }
  };
  const addArchive = async (e) => {
    e.preventDefault();
    setArchMsg(null);
    const fd = new FormData(e.target);
    if (fd.get("first_published")) fd.set("first_published", new Date(fd.get("first_published")).toISOString());
    try { await api.addArchive(fd); setArchMsg("✔ Registered. Future reports matching this image will be flagged as previously known."); e.target.reset(); setBump((x) => x + 1); }
    catch (e2) { setArchMsg("⚠ " + e2.message); }
  };
  const g = gem.data;

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="panel section">
        <h2>External evidence sources</h2>
        <p className="sub">Live health of every external integration. "No match" (the source answered but had nothing relevant) is recorded separately from "unavailable" (the request failed or was skipped). Rate-limited sources enter a cooldown and stale cached copies are labelled as stale.</p>
        <Err e={src.error} />
        <table style={{ marginTop: 8 }}>
          <thead><tr><th>Source</th><th>State</th><th>Attempts / successes</th><th>Last attempt</th><th>Last success</th><th>Last error</th><th>Cooldown until</th></tr></thead>
          <tbody>{(src.data?.sources || []).map((s) => (
            <tr key={s.source}><td><b>{s.label}</b><div className="sub">{s.source}</div></td><td><StateBadge s={s.state} /></td>
              <td>{s.attempts ?? 0} / {s.successes ?? 0}{s.consecutive_failures ? <div className="err">{s.consecutive_failures} consecutive failure(s)</div> : null}</td>
              <td className="sub">{ago(s.last_attempt_at)}</td><td className="sub">{ago(s.last_success_at)}</td>
              <td className="sub" style={{ maxWidth: 280, wordBreak: "break-word" }}>{s.last_error || "—"}</td><td className="sub">{s.cooldown_until ? fmtTime(s.cooldown_until) : "—"}</td></tr>))}</tbody>
        </table>
        {src.data?.simulated_failures?.length > 0 && <div className="banner" style={{ marginTop: 8 }}>TEST MODE: simulated failures active for {src.data.simulated_failures.join(", ")}</div>}
      </div>

      <div className="grid2" style={{ alignItems: "start" }}>
        <div className="panel section">
          <h3>Visual AI (local, free)</h3>
          {g && <div className="kv" style={{ marginTop: 8 }}>
            <span>State</span><span><StateBadge s={g.state} /></span>
            <span>Provider</span><span>{g.provider === "ollama" ? "Ollama (runs on this machine)" : "disabled"}</span>
            <span>Model</span><span>{g.model || "—"}</span>
            <span>Endpoint</span><span>{g.endpoint || "—"}</span>
            <span>Mode</span><span>{g.mode === "deferred" ? "deferred — reports finish first; AI observations are added afterwards" : "inline — analysed during processing"}</span>
            <span>Detail</span><span>{g.detail}</span>
            <span>Last check</span><span>{g.checked_at ? fmtTime(g.checked_at) : "—"}</span>
          </div>}
          <div className="row" style={{ marginTop: 10 }}>
            <button className="btn sm" onClick={validate} disabled={gemBusy || !g?.configured}>{gemBusy ? "Checking…" : "Check local model now"}</button>
          </div>
          <p className="sub" style={{ marginTop: 8 }}>No paid or cloud AI is used. Images never leave this machine: the vision model runs locally through Ollama
            and only loopback endpoints are accepted. The model reports observations (scene, indicators, visible text, landmarks); it never decides
            whether media is genuine or fake. To enable: install Ollama (free) from ollama.com, run <code>ollama pull qwen2.5vl:3b</code>, then
            set <code>VISION_PROVIDER=ollama</code> in <code>backend/.env</code>. Without it, visual analysis is shown as unavailable and everything else works.</p>
        </div>

        <div className="panel section">
          <h3>Known-media archive (recycled-media check)</h3>
          <p className="sub">Register images known to be from earlier events (e.g. a viral photo from a past flood). Only hashes are stored. Reports whose media matches an entry published &gt;72 h before their claimed event are flagged as previously known.</p>
          <form onSubmit={addArchive} className="grid2" style={{ marginTop: 8 }}>
            <label>Image<input type="file" name="file" accept="image/jpeg,image/png,image/webp" required /></label>
            <label>First published<input type="datetime-local" name="first_published" required /></label>
            <label>Source name<input name="source_name" required placeholder="e.g. Newspaper X, 2023 floods" /></label>
            <label>Source URL (optional)<input name="source_url" placeholder="https://…" /></label>
            <label style={{ gridColumn: "1 / -1" }}>Description<input name="description" /></label>
            <div><button className="btn sm primary">Register</button></div>
          </form>
          {archMsg && <p className="sub">{archMsg}</p>}
          <table style={{ marginTop: 8 }}><tbody>{(arch.data || []).map((a) => (
            <tr key={a.id}><td>#{a.id}</td><td>{a.source_name}{a.is_demo ? <> <span className="badge b-demo">SYNTHETIC DEMO</span></> : null}<div className="sub">{a.description}</div></td>
              <td className="sub">first published {fmtTime(a.first_published)}</td></tr>))}</tbody></table>
        </div>
      </div>

      <div className="panel section">
        <h3>Social-media ingestion connectors</h3>
        <p className="sub">Only authorised or documented public APIs are used; nothing is scraped. Connectors without credentials are shown as NOT CONFIGURED and fetch nothing.</p>
        <table style={{ marginTop: 8 }}>
          <thead><tr><th>Connector</th><th>Access</th><th>State</th><th>Detail</th><th>Last run</th></tr></thead>
          <tbody>{(con.data || []).map((c) => (
            <tr key={c.name}><td><b>{c.platform}</b><div className="sub">{c.name}</div></td><td className="sub">{c.kind}</td><td><StateBadge s={c.state} /></td>
              <td className="sub" style={{ maxWidth: 380 }}>{c.detail}</td>
              <td className="sub">{c.last_run ? `${ago(c.last_run.started_at)} · ${c.last_run.status} · fetched ${c.last_run.fetched}, ingested ${c.last_run.ingested}` : "—"}</td></tr>))}</tbody>
        </table>
        <div className="finding" style={{ marginTop: 10 }}>
          <b>Run Mastodon public-tag ingestion now</b>
          <p className="sub">Fetches recent public posts with image/video attachments for the given hashtags. By default, only posts whose text names a place that geocodes inside India are ingested; they enter the normal pipeline as <i>social_post</i> reports with the post URL, author handle and original publication time. These are real third-party posts, not demo data.</p>
          <div className="row"><input placeholder="tags, e.g. flood,landslide (blank = configured defaults)" value={tags} onChange={(e) => setTags(e.target.value)} style={{ maxWidth: 380 }} />
            <button className="btn sm primary" onClick={runMastodon} disabled={runBusy}>{runBusy ? "Fetching…" : "Run now"}</button></div>
          <Err e={runErr} />
          {runRes && runRes.status === "started" && <div className="sub" style={{ marginTop: 6 }}>⟳ {runRes.detail} Place names are geocoded at most once per second (OpenStreetMap policy), so a run can take a few minutes.</div>}
          {runRes && runRes.status !== "started" && <div className="sub" style={{ marginTop: 6 }}>
            Status <b>{runRes.status}</b> · fetched {runRes.fetched} · ingested {runRes.ingested}{runRes.report_ids?.length ? ` (reports ${runRes.report_ids.map((x) => "#" + x).join(", ")})` : ""}
            {Object.keys(runRes.skipped || {}).length > 0 && <div>Skipped: {Object.entries(runRes.skipped).map(([k, v]) => `${k} (${v})`).join("; ")}</div>}
            {runRes.errors?.length > 0 && <div className="err">Errors: {runRes.errors.join("; ")}</div>}
          </div>}
        </div>
        {runs.data?.length > 0 && <table style={{ marginTop: 8 }}><thead><tr><th>Run</th><th>Started</th><th>Status</th><th>Fetched</th><th>Ingested</th><th>Skipped</th><th>Error</th></tr></thead>
          <tbody>{runs.data.map((r) => <tr key={r.id}><td>#{r.id} {r.connector}</td><td className="sub">{fmtTime(r.started_at)}</td><td>{r.status === "running" ? <span className="badge b-PROCESSING">⟳ running</span> : r.status}</td><td>{r.fetched}</td><td>{r.ingested}</td><td>{r.skipped}</td><td className="sub">{r.error || "—"}</td></tr>)}</tbody></table>}
      </div>
    </div>
  );
}
