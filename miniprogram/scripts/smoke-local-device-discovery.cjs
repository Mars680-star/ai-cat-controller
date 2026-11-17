// Bounded mDNS is address assistance, never pairing/authorization or a LAN scan.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), ts = require('typescript')
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 } }).outputText, filename)
const { createLocalDeviceDiscovery } = require('../services/local-device-discovery.ts')
function harness(options = {}) {
  let listener, timer, timeout, next = 0
  const starts = [], stops = [], removals = [], views = [], nativeSet = global.setTimeout, nativeClear = global.clearTimeout
  global.setTimeout = (fn, delay) => { timer = fn; timeout = delay; return ++next }
  global.clearTimeout = () => { timer = null }
  const wx = { getAccountInfoSync: () => ({ miniProgram: { envVersion: options.env || 'develop' } }), getDeviceInfo: () => ({ platform: options.platform || 'android' }),
    onLocalServiceFound(fn) { listener = fn; if (options.listenerThrow) throw Error('PRIVATE') },
    offLocalServiceFound(fn) { removals.push(fn); if (listener === fn) listener = null },
    startLocalServiceDiscovery(value) { starts.push(value); if (options.startThrow) throw Error('PRIVATE'); if (options.startFail) value.fail({ errMsg: 'PRIVATE' }) },
    stopLocalServiceDiscovery(value) { stops.push(value) } }
  if (options.missing) delete wx[options.missing]
  const d = createLocalDeviceDiscovery(wx)
  return { d, starts, stops, removals, views,
    show() { d.show(value => views.push(value)) },
    event(event) { if (listener) listener(event) }, callback: () => listener,
    tick() { assert.equal(timeout, 30000); const fn = timer; if (fn) fn() },
    restore() { d.hide(); global.setTimeout = nativeSet; global.clearTimeout = nativeClear } }
}
let count = 0
function check(name, run) { const h = harness(); try { run(h); count++; console.log('PASS ' + name) } finally { h.restore() } }
check('construction and show are dormant until explicit discovery', h => { h.show(); assert.equal(h.starts.length, 0); assert.equal(h.stops.length, 0); assert.equal(h.d.getState().discoveryAvailable, true) })
check('one explicit start uses only the fixed official service type and a thirty-second window', h => { h.show(); assert.equal(h.d.start(), true); assert.equal(h.starts.length, 1); assert.equal(h.starts[0].serviceType, '_aicat-local._tcp.'); assert.equal(h.d.start(), false); h.tick(); assert.equal(h.stops.length, 1); assert.equal(h.d.getState().discovering, false) })
check('found answers only add canonical private addresses at the fixed port, ignoring untrusted names', h => {
  h.show(); h.d.start()
  for (const event of [{ serviceType: '_evil._tcp.', ip: '192.168.1.152', port: 8789 }, { serviceType: '_aicat-local._tcp.', ip: '8.8.8.8', port: 8789 }, { serviceType: '_aicat-local._tcp.', ip: '192.168.1.152', port: '8789' }, { serviceType: '_aicat-local._tcp.', ip: '192.168.1.152', port: 8000 }]) h.event(event)
  assert.equal(h.d.getState().candidates.length, 0)
  h.event({ serviceType: '_aicat-local._tcp.', ip: '192.168.1.152', port: 8789, serviceName: 'PRIVATE\u202eROOT', deviceId: 'untrusted' })
  assert.deepEqual(h.d.getState().candidates, [{ address: '192.168.1.152', port: 8789 }]); assert.ok(!JSON.stringify(h.views).includes('PRIVATE')); assert.equal(h.starts.length, 1)
})
check('duplicate answers are collapsed and candidates are bounded at sixteen', h => { h.show(); h.d.start(); for (let i = 1; i < 40; i++) { const event = { serviceType: '_aicat-local._tcp.', ip: '192.168.1.' + i, port: 8789 }; h.event(event); h.event(event) }; assert.equal(h.d.getState().candidates.length, 16) })
check('hide unregisters only its listener and drops late callback results', h => { h.show(); h.d.start(); const late = h.callback(); h.d.hide(); const n = h.views.length; late({ serviceType: '_aicat-local._tcp.', ip: '192.168.1.152', port: 8789 }); assert.equal(h.views.length, n); assert.equal(h.removals.length, 1); assert.equal(h.stops.length, 1); assert.equal(h.d.getState().candidates.length, 0) })
check('an old callback cannot populate candidates after a new discovery attempt', h => { h.show(); h.d.start(); const late = h.callback(); h.d.stop(); h.d.start(); late({ serviceType: '_aicat-local._tcp.', ip: '192.168.1.152', port: 8789 }); assert.equal(h.d.getState().candidates.length, 0) })
for (const options of [{ missing: 'startLocalServiceDiscovery' }, { missing: 'offLocalServiceFound' }, { platform: 'ios' }, { env: 'release' }, { startFail: true }, { startThrow: true }, { listenerThrow: true }]) {
  const h = harness(options)
  try { h.show(); h.d.start(); assert.equal(h.d.getState().discovering, false); assert.doesNotMatch(JSON.stringify(h.views), /PRIVATE/); count++; console.log('PASS unavailable/failing discovery preserves manual address input: ' + JSON.stringify(options)) } finally { h.restore() }
}
console.log('local-device-discovery-smoke: ' + count + '/' + count + ' (injected mDNS only)')
