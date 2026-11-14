#!/usr/bin/env python3
import sys, re, json, time, csv, collections, urllib.request, urllib.error, urllib.parse
BASE = "https://getrbx3d.qzz.io"; DELAY = 1.0
def get(path):
    req = urllib.request.Request(BASE + path, headers={"User-Agent": "mesh-survey/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r: return r.status, r.read()
    except urllib.error.HTTPError as e: return e.code, e.read()
    except Exception as e: return 0, str(e).encode()
items = [l.strip() for l in open(sys.argv[1]) if l.strip().isdigit()]
limit = int(sys.argv[2]) if len(sys.argv) > 2 else 100
rows, seen = [], set()
try:
    for item in items:
        if len(seen) >= limit: break
        st, body = get("/api/v2/model/info?id=" + item); time.sleep(DELAY)
        if st != 200: rows.append((item, "", "info_http_%d" % st, "")); continue
        try: d = json.loads(body)
        except Exception: rows.append((item, "", "info_not_json", "")); continue
        mids = []
        for p in d.get("parts", []):
            for m in (p.get("meshId"), (p.get("specialMesh") or {}).get("meshId")):
                if m and m not in mids: mids.append(m)
        for m in mids:
            if m in seen: continue
            mm = re.search(r"(?:rbxassetid://|[?&]id=)(\d+)", m)
            if mm: m = "rbxassetid://" + mm.group(1)
            if m in seen: continue
            seen.add(m)
            st, body = get("/api/v2/model/mesh?meshId=" + urllib.parse.quote(m, safe="")); time.sleep(DELAY)
            if st == 200: rows.append((item, m, "decoded", ""))
            else:
                txt = body.decode("utf-8", "replace")
                v = re.search(r"version (\d+\.\d+)", txt)
                rows.append((item, m, "http_%d" % st, v.group(1) if v else txt[:80].replace("\n", " ")))
            print(len(seen), rows[-1][2:], flush=True)
finally:
    with open("tmp/mesh_survey.csv", "w", newline="") as f:
        csv.writer(f).writerows([("item", "mesh", "status", "version_or_error")] + rows)
    print("\nSUMMARY:")
    for k, n in collections.Counter((r[2], r[3]) for r in rows).most_common(): print(n, k)
