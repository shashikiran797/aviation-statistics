import { compileSpec, SpecError } from "./spec.js";
import { openDb } from "./db.js";
import { renderWidget, autoTitle, label, toCsv, chooseViz, hideTip } from "./charts.js";
import { STARTERS } from "./starter.js";
import { loadSaved, saveAll, getTheme, setTheme, encodeShare, decodeShare } from "./store.js";
import * as AI from "./ai.js";
import { initCatalog } from "./catalog.js";

const $ = (s, r = document) => r.querySelector(s);
const el = (tag, props = {}, ...kids) => {
  const e = Object.assign(document.createElement(tag), props);
  e.append(...kids);
  return e;
};
const FILTER_DIMS = ["fy", "segment", "airline_type", "airline"];

const S = { catalog: null, db: null, base: "", dashboards: [], current: null, filters: {}, cache: new Map(), tableMode: new Set(), draft: null, draftIdx: null, meta: null };

// ---------- boot
async function boot() {
  const th = getTheme();
  if (th) document.documentElement.dataset.theme = th;
  const latest = await (await fetch("data/latest.json")).json();
  S.base = `data/${latest.version}`;
  S.catalog = await (await fetch(`${S.base}/catalog.json`)).json();
  $("#data-meta").textContent = "Opening local query engine…";
  S.db = await openDb(S.base, Object.keys(S.catalog.tables));
  const n = (await S.db.query("SELECT count(*) AS n, min(period) AS a, max(period) AS b FROM fact_airline_monthly JOIN dim_period USING(period_key)")).rows[0];
  const fys = S.catalog.dimensions.fy.values;
  S.meta = { rows: n.n, from: fys[0], to: fys.at(-1), version: latest.version, built: latest.built_at };
  $("#data-meta").textContent = `DGCA air traffic statistics · FY${fys[0]} to FY${fys.at(-1)} · ${n.n.toLocaleString()} rows · queried locally in your browser`;
  S.dashboards = [...STARTERS, ...loadSaved()];
  const shared = decodeShare(location.hash);
  if (shared) { S.dashboards.push(shared.dash); S.filters = shared.filters; S.current = shared.dash.id; }
  else S.current = S.dashboards[0].id;
  wireChrome();
  wireViews();
  renderFilters();
  renderDashSelect();
  await renderDashboard();
  await initCatalog({ db: S.db, base: S.base, catalog: S.catalog, openTable });
  window.__dgca = { query: (sql) => S.db.query(sql), state: S };  // test/debug hook
  document.body.dataset.ready = "1";
}

const dash = () => S.dashboards.find((d) => d.id === S.current);

// ---------- filters
function globalFilterList(fact) {
  const f = S.catalog.facts[fact];
  return Object.entries(S.filters).filter(([d, v]) => v.length && f.dimensions.includes(d)).map(([dim, values]) => ({ dim, op: "in", values }));
}
const specWithGlobals = (spec) => ({ ...spec, filters: [...(spec.filters || []), ...globalFilterList(spec.fact)] });

function valuePicker(anchor, values, selected, onChange, title) {
  document.querySelectorAll(".pop").forEach((p) => p.remove());
  const pop = el("div", { className: "pop", role: "dialog", ariaLabel: title });
  const sel = new Set(selected);
  const boxes = values.map((v) => {
    const cb = el("input", { type: "checkbox", checked: sel.has(v) });
    cb.addEventListener("change", () => { cb.checked ? sel.add(v) : sel.delete(v); onChange([...values].filter((x) => sel.has(x))); });
    pop.append(el("label", {}, cb, document.createTextNode(String(v))));
    return cb;
  });
  const clear = el("button", { className: "ghost", textContent: "Clear" });
  clear.onclick = () => { sel.clear(); boxes.forEach((b) => (b.checked = false)); onChange([]); };
  const done = el("button", { textContent: "Done" });
  done.onclick = () => pop.remove();
  pop.append(el("div", { className: "pop-foot" }, clear, done));
  const r = anchor.getBoundingClientRect();
  pop.style.left = Math.max(8, Math.min(r.left, innerWidth - 260)) + "px";
  pop.style.top = Math.min(r.bottom + 4, innerHeight - 200) + "px";
  (document.querySelector("dialog[open]") || document.body).append(pop);  // dialogs live in the top layer
  const close = (e) => { if (!pop.contains(e.target) && e.target !== anchor) { pop.remove(); document.removeEventListener("pointerdown", close); } };
  setTimeout(() => document.addEventListener("pointerdown", close), 0);
}

function renderFilters() {
  const box = $("#filters");
  box.replaceChildren(el("span", { className: "f-lbl", textContent: "Filters" }));
  for (const d of FILTER_DIMS) {
    const dm = S.catalog.dimensions[d];
    const sel = S.filters[d] || [];
    const b = el("button", { className: "fbtn", id: `f-${d}` }, document.createTextNode(dm.label + ": "), el("b", { textContent: sel.length ? (sel.length > 2 ? `${sel.length} selected` : sel.join(", ")) : d === "airline_type" ? "Airlines (default)" : "All" }));
    b.onclick = () => valuePicker(b, dm.values, sel, (v) => { S.filters[d] = v; renderFilters(); renderDashboard(); }, dm.label);
    box.append(b);
  }
  if (Object.values(S.filters).some((v) => v.length)) {
    const c = el("button", { className: "ghost", textContent: "Clear all" });
    c.onclick = () => { S.filters = {}; renderFilters(); renderDashboard(); };
    box.append(c);
  }
}

function crossFilter(dim, value) {
  if (!FILTER_DIMS.includes(dim)) return;
  const cur = new Set(S.filters[dim] || []);
  cur.has(value) ? cur.delete(value) : cur.add(value);
  S.filters[dim] = [...cur];
  renderFilters();
  renderDashboard();
}

// ---------- dashboards
function renderDashSelect() {
  const sel = $("#dash-select");
  sel.replaceChildren(...S.dashboards.map((d) => el("option", { value: d.id, textContent: (d.starter ? "★ " : "") + d.name, selected: d.id === S.current })));
  sel.onchange = () => { S.current = sel.value; renderDashboard(); };
}

function editable() {
  // starters are templates: fork on first edit
  const d = dash();
  if (!d.starter) return d;
  const copy = { id: "u" + Date.now(), name: d.name + " (my copy)", widgets: structuredClone(d.widgets) };
  S.dashboards.push(copy);
  S.current = copy.id;
  persist();
  renderDashSelect();
  return copy;
}
const persist = () => saveAll(S.dashboards.filter((d) => !d.starter && !String(d.id).startsWith("shared-")));

async function runSpec(spec) {
  const full = specWithGlobals(spec);
  const sql = compileSpec(S.catalog, full);
  if (!S.cache.has(sql)) {
    const t0 = performance.now();
    const res = await S.db.query(sql);
    res.ms = performance.now() - t0;
    S.cache.set(sql, res);
  }
  return { sql, ...S.cache.get(sql) };
}

async function renderDashboard() {
  hideTip();
  const d = dash();
  const grid = $("#grid");
  const gen = (S.gen = (S.gen || 0) + 1);
  const t0 = performance.now();
  grid.replaceChildren();
  const cards = d.widgets.map((w, i) => {
    const body = el("div", { className: "card-body loading" });
    const tools = el("div", { className: "card-tools" });
    const mk = (txt, title, fn) => { const b = el("button", { textContent: txt, title, ariaLabel: title }); b.onclick = fn; tools.append(b); return b; };
    const card = el("article", { className: `card w-${w.w || 6}`, id: `w-${w.id}` }, el("div", { className: "card-head" }, el("div", {}, el("h3", { textContent: w.title || autoTitle(w.spec, S.catalog) }), el("p", { id: `sub-${w.id}` })), tools), body);
    mk("▦", "Toggle table view", () => { S.tableMode.has(w.id) ? S.tableMode.delete(w.id) : S.tableMode.add(w.id); paint(); });
    mk("⤓", "Download CSV", async () => { const r = await runSpec(w.spec); const a = el("a", { href: URL.createObjectURL(new Blob([toCsv(r.cols, r.rows)], { type: "text/csv" })), download: `${w.id}.csv` }); a.click(); });
    mk("</>", "View SQL", async () => { const r = await runSpec(w.spec); openModal("SQL for “" + (w.title || autoTitle(w.spec, S.catalog)) + "”", el("div", {}, el("p", { className: "hint", textContent: "Generated from the chart spec – read-only. Global filters are included." }), el("pre", { textContent: r.sql }), el("details", {}, el("summary", { textContent: "Chart spec (JSON)" }), el("pre", { textContent: JSON.stringify(w.spec, null, 2) })))); });
    mk("✎", "Edit widget", () => openBuilder(i));
    mk("✕", "Remove widget", () => { const e = editable(); e.widgets = e.widgets.filter((x) => x.id !== w.id); persist(); renderDashboard(); });
    grid.append(card);
    const paint = async () => {
      try {
        const r = await runSpec(w.spec);
        if (gen !== S.gen) return;
        body.classList.remove("loading");
        const used = renderWidget(body, { spec: w.spec, cols: r.cols, rows: r.rows, catalog: S.catalog, onPick: crossFilter, forceTable: S.tableMode.has(w.id) });
        $(`#sub-${w.id}`).textContent = `${r.rows.length} row${r.rows.length === 1 ? "" : "s"} · ${r.ms ? r.ms.toFixed(0) + " ms" : "cached"}${filterNote(w.spec)}`;
      } catch (e) {
        body.classList.remove("loading");
        body.replaceChildren(el("div", { className: "error", textContent: e.message }));
      }
    };
    return paint();
  });
  await Promise.all(cards);
  if (gen === S.gen) $("#status").textContent = `${d.widgets.length} widgets rendered in ${(performance.now() - t0).toFixed(0)} ms by DuckDB-WASM in your browser.`;
}

function filterNote(spec) {
  const f = S.catalog.facts[spec.fact];
  const hasType = (spec.filters || []).concat(globalFilterList(spec.fact)).some((x) => x.dim === "airline_type");
  return hasType ? "" : " · individual airlines only";
}

// ---------- widget builder
function openBuilder(idx = null, draft = null) {
  const d = dash();
  S.draftIdx = idx;
  const w = draft || (idx !== null ? structuredClone(d.widgets[idx]) : { id: "w" + Date.now(), title: "", spec: { fact: Object.keys(S.catalog.facts)[0], dimensions: [], measures: ["pax"], filters: [] }, w: 6 });
  S.draft = w;
  $("#builder-title").textContent = idx !== null ? "Edit widget" : "New widget";
  const fact = $("#b-fact");
  fact.replaceChildren(...Object.entries(S.catalog.facts).map(([id, f]) => el("option", { value: id, textContent: f.label })));
  fact.value = w.spec.fact;
  fact.onchange = () => { S.draft.spec = { fact: fact.value, dimensions: [], measures: [], filters: [] }; syncBuilder(); };
  syncBuilder();
  const dlg = $("#builder");
  if (!dlg.open) dlg.showModal();
}

function fieldChip(kind, id, onclick) {
  const m = kind === "meas" ? S.catalog.measures[id] : S.catalog.dimensions[id];
  const c = el("span", { className: `chip ${kind}${kind === "meas" && !m.additive ? " nonadd" : ""}`, draggable: true, textContent: m.label, title: m.description || m.label, tabIndex: 0 });
  c.addEventListener("dragstart", (e) => e.dataTransfer.setData("text/plain", JSON.stringify({ kind, id })));
  c.onclick = onclick;
  c.onkeydown = (e) => e.key === "Enter" && onclick();
  return c;
}
function shelfChip(kind, id, remove) {
  const c = fieldChip(kind, id, () => {});
  const x = el("button", { className: "x", textContent: "✕", ariaLabel: "Remove " + id });
  x.onclick = (e) => { e.stopPropagation(); remove(); };
  c.append(x);
  return c;
}

let previewTimer;
function syncBuilder() {
  const w = S.draft, spec = w.spec, f = S.catalog.facts[spec.fact];
  $("#b-fact-desc").textContent = f.description.replace(/\s+/g, " ").trim();
  const add = (key, id) => { if (!spec[key].includes(id)) spec[key] = key === "dimensions" ? [...spec[key], id].slice(-2) : [...spec[key], id]; syncBuilder(); };
  $("#b-dims").replaceChildren(...f.dimensions.map((id) => fieldChip("dim", id, () => add("dimensions", id))));
  $("#b-meas").replaceChildren(...f.measures.map((id) => fieldChip("meas", id, () => add("measures", id))));
  $("#s-dimensions").replaceChildren(...spec.dimensions.map((id) => shelfChip("dim", id, () => { spec.dimensions = spec.dimensions.filter((x) => x !== id); syncBuilder(); })));
  $("#s-measures").replaceChildren(...spec.measures.map((id) => shelfChip("meas", id, () => { spec.measures = spec.measures.filter((x) => x !== id); syncBuilder(); })));
  for (const [key, shelf] of [["dimensions", "dim"], ["measures", "meas"]]) {
    const slot = $(`#s-${key}`);
    slot.ondragover = (e) => { e.preventDefault(); slot.classList.add("over"); };
    slot.ondragleave = () => slot.classList.remove("over");
    slot.ondrop = (e) => {
      e.preventDefault(); slot.classList.remove("over");
      try { const o = JSON.parse(e.dataTransfer.getData("text/plain")); if (o.kind === shelf) add(key, o.id); } catch { /* ignore */ }
    };
  }
  // filters
  const fbox = $("#s-filters");
  fbox.replaceChildren(...(spec.filters || []).map((flt, i) => {
    const dimSel = el("select", {}, ...f.dimensions.filter((d) => S.catalog.dimensions[d].kind === "category").map((d) => el("option", { value: d, textContent: S.catalog.dimensions[d].label, selected: d === flt.dim })));
    const opSel = el("select", {}, el("option", { value: "in", textContent: "is any of", selected: flt.op === "in" }), el("option", { value: "not_in", textContent: "is not", selected: flt.op === "not_in" }));
    const valBtn = el("button", { textContent: flt.values?.length ? `${flt.values.length} selected: ${flt.values.slice(0, 2).join(", ")}${flt.values.length > 2 ? "…" : ""}` : "choose values…" });
    dimSel.onchange = () => { flt.dim = dimSel.value; flt.values = []; syncBuilder(); };
    opSel.onchange = () => { flt.op = opSel.value; schedulePreview(); };
    valBtn.onclick = () => valuePicker(valBtn, S.catalog.dimensions[flt.dim].values, flt.values || [], (v) => { flt.values = v; valBtn.textContent = v.length ? `${v.length} selected` : "choose values…"; schedulePreview(); }, "values");
    const rm = el("button", { className: "ghost", textContent: "✕", ariaLabel: "Remove filter" });
    rm.onclick = () => { spec.filters.splice(i, 1); syncBuilder(); };
    return el("div", { className: "frow" }, dimSel, opSel, valBtn, rm);
  }));
  $("#b-addfilter").onclick = () => { const d = f.dimensions.find((x) => S.catalog.dimensions[x].kind === "category" && x !== "airline_type") || f.dimensions[0]; (spec.filters ||= []).push({ dim: d, op: "in", values: [] }); syncBuilder(); };
  // options
  $("#b-viz").value = spec.viz || "auto";
  $("#b-viz").onchange = (e) => { spec.viz = e.target.value; schedulePreview(); };
  const topDim = $("#b-topdim"), topMeas = $("#b-topmeas"), topN = $("#b-topn");
  topDim.replaceChildren(...spec.dimensions.map((d) => el("option", { value: d, textContent: S.catalog.dimensions[d].label })));
  topMeas.replaceChildren(...f.measures.map((m) => el("option", { value: m, textContent: S.catalog.measures[m].label })));
  topN.value = spec.top?.n ?? "";
  if (spec.top) { topDim.value = spec.top.dim; topMeas.value = spec.top.measure; }
  else topMeas.value = spec.measures[0] || f.measures[0];
  const setTop = () => { const n = parseInt(topN.value, 10); if (n > 0 && topDim.value) spec.top = { dim: topDim.value, measure: topMeas.value, n }; else delete spec.top; schedulePreview(); };
  topN.oninput = topDim.onchange = topMeas.onchange = setTop;
  const sb = $("#b-sortby");
  sb.replaceChildren(el("option", { value: "", textContent: "(default order)" }), ...[...spec.measures, ...spec.dimensions].map((id) => el("option", { value: id, textContent: label(S.catalog, id) })));
  sb.value = spec.sort?.by ?? "";
  $("#b-sortdir").value = spec.sort?.dir ?? "desc";
  const setSort = () => { if (sb.value) spec.sort = { by: sb.value, dir: $("#b-sortdir").value }; else delete spec.sort; schedulePreview(); };
  sb.onchange = $("#b-sortdir").onchange = setSort;
  const lim = $("#b-limit");
  lim.value = spec.limit ?? "";
  lim.oninput = () => { const n = parseInt(lim.value, 10); if (n > 0) spec.limit = n; else delete spec.limit; schedulePreview(); };
  const title = $("#b-title");
  title.value = w.title || "";
  title.placeholder = autoTitle(spec, S.catalog);
  title.oninput = () => { w.title = title.value; };
  schedulePreview();
}

function schedulePreview() { clearTimeout(previewTimer); previewTimer = setTimeout(previewNow, 120); }
async function previewNow() {
  const spec = S.draft.spec, err = $("#b-error");
  err.hidden = true;
  $("#b-title").placeholder = autoTitle(spec, S.catalog);
  try {
    const clean = { ...spec, filters: (spec.filters || []).filter((x) => x.values?.length) };
    const r = await runSpec(clean);
    $("#b-sql").textContent = r.sql;
    renderWidget($("#b-preview"), { spec: clean, cols: r.cols, rows: r.rows, catalog: S.catalog, onPick: null });
  } catch (e) {
    err.hidden = false;
    err.textContent = e instanceof SpecError ? e.message : "Query failed: " + e.message;
    $("#b-preview").replaceChildren();
    $("#b-sql").textContent = "";
  }
}

function saveDraft() {
  const w = S.draft;
  w.spec.filters = (w.spec.filters || []).filter((x) => x.values?.length);
  try { compileSpec(S.catalog, w.spec); } catch (e) { $("#b-error").hidden = false; $("#b-error").textContent = e.message; return; }
  const d = editable();
  if (S.draftIdx !== null && dash() === d && d.widgets[S.draftIdx]?.id === w.id) d.widgets[S.draftIdx] = w;
  else if (S.draftIdx !== null) { const i = d.widgets.findIndex((x) => x.id === w.id); if (i >= 0) d.widgets[i] = w; else d.widgets.push(w); }
  else d.widgets.push(w);
  persist();
  $("#builder").close();
  renderDashboard();
}

// ---------- modal helpers
function openModal(title, body) {
  $("#modal-title").textContent = title;
  $("#modal-body").replaceChildren(body);
  const m = $("#modal");
  if (!m.open) m.showModal();
}

// ---------- SQL console (advanced)
const FORBIDDEN = /\b(attach|copy|install|load|pragma|create|drop|insert|update|delete|alter|export|import|call|set|read_\w+|parquet_scan|glob|http\w*)\b/i;
function openSql() {
  const ta = el("textarea", { rows: 7, spellcheck: false, value: "SELECT dim_period.fy AS fy, SUM(f.pax) AS pax\nFROM fact_airline_monthly f\nJOIN dim_period ON f.period_key = dim_period.period_key\nGROUP BY 1 ORDER BY 1" });
  ta.style.cssText = "width:100%;font-family:ui-monospace,monospace;font-size:12px";
  const out = el("div");
  const run = el("button", { className: "primary", textContent: "Run (read-only)" });
  run.onclick = async () => {
    const q = ta.value.trim().replace(/;+\s*$/, "");
    out.replaceChildren();
    if (!/^(select|with)\b/i.test(q) || q.includes(";") || FORBIDDEN.test(q)) { out.append(el("div", { className: "error", textContent: "Only a single SELECT over the published tables is allowed here." })); return; }
    try { const r = await S.db.query(q); out.append(tableFrom(r.cols, r.rows.slice(0, 200))); } catch (e) { out.append(el("div", { className: "error", textContent: e.message })); }
  };
  openModal("Advanced: SQL console", el("div", {}, el("p", { className: "hint", textContent: "Tables: " + Object.entries(S.catalog.tables).map(([t, c]) => `${t}(${c.join(", ")})`).join(" · ") }), ta, el("div", { className: "frow" }, run), out));
}
function tableFrom(cols, rows) {
  const t = el("table", { className: "t" }, el("thead", {}, el("tr", {}, ...cols.map((c) => el("th", { textContent: c })))));
  t.append(el("tbody", {}, ...rows.map((r) => el("tr", {}, ...cols.map((c) => el("td", { textContent: r[c] instanceof Date ? r[c].toISOString().slice(0, 10) : String(r[c] ?? "–") }))))));
  return el("div", { className: "tbl-wrap" }, t);
}

// ---------- views + extracted-table viewer
function wireViews() {
  const show = (v) => {
    const cat = v === "cat";
    $("#view-catalog").hidden = !cat;
    $("#view-dash").hidden = cat;
    $("#filters").hidden = cat;
    for (const id of ["dash-select", "btn-new", "btn-ai", "btn-share"]) $("#" + id).hidden = cat;
    $("#v-cat").classList.toggle("on", cat);
    $("#v-dash").classList.toggle("on", !cat);
    if (!cat) renderDashboard();
  };
  $("#v-dash").onclick = () => show("dash");
  $("#v-cat").onclick = () => show("cat");
}

async function openTable(meta) {
  const file = `tables/${meta.table_sha}.parquet`;
  await S.db.register(file);
  const cols = (await S.db.query(`DESCRIBE SELECT * FROM parquet_scan('${file}')`)).rows.map((r) => r.column_name);
  const search = el("input", { type: "search", placeholder: "Filter rows…", ariaLabel: "Filter rows" });
  const info = el("p", { className: "hint" });
  const out = el("div");
  const csv = el("button", { textContent: "Download CSV" });
  let last = [];
  const run = async () => {
    const words = search.value.toLowerCase().split(/\s+/).filter(Boolean).slice(0, 5);
    const hay = `lower(concat_ws(' ', ${cols.map((c) => `"${c.replaceAll('"', '""')}"`).join(", ")}))`;
    const where = words.length ? "WHERE " + words.map((w) => `${hay} LIKE '%${w.replaceAll("'", "''")}%'`).join(" AND ") : "";
    const r = await S.db.query(`SELECT * FROM parquet_scan('${file}') ${where} LIMIT 300`);
    last = r.rows;
    info.textContent = `${meta.n_rows} rows × ${meta.n_cols} columns · showing ${r.rows.length}${r.rows.length === 300 ? "+" : ""}`;
    out.replaceChildren(tableFrom(cols, r.rows));
  };
  let tm; search.oninput = () => { clearTimeout(tm); tm = setTimeout(run, 150); };
  csv.onclick = () => el("a", { href: URL.createObjectURL(new Blob([toCsv(cols, last)], { type: "text/csv" })), download: "table.csv" }).click();
  openModal(meta.title || "Extracted table", el("div", {}, el("p", { className: "hint", textContent: `Scraped from: ${meta.section_path} (portal route ${meta.route}). Values are kept exactly as published (text).` }), el("div", { className: "frow" }, search, csv), info, out));
  await run();
}

// ---------- sources
async function openSources() {
  const src = await (await fetch(`${S.base}/sources.json`)).json();
  const byFy = {};
  for (const s of src) byFy[s.fy] = (byFy[s.fy] || 0) + 1;
  const t = tableFrom(["fiscal_year", "source_files_parsed"], Object.entries(byFy).sort().map(([fy, n]) => ({ fiscal_year: fy, source_files_parsed: n })));
  const sample = src.slice(0, 8).map((s) => el("li", {}, el("a", { href: s.s3_url, target: "_blank", rel: "noopener", textContent: `${s.fy} · ${s.entity} · table ${s.table_id}` })));
  openModal("Data sources & coverage", el("div", {},
    el("p", { textContent: `Source: DGCA India – Air Transport Statistics (monthly traffic and operating statistics, scheduled services, ICAO ATR Form A). ${src.length} Excel files parsed; every fact row keeps a source_sha pointing at its file. Built ${S.meta.built}.` }),
    t, el("h4", { textContent: "Sample source files" }), el("ul", {}, ...sample),
    el("p", { className: "hint", textContent: "Older years (before FY2014-15) and non-scheduled/pdf-only tables are not yet ingested – see the repository plan." })));
}

// ---------- AI panel
async function openAI() {
  const chrome = await AI.chromeStatus();
  const gpu = AI.webgpuStatus();
  const en = AI.enabled();
  const body = el("div");
  const out = el("div", { id: "ai-out" });
  const input = el("input", { type: "text", id: "ai-q", placeholder: "e.g. Top 5 airlines by passengers in 2018-19, by month", ariaLabel: "Your question" });
  const ask = el("button", { className: "primary", textContent: "Ask", id: "ai-ask" });
  const backendSel = el("select", { id: "ai-backend", ariaLabel: "AI backend" }, el("option", { value: "chrome", textContent: "Chrome built-in AI" }), el("option", { value: "webllm", textContent: "WebLLM (WebGPU)" }));
  backendSel.value = en.webllm && !en.chrome ? "webllm" : "chrome";
  const badge = (txt, ok) => el("span", { className: `badge ${ok ? "ok" : "no"}`, textContent: txt });

  const chromeMsg = { available: "Ready – the model is on this device.", downloadable: "Supported – the model is not downloaded yet.", downloading: "Model is downloading…", unavailable: "Not available on this device/profile.", unsupported: "Not supported by this browser (or flag is off)." }[chrome.state] || chrome.state;
  const chromeBtn = el("button", { textContent: chrome.state === "available" ? "Enable" : "Download & enable", id: "ai-chrome-enable" });
  const cprog = el("div", { className: "progress", hidden: true }, el("i"));
  chromeBtn.onclick = async () => {
    cprog.hidden = false;
    try { await AI.enableChrome((p) => (cprog.firstChild.style.width = Math.round(p * 100) + "%")); chromeBtn.textContent = "Enabled ✓"; backendSel.value = "chrome"; }
    catch (e) { out.replaceChildren(el("div", { className: "error", textContent: "Could not start Chrome AI: " + e.message })); }
  };
  chromeBtn.disabled = ["unsupported", "unavailable"].includes(chrome.state) || en.chrome;
  if (en.chrome) chromeBtn.textContent = "Enabled ✓";

  const t1 = el("div", { className: "ai-tier", id: "tier-chrome" }, el("h4", {}, document.createTextNode("1 · Chrome built-in AI (Gemini Nano)"), badge(chrome.state, chrome.state === "available")),
    el("p", { textContent: chromeMsg }), chromeBtn, cprog,
    el("details", { open: ["unsupported", "unavailable"].includes(chrome.state) }, el("summary", { textContent: "How to turn it on" }),
      el("ol", { className: "steps" },
        ...["Use desktop Chrome (recent stable). Check chrome://version – the Prompt API needs a current release.",
          "Open chrome://flags/#prompt-api-for-gemini-nano and set it to Enabled. If it is listed, also enable chrome://flags/#optimization-guide-on-device-model (Enabled BypassPerfRequirement).",
          "Relaunch Chrome, reopen this page and click “Download & enable” – the one-time model download is a few GB, so keep ~22 GB of disk free.",
          "Watch status at chrome://on-device-internals. When this panel says “available”, ask a question.",
          "Flag names change between Chrome versions – if you cannot find them see developer.chrome.com/docs/ai/prompt-api."].map((t) => el("li", { textContent: t })))));

  const modelSel = el("select", { id: "ai-model" }, ...AI.WEBLLM_MODELS.map((m) => el("option", { value: m.id, textContent: m.label })));
  const wbtn = el("button", { textContent: en.webllm ? "Enabled ✓" : "Download & enable", id: "ai-webllm-enable", disabled: gpu.state === "unsupported" || en.webllm });
  const wprog = el("div", { className: "progress", hidden: true }, el("i"));
  const wtxt = el("p", { className: "hint" });
  wbtn.onclick = async () => {
    wprog.hidden = false;
    try { await AI.enableWebLLM(modelSel.value, (p, t) => { wprog.firstChild.style.width = Math.round(p * 100) + "%"; wtxt.textContent = t || ""; }); wbtn.textContent = "Enabled ✓"; backendSel.value = "webllm"; }
    catch (e) { out.replaceChildren(el("div", { className: "error", textContent: "Could not start WebLLM: " + e.message })); }
  };
  const t2 = el("div", { className: "ai-tier", id: "tier-webllm" }, el("h4", {}, document.createTextNode("2 · WebLLM (runs on your GPU via WebGPU)"), badge(gpu.state === "unsupported" ? "no WebGPU" : "WebGPU present", gpu.state !== "unsupported")),
    el("p", { textContent: gpu.state === "unsupported" ? "This browser has no WebGPU. Use a recent desktop Chrome/Edge, or enable chrome://flags/#enable-unsafe-webgpu." : "Opt-in. The model downloads once (size shown below) and is cached by the browser; nothing is sent to any server." }),
    el("div", { className: "frow" }, modelSel, wbtn), wprog, wtxt);

  const t3 = el("p", { className: "hint", textContent: "No local model? The builder and starter dashboards work fully without AI – use “+ Add widget”." });

  const go = async () => {
    const q = input.value.trim();
    if (!q) return;
    out.replaceChildren(el("p", { className: "hint", textContent: "Thinking locally…" }));
    try {
      if (!window.__DGCA_AI_MOCK && !AI.enabled()[backendSel.value]) throw new Error("Enable that backend first (see steps above).");
      const { spec, attempts } = await AI.generateSpec(backendSel.value, q, S.catalog);
      out.replaceChildren(el("p", { textContent: `Chart spec ready (${attempts} attempt${attempts > 1 ? "s" : ""}). Opening it in the builder so you can adjust it.` }), el("pre", { textContent: JSON.stringify(spec, null, 2) }));
      $("#modal").close();
      openBuilder(null, { id: "w" + Date.now(), title: q, spec, w: 6 });
    } catch (e) { out.replaceChildren(el("div", { className: "error", textContent: e.message })); }
  };
  ask.onclick = go;
  input.onkeydown = (e) => e.key === "Enter" && go();
  body.append(el("p", { textContent: "Ask in plain English. A small on-device model turns your question into a chart spec over the semantic model; you review and edit it – it never writes free-form SQL, and nothing leaves your browser." }),
    el("div", { className: "ai-box" }, input, backendSel, ask), out, t1, t2, t3);
  openModal("Ask AI", body);
}

// ---------- chrome (menu, theme, share, import/export)
function wireChrome() {
  $("#btn-new").onclick = () => openBuilder(null);
  $("#b-cancel").onclick = () => $("#builder").close();
  $("#b-save").onclick = saveDraft;
  $("#btn-ai").onclick = openAI;
  $("#btn-theme").onclick = () => {
    const cur = document.documentElement.dataset.theme || (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    const next = cur === "dark" ? "light" : "dark";
    document.documentElement.dataset.theme = next; setTheme(next); renderDashboard();
  };
  $("#btn-share").onclick = async () => {
    const url = `${location.origin}${location.pathname}#d=${encodeShare(dash(), S.filters)}`;
    try { await navigator.clipboard.writeText(url); $("#status").textContent = "Link copied – it contains the whole dashboard, no server involved."; } catch { prompt("Copy this link", url); }
    history.replaceState(null, "", url.slice(location.origin.length));
  };
  const menu = $("#menu");
  $("#btn-menu").onclick = (e) => { e.stopPropagation(); menu.hidden = !menu.hidden; };
  document.addEventListener("click", () => (menu.hidden = true));
  menu.onclick = async (e) => {
    const act = e.target.dataset?.act;
    menu.hidden = true;
    if (act === "save") { const name = prompt("Dashboard name", dash().name + " (copy)"); if (name) { const c = { id: "u" + Date.now(), name, widgets: structuredClone(dash().widgets) }; S.dashboards.push(c); S.current = c.id; persist(); renderDashSelect(); renderDashboard(); } }
    if (act === "export") { const a = el("a", { href: URL.createObjectURL(new Blob([JSON.stringify({ name: dash().name, widgets: dash().widgets, filters: S.filters }, null, 2)], { type: "application/json" })), download: dash().name.replace(/\W+/g, "_") + ".json" }); a.click(); }
    if (act === "import") $("#file-in").click();
    if (act === "delete") { const d = dash(); if (d.starter) { alert("Starter dashboards are templates and cannot be deleted. Edit one to make your own copy."); return; } if (confirm(`Delete “${d.name}”?`)) { S.dashboards = S.dashboards.filter((x) => x !== d); S.current = S.dashboards[0].id; persist(); renderDashSelect(); renderDashboard(); } }
    if (act === "sql") openSql();
    if (act === "sources") openSources();
  };
  $("#file-in").onchange = async (e) => {
    const f = e.target.files[0]; if (!f) return;
    try { const o = JSON.parse(await f.text()); const c = { id: "u" + Date.now(), name: o.name || "Imported", widgets: o.widgets }; for (const w of c.widgets) compileSpec(S.catalog, w.spec); S.dashboards.push(c); S.current = c.id; if (o.filters) S.filters = o.filters; persist(); renderDashSelect(); renderFilters(); renderDashboard(); }
    catch (err) { alert("Could not import: " + err.message); }
    e.target.value = "";
  };
  let rt; addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(() => S.db && renderDashboard(), 250); });
}

boot().catch((e) => { $("#data-meta").textContent = "Failed to start: " + e.message; $("#status").textContent = String(e.stack || e); console.error(e); });
