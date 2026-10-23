import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import { apiConfig } from '../scripts/build-pages.mjs';

test('Pages API config handles empty and valid HTTPS values', () => {
  for (const value of ['', 'https://api.example.com/', 'https://example.com/backend']) {
    const context = { window: {} };
    vm.runInNewContext(apiConfig(value), context);
    assert.equal(context.window.TEKSTOM_API, value.replace(/\/+$/, ''));
  }
});

test('Pages API config rejects credentials, insecure URLs and query secrets', () => {
  for (const value of ['http://localhost:8000', 'https://user:pass@example.com', 'https://example.com?token=secret', 'https://example.com#secret', 'javascript:alert(1)']) {
    assert.throws(() => apiConfig(value));
  }
});
