#!/usr/bin/env python3
"""
RBXM Binary Format Dump Tool
============================
Reads an .rbxm file from stdin or argument and parses:
1. Header: magic, version, num_types, num_instances
2. All chunks: name, compressed_size, uncompressed_size
3. SSTR chunk: version, num_entries, then for each entry: index, md5_hash, length, first 32 bytes (hex + utf8 preview)
4. INST chunks: for each type_id: class_name, count, referents
5. PROP chunks for MeshPart, Part, UnionOperation, SurfaceAppearance, Decal:
   - For each property: type_id, prop_name, dtype (byte), raw bytes (first 64 hex)
   - Decoded value using existing decode functions
   - Special focus: TextureID, MeshId, AssetId, ColorMap, NormalMap, MetalnessMap, RoughnessMap, Texture (Decal), ShirtTemplate, PantsTemplate
6. For each MeshPart/Part/UnionOperation instance, print properties in a readable table

Usage: python3 tools/rbxm_dump.py < sample.rbxm
       python3 tools/rbxm_dump.py sample.rbxm
"""

import sys
import struct
import lz4.block
import json
from typing import List, Tuple, Dict, Any, Optional, Union
from io import BytesIO

# ─── Property Type Constants (per rbx-dom binary format spec) ───
PROP_TYPE_MAP = {
    0x01: "String",
    0x02: "Bool",
    0x03: "Int",
    0x04: "Float",
    0x05: "Double",
    0x06: "UDim",
    0x07: "UDim2",
    0x08: "Ray",
    0x09: "Faces",
    0x0A: "Axes",
    0x0B: "BrickColor",
    0x0C: "Color3",
    0x0D: "Vector2",
    0x0E: "Vector3",
    0x0F: "Vector2int16",
    0x10: "Vector3int16",
    0x11: "NumberSequence",
    0x12: "ColorSequence",
    0x13: "NumberRange",
    0x14: "Rect",
    0x15: "PhysicalProperties",
    0x16: "Color3uint8",
    0x17: "Int64",
    0x18: "SharedString",  # 0x18 in spec, but code uses 0x1C
    0x19: "OptionalCoordinateFrame",  # CFrame
    0x1A: "UniqueId",
    0x1B: "Font",
    0x1C: "SharedString",  # As used in this codebase
}

PROP_TYPE_STRING = 0x01
PROP_TYPE_SHARED_STRING = 0x1C

# ─── Target Classes for Detailed Property Dump ───
TARGET_CLASSES = {
    "MeshPart", "Part", "UnionOperation", "SurfaceAppearance", "Decal",
    "Texture", "Shirt", "Pants", "ShirtGraphic", "WrapLayer", "WrapTarget",
    "Model"
}

# ─── Special Property Names to Highlight ───
SPECIAL_PROPERTIES = {
    "TextureID", "MeshId", "AssetId", "ColorMap", "NormalMap",
    "MetalnessMap", "RoughnessMap", "Texture", "ShirtTemplate", "PantsTemplate",
    "Name", "CageMeshId", "ReferenceMeshId", "Size", "CFrame", "Position",
    "Color", "Color3", "Transparency", "Reflectance", "Material", "MaterialVariant"
}

# ═══════════════════════════════════════════════════════════════════════════════
# CORE PARSING FUNCTIONS (adapted from backend/main.py and backend/services/rbxm_parser.py)
# ═══════════════════════════════════════════════════════════════════════════════

class RBXMParseError(Exception):
    pass


def parse_chunks(data: bytes):
    """Parse RBXM binary format into chunks. Returns (chunks, num_types, num_instances)."""
    if data[:8] != b"<roblox!":
        raise RBXMParseError("Not a binary RBXM format (maybe XML/rbxmx or different asset type)")
    num_types = struct.unpack("<I", data[16:20])[0]
    num_instances = struct.unpack("<I", data[20:24])[0]

    offset = 32
    chunks = []
    while offset < len(data):
        name = data[offset:offset+4].rstrip(b"\x00").decode("ascii", "replace")
        cl = struct.unpack("<I", data[offset+4:offset+8])[0]
        ul = struct.unpack("<I", data[offset+8:offset+12])[0]
        bs = offset + 16
        if cl == 0:
            body = data[bs:bs+ul]
            be = bs + ul
        else:
            body = lz4.block.decompress(data[bs:bs+cl], uncompressed_size=ul)
            be = bs + cl
        chunks.append((name, body, cl, ul))  # Include compressed/uncompressed sizes
        offset = be
        if name == "END":
            break
    return chunks, num_types, num_instances


def _read_interleaved_be_u32(buf: bytes, count: int) -> List[int]:
    out = []
    for i in range(count):
        b0, b1, b2, b3 = buf[i], buf[count+i], buf[2*count+i], buf[3*count+i]
        out.append((b0 << 24) | (b1 << 16) | (b2 << 8) | b3)
    return out


def _ror1(u32: int) -> int:
    return (u32 >> 1) | ((u32 & 1) << 31)


def decode_f32_array(buf: bytes, count: int) -> List[float]:
    """VERIFIED: interleaved byte-plane transpose + ROR1 bit rotation -> BE float32."""
    raw = _read_interleaved_be_u32(buf, count)
    return [struct.unpack(">f", struct.pack(">I", _ror1(v)))[0] for v in raw]


def read_referent_array(buf: bytes, count: int) -> List[int]:
    """VERIFIED: used for INST instance ID lists."""
    raw = _read_interleaved_be_u32(buf, count)
    out = []
    last = 0
    for v in raw:
        sv = v if v < 2**31 else v - 2**32
        last = (last + sv) & 0xFFFFFFFF
        out.append(last if last < 2**31 else last - 2**32)
    return out


def parse_inst_chunks(chunks: List[Tuple]) -> Dict[int, Dict]:
    """VERIFIED. Returns {type_id: {class_name, count, referents, is_service}}"""
    type_map = {}
    for name, body, *_ in chunks:
        if name != "INST":
            continue
        pos = 0
        type_id = struct.unpack("<I", body[pos:pos+4])[0]; pos += 4
        nl = struct.unpack("<I", body[pos:pos+4])[0]; pos += 4
        class_name = body[pos:pos+nl].decode("utf-8"); pos += nl
        is_service = body[pos]; pos += 1
        n = struct.unpack("<I", body[pos:pos+4])[0]; pos += 4
        referents = []
        if n > 0:
            referents = read_referent_array(body[pos:pos+n*4], n)
        type_map[type_id] = {
            "class_name": class_name,
            "count": n,
            "referents": referents,
            "is_service": bool(is_service)
        }
    return type_map


def find_prop_chunk(chunks: List[Tuple], type_id: int, prop_name: str) -> Tuple[Optional[int], Optional[bytes]]:
    for name, body, *_ in chunks:
        if name != "PROP":
            continue
        pos = 0
        tid = struct.unpack("<I", body[pos:pos+4])[0]; pos += 4
        nl = struct.unpack("<I", body[pos:pos+4])[0]; pos += 4
        pname = body[pos:pos+nl].decode("utf-8", "replace"); pos += nl
        dtype = body[pos]; pos += 1
        if tid == type_id and pname == prop_name:
            return dtype, body[pos:]
    return None, None


def decode_string_array(raw: bytes, count: int) -> List[str]:
    """VERIFIED: sequential length-prefixed UTF8."""
    pos = 0
    out = []
    for _ in range(count):
        if pos + 4 > len(raw):
            out.append("")
            continue
        slen = struct.unpack("<I", raw[pos:pos+4])[0]; pos += 4
        if pos + slen > len(raw):
            out.append("")
            pos += slen if pos + slen <= len(raw) else len(raw) - pos
            continue
        out.append(raw[pos:pos+slen].decode("utf-8", "replace"))
        pos += slen
    return out


def decode_vector3_array(raw: bytes, count: int) -> List[Tuple[float, float, float]]:
    """VERIFIED: 3x interleaved+ROR1 f32 arrays -> (x,y,z) tuples."""
    plane = count * 4
    xs = decode_f32_array(raw[0:plane], count)
    ys = decode_f32_array(raw[plane:plane*2], count)
    zs = decode_f32_array(raw[plane*2:plane*3], count)
    return list(zip(xs, ys, zs))


def decode_cframe_positions(raw: bytes, count: int) -> Tuple[List[Tuple[float, float, float]], List[int]]:
    """Position component VERIFIED. Returns (positions, rotation_ids)."""
    rotation_ids = list(raw[:count])
    pos_block = raw[-(count * 12):]
    positions = decode_vector3_array(pos_block, count)
    return positions, rotation_ids


def decode_color3uint8_array(raw: bytes, count: int) -> List[Tuple[int, int, int]]:
    """UNVERIFIED layout - best-effort sequential RGB triplets."""
    out = []
    for i in range(count):
        if i*3+2 < len(raw):
            out.append((raw[i*3], raw[i*3+1], raw[i*3+2]))
        else:
            out.append((163, 162, 165))
    return out


def decode_bool_array(raw: bytes, count: int) -> List[bool]:
    return [bool(b) for b in raw[:count]]


def parse_shared_strings(chunks: List[Tuple]) -> Tuple[List[bytes], int, int]:
    """
    Parse SSTR chunk. Returns (entries, version, num_entries).
    Each entry is raw bytes of the string.
    """
    for name, body, *_ in chunks:
        if name != "SSTR":
            continue
        try:
            pos = 0
            version = struct.unpack("<I", body[pos:pos+4])[0]; pos += 4
            num_entries = struct.unpack("<I", body[pos:pos+4])[0]; pos += 4
            entries = []
            for i in range(num_entries):
                md5_hash = body[pos:pos+16]; pos += 16
                length = struct.unpack("<I", body[pos:pos+4])[0]; pos += 4
                data = body[pos:pos+length]; pos += length
                entries.append((md5_hash, data))
            return entries, version, num_entries
        except Exception:
            return [], 0, 0
    return [], 0, 0


def decode_shared_string_array(raw: bytes, count: int, sstrings: List[bytes]) -> List[str]:
    """Decode shared string array using interleaved u32 indices.
    Returns 'sstr#N' format (index reference) to match reference decoder behavior."""
    try:
        idxs = _read_interleaved_be_u32(raw, count)
    except Exception:
        return ["sstr#-1"] * count
    return [f"sstr#{idx}" for idx in idxs]


def decode_string_like_prop(chunks: List[Tuple], type_id: int, prop_name: str, count: int, sstrings: List[bytes]) -> Tuple[List[Optional[str]], Optional[int]]:
    """dtype-aware replacement for blindly calling decode_string_array()."""
    dtype, raw = find_prop_chunk(chunks, type_id, prop_name)
    if raw is None:
        return [None] * count, dtype
    if dtype == PROP_TYPE_STRING:
        vals = decode_string_array(raw, count)
    elif dtype == PROP_TYPE_SHARED_STRING:
        vals = decode_shared_string_array(raw, count, sstrings)
    else:
        vals = [None] * count
    return [v if v else None for v in vals], dtype


# ─── Additional Property Decoders ───

def decode_int_array(raw: bytes, count: int) -> List[int]:
    """Decode interleaved int32 array."""
    raw_u32 = _read_interleaved_be_u32(raw, count)
    return [v if v < 2**31 else v - 2**32 for v in raw_u32]


def decode_float_array(raw: bytes, count: int) -> List[float]:
    """Decode interleaved float32 array."""
    return decode_f32_array(raw, count)


def decode_double_array(raw: bytes, count: int) -> List[float]:
    """Decode interleaved double (float64) array."""
    out = []
    # Doubles are not interleaved in the same way - they're sequential
    for i in range(count):
        if i*8+7 < len(raw):
            out.append(struct.unpack(">d", raw[i*8:(i+1)*8])[0])
        else:
            out.append(0.0)
    return out


def decode_brickcolor_array(raw: bytes, count: int) -> List[int]:
    """BrickColor is just an int32."""
    return decode_int_array(raw, count)


def decode_udim_array(raw: bytes, count: int) -> List[Tuple[float, int]]:
    """UDim = Scale (float32) + Offset (int32)"""
    scales = decode_f32_array(raw[:count*4], count)
    offsets = decode_int_array(raw[count*4:count*8], count)
    return list(zip(scales, offsets))


def decode_udim2_array(raw: bytes, count: int) -> List[Tuple[float, int, float, int]]:
    """UDim2 = X.Scale, X.Offset, Y.Scale, Y.Offset"""
    plane = count * 4
    xs = decode_f32_array(raw[0:plane], count)
    xo = decode_int_array(raw[plane:plane*2], count)
    ys = decode_f32_array(raw[plane*2:plane*3], count)
    yo = decode_int_array(raw[plane*3:plane*4], count)
    return list(zip(xs, xo, ys, yo))


def decode_vector2_array(raw: bytes, count: int) -> List[Tuple[float, float]]:
    """Vector2 = X, Y (both float32)"""
    plane = count * 4
    xs = decode_f32_array(raw[0:plane], count)
    ys = decode_f32_array(raw[plane:plane*2], count)
    return list(zip(xs, ys))


def decode_vector2int16_array(raw: bytes, count: int) -> List[Tuple[int, int]]:
    """Vector2int16 = X, Y (both int16)"""
    out = []
    for i in range(count):
        if i*4+3 < len(raw):
            x = struct.unpack("<h", raw[i*4:i*4+2])[0]
            y = struct.unpack("<h", raw[i*4+2:i*4+4])[0]
            out.append((x, y))
        else:
            out.append((0, 0))
    return out


def decode_vector3int16_array(raw: bytes, count: int) -> List[Tuple[int, int, int]]:
    """Vector3int16 = X, Y, Z (all int16)"""
    out = []
    for i in range(count):
        if i*6+5 < len(raw):
            x = struct.unpack("<h", raw[i*6:i*6+2])[0]
            y = struct.unpack("<h", raw[i*6+2:i*6+4])[0]
            z = struct.unpack("<h", raw[i*6+4:i*6+6])[0]
            out.append((x, y, z))
        else:
            out.append((0, 0, 0))
    return out


def decode_color3_array(raw: bytes, count: int) -> List[Tuple[float, float, float]]:
    """Color3 = R, G, B (all float32) - same as Vector3"""
    return decode_vector3_array(raw, count)


def decode_cframe_array(raw: bytes, count: int) -> List[Dict]:
    """Decode CFrame array - returns position + rotation_id for each."""
    positions, rotation_ids = decode_cframe_positions(raw, count)
    out = []
    for i in range(count):
        out.append({
            "position": {"x": positions[i][0], "y": positions[i][1], "z": positions[i][2]},
            "rotation_id": rotation_ids[i],
            "rotation_name": "Custom" if rotation_ids[i] == 0 else f"ID_{rotation_ids[i]:02X}"
        })
    return out


def decode_uniqueid_array(raw: bytes, count: int) -> List[str]:
    """UniqueId = 16 bytes random + 8 bytes timestamp (stored as interleaved?)"""
    out = []
    # UniqueId is typically stored as sequential 24-byte blobs
    for i in range(count):
        if i*24+23 < len(raw):
            data = raw[i*24:(i+1)*24]
            # Format: 16 random bytes + 8 timestamp (big-endian)
            random_part = data[:16].hex()
            timestamp = struct.unpack(">Q", data[16:24])[0]
            out.append(f"{random_part}-{timestamp}")
        else:
            out.append("")
    return out


def decode_property_value(dtype: int, raw: bytes, count: int, sstrings: List[bytes]) -> Any:
    """Decode a property value based on its type."""
    try:
        if dtype == PROP_TYPE_STRING:
            return decode_string_array(raw, count)
        elif dtype == PROP_TYPE_SHARED_STRING:
            return decode_shared_string_array(raw, count, sstrings)
        elif dtype == 0x02:  # Bool
            return decode_bool_array(raw, count)
        elif dtype == 0x03:  # Int
            return decode_int_array(raw, count)
        elif dtype == 0x04:  # Float
            return decode_float_array(raw, count)
        elif dtype == 0x05:  # Double
            return decode_double_array(raw, count)
        elif dtype == 0x06:  # UDim
            return decode_udim_array(raw, count)
        elif dtype == 0x07:  # UDim2
            return decode_udim2_array(raw, count)
        elif dtype == 0x0B:  # BrickColor
            return decode_brickcolor_array(raw, count)
        elif dtype == 0x0C:  # Color3
            return decode_color3_array(raw, count)
        elif dtype == 0x0D:  # Vector2
            return decode_vector2_array(raw, count)
        elif dtype == 0x0E:  # Vector3
            return decode_vector3_array(raw, count)
        elif dtype == 0x0F:  # Vector2int16
            return decode_vector2int16_array(raw, count)
        elif dtype == 0x10:  # Vector3int16
            return decode_vector3int16_array(raw, count)
        elif dtype == 0x16:  # Color3uint8
            return decode_color3uint8_array(raw, count)
        elif dtype == 0x19:  # OptionalCoordinateFrame (CFrame)
            return decode_cframe_array(raw, count)
        elif dtype == 0x1A:  # UniqueId
            return decode_uniqueid_array(raw, count)
        else:
            return {"_undecoded": True, "dtype": dtype, "raw_hex": raw[:64].hex()}
    except Exception as e:
        return {"_error": str(e), "dtype": dtype, "raw_hex": raw[:64].hex()}


# ─── Formatting Helpers ───

def format_bytes_preview(data: bytes, max_len: int = 32) -> str:
    """Format bytes as hex + utf8 preview."""
    hex_str = data[:max_len].hex()
    try:
        utf8_str = data[:max_len].decode("utf-8", "replace")
        # Only show printable chars
        utf8_preview = ''.join(c if 32 <= ord(c) < 127 else '.' for c in utf8_str)
        return f"{hex_str} | {utf8_preview}"
    except Exception:
        return hex_str


def format_value_preview(val: Any, max_items: int = 5) -> str:
    """Format a decoded value for display."""
    if isinstance(val, list):
        if not val:
            return "[]"
        if len(val) <= max_items:
            return str(val)
        return f"[{', '.join(str(v) for v in val[:max_items])}, ... ({len(val)} total)]"
    return str(val)


def print_header(title: str):
    print(f"\n{'='*80}")
    print(f" {title}")
    print(f"{'='*80}")


def print_subheader(title: str):
    print(f"\n{'─'*80}")
    print(f" {title}")
    print(f"{'─'*80}")


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN DUMP FUNCTION
# ═══════════════════════════════════════════════════════════════════════════════

def dump_rbxm(data: bytes) -> Dict[str, Any]:
    """Main dump function. Returns structured data and prints human-readable summary."""
    result = {}

    # ─── 1. Parse Header & Chunks ───
    chunks, num_types, num_instances = parse_chunks(data)

    print_header("RBXM HEADER")
    print(f"  Magic:       <roblox!")
    print(f"  Num Types:   {num_types}")
    print(f"  Num Instances: {num_instances}")
    print(f"  Total Chunks:  {len(chunks)}")

    result["header"] = {
        "magic": "<roblox!",
        "num_types": num_types,
        "num_instances": num_instances
    }

    # ─── 2. List All Chunks ───
    print_header("CHUNKS")
    print(f"  {'Name':<8} {'Compressed':>12} {'Uncompressed':>12} {'Ratio':>8}")
    print(f"  {'-'*8} {'-'*12} {'-'*12} {'-'*8}")

    chunks_data = []
    for name, body, cl, ul in chunks:
        ratio = f"{cl/ul*100:.1f}%" if ul > 0 and cl > 0 else "N/A"
        if cl == 0:
            ratio = "raw"
        print(f"  {name:<8} {cl:>12} {ul:>12} {ratio:>8}")
        chunks_data.append({
            "name": name,
            "compressed_size": cl,
            "uncompressed_size": ul,
            "body_length": len(body)
        })

    result["chunks"] = chunks_data

    # ─── 3. Parse SSTR Chunk ───
    print_header("SSTR CHUNK (Shared Strings)")
    sstr_entries, sstr_version, sstr_num_entries = parse_shared_strings(chunks)
    sstrings = [entry[1] for entry in sstr_entries]

    if sstr_entries:
        print(f"  Version:      {sstr_version}")
        print(f"  Num Entries:  {sstr_num_entries}")
        print(f"  Entries:")
        sstr_data = []
        for idx, (md5_hash, entry_data) in enumerate(sstr_entries):
            preview = format_bytes_preview(entry_data)
            print(f"    [{idx:4d}] MD5: {md5_hash.hex()} | Len: {len(entry_data):5d} | {preview}")
            sstr_data.append({
                "index": idx,
                "md5_hash": md5_hash.hex(),
                "length": len(entry_data),
                "preview_hex_utf8": preview
            })
        result["sstr"] = {
            "version": sstr_version,
            "num_entries": sstr_num_entries,
            "entries": sstr_data
        }
    else:
        print("  (No SSTR chunk found)")
        result["sstr"] = {"version": 0, "num_entries": 0, "entries": []}

    # ─── 4. Parse INST Chunks ───
    print_header("INST CHUNKS (Class Map)")
    type_map = parse_inst_chunks(chunks)

    inst_data = []
    for type_id, info in sorted(type_map.items()):
        print(f"  TypeID: {type_id:5d} | Class: {info['class_name']:<25} | Count: {info['count']:4d} | Service: {info['is_service']}")
        if info['referents']:
            refs_str = ', '.join(str(r) for r in info['referents'][:10])
            if len(info['referents']) > 10:
                refs_str += f", ... ({len(info['referents'])} total)"
            print(f"    Referents: [{refs_str}]")
        inst_data.append({
            "type_id": type_id,
            "class_name": info['class_name'],
            "count": info['count'],
            "referents": info['referents'],
            "is_service": info['is_service']
        })

    result["inst"] = inst_data

    # ─── 5. Parse PROP Chunks for Target Classes ───
    print_header("PROP CHUNKS (Target Classes)")

    # Find type_ids for target classes
    target_type_ids = {}
    for type_id, info in type_map.items():
        if info['class_name'] in TARGET_CLASSES:
            target_type_ids[type_id] = info['class_name']

    if not target_type_ids:
        print("  (No target classes found in this file)")
        result["props"] = []
    else:
        props_data = []
        # Collect all PROP chunks for target type_ids
        for name, body, *_ in chunks:
            if name != "PROP":
                continue
            pos = 0
            tid = struct.unpack("<I", body[pos:pos+4])[0]; pos += 4
            if tid not in target_type_ids:
                continue
            class_name = target_type_ids[tid]
            nl = struct.unpack("<I", body[pos:pos+4])[0]; pos += 4
            pname = body[pos:pos+nl].decode("utf-8", "replace"); pos += nl
            dtype = body[pos]; pos += 1
            raw = body[pos:]

            count = type_map[tid]['count']
            decoded = decode_property_value(dtype, raw, count, sstrings)
            dtype_name = PROP_TYPE_MAP.get(dtype, f"Unknown(0x{dtype:02X})")
            is_special = pname in SPECIAL_PROPERTIES

            prop_entry = {
                "type_id": tid,
                "class_name": class_name,
                "prop_name": pname,
                "dtype": dtype,
                "dtype_name": dtype_name,
                "count": count,
                "raw_hex_first_64": raw[:64].hex(),
                "decoded": decoded,
                "is_special": is_special
            }
            props_data.append(prop_entry)

            # Print
            special_marker = " ★" if is_special else ""
            print(f"  TypeID: {tid:5d} ({class_name}) | {pname:<25} | dtype: 0x{dtype:02X} ({dtype_name}){special_marker}")
            print(f"    Raw (first 64): {raw[:64].hex()}")
            print(f"    Decoded: {format_value_preview(decoded)}")

        result["props"] = props_data

    # ─── 6. Build Instance Property Tables for MeshPart/Part/UnionOperation ───
    print_header("INSTANCE PROPERTY TABLES (MeshPart / Part / UnionOperation)")

    # Target instance classes for detailed table
    table_classes = {"MeshPart", "Part", "UnionOperation"}
    table_type_ids = {tid: info for tid, info in type_map.items() if info['class_name'] in table_classes}

    table_data = []
    for type_id, info in sorted(table_type_ids.items()):
        class_name = info['class_name']
        count = info['count']
        referents = info['referents']

        print(f"\n  ┌─ {class_name} (TypeID: {type_id}, Count: {count}) ─┐")

        # Collect all properties for this type_id
        instance_props = {}
        for name, body, *_ in chunks:
            if name != "PROP":
                continue
            pos = 0
            tid = struct.unpack("<I", body[pos:pos+4])[0]; pos += 4
            if tid != type_id:
                continue
            nl = struct.unpack("<I", body[pos:pos+4])[0]; pos += 4
            pname = body[pos:pos+nl].decode("utf-8", "replace"); pos += nl
            dtype = body[pos]; pos += 1
            raw = body[pos:]

            decoded = decode_property_value(dtype, raw, count, sstrings)
            dtype_name = PROP_TYPE_MAP.get(dtype, f"Unknown(0x{dtype:02X})")
            instance_props[pname] = {
                "dtype": dtype,
                "dtype_name": dtype_name,
                "raw_hex_first_64": raw[:64].hex(),
                "decoded": decoded
            }

        # Print as table
        if instance_props:
            # Determine column widths
            max_name_len = max(len(k) for k in instance_props.keys())
            max_name_len = max(max_name_len, len("Property"))
            max_type_len = max(len(v['dtype_name']) for v in instance_props.values())
            max_type_len = max(max_type_len, len("Type"))

            print(f"  │ {'Property'.ljust(max_name_len)} │ {'Type'.ljust(max_type_len)} │ Value │")
            print(f"  │ {'─'*max_name_len} │ {'─'*max_type_len} │ {'─'*50} │")

            for pname, pinfo in sorted(instance_props.items()):
                decoded = pinfo['decoded']
                val_str = format_value_preview(decoded, max_items=3)
                # Truncate long values
                if len(val_str) > 50:
                    val_str = val_str[:47] + "..."
                print(f"  │ {pname.ljust(max_name_len)} │ {pinfo['dtype_name'].ljust(max_type_len)} │ {val_str} │")

                table_data.append({
                    "type_id": type_id,
                    "class_name": class_name,
                    "referents": referents,
                    "property": pname,
                    "dtype": pinfo['dtype'],
                    "dtype_name": pinfo['dtype_name'],
                    "raw_hex_first_64": pinfo['raw_hex_first_64'],
                    "decoded": decoded
                })
        else:
            print(f"  │ (no properties found) │")

    result["instance_tables"] = table_data

    # ─── Summary ───
    print_header("SUMMARY")
    print(f"  File size:           {len(data)} bytes")
    print(f"  Chunks parsed:       {len(chunks)}")
    print(f"  Classes found:       {len(type_map)}")
    print(f"  Target classes:      {len(target_type_ids)}")
    print(f"  Shared strings:      {len(sstrings)}")
    print(f"  Properties dumped:   {len(props_data) if 'props_data' in locals() else 0}")

    result["summary"] = {
        "file_size": len(data),
        "chunks_parsed": len(chunks),
        "classes_found": len(type_map),
        "target_classes": len(target_type_ids),
        "shared_strings": len(sstrings),
        "properties_dumped": len(props_data) if 'props_data' in locals() else 0
    }

    return result


# ═══════════════════════════════════════════════════════════════════════════════
# CLI ENTRY POINT
# ═══════════════════════════════════════════════════════════════════════════════

def main():
    # Read from stdin or file argument
    if len(sys.argv) > 1:
        input_path = sys.argv[1]
        if input_path == "-":
            data = sys.stdin.buffer.read()
        else:
            with open(input_path, "rb") as f:
                data = f.read()
    else:
        data = sys.stdin.buffer.read()

    if not data:
        print("Error: No input data provided", file=sys.stderr)
        print("Usage: python3 tools/rbxm_dump.py < sample.rbxm", file=sys.stderr)
        print("       python3 tools/rbxm_dump.py sample.rbxm", file=sys.stderr)
        sys.exit(1)

    try:
        result = dump_rbxm(data)

        # Optionally output JSON to a separate file or stdout with a flag
        if "--json" in sys.argv:
            json_output = json.dumps(result, indent=2, default=str)
            # Print JSON to stderr so it doesn't mix with human-readable output
            print(json_output, file=sys.stderr)

    except RBXMParseError as e:
        print(f"Parse Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Unexpected Error: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()