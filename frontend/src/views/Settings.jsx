import { Fragment } from "react";
import { ACCENTS, FONTS, SHADES } from "../prefs";

const THEMES = [
  ["cinematic", "Cinematic Glass", "Dark cinematic mountains, warm amber accents, glass panels.", "linear-gradient(160deg,#171b26,#704a20)"],
  ["modern", "Modern Glass", "Clean contemporary SaaS glassmorphism with your accent.", "linear-gradient(160deg,#dfe7f3,#9fb4d8)"],
  ["plain", "Plain / Minimal", "Flat, high readability, minimal effects.", "#e5e7eb"],
];

export default function Settings({ prefs, setPrefs, reset, meta }) {
  const P = (k, v) => setPrefs({ [k]: v });
  const pickTheme = (t) => {
    const patch = { theme: t };
    if (t === "cinematic") Object.assign(patch, { accent: "amber", background: "mountains" });
    if (t === "modern" && prefs.background === "mountains") patch.background = "gradient";
    if (t === "plain") patch.background = "solid";
    setPrefs(patch);
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="panel section">
        <h2>Settings → Personalization</h2>
        <p className="sub">Preferences are saved in this browser and restored automatically.</p>
        <h4 style={{ marginTop: 14 }}>Theme</h4>
        <div className="grid3" style={{ marginTop: 6 }}>
          {THEMES.map(([k, name, desc, prev]) => (
            <div key={k} className={`theme-card ${prefs.theme === k ? "sel" : ""}`} onClick={() => pickTheme(k)}>
              <div className="theme-prev" style={{ background: prev }} /><b>{name}</b><div className="sub">{desc}</div>
            </div>))}
        </div>
        <div className="grid3" style={{ marginTop: 16 }}>
          <label>Appearance<select value={prefs.mode} onChange={(e) => P("mode", e.target.value)}><option value="light">Light</option><option value="dark">Dark</option><option value="auto">Auto (system)</option></select></label>
          <label>Background<select value={prefs.background} onChange={(e) => P("background", e.target.value)}><option value="mountains">Cinematic mountains</option><option value="gradient">Soft gradient</option><option value="solid">Solid</option></select></label>
          <label>Glass intensity ({prefs.glass}%)<input type="range" min="0" max="100" value={prefs.glass} disabled={prefs.theme === "plain"} onChange={(e) => P("glass", +e.target.value)} /></label>
        </div>
        <h4 style={{ marginTop: 16 }}>Accent colour family</h4>
        <div className="swatches" style={{ marginTop: 6 }}>
          {Object.entries(ACCENTS).map(([k, a]) => (
            <div key={k} title={k} className={`swatch ${prefs.accent === k ? "sel" : ""}`} style={{ background: `hsl(${a.h} ${a.s}% 55%)` }} onClick={() => P("accent", k)} />))}
        </div>
        <h4 style={{ marginTop: 12 }}>Accent shade</h4>
        <div className="swatches" style={{ marginTop: 6 }}>
          {Object.entries(SHADES).map(([k, l]) => {
            const a = ACCENTS[prefs.accent];
            return <div key={k} title={`shade ${k}`} className={`swatch ${+prefs.shade === +k ? "sel" : ""}`} style={{ background: `hsl(${a.h} ${a.s}% ${l}%)` }} onClick={() => P("shade", +k)} />;
          })}
        </div>
        <div className="grid3" style={{ marginTop: 16 }}>
          <label>Font<select value={prefs.font} onChange={(e) => P("font", e.target.value)}>
            <option value="calibri">Calibri (fallback: Carlito / Segoe UI)</option><option value="calibriLight">Calibri Light</option>
            <option value="sans">System sans-serif</option><option value="serif">Elegant serif (Cambria / Georgia)</option></select></label>
          <label>Heading weight<select value={prefs.headingWeight} onChange={(e) => P("headingWeight", +e.target.value)}>
            <option value={700}>Calibri Bold</option><option value={300}>Calibri Light</option><option value={600}>Semibold</option></select></label>
          <label>&nbsp;<button className="btn" onClick={reset}>Reset to defaults</button></label>
        </div>
        <p className="sub" style={{ marginTop: 10, fontFamily: FONTS[prefs.font] }}>Preview: The quick brown fox — <b>Calibri Bold</b> · <span className="light">Calibri Light</span></p>
      </div>
      <div className="panel section">
        <h3>System capabilities (live from backend)</h3>
        {meta && <div className="kv" style={{ marginTop: 8 }}>
          {Object.entries(meta.capabilities).map(([k, v]) => <Fragment key={k}><span>{k.replaceAll("_", " ")}</span><span>{typeof v === "boolean" ? (v ? "✔ available" : "✖ not available") : v}</span></Fragment>)}
          <span>Grid</span><span>{meta.grid.label} · {meta.grid.resolution_deg}°</span>
        </div>}
      </div>
    </div>
  );
}
