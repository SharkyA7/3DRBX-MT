from flask import Flask, jsonify, request, Response, redirect
import httpx, os, io, zipfile, time, json, struct, requests
import lz4.block, re

def safe_filename(name):
    return re.sub(r'[^a-zA-Z0-9_\-]', '_', str(name))[:50]

# ── INLINED MESH CONVERTER (avoids services import issue on Vercel) ──
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
    """Version 2.00 — binary with header."""
    buf = io.BytesIO(data)
    # Skip version line  e.g. b'version 2.00\n'
    buf.readline()
    # Header line: sizeof_MeshHeader sizeof_Vertex sizeof_Face
    hdr_line = buf.readline().decode().strip()
    parts = hdr_line.split()
    sizeof_header, sizeof_vertex, sizeof_face = int(parts[0]), int(parts[1]), int(parts[2])

    # Mesh header
    hdr_data = buf.read(sizeof_header)
    nh = (sizeof_header - 4) // 4  # remaining ints after 2-byte lod fields
    num_verts, num_faces = struct.unpack_from("<II", hdr_data, 0)

    verts, norms, uvs, faces = [], [], [], []

    for _ in range(num_verts):
        raw = buf.read(sizeof_vertex)
        # px py pz  nx ny nz  u v  tx ty tz ts (tangent, 4 bytes)
        px, py, pz, nx, ny, nz, u, v = struct.unpack_from("<ffffffff", raw, 0)
        verts.append((px, py, pz))
        norms.append((nx, ny, nz))
        uvs.append((u, 1.0 - v))   # flip V axis

    for _ in range(num_faces):
        raw = buf.read(sizeof_face)
        i0, i1, i2 = struct.unpack_from("<III", raw, 0)
        faces.append((i0, i1, i2))

    return RobloxMesh(verts, norms, uvs, faces, version="2.00")


def _parse_v3(data: bytes) -> RobloxMesh:
    """Version 3.00 — binary + LOD table (we ignore LOD)."""
    buf = io.BytesIO(data)
    buf.readline()  # version
    hdr_line = buf.readline().decode().strip()
    parts = hdr_line.split()
    sizeof_header, sizeof_vertex, sizeof_face, sizeof_lod = (
        int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    )
    hdr_raw = buf.read(sizeof_header)
    num_verts, num_faces, num_lods = struct.unpack_from("<III", hdr_raw, 0)

    verts, norms, uvs, faces = [], [], [], []

    for _ in range(num_verts):
        raw = buf.read(sizeof_vertex)
        px, py, pz, nx, ny, nz, u, v = struct.unpack_from("<ffffffff", raw, 0)
        verts.append((px, py, pz))
        norms.append((nx, ny, nz))
        uvs.append((u, 1.0 - v))

    for _ in range(num_faces):
        raw = buf.read(sizeof_face)
        i0, i1, i2 = struct.unpack_from("<III", raw, 0)
        faces.append((i0, i1, i2))

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
from dotenv import load_dotenv

load_dotenv()
app = Flask(__name__)

# CORS - izinkan semua request dari browser
@app.after_request
def add_cors(response):
    response.headers["Access-Control-Allow-Origin"]  = "*"
    response.headers["Access-Control-Allow-Methods"] = "GET,POST,OPTIONS"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return response

COOKIE  = os.getenv("ROBLOX_COOKIE","")

# ── ROBLOX OAUTH 2.0 CONFIG ──────────────────────────────────────
OAUTH_CLIENT_ID     = os.getenv("ROBLOX_OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.getenv("ROBLOX_OAUTH_CLIENT_SECRET", "")
OAUTH_REDIRECT_URI  = os.getenv("ROBLOX_OAUTH_REDIRECT_URI", "https://getrbx3d.qzz.io/auth/callback")
OAUTH_SCOPES        = "openid profile asset:read"
OAUTH_AUTH_URL      = "https://apis.roblox.com/oauth/v1/authorize"
OAUTH_TOKEN_URL     = "https://apis.roblox.com/oauth/v1/token"
OAUTH_USERINFO_URL  = "https://apis.roblox.com/oauth/v1/userinfo"
_cache = {}
CACHE_TTL = 300

def cache_get(key):
    if key in _cache:
        val, ts = _cache[key]
        if time.time() - ts < CACHE_TTL:
            return val
        del _cache[key]
    return None

def cache_set(key, val):
    _cache[key] = (val, time.time())
API_KEY = os.getenv("ROBLOX_API_KEY","")
TIMEOUT = 15


def parse_rbxmx(data):
    """Parse RBXMX (XML format) Roblox model file.
    Jauh lebih sederhana dari binary RBXM - semua nilai langsung terbaca sebagai teks.
    Return: dict dengan format sama seperti parse_rbxm_binary (compatible dengan model_convert/model_info)
    """
    import xml.etree.ElementTree as ET

    root = ET.fromstring(data.decode('utf-8', 'replace'))

    TARGET_CLASSES = {'MeshPart', 'Part', 'UnionOperation'}
    counts = {'MeshPart': 0, 'Part': 0, 'UnionOperation': 0}
    parts = []

    def get_text(props, tag, attr_name):
        el = props.find(f"{tag}[@name='{attr_name}']")
        return el.text.strip() if el is not None and el.text else None

    def get_float(props, tag, attr_name):
        t = get_text(props, tag, attr_name)
        try: return float(t) if t else 0.0
        except: return 0.0

    def parse_color3uint8(props):
        el = props.find("Color3uint8[@name='Color3uint8']")
        if el is None:
            return [163, 162, 165]
        try:
            # Format: single integer e.g. "10724005" = 0xA39965 = R:163 G:153 B:101
            val = int(el.text.strip())
            r = (val >> 16) & 0xFF
            g = (val >> 8) & 0xFF
            b = val & 0xFF
            return [r, g, b]
        except:
            return [163, 162, 165]

    def parse_cframe(props, cf_name='CFrame'):
        cf = props.find(f"CoordinateFrame[@name='{cf_name}']")
        if cf is None:
            return [0.0, 0.0, 0.0], [1,0,0,0,1,0,0,0,1], "identity-fallback"
        try:
            x = float(cf.find('X').text or 0)
            y = float(cf.find('Y').text or 0)
            z = float(cf.find('Z').text or 0)
            r = [float(cf.find(f'R{i}{j}').text or (1 if i==j else 0)) for i in range(3) for j in range(3)]
            return [x,y,z], r, "decoded"
        except:
            return [0.0,0.0,0.0], [1,0,0,0,1,0,0,0,1], "identity-fallback"

    def parse_vector3(props, attr_name):
        el = props.find(f"Vector3[@name='{attr_name}']")
        if el is None:
            return [1.0, 1.0, 1.0]
        try:
            return [float(el.find('X').text or 1), float(el.find('Y').text or 1), float(el.find('Z').text or 1)]
        except:
            return [1.0, 1.0, 1.0]

    for item in root.iter('Item'):
        cls = item.get('class')
        if cls not in TARGET_CLASSES:
            continue
        counts[cls] = counts.get(cls, 0) + 1
        props = item.find('Properties')
        if props is None:
            continue

        name = get_text(props, 'string', 'Name') or cls
        pos, rot_val, rot_status = parse_cframe(props, 'CFrame')
        size = parse_vector3(props, 'size')
        color = parse_color3uint8(props)

        mesh_id = None
        texture_id = None
        if cls == 'MeshPart':
            mesh_id = get_text(props, 'string', 'MeshId')
            texture_id = get_text(props, 'string', 'TextureID')

        parts.append({
            "name": name,
            "className": cls,
            "meshId": mesh_id,
            "textureId": texture_id,
            "position": {"status": "decoded", "value": [round(v,4) for v in pos]},
            "size": {"status": "decoded", "value": [round(v,4) for v in size]},
            "rotation": {"status": rot_status, "value": [round(v,6) for v in rot_val], "rawRotationId": None},
            "color": {"status": "decoded", "value": color}
        })

    return {
        "meshPartCount": counts['MeshPart'],
        "partCount": counts['Part'],
        "unionCount": counts['UnionOperation'],
        "parts": parts
    }



# ── CSGMDL V5 PARSER (Union/CSG geometry) ────────────────────────
# Format reverse-engineered from krakow10/rbx_mesh (Rust)
# V5 magic: obfuscated "CSGMDL\x05\x00\x00\x00" XOR NOISE
# Body after magic: RAW (no XOR), unlike V2/V4

CSGMDL_V5_MAGIC = b"\x15\x7d\x29\x15\x75\x6c\x35\x04\x34\x69"
CSGMDL_V4_MAGIC = b"\x15\x7d\x29\x15\x75\x6c\x34\x04\x34\x69"
CSGMDL_V2_MAGIC = b"\x15\x7d\x29\x15\x75\x6c\x32\x04\x34\x69"

CSGMDL_OBFUSCATION_NOISE = bytes([
    86,46,110,88,49,32,48,4,52,105,12,119,12,1,94,0,26,96,55,105,29,82,43,7,79,36,89,101,83,4,122
])

def _csgmdl_deobfuscate(data, start_offset):
    """XOR deobfuscation for V2/V4 body (NOT needed for V5 body)."""
    result = bytearray(data)
    noise = CSGMDL_OBFUSCATION_NOISE
    for i in range(len(result)):
        result[i] ^= noise[(start_offset + i) % 31]
    return bytes(result)

def _decode_csgmdl_faces_v5(vertex_data, expected_count):
    """Decode delta-encoded face indices (state machine from v5.rs)."""
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
            v1 = next(it)
            v2 = next(it)
            offset = int.from_bytes([v2, v1, v0 - 128, 0], 'little', signed=True)
        index_out = (index_out + offset) & 0xFFFFFFFF
        indices.append(index_out & 0x007FFFFF)
    return indices

def parse_csgmdl(data):
    """Parse CSGMDL Union geometry (V2/V4/V5).
    Returns dict: {version, vertices:[(x,y,z),...], faces:[(a,b,c),...], vertex_count, face_count}
    Raises ValueError if format not recognized or parse fails.
    """
    if len(data) < 10:
        raise ValueError("Data terlalu kecil untuk CSGMDL")

    magic = data[:10]

    if magic == CSGMDL_V5_MAGIC:
        return _parse_csgmdl_v5(data)
    elif magic == CSGMDL_V4_MAGIC:
        return _parse_csgmdl_v2_v4(data, version=4)
    elif magic == CSGMDL_V2_MAGIC:
        return _parse_csgmdl_v2_v4(data, version=2)
    else:
        raise ValueError(f"Magic CSGMDL tidak dikenal: {magic[:10].hex()}")

def _parse_csgmdl_v5(data):
    """Parse CSGMDL V5 - body is NOT obfuscated after magic."""
    p = 10  # skip magic

    pos_count = struct.unpack("<H", data[p:p+2])[0]; p += 2
    positions = []
    for _ in range(pos_count):
        x, y, z = struct.unpack("<3f", data[p:p+12]); p += 12
        positions.append((x, y, z))

    normals_count = struct.unpack("<H", data[p:p+2])[0]; p += 2
    normals_len = struct.unpack("<I", data[p:p+4])[0]; p += 4
    p += normals_count * 6  # QuantizedF32x3 = [i16;3] = 6 bytes

    color_count = struct.unpack("<H", data[p:p+2])[0]; p += 2
    p += color_count * 4

    normal_id_count = struct.unpack("<H", data[p:p+2])[0]; p += 2
    p += normal_id_count  # u8 each

    tex_count = struct.unpack("<H", data[p:p+2])[0]; p += 2
    p += tex_count * 8  # [f32;2] = 8 bytes

    tangents_count = struct.unpack("<H", data[p:p+2])[0]; p += 2
    tangents_len = struct.unpack("<I", data[p:p+4])[0]; p += 4
    p += tangents_count * 6

    # Faces5
    vertex_count = struct.unpack("<I", data[p:p+4])[0]; p += 4
    vertex_data_len = struct.unpack("<I", data[p:p+4])[0]; p += 4
    vertex_data = data[p:p+vertex_data_len]; p += vertex_data_len

    range_marker_count = data[p]; p += 1
    range_markers = []
    for _ in range(range_marker_count):
        range_markers.append(struct.unpack("<I", data[p:p+4])[0]); p += 4

    indices = _decode_csgmdl_faces_v5(vertex_data, vertex_count)

    # Use LOD0 = indices[range_markers[0]:range_markers[1]] if available
    if len(range_markers) >= 2:
        faces_indices = indices[range_markers[0]:range_markers[1]]
    else:
        faces_indices = indices

    faces = [(faces_indices[i], faces_indices[i+1], faces_indices[i+2])
             for i in range(0, len(faces_indices)-2, 3)]

    return {
        "version": "V5",
        "vertices": positions,
        "faces": faces,
        "vertex_count": len(positions),
        "face_count": len(faces)
    }

def _parse_csgmdl_v2_v4(data, version):
    """Parse CSGMDL V2/V4 - body IS obfuscated after magic (XOR with noise)."""
    raw = _csgmdl_deobfuscate(data[10:], start_offset=10)
    p = 0

    # Hash: 32 bytes
    p += 32

    # Mesh2: vertex_count u32, magic 84 u32, vertices, face_count u32, faces
    vertex_count = struct.unpack("<I", raw[p:p+4])[0]; p += 4
    magic84 = struct.unpack("<I", raw[p:p+4])[0]; p += 4  # should be 84

    # Vertex = pos[f32;3] + norm[f32;3] + color[u8;4] + normal_id u32 + tex[f32;2]
    #        + tangent magic 0u128 (16 bytes) + magic 0u128 (16 bytes) = 84 bytes total
    positions = []
    for _ in range(vertex_count):
        x, y, z = struct.unpack("<3f", raw[p:p+12])
        positions.append((x, y, z))
        p += 84  # full vertex size

    face_count_raw = struct.unpack("<I", raw[p:p+4])[0]; p += 4
    face_count = face_count_raw // 3
    faces = []
    for _ in range(face_count):
        a, b, c = struct.unpack("<3I", raw[p:p+12])
        faces.append((a, b, c)); p += 12

    if version == 4:
        unknown_count = struct.unpack("<I", raw[p:p+4])[0]; p += 4
        p += unknown_count * 4

    return {
        "version": f"V{version}",
        "vertices": positions,
        "faces": faces,
        "vertex_count": len(positions),
        "face_count": len(faces)
    }

def csgmdl_to_obj(mesh_data, name="union_mesh"):
    """Convert parsed CSGMDL data to OBJ string."""
    lines = [f"# CSG Union mesh - {mesh_data['version']} ({mesh_data['vertex_count']} verts, {mesh_data['face_count']} faces)"]
    lines.append(f"o {name}")
    for x, y, z in mesh_data["vertices"]:
        lines.append(f"v {x:.6f} {y:.6f} {z:.6f}")
    for a, b, c in mesh_data["faces"]:
        lines.append(f"f {a+1} {b+1} {c+1}")
    return "\n".join(lines)


# ── INLINED RBXM PARSER (avoids services.rbxm_parser import issue on Vercel) ──
class RBXMParseError(Exception):
    pass


def parse_chunks(data):
    if data[:8] != b"<roblox!":
        raise RBXMParseError("Bukan format RBXM binary (mungkin XML/rbxmx atau tipe asset lain)")
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
        chunks.append((name, body))
        offset = be
        if name == "END":
            break
    return chunks, num_types, num_instances


def _read_interleaved_be_u32(buf, count):
    out = []
    for i in range(count):
        b0, b1, b2, b3 = buf[i], buf[count+i], buf[2*count+i], buf[3*count+i]
        out.append((b0 << 24) | (b1 << 16) | (b2 << 8) | b3)
    return out


def _ror1(u32):
    return (u32 >> 1) | ((u32 & 1) << 31)


def decode_f32_array(buf, count):
    """VERIFIED: interleaved byte-plane transpose + ROR1 bit rotation -> BE float32."""
    raw = _read_interleaved_be_u32(buf, count)
    return [struct.unpack(">f", struct.pack(">I", _ror1(v)))[0] for v in raw]


def read_referent_array(buf, count):
    """VERIFIED: used for INST instance ID lists."""
    raw = _read_interleaved_be_u32(buf, count)
    out = []
    last = 0
    for v in raw:
        sv = v if v < 2**31 else v - 2**32
        last = (last + sv) & 0xFFFFFFFF
        out.append(last if last < 2**31 else last - 2**32)
    return out


def parse_inst_chunks(chunks):
    """VERIFIED. Returns {type_id: {class_name, count, referents, is_service}}"""
    type_map = {}
    for name, body in chunks:
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
        type_map[type_id] = {"class_name": class_name, "count": n, "referents": referents, "is_service": is_service}
    return type_map


def find_prop_chunk(chunks, type_id, prop_name):
    for name, body in chunks:
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


def decode_string_array(raw, count):
    """VERIFIED: sequential length-prefixed UTF8."""
    pos = 0
    out = []
    for _ in range(count):
        slen = struct.unpack("<I", raw[pos:pos+4])[0]; pos += 4
        out.append(raw[pos:pos+slen].decode("utf-8", "replace"))
        pos += slen
    return out


def decode_vector3_array(raw, count):
    """VERIFIED: 3x interleaved+ROR1 f32 arrays -> (x,y,z) tuples."""
    plane = count * 4
    xs = decode_f32_array(raw[0:plane], count)
    ys = decode_f32_array(raw[plane:plane*2], count)
    zs = decode_f32_array(raw[plane*2:plane*3], count)
    return list(zip(xs, ys, zs))


def decode_color3uint8_array(raw, count):
    """UNVERIFIED layout - best-effort sequential RGB triplets."""
    out = []
    for i in range(count):
        if i*3+2 < len(raw):
            out.append((raw[i*3], raw[i*3+1], raw[i*3+2]))
        else:
            out.append((163, 162, 165))
    return out


# Official 24-entry special rotation ID table (rbx-dom binary.md spec, "Version 0").
# Rotations in degrees, applied in composite order Y -> X -> Z.
ROTATION_ID_TABLE = {
    0x02: (0, 0, 0),     0x03: (90, 0, 0),    0x05: (0, 180, 180), 0x06: (-90, 0, 0),
    0x07: (0, 180, 90),  0x09: (0, 90, 90),   0x0a: (0, 0, 90),    0x0c: (0, -90, 90),
    0x0d: (-90, -90, 0), 0x0e: (0, -90, 0),   0x10: (90, -90, 0),  0x11: (0, 90, 180),
    0x14: (0, 180, 0),   0x15: (-90, -180, 0),0x17: (0, 0, 180),   0x18: (90, 180, 0),
    0x19: (0, 0, -90),   0x1b: (0, -90, -90), 0x1c: (0, -180, -90),0x1e: (0, 90, -90),
    0x1f: (90, 90, 0),   0x20: (0, 90, 0),    0x22: (-90, 90, 0),  0x23: (0, -90, 180),
}

def _euler_yxz_to_matrix(rx_deg, ry_deg, rz_deg):
    """Build 3x3 rotation matrix from Euler angles, composite order Y->X->Z (R = Rz @ Rx @ Ry)."""
    import math
    rx, ry, rz = math.radians(rx_deg), math.radians(ry_deg), math.radians(rz_deg)
    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)
    Rx = [[1,0,0],[0,cx,-sx],[0,sx,cx]]
    Ry = [[cy,0,sy],[0,1,0],[-sy,0,cy]]
    Rz = [[cz,-sz,0],[sz,cz,0],[0,0,1]]
    def matmul(A,B):
        return [[sum(A[i][k]*B[k][j] for k in range(3)) for j in range(3)] for i in range(3)]
    R = matmul(matmul(Rz, Rx), Ry)
    return [R[0][0],R[0][1],R[0][2], R[1][0],R[1][1],R[1][2], R[2][0],R[2][1],R[2][2]]


def decode_cframe_full(raw, count):
    """
    VERIFIED CORRECT (confirmed via official rbx-dom spec + empirical byte-accounting match).
    Structure is SEQUENTIAL per-instance: [ID_0][matrix_0 if ID_0==0][ID_1][matrix_1 if ID_1==0]...
    followed by a separate Position Vector3 array (interleaved+Roblox-float, trailing count*12 bytes).
    Raw matrix (ID==0) is plain little-endian sequential float32, NOT interleaved/transformed.
    ID!=0 uses the 24-entry ROTATION_ID_TABLE (official spec) converted to a matrix via Euler angles.
    Returns: positions (list of (x,y,z)), rotations (list of dicts: status/value/rawRotationId)
    """
    p = 0
    rotation_ids = []
    raw_matrices = {}
    for i in range(count):
        rid = raw[p]; p += 1
        rotation_ids.append(rid)
        if rid == 0:
            raw_matrices[i] = raw[p:p+36]
            p += 36

    pos_block = raw[-(count * 12):]
    positions = decode_vector3_array(pos_block, count)

    rotations = []
    for i in range(count):
        rid = rotation_ids[i]
        if rid == 0:
            m = raw_matrices[i]
            vals = [struct.unpack("<f", m[j*4:j*4+4])[0] for j in range(9)]
            rotations.append({"status": "decoded", "value": [round(v,6) for v in vals], "rawRotationId": 0})
        elif rid in ROTATION_ID_TABLE:
            rx, ry, rz = ROTATION_ID_TABLE[rid]
            vals = _euler_yxz_to_matrix(rx, ry, rz)
            rotations.append({"status": "decoded", "value": [round(v,6) for v in vals], "rawRotationId": rid})
        else:
            rotations.append({"status": "unknown-id", "value": [1,0,0,0,1,0,0,0,1], "rawRotationId": rid})

    return positions, rotations


def decode_bool_array(raw, count):
    return [bool(b) for b in raw[:count]]

# Cloudscraper dengan cookie Roblox
import cloudscraper as _cs
_scraper = None
def get_scraper():
    global _scraper
    if _scraper is None:
        _scraper = _cs.create_scraper()
        if COOKIE:
            _scraper.cookies.set(".ROBLOSECURITY", COOKIE, domain=".roblox.com")
    return _scraper

def handle_roblox_error(e, context="request"):
    """Generate pesan error yang jelas untuk 401/403/timeout dari Roblox API"""
    msg = str(e)
    if "401" in msg or "Unauthorized" in msg:
        return jsonify({
            "error": f"Cookie Roblox tidak valid atau sudah expired (401). Update ROBLOX_COOKIE di environment variables.",
            "code": "COOKIE_EXPIRED",
            "context": context
        }), 401
    if "403" in msg or "Forbidden" in msg:
        return jsonify({
            "error": f"Akses ditolak oleh Roblox CDN (403). Bisa karena rate limit atau Cloudflare challenge gagal. Coba lagi dalam beberapa saat.",
            "code": "CLOUDFLARE_BLOCKED",
            "context": context
        }), 403
    if "timeout" in msg.lower() or "timed out" in msg.lower():
        return jsonify({
            "error": f"Request ke Roblox timeout. Server Roblox lambat merespons, coba lagi.",
            "code": "TIMEOUT",
            "context": context
        }), 504
    return jsonify({"error": msg, "context": context}), 500

def hdr(auth=False):
    h = {"User-Agent":"Mozilla/5.0","Accept":"application/json"}
    if auth and COOKIE: h["Cookie"] = f".ROBLOSECURITY={COOKIE}"
    if API_KEY: h["x-api-key"] = API_KEY
    return h

def rget(url):
    # Cookie hanya untuk assetdelivery (download asset privat)
    needs_auth = "assetdelivery.roblox.com" in url or "rbxcdn.com" in url
    with httpx.Client(timeout=TIMEOUT,follow_redirects=True) as c:
        r = c.get(url,headers=hdr(auth=needs_auth)); r.raise_for_status(); return r.json()

def rget_bytes(url):
    needs_auth = "assetdelivery.roblox.com" in url or "rbxcdn.com" in url
    with httpx.Client(timeout=30,follow_redirects=True) as c:
        r = c.get(url,headers=hdr(auth=needs_auth)); r.raise_for_status(); return r.content

def rpost(url,body):
    with httpx.Client(timeout=TIMEOUT) as c:
        r = c.post(url,json=body,headers=hdr()); r.raise_for_status(); return r.json()

def resolve(user):
    if user.isdigit(): return int(user)
    d = rpost("https://users.roblox.com/v1/usernames/users",{"usernames":[user],"excludeBannedUsers":False})
    u = d.get("data",[])
    if not u: raise ValueError(f"Username '{user}' tidak ditemukan")
    return u[0]["id"]

def rget_cdn(url):
    """Download file dari Roblox CDN pakai cloudscraper"""
    s = get_scraper()
    r = s.get(url, timeout=30)
    r.raise_for_status()
    return r.content

def get_3d_manifest(uid, retries=3):
    url = f"https://thumbnails.roblox.com/v1/users/avatar-3d?userId={uid}"
    for i in range(retries):
        try:
            s = get_scraper()
            r = s.get(url, timeout=15)
            if r.status_code != 200:
                print(f"[3d manifest] status {r.status_code}: {r.text[:100]}")
                time.sleep(1)
                continue
            d = r.json()
            print(f"[3d manifest] response: {str(d)[:150]}")

            # Format 1: {"state":"Completed","imageUrl":"..."}
            if d.get("state") == "Completed" and d.get("imageUrl"):
                return d["imageUrl"]

            # Format 2: {"data":[{"state":"Completed","imageUrl":"..."}]}
            item = (d.get("data") or [None])[0]
            if item and item.get("state") == "Completed":
                return item["imageUrl"]
            if item and item.get("state") == "Blocked":
                return None

            time.sleep(3)
        except Exception as e:
            print(f"[3d manifest attempt {i+1}] {e}")
            time.sleep(2)
    return None

COLORS={1:"#F2F3F3",21:"#C4281C",23:"#0D69AC",24:"#F5CD2F",26:"#1B2A35",
        37:"#4B9748",101:"#DA867A",102:"#6E99CA",194:"#A3A2A5",208:"#C8C8C8",
        1001:"#FFCC99",1004:"#E8A87C",1006:"#C07A55",1008:"#7A4428"}

def bc(cid):
    h=COLORS.get(cid,"#A3A2A5").lstrip("#")
    return tuple(int(h[i:i+2],16)/255 for i in(0,2,4))

def procedural(av,name):
    sc=av.get("scales",{}); bco=av.get("bodyColors",{})
    rt=av.get("playerAvatarType","R6")
    W=sc.get("width",1);H=sc.get("height",1);HD=sc.get("head",1);D=sc.get("depth",1)
    p=[]
    if rt!="R15":
        p=[("Head",bco.get("headColorId",1004),0,5.6*H,0,1.2*HD,1.2*HD,1.2*HD),
           ("Torso",bco.get("torsoColorId",23),0,3*H,0,2*W,2*H,D),
           ("LArm",bco.get("leftArmColorId",1004),-1.5*W,3*H,0,W,2*H,D),
           ("RArm",bco.get("rightArmColorId",1004),1.5*W,3*H,0,W,2*H,D),
           ("LLeg",bco.get("leftLegColorId",194),-0.5*W,H,0,W,2*H,D),
           ("RLeg",bco.get("rightLegColorId",194),0.5*W,H,0,W,2*H,D)]
    else:
        p=[("Head",bco.get("headColorId",1004),0,6.6*H,0,1.2*HD,1.2*HD,1.1*HD),
           ("UpTorso",bco.get("torsoColorId",23),0,5.3*H,0,2*W,1.4*H,D),
           ("LoTorso",bco.get("torsoColorId",23),0,4.2*H,0,1.8*W,0.9*H,0.9*D),
           ("LUpArm",bco.get("leftArmColorId",1004),-1.5*W,5.3*H,0,0.9*W,1.2*H,0.9*D),
           ("LLoArm",bco.get("leftArmColorId",1004),-1.5*W,3.85*H,0,0.85*W,1.1*H,0.85*D),
           ("RUpArm",bco.get("rightArmColorId",1004),1.5*W,5.3*H,0,0.9*W,1.2*H,0.9*D),
           ("RLoArm",bco.get("rightArmColorId",1004),1.5*W,3.85*H,0,0.85*W,1.1*H,0.85*D),
           ("LUpLeg",bco.get("leftLegColorId",194),-0.55*W,3.1*H,0,0.9*W,1.3*H,0.9*D),
           ("LLoLeg",bco.get("leftLegColorId",194),-0.55*W,1.65*H,0,0.85*W,1.2*H,0.85*D),
           ("RUpLeg",bco.get("rightLegColorId",194),0.55*W,3.1*H,0,0.9*W,1.3*H,0.9*D),
           ("RLoLeg",bco.get("rightLegColorId",194),0.55*W,1.65*H,0,0.85*W,1.2*H,0.85*D)]
    def box(cx,cy,cz,w,h,d):
        hx,hy,hz=w/2,h/2,d/2
        v=[(cx-hx,cy-hy,cz-hz),(cx+hx,cy-hy,cz-hz),(cx+hx,cy+hy,cz-hz),(cx-hx,cy+hy,cz-hz),
           (cx-hx,cy-hy,cz+hz),(cx+hx,cy-hy,cz+hz),(cx+hx,cy+hy,cz+hz),(cx-hx,cy+hy,cz+hz)]
        f=[(1,2,3,4),(5,8,7,6),(1,5,6,2),(2,6,7,3),(3,7,8,4),(4,8,5,1)]
        return v,f
    obj=["# "+name,"mtllib avatar.mtl",""]; mtl=["# Materials",""]; mats=set(); vo=1
    for pn,cid,cx,cy,cz,pw,ph,pd in p:
        r,g,b=bc(cid); mat=f"m{cid}"
        if mat not in mats:
            mats.add(mat); mtl+=[f"newmtl {mat}",f"Kd {r:.4f} {g:.4f} {b:.4f}",""]
        verts,faces=box(cx,cy,cz,pw,ph,pd)
        obj+=[f"o {pn}",f"usemtl {mat}"]
        for vx,vy,vz in verts: obj.append(f"v {vx:.5f} {vy:.5f} {vz:.5f}")
        for face in faces: obj.append("f "+" ".join(str(vo+i-1) for i in face))
        obj.append(""); vo+=len(verts)
    return "\n".join(obj),"\n".join(mtl)

# ─────────────────────────────────────────────────────────────────

@app.get("/health")
def health():
    return jsonify({"status":"ok","cookie_set":bool(COOKIE),"maintenance":MAINTENANCE})


@app.get("/api/audio/ping")
def audio_ping():
    return jsonify({"status":"audio ok"})
@app.get("/api/avatar/info")
def avatar_info():
    user=request.args.get("user","")
    if not user: return jsonify({"error":"user required"}),400
    try:
        cached=cache_get(f"info_{user}")
        if cached: return jsonify(cached)
        uid=resolve(user)
        info=rget(f"https://users.roblox.com/v1/users/{uid}")
        av=rget(f"https://avatar.roblox.com/v1/users/{uid}/avatar")
        th=rget(f"https://thumbnails.roblox.com/v1/users/avatar?userIds={uid}&size=420x420&format=Png")
        result={"userId":uid,"username":info.get("name"),"displayName":info.get("displayName"),
            "created":info.get("created"),"rigType":av.get("playerAvatarType"),
            "scales":av.get("scales"),"bodyColors":av.get("bodyColors"),
            "assets":av.get("assets",[]),
            "thumbnailUrl":(th.get("data") or [{}])[0].get("imageUrl"),
            "profileUrl":f"https://www.roblox.com/users/{uid}/profile"}
        cache_set(f"info_{user}",result)
        return jsonify(result)
    except Exception as e: return jsonify({"error":str(e)}),500

@app.get("/api/avatar/3d-urls")
def avatar_3d_urls():
    user=request.args.get("user","")
    if not user: return jsonify({"error":"user required"}),400
    try:
        uid=resolve(user)
        url=get_3d_manifest(uid)
        if not url: return jsonify({"error":"3D thumbnail tidak tersedia","hints":["Coba lagi dalam 30 detik","Avatar mungkin R6"]}),503
        m=rget(url)
        return jsonify({"userId":uid,"objUrl":m.get("obj"),"mtlUrl":m.get("mtl"),"textures":m.get("textures",[])})
    except Exception as e: return jsonify({"error":str(e)}),500

def fix_url(url):
    if url and not url.startswith("http"):
        return f"https://t2.rbxcdn.com/{url}"
    return url

@app.get("/api/avatar/download-full")
def avatar_download_full():
    user=request.args.get("user","")
    if not user: return jsonify({"error":"user required"}),400
    try:
        uid=resolve(user)
        info=rget(f"https://users.roblox.com/v1/users/{uid}")
        name=info.get("name",str(uid))
        manifest_url=get_3d_manifest(uid)
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as zf:
            if manifest_url:
                m=rget(manifest_url)
                if m.get("obj"): zf.writestr(f"{name}.obj",rget_cdn(fix_url(m["obj"])))
                if m.get("mtl"): zf.writestr(f"{name}.mtl",rget_cdn(fix_url(m["mtl"])))
                for i,tx in enumerate(m.get("textures",[])):
                    try: zf.writestr(f"textures/texture_{i}.png",rget_cdn(fix_url(tx)))
                    except: pass
                zf.writestr("README.txt",f"Avatar: {name}\nImport {name}.obj\nTextures ada di folder textures/\nDi Prisma 3D: Import OBJ -> Material -> load texture\nDi Nomad Sculpt: Import -> OBJ -> Material -> Base Color -> pilih texture")
            else:
                av=rget(f"https://avatar.roblox.com/v1/users/{uid}/avatar")
                obj_t,mtl_t=procedural(av,name)
                zf.writestr(f"{name}.obj",obj_t)
                zf.writestr(f"{name}.mtl",mtl_t)
                try:
                    th=rget(f"https://thumbnails.roblox.com/v1/users/avatar?userIds={uid}&size=420x420&format=Png")
                    tu=(th.get("data") or [{}])[0].get("imageUrl")
                    if tu: zf.writestr(f"textures/{name}_preview.png",rget_bytes(tu))
                except: pass
                zf.writestr("README.txt",f"Avatar: {name} (Procedural)\nOBJ: geometry dengan warna dasar\nTextures: preview thumbnail di textures/\nDi Prisma 3D: Import OBJ -> Material -> load texture\nDi Nomad Sculpt: Import -> OBJ")
        buf.seek(0)
        return Response(buf.read(),mimetype="application/zip",
            headers={"Content-Disposition":f'attachment; filename="{name}_full.zip"'})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.get("/api/avatar/procedural-download")
def avatar_procedural_download():
    user=request.args.get("user","")
    if not user: return jsonify({"error":"user required"}),400
    try:
        uid=resolve(user)
        av=rget(f"https://avatar.roblox.com/v1/users/{uid}/avatar")
        info=rget(f"https://users.roblox.com/v1/users/{uid}")
        name=info.get("name",str(uid))
        obj_t,mtl_t=procedural(av,name)
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{name}.obj",obj_t)
            zf.writestr(f"{name}.mtl",mtl_t)
            try:
                th=rget(f"https://thumbnails.roblox.com/v1/users/avatar?userIds={uid}&size=420x420&format=Png")
                tu=(th.get("data") or [{}])[0].get("imageUrl")
                if tu: zf.writestr(f"textures/{name}_preview.png",rget_bytes(tu))
            except: pass
        buf.seek(0)
        return Response(buf.read(),mimetype="application/zip",
            headers={"Content-Disposition":f'attachment; filename="{name}_procedural.zip"'})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.get("/api/catalog/info")
def catalog_info():
    aid = request.args.get("asset_id","")
    if not aid: return jsonify({"error":"asset_id required"}),400
    try:
        s   = get_scraper()
        aid_int = int(aid)
        item = {}

        # Coba 3 endpoint berbeda
        endpoints = [
            f"https://catalog.roblox.com/v1/catalog/items/{aid_int}/details?itemType=Asset",
            f"https://economy.roblox.com/v2/assets/{aid_int}/details",
            f"https://apis.roblox.com/assets/v1/assets/{aid_int}",
        ]
        for ep in endpoints:
            try:
                r = s.get(ep, timeout=10)
                if r.status_code == 200:
                    item = r.json(); break
            except: continue

        # Fallback POST
        if not item:
            try:
                d = rpost("https://catalog.roblox.com/v1/catalog/items/details",
                          {"items":[{"itemType":"Asset","id":aid_int}]})
                item = (d.get("data") or [{}])[0]
            except: pass

        if not item:
            return jsonify({"error":f"Asset {aid} tidak ditemukan atau tidak dapat diakses"}),404

        # Thumbnail
        try:
            th    = s.get(f"https://thumbnails.roblox.com/v1/assets?assetIds={aid}&size=420x420&format=Png",timeout=10).json()
            thumb = (th.get("data") or [{}])[0].get("imageUrl")
        except: thumb = None

        name    = item.get("name") or item.get("displayName") or f"Asset {aid}"
        creator = item.get("creatorName") or item.get("creator",{}).get("name","")
        price   = item.get("price") or item.get("priceInRobux")
        atype   = item.get("assetType") or item.get("assetTypeId")

        return jsonify({"assetId":aid_int,"name":name,"assetType":atype,
            "creatorName":creator,"price":price,
            "thumbnailUrl":thumb,"catalogUrl":f"https://www.roblox.com/catalog/{aid}"})
    except Exception as e: return jsonify({"error":str(e)}),500

@app.get("/api/catalog/download-full")
def catalog_download_full():
    aid = request.args.get("asset_id","")
    fmt = request.args.get("format","gltf").lower()  # "obj" atau "gltf"
    if not aid: return jsonify({"error":"asset_id required"}),400
    try:
        s = get_scraper()
        r = s.post("https://catalog.roblox.com/v1/catalog/items/details", json={"items":[{"itemType":"Asset","id":int(aid)}]}, timeout=10)
        d = r.json()
        item = (d.get("data") or [{}])[0]
        if not item: return jsonify({"error":f"Asset {aid} tidak ditemukan"}),404
        name = item.get("name",f"asset_{aid}")
        safe = "".join(c if c.isalnum() or c in" _-" else "_" for c in name).strip()

        # Download asset pakai scraper (bypass Cloudflare 403)
        raw_r = s.get(f"https://assetdelivery.roblox.com/v1/asset/?id={aid}", timeout=30)
        raw   = raw_r.content

        # Thumbnail
        try:
            th  = s.get(f"https://thumbnails.roblox.com/v1/assets?assetIds={aid}&size=420x420&format=Png",timeout=10).json()
            tu  = (th.get("data") or [{}])[0].get("imageUrl")
        except: tu = None

        buf = io.BytesIO()
        with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as zf:
            try:
                mesh  = parse_mesh(raw, name=safe)
                obj_t = mesh_to_obj(mesh, mtl_name=safe)

                if fmt == "gltf" and tu:
                    # GLTF: OBJ + MTL + texture PNG
                    tex = s.get(tu, timeout=15).content
                    zf.writestr(f"textures/{safe}.png", tex)
                    mtl_t = "newmtl default\nKd 0.8 0.8 0.8\nmap_Kd textures/" + safe + ".png\n"
                else:
                    # OBJ: geometry + material tanpa texture
                    mtl_t = "newmtl default\nKd 0.8 0.8 0.8\nKa 0.1 0.1 0.1\n"

                zf.writestr(f"{safe}.obj", obj_t)
                zf.writestr(f"{safe}.mtl", mtl_t)

            except Exception as me:
                # Fallback: simpan raw mesh + thumbnail
                ext = ".png" if raw[:4]==b"\x89PNG" else ".mesh"
                zf.writestr(f"{safe}{ext}", raw)
                if tu and fmt=="gltf":
                    try: zf.writestr(f"textures/{safe}_preview.png", s.get(tu,timeout=10).content)
                    except: pass
                zf.writestr("PARSE_ERROR.txt", f"Mesh parse gagal: {me}\nFile mentah disertakan.")

            zf.writestr("README.txt",
                f"Item  : {name}\nFormat: {fmt.upper()}\n\n"
                f"NOMAD SCULPT:\n  Files > Import > {safe}.obj\n"
                + (f"  Material > Base Color > textures/{safe}.png\n" if fmt=="gltf" else "")
                + f"\nPRISMA 3D:\n  + > Import > OBJ > {safe}.obj\n"
                + (f"  Material > Texture > textures/{safe}.png" if fmt=="gltf" else "")
            )

        buf.seek(0)
        fname = f"{safe}_{'gltf' if fmt=='gltf' else 'obj'}.zip"
        return Response(buf.read(), mimetype="application/zip",
            headers={"Content-Disposition":f'attachment; filename="{fname}"'})
    except Exception as e: return jsonify({"error":str(e)}),500

# Set True saat maintenance
MAINTENANCE = os.getenv("MAINTENANCE","false").lower() == "true"

@app.get("/")
def frontend():
    base = os.path.join(os.path.dirname(__file__),"..","frontend")
    if MAINTENANCE:
        mp = os.path.join(os.path.dirname(__file__),"maintenance.html")
        if os.path.exists(mp): return open(mp).read(),503,{"Content-Type":"text/html"}
    p = os.path.join(base,"index.html")
    if os.path.exists(p): return open(p).read(),200,{"Content-Type":"text/html"}
    return "<h1>Roblox Downloader</h1>"

@app.get("/maintenance")
def maintenance_preview():
    """Preview maintenance page langsung"""
    p=os.path.join(os.path.dirname(__file__),"maintenance.html")
    if os.path.exists(p): return open(p).read(),200,{"Content-Type":"text/html"}
    return "Maintenance page not found",404


@app.get("/api/avatar/smart-download")
def avatar_smart_download():
    user = request.args.get("user","")
    if not user: return jsonify({"error":"user required"}),400
    try:
        uid  = resolve(user)
        info = rget(f"https://users.roblox.com/v1/users/{uid}")
        av   = rget(f"https://avatar.roblox.com/v1/users/{uid}/avatar")
        name = info.get("name",str(uid))
        rig  = av.get("playerAvatarType","R6")
        buf  = io.BytesIO()
        with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as zf:
            method = ""
            manifest_url = None
            for _ in range(5):
                try:
                    d = rget(f"https://thumbnails.roblox.com/v1/users/avatar-3d?userId={uid}")
                    item = (d.get("data") or [None])[0]
                    if item and item.get("state")=="Completed":
                        manifest_url = item["imageUrl"]; break
                    if item and item.get("state")=="Blocked": break
                    time.sleep(3)
                except Exception as e:
                    if "401" in str(e) or "403" in str(e): break
                    time.sleep(2)

            if manifest_url:
                try:
                    m = rget(manifest_url)
                    obj_url = m.get("obj"); mtl_url = m.get("mtl"); txs = m.get("textures",[])
                    if obj_url: zf.writestr(f"{name}.obj", rget_bytes(obj_url)); method="real"
                    if mtl_url:
                        mt = rget_bytes(mtl_url).decode("utf-8","replace")
                        for i in range(len(txs)):
                            mt = re.sub(r"map_Kd\s+\S+", f"map_Kd textures/texture_{i}.png", mt, count=1)
                        zf.writestr(f"{name}.mtl", mt)
                    for i,tx in enumerate(txs):
                        try: zf.writestr(f"textures/texture_{i}.png", rget_cdn(fix_url(tx)))
                        except: pass
                except: manifest_url = None

            if not manifest_url:
                obj_t,mtl_t = procedural(av,name)
                mtl_t += f"\nmap_Kd textures/{name}_skin.png"
                zf.writestr(f"{name}.obj", obj_t)
                zf.writestr(f"{name}.mtl", mtl_t)
                method = f"procedural_{rig}"
                try:
                    th = rget(f"https://thumbnails.roblox.com/v1/users/avatar?userIds={uid}&size=420x420&format=Png")
                    tu = (th.get("data") or [{}])[0].get("imageUrl")
                    if tu: zf.writestr(f"textures/{name}_skin.png", rget_bytes(tu))
                except: pass

            WEAR={"Hat","HairAccessory","FaceAccessory","NeckAccessory","WaistAccessory","BackAccessory","Shirt","Pants","Face"}
            for asset in av.get("assets",[]):
                if asset.get("assetType",{}).get("name","") in WEAR:
                    try:
                        raw = rget_bytes(f"https://assetdelivery.roblox.com/v1/asset/?id={asset['id']}")
                        safe = "".join(c if c.isalnum() else "_" for c in asset["name"][:25])
                        ext = ".png" if raw[:4]==b"\x89PNG" else ".mesh"
                        zf.writestr(f"accessories/{safe}{ext}", raw)
                    except: pass

            zf.writestr("README.txt",
                f"Avatar: {name} | Rig: {rig} | Method: {method}\n\n"
                f"NOMAD SCULPT:\n  Files > Import > {name}.obj\n  Material > Base Color > textures/\n\n"
                f"PRISMA 3D:\n  + > Import > OBJ > {name}.obj\n  Material > Texture > textures/\n\n"
                f"{'NOTE: Procedural model (R6 tidak didukung Roblox 3D API)' if 'procedural' in method else 'Real mesh dari Roblox CDN'}")
        buf.seek(0)
        return Response(buf.read(),mimetype="application/zip",
            headers={"Content-Disposition":f'attachment; filename="{name}_avatar.zip"'})
    except Exception as e: return jsonify({"error":str(e)}),500

def get_hash_url(h):
    """Port dari global.js Faizdzn - convert hash ke rbxcdn URL"""
    st = 31
    for ch in h:
        st ^= ord(ch)
    return f"https://t{st % 8}.rbxcdn.com/{h}"

def get_obj_urls(manifest):
    """Convert semua hash di manifest ke URL CDN yang benar"""
    obj_url = get_hash_url(manifest["obj"]) if not manifest["obj"].startswith("http") else manifest["obj"]
    mtl_url = get_hash_url(manifest["mtl"]) if not manifest["mtl"].startswith("http") else manifest["mtl"]
    tex_hashes = manifest.get("textures", [])
    tex_urls   = [get_hash_url(h) if not h.startswith("http") else h for h in tex_hashes]
    return obj_url, mtl_url, tex_hashes, tex_urls

def fix_mtl_textures(mtl_text, tex_hashes, tex_filenames):
    """Replace hash di MTL dengan nama file yang benar (port dari str_replace JS)"""
    for h, fname in zip(tex_hashes, tex_filenames):
        mtl_text = mtl_text.replace(h, fname)
    return mtl_text

@app.get("/api/v2/avatar")
def avatar_v2():
    """Avatar download - real mesh + UV texture"""
    user   = request.args.get("user","")
    fmt    = request.args.get("format","gltf").lower()  # "obj" atau "gltf"
    if not user: return jsonify({"error":"user required"}),400
    try:
        uid  = resolve(user)
        info = rget(f"https://users.roblox.com/v1/users/{uid}")
        av   = rget(f"https://avatar.roblox.com/v1/users/{uid}/avatar")
        name = info.get("name", str(uid))
        rig  = av.get("playerAvatarType","R6")
        s    = get_scraper()

        manifest_url = get_3d_manifest(uid)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as zf:
            if manifest_url:
                # Fetch manifest JSON
                m = s.get(manifest_url, timeout=15).json()

                # Convert hash -> CDN URL
                obj_url, mtl_url, tex_hashes, tex_urls = get_obj_urls(m)
                tex_names = [f"{name}_Tex{i+1}.png" for i in range(len(tex_hashes))]

                # Download OBJ
                obj_data = s.get(obj_url, timeout=30).text

                # Download MTL + fix texture paths
                mtl_raw   = s.get(mtl_url, timeout=15).text
                mtl_fixed = fix_mtl_textures(mtl_raw, tex_hashes, tex_names)

                zf.writestr(f"{name}.obj", obj_data)
                zf.writestr(f"{name}.mtl", mtl_fixed)

                # GLTF mode: include textures. OBJ mode: geometry only
                if fmt == "gltf":
                    for i, tex_url in enumerate(tex_urls):
                        try:
                            tb = s.get(tex_url, timeout=20)
                            if tb.status_code == 200:
                                zf.writestr(tex_names[i], tb.content)
                        except: pass

                method = "real_mesh"
            else:
                # Fallback procedural
                obj_t, mtl_t = procedural(av, name)
                mtl_t += f"\nmap_Kd {name}_skin.png"
                zf.writestr(f"{name}.obj", obj_t)
                zf.writestr(f"{name}.mtl", mtl_t)
                try:
                    th  = rget(f"https://thumbnails.roblox.com/v1/users/avatar?userIds={uid}&size=420x420&format=Png")
                    thu = (th.get("data") or [{}])[0].get("imageUrl")
                    if thu: zf.writestr(f"{name}_skin.png", rget_bytes(thu))
                except: pass
                method = f"procedural_{rig}"

            zf.writestr("README.txt",
                f"Avatar : {name}\nRig    : {rig}\nMethod : {method}\n\n"
                f"NOMAD SCULPT:\n  Files > Import > {name}.obj\n"
                f"  Tap mesh > Material > Base Color > load texture PNG\n\n"
                f"PRISMA 3D:\n  + > Import > OBJ > {name}.obj\n"
                f"  Material > Texture > pilih texture PNG")

        buf.seek(0)
        return Response(buf.read(), mimetype="application/zip",
            headers={"Content-Disposition":f'attachment; filename="{name}_avatar.zip"'})
    except Exception as e: return handle_roblox_error(e, "avatar_download")

@app.get("/api/v2/item")
def item_v2():
    """Item download - cloudscraper + format OBJ/GLTF (Faizdzn method)"""
    aid = request.args.get("id","")
    fmt = request.args.get("format","gltf").lower()
    if not aid: return jsonify({"error":"id required"}),400
    try:
        file_id = int(aid)
        s = get_scraper()

        # Get item name
        try:
            d2 = rpost("https://catalog.roblox.com/v1/catalog/items/details",{"items":[{"itemType":"Asset","id":file_id}]})
            item_name = (d2.get("data") or [{}])[0].get("name", str(file_id))
            safe_name = "".join(c if c.isalnum() or c in" _-" else "_" for c in item_name).strip()
        except:
            item_name = str(file_id); safe_name = str(file_id)

        # Get 3D manifest pakai scraper
        r3d = s.get(f"https://thumbnails.roblox.com/v1/assets-thumbnail-3d?assetId={file_id}", timeout=15)
        if r3d.status_code != 200:
            return jsonify({"error":f"3D thumbnail item error {r3d.status_code}"}),503
        d = r3d.json()
        manifest_url = d.get("imageUrl")
        if not manifest_url:
            return jsonify({"error":"3D thumbnail item tidak tersedia — item ini mungkin tidak punya model 3D"}),503

        # Fetch manifest JSON
        manifest = s.get(manifest_url, timeout=15).json()

        # Convert hash -> CDN URL
        obj_url, mtl_url, tex_hashes, tex_urls = get_obj_urls(manifest)
        tex_names = [f"{safe_name}_Tex{i+1}.png" for i in range(len(tex_hashes))]

        # Download OBJ + MTL pakai scraper
        obj_data  = s.get(obj_url, timeout=30).text
        mtl_raw   = s.get(mtl_url, timeout=15).text
        mtl_fixed = fix_mtl_textures(mtl_raw, tex_hashes, tex_names)

        buf = io.BytesIO()
        with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{safe_name}.obj", obj_data)
            zf.writestr(f"{safe_name}.mtl", mtl_fixed)

            if fmt == "gltf":
                # Include all UV textures
                for i, tex_url in enumerate(tex_urls):
                    try:
                        tb = s.get(tex_url, timeout=20)
                        if tb.status_code == 200:
                            zf.writestr(tex_names[i], tb.content)
                    except: pass
            # OBJ mode: no textures

            zf.writestr("README.txt",
                f"Item  : {item_name}\nFormat: {fmt.upper()}\n\n"
                f"NOMAD SCULPT:\n  Files > Import > {safe_name}.obj\n"
                + (f"  Material > Base Color > {tex_names[0] if tex_names else ''}\n" if fmt=="gltf" else "")
                + f"\nPRISMA 3D:\n  + > Import > OBJ > {safe_name}.obj"
            )

        buf.seek(0)
        fname = f"{safe_name}_{'gltf' if fmt=='gltf' else 'obj'}.zip"
        return Response(buf.read(), mimetype="application/zip",
            headers={"Content-Disposition":f'attachment; filename="{fname}"'})
    except Exception as e: return handle_roblox_error(e, "catalog_item_download")

@app.get("/api/proxy/avatar-3d")
def proxy_avatar_3d():
    """Proxy endpoint - browser call ini, server fetch ke Roblox pakai cookie"""
    uid = request.args.get("uid","")
    if not uid: return jsonify({"error":"uid required"}),400
    try:
        h = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://www.roblox.com/",
        }
        # Jangan kirim cookie ke thumbnail - menyebabkan 401
        with httpx.Client(timeout=15, follow_redirects=True) as c:
            r = c.get(f"https://thumbnails.roblox.com/v1/users/avatar-3d?userId={uid}", headers=h)
            if r.is_success: return jsonify(r.json()), r.status_code
        # Fallback allorigins
        proxy = f"https://api.allorigins.win/get?url=https://thumbnails.roblox.com/v1/users/avatar-3d?userId={uid}"
        with httpx.Client(timeout=15) as c:
            r = c.get(proxy)
            import json as _j
            return jsonify(_j.loads(r.json()["contents"])), 200
    except Exception as e:
        return jsonify({"error":str(e)}),500

@app.get("/api/proxy/fetch")
def proxy_fetch():
    """General proxy - browser minta server untuk fetch URL apapun dari Roblox CDN"""
    url = request.args.get("url","")
    if not url: return jsonify({"error":"url required"}),400
    if "roblox.com" not in url and "rbxcdn.com" not in url:
        return jsonify({"error":"only roblox domains allowed"}),403
    try:
        h = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://www.roblox.com/",
        }
        if COOKIE: h["Cookie"] = f".ROBLOSECURITY={COOKIE}"
        fmt = request.args.get("fmt","json")
        with httpx.Client(timeout=20, follow_redirects=True) as c:
            r = c.get(url, headers=h)
            if fmt == "bytes":
                import base64
                return jsonify({"b64": base64.b64encode(r.content).decode()})
            return jsonify(r.json())
    except Exception as e:
        return jsonify({"error":str(e)}),500


@app.get("/api/audio/info")
def audio_info():
    aid = request.args.get("id","")
    if not aid: return jsonify({"error":"id required"}),400
    try:
        d = rget(f"https://economy.roblox.com/v2/assets/{aid}/details")
        if d.get("AssetTypeId") != 3:
            return jsonify({"error":"Bukan audio asset"}),400
        creator = d.get("Creator",{})
        return jsonify({
            "assetId": int(aid),
            "name": d.get("Name",""),
            "creator": creator.get("Name",""),
            "creatorId": creator.get("CreatorTargetId"),
            "creatorType": creator.get("CreatorType",""),
            "created": d.get("Created",""),
            "robloxUrl": f"https://www.roblox.com/library/{aid}"
        })
    except Exception as e: return jsonify({"error":str(e)}),500

@app.get("/api/audio/download")
def audio_download():
    aid = request.args.get("id","")
    if not aid: return jsonify({"error":"id required"}),400
    try:
        d = rget(f"https://economy.roblox.com/v2/assets/{aid}/details")
        if d.get("AssetTypeId") != 3:
            return jsonify({"error":"Bukan audio asset"}),400
        creator = d.get("Creator",{})
        name = d.get("Name", str(aid))
        safe = "".join(c if c.isalnum() or c in " _-" else "_" for c in name).strip()

        # Download audio
        import gzip
        s = get_scraper()
        r = s.get(f"https://assetdelivery.roblox.com/v1/asset/?id={aid}", timeout=30)
        raw = r.content
        try:
            raw = gzip.decompress(raw)
        except: pass

        # Build ZIP
        buf = io.BytesIO()
        with zipfile.ZipFile(buf,"w",zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{safe}.ogg", raw)
            zf.writestr("INFO.txt",
                f"Audio: {name}\n"
                f"Asset ID: {aid}\n"
                f"Creator: {creator.get('Name','')}\n"
                f"Creator ID: {creator.get('CreatorTargetId','')}\n"
                f"Creator Type: {creator.get('CreatorType','')}\n"
                f"Roblox URL: https://www.roblox.com/library/{aid}\n"
            )
            zf.writestr("WARNING.txt",
                "⚠ COPYRIGHT WARNING\n"
                "====================\n"
                "This audio file may be protected by copyright.\n"
                "Only use audio you own or have permission to use.\n"
                "Do not redistribute without the creator's permission.\n"
                "The developer of this tool is not responsible for misuse.\n"
            )
        buf.seek(0)
        return Response(buf.read(), mimetype="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{safe}_audio.zip"'})
    except Exception as e: return handle_roblox_error(e, "audio_download")

# Railway / production WSGI entry point
application = app

@app.get("/api/audio/test")
def audio_test():
    return jsonify({"status":"audio ok"})

@app.get("/api/debug/asset-raw")
def debug_asset_raw():
    """TEMPORARY - inspect/download raw asset file"""
    aid = request.args.get("id","")
    raw = request.args.get("raw","")
    if not aid: return jsonify({"error":"id required"}),400
    try:
        s = get_scraper()
        r = s.get(f"https://assetdelivery.roblox.com/v1/asset/?id={aid}", timeout=30)
        content = r.content
        if raw == "1":
            return Response(content, mimetype="application/octet-stream",
                headers={"Content-Disposition": f'attachment; filename="{aid}.rbxm"'})
        return jsonify({
            "status": r.status_code,
            "content_type": r.headers.get("content-type"),
            "size": len(content),
            "first_200_bytes": content[:200].decode("utf-8","replace"),
            "first_50_hex": content[:50].hex()
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.get("/api/v2/model/info")
def model_info():
    """MVP: parse RBXM (Model assets from create.roblox.com), return manifest.
    Supports only MeshPart+Part, no UnionOperation, <=30 parts.
    Rotation is identity-fallback (not yet decoded)."""
    aid = request.args.get("id", "")
    if not aid: return jsonify({"error": "id required"}), 400

    try:
        file_id = int(aid)
        s = get_scraper()
        r = s.get(f"https://assetdelivery.roblox.com/v1/asset/?id={file_id}", timeout=25)
        if r.status_code != 200:
            return jsonify({"error": f"Gagal download asset (HTTP {r.status_code})", "supported": False}), 502

        data = r.content

        # Detect format: XML (rbxmx) vs Binary (rbxm)
        is_xml = data[:20].lstrip().startswith(b'<roblox') and not data[:8] == b'<roblox!'

        if is_xml:
            try:
                parsed = parse_rbxmx(data)
            except Exception as e:
                return jsonify({"error": f"Gagal parse XML: {str(e)}", "supported": False}), 422
            meshpart_count = parsed["meshPartCount"]
            part_count = parsed["partCount"]
            union_count = parsed["unionCount"]
            total_parts = meshpart_count + part_count
            pre_parts = parsed["parts"]
        else:
            try:
                chunks, num_types, num_instances = parse_chunks(data)
            except RBXMParseError as e:
                return jsonify({"error": str(e), "supported": False}), 422

            type_map = parse_inst_chunks(chunks)

            meshpart_tid = part_tid = union_tid = None
            for tid, info in type_map.items():
                cn = info["class_name"]
                if cn == "MeshPart": meshpart_tid = tid
                elif cn == "Part": part_tid = tid
                elif cn == "UnionOperation": union_tid = tid

            meshpart_count = type_map.get(meshpart_tid, {}).get("count", 0) if meshpart_tid is not None else 0
            part_count = type_map.get(part_tid, {}).get("count", 0) if part_tid is not None else 0
            union_count = type_map.get(union_tid, {}).get("count", 0) if union_tid is not None else 0
            total_parts = meshpart_count + part_count
            pre_parts = None

        total_parts_all = meshpart_count + part_count + union_count
        reasons = []
        if total_parts_all > 100:
            reasons.append(f"Asset terlalu kompleks ({total_parts_all} parts, maksimum 100) — Cuh... itu terlalu banyak mesh, aku nggak sanggup handle itu 😅")
        if total_parts_all == 0:
            reasons.append("Tidak ada MeshPart/Part/Union - asset ini mungkin bukan 3D Model (cek tipe asset)")

        supported = (0 < total_parts_all <= 500)

        parts = []

        def decode_class(type_id, count, class_name):
            if type_id is None or count == 0:
                return
            _, size_raw = find_prop_chunk(chunks, type_id, "size")
            sizes = decode_vector3_array(size_raw, count) if size_raw else [(1.0,1.0,1.0)]*count

            _, cf_raw = find_prop_chunk(chunks, type_id, "CFrame")
            if cf_raw:
                positions, rotations = decode_cframe_full(cf_raw, count)
            else:
                positions, rotations = [(0.0,0.0,0.0)]*count, [{"status":"identity-fallback","value":[1,0,0,0,1,0,0,0,1],"rawRotationId":None}]*count

            _, name_raw = find_prop_chunk(chunks, type_id, "Name")
            names = decode_string_array(name_raw, count) if name_raw else [f"{class_name}{i}" for i in range(count)]

            _, color_raw = find_prop_chunk(chunks, type_id, "Color3uint8")
            colors = decode_color3uint8_array(color_raw, count) if color_raw else [(163,162,165)]*count

            mesh_ids = [None]*count
            texture_ids = [None]*count
            if class_name == "MeshPart":
                _, mid_raw = find_prop_chunk(chunks, type_id, "MeshId")
                if mid_raw:
                    raw_ids = decode_string_array(mid_raw, count)
                    mesh_ids = [m if m else None for m in raw_ids]
                _, tex_raw = find_prop_chunk(chunks, type_id, "TextureID")
                if tex_raw:
                    raw_tex = decode_string_array(tex_raw, count)
                    texture_ids = [t if t else None for t in raw_tex]

            for i in range(count):
                parts.append({
                    "name": names[i],
                    "className": class_name,
                    "meshId": mesh_ids[i],
                    "position": {"status": "decoded", "value": [round(v,4) for v in positions[i]]},
                    "size": {"status": "decoded", "value": [round(v,4) for v in sizes[i]]},
                    "rotation": rotations[i],
                    "color": {"status": "best-effort", "value": list(colors[i])},
                    "textureId": texture_ids[i]
                })

        decode_class(meshpart_tid, meshpart_count, "MeshPart")
        decode_class(part_tid, part_count, "Part")

        # Decode UnionOperation - ambil AssetId dan size/CFrame/color/name
        if union_tid is not None and union_count > 0:
            _, size_raw = find_prop_chunk(chunks, union_tid, "size")
            sizes = decode_vector3_array(size_raw, union_count) if size_raw else [(1.0,1.0,1.0)]*union_count

            _, cf_raw = find_prop_chunk(chunks, union_tid, "CFrame")
            if cf_raw:
                u_positions, u_rotations = decode_cframe_full(cf_raw, union_count)
            else:
                u_positions = [(0.0,0.0,0.0)]*union_count
                u_rotations = [{"status":"identity-fallback","value":[1,0,0,0,1,0,0,0,1],"rawRotationId":None}]*union_count

            _, name_raw = find_prop_chunk(chunks, union_tid, "Name")
            u_names = decode_string_array(name_raw, union_count) if name_raw else [f"Union{i}" for i in range(union_count)]

            _, color_raw = find_prop_chunk(chunks, union_tid, "Color3uint8")
            u_colors = decode_color3uint8_array(color_raw, union_count) if color_raw else [(163,162,165)]*union_count

            _, assetid_raw = find_prop_chunk(chunks, union_tid, "AssetId")
            if assetid_raw:
                u_asset_ids = decode_string_array(assetid_raw, union_count)
            else:
                u_asset_ids = [None]*union_count

            for i in range(union_count):
                # Extract numeric ID from URL like "https://www.roblox.com//asset/?id=123456"
                aid_raw = u_asset_ids[i] if u_asset_ids[i] else ""
                aid_match = re.search(r"(\d{4,})", aid_raw)
                asset_id_num = aid_match.group(1) if aid_match else None

                parts.append({
                    "name": u_names[i],
                    "className": "UnionOperation",
                    "meshId": None,
                    "textureId": None,
                    "unionAssetId": asset_id_num,
                    "position": {"status": "decoded", "value": [round(v,4) for v in u_positions[i]]},
                    "size": {"status": "decoded", "value": [round(v,4) for v in sizes[i]]},
                    "rotation": u_rotations[i],
                    "color": {"status": "best-effort", "value": list(u_colors[i])}
                })

        return jsonify({
            "assetId": file_id,
            "supported": supported,
            "reasons": reasons,
            "meshPartCount": meshpart_count,
            "partCount": part_count,
            "unionCount": union_count,
            "totalParts": total_parts_all,
            "parts": parts
        })

    except ValueError:
        return jsonify({"error": "id harus berupa angka"}), 400
    except Exception as e:
        return handle_roblox_error(e, "model_info")


@app.get("/api/v2/model/mesh")
def model_mesh():
    """Fetch raw .mesh by ID (dari MeshId di manifest model/info), convert ke OBJ.
    Dipanggil per-part dari browser saat assembly GLB untuk Model 3D create.roblox.com."""
    raw_id = request.args.get("meshId", "")
    if not raw_id:
        return jsonify({"error": "meshId required"}), 400

    # meshId bisa berupa "rbxassetid://123456" atau angka polos
    try:
        clean_id = raw_id.replace("rbxassetid://", "").strip()
        mesh_asset_id = int(clean_id)
    except ValueError:
        return jsonify({"error": "meshId tidak valid"}), 400

    try:
        s = get_scraper()
        r = s.get(f"https://assetdelivery.roblox.com/v1/asset/?id={mesh_asset_id}", timeout=25)
        if r.status_code != 200:
            return jsonify({"error": f"Gagal download mesh (HTTP {r.status_code})"}), 502

        raw = r.content
        if not raw.startswith(b"version"):
            return jsonify({"error": "Bukan format .mesh yang dikenali"}), 422

        mesh = parse_mesh(raw, name=f"mesh_{mesh_asset_id}")
        obj_text = mesh_to_obj(mesh, mtl_name=f"mesh_{mesh_asset_id}")

        return jsonify({
            "meshAssetId": mesh_asset_id,
            "vertexCount": len(mesh.vertices),
            "faceCount": len(mesh.faces),
            "meshVersion": mesh.version,
            "obj": obj_text
        })
    except Exception as e:
        return handle_roblox_error(e, "model_mesh")


@app.get("/api/debug/thumbnail3d")
def debug_thumbnail3d():
    """TEMP: cek apakah Roblox punya 3D thumbnail render untuk asset Model (bypass CSG decode)."""
    aid = request.args.get("id", "")
    if not aid: return jsonify({"error": "id required"}), 400
    try:
        s = get_scraper()
        r = s.get(f"https://thumbnails.roblox.com/v1/assets-thumbnail-3d?assetId={aid}", timeout=20)
        return jsonify({"status": r.status_code, "body": r.text[:1000]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.get("/api/v2/model/texture")
def model_texture():
    """Fetch texture image untuk MeshPart (dari TextureID di manifest). Proxy langsung sebagai binary image."""
    raw_id = request.args.get("textureId", "")
    if not raw_id:
        return jsonify({"error": "textureId required"}), 400
    m = re.search(r"(\d{4,})", raw_id)
    if not m:
        return jsonify({"error": "textureId tidak valid - tidak ada angka ID ditemukan"}), 400
    tex_asset_id = int(m.group(1))

    try:
        s = get_scraper()
        r = s.get(f"https://assetdelivery.roblox.com/v1/asset/?id={tex_asset_id}", timeout=20)
        if r.status_code != 200:
            return jsonify({"error": f"Gagal download texture (HTTP {r.status_code})"}), 502

        content = r.content
        ctype = "image/png"
        if content[:2] == b"\xff\xd8":
            ctype = "image/jpeg"
        elif content[:8] == b"\x89PNG\r\n\x1a\n":
            ctype = "image/png"

        return Response(content, mimetype=ctype)
    except Exception as e:
        return handle_roblox_error(e, "model_texture")


@app.get("/api/v2/model/download")
def model_download():
    """Download raw .rbxm file langsung dari Roblox - no limit, no conversion."""
    aid = request.args.get("id", "")
    if not aid:
        return jsonify({"error": "id required"}), 400

    m = re.search(r"(\d{5,})", aid)
    if not m:
        return jsonify({"error": "id tidak valid"}), 400
    asset_id = int(m.group(1))

    try:
        s = get_scraper()
        r = s.get(f"https://assetdelivery.roblox.com/v1/asset/?id={asset_id}", timeout=30)
        if r.status_code != 200:
            return jsonify({"error": f"Gagal download asset (HTTP {r.status_code})"}), 502

        content = r.content
        if not content:
            return jsonify({"error": "File kosong"}), 502

        return Response(
            content,
            mimetype="application/octet-stream",
            headers={
                "Content-Disposition": f'attachment; filename="{asset_id}.rbxm"',
                "Content-Length": str(len(content))
            }
        )
    except Exception as e:
        return handle_roblox_error(e, "model_download")


@app.post("/api/v2/model/convert")
def model_convert():
    """Parse uploaded .rbxm file dan return manifest (sama seperti model/info tapi dari upload, bukan fetch Roblox).
    Tidak butuh cookie/scraper - file di-upload langsung oleh user."""
    if 'file' not in request.files:
        return jsonify({"error": "Tidak ada file yang di-upload"}), 400

    f = request.files['file']
    if not f.filename:
        return jsonify({"error": "Nama file kosong"}), 400
    if not f.filename.lower().endswith(('.rbxm', '.rbxmx')):
        return jsonify({"error": "Hanya file .rbxm yang didukung"}), 400

    try:
        data = f.read()
        if len(data) == 0:
            return jsonify({"error": "File kosong"}), 400
        if len(data) > 50 * 1024 * 1024:  # 50MB max
            return jsonify({"error": "File terlalu besar (maks 50MB)"}), 400

        # Auto-detect format: XML (.rbxmx) vs Binary (.rbxm)
        is_xml = data[:20].lstrip().startswith(b'<roblox') and data[:8] != b'<roblox!'

        pre_parts = None
        if is_xml:
            try:
                parsed = parse_rbxmx(data)
                meshpart_count = parsed["meshPartCount"]
                part_count = parsed["partCount"]
                union_count = parsed["unionCount"]
                total_parts = meshpart_count + part_count
                pre_parts = parsed["parts"]
            except Exception as e:
                return jsonify({"error": f"Gagal parse XML: {str(e)}", "supported": False}), 422
        else:
            try:
                chunks, num_types, num_instances = parse_chunks(data)
            except RBXMParseError as e:
                return jsonify({"error": str(e), "supported": False}), 422

            type_map = parse_inst_chunks(chunks)

            meshpart_tid = part_tid = union_tid = None
            for tid, info in type_map.items():
                cn = info["class_name"]
                if cn == "MeshPart": meshpart_tid = tid
                elif cn == "Part": part_tid = tid
                elif cn == "UnionOperation": union_tid = tid

            meshpart_count = type_map.get(meshpart_tid, {}).get("count", 0) if meshpart_tid is not None else 0
            part_count = type_map.get(part_tid, {}).get("count", 0) if part_tid is not None else 0
            union_count = type_map.get(union_tid, {}).get("count", 0) if union_tid is not None else 0
            total_parts = meshpart_count + part_count

        total_parts_all = meshpart_count + part_count + union_count
        reasons = []
        if total_parts_all > 100:
            reasons.append(f"Asset terlalu kompleks ({total_parts_all} parts, maksimum 100) — Cuh... itu terlalu banyak mesh, aku nggak sanggup handle itu 😅")
        if total_parts_all == 0:
            reasons.append("Tidak ada MeshPart/Part/Union ditemukan")

        supported = (0 < total_parts_all <= 500)

        parts = []

        def decode_class(type_id, count, class_name):
            if type_id is None or count == 0:
                return
            _, size_raw = find_prop_chunk(chunks, type_id, "size")
            sizes = decode_vector3_array(size_raw, count) if size_raw else [(1.0,1.0,1.0)]*count

            _, cf_raw = find_prop_chunk(chunks, type_id, "CFrame")
            if cf_raw:
                positions, rotations = decode_cframe_full(cf_raw, count)
            else:
                positions = [(0.0,0.0,0.0)]*count
                rotations = [{"status":"identity-fallback","value":[1,0,0,0,1,0,0,0,1],"rawRotationId":None}]*count

            _, name_raw = find_prop_chunk(chunks, type_id, "Name")
            names = decode_string_array(name_raw, count) if name_raw else [f"{class_name}{i}" for i in range(count)]

            _, color_raw = find_prop_chunk(chunks, type_id, "Color3uint8")
            colors = decode_color3uint8_array(color_raw, count) if color_raw else [(163,162,165)]*count

            mesh_ids = [None]*count
            texture_ids = [None]*count
            if class_name == "MeshPart":
                _, mid_raw = find_prop_chunk(chunks, type_id, "MeshId")
                if mid_raw:
                    raw_ids = decode_string_array(mid_raw, count)
                    mesh_ids = [m if m else None for m in raw_ids]
                _, tex_raw = find_prop_chunk(chunks, type_id, "TextureID")
                if tex_raw:
                    raw_tex = decode_string_array(tex_raw, count)
                    texture_ids = [t if t else None for t in raw_tex]

            for i in range(count):
                parts.append({
                    "name": names[i],
                    "className": class_name,
                    "meshId": mesh_ids[i],
                    "textureId": texture_ids[i],
                    "position": {"status": "decoded", "value": [round(v,4) for v in positions[i]]},
                    "size": {"status": "decoded", "value": [round(v,4) for v in sizes[i]]},
                    "rotation": rotations[i],
                    "color": {"status": "best-effort", "value": list(colors[i])}
                })

        if pre_parts is not None:
            parts = pre_parts
        else:
            decode_class(meshpart_tid, meshpart_count, "MeshPart")
            decode_class(part_tid, part_count, "Part")

            if union_tid is not None and union_count > 0:
                _, size_raw = find_prop_chunk(chunks, union_tid, "size")
                sizes = decode_vector3_array(size_raw, union_count) if size_raw else [(1.0,1.0,1.0)]*union_count

                _, cf_raw = find_prop_chunk(chunks, union_tid, "CFrame")
                if cf_raw:
                    u_positions, u_rotations = decode_cframe_full(cf_raw, union_count)
                else:
                    u_positions = [(0.0,0.0,0.0)]*union_count
                    u_rotations = [{"status":"identity-fallback","value":[1,0,0,0,1,0,0,0,1],"rawRotationId":None}]*union_count

                _, name_raw = find_prop_chunk(chunks, union_tid, "Name")
                u_names = decode_string_array(name_raw, union_count) if name_raw else [f"Union{i}" for i in range(union_count)]

                _, color_raw = find_prop_chunk(chunks, union_tid, "Color3uint8")
                u_colors = decode_color3uint8_array(color_raw, union_count) if color_raw else [(163,162,165)]*union_count

                _, assetid_raw = find_prop_chunk(chunks, union_tid, "AssetId")
                if assetid_raw:
                    u_asset_ids = decode_string_array(assetid_raw, union_count)
                else:
                    u_asset_ids = [None]*union_count

                for i in range(union_count):
                    aid_raw = u_asset_ids[i] if u_asset_ids[i] else ""
                    aid_match = re.search(r"(\d{4,})", aid_raw)
                    asset_id_num = aid_match.group(1) if aid_match else None
                    parts.append({
                        "name": u_names[i],
                        "className": "UnionOperation",
                        "meshId": None,
                        "textureId": None,
                        "unionAssetId": asset_id_num,
                        "position": {"status": "decoded", "value": [round(v,4) for v in u_positions[i]]},
                        "size": {"status": "decoded", "value": [round(v,4) for v in sizes[i]]},
                        "rotation": u_rotations[i],
                        "color": {"status": "best-effort", "value": list(u_colors[i])}
                    })

        return jsonify({
            "assetId": f.filename.replace('.rbxm','').replace('.rbxmx',''),
            "filename": f.filename,
            "fileSize": len(data),
            "supported": supported,
            "reasons": reasons,
            "meshPartCount": meshpart_count,
            "partCount": part_count,
            "unionCount": union_count,
            "totalParts": total_parts_all,
            "parts": parts
        })

    except Exception as e:
        import traceback
        return jsonify({"error": str(e), "traceback": traceback.format_exc()}), 500


@app.get("/api/v2/model/mesh-union")
def model_mesh_union():
    """Fetch dan decode geometry Union/CSG dari AssetId (nested RBXM + CSGMDL).
    Dipanggil per-UnionOperation dari browser saat assembly GLB."""
    raw_id = request.args.get("assetId", "")
    if not raw_id:
        return jsonify({"error": "assetId required"}), 400

    m = re.search(r"(\d{4,})", raw_id)
    if not m:
        return jsonify({"error": "assetId tidak valid"}), 400
    asset_id = int(m.group(1))

    try:
        s = get_scraper()
        # Fetch nested RBXM (PartOperationAsset)
        r = s.get(f"https://assetdelivery.roblox.com/v1/asset/?id={asset_id}", timeout=25)
        if r.status_code != 200:
            return jsonify({"error": f"Gagal fetch Union asset (HTTP {r.status_code})"}), 502

        rbxm_data = r.content

        # Parse nested RBXM
        try:
            nested_chunks, _, _ = parse_chunks(rbxm_data)
        except RBXMParseError as e:
            return jsonify({"error": f"Gagal parse nested RBXM: {str(e)}"}), 422

        # Cari PartOperationAsset INST
        nested_type_map = parse_inst_chunks(nested_chunks)
        poa_tid = next((tid for tid, info in nested_type_map.items()
                       if info["class_name"] == "PartOperationAsset"), None)
        if poa_tid is None:
            return jsonify({"error": "PartOperationAsset tidak ditemukan di nested RBXM"}), 422

        # Ambil MeshData property
        _, meshdata_raw = find_prop_chunk(nested_chunks, poa_tid, "MeshData")
        if meshdata_raw is None:
            return jsonify({"error": "MeshData property tidak ditemukan"}), 422

        # Baca raw bytes langsung (MeshData adalah binary, bukan UTF-8 string)
        pos = 0
        sl = struct.unpack("<I", meshdata_raw[pos:pos+4])[0]; pos += 4
        mesh_bytes = meshdata_raw[pos:pos+sl]

        if not mesh_bytes:
            return jsonify({"error": "MeshData kosong"}), 422

        # Parse CSGMDL
        try:
            mesh_data = parse_csgmdl(mesh_bytes)
        except ValueError as e:
            return jsonify({"error": f"Gagal parse CSGMDL: {str(e)}"}), 422

        obj_text = csgmdl_to_obj(mesh_data, name=f"union_{asset_id}")

        return jsonify({
            "assetId": asset_id,
            "csgmdlVersion": mesh_data["version"],
            "vertexCount": mesh_data["vertex_count"],
            "faceCount": mesh_data["face_count"],
            "obj": obj_text
        })

    except Exception as e:
        return handle_roblox_error(e, "model_mesh_union")


@app.get("/privacy")
def privacy_page():
    from flask import send_from_directory
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)) + "/../", "privacy.html")

@app.get("/terms")
def terms_page():
    from flask import send_from_directory
    return send_from_directory(os.path.dirname(os.path.abspath(__file__)) + "/../", "terms.html")


# ── ROBLOX OAUTH 2.0 ENDPOINTS ───────────────────────────────────

@app.get("/auth/login")
def auth_login():
    """Step 1: Redirect user ke Roblox OAuth page."""
    import secrets, urllib.parse
    if not OAUTH_CLIENT_ID:
        return jsonify({"error": "OAuth not configured yet - waiting for Roblox app approval"}), 503

    state = secrets.token_urlsafe(16)
    params = {
        "client_id": OAUTH_CLIENT_ID,
        "redirect_uri": OAUTH_REDIRECT_URI,
        "response_type": "code",
        "scope": OAUTH_SCOPES,
        "state": state
    }
    auth_url = OAUTH_AUTH_URL + "?" + urllib.parse.urlencode(params)

    resp = redirect(auth_url)
    resp.set_cookie("oauth_state", state, max_age=600, httponly=True, samesite="Lax", secure=True)
    return resp

@app.get("/auth/callback")
def auth_callback():
    """Step 2: Roblox redirect ke sini dengan authorization code."""
    import base64, urllib.parse
    if not OAUTH_CLIENT_ID:
        return jsonify({"error": "OAuth not configured yet"}), 503

    code_param = request.args.get("code", "")
    state = request.args.get("state", "")
    error = request.args.get("error", "")

    if error:
        return redirect(f"/?oauth_error={urllib.parse.quote(error)}")

    # Verify state
    saved_state = request.cookies.get("oauth_state", "")
    if not saved_state or saved_state != state:
        return redirect("/?oauth_error=invalid_state")

    # Exchange code for token
    try:
        credentials = base64.b64encode(f"{OAUTH_CLIENT_ID}:{OAUTH_CLIENT_SECRET}".encode()).decode()
        token_resp = requests.post(OAUTH_TOKEN_URL, headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded"
        }, data={
            "grant_type": "authorization_code",
            "code": code_param,
            "redirect_uri": OAUTH_REDIRECT_URI
        }, timeout=15)

        if token_resp.status_code != 200:
            return redirect(f"/?oauth_error=token_exchange_failed")

        token_data = token_resp.json()
        access_token = token_data.get("access_token", "")

        # Get user info
        userinfo_resp = requests.get(OAUTH_USERINFO_URL, headers={
            "Authorization": f"Bearer {access_token}"
        }, timeout=10)
        userinfo = userinfo_resp.json() if userinfo_resp.status_code == 200 else {}

        # Redirect ke frontend dengan token (stored di client-side)
        import urllib.parse
        params = urllib.parse.urlencode({
            "oauth_success": "1",
            "access_token": access_token,
            "username": userinfo.get("name", ""),
            "user_id": userinfo.get("sub", ""),
            "expires_in": token_data.get("expires_in", 3600)
        })
        resp = redirect(f"/?{params}")
        resp.delete_cookie("oauth_state")
        return resp

    except Exception as e:
        return redirect(f"/?oauth_error={urllib.parse.quote(str(e))}")

@app.get("/auth/me")
def auth_me():
    """Validate OAuth token dan return user info."""
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return jsonify({"authenticated": False}), 401

    token = auth_header[7:]
    try:
        resp = requests.get(OAUTH_USERINFO_URL, headers={
            "Authorization": f"Bearer {token}"
        }, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            return jsonify({
                "authenticated": True,
                "username": data.get("name", ""),
                "userId": data.get("sub", ""),
                "displayName": data.get("preferred_username", data.get("name", ""))
            })
        return jsonify({"authenticated": False}), 401
    except Exception as e:
        return jsonify({"authenticated": False, "error": str(e)}), 500

@app.get("/auth/logout")
def auth_logout():
    """Clear session - token di-clear di sisi client."""
    return jsonify({"success": True, "message": "Token cleared client-side"})

SUPABASE_URL = os.environ.get("SUPABASE_URL", "")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY", "")

def get_maintenance_state():
    try:
        r = requests.get(
            f"{SUPABASE_URL}/rest/v1/app_state",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}"
            },
            params={"key": "eq.maintenance_mode", "select": "value"},
            timeout=5
        )
        data = r.json()
        if data and len(data) > 0:
            return data[0].get("value", False)
        return False
    except Exception as e:
        print(f"Error getting maintenance state: {e}")
        return False

def set_maintenance_state(active):
    try:
        r = requests.patch(
            f"{SUPABASE_URL}/rest/v1/app_state",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Content-Type": "application/json",
                "Prefer": "return=representation"
            },
            params={"key": "eq.maintenance_mode"},
            json={"value": active},
            timeout=5
        )
        print(f"Supabase PATCH status: {r.status_code}, body: {r.text}, url_used: {SUPABASE_URL[:20]}...")
    except Exception as e:
        print(f"Error setting maintenance state: {e}")

@app.get("/api/maintenance/status")
def maintenance_status():
    return jsonify({"active": get_maintenance_state()})

@app.post("/api/maintenance/toggle")
def maintenance_toggle():
    data = request.get_json(force=True, silent=True) or {}
    password = data.get("password", "")
    admin_password = os.environ.get("MAINTENANCE_ADMIN_PASSWORD", "")
    if not admin_password or password != admin_password:
        return jsonify({"error": "Invalid password"}), 403
    new_state = not get_maintenance_state()
    set_maintenance_state(new_state)
    return jsonify({"active": new_state})

@app.get("/auth/status")
def auth_status():
    """Check apakah OAuth sudah dikonfigurasi."""
    return jsonify({
        "oauth_configured": bool(OAUTH_CLIENT_ID and OAUTH_CLIENT_SECRET),
        "client_id_set": bool(OAUTH_CLIENT_ID),
        "redirect_uri": OAUTH_REDIRECT_URI
    })


if __name__ == "__main__":
    port=int(os.getenv("PORT",8000))
    print(f"Server jalan di http://0.0.0.0:{port}")
    app.run(host="0.0.0.0",port=port,debug=False)
