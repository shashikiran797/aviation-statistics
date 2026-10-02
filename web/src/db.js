// DuckDB-WASM: queries Parquet over HTTP range requests, entirely in the browser.
import * as duckdb from "https://cdn.jsdelivr.net/npm/@duckdb/duckdb-wasm@1.29.0/+esm";

export async function openDb(baseUrl, tables) {
  const bundle = await duckdb.selectBundle(duckdb.getJsDelivrBundles());
  const workerUrl = URL.createObjectURL(new Blob([`importScripts("${bundle.mainWorker}");`], { type: "text/javascript" }));
  const db = new duckdb.AsyncDuckDB(new duckdb.ConsoleLogger(duckdb.LogLevel.WARNING), new Worker(workerUrl));
  await db.instantiate(bundle.mainModule, bundle.pthreadWorker);
  URL.revokeObjectURL(workerUrl);
  const conn = await db.connect();
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
