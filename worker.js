// Cloudflare Worker: edge-caches specific Roblox asset routes so repeat
// downloads of the same mesh/texture/image never re-hit the Vercel backend
// (and, transitively, never re-hit Roblox's own CDN via cloudscraper either).
//
// Everything that ISN'T one of these routes falls straight through to normal
// static asset serving -- this Worker does not change how the site itself is
// served, only how these specific byte-heavy GET requests are handled.
//
// Why these three: Roblox asset IDs are immutable in practice -- the mesh or
// texture bytes for a given ID never change once uploaded, so caching them
// aggressively at the edge is safe, unlike e.g. catalog_info (item names,
// prices) or the OBJ/GLTF zip endpoints (built dynamically per request).

const CACHEABLE_PATHS = new Set([
  "/api/v2/model/mesh",
  "/api/v2/model/mesh-union",
  "/api/v2/model/texture",
  "/api/catalog/image",
]);

// The actual Vercel backend this Worker proxies cache-misses to. Point this at
// whichever of the two Vercel projects is the canonical one you want the edge
// cache backed by.
const BACKEND_ORIGIN = "https://getrbx3d.qzz.io";

// 7 days: long enough to eliminate the overwhelming majority of re-fetches for
// popular assets, short enough that anything wrongly cached (a transient
// Roblox-side error slipping through, say) ages out on its own without needing
// a manual purge.
const CACHE_TTL_SECONDS = 60 * 60 * 24 * 7;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    const isCacheable =
      request.method === "GET" && CACHEABLE_PATHS.has(url.pathname);

    if (!isCacheable) {
      // Not one of the asset routes -- serve the static site exactly as before.
      return env.ASSETS.fetch(request);
    }

    const cache = caches.default;
    // Cache key is the full request URL including query string, so different
    // asset_id / variant / format combinations are cached separately.
    const cacheKey = new Request(url.toString(), request);

    const cached = await cache.match(cacheKey);
    if (cached) {
      const hit = new Response(cached.body, cached);
      hit.headers.set("X-Edge-Cache", "hit");
      // The site itself is served from a different origin (getrbx3d.qzz.io on
      // Vercel) but fetches these specific routes from this Worker's domain --
      // that's a cross-origin request, so the browser needs this header or it
      // silently blocks the response from ever reaching the page's JS.
      hit.headers.set("Access-Control-Allow-Origin", "*");
      return hit;
    }

    // Cache miss -- fetch fresh from the real backend, which does the actual
    // cloudscraper/Roblox-CDN work.
    const backendUrl = BACKEND_ORIGIN + url.pathname + url.search;
    const backendResponse = await fetch(backendUrl, {
      headers: request.headers,
      cf: { cacheTtl: 0, cacheEverything: false }, // we manage caching ourselves below
    });

    if (!backendResponse.ok) {
      // Don't cache errors (a 404/502 for one user shouldn't poison the cache
      // for everyone else requesting the same asset a moment later).
      const miss = new Response(backendResponse.body, backendResponse);
      miss.headers.set("X-Edge-Cache", "miss-error");
      miss.headers.set("Access-Control-Allow-Origin", "*");
      return miss;
    }

    const response = new Response(backendResponse.body, backendResponse);
    response.headers.set(
      "Cache-Control",
      `public, max-age=${CACHE_TTL_SECONDS}, immutable`
    );
    response.headers.set("X-Edge-Cache", "miss");
    response.headers.set("Access-Control-Allow-Origin", "*");

    // Store a clone in the edge cache without blocking the response back to
    // this user.
    ctx.waitUntil(cache.put(cacheKey, response.clone()));

    return response;
  },
};
