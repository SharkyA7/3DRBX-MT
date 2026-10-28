// Cloudflare Worker: edge-caches specific Roblox asset routes so repeat
// downloads of the same mesh/texture/image never re-hit the Vercel backend
// (and, transitively, never re-hit Roblox's own CDN via cloudscraper either).
//
// Everything that ISN'T one of these routes falls straight through to normal
// static asset serving -- this Worker does not change how the site itself is
// served, only how these specific byte-heavy GET requests are handled.
//
// Why these: Roblox asset IDs are immutable in practice -- the mesh or
// texture bytes for a given ID never change once uploaded, so caching them
// aggressively at the edge is safe, unlike e.g. catalog_info (item names,
// prices). The Catalog ZIP/animation downloads (ZIP_PATHS below) are built per
// request but are a pure function of (id, format, name), so they are cached for
// a day under a canonical key: a repeat download never wakes Vercel or Roblox.

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
  ["/api/catalog/browse", 60 * 30],      // marketplace search pages (30 min)
  ["/api/catalog/items-meta", 60 * 30],  // Roblox's name + thumbnail for a list of item IDs (30 min)
]);

// Catalog downloads (ZIP / BVH / GLB). path -> the ONLY query params that reach
// the backend and the cache key. Anything else is dropped, so junk params can't
// be used to dodge the cache. Deliberately NOT here: avatar downloads (an avatar
// changes), /api/v2/model/download (models get new versions) and item-batch (POST).
const ZIP_TTL_SECONDS = 60 * 60 * 24 * 7; // 7 days
const ZIP_PATHS = new Map([
  ["/api/v2/item", ["id", "format", "name"]],
  ["/api/animation/download-full", ["asset_id", "format"]],
  ["/api/animation/download-bundle", ["bundle_id", "format"]],
]);
const ZIP_ID_PARAMS = new Set(["id", "asset_id", "bundle_id"]);
const ZIP_FORMATS = new Set(["gltf", "obj", "fbx", "bvh"]);

// Canonical URL for a ZIP request, or null if a param looks wrong (then the
// request goes live to the backend, which answers 400, and nothing is cached).
function canonicalZipUrl(url, params) {
  const out = new URL(url.origin + url.pathname);
  for (const key of params) {
    let v = url.searchParams.get(key);
    if (v === null) continue;
    v = v.trim();
    if (key === "name") v = v.slice(0, 200);
    if (v === "") continue;
    if (ZIP_ID_PARAMS.has(key) && !/^\d{1,16}$/.test(v)) return null;
    if (key === "format") {
      v = v.toLowerCase();
      if (!ZIP_FORMATS.has(v)) return null;
    }
    out.searchParams.set(key, v);
  }
  return out;
}

// CORS: the site lives on another origin, and the animation download reads
// Content-Disposition for its filename, which the browser hides unless exposed.
function zipResponse(res, edge) {
  const r = new Response(res.body, res);
  r.headers.set("Access-Control-Allow-Origin", "*");
  r.headers.set("Access-Control-Expose-Headers", "Content-Disposition, X-Edge-Cache");
  r.headers.set("X-Edge-Cache", edge);
  return r;
}

// Only these query params reach the backend/cache key for browse. Anything else
// is dropped, so junk params can't be used to dodge the cache and hammer the
// backend (and Roblox) with endless unique URLs.
const BROWSE_PARAMS = ["q", "category", "sub", "sort", "cursor"];

// The actual Vercel backend this Worker proxies cache-misses to. Point this at
// whichever of the two Vercel projects is the canonical one you want the edge
// cache backed by.
const BACKEND_ORIGIN = "https://getrbx3d.qzz.io";

// 30 days: Roblox asset IDs are immutable — mesh/texture bytes never change once
// uploaded. 30 days eliminates re-fetches for popular assets; any wrongly cached
// error ages out without manual purge.
const CACHE_TTL_SECONDS = 60 * 60 * 24 * 30; // 30 days (immutable assets)

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

async function handleZip(request, ctx, url, params) {
  const canonical = canonicalZipUrl(url, params);

  // Never cached: bad params, authenticated requests, ?debug=1 diagnostics.
  if (!canonical || request.headers.has("Authorization") || url.searchParams.get("debug") === "1") {
    const live = await fetch(BACKEND_ORIGIN + url.pathname + url.search, {
      headers: backendHeaders(request),
      cf: { cacheTtl: 0, cacheEverything: false },
    });
    const res = zipResponse(live, "bypass");
    res.headers.set("Cache-Control", "no-store");
    return res;
  }

  const cache = caches.default;
  const cacheKey = new Request(canonical.toString(), { method: "GET" });
  const cached = await cache.match(cacheKey);
  if (cached) return zipResponse(cached, "hit");

  const upstream = await fetch(BACKEND_ORIGIN + canonical.pathname + canonical.search, {
    headers: backendHeaders(request),
    cf: { cacheTtl: 0, cacheEverything: false }, // we manage caching ourselves
  });

  if (!upstream.ok) {
    const err = zipResponse(upstream, "miss-error");
    err.headers.set("Cache-Control", "no-store");
    return err;
  }

  // The backend marks incomplete results (a bundle component failed, an FBX
  // fell back to OBJ) no-store: pass those through without pinning them.
  if (/no-store/i.test(upstream.headers.get("Cache-Control") || "")) {
    return zipResponse(upstream, "miss-nocache");
  }

  const res = zipResponse(upstream, "miss");
  res.headers.set("Cache-Control", `public, max-age=${ZIP_TTL_SECONDS}`);
  ctx.waitUntil(cache.put(cacheKey, res.clone()));
  return res;
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    const zipParams = request.method === "GET" ? ZIP_PATHS.get(url.pathname) : undefined;
    if (zipParams) return handleZip(request, ctx, url, zipParams);

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
