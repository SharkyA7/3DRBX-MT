#!/usr/bin/env python3
"""
Draco Spike for Roblox Mesh v7.00 (COREMESH v2 with Draco compression)

This is a SEPARATE SCRIPT - NOT integrated into backend.
Run when v7 mesh file is available (e.g., glasses asset 140676328893231).

Usage:
    python3 tools/draco_spike.py <path_to_v7_mesh_file>

Requirements:
    pip install draco3dgltf  # pure Python/WASM Draco decoder (Vercel-compatible)
    # OR: pip install python-draco  # native C++ bindings (may not work on Vercel)

Reports:
- Decoded attributes (POSITION, NORMAL, TEXCOORD_0, COLOR, TANGENT, etc.)
- Vertex/face counts, bounding box
- Library Vercel compatibility assessment
"""

import sys
import struct
import gzip
from pathlib import Path

try:
    import draco3dgltf
    DRACO_LIB = "draco3dgltf"
except ImportError:
    try:
        import draco
        DRACO_LIB = "python-draco"
    except ImportError:
        DRACO_LIB = None


def read_version_header(data: bytes) -> tuple[str, int]:
    """Read version header line, return (version_string, header_end_pos)."""
    try:
        newline_pos = data.index(b'\n')
        header = data[:newline_pos + 1].decode('utf-8', errors='replace').strip()
        return header, newline_pos + 1
    except ValueError:
        return "", 0


def parse_v7_chunks(data: bytes, pos: int) -> list[dict]:
    """Parse v7 chunked format: [type[8], uint32 chunkVersion, uint32 size, data]"""
    chunks = []
    while pos + 16 <= len(data):
        chunk_type = data[pos:pos+8].rstrip(b'\x00')
        chunk_version = struct.unpack_from("<I", data, pos + 8)[0]
        chunk_size = struct.unpack_from("<I", data, pos + 12)[0]
        chunk_data_start = pos + 16
        chunk_data_end = chunk_data_start + chunk_size

        if chunk_data_end > len(data):
            raise ValueError(f"Chunk {chunk_type.decode('ascii', errors='replace')} size {chunk_size} exceeds remaining data")

        chunks.append({
            'type': chunk_type,
            'version': chunk_version,
            'size': chunk_size,
            'data': data[chunk_data_start:chunk_data_end],
            'offset': pos,
        })
        pos = chunk_data_end
    return chunks


def decode_draco_bitstream(draco_data: bytes):
    """Decode Draco bitstream using available library."""
    if DRACO_LIB == "draco3dgltf":
        # draco3dgltf API: decode_draco(draco_bytes) -> dict with attributes
        return draco3dgltf.decode_draco(draco_data)
    elif DRACO_LIB == "python-draco":
        # python-draco API
        buffer = draco.DecoderBuffer()
        buffer.Init(draco_data, len(draco_data))
        decoder = draco.Decoder()
        mesh = draco.Mesh()
        status = decoder.DecodeMeshFromBuffer(buffer, mesh)
        if not status.ok():
            raise RuntimeError(f"Draco decode failed: {status.error_msg()}")
        return mesh
    else:
        raise RuntimeError("No Draco library available. Install draco3dgltf or python-draco")


def extract_draco_attributes(decoded_mesh):
    """Extract attributes from decoded mesh based on library."""
    attrs = {}
    if DRACO_LIB == "draco3dgltf":
        # draco3dgltf returns dict with attribute arrays
        for name, data in decoded_mesh.items():
            if isinstance(data, (list, tuple)) and len(data) > 0:
                attrs[name] = {
                    'count': len(data),
                    'sample': data[:3] if len(data) >= 3 else data,
                    'dtype': type(data[0]).__name__ if len(data) > 0 else 'unknown',
                }
    elif DRACO_LIB == "python-draco":
        # python-draco mesh object
        for i in range(decoded_mesh.num_attributes()):
            attr = decoded_mesh.attribute(i)
            attrs[f"attr_{i}"] = {
                'name': attr.name(),
                'num_components': attr.num_components(),
                'data_type': attr.data_type(),
                'size': attr.size(),
            }
    return attrs


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 tools/draco_spike.py <path_to_v7_mesh_file>")
        print("\nNo v7 mesh file provided. This script is a template for when you have the glasses file (140676328893231).")
        print("\nTo test: download the v7 mesh asset, save as mesh_v7.raw or mesh_v7.bin, then run:")
        print("  python3 tools/draco_spike.py samples/downloads/mesh_v7.raw")
        print("\nChecking for available Draco libraries...")
        if DRACO_LIB:
            print(f"  ✅ Found: {DRACO_LIB}")
        else:
            print("  ❌ None found. Install with: pip install draco3dgltf")
            print("     (draco3dgltf is pure Python/WASM - Vercel compatible)")
            print("     Alternative: pip install python-draco (native, may not work on Vercel)")
        return 0

    filepath = Path(sys.argv[1])
    if not filepath.exists():
        print(f"File not found: {filepath}")
        return 1

    print(f"=== Draco Spike for v7.00 Mesh ===")
    print(f"File: {filepath} ({filepath.stat().st_size} bytes)")
    print(f"Draco library: {DRACO_LIB or 'NONE - install draco3dgltf'}")

    # Read file (handle gzip)
    raw_data = filepath.read_bytes()
    if raw_data[:2] == b'\x1f\x8b':
        print("  → Detected gzip compression, decompressing...")
        raw_data = gzip.decompress(raw_data)
        print(f"  → Decompressed: {len(raw_data)} bytes")

    # Read version header
    version_header, pos = read_version_header(raw_data)
    print(f"  → Version header: {version_header}")

    if not version_header.startswith("version 7."):
        print(f"  ⚠️  Warning: Expected version 7.x, got: {version_header}")
        print("  Continuing anyway...")

    # Parse chunks
    try:
        chunks = parse_v7_chunks(raw_data, pos)
        print(f"  → Found {len(chunks)} chunks:")
        for chunk in chunks:
            extra = ""
            if chunk['type'] == b'COREMESH' and chunk['version'] == 2:
                extra = " ← COREMESH v2 (Draco)"
            elif chunk['type'] == b'COREMESH' and chunk['version'] == 1:
                extra = " ← COREMESH v1 (uncompressed)"
            print(f"    {chunk['type'].decode('ascii', errors='replace')} v{chunk['version']} size={chunk['size']} offset={chunk['offset']}{extra}")
    except Exception as e:
        print(f"  ❌ Chunk parse error: {e}")
        return 1

    # Find COREMESH v2 chunk
    coremesh_v2 = None
    for chunk in chunks:
        if chunk['type'] == b'COREMESH' and chunk['version'] == 2:
            coremesh_v2 = chunk
            break

    if not coremesh_v2:
        print("  ❌ COREMESH v2 chunk not found - not a v7 Draco mesh")
        return 1

    print(f"\n  → COREMESH v2 chunk found: {coremesh_v2['size']} bytes")

    # Extract Draco bitstream from COREMESH v2
    # Structure: uint32 dracoSize + draco bitstream
    cdata = coremesh_v2['data']
    if len(cdata) < 4:
        print("  ❌ COREMESH v2 chunk too small for dracoSize")
        return 1

    draco_size = struct.unpack_from("<I", cdata, 0)[0]
    draco_bitstream = cdata[4:4+draco_size]

    print(f"  → Draco bitstream size: {draco_size} bytes (available: {len(draco_bitstream)})")

    if len(draco_bitstream) != draco_size:
        print(f"  ⚠️  Warning: dracoSize ({draco_size}) != actual bitstream length ({len(draco_bitstream)})")

    if DRACO_LIB is None:
        print("\n  ❌ No Draco library available. Cannot decode.")
        print("  Install: pip install draco3dgltf")
        return 1

    # Decode Draco
    print(f"\n  → Decoding with {DRACO_LIB}...")
    try:
        decoded = decode_draco_bitstream(draco_bitstream)
        print(f"  ✅ Decode successful!")
    except Exception as e:
        print(f"  ❌ Decode failed: {e}")
        return 1

    # Extract and report attributes
    print(f"\n=== Decoded Attributes ===")
    attrs = extract_draco_attributes(decoded)
    for name, info in attrs.items():
        print(f"  {name}: {info}")

    # Vercel compatibility assessment
    print(f"\n=== Vercel Compatibility ===")
    if DRACO_LIB == "draco3dgltf":
        print("  ✅ draco3dgltf: Pure Python/WASM - FULLY COMPATIBLE with Vercel")
        print("     - No native dependencies")
        print("     - Runs in serverless functions")
        print("     - May have larger bundle size (WASM)")
        print("     - Slower than native but acceptable for mesh decoding")
    elif DRACO_LIB == "python-draco":
        print("  ⚠️  python-draco: Native C++ bindings - MAY NOT WORK on Vercel")
        print("     - Requires native library compilation")
        print("     - Vercel read-only filesystem may prevent loading .so files")
        print("     - Would need to bundle compiled binaries (complex)")
        print("     - Faster decoding if it works")

    # Summary
    print(f"\n=== Summary ===")
    print(f"  File: {filepath.name}")
    print(f"  Version: {version_header}")
    print(f"  COREMESH v2: {coremesh_v2['size']} bytes")
    print(f"  Draco bitstream: {draco_size} bytes")
    print(f"  Decoded attributes: {list(attrs.keys())}")
    print(f"  Library: {DRACO_LIB}")
    print(f"  Vercel ready: {'YES' if DRACO_LIB == 'draco3dgltf' else 'NO (native)'}")

    return 0


if __name__ == "__main__":
    sys.exit(main())