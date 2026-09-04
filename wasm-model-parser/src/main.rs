// src/main.rs — WASI build of the model parser.
//
// Reads raw .rbxm/.rbxmx bytes from stdin, parses with rbx-dom, writes JSON to
// stdout in the EXACT same shape the Vercel-hosted Rust service already returns
// ({"instances": [...], "count": N}) — so the Python side's conversion logic
// (_manifest_from_rust_instances, SurfaceAppearance/Decal/Shirt/Pants resolution,
// the Handle-substring-bug fix) needs ZERO changes. Only how this gets INVOKED is
// different: embedded in-process via `wasmtime` instead of an HTTP call to a
// separate Vercel project.
//
// The parsing logic below (variant_to_json, parse_dom, format detection) is
// copied verbatim from rust-model-parser/api/parse_model.rs, which is already
// confirmed working in production — correctly parsed the Bacon NPC's 201
// instances, resolved SurfaceAppearance/Decal/Shirt/Pants textures, etc. Nothing
// about the ACTUAL parsing was rewritten here, only the entry point.
//
// AUTHORING NOTE: same caveat as always — written without the ability to
// `cargo build` against the real crates in this environment. Unlike the Vercel
// version though, there's no framework-specific API to guess at here (no
// vercel_runtime, no hyper, no tower) — just std::io::stdin/stdout, which is
// about as stable and well-documented as Rust gets. The one build-specific
// unknown is the exact WASI target name (see Cargo.toml comment) — a toolchain
///build-command issue, not a code issue, so it shouldn't require editing this
// file to resolve.

use rbx_dom_weak::WeakDom;
use rbx_dom_weak::types::{Ref, Variant};
use serde::Serialize;
use serde_json::json;
use std::collections::HashMap;
use std::io::{self, Read, Write};

#[derive(Serialize)]
struct FlatInstance {
    referent: String,
    parent_referent: Option<String>,
    class_name: String,
    name: String,
    properties: HashMap<String, serde_json::Value>,
}

/// Identical to the Vercel version's variant_to_json — see that file for the
/// full explanation of the non_exhaustive-match / Debug-fallback approach.
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

fn main() {
    let mut body_bytes = Vec::new();
    if io::stdin().read_to_end(&mut body_bytes).is_err() {
        print!("{}", json!({"error": "gagal membaca stdin"}));
        return;
    }

    if body_bytes.is_empty() {
        print!("{}", json!({"error": "empty input — pipe raw .rbxm/.rbxmx bytes via stdin"}));
        return;
    }

    // Same binary-vs-XML detection as the Vercel version, including the fix for
    // the "<roblox!" (binary) vs "<roblox " (XML) prefix overlap bug found
    // during live testing.
    let is_xml = {
        let trimmed = body_bytes
            .iter()
            .position(|&b| !b.is_ascii_whitespace())
            .map(|i| &body_bytes[i..])
            .unwrap_or(&body_bytes[..]);
        trimmed.starts_with(b"<roblox") && !trimmed.starts_with(b"<roblox!")
    };

    let parse_result: Result<WeakDom, String> = if is_xml {
        rbx_xml::from_reader_default(&body_bytes[..]).map_err(|e| e.to_string())
    } else {
        rbx_binary::from_reader(&body_bytes[..]).map_err(|e| e.to_string())
    };

    let dom = match parse_result {
        Ok(d) => d,
        Err(e) => {
            print!("{}", json!({"error": format!("Gagal parse RBXM/RBXMX: {}", e), "supported": false}));
            return;
        }
    };

    let instances = parse_dom(&dom);
    let output = json!({ "instances": instances, "count": instances.len() });
    let _ = io::stdout().write_all(output.to_string().as_bytes());
}
