# CLAUDE.md: rules for editing 3DRBXMT (read fully before touching anything)

This site is live in production. A single syntax error in an HTML page kills every button on it. Past breakages came from: a missing `}`, `await` in a non-async function, `element.getElementById(...)`, and an old copy of a file overwriting newer work. Follow this file exactly.

## 1. Which files are real
- Root `index.html`, `uv-checker.html`, `sw.js`, `manifest.json` are the LIVE frontend.
- `public/` holds synced copies used by the Cloudflare deploy. After editing any of those four root files, copy it to `public/` (same name).
- `frontend/index.html` is a stale Railway-era copy. NEVER edit it, never restore from it.
- `backend/main.py` is the Flask API (Vercel). `backend/services/mesh_converter.py` is dead code; do not edit it expecting an effect.
- `worker.js` / `wrangler.jsonc` are the Cloudflare edge cache (mesh, mesh-union, texture, catalog image routes). Do not change routes or TTL unless asked.
- `wasm-model-parser/` and `rust-model-parser/` are Rust. Do not touch them unless the task is about them. Never commit `target/` folders.

## 2. Before you edit
1. Run `git status` and `git log --oneline -10`. Another Claude session may have changed the same file. If the working tree is dirty with changes you did not make, stop and tell me.
2. Read the function you will change AND the code that calls it. Search with grep for every use of a name before you rename, move or delete it.
3. Work on the live file (root), never on an older upload or a copy from memory. Do not "restore" a whole file from an older version: you will silently delete newer features (Fetch Avatar, UV Bake buttons, Cages card, EMOTE card, Packed Catalog, API key system).

## 3. How to edit
- Make the smallest possible change. No refactors, renames, reformatting, or "cleanup" outside the task. Do not remove a feature to fix a bug.
- Use targeted edits (str_replace style). Never rewrite or re-paste an entire HTML file.
- One task = one change set. Do not bundle unrelated fixes.
- Keep the existing style and the bilingual ID/EN text system. Every new user-visible string needs both languages.
- Never delete, rename or change the meaning of an existing element id, API route, query parameter, JSON field or Supabase key. Other pages, the Discord bots and API-key users depend on them.

## 4. JavaScript rules (the things that broke before)
- `await` only inside an `async` function. When you add `await` to a function, mark it `async` and make every caller handle the Promise (`await` it or `.catch`).
- Every `{`, `(` and backtick must be closed. After editing inside a function, especially around `try/catch`, re-read the whole function end to end.
- `getElementById` exists ONLY on `document`. Inside an element use `el.querySelector('#id')`.
- Null-guard lookups of elements that may not exist: `document.getElementById('x')?.addEventListener(...)`.
- In `uv-checker.html` all buttons are wired with `addEventListener` inside one `<script type="module">`. Module functions are NOT global, so do not add inline `onclick="fn()"`. One uncaught error in the init code silently kills every button after it.
- Do not declare the same `const`/`let` name twice in one scope.
- Do not use `localStorage` keys or storage names that already exist for something else.

## 5. Service worker and PWA (`sw.js`)
- HTML pages stay network-first. Never make HTML cache-first.
- Never call `skipWaiting()` in the `install` event. The worker only skips waiting after the page sends the `skipWaiting` message (user tapped Update).
- Never auto-reload on `controllerchange` unless the user approved the update.
- If you change cached files or caching logic, bump `VERSION` in `sw.js`.
- Only one `serviceWorker.register` per page.

## 6. Backend rules (`backend/main.py`)
- After editing, run `python3 -m py_compile backend/main.py`.
- Do not change the response shape of existing endpoints (`/api/v2/model/info`, `/api/v2/item`, `/api/catalog/*`, `/api/avatar/wearing`, `/api/catalog/cages`). Add fields, do not remove or rename them.
- Secrets (Roblox API key, Supabase keys, OAuth secrets, API-key signing secrets) come from environment variables. Never hard-code or print them, never commit them.
- API keys always have a user-chosen expiration date. There is no "never expires" option, and keys stay free.
- Maintenance mode and admin toggles live in Supabase. Do not replace them with local flags.

## 7. MANDATORY checks before you say "done" and before every commit
1. Run `node check-scripts.js`. It must print `OK`. If it prints FAIL, fix the problem and run it again. Do not commit while it fails.
2. If you touched the backend, run `python3 -m py_compile backend/main.py`.
3. Open the changed page in a browser with the console open. There must be no red errors on load, and every button you did not touch must still work. Test at least: open the menu, switch tabs, press the buttons next to the thing you changed.
4. Run `git diff --stat`. If files you did not mean to change show up, or the diff is far larger than the task needs, undo and redo it smaller.
5. Confirm root files and `public/` copies are identical (the checker does this).

If you cannot run the browser test, say so explicitly. Do not claim "it works" without running the checks.

## 8. Committing and deploying
- Every push to `main` triggers a full Vercel deployment, and the free plan is near its storage limit. Make ONE commit per finished task, not many small pushes. Do not use "upload files" style commits of many files.
- Commit message: what changed and why, one line.
- Never force-push, never rewrite history, never delete branches.
- Do not touch `.vercelignore`, `vercel.json`, or the Cloudflare config unless the task is specifically about deployment.

## 9. When something goes wrong
- If a change breaks anything, revert with `git checkout -- <file>` or `git revert`, then retry smaller. Do not stack fixes on top of a broken state.
- If you are unsure whether a change is safe, stop and ask me before changing the code.
- Final answer must list: files changed, what was broken or added, and the result of every check in section 7.

## RBXM decoder status (2026-11-05)

Goal: RBXM -> (optional RBXMX debug view) -> OBJ + MTL + texture.

Done and verified on the chair control sample:
- samples/case-control-chair.rbxm: TextureID is a plain String (rbxassetid://9797125350), MeshId/MeshID rbxassetid://9797125202. SharedStrings are physics data only (CSGPHS, plus an empty one).
- tools/rbxm_check.py is the reference decoder. tools/rbxm_dump.py matches it on the chair.
- Pipeline ran end to end: asset IDs -> Open Cloud asset delivery download -> gunzip -> mesh version 4.01 decode (header 24 bytes, 40-byte vertices, 996 verts, 844 faces, 5 LOD offsets, LOD 0 = 590 faces; byte accounting matches the 50,025-byte file) -> out/chair.obj, chair.mtl, chair.png (v written as 1 - v). Opened in Nomad Sculpt with the texture applied.
- chair.png is a palette atlas; the mesh UVs sit in one thin band (v 0.42-0.50).

Rules for this project:
- Never ask for, print or log ROBLOX_API_KEY. It is not available to Claude Code's shell. The user downloads assets in their own Termux shell; work only from files already in samples/downloads/.
- Downloaded assets are gzip-compressed (starts 1f 8b). Decompress before reading.
- A previous sampling script returned all-black results for a texture that is fully colored, so verify image decoding with PIL before trusting texture analysis.

Open items:
1. The original missing-texture asset has not been found. Need a file where TextureID is a SharedString (rbxm_check.py --sstr shows it).
2. The V flip (v = 1 - v) is unproven. The chair's UVs are in one band so color can't confirm it. Test on a model whose UVs span several colored swatches and compare with Roblox.
3. CFrame is not decoded, so multi-part models can't be placed.
4. Not yet tested: other mesh versions, SpecialMesh models, unions (geometry in SharedString), skipping Transparency 1.0 parts, scale from part Size vs InitialSize.

Next step: get a second RBXM with several MeshParts (catalog accessory or Studio "Save to File"), run rbxm_check.py --all --sstr on it, then run our decoder and compare.
