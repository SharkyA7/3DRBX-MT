# 3DRBX Model Parser (WASM)

A drop-in replacement for `rust-model-parser/` — same `rbx-dom`-based parsing
logic, but compiled to WebAssembly (WASI) and embedded directly inside the
Python backend instead of running as a separate Vercel Rust HTTP function.

## Why this exists

The Vercel Rust service (`rust-model-parser/`) works, but requires its own
Vercel project, its own hosting, and — during setup — the `vercel_runtime`
crate's HTTP/hyper bridge caused multiple rounds of build errors (documented
in that project's `api/parse_model.rs` history comment) before it worked.

This version drops that entire layer. There's no HTTP server here at all —
just a plain command-line program that reads bytes from stdin and writes JSON
to stdout. Python's `wasmtime` package runs it in-process, in the same
function invocation, with no network call and no separate deployment to
manage. The actual Roblox-format parsing logic (`variant_to_json`,
`parse_dom`, binary/XML detection) is copied verbatim from the working Rust
service — nothing about the parsing itself changed, only how it's invoked.

## Building

```bash
rustup target add wasm32-wasip1
cargo build --release --target wasm32-wasip1
cp target/wasm32-wasip1/release/rbx_wasm_parser.wasm ../backend/rbx_parser.wasm
```

If your toolchain doesn't recognize `wasm32-wasip1`, try the older name
`wasm32-wasi` instead — Rust's WASI target naming has changed across toolchain
versions; both refer to the same kind of build.

The `.wasm` file needs to end up at `backend/rbx_parser.wasm` (next to
`main.py`) — that's the path the Python integration expects, so it gets
bundled into the Vercel Python deployment automatically as a static file.

## Python side

`backend/requirements.txt` needs `wasmtime` added (already done). The
integration in `backend/main.py` (`_parse_model_via_wasm`) tries this path
FIRST, falls back to the Vercel Rust HTTP service
(`ROBLOX_MODEL_PARSER_URL`) if the `.wasm` file is missing or fails for any
reason, and falls back to the original hand-rolled Python parser as the final
safety net if both of those are unavailable. Nothing is removed — this is a
pure addition on top of what already works.

## ⚠️ Not yet compile-tested

Same caveat as always in this project: written without network access to
`cargo build` against the real crates. The good news is the surface area
here is much smaller than the Vercel version — no framework-specific APIs to
guess at, just `std::io::stdin`/`stdout`, which is about as stable as Rust
gets. The `rbx-dom` parsing logic itself is copied from the already-working
Vercel service, so it shouldn't need any changes. Run `cargo build` locally
and fix anything that comes up — if something does, it's most likely to be
the exact WASI target name (see Building section above), not the parsing code.
