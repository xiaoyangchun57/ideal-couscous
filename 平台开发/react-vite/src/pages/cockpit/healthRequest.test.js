import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { createHealthRequest } from './healthRequest.js';

const tick = () => new Promise(resolve => setTimeout(resolve, 0));

test('coalesces in-flight requests and applies only the latest period', async () => {
  const requests = [];
  const events = [];
  const controller = createHealthRequest({
    load: key => new Promise((resolve, reject) => requests.push({ key, resolve, reject })),
    onStart: key => events.push(['start', key]),
    onSuccess: (value, key) => events.push(['success', key, value]),
    onError: (error, key) => events.push(['error', key, error.message]),
    onIdle: () => events.push(['idle']),
  });
  controller.request('month');
  controller.request('month');
  controller.request('30d');
  controller.request('7d');
  assert.deepEqual(requests.map(request => request.key), ['month']);
  requests[0].resolve('old result');
  await tick();
  assert.deepEqual(requests.map(request => request.key), ['month', '7d']);
  requests[1].resolve('current result');
  await tick();
  assert.deepEqual(events, [['start', 'month'], ['start', '7d'],
    ['success', '7d', 'current result'], ['idle']]);
});

test('failure can be retried and disposal suppresses late updates', async () => {
  const requests = [];
  const events = [];
  const controller = createHealthRequest({
    load: key => new Promise((resolve, reject) => requests.push({ key, resolve, reject })),
    onStart: () => events.push('start'),
    onSuccess: () => events.push('success'),
    onError: error => events.push(error.message),
    onIdle: () => events.push('idle'),
  });
  controller.request('month');
  requests[0].reject(new Error('unavailable'));
  await tick();
  assert.deepEqual(events, ['start', 'unavailable', 'idle']);
  controller.request('month');
  controller.dispose();
  requests[1].resolve({ total: {} });
  await tick();
  assert.deepEqual(events, ['start', 'unavailable', 'idle', 'start']);
});

test('automatic refresh excludes the heavy health calculation', () => {
  const source = readFileSync(new URL('./CockpitPage.jsx', import.meta.url), 'utf8');
  const light = source.slice(source.indexOf('const fetchData = useCallback'),
    source.indexOf('const refreshAll = useCallback'));
  assert.doesNotMatch(light.slice(0, light.indexOf('  }, []);')), /data\/health/);
  assert.match(source, /setInterval\(fetchData, 60000\)/);
  assert.match(source, /healthRequestRef\.current\?\.request\(healthPeriod\)/);
});
