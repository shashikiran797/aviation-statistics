// Renders a widget (chart spec + result rows) as KPI tiles, line/bar/heatmap charts or a table.
import * as Plot from "https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.16/+esm";
import { fmt, fmtFull, monthLabel, toDate } from "./format.js";

const MAX_SERIES = 8;
const css = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim();
const palette = () => Array.from({ length: MAX_SERIES }, (_, i) => css(`--series-${i + 1}`));
const tipEl = () => document.getElementById("tip");

export function showTip(html, x, y) {
  const t = tipEl();
  t.replaceChildren(html);
  t.hidden = false;
  const r = t.getBoundingClientRect();
  t.style.left = Math.min(x + 14, innerWidth - r.width - 8) + "px";
  t.style.top = Math.min(y + 14, innerHeight - r.height - 8) + "px";
}
export const hideTip = () => (tipEl().hidden = true);

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

export function label(catalog, id) {
  return (catalog.dimensions[id] || catalog.measures[id] || {}).label || id;
}

export function chooseViz(spec, catalog) {
  const dims = spec.dimensions || [];
  if (!dims.length) return "kpi";
  const v = spec.viz && spec.viz !== "auto" ? spec.viz : null;
  if (v === "table") return "table";
  if (v === "heatmap") return dims.length >= 2 ? "heatmap" : "bar";
  if (v) return v;
  return catalog.dimensions[dims[0]].ordered ? "line" : "bar";
}

export function autoTitle(spec, catalog) {
  const ms = (spec.measures || []).map((m) => label(catalog, m)).join(", ");
  const ds = (spec.dimensions || []).map((d) => label(catalog, d)).join(" × ");
  const top = spec.top ? `Top ${spec.top.n} ${label(catalog, spec.top.dim).toLowerCase()}s – ` : "";
  return top + (ms || "Rows") + (ds ? ` by ${ds.toLowerCase()}` : "");
}

/** Normalise raw rows: dates -> Date, add x/series keys. */
function prep(rows, spec, catalog) {
  const [d0, d1] = spec.dimensions || [];
  return rows.map((r) => {
    const o = { ...r };
    for (const d of spec.dimensions || []) if (catalog.dimensions[d].kind === "time") o[d] = toDate(r[d]);
    o.__x = d0 ? o[d0] : null;
    o.__s = d1 ? String(o[d1]) : null;
    return o;
  });
}

function seriesOrder(data, m) {
  const tot = new Map();
  for (const r of data) tot.set(r.__s, (tot.get(r.__s) || 0) + (r[m] || 0));
  return [...tot.entries()].sort((a, b) => b[1] - a[1]).map(([k]) => k);
}

function legend(names, colors, hidden) {
  const lg = el("div", "legend");
  names.forEach((n, i) => {
    const s = el("span");
    const sw = el("i");
    sw.style.background = colors[i];
    s.append(sw, document.createTextNode(n));
    lg.append(s);
  });
  if (hidden > 0) lg.append(el("span", "more", `+${hidden} more not shown – use Top N or filters`));
  return lg;
}

function tableView(cols, rows, catalog) {
  const wrap = el("div", "tbl-wrap");
  const t = el("table", "t");
  const head = el("tr");
  cols.forEach((c, i) => {
    const th = el("th", catalog.dimensions[c] ? "l" : "", label(catalog, c));
    head.append(th);
  });
  const thead = el("thead");
  thead.append(head);
  t.append(thead);
  const tb = el("tbody");
  for (const r of rows.slice(0, 500)) {
    const tr = el("tr");
    cols.forEach((c) => {
      const isDim = !!catalog.dimensions[c];
      let v = r[c];
      if (isDim && catalog.dimensions[c].kind === "time") v = monthLabel(v);
      else if (!isDim) v = fmtFull(v);
      tr.append(el("td", isDim ? "l" : "", v === null ? "–" : String(v)));
    });
    tb.append(tr);
  }
  t.append(tb);
  wrap.append(t);
  if (rows.length > 500) wrap.append(el("p", "hint", `Showing first 500 of ${rows.length} rows – download CSV for all.`));
  return wrap;
}

function kpis(rows, spec, catalog) {
  const box = el("div", "kpis");
  const r = rows[0] || {};
  for (const m of spec.measures) {
    const k = el("div", "kpi");
    k.append(el("div", "v", fmt(r[m], catalog.measures[m].format)), el("div", "l", `${label(catalog, m)}${catalog.measures[m].unit && catalog.measures[m].unit !== "%" ? "" : ""}`));
    k.title = fmtFull(r[m]) + " " + (catalog.measures[m].unit || "");
    box.append(k);
  }
  return box;
}

function axisTick(m, catalog) {
  const f = catalog.measures[m].format;
  return (v) => fmt(v, f === "number" ? "number" : f);
}

/** One chart for one measure. */
function oneChart({ data, spec, catalog, measure, kind, width, onPick, colors, order }) {
  const [d0, d1] = spec.dimensions;
  const M = catalog.measures[measure];
  const timeX = catalog.dimensions[d0].kind === "time";
  const text = css("--text-2"), grid = css("--grid");
  const base = { width, height: kind === "hbar" ? Math.max(180, 26 * new Set(data.map((r) => r.__x)).size + 40) : 250, marginLeft: kind === "hbar" ? 130 : 52, marginBottom: 34,
    style: { background: "transparent", color: text, fontSize: "11px", overflow: "visible" } };
  const y = { label: null, grid: true, tickFormat: axisTick(measure, catalog), nice: true };
  const xT = { label: null, ...(timeX ? { tickFormat: (d) => monthLabel(d), ticks: width < 480 ? 4 : 8 } : { tickRotate: 0 }) };
  let marks, svg;
  const colorDomain = d1 ? order : null;
  const fillScale = d1 ? { color: { domain: order, range: colors.slice(0, order.length) } } : {};
  const solid = colors[0];
  const noHidden = d1 ? data.filter((r) => order.includes(r.__s)) : data;

  if (kind === "line") {
    marks = [
      ...(M.additive ? [Plot.ruleY([0], { stroke: grid })] : []),  // ratios keep their natural range
      Plot.line(noHidden, { x: "__x", y: measure, stroke: d1 ? "__s" : solid, strokeWidth: 2, curve: "monotone-x", marker: noHidden.length < 40 ? "dot" : null }),
    ];
    svg = Plot.plot({ ...base, ...fillScale, x: timeX ? { ...xT, type: "utc" } : { ...xT, type: "point" }, y, marks });
  } else if (kind === "bar") {
    marks = [Plot.ruleY([0], { stroke: grid }), Plot.barY(noHidden, { x: "__x", y: measure, fill: d1 ? "__s" : solid, rx2: 3, inset: 1 })];
    if (d1 && !M.additive) marks = [Plot.ruleY([0], { stroke: grid }), Plot.barY(noHidden, { fx: "__x", x: "__s", y: measure, fill: "__s", rx2: 3 })];
    svg = Plot.plot({ ...base, ...fillScale, x: d1 && !M.additive ? { axis: null } : { ...xT, type: "band", padding: 0.25 }, fx: d1 && !M.additive ? { label: null } : undefined, y, marks });
  } else if (kind === "hbar") {
    const sorted = [...noHidden].sort((a, b) => (b[measure] || 0) - (a[measure] || 0));
    marks = [Plot.ruleX([0], { stroke: grid }), Plot.barX(sorted, { y: "__x", x: measure, fill: d1 ? "__s" : solid, rx2: 3, inset: 1, sort: null })];
    svg = Plot.plot({ ...base, ...fillScale, y: { label: null, domain: [...new Set(sorted.map((r) => r.__x))], type: "band", padding: 0.2 }, x: { label: null, grid: true, tickFormat: axisTick(measure, catalog), nice: true }, marks });
  } else if (kind === "heatmap") {
    const lo = css("--seq-0"), hi = css("--seq-3");
    const dark = getComputedStyle(document.documentElement).colorScheme === "dark";  // dark ramp runs dark -> light
    const hmax = Math.max(...data.map((r) => r[measure] || 0)) || 1;
    marks = [Plot.cell(data, { x: "__x", y: "__s", fill: measure, inset: 1, rx: 3 }), Plot.text(data, { x: "__x", y: "__s", text: (r) => fmt(r[measure], M.format), fill: (r) => ((r[measure] || 0) / hmax > 0.55) === dark ? "#0b0b0b" : "#ffffff", fontSize: 10 })];
    svg = Plot.plot({ ...base, marginLeft: 130, height: Math.max(200, 26 * new Set(data.map((r) => r.__s)).size + 50),
      x: { ...xT, type: timeX ? "band" : "band", tickFormat: timeX ? (d) => monthLabel(d) : undefined }, y: { label: null, type: "band" }, color: { type: "linear", range: [lo, hi], legend: false }, marks });
  }
  return { svg, kind, noHidden };
}

/** Attach hover tooltip + click cross-filter to a rendered chart. */
function interact(host, svg, { kind, data, spec, catalog, measure, order, colors, onPick }) {
  const [d0, d1] = spec.dimensions;
  const M = catalog.measures[measure];
  const timeX = catalog.dimensions[d0].kind === "time";
  const xLabel = (v) => (timeX ? monthLabel(v) : String(v));
  const row = (name, value, color) => {
    const r = el("div", "tr");
    const l = el("span", "tl");
    if (color) { const k = el("i", "k"); k.style.background = color; l.append(k); }
    l.append(document.createTextNode(name));
    r.append(l, el("b", "", value));
    return r;
  };
  if (kind === "line") {
    const hair = el("div", "hair");
    hair.hidden = true;
    host.append(hair);
    const xs = [...new Map(data.map((r) => [+toDate(r.__x) || r.__x, r.__x])).values()];
    const sx = svg.scale("x");
    const pos = xs.map((v) => sx.apply(v));
    host.addEventListener("pointermove", (e) => {
      const hb = host.getBoundingClientRect(), sb = svg.getBoundingClientRect();
      const px = e.clientX - sb.left;
      let best = 0;
      pos.forEach((p, i) => { if (Math.abs(p - px) < Math.abs(pos[best] - px)) best = i; });
      if (!pos.length || px < pos[0] - 20 || px > pos[pos.length - 1] + 20) { hair.hidden = true; hideTip(); return; }
      hair.hidden = false;
      hair.style.left = sb.left - hb.left + pos[best] + "px";
      const at = data.filter((r) => String(+toDate(r.__x) || r.__x) === String(+toDate(xs[best]) || xs[best]));
      const box = el("div");
      box.append(el("div", "th", xLabel(xs[best])));
      if (d1) {
        const vis = at.filter((r) => order.includes(r.__s)).sort((a, b) => (b[measure] || 0) - (a[measure] || 0));
        vis.forEach((r) => box.append(row(r.__s, fmtFull(r[measure]), colors[order.indexOf(r.__s)])));
      } else box.append(row(M.label, fmtFull(at[0]?.[measure]) + (M.unit === "%" ? "%" : ""), colors[0]));
      showTip(box, e.clientX, e.clientY);
    });
    host.addEventListener("pointerleave", () => { hair.hidden = true; hideTip(); });
    return;
  }
  // bars / cells: the mark is the hit target
  const sel = kind === "heatmap" ? 'g[aria-label="cell"] > *' : 'g[aria-label="bar"] > *';
  const rects = [...svg.querySelectorAll(sel)];
  const items = kind === "hbar" ? [...data].sort((a, b) => (b[measure] || 0) - (a[measure] || 0)) : data;
  rects.forEach((rc, i) => {
    const r = items[i];
    if (!r) return;
    rc.style.cursor = d0 && catalog.dimensions[d0].kind !== "time" ? "pointer" : "default";
    rc.setAttribute("tabindex", "0");
    rc.setAttribute("aria-label", `${xLabel(r.__x)}${r.__s ? ", " + r.__s : ""}: ${fmtFull(r[measure])}`);
    const show = (e) => {
      const box = el("div");
      box.append(el("div", "th", xLabel(r.__x) + (r.__s ? " · " + r.__s : "")));
      box.append(row(M.label, fmtFull(r[measure]) + (M.unit === "%" ? "%" : ""), kind === "heatmap" ? null : rc.getAttribute("fill")));
      const b = rc.getBoundingClientRect();
      showTip(box, e.clientX ?? b.right, e.clientY ?? b.top);
      rc.style.opacity = "0.8";
    };
    rc.addEventListener("pointermove", show);
    rc.addEventListener("focus", show);
    rc.addEventListener("pointerleave", () => { hideTip(); rc.style.opacity = ""; });
    rc.addEventListener("blur", () => { hideTip(); rc.style.opacity = ""; });
    rc.addEventListener("click", () => onPick && !timeX && onPick(d0, r.__x));
    rc.addEventListener("keydown", (e) => { if (e.key === "Enter") rc.dispatchEvent(new Event("click")); });
  });
}

export function renderWidget(host, { spec, cols, rows, catalog, onPick, forceTable }) {
  host.replaceChildren();
  let viz = forceTable ? "table" : chooseViz(spec, catalog);
  if (!rows.length) { host.append(el("div", "empty", "No rows match these filters.")); return viz; }
  if (viz === "kpi") { host.append(kpis(rows, spec, catalog)); return viz; }
  if (viz === "table" || !spec.measures.length) { host.append(tableView(cols, rows, catalog)); return "table"; }
  const data = prep(rows, spec, catalog);
  const d0 = spec.dimensions[0];
  const colors = palette();
  const d1 = spec.dimensions[1];
  let order = null, hidden = 0;
  if (d1) {
    const all = seriesOrder(data, spec.measures[0]);
    order = all.slice(0, MAX_SERIES);
    hidden = all.length - order.length;
  }
  const nCats = new Set(data.map((r) => r.__x)).size;
  let kind = viz;
  if (kind === "bar" && !d1 && (nCats > 8 || String([...new Set(data.map((r) => r.__x))].join("")).length > 70)) kind = "hbar";
  if (kind === "heatmap" && !d1) kind = "bar";
  const width = Math.max(260, host.clientWidth - 4);
  const multi = spec.measures.length > 1;
  if (d1 && kind !== "heatmap") host.append(legend(order, colors, hidden));
  const grid = el("div");
  if (multi) { grid.style.cssText = "display:grid;gap:8px;grid-template-columns:repeat(auto-fit,minmax(280px,1fr))"; }
  host.append(grid);
  for (const m of spec.measures) {
    const cell = el("div");
    cell.style.position = "relative";
    if (multi) cell.append(el("div", "hint", label(catalog, m)));
    const w = multi ? Math.max(260, Math.floor((width - 8) / Math.max(1, Math.min(2, spec.measures.length)))) : width;
    const { svg } = oneChart({ data, spec, catalog, measure: m, kind: kind === "heatmap" && multi ? "bar" : kind, width: w, colors, order });
    cell.append(svg);
    grid.append(cell);
    interact(cell, svg, { kind, data: d1 && kind !== "heatmap" ? data.filter((r) => order.includes(r.__s)) : data, spec, catalog, measure: m, order, colors, onPick });
  }
  return kind;
}

export function toCsv(cols, rows) {
  const esc = (v) => { const s = v instanceof Date ? v.toISOString().slice(0, 10) : String(v ?? ""); return /[",\n]/.test(s) ? `"${s.replaceAll('"', '""')}"` : s; };
  return [cols.join(","), ...rows.map((r) => cols.map((c) => esc(r[c])).join(","))].join("\n");
}
