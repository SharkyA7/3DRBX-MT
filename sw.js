// 3DRBXMT Service Worker (v4)
// - HTML pages: network-first (new deploys show up immediately, cache = offline fallback only)
// - Same-origin static files: stale-while-revalidate
// - Pinned CDN libs (three.js, jszip, fonts): cache-first, failures are never cached
// - API routes: not intercepted at all
// - Does NOT skipWaiting on install: the page asks for it after the user taps "Update"

const VERSION = 'v9';
const STATIC_CACHE = '3drbxmt-static-' + VERSION;
const CDN_CACHE = '3drbxmt-cdn-v1'; // CDN URLs are version-pinned, so this one can persist across releases

const PAGES = [
  '/', '/index.html', '/uv-checker', '/uv-checker.html', '/beta.html', '/flipbook.html',
  '/maintenance.html', '/privacy.html', '/terms.html'
];
const FILES = [
  '/manifest.json', '/favicon.png', '/icons/icon-192.png', '/icons/icon-512.png'
];
const CDN_ASSETS = [
  'https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Share+Tech+Mono&family=Space+Grotesk:wght@400;500;600;700&display=swap',
  'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js',
  'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/controls/OrbitControls.js',
  'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/loaders/OBJLoader.js',
  'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/loaders/MTLLoader.js',
  'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/exporters/OBJExporter.js',
  'https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js'
];
const CDN_HOSTS = ['cdn.jsdelivr.net', 'cdnjs.cloudflare.com', 'fonts.googleapis.com', 'fonts.gstatic.com'];
const PASSTHROUGH_PREFIXES = ['/api/', '/auth/', '/health'];
const STATIC_EXT = /\.(?:js|mjs|css|png|jpe?g|svg|ico|woff2?|ttf|txt|xml|json|wasm)$/i;

// Cache each file on its own so one failure can't wipe out the whole precache.
async function addSafely(cache, url) {
  try {
    const res = await fetch(new Request(url, { cache: 'reload' }));
    if (res && res.ok) await cache.put(url, res);
  } catch (err) {
    console.warn('[SW] precache skipped:', url);
  }
}

self.addEventListener('install', (event) => {
  event.waitUntil((async () => {
    const stat = await caches.open(STATIC_CACHE);
    await Promise.all([...PAGES, ...FILES].map((u) => addSafely(stat, u)));
    const cdn = await caches.open(CDN_CACHE);
    await Promise.all(CDN_ASSETS.map(async (u) => {
      if (await cdn.match(u)) return;
      await addSafely(cdn, u);
    }));
    // intentionally no self.skipWaiting() here
  })());
});

self.addEventListener('activate', (event) => {
  event.waitUntil((async () => {
    const keep = [STATIC_CACHE, CDN_CACHE];
    const names = await caches.keys();
    await Promise.all(
      names.filter((n) => n.startsWith('3drbxmt') && !keep.includes(n)).map((n) => caches.delete(n))
    );
    await self.clients.claim();
  })());
});

self.addEventListener('message', (event) => {
  const d = event.data;
  if (d === 'skipWaiting' || (d && d.type === 'SKIP_WAITING')) self.skipWaiting();
  if (d === 'getVersion' && event.ports && event.ports[0]) event.ports[0].postMessage({ version: VERSION });
});

self.addEventListener('fetch', (event) => {
  const req = event.request;
  if (req.method !== 'GET' || !req.url.startsWith('http') || req.headers.has('range')) return;

  const url = new URL(req.url);
  const sameOrigin = url.origin === self.location.origin;

  if (sameOrigin && PASSTHROUGH_PREFIXES.some((p) => url.pathname.startsWith(p))) return;

  if (req.mode === 'navigate' && sameOrigin) {
    event.respondWith(networkFirstPage(req, url));
    return;
  }
  if (CDN_HOSTS.includes(url.hostname)) {
    event.respondWith(cacheFirstCdn(req));
    return;
  }
  if (sameOrigin && STATIC_EXT.test(url.pathname)) {
    event.respondWith(staleWhileRevalidate(req));
    return;
  }
  // everything else: normal browser behaviour
});

async function networkFirstPage(req, url) {
  const cache = await caches.open(STATIC_CACHE);
  try {
    const res = await fetch(req);
    if (res.ok) cache.put(url.pathname, res.clone()); // key by path, not full URL/query
    return res;
  } catch (err) {
    const hit = await cache.match(url.pathname);
    if (hit) return hit;
    const fallback = url.pathname.toLowerCase().includes('uv-checker') ? '/uv-checker' : '/';
    return (await cache.match(fallback)) || Response.error();
  }
}

async function cacheFirstCdn(req) {
  const cache = await caches.open(CDN_CACHE);
  const hit = await cache.match(req);
  if (hit) return hit;
  const res = await fetch(req); // if this throws, the browser sees a real network error (not a fake 200/503 body)
  if (res.ok) cache.put(req, res.clone());
  return res;
}

async function staleWhileRevalidate(req) {
  const cache = await caches.open(STATIC_CACHE);
  const hit = await cache.match(req);
  const refresh = fetch(req).then((res) => {
    if (res.ok) cache.put(req, res.clone());
    return res;
  });
  if (hit) { refresh.catch(() => {}); return hit; }
  return refresh;
}
