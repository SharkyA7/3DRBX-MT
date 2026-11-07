#!/usr/bin/env python3
"""
Create a minimal test RBXM file for testing the dump tool.
"""

import struct
import lz4.block

def create_test_rbxm():
    # Header: <roblox! (8) + version (8, uint64) + num_types (4) + num_instances (4) + reserved (8) = 32 bytes
    header = b"<roblox!"
    header += struct.pack("<Q", 0)      # version (0 for binary format) - 8 bytes uint64
    header += struct.pack("<I", 1)      # num_types (1 class)
    header += struct.pack("<I", 1)      # num_instances (1 instance)
    header += b"\x00" * 8               # reserved (8 bytes)

    # INST chunk: TypeID=1, ClassName="Part", is_service=0, count=1, referents=[1]
    # INST format: type_id:u32, name_len:u32, name:bytes, is_service:u8, count:u32, referents:interleaved_u32
    inst_body = bytearray()
    inst_body += struct.pack("<I", 1)           # type_id = 1
    class_name = b"Part"
    inst_body += struct.pack("<I", len(class_name))
    inst_body += class_name
    inst_body += b"\x00"                        # is_service = 0
    inst_body += struct.pack("<I", 1)           # count = 1
    # Referent: just [1] encoded as interleaved (simple case)
    inst_body += struct.pack("<I", 1)           # referent 1

    # Compress INST chunk
    inst_compressed = lz4.block.compress(bytes(inst_body), store_size=False)
    inst_chunk = bytearray()
    inst_chunk += b"INST"                       # chunk name (exactly 4 bytes)
    inst_chunk += struct.pack("<I", len(inst_compressed))  # compressed size
    inst_chunk += struct.pack("<I", len(inst_body))        # uncompressed size
    inst_chunk += b"\x00" * 4                   # padding
    inst_chunk += inst_compressed

    # PROP chunk for Part.Name = "TestPart"
    # PROP format: type_id:u32, name_len:u32, name:bytes, dtype:u8, data...
    prop_name_body = bytearray()
    prop_name_body += struct.pack("<I", 1)      # type_id = 1
    pname = b"Name"
    prop_name_body += struct.pack("<I", len(pname))
    prop_name_body += pname
    prop_name_body += b"\x01"                   # dtype = 0x01 (String)
    # String data: length-prefixed UTF-8
    prop_name_body += struct.pack("<I", 8)
    prop_name_body += b"TestPart"

    prop_name_compressed = lz4.block.compress(bytes(prop_name_body), store_size=False)
    prop_name_chunk = bytearray()
    prop_name_chunk += b"PROP"
    prop_name_chunk += struct.pack("<I", len(prop_name_compressed))
    prop_name_chunk += struct.pack("<I", len(prop_name_body))
    prop_name_chunk += b"\x00" * 4
    prop_name_chunk += prop_name_compressed

    # PROP chunk for Part.Size = Vector3(4, 2, 4)
    # Vector3 is dtype 0x0E, interleaved float32 arrays
    prop_size_body = bytearray()
    prop_size_body += struct.pack("<I", 1)      # type_id = 1
    pname = b"Size"
    prop_size_body += struct.pack("<I", len(pname))
    prop_size_body += pname
    prop_size_body += b"\x0E"                   # dtype = 0x0E (Vector3)
    # Vector3 data: 3 planes of interleaved float32, count=1
    # For count=1, interleaved is just the 3 floats
    # We need to ROL1 encode each float (inverse of decoder's ROR1)
    def rol1(u32):
        return ((u32 << 1) & 0xFFFFFFFF) | (u32 >> 31)

    def encode_f32(f):
        import struct as st
        bits = st.unpack(">I", st.pack(">f", f))[0]
        return rol1(bits)

    # Size = (4, 2, 4)
    x_bits = encode_f32(4.0)
    y_bits = encode_f32(2.0)
    z_bits = encode_f32(4.0)
    # For count=1, each plane is 4 bytes
    prop_size_body += struct.pack(">I", x_bits)
    prop_size_body += struct.pack(">I", y_bits)
    prop_size_body += struct.pack(">I", z_bits)

    prop_size_compressed = lz4.block.compress(bytes(prop_size_body), store_size=False)
    prop_size_chunk = bytearray()
    prop_size_chunk += b"PROP"
    prop_size_chunk += struct.pack("<I", len(prop_size_compressed))
    prop_size_chunk += struct.pack("<I", len(prop_size_body))
    prop_size_chunk += b"\x00" * 4
    prop_size_chunk += prop_size_compressed

    # PROP chunk for Part.Color3uint8 = (255, 0, 0) - dtype 0x16
    prop_color_body = bytearray()
    prop_color_body += struct.pack("<I", 1)
    pname = b"Color"
    prop_color_body += struct.pack("<I", len(pname))
    prop_color_body += pname
    prop_color_body += b"\x16"                  # dtype = 0x16 (Color3uint8)
    # Color3uint8: sequential RGB triplets
    prop_color_body += bytes([255, 0, 0])

    prop_color_compressed = lz4.block.compress(bytes(prop_color_body), store_size=False)
    prop_color_chunk = bytearray()
    prop_color_chunk += b"PROP"
    prop_color_chunk += struct.pack("<I", len(prop_color_compressed))
    prop_color_chunk += struct.pack("<I", len(prop_color_body))
    prop_color_chunk += b"\x00" * 4
    prop_color_chunk += prop_color_compressed

    # END chunk (no compression)
    end_chunk = bytearray()
    end_chunk += b"END\x00"  # 3 chars + null padding = 4 bytes
    end_chunk += struct.pack("<I", 0)           # compressed size = 0
    end_chunk += struct.pack("<I", 0)           # uncompressed size = 0
    end_chunk += b"\x00" * 4
    # no body

    # Combine all chunks
    rbxm_data = header + bytes(inst_chunk) + bytes(prop_name_chunk) + bytes(prop_size_chunk) + bytes(prop_color_chunk) + bytes(end_chunk)

    with open("/root/3DRBX-MT/test_sample.rbxm", "wb") as f:
        f.write(rbxm_data)

    print(f"Created test_sample.rbxm ({len(rbxm_data)} bytes)")
    return rbxm_data

if __name__ == "__main__":
    create_test_rbxm()