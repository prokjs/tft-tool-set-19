// Serve only the generated standalone frontend; no calculation endpoints.
// First run npm run build:web, then npm run preview -- --port 8765.
import { createServer } from 'node:http';
import { createReadStream } from 'node:fs';
import { realpath, stat } from 'node:fs/promises';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const args = process.argv.slice(2);
if (args.length !== 0 && (args.length !== 2 || args[0] !== '--port')) {
  throw new Error('Usage: npm run preview -- [--port 8765]');
}
const port = args.length ? Number(args[1]) : 8765;
if (!Number.isInteger(port) || port < 0 || port > 65535) throw new Error('Port must be an integer between 0 and 65535.');
const root = await realpath(fileURLToPath(new URL('../frontend-dist/', import.meta.url)));
const mime = {
  '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8', '.json': 'application/json; charset=utf-8',
  '.wasm': 'application/wasm', '.png': 'image/png', '.jpg': 'image/jpeg',
  '.jpeg': 'image/jpeg', '.webp': 'image/webp', '.gif': 'image/gif',
  '.svg': 'image/svg+xml', '.ico': 'image/x-icon', '.woff': 'font/woff',
  '.woff2': 'font/woff2', '.ttf': 'font/ttf', '.otf': 'font/otf',
  '.txt': 'text/plain; charset=utf-8', '.md': 'text/plain; charset=utf-8', '.rs': 'text/plain; charset=utf-8',
};
function reply(response, code, text) {
  response.writeHead(code, { 'Content-Type': 'text/plain; charset=utf-8' });
  response.end(text);
}
const server = createServer(async (request, response) => {
  response.setHeader('Cache-Control', 'no-store');
  response.setHeader('X-Content-Type-Options', 'nosniff');
  response.setHeader('Referrer-Policy', 'no-referrer');
  if (request.method !== 'GET' && request.method !== 'HEAD') {
    response.setHeader('Allow', 'GET, HEAD');
    reply(response, 405, 'Static files only.');
    return;
  }
  try {
    let pathname = decodeURIComponent(new URL(request.url, 'http://127.0.0.1').pathname);
    if (pathname === '/') {
      response.writeHead(302, { Location: '/web/' });
      response.end();
      return;
    }
    if (pathname === '/web/') pathname = '/web/index.html';
    if (pathname.includes('\\') || pathname.includes('\0')) {
      reply(response, 404, 'Not found.');
      return;
    }
    const candidate = path.resolve(root, `.${pathname}`);
    const relative = path.relative(root, candidate);
    if (relative.startsWith('..') || path.isAbsolute(relative) || relative.split(path.sep).some(part => part.startsWith('.'))) {
      reply(response, 404, 'Not found.');
      return;
    }
    const filename = await realpath(candidate);
    const resolvedRelative = path.relative(root, filename);
    if (resolvedRelative.startsWith('..') || path.isAbsolute(resolvedRelative)) {
      reply(response, 404, 'Not found.');
      return;
    }
    const info = await stat(filename);
    if (!info.isFile()) {
      reply(response, 404, 'Not found.');
      return;
    }
    response.writeHead(200, { 'Content-Type': mime[path.extname(filename).toLowerCase()] || 'application/octet-stream', 'Content-Length': info.size });
    if (request.method === 'HEAD') response.end();
    else createReadStream(filename).on('error', () => response.destroy()).pipe(response);
  } catch {
    reply(response, 404, 'Not found. Build the browser frontend with npm run build:web first.');
  }
});
server.on('error', error => { console.error(error.message); process.exitCode = 1; });
server.listen(port, '127.0.0.1', () => {
  console.log(`Local Rust/WASM preview: http://127.0.0.1:${server.address().port}/web/`);
});
