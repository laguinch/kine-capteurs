const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
(async () => {
  for (const kind of ['car', 'slalom', 'brick']) {
    const source = fs.readFileSync(path.join(__dirname, '../frontend/static/kplates_' + kind + '_game.js'), 'utf8');
    const extract = name => source.match(new RegExp('(?:async )?function ' + name + '\\([^]*?\\n}'))[0];
    const elements = {};
    const requests = [];
    const game = {running: false, starting: false, playing: false, pollRevision: 0,
      polling: false, latestMeasurement: {left_pct: 51, right_pct: 49}, level: 'easy'};
    const seen = [];
    const context = vm.createContext({game, laneCount: 5, Date, performance: {now: () => 1},
      document: {querySelectorAll: () => []}, buildBricks: () => [], resetBall: () => {},
      $: id => elements[id] ||= {style: {}, textContent: ''},
      message: () => {}, updateStatus: data => seen.push(data),
      fetch: url => new Promise(resolve => requests.push({url, resolve}))});
    vm.runInContext(['poll', 'resetMeasurementDisplay', 'startGame'].map(extract).join('\n'), context);
    const oldPoll = context.poll();
    await context.poll();
    assert.equal(requests.length, 1, 'one polling request at a time');
    const start = context.startGame();
    assert.equal(game.latestMeasurement, null, kind);
    assert.equal(elements.leftForceText.textContent, '—', kind);
    assert.equal(elements.rightForceText.textContent, '—', kind);
    requests[1].resolve({ok: true, json: async () => ({running: true, measurement: null})});
    await start;
    requests[0].resolve({ok: true, json: async () => ({running: false, measurement: {left_pct: 51, right_pct: 49}})});
    await oldPoll;
    assert.equal(seen.length, 1, 'old response must not end or steer the new game');
    assert.equal(game.running, true);
    const fresh = context.poll();
    requests[2].resolve({ok: true, json: async () => ({running: true, measurement: {left_pct: 70, right_pct: 30}})});
    await fresh;
    assert.equal(seen[1].measurement.left_pct, 70);
    assert.equal(game.polling, false);
  }
  console.log('3 jeux : redémarrage, ancienne réponse retardée et nouvelle mesure vérifiés.');
})().catch(error => { console.error(error); process.exitCode = 1; });
