# Credits & Acknowledgements

## Faizdzn — rblxapi_backend
https://github.com/Faizdzn/rblxapi_backend

The initial technique for resolving a Roblox 3D-thumbnail manifest's hash
references into working `rbxcdn.com` URLs (the `get_hash_url` /
`get_obj_urls` hashing approach) was originally learned from this project,
around early 2026. It's an archived, unlicensed personal repo — no LICENSE
file is present upstream — so it isn't covered by 3DRBX-MT's own MIT license
(see LICENSE).

**3DRBX-MT has since diverged substantially from that origin.** What started
as a single hash-resolution trick has grown into its own project with:
- Full avatar + catalog + audio + bundle download support
- Real mesh + MTL + texture packing (not just raw hash lookups)
- Batch/"Packed Catalog" downloads — many catalog items or bundles into one ZIP
- Bundle auto-resolution (an ID that isn't a plain Asset transparently
  resolves to its component assets)
- Client-side OBJ→GLB conversion
- A completely independent Flask/FastAPI backend and web UI

If you're the author of rblxapi_backend and have any concerns about this
attribution or usage, please open an issue on this repo and it'll be
addressed right away.

## Roblox Corporation
All downloaded meshes, textures, and catalog/bundle metadata remain the
property of Roblox Corporation and/or the original asset creators. 3DRBX-MT
is an unofficial tool and is not affiliated with or endorsed by Roblox
Corporation.
