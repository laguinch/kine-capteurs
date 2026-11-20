const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
for (const kind of ['car', 'slalom', 'brick']) {
  const source = fs.readFileSync(path.join(__dirname, '../frontend/static/kplates_' + kind + '_game.js'), 'utf8');
  const extract = (name) => source.match(new RegExp('function ' + name + '\\([^]*?\\n}'))[0];
  let now = 100;
  const elements = {};
  const game = { running: true, playing: false, startedAt: null,
    lastMeasurementTimestamp: null, lastFreshMeasurementAt: null, score: 7 };
  const context = vm.createContext({game, performance: {now: () => now},
    $: (id) => elements[id] ||= {style: {}, textContent: ''},
    message: () => {}, format: (n) => String(n), laneCount: 5,
    fullSteeringAsymmetry: 45, movementThreshold: 10});
  vm.runInContext(extract('updateControls') + '\n' + extract('checkMeasurementFreshness'), context);
  const sample = {timestamp_utc: 'first', left_kg: 40, right_kg: 40, total_kg: 80,
    left_pct: 50, right_pct: 50, asymmetry_pct: 0};
  context.updateControls(sample);
  assert.equal(game.playing, true, kind);
  now = 1200;
  context.updateControls(sample); // polling repeats the same CSV row
  context.checkMeasurementFreshness(now);
  assert.equal(game.playing, false, kind);
  assert.equal(game.running, true, kind);
  assert.equal(game.score, 7, kind);
  now = 1800;
  context.updateControls({...sample, timestamp_utc: 'second'});
  assert.equal(game.playing, true, kind);
  assert.equal(game.startedAt, 100, 'resumption must not restart the session clock');
  assert.equal(game.score, 7, kind);
  game.running = false;
  game.playing = false;
  context.updateControls({...sample, timestamp_utc: 'third'});
  assert.equal(game.playing, false, 'ended session must not resume');
}
console.log('3 jeux : absence, répétition, reprise et fin de session vérifiées.');
