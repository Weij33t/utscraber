import { cp, mkdir, writeFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

export function apiConfig(input = '') {
  const value = input.trim().replace(/\/+$/, '');
  if (value) {
    const url = new URL(value);
    if (url.protocol !== 'https:' || url.username || url.password || url.search || url.hash) {
      throw new Error('TEKSTOM_API must be a public HTTPS API URL without credentials, query or fragment.');
    }
  }
  return `// Generated for GitHub Pages. Public API URL; no secrets.\nwindow.TEKSTOM_API = ${JSON.stringify(value)};\n`;
}

async function build() {
  const root = fileURLToPath(new URL('../', import.meta.url));
  const config = apiConfig(process.env.TEKSTOM_API);
  const out = path.join(root, 'dist');
  await mkdir(out, { recursive: true });
  await cp(path.join(root, 'web'), out, { recursive: true });
  await writeFile(path.join(out, 'config.js'), config);
  await writeFile(path.join(out, '.nojekyll'), '');
  await writeFile(path.join(out, 'version.json'), JSON.stringify({
    commit: process.env.GITHUB_SHA || 'local',
    builtAt: new Date().toISOString(),
  }) + '\n');
  if (!process.env.TEKSTOM_API?.trim()) {
    console.warn('API is not configured: the site will show API connection settings. Set repository variable TEKSTOM_API when a backend is available.');
  }
  console.log('GitHub Pages artifact prepared in dist/');
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  build().catch(error => { console.error(error.message); process.exitCode = 1; });
}
