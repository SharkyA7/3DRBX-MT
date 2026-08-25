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
// against the real rbx-dom crates (no network access in the authoring sandbox).
// The rbx_binary::from_reader / dom.root() / dom.get_by_ref() shapes below are
// confirmed from the crate's own published usage example, but exact field names on
// `Instance` (e.g. whether the properties map key type is `Ustr` vs `String`) are
// the most likely spot to need a small fix after a real `cargo build`.

use rbx_dom_weak::{WeakDom};
use rbx_dom_weak::types::{Ref, Variant};
use serde::Serialize;
use serde_json::json;
use std::collections::HashMap;
use vercel_runtime::{run, Body, Error, Request, Response, StatusCode};

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

async fn handler(req: Request) -> Result<Response<Body>, Error> {
    let body_bytes: &[u8] = req.body();
    if body_bytes.is_empty() {
        return Ok(Response::builder()
            .status(StatusCode::BAD_REQUEST)
            .header("content-type", "application/json")
            .body(json!({"error": "empty body — POST raw .rbxm/.rbxmx bytes"}).to_string().into())?);
    }

    // Detect binary (.rbxm, starts with the magic header) vs XML (.rbxmx, starts
    // with "<roblox"). Same detection heuristic the existing Python parser uses.
    let is_xml = {
        let trimmed = body_bytes
            .iter()
            .position(|&b| !b.is_ascii_whitespace())
            .map(|i| &body_bytes[i..])
            .unwrap_or(body_bytes);
        trimmed.starts_with(b"<roblox")
    };

    let parse_result = if is_xml {
        // VERIFY: exact function name — rbx_xml's entry point may be named
        // differently (e.g. `from_reader` with a DecodeOptions arg instead of a
        // `_default` variant). Check rbx_xml's docs.rs page if this line errors.
        rbx_xml::from_reader_default(body_bytes)
    } else {
        rbx_binary::from_reader(body_bytes)
    };

    let dom = match parse_result {
        Ok(d) => d,
        Err(e) => {
            return Ok(Response::builder()
                .status(StatusCode::UNPROCESSABLE_ENTITY)
                .header("content-type", "application/json")
                .body(json!({"error": format!("Gagal parse RBXM/RBXMX: {}", e), "supported": false}).to_string().into())?);
        }
    };

    let instances = parse_dom(&dom);

    Ok(Response::builder()
        .status(StatusCode::OK)
        .header("content-type", "application/json")
        .body(json!({ "instances": instances, "count": instances.len() }).to_string().into())?)
}

#[tokio::main]
async fn main() -> Result<(), Error> {
    run(handler).await
}
