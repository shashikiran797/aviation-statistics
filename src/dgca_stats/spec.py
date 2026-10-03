"""Chart spec -> DuckDB SQL compiler (reference implementation).

The JS port in web/src/spec.js must produce byte-identical SQL; both are tested against tests/vectors/spec_cases.json.
A spec is {fact, dimensions[], measures[], filters[], top?, sort?, limit?}; everything is checked against the catalog,
so only model-declared identifiers ever reach the SQL and values are always escaped literals.
"""
import re

DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
OPS = {"in", "not_in", "between"}


class SpecError(ValueError):
    pass


def _lit(v, kind: str) -> str:
    if kind == "time":
        s = str(v)
        if not DATE_RE.match(s):
            raise SpecError(f"bad date value {v!r}")
        return f"DATE '{s}'"
    if isinstance(v, bool) or v is None:
        raise SpecError(f"bad filter value {v!r}")
    if isinstance(v, (int, float)):
        return repr(v) if isinstance(v, float) else str(v)
    return "'" + str(v).replace("'", "''") + "'"


def effective_filters(catalog: dict, spec: dict) -> list[dict]:
    fact = catalog["facts"][spec["fact"]]
    given = [f for f in spec.get("filters", []) if f.get("values")]
    have = {f["dim"] for f in spec.get("filters", [])}
    return given + [f for f in fact.get("default_filters", []) if f["dim"] not in have]


def compile_spec(catalog: dict, spec: dict) -> str:
    if spec.get("fact") not in catalog["facts"]:
        raise SpecError(f"unknown fact {spec.get('fact')!r}")
    fact = catalog["facts"][spec["fact"]]
    dims, meas = spec.get("dimensions", []), spec.get("measures", [])
    if not dims and not meas:
        raise SpecError("choose at least one dimension or measure")
    for d in dims:
        if d not in fact["dimensions"]:
            raise SpecError(f"unknown dimension {d} for {spec['fact']}; valid dimensions: {', '.join(fact['dimensions'])}")
    for m in meas:
        if m not in fact["measures"]:
            raise SpecError(f"unknown measure {m} for {spec['fact']}; valid measures: {', '.join(fact['measures'])}")
    if len(set(dims)) != len(dims) or len(set(meas)) != len(meas):
        raise SpecError("duplicate field")
    D, M = catalog["dimensions"], catalog["measures"]

    def dexpr(d):
        return f"{D[d]['table']}.{D[d]['column']}"

    def dsort(d):
        return f"{D[d]['table']}.{D[d]['sort_by']}" if D[d].get("sort_by") else dexpr(d)

    filters = effective_filters(catalog, spec)
    top = spec.get("top")
    used = set(dims) | {f["dim"] for f in filters} | ({top["dim"]} if top else set())
    for d in used:
        if d not in fact["dimensions"]:
            raise SpecError(f"unknown dimension {d} for {spec['fact']}; valid dimensions: {', '.join(fact['dimensions'])}")
    tables = {D[d]["table"] for d in used}
    # a dimension's `table` is a join *alias*; one physical table may play several roles (dim_place as origin and destination)
    joins = [f"JOIN {dt} ON f.{fk} = {dt}.{dk}" if alias == dt else f"JOIN {dt} AS {alias} ON f.{fk} = {alias}.{dk}"
             for alias, (fk, dt, dk) in fact["joins"].items() if alias in tables]

    where = []
    for f in filters:
        if f.get("op") not in OPS:
            raise SpecError(f"unknown filter op {f.get('op')!r}")
        dm = D[f["dim"]]
        vals = [_lit(v, dm["kind"]) for v in f["values"]]
        e = dexpr(f["dim"])
        if f["op"] == "between":
            if len(vals) != 2:
                raise SpecError("between needs two values")
            where.append(f"{e} BETWEEN {vals[0]} AND {vals[1]}")
        else:
            where.append(f"{e} {'NOT ' if f['op'] == 'not_in' else ''}IN ({', '.join(vals)})")

    base = f"FROM {fact['table']} AS f" + "".join(f"\n{j}" for j in joins)
    ctes = ""
    main_where = list(where)
    if top:
        if top.get("measure") not in fact["measures"] or top["dim"] not in dims:
            raise SpecError("top needs a dimension in the chart and a model measure")
        n = int(top["n"])
        if not 1 <= n <= 1000:
            raise SpecError("top n out of range")
        w = ("\nWHERE " + "\n  AND ".join(where)) if where else ""
        ctes = (f"WITH top_n AS (\nSELECT {dexpr(top['dim'])} AS v\n{base}{w}\nGROUP BY {dexpr(top['dim'])}\n"
                f"ORDER BY {M[top['measure']]['expr']} DESC NULLS LAST, v\nLIMIT {n}\n)\n")
        main_where.append(f"{dexpr(top['dim'])} IN (SELECT v FROM top_n)")

    select = [f'{dexpr(d)} AS "{d}"' for d in dims] + [f'{M[m]["expr"]} AS "{m}"' for m in meas]
    group = [dexpr(d) for d in dims] + [dsort(d) for d in dims if D[d].get("sort_by")]
    order = []
    if spec.get("sort"):
        by, direction = spec["sort"]["by"], spec["sort"].get("dir", "desc").upper()
        if by not in dims and by not in meas or direction not in ("ASC", "DESC"):
            raise SpecError("bad sort")
        order.append(f'"{by}" {direction} NULLS LAST')
    order += [dsort(d) for d in dims]

    sql = ctes + "SELECT " + ",\n       ".join(select) + "\n" + base
    if main_where:
        sql += "\nWHERE " + "\n  AND ".join(main_where)
    if group:
        sql += "\nGROUP BY " + ", ".join(group)
    if order:
        sql += "\nORDER BY " + ", ".join(order)
    if spec.get("limit"):
        lim = int(spec["limit"])
        if not 1 <= lim <= 100000:
            raise SpecError("limit out of range")
        sql += f"\nLIMIT {lim}"
    return sql
