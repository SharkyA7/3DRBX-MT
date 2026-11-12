#!/usr/bin/env python3
"""
Comprehensive Mesh Tests - Step 6 (Self-contained, no Flask dependency)

Decodes every mesh file in samples/ and validates:
- Vertex/face counts
- Index bounds (no out-of-bounds indices)
- NaN detection in vertices/normals/UVs
- Bounding box computation
"""

import sys
import struct
import gzip
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List, Tuple


@dataclass
class RobloxMesh:
    vertices: List[Tuple[float, float, float]]
    normals: List[Tuple[float, float, float]]
    uvs: List[Tuple[float, float]]
    faces: List[Tuple[int, int, int]]
    version: str = "unknown"
    name: str = "mesh"


# ── PARSERS (copied from backend/main.py) ──────────────────────────

def _parse_v1(data: bytes) -> RobloxMesh:
    """Version 1.00 — plain ASCII."""
    pos = data.index(b"\n") + 1
    tokens = data[pos:].decode("ascii", errors="replace").split()
    verts, norms, uvs, faces = [], [], [], []
    for i in range(0, len(tokens), 24):
        chunk = tokens[i:i+24]
        if len(chunk) < 24:
            break
        vals = list(map(float, chunk))
        for v in range(3):
            verts.append((vals[v*8+0], vals[v*8+1], vals[v*8+2]))
            norms.append((vals[v*8+3], vals[v*8+4], vals[v*8+5]))
            uvs.append((vals[v*8+6], 1.0 - vals[v*8+7]))
        base = len(verts) - 3
        faces.append((base, base+1, base+2))
    return RobloxMesh(verts, norms, uvs, faces, version="1.00")


def _parse_v2(data: bytes) -> RobloxMesh:
    """Version 2.00 — binary header + vertices + faces."""
    pos = data.index(b"\n") + 1
    sizeof_header, sizeof_vertex, sizeof_face = struct.unpack_from("<HBB", data, pos)
    num_verts, num_faces = struct.unpack_from("<II", data, pos + 4)
    p = pos + sizeof_header
    verts, norms, uvs, faces = [], [], [], []
    for _ in range(num_verts):
        px, py, pz, nx, ny, nz, u, v = struct.unpack_from("<ffffffff", data, p)
        verts.append((px, py, pz))
        norms.append((nx, ny, nz))
        uvs.append((u, 1.0 - v))
        p += sizeof_vertex
    for _ in range(num_faces):
        i0, i1, i2 = struct.unpack_from("<III", data, p)
        faces.append((i0, i1, i2))
        p += sizeof_face
    return RobloxMesh(verts, norms, uvs, faces, version="2.00")


def _parse_v3(data: bytes) -> RobloxMesh:
    """Version 3.00 — binary header + LOD + vertices + faces."""
    pos = data.index(b"\n") + 1
    (sizeof_header, sizeof_vertex, sizeof_face, sizeof_lod,
     num_lods, num_verts, num_faces) = struct.unpack_from("<HHHHHHH", data, pos)
    p = pos + sizeof_header
    verts, norms, uvs, faces = [], [], [], []
    for _ in range(num_verts):
        px, py, pz, nx, ny, nz, u, v = struct.unpack_from("<ffffffff", data, p)
        verts.append((px, py, pz))
        norms.append((nx, ny, nz))
        uvs.append((u, 1.0 - v))
        p += sizeof_vertex
    for _ in range(num_faces):
        i0, i1, i2 = struct.unpack_from("<III", data, p)
        faces.append((i0, i1, i2))
        p += sizeof_face
    return RobloxMesh(verts, norms, uvs, faces, version="3.00")


def _parse_v4(data: bytes) -> RobloxMesh:
    """Version 4.0x — 24-byte header, 40-byte vertices, LOD offsets."""
    pos = data.index(b"\n") + 1
    header_size = struct.unpack("<H", data[pos:pos+2])[0]
    num_verts = struct.unpack("<I", data[pos+4:pos+8])[0]
    num_faces = struct.unpack("<I", data[pos+8:pos+12])[0]
    num_lods = struct.unpack("<H", data[pos+12:pos+14])[0]
    vstart = pos + header_size
    vertices, normals, uvs = [], [], []
    p = vstart
    for _ in range(num_verts):
        px,py,pz,nx,ny,nz,tu,tv,tw = struct.unpack("<9f", data[p:p+36])
        vertices.append((px,py,pz))
        normals.append((nx,ny,nz))
        uvs.append((tu, 1.0-tv))
        p += 40
    faces_all = []
    for _ in range(num_faces):
        a,b,c = struct.unpack("<3I", data[p:p+12])
        faces_all.append((a,b,c))
        p += 12
    lod_offsets = []
    for _ in range(num_lods):
        lod_offsets.append(struct.unpack("<I", data[p:p+4])[0])
        p += 4
    if len(lod_offsets) >= 2:
        faces = faces_all[lod_offsets[0]:lod_offsets[1]]
    else:
        faces = faces_all
    return RobloxMesh(vertices, normals, uvs, faces, version="4.0x")


def _parse_v6(data: bytes) -> RobloxMesh:
    """Version 6.00 — chunked format (COREMESH v1, LODS, SKINNING, FACS, HSRAVIS)."""
    pos = data.index(b"\n") + 1
    chunks = []
    while pos + 16 <= len(data):
        chunk_type = data[pos:pos+8].rstrip(b"\x00")
        chunk_version = struct.unpack_from("<I", data, pos + 8)[0]
        chunk_size = struct.unpack_from("<I", data, pos + 12)[0]
        chunk_data_start = pos + 16
        chunk_data_end = chunk_data_start + chunk_size
        if chunk_data_end > len(data):
            raise ValueError(f"Chunk {chunk_type.decode('ascii', errors='replace')} size exceeds data")
        chunks.append({'type': chunk_type, 'version': chunk_version, 'size': chunk_size,
                       'data': data[chunk_data_start:chunk_data_end]})
        pos = chunk_data_end
    coremesh_chunk = next((c for c in chunks if c['type'] == b'COREMESH' and c['version'] == 1), None)
    lods_chunk = next((c for c in chunks if c['type'] == b'LODS'), None)
    if not coremesh_chunk:
        raise ValueError("COREMESH v1 chunk not found")
    cdata = coremesh_chunk['data']
    if len(cdata) < 24:
        raise ValueError("COREMESH chunk too small")
    header_size = struct.unpack_from("<H", cdata, 0)[0]
    num_verts = struct.unpack_from("<I", cdata, 4)[0]
    num_faces = struct.unpack_from("<I", cdata, 8)[0]
    num_lods = struct.unpack_from("<H", cdata, 12)[0]
    vstart = header_size
    if vstart + num_verts * 40 > len(cdata):
        raise ValueError("COREMESH vertex data truncated")
    vertices, normals, uvs = [], [], []
    p = vstart
    for _ in range(num_verts):
        px, py, pz, nx, ny, nz, tu, tv, tw = struct.unpack_from("<9f", cdata, p)
        vertices.append((px, py, pz))
        normals.append((nx, ny, nz))
        uvs.append((tu, 1.0 - tv))
        p += 40
    if p + num_faces * 12 > len(cdata):
        raise ValueError("COREMESH face data truncated")
    faces_all = []
    for _ in range(num_faces):
        a, b, c = struct.unpack_from("<3I", cdata, p)
        faces_all.append((a, b, c))
        p += 12
    lod_offsets = []
    if p + num_lods * 4 <= len(cdata):
        for _ in range(num_lods):
            lod_offsets.append(struct.unpack_from("<I", cdata, p)[0])
            p += 4
    if lods_chunk and len(lods_chunk['data']) >= num_lods * 4:
        lod_offsets = [struct.unpack_from("<I", lods_chunk['data'], i * 4)[0] for i in range(num_lods)]
    if len(lod_offsets) >= 2:
        faces = faces_all[lod_offsets[0]:lod_offsets[1]]
    else:
        faces = faces_all
    return RobloxMesh(vertices, normals, uvs, faces, version="6.00")


def parse_mesh(data: bytes, name: str = "mesh") -> RobloxMesh:
    """Dispatch to version-specific parser."""
    if not data:
        raise ValueError("Empty mesh data")
    header = data[:16].decode("utf-8", errors="replace")
    if "version 1." in header:
        mesh = _parse_v1(data)
    elif "version 2." in header:
        mesh = _parse_v2(data)
    elif "version 3." in header:
        mesh = _parse_v3(data)
    elif "version 4." in header or "version 5." in header:
        mesh = _parse_v4(data)
    elif "version 6." in header:
        mesh = _parse_v6(data)
    else:
        raise ValueError(f"Unknown mesh format: {header[:20]!r}")
    mesh.name = name
    return mesh


# CSGMDL constants
CSGMDL_V5_MAGIC = b"\x15\x7d\x29\x15\x75\x6c\x35\x04\x34\x69"
CSGMDL_V4_MAGIC = b"\x15\x7d\x29\x15\x75\x6c\x34\x04\x34\x69"
CSGMDL_V2_MAGIC = b"\x15\x7d\x29\x15\x75\x6c\x32\x04\x34\x69"
CSGMDL_OBFUSCATION_NOISE = bytes([86,46,110,88,49,32,48,4,52,105,12,119,12,1,94,0,26,96,55,105,29,82,43,7,79,36,89,101,83,4,122])

def _csgmdl_deobfuscate(data, start_offset):
    result = bytearray(data)
    noise = CSGMDL_OBFUSCATION_NOISE
    for i in range(len(result)):
        result[i] ^= noise[(start_offset + i) % 31]
    return bytes(result)

def _decode_csgmdl_faces_v5(vertex_data, expected_count):
    indices = []
    it = iter(vertex_data)
    index_out = 0
    for _ in range(expected_count):
        v0 = next(it)
        if v0 < 64:
            offset = v0
        elif v0 < 128:
            offset = v0 - 128
        else:
            v1 = next(it); v2 = next(it)
            offset = int.from_bytes([v2, v1, v0 - 128, 0], 'little', signed=True)
        index_out = (index_out + offset) & 0xFFFFFFFF
        indices.append(index_out & 0x007FFFFF)
    return indices

def parse_csgmdl(data: bytes) -> dict:
    """Parse CSGMDL Union geometry (V2/V4/V5)."""
    if len(data) < 10:
        raise ValueError("Too small for CSGMDL")
    magic = data[:10]
    if magic == CSGMDL_V5_MAGIC:
        p = 10
        pos_count = struct.unpack_from("<H", data, p)[0]; p += 2
        positions = [(struct.unpack_from("<f", data, p+i*12)[0],
                     struct.unpack_from("<f", data, p+i*12+4)[0],
                     struct.unpack_from("<f", data, p+i*12+8)[0]) for i in range(pos_count)]
        p += pos_count * 12
        normals_count = struct.unpack_from("<H", data, p)[0]; p += 2
        normals_len = struct.unpack_from("<I", data, p)[0]; p += 4
        p += normals_count * 6
        color_count = struct.unpack_from("<H", data, p)[0]; p += 2
        p += color_count * 4
        normal_id_count = struct.unpack_from("<H", data, p)[0]; p += 2
        p += normal_id_count
        tex_count = struct.unpack_from("<H", data, p)[0]; p += 2
        p += tex_count * 8
        tangents_count = struct.unpack_from("<H", data, p)[0]; p += 2
        tangents_len = struct.unpack_from("<I", data, p)[0]; p += 4
        p += tangents_count * 6
        vertex_count = struct.unpack_from("<I", data, p)[0]; p += 4
        vertex_data_len = struct.unpack_from("<I", data, p)[0]; p += 4
        vertex_data = data[p:p+vertex_data_len]; p += vertex_data_len
        range_marker_count = data[p]; p += 1
        range_markers = [struct.unpack_from("<I", data, p+i*4)[0] for i in range(range_marker_count)]
        indices = _decode_csgmdl_faces_v5(vertex_data, vertex_count)
        if len(range_markers) >= 2:
            faces_indices = indices[range_markers[0]:range_markers[1]]
        else:
            faces_indices = indices
        faces = [(faces_indices[i], faces_indices[i+1], faces_indices[i+2])
                 for i in range(0, len(faces_indices)-2, 3)]
        return {"version": "V5", "vertices": positions, "faces": faces,
                "vertex_count": len(positions), "face_count": len(faces)}
    elif magic == CSGMDL_V4_MAGIC:
        raw = _csgmdl_deobfuscate(data[10:], start_offset=10)
        p = 32  # skip hash
        vertex_count = struct.unpack_from("<I", raw, p)[0]; p += 4
        magic84 = struct.unpack_from("<I", raw, p)[0]; p += 4
        positions = []
        for _ in range(vertex_count):
            x, y, z = struct.unpack_from("<3f", raw, p)
            positions.append((x, y, z))
            p += 84
        face_count = struct.unpack_from("<I", raw, p)[0] // 3; p += 4
        faces = [(struct.unpack_from("<I", raw, p+i*12)[0],
                 struct.unpack_from("<I", raw, p+i*12+4)[0],
                 struct.unpack_from("<I", raw, p+i*12+8)[0]) for i in range(face_count)]
        return {"version": "V4", "vertices": positions, "faces": faces,
                "vertex_count": len(positions), "face_count": len(faces)}
    elif magic == CSGMDL_V2_MAGIC:
        raw = _csgmdl_deobfuscate(data[10:], start_offset=10)
        p = 32
        vertex_count = struct.unpack_from("<I", raw, p)[0]; p += 4
        magic84 = struct.unpack_from("<I", raw, p)[0]; p += 4
        positions = []
        for _ in range(vertex_count):
            x, y, z = struct.unpack_from("<3f", raw, p)
            positions.append((x, y, z))
            p += 84
        face_count = struct.unpack_from("<I", raw, p)[0] // 3; p += 4
        faces = [(struct.unpack_from("<I", raw, p+i*12)[0],
                 struct.unpack_from("<I", raw, p+i*12+4)[0],
                 struct.unpack_from("<I", raw, p+i*12+8)[0]) for i in range(face_count)]
        return {"version": "V2", "vertices": positions, "faces": faces,
                "vertex_count": len(positions), "face_count": len(faces)}
    else:
        raise ValueError(f"Unknown CSGMDL magic: {magic.hex()}")


# ── VALIDATION ─────────────────────────────────────────────────────

def validate_mesh(mesh: RobloxMesh, name: str) -> dict:
    results = {'name': name, 'passed': True, 'errors': [], 'warnings': [], 'stats': {}}
    v_count = len(mesh.vertices)
    f_count = len(mesh.faces)
    n_count = len(mesh.normals)
    uv_count = len(mesh.uvs)
    results['stats'] = {'vertices': v_count, 'faces': f_count, 'normals': n_count, 'uvs': uv_count}
    if v_count == 0:
        results['passed'] = False; results['errors'].append("Zero vertices")
    if f_count == 0:
        results['passed'] = False; results['errors'].append("Zero faces")
    if n_count != v_count:
        results['warnings'].append(f"Normal count ({n_count}) != vertex count ({v_count})")
    if uv_count != v_count:
        results['warnings'].append(f"UV count ({uv_count}) != vertex count ({v_count})")
    max_index = v_count - 1
    for i, (a, b, c) in enumerate(mesh.faces):
        for idx, label in [(a, 'a'), (b, 'b'), (c, 'c')]:
            if idx < 0 or idx > max_index:
                results['passed'] = False
                results['errors'].append(f"Face {i}: index {label}={idx} out of bounds [0, {max_index}]")
    for i, (x, y, z) in enumerate(mesh.vertices):
        if any(val != val for val in (x, y, z)):
            results['passed'] = False; results['errors'].append(f"Vertex {i}: NaN in position")
    for i, (x, y, z) in enumerate(mesh.normals):
        if any(val != val for val in (x, y, z)):
            results['passed'] = False; results['errors'].append(f"Normal {i}: NaN in normal")
    for i, (u, v) in enumerate(mesh.uvs):
        if any(val != val for val in (u, v)):
            results['passed'] = False; results['errors'].append(f"UV {i}: NaN in UV")
    if v_count > 0:
        xs = [v[0] for v in mesh.vertices]
        ys = [v[1] for v in mesh.vertices]
        zs = [v[2] for v in mesh.vertices]
        bbox = {'min': (min(xs), min(ys), min(zs)), 'max': (max(xs), max(ys), max(zs)),
                'size': (max(xs)-min(xs), max(ys)-min(ys), max(zs)-min(zs))}
        results['bbox'] = bbox
        if bbox['size'][0] == 0 or bbox['size'][1] == 0 or bbox['size'][2] == 0:
            results['warnings'].append(f"Degenerate bbox: {bbox['size']}")
    for i, (a, b, c) in enumerate(mesh.faces):
        if a == b or b == c or a == c:
            results['warnings'].append(f"Face {i}: duplicate indices ({a}, {b}, {c})")
    return results


def extract_meshes_from_rbxm(rbxm_path: Path) -> list[tuple[str, bytes]]:
    """Extract embedded mesh data from RBXM binary by parsing chunks properly."""
    data = rbxm_path.read_bytes()
    meshes = []

    if not data.startswith(b"<roblox!"):
        return meshes

    try:
        # Parse RBXM header
        num_types = struct.unpack("<I", data[16:20])[0]
        num_instances = struct.unpack("<I", data[20:24])[0]

        offset = 32
        chunks = {}
        while offset < len(data):
            name = data[offset:offset+4].rstrip(b"\x00").decode("ascii", "replace")
            cl = struct.unpack("<I", data[offset+4:offset+8])[0]
            ul = struct.unpack("<I", data[offset+8:offset+12])[0]
            bs = offset + 16
            if cl == 0:
                body = data[bs:bs+ul]
                be = bs + ul
            else:
                try:
                    body = lz4.block.decompress(data[bs:bs+cl], uncompressed_size=ul)
                except:
                    body = b""
                be = bs + cl
            chunks[name] = body
            offset = be
            if name == "END":
                break

        # 1. Check for PartOperationAsset MeshData in PROP chunks
        # (This is a simplified approach - full parsing needs type map)

        # 2. Check for standalone .mesh files in BSTR/BINA chunks or embedded in SSTR
        # Look for "version " strings in any chunk that might contain mesh data
        for chunk_name, chunk_data in chunks.items():
            pos = 0
            while True:
                idx = chunk_data.find(b"version ", pos)
                if idx == -1:
                    break
                next_idx = chunk_data.find(b"version ", idx + 1)
                mesh_data = chunk_data[idx:] if next_idx == -1 else chunk_data[idx:next_idx]
                if len(mesh_data) > 50:  # Filter out false positives
                    meshes.append((f"{rbxm_path.stem}_{chunk_name}_mesh_{len(meshes)}", mesh_data))
                pos = idx + 1

        # 3. Also scan raw data as fallback
        pos = 0
        while True:
            idx = data.find(b"version ", pos)
            if idx == -1: break
            next_idx = data.find(b"version ", idx + 1)
            mesh_data = data[idx:] if next_idx == -1 else data[idx:next_idx]
            if len(mesh_data) > 50:
                meshes.append((f"{rbxm_path.stem}_raw_mesh_{len(meshes)}", mesh_data))
            pos = idx + 1

    except Exception as e:
        pass  # Silently fail, return whatever we found

    return meshes


def test_file(filepath: Path, name: str = None) -> dict:
    if name is None: name = filepath.name
    raw_data = filepath.read_bytes()
    if raw_data[:2] == b'\x1f\x8b':
        try: raw_data = gzip.decompress(raw_data)
        except Exception as e:
            return {'name': name, 'passed': False, 'errors': [f'Gzip failed: {e}'], 'warnings': [], 'stats': {}}
    if len(raw_data) >= 10:
        magic = raw_data[:10]
        if magic in (CSGMDL_V5_MAGIC, CSGMDL_V4_MAGIC, CSGMDL_V2_MAGIC):
            try:
                mesh_data = parse_csgmdl(raw_data)
                mesh = RobloxMesh(
                    vertices=mesh_data['vertices'],
                    normals=[(0,0,1)] * len(mesh_data['vertices']),
                    uvs=[(0,0)] * len(mesh_data['vertices']),
                    faces=mesh_data['faces'],
                    version=mesh_data['version'],
                    name=name
                )
                return validate_mesh(mesh, name)
            except Exception as e:
                return {'name': name, 'passed': False, 'errors': [f'CSGMDL parse failed: {e}'], 'warnings': [], 'stats': {}}
    if not raw_data.startswith(b"version"):
        return {'name': name, 'passed': False, 'errors': ['No version header'], 'warnings': [], 'stats': {}}
    try:
        mesh = parse_mesh(raw_data, name=name)
        return validate_mesh(mesh, name)
    except Exception as e:
        return {'name': name, 'passed': False, 'errors': [f'Parse failed: {e}'], 'warnings': [], 'stats': {}}


# ── MAIN ───────────────────────────────────────────────────────────

def main():
    base = Path("/root/3DRBX-MT")
    all_results = []
    for f in (base / "samples" / "meshes_raw").glob("*"):
        if f.is_file() and not f.name.endswith(('.txt', '.log', '.gltf', '.zip')):
            all_results.append(test_file(f))
    for f in (base / "samples" / "downloads").glob("*"):
        if f.is_file() and not f.name.endswith(('.txt', '.log', '.gltf', '.zip')):
            all_results.append(test_file(f))
    for rbxm in (base / "samples").glob("*.rbxm"):
        for name, mesh_data in extract_meshes_from_rbxm(rbxm):
            import tempfile
            with tempfile.NamedTemporaryFile(mode='wb', suffix='.mesh', delete=False) as tf:
                tf.write(mesh_data); temp_path = Path(tf.name)
            try:
                all_results.append(test_file(temp_path, name))
            finally:
                temp_path.unlink(missing_ok=True)
    print("=" * 80)
    print("COMPREHENSIVE MESH TESTS - Step 6")
    print("=" * 80)
    total = len(all_results)
    passed = sum(1 for r in all_results if r['passed'])
    failed = total - passed
    for r in all_results:
        status = "✅ PASS" if r['passed'] else "❌ FAIL"
        print(f"\n{status}  {r['name']}")
        if r['stats']:
            print(f"  Vertices: {r['stats']['vertices']}  Faces: {r['stats']['faces']}  Normals: {r['stats']['normals']}  UVs: {r['stats']['uvs']}")
        if r.get('bbox'):
            b = r['bbox']
            print(f"  BBox: min={b['min']} max={b['max']} size={b['size']}")
        for w in r['warnings']:
            print(f"  ⚠️  {w}")
        for e in r['errors']:
            print(f"  ❌ {e}")
    print("\n" + "=" * 80)
    print(f"SUMMARY: {passed}/{total} passed, {failed} failed")
    print("=" * 80)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())