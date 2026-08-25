# 3DRBX Model Parser (Rust)

A small, standalone Vercel project whose only job is: given raw `.rbxm`/`.rbxmx`
bytes, parse them properly using [rbx-dom](https://github.com/rojo-rbx/rbx-dom)
(the Rojo project's Roblox model-format library) and return structured JSON.

## Why this is a separate project

The main 3DRBX-MT backend (`backend/main.py`) uses Vercel's legacy `builds`/
`routes` config in its `vercel.json` — and Vercel does not allow mixing that
with the `functions` config (or zero-config `api/` auto-detection) that the
official Rust runtime needs. Rather than risk rewriting the main project's
entire routing/headers config blind, this parser lives as its own tiny,
independent deployment that the main backend calls over HTTPS — the same way
it already calls Roblox's own APIs.

## What it does NOT do

This function never talks to Roblox's servers. It has no authentication, no
cookies, no API keys — it only parses bytes it's given. The main Python
backend already has working, authenticated asset-fetching
(`fetch_asset_raw_bytes`, with Open Cloud + cookie fallback); this project
intentionally doesn't duplicate that.

## ⚠️ Not yet compile-tested

This was written without network access to `cargo build`/`cargo check`
against the real crates. The overall structure and the `rbx_binary`/
`vercel_runtime` usage patterns are confirmed from their published docs, but
a few specific lines are flagged `// VERIFY` in `api/parse_model.rs` — most
likely spot for a small fix if the first `cargo build` errors out. Run
`cargo build` locally before deploying and fix anything that doesn't match
the crates' actual current API.

## Deploying

1. `cd rust-model-parser`
2. `cargo build` — fix any compile errors first (see note above)
3. Push this folder as its own Vercel project:
   - Easiest: make this folder its own Git repo, then "Import Project" on
     Vercel pointing at it
   - Or: keep it in the same repo as the main project, but when creating the
     new Vercel project, set **Root Directory** to `rust-model-parser` in
     the project's settings
4. Vercel should auto-detect `api/parse_model.rs` + the root `Cargo.toml` and
   deploy it as a Rust function — no further `vercel.json` config needed for
   the common case
5. Once deployed, copy that project's URL (e.g.
   `https://your-parser-name.vercel.app`)
6. In the **main** 3DRBX-MT project's environment variables, add:
   ```
   ROBLOX_MODEL_PARSER_URL=https://your-parser-name.vercel.app
   ```
7. Redeploy the main project

## API

`POST /api/parse_model`
Body: raw `.rbxm` or `.rbxmx` bytes (binary or XML auto-detected)

Response:
```json
{
  "count": 42,
  "instances": [
    {
      "referent": "...",
      "parent_referent": "...",
      "class_name": "MeshPart",
      "name": "Head",
      "properties": { "Size": [1.0, 1.0, 1.0], "MeshId": "...", ... }
    }
  ]
}
```

## If it's not configured

The main backend falls back to its existing (less capable, Python-only)
`.rbxm` parser automatically if `ROBLOX_MODEL_PARSER_URL` isn't set, or if a
call to this service fails for any reason — this is a pure enhancement, not
a hard dependency.

## Status

✅ Main backend integration is done (`backend/main.py`: `MODEL_PARSER_URL`,
`_parse_model_via_rust_service`, `_manifest_from_rust_instances`,
wired into `/api/v2/model/info`). What's left is deploying *this* folder as
its own Vercel project (steps above) and setting the env var on the main
project — nothing more to change on the Python side.
