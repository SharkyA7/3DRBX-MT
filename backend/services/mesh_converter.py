"""
services/mesh_converter.py
Parse format mesh Roblox (.mesh binary) dan konversi ke OBJ atau GLTF.

Roblox Mesh Versions:
  v1.00  — ASCII
  v2.00  — Binary (triangles + normals + UVs)
  v3.00  — Binary + LOD
  v4.00  — Binary + skinning data
  v5.00  — Binary + skinning + FACS blendshapes
"""
import struct
import json
import io
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RobloxMesh:
    vertices: list   # list of (x, y, z)
    normals:  list   # list of (nx, ny, nz)
    uvs:      list   # list of (u, v)
    faces:    list   # list of (i0, i1, i2)
    version:  str    = "unknown"
    name:     str    = "mesh"


# ── PARSERS ───────────────────────────────────────────────────────

def _parse_v1(data: bytes) -> RobloxMesh:
    """Version 1.00 — plain ASCII."""
    lines = data.decode("utf-8", errors="replace").splitlines()
    # line 0: version, line 1: face count, line 2+: face data
    faces_raw = []
    verts, norms, uvs, faces = [], [], [], []

    for line in lines[2:]:
        line = line.strip()
        if not line:
            continue
        # Each line is a face: [vx,vy,vz][nx,ny,nz][u,v] repeated 3x
        tokens = line.replace("[", " ").replace("]", " ").split(",")
        floats = [float(t.strip()) for t in tokens if t.strip()]
        # 3 vertices × 8 floats = 24
        if len(floats) < 24:
            continue
        base = len(verts)
        for i in range(3):
            o = i * 8
            verts.append((floats[o],   floats[o+1], floats[o+2]))
            norms.append((floats[o+3], floats[o+4], floats[o+5]))
            uvs.append(  (floats[o+6], floats[o+7]))
        faces.append((base, base+1, base+2))

    return RobloxMesh(verts, norms, uvs, faces, version="1.00")


def _parse_v2(data: bytes) -> RobloxMesh:
    """Version 2.00 — binary MeshHeader immediately follows the fixed 13-byte
    "version 2.00\\n" line. There is NO ASCII/newline-terminated header line here —
    the header (sizeof_MeshHeader: uint16, sizeof_Vertex: uint8, sizeof_Face: uint8,
    numVerts: uint32, numFaces: uint32 = 12 bytes total) is raw binary.
    Reading it with buf.readline().decode() (the old approach) is wrong: readline()
    stops at the first 0x0A byte, which shows up constantly inside ordinary binary
    vertex floats, so it silently swallows real vertex bytes into a bogus "header
    line" and then throws a UnicodeDecodeError trying to decode them as UTF-8.
    Ref: devforum.roblox.com/t/roblox-mesh-format/326114,
         github.com/pkhead/rbx-mesh2obj (mesh_v2::MeshHeader)."""
    pos = data.index(b"\n") + 1  # skip "version 2.00\n" (13 bytes, but be tolerant of length)
    sizeof_header, sizeof_vertex, sizeof_face = struct.unpack_from("<HBB", data, pos)
    num_verts, num_faces = struct.unpack_from("<II", data, pos + 4)

    p = pos + sizeof_header
    verts, norms, uvs, faces = [], [], [], []

    for _ in range(num_verts):
        # px py pz  nx ny nz  u v  (+ optional trailing color bytes we don't read)
        px, py, pz, nx, ny, nz, u, v = struct.unpack_from("<ffffffff", data, p)
        verts.append((px, py, pz))
        norms.append((nx, ny, nz))
        uvs.append((u, 1.0 - v))   # flip V axis
        p += sizeof_vertex

    for _ in range(num_faces):
        i0, i1, i2 = struct.unpack_from("<III", data, p)
        faces.append((i0, i1, i2))
        p += sizeof_face

    return RobloxMesh(verts, norms, uvs, faces, version="2.00")


def _parse_v3(data: bytes) -> RobloxMesh:
    """Version 3.00 — binary MeshHeader immediately follows the fixed 13-byte
    "version 3.00\\n" line. Per MaximumADHD's official writeup
    (devforum.roblox.com/t/version-300-of-mesh-format-has-no-public-documentation/287887),
    the v3 MeshHeader is 7 consecutive uint16 fields (14 bytes total):
      sizeof_MeshHeader, sizeof_MeshVertex, sizeof_MeshFace, sizeof_MeshLOD,
      numLODs, numVerts, numFaces.
    As with v2, this is raw binary — NOT an ASCII line — so it must never be read
    with buf.readline().decode()."""
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



def _parse_v4(data: bytes) -> "RobloxMesh":
    """Version 4.0x — binary header (verified): 24-byte header dgn LOD/bone/subset fields,
    40-byte vertex (9 float pos/normal/uv + 4 byte RGBA color), 12-byte face (3x uint32).
    LOD offsets array (numLods x uint32) adalah BOUNDARY KUMULATIF, bukan length:
    LOD0 = faces[offsets[0]:offsets[1]] (level paling detail).
    Ref: devforum.roblox.com/t/roblox-mesh-format/326114
    """
    pos = 13  # skip "version 4.0X\n"
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

    fstart = p
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

    return RobloxMesh(vertices=vertices, normals=normals, uvs=uvs, faces=faces, version="4.0x")


def parse_mesh(data: bytes, name: str = "mesh") -> RobloxMesh:
    """
    Deteksi versi dan parse mesh Roblox.
    Raises ValueError jika format tidak dikenal.
    """
    if not data:
        raise ValueError("Data mesh kosong")

    header = data[:16].decode("utf-8", errors="replace")

    if "version 1." in header:
        mesh = _parse_v1(data)
    elif "version 2." in header:
        mesh = _parse_v2(data)
    elif "version 4." in header or "version 5." in header:
        # v4.xx/v5.xx - binary header verified, LOD0 extraction
        try:
            mesh = _parse_v4(data)
        except Exception:
            try:
                mesh = _parse_v3(data)
            except Exception:
                mesh = _parse_v2(data)
    elif "version 3." in header:
        try:
            mesh = _parse_v3(data)
        except Exception:
            mesh = _parse_v2(data)
    else:
        raise ValueError(f"Format mesh Roblox tidak dikenal: {header[:20]!r}")

    mesh.name = name
    return mesh


# ── EXPORTERS ─────────────────────────────────────────────────────

def mesh_to_obj(mesh: RobloxMesh, mtl_name: Optional[str] = None) -> str:
    """Konversi RobloxMesh ke format OBJ string."""
    lines = [
        f"# Roblox Mesh — {mesh.name}",
        f"# Version: {mesh.version}",
        f"# Vertices: {len(mesh.vertices)}  Faces: {len(mesh.faces)}",
        "",
    ]
    if mtl_name:
        lines += [f"mtllib {mtl_name}.mtl", f"usemtl default", ""]

    lines.append(f"o {mesh.name}")

    for x, y, z in mesh.vertices:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")

    lines.append("")
    for u, v in mesh.uvs:
        lines.append(f"vt {u:.6f} {v:.6f}")

    lines.append("")
    for nx, ny, nz in mesh.normals:
        lines.append(f"vn {nx:.6f} {ny:.6f} {nz:.6f}")

    lines.append("")
    for i0, i1, i2 in mesh.faces:
        # OBJ adalah 1-indexed, format: v/vt/vn
        a, b, c = i0+1, i1+1, i2+1
        lines.append(f"f {a}/{a}/{a} {b}/{b}/{b} {c}/{c}/{c}")

    return "\n".join(lines)


def mesh_to_gltf(mesh: RobloxMesh) -> dict:
    """Konversi RobloxMesh ke GLTF 2.0 dict (bisa di-json.dumps langsung)."""
    import base64

    # Flatten arrays
    pos_data = b""
    for x, y, z in mesh.vertices:
        pos_data += struct.pack("<fff", x, y, z)

    norm_data = b""
    for nx, ny, nz in mesh.normals:
        norm_data += struct.pack("<fff", nx, ny, nz)

    uv_data = b""
    for u, v in mesh.uvs:
        uv_data += struct.pack("<ff", u, v)

    idx_data = b""
    for i0, i1, i2 in mesh.faces:
        idx_data += struct.pack("<III", i0, i1, i2)

    def b64(b: bytes) -> str:
        return "data:application/octet-stream;base64," + base64.b64encode(b).decode()

    n_verts = len(mesh.vertices)
    n_faces = len(mesh.faces)

    min_pos = [min(v[i] for v in mesh.vertices) for i in range(3)]
    max_pos = [max(v[i] for v in mesh.vertices) for i in range(3)]

    gltf = {
        "asset": {"version": "2.0", "generator": "RobloxDownloader/1.0"},
        "scene": 0,
        "scenes": [{"nodes": [0]}],
        "nodes": [{"mesh": 0, "name": mesh.name}],
        "meshes": [{
            "name": mesh.name,
            "primitives": [{
                "attributes": {
                    "POSITION":   0,
                    "NORMAL":     1,
                    "TEXCOORD_0": 2,
                },
                "indices": 3,
                "mode": 4,   # TRIANGLES
            }]
        }],
        "accessors": [
            # 0 POSITION
            {"bufferView": 0, "componentType": 5126, "count": n_verts,
             "type": "VEC3", "min": min_pos, "max": max_pos},
            # 1 NORMAL
            {"bufferView": 1, "componentType": 5126, "count": n_verts, "type": "VEC3"},
            # 2 TEXCOORD_0
            {"bufferView": 2, "componentType": 5126, "count": n_verts, "type": "VEC2"},
            # 3 INDICES
            {"bufferView": 3, "componentType": 5125, "count": n_faces * 3, "type": "SCALAR"},
        ],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0,                      "byteLength": len(pos_data),  "target": 34962},
            {"buffer": 1, "byteOffset": 0,                      "byteLength": len(norm_data), "target": 34962},
            {"buffer": 2, "byteOffset": 0,                      "byteLength": len(uv_data),   "target": 34962},
            {"buffer": 3, "byteOffset": 0,                      "byteLength": len(idx_data),  "target": 34963},
        ],
        "buffers": [
            {"uri": b64(pos_data),  "byteLength": len(pos_data)},
            {"uri": b64(norm_data), "byteLength": len(norm_data)},
            {"uri": b64(uv_data),   "byteLength": len(uv_data)},
            {"uri": b64(idx_data),  "byteLength": len(idx_data)},
        ],
    }
    return gltf


def default_mtl(name: str = "default") -> str:
    return (
        f"newmtl {name}\n"
        "Ka 0.1 0.1 0.1\n"
        "Kd 0.8 0.8 0.8\n"
        "Ks 0.05 0.05 0.05\n"
        "Ns 10\n"
        "d 1\n"
    )
