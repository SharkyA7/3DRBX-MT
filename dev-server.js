#!/usr/bin/env node
// Dev server with devtools-like logging for 3DRBX-MT
// Usage: node dev-server.js [port]

const http = require('http');
const fs = require('fs');
const path = require('path');
const url = require('url');

const PORT = process.argv[2] || 8080;
const ROOT = process.cwd();

// MIME types
const MIME_TYPES = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg',
  '.gif': 'image/gif',
  '.svg': 'image/svg+xml',
  '.ico': 'image/x-icon',
  '.woff': 'font/woff',
  '.woff2': 'font/woff2',
  '.ttf': 'font/ttf',
  '.wasm': 'application/wasm',
  '.txt': 'text/plain; charset=utf-8',
  '.xml': 'application/xml; charset=utf-8',
  '.pdf': 'application/pdf',
  '.zip': 'application/zip',
  '.rbxm': 'application/octet-stream',
  '.obj': 'text/plain; charset=utf-8',
  '.mtl': 'text/plain; charset=utf-8',
  '.gltf': 'model/gltf+json',
  '.glb': 'model/gltf-binary',
  '.bin': 'application/octet-stream',
};

function getMimeType(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  return MIME_TYPES[ext] || 'application/octet-stream';
}

function formatBytes(bytes) {
  if (bytes < 1024) return `${bytes}B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)}KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)}MB`;
}

function logRequest(req, res, startTime, filePath, statusCode, bytes) {
  const duration = Date.now() - startTime;
  const method = req.method;
  const reqUrl = req.url;
  const status = statusCode;
  const size = formatBytes(bytes);
  const ip = req.socket.remoteAddress || 'unknown';

  // Color codes for status
  let statusColor = '\x1b[32m'; // green for 2xx
  if (status >= 400 && status < 500) statusColor = '\x1b[33m'; // yellow for 4xx
  if (status >= 500) statusColor = '\x1b[31m'; // red for 5xx
  const reset = '\x1b[0m';

  const timeStr = new Date().toISOString().replace('T', ' ').substring(0, 19);

  console.log(`${timeStr} | ${method} | ${statusColor}${status}${reset} | ${duration}ms | ${size} | ${reqUrl}`);

  // Log request headers for debugging
  if (status >= 400) {
    console.log(`  → Request headers:`, JSON.stringify(req.headers, null, 2));
  }

  // Log response headers for errors
  if (status >= 400) {
    console.log(`  → Response headers:`, res.getHeaders());
  }
}

function serveFile(req, res, filePath) {
  const startTime = Date.now();

  // Security: prevent directory traversal
  const resolvedPath = path.resolve(filePath);
  const resolvedRoot = path.resolve(ROOT);
  if (!resolvedPath.startsWith(resolvedRoot)) {
    res.writeHead(403, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('403 Forbidden');
    logRequest(req, res, startTime, filePath, 403, 0);
    return;
  }

  // Check if file exists
  fs.stat(filePath, (err, stats) => {
    if (err || !stats.isFile()) {
      // Try index.html for directories
      if (filePath.endsWith('/')) {
        serveFile(req, res, path.join(filePath, 'index.html'));
      } else {
        res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
        res.end('404 Not Found');
        logRequest(req, res, startTime, filePath, 404, 0);
      }
      return;
    }

    const mimeType = getMimeType(filePath);
    const fileSize = stats.size;

    // Set headers
    res.writeHead(200, {
      'Content-Type': mimeType,
      'Content-Length': fileSize,
      'Cache-Control': 'no-cache', // Dev server - no cache
      'Access-Control-Allow-Origin': '*',
    });

    // Stream the file
    const stream = fs.createReadStream(filePath);
    let bytesSent = 0;

    stream.on('data', (chunk) => {
      bytesSent += chunk.length;
    });

    stream.on('error', (err) => {
      console.error(`  → Stream error:`, err.message);
      if (!res.headersSent) {
        res.writeHead(500, { 'Content-Type': 'text/plain; charset=utf-8' });
        res.end('500 Internal Server Error');
      }
      logRequest(req, res, startTime, filePath, 500, bytesSent);
    });

    stream.pipe(res);

    stream.on('end', () => {
      logRequest(req, res, startTime, filePath, 200, bytesSent);
    });
  });
}

const server = http.createServer((req, res) => {
  const parsedUrl = url.parse(req.url, true);
  let filePath = path.join(ROOT, parsedUrl.pathname);

  // Default to index.html for root
  if (parsedUrl.pathname === '/' || parsedUrl.pathname === '') {
    filePath = path.join(ROOT, 'index.html');
  }

  serveFile(req, res, filePath);
});

server.listen(PORT, () => {
  console.log('\n╔═══════════════════════════════════════════════════════════════╗');
  console.log(`║  3DRBX-MT Dev Server                                         ║`);
  console.log(`║  ──────────────────────────────────────────────────────────  ║`);
  console.log(`║  Serving: ${ROOT.padEnd(50)} ║`);
  console.log(`║  URL:     http://localhost:${PORT}                                     ║`);
  console.log(`║  UV Checker: http://localhost:${PORT}/uv-checker.html                   ║`);
  console.log(`║                                                                ║`);
  console.log(`║  Log format: TIME | METHOD | STATUS | DURATION | SIZE | URL  ║`);
  console.log(`╚═══════════════════════════════════════════════════════════════╝\n`);
});

server.on('error', (err) => {
  if (err.code === 'EADDRINUSE') {
    console.error(`Port ${PORT} is already in use. Try a different port:`);
    console.error(`  node dev-server.js ${parseInt(PORT) + 1}`);
  } else {
    console.error('Server error:', err);
  }
  process.exit(1);
});

// Graceful shutdown
process.on('SIGINT', () => {
  console.log('\nShutting down dev server...');
  server.close(() => process.exit(0));
});