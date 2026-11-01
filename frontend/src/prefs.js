import { useEffect, useState } from "react";

export const ACCENTS = {
  amber: { h: 38, s: 92 }, blue: { h: 217, s: 85 }, cyan: { h: 189, s: 80 }, green: { h: 150, s: 60 },
  purple: { h: 268, s: 70 }, red: { h: 0, s: 75 }, neutral: { h: 220, s: 8 },
};
export const SHADES = { 1: 72, 2: 64, 3: 55, 4: 46, 5: 38 };
export const FONTS = {
  calibri: '"Calibri", "Carlito", "Segoe UI", system-ui, sans-serif',
  calibriLight: '"Calibri Light", "Calibri", "Carlito", "Segoe UI", system-ui, sans-serif',
  sans: 'system-ui, "Segoe UI", "Inter", Roboto, sans-serif',
  serif: '"Cambria", "Georgia", "Times New Roman", serif',
};
const DEFAULTS = { theme: "cinematic", mode: "dark", accent: "amber", shade: 3, glass: 60, background: "mountains", font: "calibri", headingWeight: 700 };
const KEY = "pralay.prefs.v1";

export function usePrefs() {
  const [prefs, setPrefs] = useState(() => {
    let saved = {};
    try {
      saved = JSON.parse(localStorage.getItem(KEY) || "{}");
    } catch {
      saved = {};
    }
    // Optional URL overrides, e.g. ?theme=modern&mode=light&accent=blue (handy for demos); persisted like any change.
    const url = Object.fromEntries(new URLSearchParams(location.search));
    const pick = Object.fromEntries(Object.entries(url).filter(([k]) => k in DEFAULTS).map(([k, v]) => [k, typeof DEFAULTS[k] === "number" ? +v : v]));
    return { ...DEFAULTS, ...saved, ...pick };
  });
  const [sysDark, setSysDark] = useState(() => window.matchMedia("(prefers-color-scheme: dark)").matches);
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const f = (e) => setSysDark(e.matches);
    mq.addEventListener("change", f);
    return () => mq.removeEventListener("change", f);
  }, []);
  useEffect(() => {
    localStorage.setItem(KEY, JSON.stringify(prefs));
    const root = document.documentElement;
    const mode = prefs.mode === "auto" ? (sysDark ? "dark" : "light") : prefs.mode;
    root.dataset.theme = prefs.theme;
    root.dataset.mode = mode;
    root.dataset.bg = prefs.background;
    const a = ACCENTS[prefs.accent] || ACCENTS.amber;
    const l = SHADES[prefs.shade] || 55;
    root.style.setProperty("--accent", `hsl(${a.h} ${a.s}% ${l}%)`);
    root.style.setProperty("--accent-soft", `hsl(${a.h} ${a.s}% ${l}% / 0.16)`);
    root.style.setProperty("--accent-strong", `hsl(${a.h} ${a.s}% ${Math.max(l - 12, 25)}%)`);
    root.style.setProperty("--accent-contrast", l > 58 ? "#111" : "#fff");
    const g = prefs.theme === "plain" ? 0 : prefs.glass;
    root.style.setProperty("--blur", `${Math.round(g / 5)}px`);
    root.style.setProperty("--glass-alpha", mode === "dark" ? (0.88 - g / 220).toFixed(2) : (0.92 - g / 300).toFixed(2));
    root.style.setProperty("--font", FONTS[prefs.font] || FONTS.calibri);
    root.style.setProperty("--heading-weight", prefs.headingWeight);
  }, [prefs, sysDark]);
  return [prefs, (patch) => setPrefs((p) => ({ ...p, ...patch })), () => setPrefs(DEFAULTS)];
}
