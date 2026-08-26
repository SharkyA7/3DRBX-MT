// api/parse_model.rs
//
// A Vercel Rust serverless function whose ONLY job is: given raw .rbxm or .rbxmx
// bytes (POSTed as the request body), parse them properly using rbx-dom (the Rojo
// project's model-format library) and return a flat JSON list of every instance
// with the properties our OBJ-conversion pipeline needs.
//
// This deliberately does NOT talk to Roblox's servers at all — the Python backend
// already has working, authenticated asset-fetching code (fetch_asset_raw_bytes,
// with Open Cloud + cookie fallback); duplicating that here would mean maintaining
// two auth implementations. This function's whole value is doing the PARSING
// correctly, which the existing hand-rolled Python parser can't (it doesn't
// understand SurfaceAppearance / modern PBR materials, among other gaps).
//
// AUTHORING NOTE: written without the ability to `cargo build`/`cargo check`
// against the real crates. First build attempt failed on vercel_runtime usage
// (fixed: this now uses the confirmed-correct `service_fn(handler)` + `Result<Value,
// Error>` pattern from Vercel's own current official example, vs. the wrong
// Response<Body>-based pattern from an outdated/community example I'd followed
// initially). Remaining highest-risk spots, each marked `// VERIFY` below:
//   1. How `Request::body()` exposes raw bytes (Body enum variants assumed)
//   2. rbx_xml's exact entry-point function name
//   3. Whether `instance.properties` iterates directly as shown
// The rbx-dom parsing logic itself (variant_to_json, parse_dom) is independent of
// all three and shouldn't need changes even if those need adjusting.

use rbx_dom_weak::{WeakDom};
use rbx_dom_weak::types::{Ref, Variant};
use serde::Serialize;
use serde_json::{json, Value};
use std::collections::HashMap;
use vercel_runtime::{run, service_fn, Error, Request};

#[derive(Serialize)]
struct FlatInstance {
    referent: String,
    parent_referent: Option<String>,
    class_name: String,
    name: String,
    properties: HashMap<String, serde_json::Value>,
}

/// Best-effort conversion of an rbx_dom_weak::types::Variant into a JSON value.
/// Variant is #[non_exhaustive] (rbx-dom adds new property types as Roblox does),
/// so a wildcard arm is required regardless — that arm falls back to the Rust
/// Debug representation, then the Python side regex-extracts whatever it actually
/// needs (e.g. asset IDs) from that string, the same defensive way the existing
/// Python code already handles inconsistently-shaped API responses elsewhere in
/// this project.
fn variant_to_json(v: &Variant) -> serde_json::Value {
    match v {
        Variant::String(s) => json!(s),
        Variant::Bool(b) => json!(b),
        Variant::Int32(i) => json!(i),
        Variant::Int64(i) => json!(i),
        Variant::Float32(f) => json!(f),
        Variant::Float64(f) => json!(f),
        Variant::Vector3(v3) => json!([v3.x, v3.y, v3.z]),
        Variant::Vector2(v2) => json!([v2.x, v2.y]),
        Variant::Color3(c) => json!([c.r, c.g, c.b]),
        Variant::Color3uint8(c) => json!([c.r, c.g, c.b]),
        Variant::CFrame(cf) => json!({
            "position": [cf.position.x, cf.position.y, cf.position.z],
            // Orientation matrix rows — Python side can derive Euler angles from
            // this if/when real rotation support gets added (today's parser
            // falls back to identity rotation, per its own docstring).
            "orientation": [
                [cf.orientation.x.x, cf.orientation.x.y, cf.orientation.x.z],
                [cf.orientation.y.x, cf.orientation.y.y, cf.orientation.y.z],
                [cf.orientation.z.x, cf.orientation.z.y, cf.orientation.z.z],
            ]
        }),
        Variant::Content(c) => json!(format!("{:?}", c)),
        Variant::Ref(r) => json!(format!("{:?}", r)),
        Variant::BrickColor(bc) => json!(format!("{:?}", bc)),
        Variant::Enum(e) => json!(e.to_u32()),
        // Anything else (Attributes, Tags, NumberSequence, PhysicalProperties,
        // SharedString, and whatever Roblox adds next): stringify via Debug so
        // this still compiles and returns *something* usable rather than
        // silently dropping the property.
        other => json!(format!("{:?}", other)),
    }
}

fn parse_dom(dom: &WeakDom) -> Vec<FlatInstance> {
    let mut out = Vec::new();
    let mut stack: Vec<(Ref, Option<Ref>)> = dom
        .root()
        .children()
        .iter()
        .map(|&r| (r, None))
        .collect();

    while let Some((referent, parent)) = stack.pop() {
        let Some(instance) = dom.get_by_ref(referent) else { continue };

        let mut props: HashMap<String, serde_json::Value> = HashMap::new();
        // VERIFY: assumes `instance.properties` is a public, iterable key→Variant
        // map (key type may be `Ustr` or `String` depending on rbx_dom_weak
        // version — `.to_string()` on the key works either way).
        for (key, value) in instance.properties.iter() {
            props.insert(key.to_string(), variant_to_json(value));
        }

        out.push(FlatInstance {
            referent: format!("{:?}", referent),
            parent_referent: parent.map(|p| format!("{:?}", p)),
            class_name: instance.class.to_string(),
            name: instance.name.clone(),
            properties: props,
        });

        for &child in instance.children() {
            stack.push((child, Some(referent)));
        }
    }
    out
}

async fn handler(req: Request) -> Result<Value, Error> {
    // VERIFY: body-extraction is the one part of this rewrite I couldn't confirm
    // against an official example (the docs snippet I have access to doesn't show
    // a body-reading handler, only a no-args "hello world" one). `Request` here
    // comes from the vercel_runtime crate, which is lambda_http-derived — in that
    // ecosystem `req.body()` returns a `&Body` enum (Empty/Text(String)/
    // Binary(Vec<u8>)), which is what the match below assumes. If this doesn't
    // compile, check vercel_runtime::Body's actual variants on docs.rs and adjust
    // this match accordingly — everything below this point (the actual rbx-dom
    // parsing) does not depend on exactly how this part resolves.
    let body_bytes: Vec<u8> = match req.body() {
        vercel_runtime::Body::Empty => Vec::new(),
        vercel_runtime::Body::Text(s) => s.clone().into_bytes(),
        vercel_runtime::Body::Binary(b) => b.clone(),
    };

    if body_bytes.is_empty() {
        return Ok(json!({"error": "empty body — POST raw .rbxm/.rbxmx bytes"}));
    }

    // Detect binary (.rbxm, starts with the magic header) vs XML (.rbxmx, starts
    // with "<roblox"). Same detection heuristic the existing Python parser uses.
    let is_xml = {
        let trimmed = body_bytes
            .iter()
            .position(|&b| !b.is_ascii_whitespace())
            .map(|i| &body_bytes[i..])
            .unwrap_or(&body_bytes[..]);
        trimmed.starts_with(b"<roblox")
    };

    // Both branches are normalized to Result<WeakDom, String> (via .map_err) since
    // rbx_xml::DecodeError and rbx_binary::DecodeError are different types — an
    // if/else needs both arms to produce the same type.
    let parse_result: Result<WeakDom, String> = if is_xml {
        // VERIFY: exact function name — rbx_xml's entry point may be named
        // differently (e.g. `from_reader` with a DecodeOptions arg instead of a
        // `_default` variant). Check rbx_xml's docs.rs page if this line errors.
        rbx_xml::from_reader_default(&body_bytes[..]).map_err(|e| e.to_string())
    } else {
        rbx_binary::from_reader(&body_bytes[..]).map_err(|e| e.to_string())
    };

    let dom = match parse_result {
        Ok(d) => d,
        Err(e) => {
            return Ok(json!({"error": format!("Gagal parse RBXM/RBXMX: {}", e), "supported": false}));
        }
    };

    let instances = parse_dom(&dom);
    Ok(json!({ "instances": instances, "count": instances.len() }))
}

#[tokio::main]
async fn main() -> Result<(), Error> {
    let service = service_fn(handler);
    run(service).await
}
