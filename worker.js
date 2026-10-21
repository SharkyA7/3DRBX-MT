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

// Short-lived public JSON, shared by every visitor. The marketplace browse
// results are identical for everyone who asks the same thing, so the edge can
// answer repeats without waking the backend (or Roblox). Unlike the immutable
// asset bytes above, the catalog changes -- new items, new sort order -- so this
// is cached for minutes, not days. path -> TTL in seconds.
const SHORT_TTL_PATHS = new Map([
  ["/api/catalog/browse", 300],      // marketplace search pages
  ["/api/catalog/items-meta", 600],  // Roblox's name + thumbnail for a list of item IDs
]);

// Only these query params reach the backend/cache key for browse. Anything else
// is dropped, so junk params can't be used to dodge the cache and hammer the
// backend (and Roblox) with endless unique URLs.
const BROWSE_PARAMS = ["q", "category", "sub", "sort", "cursor"];

// The actual Vercel backend this Worker proxies cache-misses to. Point this at
// whichever of the two Vercel projects is the canonical one you want the edge
// cache backed by.
const BACKEND_ORIGIN = "https://getrbx3d.qzz.io";

// 7 days: long enough to eliminate the overwhelming majority of re-fetches for
// popular assets, short enough that anything wrongly cached (a transient
// Roblox-side error slipping through, say) ages out on its own without needing
// a manual purge.
const CACHE_TTL_SECONDS = 60 * 60 * 24 * 7;

// items-meta: the same set of IDs always gets the same cache entry, whatever
// order they were asked in. The page matches results by "type:id", not by
// position, so sorting here is safe.
function canonicalItemsMetaUrl(url) {
  const out = new URL(url.origin + url.pathname);
  for (const key of ["assets", "bundles"]) {
    const ids = (url.searchParams.get(key) || "")
      .split(",").map((x) => x.trim()).filter(Boolean);
    // Anything that isn't plain digits is left as-is: the backend rejects it
    // with a 400, which is never cached.
    const sorted = ids.every((x) => /^\d{1,16}$/.test(x))
      ? [...new Set(ids.map(Number))].sort((a, b) => a - b).join(",")
      : ids.join(",");
    if (sorted !== "") out.searchParams.set(key, sorted);
  }
  return out;
}

// Whether a successful backend answer is worth keeping at the edge.
// browse: only pages that actually have items (an empty page can be a Roblox
//   hiccup). items-meta: only if Roblox knew at least one of the items (an
//   all-"not found" answer could equally be a hiccup).
function worthCaching(pathname, body) {
  if (!body || !Array.isArray(body.items)) return false;
  if (pathname === "/api/catalog/items-meta") return body.items.some((i) => i && i.available);
  return body.items.length > 0;
}

// One canonical URL per distinct browse query: only known params, trimmed and
// lower-cased `q`, stable order. "Shirt" and "shirt " share one cache entry.
function canonicalBrowseUrl(url) {
  if (url.pathname === "/api/catalog/items-meta") return canonicalItemsMetaUrl(url);
  const out = new URL(url.origin + url.pathname);
  for (const key of BROWSE_PARAMS) {
    let v = url.searchParams.get(key);
    if (v === null) continue;
    v = v.trim();
    if (key === "q") v = v.toLowerCase().slice(0, 100);
    if (v !== "") out.searchParams.set(key, v);
  }
  return out;
}

// The backend rate-limits by the first X-Forwarded-For address. Behind a Worker
// that would otherwise be Cloudflare's address (one shared bucket for everyone)
// or a value the visitor wrote themselves. Cloudflare sets CF-Connecting-IP
// itself and a visitor can't forge it, so hand the backend that.
function backendHeaders(request) {
  const headers = new Headers(request.headers);
  const ip = request.headers.get("CF-Connecting-IP");
  if (ip) headers.set("X-Forwarded-For", ip);
  return headers;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    const isImmutable =
      request.method === "GET" && CACHEABLE_PATHS.has(url.pathname);
    const shortTtl =
      request.method === "GET" ? SHORT_TTL_PATHS.get(url.pathname) : undefined;
    const isShortLived = shortTtl !== undefined;

    if (!isImmutable && !isShortLived) {
      // Not one of the proxied routes -- serve the static site exactly as before.
      return env.ASSETS.fetch(request);
    }

    // ?debug=1 is for diagnosing Roblox responses: always live, never cached.
    if (isShortLived && url.searchParams.get("debug") === "1") {
      const live = await fetch(BACKEND_ORIGIN + url.pathname + url.search, {
        headers: backendHeaders(request),
        cf: { cacheTtl: 0, cacheEverything: false },
      });
      const res = new Response(live.body, live);
      res.headers.set("Cache-Control", "no-store");
      res.headers.set("X-Edge-Cache", "bypass-debug");
      res.headers.set("Access-Control-Allow-Origin", "*");
      return res;
    }

    const cache = caches.default;
    // Immutable assets: the full request URL is the key, so different asset_id /
    // variant / format combinations cache separately. Browse: canonical URL.
    const canonical = isShortLived ? canonicalBrowseUrl(url) : null;
    const cacheKey = isShortLived
      ? new Request(canonical.toString(), { method: "GET" })
      : new Request(url.toString(), request);

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
    // cloudscraper/Roblox work. Browse sends only the canonical query.
    const backendUrl = isShortLived
      ? BACKEND_ORIGIN + canonical.pathname + canonical.search
      : BACKEND_ORIGIN + url.pathname + url.search;
    const backendResponse = await fetch(backendUrl, {
      headers: backendHeaders(request),
      cf: { cacheTtl: 0, cacheEverything: false }, // we manage caching ourselves below
    });

    if (!backendResponse.ok) {
      // Don't cache errors (a 404/502/429 for one user shouldn't poison the cache
      // for everyone else requesting the same thing a moment later).
      const miss = new Response(backendResponse.body, backendResponse);
      miss.headers.set("Cache-Control", "no-store");
      miss.headers.set("X-Edge-Cache", "miss-error");
      miss.headers.set("Access-Control-Allow-Origin", "*");
      return miss;
    }

    if (isShortLived) {
      // Cache a browse page only if it actually has items: an empty page can be
      // a Roblox hiccup, and pinning "nothing found" for 5 minutes would hurt.
      const text = await backendResponse.text();
      let hasItems = false;
      try {
        hasItems = worthCaching(url.pathname, JSON.parse(text));
      } catch (_) { /* not JSON -> don't cache */ }

      const response = new Response(text, backendResponse);
      response.headers.set(
        "Cache-Control",
        hasItems ? `public, max-age=${shortTtl}` : "no-store"
      );
      response.headers.set("X-Edge-Cache", hasItems ? "miss" : "miss-empty");
      response.headers.set("Access-Control-Allow-Origin", "*");
      if (hasItems) ctx.waitUntil(cache.put(cacheKey, response.clone()));
      return response;
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
