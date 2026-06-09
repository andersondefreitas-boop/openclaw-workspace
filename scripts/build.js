import { mkdir, cp, access } from 'node:fs/promises';

await access('index.html');
await access('src/app.js');
await access('src/styles.css');
await mkdir('dist', { recursive: true });
await cp('index.html', 'dist/index.html');
await cp('src', 'dist/src', { recursive: true });
console.log('Build estático gerado em dist/');
