// Local-only preview; API requests proxy only when PSOP_API_ORIGIN is explicitly set.
const http = require('node:http');
const fs = require('node:fs');
const path = require('node:path');
const root = path.resolve(__dirname, '..');
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript', '.css': 'text/css', '.json': 'application/json', '.woff2': 'font/woff2', '.svg': 'image/svg+xml', '.png': 'image/png' };
http.createServer(async (req, res) => {
  try {
    const url = new URL(req.url, 'http://localhost');
    if (url.pathname.startsWith('/api/') || url.pathname === '/health') {
      if (!process.env.PSOP_API_ORIGIN) {
        res.writeHead(503, { 'Content-Type': 'application/json' });
        return res.end(JSON.stringify({ detail: '未连接 API。设置 PSOP_API_ORIGIN 后重启预览服务。' }));
      }
      const response = await fetch(new URL(req.url, process.env.PSOP_API_ORIGIN), {
        method: req.method, headers: { 'content-type': req.headers['content-type'] || 'application/json' },
        ...(req.method === 'GET' || req.method === 'HEAD' ? {} : { body: req, duplex: 'half' }),
      });
      res.writeHead(response.status, { 'Content-Type': response.headers.get('content-type') || 'application/octet-stream' });
      if (response.body) for await (const chunk of response.body) res.write(chunk);
      return res.end();
    }
    const pathname = decodeURIComponent(url.pathname);
    const entry = pathname === '/' || /^\/(videos(?:\/[^/]+)?|uploads|usage)\/?$/.test(pathname);
    const file = path.resolve(root, entry ? 'index.html' : '.' + pathname);
    if (!file.startsWith(root + path.sep) || pathname.split('/').some(part => part.startsWith('.'))) {
      res.writeHead(403); return res.end('Forbidden');
    }
    if (!fs.existsSync(file) || !fs.statSync(file).isFile()) { res.writeHead(404); return res.end('Not found'); }
    res.writeHead(200, { 'Content-Type': types[path.extname(file)] || 'application/octet-stream', 'Cache-Control': 'no-store' });
    fs.createReadStream(file).pipe(res);
  } catch { res.writeHead(502); res.end('Preview request failed'); }
}).listen(Number(process.env.PORT || 4173), '127.0.0.1', () => console.log(`PSOP preview: http://127.0.0.1:${process.env.PORT || 4173}`));
