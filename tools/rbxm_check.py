#!/usr/bin/env python3
"""
rbxm_check.py - independent reference dump of a Roblox binary model (.rbxm/.rbxl).
Use it to cross-check any RBXM decoder (e.g. your Rust parser or Claude Code's tools/rbxm_dump.py).

  pip install lz4
  python3 rbxm_check.py file.rbxm            # summary + key properties
  python3 rbxm_check.py file.rbxm --all      # every property of every instance
  python3 rbxm_check.py file.rbxm --sstr     # also hex-dump the start of each SharedString

Format notes (verified against a real file):
 - header: '<roblox!' 89 FF 0D 0A 1A 0A, u16 version, i32 classCount, i32 instCount, 8 reserved bytes
 - chunks: 4-char name, u32 compressedLen, u32 uncompressedLen, u32 reserved, data (LZ4 block; compressedLen 0 = stored raw)
 - chunk names: META, SSTR, INST, PROP, PRNT, END
 - arrays of ints/floats are byte-interleaved (byte j of value i at j*N+i); ints use zigzag, floats are rotated left by 1 bit
"""
import struct, sys
import lz4.block

KEY = {'Name','MeshId','MeshID','TextureID','ColorMap','Color3uint8','size','Size','InitialSize','Material',
       'Transparency','PhysicalConfigData','ModelMeshData','SourceAssetId','CFrame','Position','Reflectance'}
TYPES = {1:'String',2:'Bool',3:'Int32',4:'Float',5:'Double',6:'UDim',7:'UDim2',8:'Ray',9:'Faces',10:'Axis',
         11:'BrickColor',12:'Color3',13:'Vector2',14:'Vector3',16:'CFrame',18:'Enum',19:'Referent',
         20:'Vector3int16',21:'NumberSequence',22:'ColorSequence',23:'NumberRange',24:'Rect',
         25:'PhysicalProperties',26:'Color3uint8',27:'Int64',28:'SharedString',30:'UniqueId',31:'Font'}

def deint(b, n, w):
    return [int.from_bytes(bytes(b[j*n+i] for j in range(w)), 'big') for i in range(n)]
def zz(v):  return (v >> 1) ^ -(v & 1)
def f32(u):
    u = ((u >> 1) | (u << 31)) & 0xFFFFFFFF
    return struct.unpack('<f', struct.pack('<I', u))[0]
def rstr(b, p):
    n = struct.unpack('<I', b[p:p+4])[0]
    return b[p+4:p+4+n], p+4+n

def read_chunks(data):
    assert data[:8] == b'<roblox!', 'not a Roblox binary file'
    ver, ncls, ninst = struct.unpack('<HII', data[14:24])
    pos, out = 32, []
    while pos < len(data):
        name = data[pos:pos+4].decode('latin1').strip('\x00')
        clen, ulen, _ = struct.unpack('<III', data[pos+4:pos+16]); pos += 16
        if clen == 0: body = data[pos:pos+ulen]; pos += ulen
        else: body = lz4.block.decompress(data[pos:pos+clen], uncompressed_size=ulen); pos += clen
        out.append((name, body))
        if name == 'END': break
    return ver, ncls, ninst, out

def decode_values(t, body, n):
    if t == 1:
        vals, p = [], 0
        for _ in range(n):
            s, p = rstr(body, p); vals.append(s.decode('utf-8', 'replace'))
        return vals
    if t == 2:  return [bool(x) for x in body[:n]]
    if t == 3:  return [zz(v) for v in deint(body, n, 4)]
    if t == 4:  return [round(f32(v), 5) for v in deint(body, n, 4)]
    if t == 18: return deint(body, n, 4)
    if t == 19:
        out, acc = [], 0
        for v in deint(body, n, 4): acc += zz(v); out.append(acc)
        return out
    if t == 28: return ['sstr#%d' % v for v in deint(body, n, 4)]
    if t == 27: return [zz(v) for v in deint(body, n, 8)]
    if t == 26:
        return [(body[i], body[n+i], body[2*n+i]) for i in range(n)]
    if t == 14:
        xs = [round(f32(v), 4) for v in deint(body[0:4*n], n, 4)]
        ys = [round(f32(v), 4) for v in deint(body[4*n:8*n], n, 4)]
        zs = [round(f32(v), 4) for v in deint(body[8*n:12*n], n, 4)]
        return list(zip(xs, ys, zs))
    return ['<%d bytes, undecoded type>' % len(body)]

def main():
    path = sys.argv[1]; show_all = '--all' in sys.argv; show_sstr = '--sstr' in sys.argv
    data = open(path, 'rb').read()
    ver, ncls, ninst, chunks = read_chunks(data)
    print(f'{path}: {len(data)} bytes | format version {ver} | {ncls} classes | {ninst} instances')
    classes, sstr = {}, []
    for name, b in chunks:
        if name == 'META':
            k = struct.unpack('<I', b[:4])[0]; p = 4
            for _ in range(k):
                kk, p = rstr(b, p); vv, p = rstr(b, p); print('META', kk.decode(), '=', vv.decode())
        elif name == 'SSTR':
            cnt = struct.unpack('<I', b[4:8])[0]; p = 8
            for i in range(cnt):
                p += 16; s, p = rstr(b, p); sstr.append(s)
                print(f'SSTR #{i}: {len(s)} bytes, starts with {s[:6]!r}')
                if show_sstr: print('   ', s[:48].hex())
        elif name == 'INST':
            cid = struct.unpack('<I', b[:4])[0]; cname, p = rstr(b, 4)
            n = struct.unpack('<I', b[p+1:p+5])[0]
            refs, acc = [], 0
            for v in deint(b[p+5:p+5+4*n], n, 4): acc += zz(v); refs.append(acc)
            classes[cid] = (cname.decode(), n, refs)
            print(f'INST class {cid}: {cname.decode()} x{n} referents={refs}')
    print('--- properties' + (' (all)' if show_all else ' (key ones; use --all for everything)') + ' ---')
    for name, b in chunks:
        if name != 'PROP': continue
        cid = struct.unpack('<I', b[:4])[0]; pn, p = rstr(b, 4); pn = pn.decode(); t = b[p]; body = b[p+1:]
        if not show_all and pn not in KEY: continue
        cname, n, refs = classes[cid]
        vals = decode_values(t, body, n)
        for r, v in zip(refs, vals):
            print(f'{cname}[{r}].{pn} ({TYPES.get(t, t)}) = {v}')
    for name, b in chunks:
        if name == 'PRNT':
            n = struct.unpack('<I', b[1:5])[0]
            kids, parents, acc1, acc2 = [], [], 0, 0
            for v in deint(b[5:5+4*n], n, 4): acc1 += zz(v); kids.append(acc1)
            for v in deint(b[5+4*n:5+8*n], n, 4): acc2 += zz(v); parents.append(acc2)
            print('--- hierarchy (child -> parent referent) ---')
            for c, pr in zip(kids, parents): print(f'  {c} -> {pr}')

if __name__ == '__main__':
    if len(sys.argv) < 2: print(__doc__); sys.exit(1)
    main()
