// DuckDB-WASM: queries Parquet over HTTP range requests, entirely in the browser.
import { duckdb, duckdbBundle } from "./libs.js";

export async function openDb(baseUrl, tables) {
  const bundle = await duckdbBundle();
  // same-origin worker when vendored; a cross-origin CDN worker must be wrapped in a blob that importScripts() it
  const workerUrl = bundle.vendored ? bundle.mainWorker : URL.createObjectURL(new Blob([`importScripts("${bundle.mainWorker}");`], { type: "text/javascript" }));
  const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), new Worker(workerUrl));
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  if (!bundle.vendored) URL.revokeObjectURL(workerUrl);
  const conn = await db.connect();
  // Parquet support is a DuckDB extension that is otherwise downloaded from extensions.duckdb.org at runtime
  if (bundle.vendored) await conn.query(`SET custom_extension_repository = '${bundle.extensionRepo}'`);
  for (const t of tables) {
    const file = `${t}.parquet`;
    await db.registerFileURL(file, new URL(`${baseUrl}/${file}`, location.href).href, duckdb.DuckDBDataProtocol.HTTP, false);
    await conn.query(`CREATE VIEW ${t} AS SELECT * FROM parquet_scan('${file}')`);
  }
  const registered = new Set(tables.map((t) => `${t}.parquet`));
  return {
    /** Register another parquet file lazily (fetched by range requests only when queried). */
    async register(file) {
      if (registered.has(file)) return;
      registered.add(file);
      await db.registerFileURL(file, new URL(`${baseUrl}/${file}`, location.href).href, duckdb.DuckDBDataProtocol.HTTP, false);
    },
    async query(sql) {
      const table = await conn.query(sql);
      const cols = table.schema.fields.map((f) => f.name);
      const rows = table.toArray().map((r) => {
        const o = {};
        for (const c of cols) {
          let v = r[c];
          if (typeof v === "bigint") v = Number(v);
          o[c] = v;
        }
        return o;
      });
      return { cols, rows };
    },
  };
}
