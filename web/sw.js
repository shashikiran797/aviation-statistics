// Offline support. App shell: stale-while-revalidate. Vendor (DuckDB-WASM etc): cache-first.
// Data: network-first with cache fallback. Range requests (Parquet) go straight to the network.
const V = "v1";
const SHELL = `shell-${V}`, VENDOR = `vendor-${V}`, DATA = `data-${V}`;
const PRECACHE = ["./", "index.html", "styles.css", "manifest.webmanifest", "icons/icon.svg", "icons/favicon-32.png",
  "src/ai.js", "src/app.js", "src/catalog.js", "src/charts.js", "src/db.js", "src/format.js", "src/libs.js",
  "src/spec.js", "src/starter.js", "src/store.js"];

self.addEventListener("install", e => {
  e.waitUntil(caches.open(SHELL).then(c => c.addAll(PRECACHE)).then(() => self.skipWaiting()));
});
self.addEventListener("activate", e => {
  e.waitUntil(caches.keys().then(ks => Promise.all(ks.filter(k => !k.endsWith(`-${V}`)).map(k => caches.delete(k))))
    .then(() => self.clients.claim()));
});

async function put(name, req, res) {
  if (res.ok && res.status === 200) (await caches.open(name)).put(req, res.clone());
  return res;
}
const networkFirst = (name, req) => fetch(req).then(r => put(name, req, r)).catch(() => caches.match(req));
const cacheFirst = (name, req) => caches.match(req).then(h => h || fetch(req).then(r => put(name, req, r)));
const swr = (name, req) => caches.match(req).then(h => {
  const net = fetch(req).then(r => put(name, req, r)).catch(() => h);
  return h || net;
});

self.addEventListener("fetch", e => {
  const req = e.request, url = new URL(req.url);
  if (req.method !== "GET" || url.origin !== location.origin || req.headers.has("range")) return;
  const p = url.pathname;
  if (p.includes("/vendor/")) e.respondWith(cacheFirst(VENDOR, req));
  else if (p.includes("/data/")) e.respondWith(networkFirst(DATA, req));
  else e.respondWith(swr(SHELL, req));
});
