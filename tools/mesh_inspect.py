#!/usr/bin/env python3
"""
Roblox Mesh Inspector - stdlib only, no network.

Scans mesh files, prints: id, size, version header, structural check.
For v2-v5: byte accounting against spec.
For v6/v7: chunk list with sizes.
Outputs version histogram and flags byte mismatches.
"""
import os
import struct
import sys
from collections import Counter
from pathlib import Path


def read_version_header(data: bytes) -> tuple[str, int]:
    """Read version header line, return (version_string, header_end_pos)."""
    # Version header ends at first newline
    try:
        newline_pos = data.index(b'\n')
        header = data[:newline_pos + 1].decode('utf-8', errors='replace').strip()
        return header, newline_pos + 1
    except ValueError:
        return "", 0


def inspect_v2(data: bytes, pos: int) -> dict:
    """Inspect v2.00 mesh: header + vertices + faces."""
    result = {'version': '2.00', 'valid': True, 'errors': [], 'bytes_accounted': 0}
    try:
        # MeshHeader: uint16 sizeof_header, uint8 sizeof_vertex, uint8 sizeof_face, uint32 num_verts, uint32 num_faces
        sizeof_header, sizeof_vertex, sizeof_face = struct.unpack_from("<HBB", data, pos)
        num_verts, num_faces = struct.unpack_from("<II", data, pos + 4)
        result['header'] = {
            'sizeof_header': sizeof_header,
            'sizeof_vertex': sizeof_vertex,
            'sizeof_face': sizeof_face,
            'num_verts': num_verts,
            'num_faces': num_faces,
        }
        # Vertex: 8 floats = 32 bytes (pos[3], normal[3], uv[2])
        # Face: 3 uint32 = 12 bytes
        vertex_data_size = num_verts * sizeof_vertex
        face_data_size = num_faces * sizeof_face
        expected_total = sizeof_header + vertex_data_size + face_data_size
        actual_remaining = len(data) - (pos + sizeof_header)
        result['bytes_accounted'] = sizeof_header + vertex_data_size + face_data_size
        result['expected_remaining'] = expected_total
        result['actual_remaining'] = actual_remaining
        if actual_remaining != expected_total:
            result['valid'] = False
            result['errors'].append(f"Byte mismatch: expected {expected_total} bytes after header, got {actual_remaining}")
    except Exception as e:
        result['valid'] = False
        result['errors'].append(f"Parse error: {e}")
    return result


def inspect_v3(data: bytes, pos: int) -> dict:
    """Inspect v3.00 mesh: header + vertices + faces + LOD."""
    result = {'version': '3.00', 'valid': True, 'errors': [], 'bytes_accounted': 0}
    try:
        # v3 MeshHeader: 7x uint16 = 14 bytes
        # sizeof_MeshHeader, sizeof_MeshVertex, sizeof_MeshFace, sizeof_MeshLOD, numLODs, numVerts, numFaces
        fields = struct.unpack_from("<HHHHHHH", data, pos)
        sizeof_header, sizeof_vertex, sizeof_face, sizeof_lod, num_lods, num_verts, num_faces = fields
        result['header'] = {
            'sizeof_header': sizeof_header,
            'sizeof_vertex': sizeof_vertex,
            'sizeof_face': sizeof_face,
            'sizeof_lod': sizeof_lod,
            'num_lods': num_lods,
            'num_verts': num_verts,
            'num_faces': num_faces,
        }
        # Vertex: 8 floats = 32 bytes (pos[3], normal[3], uv[2])
        # Face: 3 uint32 = 12 bytes
        # LOD: array of numLODs uint32 offsets
        vertex_data_size = num_verts * sizeof_vertex
        face_data_size = num_faces * sizeof_face
        lod_data_size = num_lods * sizeof_lod
        expected_total = sizeof_header + vertex_data_size + face_data_size + lod_data_size
        actual_remaining = len(data) - (pos + sizeof_header)
        result['bytes_accounted'] = expected_total
        result['expected_remaining'] = expected_total
        result['actual_remaining'] = actual_remaining
        if actual_remaining != expected_total:
            result['valid'] = False
            result['errors'].append(f"Byte mismatch: expected {expected_total} bytes after header, got {actual_remaining}")
    except Exception as e:
        result['valid'] = False
        result['errors'].append(f"Parse error: {e}")
    return result


def inspect_v4(data: bytes, pos: int) -> dict:
    """Inspect v4.xx mesh: 24-byte header + vertices + faces + LOD offsets."""
    result = {'version': '4.xx', 'valid': True, 'errors': [], 'bytes_accounted': 0}
    try:
        # Header: 24 bytes
        # uint16 sizeof_header, uint16 padding?, uint32 num_verts, uint32 num_faces, uint16 num_lods, uint16 num_bones?, uint16 num_subsets?
        if pos + 24 > len(data):
            result['valid'] = False
            result['errors'].append("Data too short for v4 header")
            return result
        header_size = struct.unpack_from("<H", data, pos)[0]
        num_verts = struct.unpack_from("<I", data, pos + 4)[0]
        num_faces = struct.unpack_from("<I", data, pos + 8)[0]
        num_lods = struct.unpack_from("<H", data, pos + 12)[0]
        num_bones = struct.unpack_from("<H", data, pos + 14)[0] if pos + 16 <= len(data) else 0
        num_subsets = struct.unpack_from("<H", data, pos + 16)[0] if pos + 18 <= len(data) else 0

        result['header'] = {
            'header_size': header_size,
            'num_verts': num_verts,
            'num_faces': num_faces,
            'num_lods': num_lods,
            'num_bones': num_bones,
            'num_subsets': num_subsets,
        }

        # Vertex: 40 bytes (9 floats pos/normal/uv + 4 byte RGBA)
        # Face: 12 bytes (3x uint32)
        # LOD offsets: num_lods x uint32
        vstart = pos + header_size
        vertex_data_size = num_verts * 40
        face_data_size = num_faces * 12
        lod_data_size = num_lods * 4
        expected_total = header_size + vertex_data_size + face_data_size + lod_data_size
        actual_remaining = len(data) - vstart
        result['bytes_accounted'] = expected_total
        result['expected_remaining'] = expected_total
        result['actual_remaining'] = actual_remaining
        if actual_remaining != expected_total:
            result['valid'] = False
            result['errors'].append(f"Byte mismatch: expected {expected_total} bytes after header, got {actual_remaining}")
    except Exception as e:
        result['valid'] = False
        result['errors'].append(f"Parse error: {e}")
    return result


def inspect_v5(data: bytes, pos: int) -> dict:
    """Inspect v5.xx mesh - same binary format as v4 per current parser."""
    # v5 routes to _parse_v4 in current code
    return inspect_v4(data, pos)


def inspect_v6(data: bytes, pos: int) -> dict:
    """Inspect v6.00 mesh: chunked format."""
    result = {'version': '6.00', 'valid': True, 'errors': [], 'chunks': [], 'bytes_accounted': 0}
    try:
        # After "version 6.00\n", chunks of: type[8], uint32 chunkVersion, uint32 size, data
        total_chunk_data = 0
        while pos + 16 <= len(data):
            chunk_type = data[pos:pos+8].rstrip(b'\x00').decode('ascii', errors='replace')
            chunk_version = struct.unpack_from("<I", data, pos + 8)[0]
            chunk_size = struct.unpack_from("<I", data, pos + 12)[0]
            chunk_data_start = pos + 16
            chunk_data_end = chunk_data_start + chunk_size

            if chunk_data_end > len(data):
                result['valid'] = False
                result['errors'].append(f"Chunk {chunk_type} size {chunk_size} exceeds remaining data")
                break

            result['chunks'].append({
                'type': chunk_type,
                'version': chunk_version,
                'size': chunk_size,
                'offset': pos,
            })
            total_chunk_data += 16 + chunk_size
            pos = chunk_data_end

        result['bytes_accounted'] = total_chunk_data
        result['expected_remaining'] = total_chunk_data
        result['actual_remaining'] = len(data) - (pos - total_chunk_data)  # approximate
    except Exception as e:
        result['valid'] = False
        result['errors'].append(f"Parse error: {e}")
    return result


def inspect_v7(data: bytes, pos: int) -> dict:
    """Inspect v7.00 mesh: COREMESH v2 with Draco."""
    result = {'version': '7.00', 'valid': True, 'errors': [], 'chunks': [], 'bytes_accounted': 0}
    try:
        # Similar chunked format, but COREMESH chunkVersion 2 has Draco bitstream
        total_chunk_data = 0
        while pos + 16 <= len(data):
            chunk_type = data[pos:pos+8].rstrip(b'\x00').decode('ascii', errors='replace')
            chunk_version = struct.unpack_from("<I", data, pos + 8)[0]
            chunk_size = struct.unpack_from("<I", data, pos + 12)[0]
            chunk_data_start = pos + 16
            chunk_data_end = chunk_data_start + chunk_size

            if chunk_data_end > len(data):
                result['valid'] = False
                result['errors'].append(f"Chunk {chunk_type} size {chunk_size} exceeds remaining data")
                break

            # Special handling for COREMESH v2
            extra = ""
            if chunk_type == "COREMESH" and chunk_version == 2 and chunk_size >= 4:
                draco_size = struct.unpack_from("<I", data, chunk_data_start)[0]
                extra = f" (Draco bitstream size: {draco_size})"

            result['chunks'].append({
                'type': chunk_type,
                'version': chunk_version,
                'size': chunk_size,
                'offset': pos,
                'extra': extra,
            })
            total_chunk_data += 16 + chunk_size
            pos = chunk_data_end

        result['bytes_accounted'] = total_chunk_data
        result['expected_remaining'] = total_chunk_data
        result['actual_remaining'] = len(data) - (pos - total_chunk_data)
    except Exception as e:
        result['valid'] = False
        result['errors'].append(f"Parse error: {e}")
    return result


def inspect_csgmdl(data: bytes) -> dict:
    """Inspect CSGMDL (Union/CSG geometry) - V2/V4/V5."""
    result = {'version': 'CSGMDL', 'valid': True, 'errors': [], 'bytes_accounted': 0}
    if len(data) < 10:
        result['valid'] = False
        result['errors'].append("Too small for CSGMDL")
        return result

    magic = data[:10]
    CSGMDL_V5_MAGIC = b"\x15\x7d\x29\x15\x75\x6c\x35\x04\x34\x69"
    CSGMDL_V4_MAGIC = b"\x15\x7d\x29\x15\x75\x6c\x34\x04\x34\x69"
    CSGMDL_V2_MAGIC = b"\x15\x7d\x29\x15\x75\x6c\x32\x04\x34\x69"

    if magic == CSGMDL_V5_MAGIC:
        result['version'] = 'CSGMDL V5'
        p = 10
        try:
            pos_count = struct.unpack_from("<H", data, p)[0]; p += 2
            p += pos_count * 12  # positions
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
            p += vertex_data_len
            range_marker_count = data[p]; p += 1
            p += range_marker_count * 4
            result['bytes_accounted'] = p
            result['header'] = {
                'pos_count': pos_count,
                'normals_count': normals_count,
                'normals_len': normals_len,
                'color_count': color_count,
                'normal_id_count': normal_id_count,
                'tex_count': tex_count,
                'tangents_count': tangents_count,
                'tangents_len': tangents_len,
                'vertex_count': vertex_count,
                'vertex_data_len': vertex_data_len,
                'range_marker_count': range_marker_count,
            }
        except Exception as e:
            result['valid'] = False
            result['errors'].append(f"V5 parse error: {e}")

    elif magic == CSGMDL_V4_MAGIC:
        result['version'] = 'CSGMDL V4'
        # XOR deobfuscated body
        result['note'] = 'Body is XOR-obfuscated (not inspected here)'

    elif magic == CSGMDL_V2_MAGIC:
        result['version'] = 'CSGMDL V2'
        result['note'] = 'Body is XOR-obfuscated (not inspected here)'

    else:
        result['valid'] = False
        result['errors'].append(f"Unknown CSGMDL magic: {magic.hex()}")

    return result


def inspect_mesh_file(filepath: Path) -> dict:
    """Inspect a single mesh file."""
    data = filepath.read_bytes()
    result = {
        'file': filepath.name,
        'path': str(filepath),
        'size': len(data),
        'version_header': '',
        'details': {},
        'valid': True,
        'errors': [],
    }

    # Check for CSGMDL magic first
    if len(data) >= 10:
        magic = data[:10]
        CSGMDL_V5_MAGIC = b"\x15\x7d\x29\x15\x75\x6c\x35\x04\x34\x69"
        CSGMDL_V4_MAGIC = b"\x15\x7d\x29\x15\x75\x6c\x34\x04\x34\x69"
        CSGMDL_V2_MAGIC = b"\x15\x7d\x29\x15\x75\x6c\x32\x04\x34\x69"
        if magic in (CSGMDL_V5_MAGIC, CSGMDL_V4_MAGIC, CSGMDL_V2_MAGIC):
            csg_result = inspect_csgmdl(data)
            result['version_header'] = 'CSGMDL'
            result['details'] = csg_result
            result['valid'] = csg_result['valid']
            result['errors'] = csg_result['errors']
            return result

    # Check for version header
    if data.startswith(b"version "):
        version_header, pos = read_version_header(data)
        result['version_header'] = version_header

        # Dispatch to version-specific inspector
        if "version 1." in version_header:
            result['details'] = {'version': '1.x (ASCII)', 'note': 'ASCII format, not byte-inspected'}
        elif "version 2." in version_header:
            result['details'] = inspect_v2(data, pos)
        elif "version 3." in version_header:
            result['details'] = inspect_v3(data, pos)
        elif "version 4." in version_header:
            result['details'] = inspect_v4(data, pos)
        elif "version 5." in version_header:
            result['details'] = inspect_v5(data, pos)
        elif "version 6." in version_header:
            result['details'] = inspect_v6(data, pos)
        elif "version 7." in version_header:
            result['details'] = inspect_v7(data, pos)
        else:
            result['details'] = {'version': 'unknown', 'note': f'Unrecognized version: {version_header}'}
            result['valid'] = False
            result['errors'].append(f'Unrecognized version header: {version_header}')

        result['valid'] = result['details'].get('valid', True)
        result['errors'] = result['details'].get('errors', [])
    else:
        result['version_header'] = 'NO VERSION HEADER'
        result['valid'] = False
        result['errors'].append('File does not start with "version " header')

    return result


def extract_meshes_from_rbxm(rbxm_path: Path) -> list[tuple[str, bytes]]:
    """Extract embedded .mesh files from RBXM binary."""
    # Simple extraction: look for "version " strings in the binary
    data = rbxm_path.read_bytes()
    meshes = []
    pos = 0
    while True:
        idx = data.find(b"version ", pos)
        if idx == -1:
            break
        # Found a version header, extract from here to next "version " or end
        next_idx = data.find(b"version ", idx + 1)
        if next_idx == -1:
            mesh_data = data[idx:]
        else:
            mesh_data = data[idx:next_idx]
        # Trim trailing garbage - find last valid byte
        meshes.append((f"{rbxm_path.stem}_mesh_{len(meshes)}", mesh_data))
        pos = idx + 1
    return meshes


def main():
    base = Path("/root/3DRBX-MT")
    scan_dirs = [
        base / "samples" / "meshes_raw",
        base / "samples" / "downloads",
    ]

    all_results = []
    version_counter = Counter()

    # Scan directories
    for scan_dir in scan_dirs:
        if not scan_dir.exists():
            continue
        for filepath in scan_dir.iterdir():
            if filepath.is_file() and not filepath.name.endswith(('.rbxm', '.rbxmx', '.txt', '.log', '.gltf', '.zip')):
                result = inspect_mesh_file(filepath)
                all_results.append(result)
                version_counter[result['version_header']] += 1

    # Also extract meshes from RBXM files
    rbxm_dir = base / "samples"
    for rbxm_file in rbxm_dir.glob("*.rbxm"):
        extracted = extract_meshes_from_rbxm(rbxm_file)
        for name, mesh_data in extracted:
            # Write to temp file for inspection
            import tempfile
            with tempfile.NamedTemporaryFile(mode='wb', suffix='.mesh', delete=False) as f:
                f.write(mesh_data)
                temp_path = Path(f.name)
            try:
                result = inspect_mesh_file(temp_path)
                result['file'] = name
                result['path'] = str(rbxm_file)
                all_results.append(result)
                version_counter[result['version_header']] += 1
            finally:
                temp_path.unlink(missing_ok=True)

    # Print results
    print("=" * 80)
    print("MESH INSPECTION REPORT")
    print("=" * 80)

    for result in all_results:
        print(f"\nFile: {result['file']}")
        print(f"  Path: {result['path']}")
        print(f"  Size: {result['size']} bytes")
        print(f"  Version Header: {result['version_header']}")
        print(f"  Valid: {result['valid']}")

        details = result['details']
        if 'header' in details:
            print(f"  Header: {details['header']}")
        if 'chunks' in details:
            print(f"  Chunks:")
            for chunk in details['chunks']:
                extra = chunk.get('extra', '')
                print(f"    {chunk['type']} v{chunk['version']} size={chunk['size']} offset={chunk['offset']}{extra}")
        if 'bytes_accounted' in details:
            print(f"  Bytes Accounted: {details['bytes_accounted']}")
            if 'expected_remaining' in details:
                print(f"  Expected Remaining: {details['expected_remaining']}")
                print(f"  Actual Remaining: {details['actual_remaining']}")
        if result['errors']:
            for err in result['errors']:
                print(f"  ERROR: {err}")

    # Version histogram
    print("\n" + "=" * 80)
    print("VERSION HISTOGRAM")
    print("=" * 80)
    for ver, count in sorted(version_counter.items(), key=lambda x: -x[1]):
        print(f"  {ver}: {count}")

    # Summary
    total = len(all_results)
    valid = sum(1 for r in all_results if r['valid'])
    invalid = total - valid
    print(f"\nTotal files inspected: {total}")
    print(f"Valid: {valid}")
    print(f"Invalid/Anomalies: {invalid}")

    if invalid > 0:
        print("\nANOMALIES:")
        for result in all_results:
            if not result['valid']:
                print(f"  {result['file']}: {result['errors']}")

    return 0 if invalid == 0 else 1


if __name__ == "__main__":
    sys.exit(main())