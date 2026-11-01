import { useEffect, useRef, useState } from "react";
import { ago, api, fmtTime } from "../api";
import { Badge } from "../components/common";

const SUGGEST = ["Is there flooding in Mohali?", "Are there flood incidents near Mohali?", "Show active incidents near Chandigarh",
  "What evidence supports incident #1?", "Are there duplicate images?", "Which reports remain unverified?",
  "Which grid cells contain corroborated incidents?", "Show contradicted reports"];

// [text](https://url) -> link; only http(s) URLs (blocks javascript: etc.). Everything else stays plain text (no HTML injection).
function mdLinks(text) {
  const out = [], rx = /\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)|\*\*([^*]+)\*\*/g;
  let last = 0, m;
  while ((m = rx.exec(text || ""))) {
    out.push(text.slice(last, m.index));
    out.push(m[2] ? <a key={m.index} href={m[2]} target="_blank" rel="noreferrer">{m[1]}</a> : <b key={m.index}>{m[3]}</b>);
    last = rx.lastIndex;
  }
  out.push((text || "").slice(last));
  return out;
}

export default function Chat({ openIncident, openReport }) {
  const [msgs, setMsgs] = useState(() => {
    try { return JSON.parse(sessionStorage.getItem("pralay.chat") || "[]"); } catch { return []; }
  });
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const end = useRef(null);
  useEffect(() => { sessionStorage.setItem("pralay.chat", JSON.stringify(msgs.slice(-30))); end.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);
  // Deep link: ?ask=Is+there+flooding+in+Mohali%3F asks once on load.
  const asked = useRef(false);
  useEffect(() => {
    const a = new URLSearchParams(location.search).get("ask");
    if (a && !asked.current) { asked.current = true; ask(a); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const ask = async (text) => {
    const t = (text ?? q).trim();
    if (t.length < 2 || busy) return;
    setQ("");
    setMsgs((m) => [...m, { role: "user", text: t }]);
    setBusy(true);
    try {
      const lastCtx = [...msgs].reverse().find((m) => m.role === "bot" && m.r?.context)?.r.context;
      const r = await api.chat(t, lastCtx);
      setMsgs((m) => [...m, { role: "bot", r }]);
    } catch (e) {
      setMsgs((m) => [...m, { role: "bot", r: { answer: `⚠ ${e.message}`, incidents: [], sources: [], provenance: {} } }]);
    } finally { setBusy(false); }
  };

  return (
    <div className="panel chat">
      <div className="pane-head"><h2 style={{ flex: 1 }}>AI Investigation Assistant</h2>
        <span className="sub">Grounded in PRALAY's shared intelligence database — never invents incidents or sources.</span>
        <button className="btn sm" onClick={() => setMsgs([])}>Clear</button></div>
      <div className="msgs">
        {!msgs.length && <div className="empty">Ask about incidents, evidence, duplicates or grid cells.</div>}
        {msgs.map((m, i) => m.role === "user" ? <div key={i} className="msg user">{m.text}</div> : <BotMsg key={i} r={m.r} openIncident={openIncident} openReport={openReport} />)}
        {busy && <div className="msg bot sub">Checking shared intelligence… (external sources are queried only if needed)</div>}
        <div ref={end} />
      </div>
      <div style={{ padding: 12, borderTop: "1px solid var(--line)", display: "flex", flexDirection: "column", gap: 8 }}>
        <div className="chips">{SUGGEST.map((s) => <button key={s} className="chip" onClick={() => ask(s)}>{s}</button>)}</div>
        <form className="row" onSubmit={(e) => { e.preventDefault(); ask(); }}>
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. Is there flooding in Mohali?" style={{ flex: 1 }} />
          <button className="btn primary" disabled={busy}>Ask</button>
        </form>
      </div>
    </div>
  );
}

const CONF_CLS = { HIGH: "clear", MEDIUM: "neutral", LOW: "concern" };
const FRESH_TXT = { fresh: "fresh", stale: "stale — awaiting refresh", never_checked: "not yet checked" };

function Summary({ s, p }) {
  return (
    <div className="inv-summary">
      <div className="inv-kicker">Investigation Summary</div>
      <div className="inv-headline" style={{ whiteSpace: "pre-wrap" }}>{mdLinks(s.headline)}</div>
      <div className="inv-facts">
        {s.status && <span className="inv-fact"><span className="sub">Status</span><Badge v={s.status} /></span>}
        {s.confidence && (
          <span className="inv-fact" title={s.confidence.percentage_note}>
            <span className="sub">Confidence</span><Badge cls={CONF_CLS[s.confidence.level]}>{s.confidence.level}</Badge>
          </span>
        )}
        {s.live_counts && <span className="inv-fact"><span className="sub">Live news</span>
          {["actual", "warning", "forecast", "historical"].filter((k) => s.live_counts[k]).map((k) => <Badge key={k} cls={{ actual: "clear", warning: "concern", forecast: "neutral", historical: "info" }[k]}>{s.live_counts[k]} {k}</Badge>)}
          {!Object.values(s.live_counts).some(Boolean) && <span className="sub">none</span>}</span>}
        {s.reports !== null && s.reports !== undefined && <span className="inv-fact"><span className="sub">Reports</span><b>{s.reports}</b>
          {s.incidents > 1 && <span className="sub">across {s.incidents} incidents</span>}</span>}
        {s.freshness && <span className="inv-fact"><span className="sub">Evidence</span><Badge v={s.freshness}>{FRESH_TXT[s.freshness] || s.freshness}</Badge></span>}
        {p.query_cache === "hit" && <span className="badge b-clear">♻ REUSED FROM SHARED INTELLIGENCE · cached {ago(p.cached_at)}</span>}
        {(p.query_cache === "miss" || p.query_cache === "stale") && <span className="badge b-RECEIVED">{p.query_cache === "stale" ? "Re-checked (cache stale)" : "New investigation"}</span>}
      </div>
      {s.confidence && <div className="inv-line"><span className="sub">Why this confidence:</span> {s.confidence.reasons[0]}{s.confidence.reasons.length > 1 ? "; " + s.confidence.reasons.slice(1, 3).join("; ") : ""}.</div>}
      {s.why && <div className="inv-line"><span className="sub">Basis:</span> {s.why}</div>}
      {s.limitations?.length > 0 && (
        <div className="inv-line"><span className="sub">Limitations:</span> {s.limitations.slice(0, 2).join(" ")}
          {s.limitations.length > 2 && <span className="sub"> (+{s.limitations.length - 2} more in Extended Information)</span>}</div>
      )}
      {s.other_incidents?.length > 0 && (
        <div className="inv-line sub">Also found: {s.other_incidents.map((o) => `#${o.id} ${o.status.toLowerCase()}`).join(", ")}</div>
      )}
    </div>
  );
}

function BotMsg({ r, openIncident, openReport }) {
  const p = r.provenance || {};
  const s = r.summary;
  if (!s) return <div className="msg bot">{r.answer}</div>; // errors / older cached messages: unchanged plain rendering
  const hasExtended = r.live || r.incidents?.length || r.sources?.length || r.items?.length || (r.answer || "").includes("\n") || p.query_cache;
  return (
    <div className="msg bot inv">
      <Summary s={s} p={p} />
      {hasExtended ? (
        <details className="inv-extended">
          <summary><span className="inv-chevron" aria-hidden="true">›</span> Extended Information
            <span className="sub"> — {r.incidents?.length || 0} incident(s) · {r.sources?.length || 0} source(s){r.items?.length ? ` · ${r.items.length} item(s)` : ""}</span></summary>
          <div className="inv-ext-body">
            {p.query_cache && (
              <div className="row" style={{ marginBottom: 6 }}>
                {p.query_cache === "hit" ? <span className="badge b-clear">♻ REUSED FROM SHARED INTELLIGENCE · cached {ago(p.cached_at)} · {p.hit_count} reuse(s)</span>
                  : p.query_cache === "miss" || p.query_cache === "stale" ? <span className="badge b-RECEIVED">{p.query_cache === "stale" ? "Query cache stale → re-checked" : "New investigation"}</span>
                    : null}
                {(p.incidents || []).map((x) => <span key={x.incident_id} className="badge b-accent">#{x.incident_id}: {x.action}</span>)}
              </div>
            )}
            <div className="inv-answer">{r.answer}</div>
            {r.live && (
              <div className="finding" style={{ marginTop: 8 }}>
                <div className="ft">Live investigation · {r.live.connector} {r.live.ok ? <Badge cls="clear">queried</Badge> : <Badge cls="unavailable">unavailable</Badge>}
                  <span className="sub">query “{r.live.query}” · retrieved {fmtTime(r.live.retrieved_at)}{r.live.from_cache ? " (cached)" : ""}</span></div>
                {!r.live.ok && <div className="err">{r.live.error}</div>}
                <ul className="list inv-scroll">{r.live.items.map((it, i) => (
                  <li key={i}><Badge cls={{ actual: "clear", warning: "concern", forecast: "neutral", historical: "info" }[it.classification]}>{it.classification}</Badge>{" "}
                    {it.title}{it.publisher ? ` · ${it.publisher}` : ""} <a href={it.url} target="_blank" rel="noreferrer">↗</a>
                    <span className="sub"> (published {fmtTime(it.published)})</span></li>))}</ul>
                {r.live.ok && !r.live.items.length && <div className="sub">No matching live news.</div>}
                <div className="sub">Live evidence is external and unverified by PRALAY; syndicated copies are collapsed; warnings/forecasts are not treated as events.</div>
              </div>
            )}
            {r.incidents?.length > 0 && (
              <div className="inv-scroll">
                <table style={{ marginTop: 8 }}><tbody>{r.incidents.map((v) => (
                  <tr key={v.id} className="click" onClick={() => openIncident(v.id)}><td>#{v.id}</td><td>{v.title}</td><td><Badge v={v.status} /></td>
                    <td>{v.reports} report(s)</td><td><Badge v={v.freshness.state} /> <span className="sub">checked {ago(v.freshness.last_checked)}</span></td></tr>))}</tbody></table>
                {r.incidents.map((v) => v.confidence_assessment && (
                  <div key={"c" + v.id} className="finding" style={{ marginTop: 8 }}>
                    <div className="ft">#{v.id} confidence <Badge cls={CONF_CLS[v.confidence_assessment.level]}>{v.confidence_assessment.level}</Badge>
                      <span className="sub">evidence score {v.confidence ?? "—"}/100 (heuristic, not a probability)</span></div>
                    <ul className="list">{v.confidence_assessment.reasons.map((x, i) => <li key={i}>{x}</li>)}</ul>
                    {v.evidence_profile && <div className="sub">
                      Independent reports {v.evidence_profile.independent_reports} ({v.evidence_profile.reports_with_consistent_location} with consistent location,
                      {" "}{v.evidence_profile.reports_time_consistent} time-consistent) · duplicates {v.evidence_profile.duplicate_reports} ·
                      {" "}contradicted {v.evidence_profile.contradicted_reports} · independent external groups {v.evidence_profile.independent_external_groups.length}
                      {v.evidence_profile.independent_external_groups.length ? ` (${v.evidence_profile.independent_external_groups.join(", ")})` : ""} from {v.evidence_profile.supporting_items} supporting item(s) ·
                      {" "}official {v.evidence_profile.official_support ? "yes" : "no"} · weather {v.evidence_profile.weather_support ? "yes" : "no"}
                      {v.evidence_profile.rules_met.length > 0 && <> · rules met: {v.evidence_profile.rules_met.join("; ")}</>}
                    </div>}
                    {v.missing?.length > 0 && <div className="sub">Missing: {v.missing.join(" ")}</div>}
                    <div className="sub">{v.confidence_assessment.percentage_note}</div>
                  </div>))}
              </div>
            )}
            {r.items?.length > 0 && r.items[0].report_id !== undefined && (
              <div className="chips" style={{ marginTop: 6 }}>{r.items.map((x, i) => <button key={i} className="chip" onClick={() => openReport(x.report_id)}>report #{x.report_id}</button>)}</div>
            )}
            {r.items?.length > 0 && r.items[0].id !== undefined && r.items[0].report_id === undefined && (
              <div className="chips" style={{ marginTop: 6 }}>{r.items.map((x, i) => <button key={i} className="chip" onClick={() => openReport(x.id)}>report #{x.id}</button>)}</div>
            )}
            {r.sources?.length > 0 && (
              <div style={{ marginTop: 8 }}>
                <div className="sub">Sources ({r.sources.length})</div>
                <ul className="list inv-scroll">{r.sources.map((s2, i) => (
                  <li key={i}><Badge v={s2.stance} /> <b>{s2.source_name}</b>{s2.publisher ? ` · ${s2.publisher}` : ""} — {s2.summary.slice(0, 200)}
                    {s2.source_url && <> <a href={s2.source_url} target="_blank" rel="noreferrer">↗</a></>}
                    <span className="sub"> (published {fmtTime(s2.publication_time)} · retrieved {fmtTime(s2.retrieval_time)})</span></li>))}</ul>
              </div>
            )}
            {s.limitations?.length > 2 && <div className="sub" style={{ marginTop: 6 }}>All limitations: {s.limitations.join(" ")}</div>}
            <div className="sub" style={{ marginTop: 6 }}>Answered {fmtTime(r.generated_at)}</div>
            <details className="sub" style={{ marginTop: 4 }}><summary style={{ cursor: "pointer" }}>Diagnostics</summary>
              intent {r.parsed?.intent} · place {r.parsed?.place || "—"} · category {r.parsed?.category || "—"} · hazard {r.parsed?.hazard || "—"} · name {r.parsed?.name || "—"} · cache {p.query_cache || "—"}</details>
          </div>
        </details>
      ) : (
        <div className="sub" style={{ marginTop: 6 }}>Answered {fmtTime(r.generated_at)}</div>
      )}
    </div>
  );
}
