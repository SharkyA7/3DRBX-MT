#!/usr/bin/env python3
"""
Create comprehensive test RBXM files for testing the dump tool.
Generates samples covering:
1. MeshPart with TextureID using SharedString (0x1C)
2. MeshPart with TextureID using plain String (0x01)
3. SurfaceAppearance with ColorMap, NormalMap, MetalnessMap, RoughnessMap
4. Decal with Texture property
5. UnionOperation with AssetId property
"""

import struct
import lz4.block
import hashlib

def rol1(u32):
    return ((u32 << 1) & 0xFFFFFFFF) | (u32 >> 31)

def encode_f32(f):
    bits = struct.unpack(">I", struct.pack(">f", f))[0]
    return rol1(bits)

def encode_string_array(strings):
    """Encode array of strings as length-prefixed UTF-8."""
    out = bytearray()
    for s in strings:
        data = s.encode("utf-8")
        out += struct.pack("<I", len(data))
        out += data
    return bytes(out)

def encode_shared_string_array(strings, sstr_pool):
    """Encode array of shared string indices (interleaved u32)."""
    # Find indices in pool
    indices = []
    for s in strings:
        if s in sstr_pool:
            indices.append(sstr_pool.index(s))
        else:
            indices.append(0)
    # Encode as interleaved BE u32
    count = len(indices)
    out = bytearray(4 * count)
    for i, idx in enumerate(indices):
        out[i] = (idx >> 24) & 0xFF
        out[count + i] = (idx >> 16) & 0xFF
        out[2*count + i] = (idx >> 8) & 0xFF
        out[3*count + i] = idx & 0xFF
    return bytes(out)

def encode_vector3_array(vectors):
    """Encode Vector3 array (interleaved float32 with ROR1)."""
    count = len(vectors)
    xs = [v[0] for v in vectors]
    ys = [v[1] for v in vectors]
    zs = [v[2] for v in vectors]
    out = bytearray()
    for x in xs:
        out += struct.pack(">I", encode_f32(x))
    for y in ys:
        out += struct.pack(">I", encode_f32(y))
    for z in zs:
        out += struct.pack(">I", encode_f32(z))
    return bytes(out)

def encode_cframe_array(cframes):
    """Encode CFrame array (positions + rotation_ids)."""
    count = len(cframes)
    # Rotation IDs (1 byte each)
    rot_ids = bytes([c.get("rotation_id", 0) for c in cframes])
    # Positions (Vector3)
    positions = [(c["x"], c["y"], c["z"]) for c in cframes]
    pos_data = encode_vector3_array(positions)
    return rot_ids + pos_data

def encode_int_array(ints):
    """Encode interleaved int32 array."""
    count = len(ints)
    out = bytearray()
    for v in ints:
        u32 = v & 0xFFFFFFFF
        out[i] = (u32 >> 24) & 0xFF
        out[count + i] = (u32 >> 16) & 0xFF
        out[2*count + i] = (u32 >> 8) & 0xFF
        out[3*count + i] = u32 & 0xFF
    return bytes(out)

def encode_bool_array(bools):
    return bytes([1 if b else 0 for b in bools])

def make_chunk(name, body, compress=True):
    """Create a chunk with optional compression."""
    if compress and len(body) > 0:
        compressed = lz4.block.compress(body, store_size=False)
        cl = len(compressed)
        ul = len(body)
        chunk = bytearray()
        chunk += name.encode("ascii").ljust(4, b'\x00')[:4]
        chunk += struct.pack("<I", cl)
        chunk += struct.pack("<I", ul)
        chunk += b"\x00" * 4
        chunk += compressed
    else:
        cl = 0
        ul = len(body)
        chunk = bytearray()
        chunk += name.encode("ascii").ljust(4, b'\x00')[:4]
        chunk += struct.pack("<I", cl)
        chunk += struct.pack("<I", ul)
        chunk += b"\x00" * 4
        chunk += body
    return bytes(chunk)

def make_inst_chunk(type_id, class_name, referents):
    """Create INST chunk."""
    body = bytearray()
    body += struct.pack("<I", type_id)
    class_bytes = class_name.encode("utf-8")
    body += struct.pack("<I", len(class_bytes))
    body += class_bytes
    body += b"\x00"  # is_service = 0
    count = len(referents)
    body += struct.pack("<I", count)
    # Encode referents as interleaved u32 (4 planes of count bytes each)
    if count > 0:
        # Prepare 4 planes
        plane0 = bytearray(count)
        plane1 = bytearray(count)
        plane2 = bytearray(count)
        plane3 = bytearray(count)
        for i, ref in enumerate(referents):
            ref = ref & 0xFFFFFFFF
            plane0[i] = (ref >> 24) & 0xFF
            plane1[i] = (ref >> 16) & 0xFF
            plane2[i] = (ref >> 8) & 0xFF
            plane3[i] = ref & 0xFF
        body += plane0 + plane1 + plane2 + plane3
    return make_chunk("INST", bytes(body))

def make_prop_chunk(type_id, prop_name, dtype, data):
    """Create PROP chunk."""
    body = bytearray()
    body += struct.pack("<I", type_id)
    pname_bytes = prop_name.encode("utf-8")
    body += struct.pack("<I", len(pname_bytes))
    body += pname_bytes
    body += bytes([dtype])
    body += data
    return make_chunk("PROP", bytes(body))

def make_sstr_chunk(strings):
    """Create SSTR chunk with shared strings."""
    body = bytearray()
    body += struct.pack("<I", 1)  # version
    body += struct.pack("<I", len(strings))  # num_entries
    for s in strings:
        data = s.encode("utf-8")
        md5 = hashlib.md5(data).digest()
        body += md5
        body += struct.pack("<I", len(data))
        body += data
    return make_chunk("SSTR", bytes(body))

def write_rbxm(filename, chunks, num_types, num_instances):
    """Write complete RBXM file."""
    header = b"<roblox!"
    header += struct.pack("<Q", 0)  # version
    header += struct.pack("<I", num_types)
    header += struct.pack("<I", num_instances)
    header += b"\x00" * 8  # reserved

    end_chunk = make_chunk("END", b"", compress=False)

    with open(filename, "wb") as f:
        f.write(header)
        for chunk in chunks:
            f.write(chunk)
        f.write(end_chunk)

    print(f"Created {filename} ({sum(len(c) for c in [header] + chunks + [end_chunk])} bytes)")

# ═══════════════════════════════════════════════════════════════════════════════
# SAMPLE 1: MeshPart with TextureID using SharedString (0x1C)
# ═══════════════════════════════════════════════════════════════════════════════

def create_sample_1():
    """MeshPart with TextureID as SharedString."""
    # Shared string pool
    sstrings = [
        "rbxassetid://123456789",  # TextureID
        "rbxassetid://987654321",  # MeshId
        "TestMeshPart",            # Name
    ]

    # TypeID 1 = MeshPart
    referents = [1]  # Single instance

    chunks = []
    chunks.append(make_inst_chunk(1, "MeshPart", referents))
    chunks.append(make_sstr_chunk(sstrings))

    # TextureID (SharedString, dtype 0x1C) - index 0 in pool
    tex_data = encode_shared_string_array(["rbxassetid://123456789"], sstrings)
    chunks.append(make_prop_chunk(1, "TextureID", 0x1C, tex_data))

    # MeshId (SharedString, dtype 0x1C) - index 1 in pool
    mesh_data = encode_shared_string_array(["rbxassetid://987654321"], sstrings)
    chunks.append(make_prop_chunk(1, "MeshId", 0x1C, mesh_data))

    # Name (String, dtype 0x01) - index 2 in pool
    name_data = encode_string_array(["TestMeshPart"])
    chunks.append(make_prop_chunk(1, "Name", 0x01, name_data))

    # Size (Vector3)
    size_data = encode_vector3_array([(4.0, 4.0, 4.0)])
    chunks.append(make_prop_chunk(1, "Size", 0x0E, size_data))

    # CFrame
    cframe_data = encode_cframe_array([{"x": 0, "y": 0, "z": 0, "rotation_id": 0}])
    chunks.append(make_prop_chunk(1, "CFrame", 0x19, cframe_data))

    write_rbxm("/root/3DRBX-MT/samples/sample1_meshpart_sharedstring.rbxm", chunks, 1, 1)

# ═══════════════════════════════════════════════════════════════════════════════
# SAMPLE 2: MeshPart with TextureID using plain String (0x01)
# ═══════════════════════════════════════════════════════════════════════════════

def create_sample_2():
    """MeshPart with TextureID as plain String."""
    # No SSTR chunk needed for plain strings

    referents = [2]  # Different referent

    chunks = []
    chunks.append(make_inst_chunk(1, "MeshPart", referents))

    # TextureID (String, dtype 0x01)
    tex_data = encode_string_array(["rbxassetid://555555555"])
    chunks.append(make_prop_chunk(1, "TextureID", 0x01, tex_data))

    # MeshId (String, dtype 0x01)
    mesh_data = encode_string_array(["rbxassetid://666666666"])
    chunks.append(make_prop_chunk(1, "MeshId", 0x01, mesh_data))

    # Name (String)
    name_data = encode_string_array(["PlainStringMeshPart"])
    chunks.append(make_prop_chunk(1, "Name", 0x01, name_data))

    # Size
    size_data = encode_vector3_array([(2.0, 2.0, 2.0)])
    chunks.append(make_prop_chunk(1, "Size", 0x0E, size_data))

    write_rbxm("/root/3DRBX-MT/samples/sample2_meshpart_plainstring.rbxm", chunks, 1, 1)

# ═══════════════════════════════════════════════════════════════════════════════
# SAMPLE 3: SurfaceAppearance with ColorMap, NormalMap, MetalnessMap, RoughnessMap
# ═══════════════════════════════════════════════════════════════════════════════

def create_sample_3():
    """SurfaceAppearance with all map properties."""
    # Shared strings for all texture URLs
    sstrings = [
        "rbxassetid://111111111",  # ColorMap
        "rbxassetid://222222222",  # NormalMap
        "rbxassetid://333333333",  # MetalnessMap
        "rbxassetid://444444444",  # RoughnessMap
        "MySurfaceAppearance",     # Name
    ]

    # TypeID 1 = SurfaceAppearance, TypeID 2 = MeshPart (parent)
    chunks = []
    chunks.append(make_inst_chunk(1, "SurfaceAppearance", [10]))
    chunks.append(make_inst_chunk(2, "MeshPart", [11]))
    chunks.append(make_sstr_chunk(sstrings))

    # SurfaceAppearance properties (all SharedString)
    color_map = encode_shared_string_array(["rbxassetid://111111111"], sstrings)
    chunks.append(make_prop_chunk(1, "ColorMap", 0x1C, color_map))

    normal_map = encode_shared_string_array(["rbxassetid://222222222"], sstrings)
    chunks.append(make_prop_chunk(1, "NormalMap", 0x1C, normal_map))

    metalness_map = encode_shared_string_array(["rbxassetid://333333333"], sstrings)
    chunks.append(make_prop_chunk(1, "MetalnessMap", 0x1C, metalness_map))

    roughness_map = encode_shared_string_array(["rbxassetid://444444444"], sstrings)
    chunks.append(make_prop_chunk(1, "RoughnessMap", 0x1C, roughness_map))

    # Name
    name_data = encode_string_array(["MySurfaceAppearance"])
    chunks.append(make_prop_chunk(1, "Name", 0x01, name_data))

    # MeshPart properties
    mesh_tex = encode_shared_string_array(["rbxassetid://555555555"], sstrings)
    chunks.append(make_prop_chunk(2, "TextureID", 0x1C, mesh_tex))

    write_rbxm("/root/3DRBX-MT/samples/sample3_surfaceappearance.rbxm", chunks, 2, 2)

# ═══════════════════════════════════════════════════════════════════════════════
# SAMPLE 4: Decal with Texture property
# ═══════════════════════════════════════════════════════════════════════════════

def create_sample_4():
    """Decal with Texture property."""
    sstrings = [
        "rbxassetid://777777777",  # Texture
        "MyDecal",                 # Name
    ]

    chunks = []
    chunks.append(make_inst_chunk(1, "Decal", [20]))
    chunks.append(make_inst_chunk(2, "Part", [21]))  # Parent part
    chunks.append(make_sstr_chunk(sstrings))

    # Decal Texture (SharedString)
    tex_data = encode_shared_string_array(["rbxassetid://777777777"], sstrings)
    chunks.append(make_prop_chunk(1, "Texture", 0x1C, tex_data))

    # Decal Name
    name_data = encode_string_array(["MyDecal"])
    chunks.append(make_prop_chunk(1, "Name", 0x01, name_data))

    # Part properties
    part_name = encode_string_array(["DecalParentPart"])
    chunks.append(make_prop_chunk(2, "Name", 0x01, part_name))

    write_rbxm("/root/3DRBX-MT/samples/sample4_decal.rbxm", chunks, 2, 2)

# ═══════════════════════════════════════════════════════════════════════════════
# SAMPLE 5: UnionOperation with AssetId property
# ═══════════════════════════════════════════════════════════════════════════════

def create_sample_5():
    """UnionOperation with AssetId property."""
    sstrings = [
        "rbxassetid://999999999",  # AssetId
        "MyUnion",                 # Name
    ]

    chunks = []
    chunks.append(make_inst_chunk(1, "UnionOperation", [30]))
    chunks.append(make_sstr_chunk(sstrings))

    # UnionOperation AssetId (SharedString)
    asset_data = encode_shared_string_array(["rbxassetid://999999999"], sstrings)
    chunks.append(make_prop_chunk(1, "AssetId", 0x1C, asset_data))

    # Name
    name_data = encode_string_array(["MyUnion"])
    chunks.append(make_prop_chunk(1, "Name", 0x01, name_data))

    # Size
    size_data = encode_vector3_array([(3.0, 3.0, 3.0)])
    chunks.append(make_prop_chunk(1, "Size", 0x0E, size_data))

    write_rbxm("/root/3DRBX-MT/samples/sample5_unionoperation.rbxm", chunks, 1, 1)

# ═══════════════════════════════════════════════════════════════════════════════
# SAMPLE 6: Mixed - MeshPart with both SharedString and String properties
# ═══════════════════════════════════════════════════════════════════════════════

def create_sample_6():
    """MeshPart with TextureID as SharedString and MeshId as plain String."""
    sstrings = [
        "rbxassetid://111111111",  # TextureID (SharedString)
        "MixedMeshPart",           # Name
    ]

    chunks = []
    chunks.append(make_inst_chunk(1, "MeshPart", [40]))
    chunks.append(make_sstr_chunk(sstrings))

    # TextureID (SharedString)
    tex_data = encode_shared_string_array(["rbxassetid://111111111"], sstrings)
    chunks.append(make_prop_chunk(1, "TextureID", 0x1C, tex_data))

    # MeshId (plain String)
    mesh_data = encode_string_array(["rbxassetid://222222222"])
    chunks.append(make_prop_chunk(1, "MeshId", 0x01, mesh_data))

    # Name (String)
    name_data = encode_string_array(["MixedMeshPart"])
    chunks.append(make_prop_chunk(1, "Name", 0x01, name_data))

    write_rbxm("/root/3DRBX-MT/samples/sample6_mixed_strings.rbxm", chunks, 1, 1)

# ═══════════════════════════════════════════════════════════════════════════════
# SAMPLE 7: Multiple MeshParts with different TextureID encodings
# ═══════════════════════════════════════════════════════════════════════════════

def create_sample_7():
    """Multiple MeshParts - some SharedString, some plain String."""
    sstrings = [
        "rbxassetid://100000001",
        "rbxassetid://100000002",
        "rbxassetid://100000003",
        "MeshPart_Shared",
        "MeshPart_Plain",
        "MeshPart_Both",
    ]

    # Three MeshParts with different referents
    chunks = []
    chunks.append(make_inst_chunk(1, "MeshPart", [50, 51, 52]))  # 3 instances
    chunks.append(make_sstr_chunk(sstrings))

    # Instance 1: TextureID as SharedString
    tex1 = encode_shared_string_array(["rbxassetid://100000001"], sstrings)
    chunks.append(make_prop_chunk(1, "TextureID", 0x1C, tex1))

    # Instance 2: TextureID as plain String (need different type_id)
    # Actually, for multiple instances of same class, we need one PROP per property per type
    # The property data contains values for ALL instances
    # Let me redo this - for multiple instances, we encode arrays

    # For 3 instances:
    # Instance 0: SharedString TextureID
    # Instance 1: Plain String TextureID
    # Instance 2: Both (SharedString TextureID, plain String MeshId)

    # TextureID: SharedString for all 3 (indices 0, 1, 2)
    tex_shared = encode_shared_string_array([
        "rbxassetid://100000001",
        "rbxassetid://100000002",
        "rbxassetid://100000003"
    ], sstrings)
    chunks.append(make_prop_chunk(1, "TextureID", 0x1C, tex_shared))

    # MeshId: plain String for all 3
    mesh_plain = encode_string_array([
        "rbxassetid://200000001",
        "rbxassetid://200000002",
        "rbxassetid://200000003"
    ])
    chunks.append(make_prop_chunk(1, "MeshId", 0x01, mesh_plain))

    # Names
    names = encode_string_array(["MeshPart_Shared", "MeshPart_Plain", "MeshPart_Both"])
    chunks.append(make_prop_chunk(1, "Name", 0x01, names))

    write_rbxm("/root/3DRBX-MT/samples/sample7_multiple_meshparts.rbxm", chunks, 1, 3)

# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    create_sample_1()
    create_sample_2()
    create_sample_3()
    create_sample_4()
    create_sample_5()
    create_sample_6()
    create_sample_7()
    print("\nAll samples created in /root/3DRBX-MT/samples/")