from flask import Flask, jsonify, request, Response
import httpx, os, io, zipfile, time, json, re
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
API_KEY = os.getenv("ROBLOX_API_KEY","")
TIMEOUT = 15

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

def hdr(auth=False):
    h = {"User-Agent":"Mozilla/5.0","Accept":"application/json"}
    if auth and COOKIE: h["Cookie"] = f".ROBLOSECURITY={COOKIE}"
    if API_KEY: h["x-api-key"] = API_KEY
    return h

def rget(url):
    # Cookie hanya untuk assetdelivery (download asset privat)
    needs_auth = "assetdelivery.roblox.com" in url
    with httpx.Client(timeout=TIMEOUT,follow_redirects=True) as c:
        r = c.get(url,headers=hdr(auth=needs_auth)); r.raise_for_status(); return r.json()

def rget_bytes(url):
    needs_auth = "assetdelivery.roblox.com" in url
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

def get_3d_manifest(uid, retries=6):
    url = f"https://thumbnails.roblox.com/v1/users/avatar-3d?userId={uid}"
    for i in range(retries):
        try:
            s = get_scraper()
            r = s.get(url, timeout=15)
            if r.status_code != 200:
                print(f"[3d manifest] status {r.status_code}: {r.text[:100]}")
                time.sleep(3)
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
    return jsonify({"status":"ok","cookie_set":bool(COOKIE)})

@app.get("/api/avatar/info")
def avatar_info():
    user=request.args.get("user","")
    if not user: return jsonify({"error":"user required"}),400
    try:
        uid=resolve(user)
        info=rget(f"https://users.roblox.com/v1/users/{uid}")
        av=rget(f"https://avatar.roblox.com/v1/users/{uid}/avatar")
        th=rget(f"https://thumbnails.roblox.com/v1/users/avatar?userIds={uid}&size=420x420&format=Png")
        return jsonify({"userId":uid,"username":info.get("name"),"displayName":info.get("displayName"),
            "created":info.get("created"),"rigType":av.get("playerAvatarType"),
            "scales":av.get("scales"),"bodyColors":av.get("bodyColors"),
            "assets":av.get("assets",[]),
            "thumbnailUrl":(th.get("data") or [{}])[0].get("imageUrl"),
            "profileUrl":f"https://www.roblox.com/users/{uid}/profile"})
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
                if m.get("obj"): zf.writestr(f"{name}.obj",rget_bytes(fix_url(m["obj"])))
                if m.get("mtl"): zf.writestr(f"{name}.mtl",rget_bytes(fix_url(m["mtl"])))
                for i,tx in enumerate(m.get("textures",[])):
                    try: zf.writestr(f"textures/texture_{i}.png",rget_bytes(fix_url(tx)))
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
        d = rpost("https://catalog.roblox.com/v1/catalog/items/details",{"items":[{"itemType":"Asset","id":int(aid)}]})
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
                from services.mesh_converter import parse_mesh, mesh_to_obj
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
        mp = os.path.join(base,"maintenance.html")
        if os.path.exists(mp): return open(mp).read(),503,{"Content-Type":"text/html"}
    p = os.path.join(base,"index.html")
    if os.path.exists(p): return open(p).read(),200,{"Content-Type":"text/html"}
    return "<h1>Roblox Downloader</h1>"

@app.get("/maintenance")
def maintenance_preview():
    """Preview maintenance page langsung"""
    p=os.path.join(os.path.dirname(__file__),"..","frontend","maintenance.html")
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
                        try: zf.writestr(f"textures/texture_{i}.png", rget_bytes(fix_url(tx)))
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
    except Exception as e: return jsonify({"error":str(e)}),500

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
    except Exception as e: return jsonify({"error":str(e)}),500

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

if __name__ == "__main__":
    port=int(os.getenv("PORT",8000))
    print(f"Server jalan di http://0.0.0.0:{port}")
    app.run(host="0.0.0.0",port=port,debug=False)

# Railway / production WSGI entry point
application = app
