// Chart spec -> DuckDB SQL. JS port of src/dgca_stats/spec.py; both must emit byte-identical SQL
// (tests/vectors/spec_cases.json is checked by pytest and `node --test`).
export class SpecError extends Error {}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const OPS = new Set(["in", "not_in", "between"]);

function lit(v, kind) {
  if (kind === "time") {
    const s = String(v);
    if (!DATE_RE.test(s)) throw new SpecError(`bad date value ${v}`);
    return `DATE '${s}'`;
  }
  if (typeof v === "boolean" || v === null || v === undefined) throw new SpecError(`bad filter value ${v}`);
  if (typeof v === "number") return String(v);
  return "'" + String(v).replaceAll("'", "''") + "'";
}

export function effectiveFilters(catalog, spec) {
  const fact = catalog.facts[spec.fact];
  const all = spec.filters || [];
  const given = all.filter((f) => f.values && f.values.length);
  const have = new Set(all.map((f) => f.dim));
  return given.concat((fact.default_filters || []).filter((f) => !have.has(f.dim)));
}

export function compileSpec(catalog, spec) {
  if (!(spec.fact in catalog.facts)) throw new SpecError(`unknown fact ${spec.fact}`);
  const fact = catalog.facts[spec.fact];
  const dims = spec.dimensions || [], meas = spec.measures || [];
  if (!dims.length && !meas.length) throw new SpecError("choose at least one dimension or measure");
  for (const d of dims) if (!fact.dimensions.includes(d)) throw new SpecError(`unknown dimension ${d} for ${spec.fact}; valid dimensions: ${fact.dimensions.join(", ")}`);
  for (const m of meas) if (!fact.measures.includes(m)) throw new SpecError(`unknown measure ${m} for ${spec.fact}; valid measures: ${fact.measures.join(", ")}`);
  if (new Set(dims).size !== dims.length || new Set(meas).size !== meas.length) throw new SpecError("duplicate field");
  const D = catalog.dimensions, M = catalog.measures;
  const dexpr = (d) => `${D[d].table}.${D[d].column}`;
  const dsort = (d) => (D[d].sort_by ? `${D[d].table}.${D[d].sort_by}` : dexpr(d));

  const filters = effectiveFilters(catalog, spec);
  const top = spec.top;
  const used = new Set([...dims, ...filters.map((f) => f.dim), ...(top ? [top.dim] : [])]);
  for (const d of used) if (!fact.dimensions.includes(d)) throw new SpecError(`unknown dimension ${d} for ${spec.fact}; valid dimensions: ${fact.dimensions.join(", ")}`);
  const tables = new Set([...used].map((d) => D[d].table));
  // a dimension's `table` is a join alias; one physical table may play several roles (dim_place as origin and destination)
  const joins = Object.entries(fact.joins).filter(([alias]) => tables.has(alias))
    .map(([alias, [fk, dt, dk]]) => (alias === dt ? `JOIN ${dt} ON f.${fk} = ${dt}.${dk}` : `JOIN ${dt} AS ${alias} ON f.${fk} = ${alias}.${dk}`));

  const where = [];
  for (const f of filters) {
    if (!OPS.has(f.op)) throw new SpecError(`unknown filter op ${f.op}`);
    const vals = f.values.map((v) => lit(v, D[f.dim].kind));
    const e = dexpr(f.dim);
    if (f.op === "between") {
      if (vals.length !== 2) throw new SpecError("between needs two values");
      where.push(`${e} BETWEEN ${vals[0]} AND ${vals[1]}`);
    } else where.push(`${e} ${f.op === "not_in" ? "NOT " : ""}IN (${vals.join(", ")})`);
  }

  const base = `FROM ${fact.table} AS f` + joins.map((j) => `\n${j}`).join("");
  let ctes = "";
  const mainWhere = [...where];
  if (top) {
    if (!fact.measures.includes(top.measure) || !dims.includes(top.dim)) throw new SpecError("top needs a dimension in the chart and a model measure");
    const n = parseInt(top.n, 10);
    if (!(n >= 1 && n <= 1000)) throw new SpecError("top n out of range");
    const w = where.length ? "\nWHERE " + where.join("\n  AND ") : "";
    ctes = `WITH top_n AS (\nSELECT ${dexpr(top.dim)} AS v\n${base}${w}\nGROUP BY ${dexpr(top.dim)}\n` +
      `ORDER BY ${M[top.measure].expr} DESC NULLS LAST, v\nLIMIT ${n}\n)\n`;
    mainWhere.push(`${dexpr(top.dim)} IN (SELECT v FROM top_n)`);
  }

  const select = [...dims.map((d) => `${dexpr(d)} AS "${d}"`), ...meas.map((m) => `${M[m].expr} AS "${m}"`)];
  const group = [...dims.map(dexpr), ...dims.filter((d) => D[d].sort_by).map(dsort)];
  const order = [];
  if (spec.sort) {
    const by = spec.sort.by, dir = (spec.sort.dir || "desc").toUpperCase();
    if ((!dims.includes(by) && !meas.includes(by)) || !["ASC", "DESC"].includes(dir)) throw new SpecError("bad sort");
    order.push(`"${by}" ${dir} NULLS LAST`);
  }
  order.push(...dims.map(dsort));

  let sql = ctes + "SELECT " + select.join(",\n       ") + "\n" + base;
  if (mainWhere.length) sql += "\nWHERE " + mainWhere.join("\n  AND ");
  if (group.length) sql += "\nGROUP BY " + group.join(", ");
  if (order.length) sql += "\nORDER BY " + order.join(", ");
  if (spec.limit) {
    const lim = parseInt(spec.limit, 10);
    if (!(lim >= 1 && lim <= 100000)) throw new SpecError("limit out of range");
    sql += `\nLIMIT ${lim}`;
  }
  return sql;
}
