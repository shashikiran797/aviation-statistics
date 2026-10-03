#!/usr/bin/env bash
# Fetch the two browser libraries into web/vendor so the app runs without a CDN (offline, no third-party outage risk).
# Versions are pinned; web/vendor/MANIFEST.json records them with checksums. web/vendor is gitignored: run this before serving or deploying.
set -euo pipefail
cd "$(dirname "$0")/.."
DUCKDB=1.29.0; PLOT=0.6.16; ESBUILD=0.24.0
OUT=web/vendor
TMP=$(mktemp -d); trap 'rm -rf "$TMP"' EXIT
mkdir -p "$OUT/duckdb"
(cd "$TMP" && npm init -y >/dev/null && npm install --no-audit --no-fund --loglevel=error "@duckdb/duckdb-wasm@$DUCKDB" "@observablehq/plot@$PLOT" "esbuild@$ESBUILD")
D="$TMP/node_modules/@duckdb/duckdb-wasm/dist"
# 'eh' (WebAssembly exception handling) is supported by every current browser; the 'mvp' build only adds ~40 MB for very old ones
cp "$D/duckdb-eh.wasm" "$D/duckdb-browser-eh.worker.js" "$OUT/duckdb/"
# DuckDB-WASM loads its Parquet reader as an extension at runtime (from extensions.duckdb.org by default). Vendor it and point DuckDB at it.
# The version directory must match the engine version inside duckdb-wasm $DUCKDB (v1.1.1); a mismatch makes the engine refuse the file.
EXT_VER=v1.1.1
mkdir -p "$OUT/duckdb/extensions/$EXT_VER/wasm_eh"
curl -fsSL "https://extensions.duckdb.org/$EXT_VER/wasm_eh/parquet.duckdb_extension.wasm" -o "$OUT/duckdb/extensions/$EXT_VER/wasm_eh/parquet.duckdb_extension.wasm"
# the published duckdb ESM imports 'apache-arrow' as a bare specifier, which browsers cannot resolve: bundle it in (as with d3 for Plot)
echo 'export * from "@observablehq/plot";' > "$TMP/plot-entry.js"
echo 'export * from "@duckdb/duckdb-wasm";' > "$TMP/duckdb-entry.js"
ROOT_DIR=$(pwd)
(cd "$TMP" && node_modules/.bin/esbuild plot-entry.js --bundle --minify --format=esm --outfile="$ROOT_DIR/$OUT/plot.js" --log-level=warning \
  && node_modules/.bin/esbuild duckdb-entry.js --bundle --minify --format=esm --platform=browser --outfile="$ROOT_DIR/$OUT/duckdb/duckdb.js" --log-level=warning)
python3 - <<PY
import hashlib, json, pathlib
root = pathlib.Path("$OUT")
files = {str(p.relative_to(root)): {"sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size} for p in sorted(root.rglob("*")) if p.is_file() and p.name != "MANIFEST.json"}
(root / "MANIFEST.json").write_text(json.dumps({"duckdb-wasm": "$DUCKDB", "observable-plot": "$PLOT", "files": files}, indent=1))
print("vendored:", {k: f"{v['bytes']/1e6:.1f} MB" for k, v in files.items()})
PY
