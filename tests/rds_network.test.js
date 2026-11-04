const { test } = require('node:test');
const assert = require('node:assert/strict');
const { clockTime, statistics, buildFrames, aggregateRoutes } = require('../html/rds_network.js');

const probe = (overrides = {}) => ({ timestamp: '2026-11-03 10:00:01', frameTimestamp: '2026-11-03 10:00:00',
  sourceIp: '10.0.0.1', targetIp: '10.0.0.2', queue: '0', usec: 100, file: 'sample.dat', ...overrides });

test('source-clock parsing preserves logged fields and rejects invalid dates', () => {
  assert.equal(clockTime('2026-11-03 10:00:01'), Date.UTC(2026, 10, 3, 10, 0, 1));
  assert.equal(clockTime('2026-11-03T10:00:01.000-0300'), Date.UTC(2026, 10, 3, 10, 0, 1));
  for (const value of ['Line 42', '2026-02-30 10:00:00', '2026-11-03 24:00:00']) assert.ok(Number.isNaN(clockTime(value)));
});

test('frames deduplicate overlapping files, keep distinct queue probes, and preserve empty collections', () => {
  const rows = [probe(), probe({ file: 'overlap.dat' }), probe({ queue: '2', usec: 300 }),
    probe({ timestamp: '2026-11-03 10:02:01', frameTimestamp: '2026-11-03 10:02:00' })];
  const result = buildFrames(rows, [{ timestamp: '2026-11-03 10:01:00' }, { timestamp: '2026-11-03 10:00:00' }]);
  assert.deepEqual(result.frames.map(frame => frame.rows.length), [2, 0, 1]);
  assert.equal(result.duplicates, 1);
  assert.equal(statistics(result.frames[1].rows).mean, null);
});

test('unknown endpoints and unparseable timestamps never become inferred routes', () => {
  const result = buildFrames([probe({ sourceIp: 'unknown' }), probe({ timestamp: 'Line 4' }),
    probe({ usec: Infinity }), probe({ usec: -1 }), probe()], []);
  assert.equal(result.skipped, 4);
  assert.equal(result.frames[0].rows.length, 1);
});

test('means are weighted by probes, p95 is nearest-rank, and opposite directions stay separate', () => {
  const rows = [probe({ usec: 0 }), probe({ usec: 10 }), probe({ queue: '2', usec: 200 }),
    probe({ sourceIp: '10.0.0.2', targetIp: '10.0.0.1', usec: 300 })];
  const routes = aggregateRoutes(rows);
  assert.equal(routes.length, 2);
  assert.equal(routes[0].mean, 70);
  assert.equal(routes[0].max, 200);
  assert.equal(routes[1].count, 1);
  assert.equal(statistics(Array.from({ length: 20 }, (_, i) => ({ usec: i + 1 }))).p95, 19);
  assert.equal(statistics([]).max, null);
});

test('ping timestamps can define frames without collection markers', () => {
  const result = buildFrames([probe({ frameTimestamp: '' })], []);
  assert.equal(result.frames[0].timestamp, '2026-11-03 10:00:01');
});
