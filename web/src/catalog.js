// "Data catalog" view: search every file and extracted table the crawler found, entirely client-side.
const $ = (s, r = document) => r.querySelector(s);
const el = (tag, props = {}, ...kids) => { const e = Object.assign(document.createElement(tag), props); e.append(...kids); return e; };
const q1 = (s) => "'" + String(s).replaceAll("'", "''") + "'";
const PAGE = 40;

export async function initCatalog({ db, base, catalog, openTable }) {
  if (!catalog.site || !catalog.site.files) { $("#v-cat").hidden = true; return; }
  await db.register("site_files.parquet");
  await db.query("CREATE VIEW IF NOT EXISTS site_files AS SELECT * FROM parquet_scan('site_files.parquet')");
  const hasTables = catalog.site.tables > 0;
  if (hasTables) { await db.register("site_tables.parquet"); await db.query("CREATE VIEW IF NOT EXISTS site_tables AS SELECT * FROM parquet_scan('site_tables.parquet')"); }
  const st = { q: "", section: null, type: "all", page: 0 };

  const sections = (await db.query("SELECT section, count(*) n FROM site_files GROUP BY 1 ORDER BY 2 DESC")).rows;
  const tcount = hasTables ? (await db.query("SELECT section, count(*) n FROM site_tables GROUP BY 1")).rows : [];
  const types = (await db.query("SELECT ext, count(*) n FROM site_files GROUP BY 1 ORDER BY 2 DESC")).rows;

  const typeBox = $("#cat-types");
  const typeList = [["all", "All"], ...(hasTables ? [["tables", `Extracted tables (${catalog.site.tables})`]] : []), ["datasets", "Datasets (xlsx · csv · zip)"], ...types.map((t) => [t.ext, `${t.ext} (${t.n})`])];
  typeBox.replaceChildren(...typeList.map(([id, lbl]) => { const c = el("button", { className: "chip pick" + (id === st.type ? " on" : ""), textContent: lbl, id: `ct-${id}` }); c.onclick = () => { st.type = id; st.page = 0; run(); }; return c; }));

  const secBox = $("#cat-sections");
  const secLabel = (s) => s.replaceAll("_", " ");
  function drawSections() {
    const tmap = Object.fromEntries(tcount.map((r) => [r.section, r.n]));
    const all = el("button", { className: st.section === null ? "on" : "", onclick: () => { st.section = null; st.page = 0; run(); } }, el("span", { textContent: "All sections" }), el("small", { textContent: String(sections.reduce((a, b) => a + b.n, 0)) }));
    secBox.replaceChildren(all, ...sections.map((s) => el("button", { className: st.section === s.section ? "on" : "", onclick: () => { st.section = s.section; st.page = 0; run(); } }, el("span", { textContent: secLabel(s.section) }), el("small", { textContent: String(s.n) }))));
  }

  const qbox = $("#cat-q");
  let t; qbox.oninput = () => { clearTimeout(t); t = setTimeout(() => { st.q = qbox.value.trim(); st.page = 0; run(); }, 200); };

  function where(cols) {
    const w = [];
    if (st.q) for (const word of st.q.split(/\s+/).slice(0, 6)) w.push(`(${cols.map((c) => `lower(${c}) LIKE ${q1("%" + word.toLowerCase() + "%")}`).join(" OR ")})`);
    if (st.section) w.push(`section = ${q1(st.section)}`);
    return w;
  }

  async function run() {
    drawSections();
    for (const [id] of typeList) $(`#ct-${id}`).classList.toggle("on", id === st.type);
    const out = $("#cat-results");
    if (st.type === "tables") {
      const w = where(["title", "section_path", "columns"]);
      const total = (await db.query(`SELECT count(*) n FROM site_tables ${w.length ? "WHERE " + w.join(" AND ") : ""}`)).rows[0].n;
      const rows = (await db.query(`SELECT * FROM site_tables ${w.length ? "WHERE " + w.join(" AND ") : ""} ORDER BY n_rows DESC LIMIT ${PAGE} OFFSET ${st.page * PAGE}`)).rows;
      $("#cat-summary").textContent = `${total} extracted table${total === 1 ? "" : "s"} – real rows and columns scraped from DGCA web pages.`;
      out.replaceChildren(...rows.map((r) => {
        const b = el("button", { className: "lk", textContent: r.title || r.route, onclick: () => openTable(r) });
        return el("div", { className: "cat-row" }, el("div", {}, b, el("div", { className: "path", textContent: `${r.section_path}` }), el("div", { className: "path", textContent: `columns: ${r.columns.slice(0, 160)}` })), el("div", {}, el("span", { className: "tag ds", textContent: `${r.n_rows} × ${r.n_cols}` })));
      }));
      pager(out, total);
      return;
    }
    const w = where(["title", "path", "section", "coalesce(fy,'')"]);
    if (st.type === "datasets") w.push("ext IN ('xlsx','xls','csv','zip','xlsm')");
    else if (st.type !== "all") w.push(`ext = ${q1(st.type)}`);
    const cond = w.length ? "WHERE " + w.join(" AND ") : "";
    const total = (await db.query(`SELECT count(*) n FROM site_files ${cond}`)).rows[0].n;
    const rows = (await db.query(`SELECT * FROM site_files ${cond} ORDER BY in_dashboards DESC, section, path, title LIMIT ${PAGE} OFFSET ${st.page * PAGE}`)).rows;
    $("#cat-summary").textContent = `${total.toLocaleString()} document${total === 1 ? "" : "s"}${st.section ? " in " + secLabel(st.section) : ""}${st.q ? ` matching “${st.q}”` : ""}.`;
    out.replaceChildren(...rows.map((r) => {
      const link = r.url.startsWith("http") ? el("a", { href: r.url, target: "_blank", rel: "noopener", textContent: r.title || r.url }) : el("span", { textContent: (r.title || r.url) + " (dynamic page – opens inside dgca.gov.in)" });
      return el("div", { className: "cat-row" }, el("div", {}, link, el("div", { className: "path", textContent: [secLabel(r.section), r.path, r.fy].filter(Boolean).join(" · ") })),
        el("div", {}, r.in_dashboards ? el("span", { className: "tag dash", textContent: "in dashboards" }) : "", " ", el("span", { className: "tag", textContent: r.ext })));
    }));
    pager(out, total);
  }
  function pager(out, total) {
    const pages = Math.ceil(total / PAGE);
    if (pages <= 1) return;
    const prev = el("button", { textContent: "← Prev", disabled: st.page === 0, onclick: () => { st.page--; run(); } });
    const next = el("button", { textContent: "Next →", disabled: st.page >= pages - 1, onclick: () => { st.page++; run(); } });
    out.append(el("div", { className: "pager" }, prev, el("span", { textContent: `Page ${st.page + 1} of ${pages}` }), next));
  }
  await run();
  return { run };
}
