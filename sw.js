// 3DRBXMT Service Worker
// Handles offline caching for static assets while keeping API routes dynamic

const CACHE_NAME = '3drbxmt-v3';
const STATIC_CACHE_NAME = '3drbxmt-static-v3';
const API_CACHE_NAME = '3drbxmt-api-v3';

// Files to cache immediately on install
const STATIC_ASSETS = [
  '/',
  '/index.html',
  '/uv-checker.html',
  '/beta.html',
  '/flipbook.html',
  '/maintenance.html',
  '/privacy.html',
  '/terms.html',
  '/favicon.png',
  '/manifest.json',
  '/icons/icon-192.png',
  '/icons/icon-512.png',
  '/robots.txt',
  '/sitemap.xml',
  '/llms.txt',
  'https://fonts.googleapis.com/css2?family=Press+Start+2P&family=Share+Tech+Mono&family=Space+Grotesk:wght@400;500;600;700&display=swap',
  'https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js',
  'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/controls/OrbitControls.js',
  'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/loaders/OBJLoader.js',
  'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/loaders/MTLLoader.js',
  'https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/exporters/OBJExporter.js',
  'https://cdnjs.cloudflare.com/ajax/libs/jszip/3.10.1/jszip.min.js'
];

// API routes that should NOT be cached (always network-first)
const UNCACHED_ROUTES = [
  '/api/',
  '/api/v2/',
  '/api/catalog/',
  '/api/animation/',
  '/api/v2/model/',
  '/api/v2/item',
  '/health',
  '/auth/'
];

// Check if a URL is an API route that should bypass cache
function isApiRoute(url) {
  const urlObj = new URL(url);
  return UNCACHED_ROUTES.some(route => urlObj.pathname.startsWith(route));
}

// Check if a URL is a static asset we should cache
function isStaticAsset(url) {
  const urlObj = new URL(url);
  // Cache static files from our domain and known CDNs
  if (urlObj.origin === location.origin) {
    const pathname = urlObj.pathname;
    return pathname.endsWith('.html') ||
           pathname.endsWith('.js') ||
           pathname.endsWith('.mjs') ||
           pathname.endsWith('.css') ||
           pathname.endsWith('.png') ||
           pathname.endsWith('.jpg') ||
           pathname.endsWith('.jpeg') ||
           pathname.endsWith('.svg') ||
           pathname.endsWith('.ico') ||
           pathname.endsWith('.woff') ||
           pathname.endsWith('.woff2') ||
           pathname.endsWith('.ttf') ||
           pathname.endsWith('.txt') ||
           pathname.endsWith('.xml') ||
           pathname === '/' ||
           pathname === '/uv-checker';
  }
  // Cache known CDN resources
  return urlObj.hostname === 'cdn.jsdelivr.net' ||
         urlObj.hostname === 'cdnjs.cloudflare.com' ||
         urlObj.hostname === 'fonts.googleapis.com' ||
         urlObj.hostname === 'fonts.gstatic.com';
}

// Install event - cache static assets
self.addEventListener('install', (event) => {
  event.waitUntil(
    caches.open(STATIC_CACHE_NAME)
      .then((cache) => {
        console.log('[SW] Caching static assets');
        return cache.addAll(STATIC_ASSETS.map(url => new Request(url, { credentials: 'omit' })))
          .catch(err => {
            console.warn('[SW] Some static assets failed to cache:', err);
          });
      })
      .then(() => self.skipWaiting())
  );
});

// Activate event - clean up old caches
self.addEventListener('activate', (event) => {
  event.waitUntil(
    caches.keys()
      .then((cacheNames) => {
        return Promise.all(
          cacheNames
            .filter((name) => name !== STATIC_CACHE_NAME && name !== API_CACHE_NAME)
            .map((name) => caches.delete(name))
        );
      })
      .then(() => self.clients.claim())
  );
});

// Fetch event - handle requests with appropriate strategy
self.addEventListener('fetch', (event) => {
  const url = event.request.url;

  // Skip non-GET requests
  if (event.request.method !== 'GET') {
    return;
  }

  // Skip chrome-extension and other non-http(s) schemes
  if (!url.startsWith('http')) {
    return;
  }

  // API routes: Network-first, no caching
  if (isApiRoute(url)) {
    event.respondWith(networkFirstApi(event.request));
    return;
  }

  // Static assets: Cache-first
  if (isStaticAsset(url)) {
    event.respondWith(cacheFirstStatic(event.request));
    return;
  }

  // Everything else: Network-first with offline fallback
  event.respondWith(networkFirstWithFallback(event.request));
});

// Cache-first strategy for static assets
async function cacheFirstStatic(request) {
  const cache = await caches.open(STATIC_CACHE_NAME);
  const cached = await cache.match(request);
  if (cached) {
    return cached;
  }
  try {
    const response = await fetch(request, { credentials: 'omit' });
    if (response.ok) {
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    console.warn('[SW] Failed to fetch static asset:', request.url, err);
    // Try to serve a generic offline page for HTML requests
    if (request.headers.get('accept')?.includes('text/html')) {
      return caches.match('/index.html');
    }
    return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
  }
}

// Network-first for API routes (never cache)
async function networkFirstApi(request) {
  try {
    const response = await fetch(request);
    return response;
  } catch (err) {
    console.warn('[SW] API request failed (offline):', request.url);
    return new Response(
      JSON.stringify({ error: 'Offline - API unavailable', offline: true }),
      { status: 503, headers: { 'Content-Type': 'application/json' } }
    );
  }
}

// Network-first with offline fallback for other requests
async function networkFirstWithFallback(request) {
  try {
    const response = await fetch(request);
    // Optionally cache successful responses for non-API routes
    if (response.ok && isStaticAsset(request.url)) {
      const cache = await caches.open(STATIC_CACHE_NAME);
      cache.put(request, response.clone());
    }
    return response;
  } catch (err) {
    const cache = await caches.open(STATIC_CACHE_NAME);
    const cached = await cache.match(request);
    if (cached) {
      return cached;
    }
    // Fallback for HTML pages
    if (request.headers.get('accept')?.includes('text/html')) {
      return caches.match('/index.html');
    }
    return new Response('Offline', { status: 503, statusText: 'Service Unavailable' });
  }
}

// Handle messages from the client (e.g., skipWaiting)
self.addEventListener('message', (event) => {
  if (event.data === 'skipWaiting') {
    self.skipWaiting();
  }
  if (event.data === 'getVersion') {
    event.ports[0].postMessage({ version: CACHE_NAME });
  }
});

// Background sync for failed requests (optional)
self.addEventListener('sync', (event) => {
  if (event.tag === 'sync-failed-requests') {
    event.waitUntil(syncFailedRequests());
  }
});

async function syncFailedRequests() {
  // Could implement retry logic for failed API requests here
  console.log('[SW] Background sync triggered');
}