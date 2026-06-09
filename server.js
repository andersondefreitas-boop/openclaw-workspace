import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize } from 'node:path';

const root = process.cwd();
const port = process.env.PORT || 3000;
const types = { '.html': 'text/html; charset=utf-8', '.js': 'text/javascript; charset=utf-8', '.css': 'text/css; charset=utf-8' };

createServer(async (req, res) => {
  const url = new URL(req.url, `http://localhost:${port}`);
  const safe = normalize(url.pathname).replace(/^\/+/, '');
  const file = safe && extname(safe) ? safe : 'index.html';
  try {
    const body = await readFile(join(root, file));
    res.writeHead(200, { 'Content-Type': types[extname(file)] || 'application/octet-stream' });
    res.end(body);
  } catch {
    const body = await readFile(join(root, 'index.html'));
    res.writeHead(200, { 'Content-Type': types['.html'] });
    res.end(body);
  }
}).listen(port, () => console.log(`AnestesiaApp em http://localhost:${port}`));
