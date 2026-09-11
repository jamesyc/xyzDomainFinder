import test from 'node:test';
import assert from 'node:assert/strict';
import {requestJSON} from './web/api.mjs';

const reply = (body, status = 200, type = 'application/json') => async () =>
  new Response(body, {status, headers: {'Content-Type': type}});

test('scan preview handles an old server returning an HTML 404', async () => {
  await assert.rejects(requestJSON('/api/scan/preview', {}, reply('<!DOCTYPE HTML><h1>Not Found</h1>', 404, 'text/html')),
    {message: 'This endpoint is unavailable. Restart the local server and refresh the page.'});
});

test('HTML failures and malformed successful responses never leak parser errors', async () => {
  for (const status of [200, 500]) {
    await assert.rejects(requestJSON('/api/scan/preview', {}, reply('<html>private backend diagnostics</html>', status, 'text/html')),
      error => error.message.includes(`HTTP ${status}`) && !error.message.includes('JSON.parse') && !error.message.includes('private'));
  }
});

test('valid preview objects and an idle null job are accepted', async () => {
  assert.deepEqual(await requestJSON('/api/scan/preview', {}, reply('{"selected":42}')), {selected: 42});
  assert.equal(await requestJSON('/api/check/status', {}, reply('null')), null);
});

test('structured backend errors remain actionable', async () => {
  await assert.rejects(requestJSON('/api/scan/start', {}, reply('{"error":"Review this preview again"}', 409)),
    {message: 'Review this preview again'});
  await assert.rejects(requestJSON('/api/scan/preview', {}, reply('{"error":"Not Found"}', 404)), /Restart the local server/);
  await assert.rejects(requestJSON('/api/domain', {}, reply('{"error":"Candidate not found"}', 404)), {message:'Candidate not found'});
});

test('aborts are preserved, connection failures are explained, and options pass through', async () => {
  const aborted = new DOMException('Cancelled', 'AbortError');
  await assert.rejects(requestJSON('/api/catalog', {}, async () => {throw aborted;}), error => error === aborted);
  await assert.rejects(requestJSON('/api/catalog', {}, async () => ({text:async()=>{throw aborted;}})), error => error === aborted);
  await assert.rejects(requestJSON('/api/catalog', {}, async () => {throw new Error('socket failed');}), /Could not reach the local server/);
  await requestJSON('/api/scan/preview', {method: 'POST', body: '{}'}, async (url, options) => {
    assert.equal(url, '/api/scan/preview');
    assert.equal(options.method, 'POST');
    assert.equal(options.body, '{}');
    assert.equal(options.cache, 'no-store');
    return new Response('{}');
  });
});
