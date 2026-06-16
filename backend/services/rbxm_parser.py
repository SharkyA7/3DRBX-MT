"""
RBXM Binary format parser - MVP scope.
Verified working: header/chunks/LZ4, INST (class map), String props (MeshId/Name),
Vector3/Float32 props via ROR1 transform (size, CFrame position).
NOT verified: CFrame rotation matrices, Color3uint8 layout, SharedStrings.
"""
import struct
import lz4.block


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


def decode_cframe_positions(raw, count):
    """
    Position component VERIFIED 763/763 sane on test asset.
    Structure: [N rotation_id bytes][raw 9-float matrices for non-table rotations, variable count]
               [position XYZ block - ALWAYS the trailing count*12 bytes, regardless of matrix count]
    Rotation NOT decoded for MVP - returns identity matrix + raw rotation_id for diagnostics.
    Note: position block size is fixed (count*12) and always at the END of the CFrame property,
    so we slice from the end rather than computing offset from rotation_id==0 count.
    """
    rotation_ids = list(raw[:count])
    pos_block = raw[-(count * 12):]
    positions = decode_vector3_array(pos_block, count)
    return positions, rotation_ids


def decode_bool_array(raw, count):
    return [bool(b) for b in raw[:count]]
