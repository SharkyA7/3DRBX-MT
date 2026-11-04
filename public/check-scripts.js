#!/usr/bin/env node
// Syntax-checks every inline <script> in the site's HTML pages and verifies public/ copies match the root files.
// Usage: node check-scripts.js   (exit code 1 = something is broken, do NOT commit/deploy)
const fs = require('fs');
const os = require('os');
const path = require('path');
const { spawnSync } = require('child_process');

const PAGES = ['index.html', 'uv-checker.html', 'beta.html', 'flipbook.html', 'maintenance.html', 'privacy.html', 'terms.html'];
const SYNCED = ['index.html', 'uv-checker.html', 'sw.js', 'manifest.json'];
const MODALS = ['settings-modal', 'uv2d-modal', 'sheet-tools', 'sheet-options', 'sheet-layers'];
let failed = 0;

// Check modal/sheet elements exist and no divs left unclosed
// Only runs on uv-checker.html since other pages don't have these elements
function checkMarkup(html, filename) {
  if (filename !== 'uv-checker.html') return;

  // Only check markup before <script type="importmap">
  const importMapIndex = html.indexOf('<script type="importmap">');
  const markup = importMapIndex > -1 ? html.slice(0, importMapIndex) : html;

  // Parse div nesting
  const stack = [];
  const foundModals = new Set();
  const divOpenRegex = /<div\b[^>]*>/gi;
  const divCloseRegex = /<\/div>/gi;

  let match;
  while ((match = divOpenRegex.exec(markup)) !== null) {
    const tag = match[0];
    const modalMatch = tag.match(/id="(settings-modal|uv2d-modal|sheet-tools|sheet-options|sheet-layers)"/);
    if (modalMatch) foundModals.add(modalMatch[1]);
    stack.push({ tag: 'div', modal: modalMatch ? modalMatch[1] : null });
  }

  while ((match = divCloseRegex.exec(markup)) !== null) {
    if (stack.length > 0) stack.pop();
    else {
      failed++;
      console.error(`FAIL ${filename}: stray </div> at position ${match.index}`);
    }
  }

  // Check all 5 modals exist
  for (const m of MODALS) {
    if (!foundModals.has(m)) {
      failed++;
      console.error(`FAIL ${filename}: #${m} not found in markup`);
    }
  }

  // Check unclosed divs
  if (stack.length > 0) {
    failed++;
    console.error(`FAIL ${filename}: ${stack.length} unclosed <div> tag(s) at end of markup`);
    for (const item of stack.slice(-3)) {
      console.error(`  Last unclosed: <div${item.modal ? ' id="' + item.modal + '"' : ''}>`);
    }
  }
}

for (const page of PAGES) {
  if (!fs.existsSync(page)) continue;
  const html = fs.readFileSync(page, 'utf8');

  // Markup structure check
  checkMarkup(html, page);

  const re = /<script(?![^>]*\bsrc=)([^>]*)>([\s\S]*?)<\/script>/gi;
  let m, i = 0;
  while ((m = re.exec(html))) {
    const attrs = m[1];
    if (/importmap|ld\+json|application\/json/i.test(attrs)) continue;
    const isModule = /type=["']module["']/i.test(attrs);
    const tmp = path.join(os.tmpdir(), `chk_${path.basename(page)}_${i++}${isModule ? '.mjs' : '.js'}`);
    fs.writeFileSync(tmp, m[2]);
    const r = spawnSync(process.execPath, ['--check', tmp], { encoding: 'utf8' });
    if (r.status !== 0) {
      failed++;
      const line = html.slice(0, m.index).split('\n').length;
      console.error(`FAIL ${page} (script starting at line ${line}):\n${r.stderr.split('\n').slice(0, 6).join('\n')}\n`);
    }
  }
}

if (fs.existsSync('sw.js')) {
  const r = spawnSync(process.execPath, ['--check', 'sw.js'], { encoding: 'utf8' });
  if (r.status !== 0) { failed++; console.error('FAIL sw.js\n' + r.stderr); }
}


// Optional: Python syntax check for the backend
const py = spawnSync('python3', ['-m', 'py_compile', 'backend/main.py'], { encoding: 'utf8' });
if (py.error === undefined && py.status !== 0) { failed++; console.error('FAIL backend/main.py\n' + py.stderr); }

for (const f of SYNCED) {
  if (fs.existsSync(f) && fs.existsSync(path.join('public', f)) &&
      fs.readFileSync(f, 'utf8') !== fs.readFileSync(path.join('public', f), 'utf8')) {
    failed++;
    console.error(`FAIL public/${f} is out of sync with ${f} (Cloudflare deploys public/). Copy it over.`);
  }
}

if (failed) { console.error(`\n${failed} problem(s) found.`); process.exit(1); }
console.log('OK: all inline scripts parse, public/ is in sync, backend compiles.');
