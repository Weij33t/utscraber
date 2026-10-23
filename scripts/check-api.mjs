import { apiConfig } from './build-pages.mjs';

try {
  const origin = process.env.TEKSTOM_API?.trim().replace(/\/+$/, '');
  if (!origin) throw new Error('TEKSTOM_API is missing: refusing to deploy a site without its API.');
  apiConfig(origin);
  const response = await fetch(origin + '/api/health', {
    headers: { Origin: 'https://weij33t.github.io' },
    signal: AbortSignal.timeout(20000),
    redirect: 'error',
  });
  if (!response.ok) throw new Error('API health check failed: HTTP ' + response.status);
  const data = await response.json();
  if (!data.ok || data.service !== 'tekstom') throw new Error('Unexpected API health response.');
  if (response.headers.get('access-control-allow-origin') !== 'https://weij33t.github.io') throw new Error('API does not allow the GitHub Pages origin (CORS).');
  console.log('Public API is available and permits requests from GitHub Pages.');
} catch (error) {
  console.error(error.message);
  process.exitCode = 1;
}
