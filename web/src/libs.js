// Third-party browser libraries. Served from web/vendor when present (see scripts/vendor.sh: no CDN, works offline);
// otherwise falls back to the pinned CDN builds, so a missing vendor step degrades instead of breaking.
const CDN = {
  duckdb: "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm",
  plot: "https://cdn.jsdelivr.net/npm/@observablehq/plot@0.6.16/+esm",
};
const local = (p) => new URL(`../vendor/${p}`, import.meta.url).href;

export const source = { duckdb: "vendor", plot: "vendor" };

async function load(name, localPath) {
  try {
    return await import(local(localPath));
  } catch {
    source[name] = "cdn";
    return import(CDN[name]);
  }
}

export const duckdb = await load("duckdb", "duckdb/duckdb.js");
export const Plot = await load("plot", "plot.js");

/** DuckDB-WASM bundle: the vendored 'eh' build served from this origin, or the CDN bundles when running without vendor files. */
export async function duckdbBundle() {
  if (source.duckdb === "vendor") {
    const ok = await fetch(local("duckdb/duckdb-eh.wasm"), { method: "HEAD" }).then((r) => r.ok, () => false);
    if (ok) return { mainModule: local("duckdb/duckdb-eh.wasm"), mainWorker: local("duckdb/duckdb-browser-eh.worker.js"), pthreadWorker: null, vendored: true, extensionRepo: local("duckdb/extensions") };
  }
  source.duckdb = "cdn";
  const b = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
  return { ...b, vendored: false };
}
