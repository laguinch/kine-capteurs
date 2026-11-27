const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
(async () => {
  const source = fs.readFileSync(path.join(__dirname, '../frontend/static/app.js'), 'utf8');
  const extract = name => source.match(new RegExp('(?:async )?function ' + name + '\\([^]*?\\n}'))[0];
  const state = {pollRevision: 0, pollInFlight: false};
  const requests = [], statuses = [], measurements = [];
  let resets = 0;
  const context = vm.createContext({state,
    fetch: url => new Promise(resolve => requests.push({url, resolve})),
    updateStatus: data => statuses.push(data), updateMeasurement: m => measurements.push(m),
    resetMeasurement: () => resets++, setMessage: () => {}, $: () => ({})});
  vm.runInContext(extract('poll') + '\n' + extract('setBluetoothConnection'), context);
  const old = context.poll();
  await context.poll();
  assert.equal(requests.length, 1);
  const disconnect = context.setBluetoothConnection('disconnect');
  await context.poll();
  assert.equal(requests.length, 2);
  requests[1].resolve({ok: true, json: async () => ({worker_phase: 'disconnected'})});
  await disconnect;
  requests[0].resolve({ok: true, json: async () => ({running: true, measurement: {left_pct: 51}})});
  await old;
  assert.equal(statuses.length, 1, 'old reply cannot restore connected status');
  assert.equal(measurements.length, 0);
  assert.equal(resets, 1);
  for (const data of [
    {running: false}, {running: true, validating_streams: true},
    {running: true, last_error: 'connection lost'}, {running: true}
  ]) {
    const pending = context.poll();
    requests.at(-1).resolve({ok: true, json: async () => ({...data, measurement: {left_pct: 70}})});
    await pending;
  }
  assert.equal(measurements.length, 1, 'only active validated acquisition updates measures');
  assert.equal(state.pollInFlight, false);
  console.log('Bipodal polling regression tests passed');
})().catch(error => { console.error(error); process.exitCode = 1; });
