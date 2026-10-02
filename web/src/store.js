const KEY = "dgca.dashboards.v1";
const THEME = "dgca.theme";

export function loadSaved() {
  try { return JSON.parse(localStorage.getItem(KEY) || "[]"); } catch { return []; }
}
export function saveAll(list) {
  try { localStorage.setItem(KEY, JSON.stringify(list)); } catch { /* storage blocked: keep working in memory */ }
}
export const getTheme = () => { try { return localStorage.getItem(THEME); } catch { return null; } };
export const setTheme = (t) => { try { localStorage.setItem(THEME, t); } catch { /* ignore */ } };

const b64 = (s) => btoa(unescape(encodeURIComponent(s))).replaceAll("+", "-").replaceAll("/", "_").replace(/=+$/, "");
const unb64 = (s) => decodeURIComponent(escape(atob(s.replaceAll("-", "+").replaceAll("_", "/"))));

export function encodeShare(dash, filters) {
  return b64(JSON.stringify({ n: dash.name, w: dash.widgets, f: filters }));
}
export function decodeShare(hash) {
  const m = /[#&]d=([^&]+)/.exec(hash);
  if (!m) return null;
  try {
    const o = JSON.parse(unb64(m[1]));
    return { dash: { id: "shared-" + Date.now(), name: o.n || "Shared dashboard", widgets: o.w || [] }, filters: o.f || {} };
  } catch { return null; }
}
