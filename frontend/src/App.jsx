import { useCallback, useEffect, useRef, useState } from "react";
import { api, fmtTime, SHOWCASE } from "./api";
import { Drawer } from "./components/common";
import { usePrefs } from "./prefs";
import Chat from "./views/Chat";
import Dashboard from "./views/Dashboard";
import GridView from "./views/GridView";
import IncidentDetail from "./views/IncidentDetail";
import Pipeline from "./views/Pipeline";
import ReportDetail from "./views/ReportDetail";
import Settings from "./views/Settings";
import Integrations from "./views/Integrations";
import Submit from "./views/Submit";

const NAV = [
  ["dashboard", "Dashboard", "◧"], ["submit", "Submit report", "＋"], ["grid", "Grid intelligence", "▦"],
  ["pipeline", "Processing & reports", "⟳"], ["chat", "Investigate (AI)", "✦"], ["sources", "Sources & AI", "⇄"], ["settings", "Settings", "⚙"],
];

export default function App() {
  const [prefs, setPrefs, resetPrefs] = usePrefs();
  const [view, setView] = useState(() => location.hash.slice(1) || "dashboard");
  const [meta, setMeta] = useState(null);
  const [summary, setSummary] = useState(null);
  const [version, setVersion] = useState(0);
  const [offline, setOffline] = useState(null);
  // Deep link: ?open=report:5 or ?open=incident:1 opens the investigation drawer.
  const [drawer, setDrawer] = useState(() => {
    const m = /^(report|incident):(\d+)$/.exec(new URLSearchParams(location.search).get("open") || "");
    return m ? { type: m[1], id: +m[2] } : null;
  });
  const lastV = useRef(-1);

  useEffect(() => { if (location.hash.slice(1) !== view) location.hash = view; }, [view]);
  // Follow hash changes from browser back/forward, edited URLs and in-page links.
  useEffect(() => {
    const f = () => { const h = location.hash.slice(1); if (h && NAV.some((n) => n[0] === h)) setView(h); };
    window.addEventListener("hashchange", f);
    return () => window.removeEventListener("hashchange", f);
  }, []);
  useEffect(() => { api.meta().then(setMeta).catch((e) => setOffline(e.message)); }, []);

  // Dashboard updates: poll summary every 3 s; when the backend change counter moves, views refetch.
  useEffect(() => {
    let alive = true;
    const tick = async () => {
      try {
        const s = await api.summary();
        if (!alive) return;
        setSummary(s);
        setOffline(null);
        if (s.version !== lastV.current) { lastV.current = s.version; setVersion((v) => v + 1); }
        if (!meta) api.meta().then(setMeta).catch(() => {});
      } catch (e) { alive && setOffline(e.message); }
    };
    tick();
    const t = setInterval(tick, 3000);
    return () => { alive = false; clearInterval(t); };
  }, [meta]);

  const openReport = useCallback((id) => setDrawer({ type: "report", id }), []);
  const openIncident = useCallback((id) => setDrawer({ type: "incident", id }), []);
  const R = summary?.reports;
  const busy = R ? R.awaiting + R.processing : 0;

  return (
    <>
      <div className="app-bg" />
      <div className="shell">
        <nav className="panel sidebar">
          <div className="brand"><div className="brand-mark">प्र</div><div><div className="brand-title">PRALAY AI</div><div className="brand-sub">Disaster intelligence & verification</div></div></div>
          {NAV.map(([k, label, icon]) => (
            <button key={k} className={`nav-btn ${view === k ? "active" : ""}`} onClick={() => setView(k)}>
              <span style={{ width: 18, textAlign: "center" }}>{icon}</span>{label}
              {k === "pipeline" && busy > 0 && <span className="badge b-PROCESSING">{busy}</span>}
            </button>))}
        </nav>
        <main className="main">
          {SHOWCASE && (
            <div className="banner grid" role="status">
              STATIC SHOWCASE — read-only snapshot of SYNTHETIC DEMO data. No backend, no live processing, no AI, no real reports.
              Uploads, chat, reviews and refreshes are disabled. Run PRALAY AI locally for the working system (see the repository README).
            </div>
          )}
          <div className="panel topbar">
            <h2 className="grow">{NAV.find((n) => n[0] === view)?.[1]}</h2>
            {offline ? <span className="badge b-CONTRADICTED">⚠ {offline}</span>
              : <span className="sub">{busy > 0 ? <span className="badge b-PROCESSING">⟳ processing {busy} report(s)</span> : <span className="badge b-clear">● live</span>} updated {fmtTime(summary?.generated_at)}</span>}
            {R?.demo > 0 && <span className="badge b-demo" title="Synthetic demonstration reports are present and labelled">{R.demo} demo report(s)</span>}
          </div>
          {view === "dashboard" && <Dashboard summary={summary} version={version} meta={meta} openReport={openReport} openIncident={openIncident} goto={setView} />}
          {view === "submit" && <Submit meta={meta} openReport={openReport} version={version} />}
          {view === "grid" && <GridView meta={meta} version={version} openReport={openReport} openIncident={openIncident} />}
          {view === "pipeline" && <Pipeline meta={meta} version={version} summary={summary} openReport={openReport} />}
          {view === "chat" && <Chat openIncident={openIncident} openReport={openReport} />}
          {view === "sources" && <Integrations version={version} />}
          {view === "settings" && <Settings prefs={prefs} setPrefs={setPrefs} reset={resetPrefs} meta={meta} />}
        </main>
      </div>
      {drawer && (
        <Drawer onClose={() => setDrawer(null)} title={drawer.type === "report" ? `Report #${drawer.id} — investigation` : `Incident #${drawer.id} — review`}>
          {drawer.type === "report"
            ? <ReportDetail id={drawer.id} version={version} stages={meta?.pipeline_stages || []} openIncident={openIncident} />
            : <IncidentDetail id={drawer.id} version={version} openReport={openReport} />}
        </Drawer>
      )}
    </>
  );
}
