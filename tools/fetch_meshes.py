import os, sys, json, gzip, time, collections, urllib.request
key = os.environ.get("ROBLOX_API_KEY")
if not key: sys.exit("ROBLOX_API_KEY not set (enter it with read -s first)")
def req(url, hdr=None):
    r = urllib.request.Request(url, headers=dict({"User-Agent": "mesh-corpus/1.0"}, **(hdr or {})))
    with urllib.request.urlopen(r, timeout=60) as x: return x.read()
hist = collections.Counter()
for line in open(sys.argv[1]):
    i = line.strip()
    if not i.isdigit(): continue
    out = f"samples/meshes_raw/{i}.mesh"
    if os.path.exists(out): data = open(out, "rb").read()
    else:
        try:
            meta = json.loads(req(f"https://apis.roblox.com/asset-delivery-api/v1/assetId/{i}", {"x-api-key": key}))
            data = req(meta["location"])
            if data[:2] == b"\x1f\x8b": data = gzip.decompress(data)
            open(out, "wb").write(data)
        except Exception as e:
            hist["fail"] += 1; print(i, "FAIL", type(e).__name__); time.sleep(1); continue
        time.sleep(1)
    head = data[:12].decode("ascii", "replace")
    hist[head] += 1; print(i, len(data), repr(head))
print("\nVERSION COUNTS:")
for k, n in hist.most_common(): print(n, repr(k))
