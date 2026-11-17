// Product-page integration against a controlled shared session; no network/device writes.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..')
const clone = value => JSON.parse(JSON.stringify(value))
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }
const deferred = () => { let resolve; const promise = new Promise(yes => { resolve = yes }); return { promise, resolve } }
const pet = { petId: 'demo', name: '演示宠物', batteryPercent: 82, batteryState: 'discharging', connectionState: 'online' }
const dashboard = { pet, intimacy: {}, today: {}, recent: [] }
const realSnapshot = () => ({
  readOnly: true, sampledAt: '2026-10-20T08:00:00Z',
  health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' },
  device: { connected: true, deviceSerial: '7c2****a128', networkName: null, lastSeenAt: null,
    batteryPercent: 62, batteryState: 'discharging', batteryVoltage: 3.7, chargerOnline: false, batteryPresent: true },
  dialog: { state: 'offline', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false }
})

function harness(mode = 'preview', overrides = {}, mockSession = true) {
  const calls = [], writes = [], redirects = [], toasts = [], timers = new Map(), listeners = new Set()
  let timerId = 0, stopped = 0
  let state = { mode, signedIn: mode === 'live', phase: 'selection', error: '', message: '', health: null,
    pets: [{ petId: 'authorized', name: '小安' }], selectedPetId: 'authorized', snapshot: null,
    loading: false, listLoading: false, statusLoading: false }
  const emit = patch => { state = { ...state, ...patch }; for (const fn of listeners) fn(clone(state)) }
  const session = {
    getState: () => clone(state),
    attach(fn) { listeners.add(fn); fn(clone(state)); return () => { listeners.delete(fn); if (!listeners.size) state.snapshot = null } },
    enable() { calls.push('enable'); emit({ mode: 'live' }); return Promise.resolve() },
    preview() { calls.push('preview'); emit({ mode: 'preview', signedIn: false, snapshot: null }); return Promise.resolve() },
    clear() { calls.push('clear'); emit({ signedIn: false, pets: [], selectedPetId: '', snapshot: null }) }
  }
  for (const method of ['login', 'refreshPets', 'selectPet', 'refresh', 'logout', 'checkHealth']) {
    session[method] = (...args) => { calls.push({ method, args }); return Promise.resolve() }
  }
  const service = {
    getBoundPet: async () => pet, getDashboard: async () => dashboard, getDeviceDetail: async () => pet,
    bindPet: async () => {}, updateDeviceStatus: async () => {}, reconnectDevice: async () => {}, ...overrides
  }
  for (const key of Object.keys(service)) {
    const impl = service[key]; service[key] = (...args) => { calls.push({ method: key, args }); return impl(...args) }
  }
  const wx = {
    getStorageSync: () => mockSession,
    redirectTo: value => redirects.push(value), reLaunch: value => redirects.push(value),
    showToast: value => toasts.push(value), stopPullDownRefresh: () => stopped++
  }
  const cache = new Map()
  function load(relative, capture = {}) {
    const filename = path.join(root, relative)
    if (cache.has(filename)) return cache.get(filename)
    const module = { exports: {} }
    const context = {
      module, exports: module.exports, wx,
      Page: value => { capture.definition = value }, App: value => { capture.definition = value },
      setInterval: fn => { const id = ++timerId; timers.set(id, fn); return id }, clearInterval: id => timers.delete(id),
      setTimeout: fn => { const id = ++timerId; timers.set(id, fn); return id }, clearTimeout: id => timers.delete(id),
      require(id) {
        if (id.endsWith('/live-device-session')) return { getLiveDeviceSession: () => session }
        if (id.endsWith('/pet-service')) return { getPetService: () => service }
        if (id.endsWith('/navigation')) return { redirectOnce: url => redirects.push({ url }), navigateOnce: url => redirects.push({ url }) }
        const file = path.relative(root, path.resolve(path.dirname(filename), id + '.ts'))
        return load(file)
      }
    }
    vm.runInNewContext(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
    }).outputText, context, { filename })
    cache.set(filename, module.exports)
    return module.exports
  }
  function page(name) {
    const capture = {}; load(`pages/${name}/index.ts`, capture)
    const value = { ...capture.definition, data: clone(capture.definition.data) }
    value.setData = patch => { Object.assign(value.data, patch); writes.push({ page: name, patch }) }
    return value
  }
  return { page, emit, calls, writes, timers, redirects, toasts, state: () => clone(state),
    app() { const capture = {}; load('app.ts', capture); return capture.definition },
    count: method => calls.filter(value => value.method === method).length,
    stopped: () => stopped, listeners: () => listeners.size }
}

const cases = []
const test = (name, run) => cases.push({ name, run })
test('preview remains local and never initiates login or live reads', async () => {
  const h = harness(); const p = h.page('home'); await p.onShow()
  assert.equal(h.count('getDashboard'), 1); assert.equal(h.count('login'), 0); assert.equal(h.count('refresh'), 0)
  assert.equal(h.timers.size, 1); p.onHide(); assert.equal(h.timers.size, 0)
})
for (const name of ['home', 'device']) {
  test(`${name} live entry does not need Mock login, binding or blind box`, async () => {
    const h = harness('live', {}, false); const p = h.page(name); await p.onShow()
    assert.equal(p.data.liveMode, true); assert.equal(p.data.loading, false)
    assert.equal(h.count('getBoundPet'), 0); assert.equal(h.count('getDashboard'), 0); assert.equal(h.count('getDeviceDetail'), 0)
    assert.equal(h.redirects.length, 0); assert.equal(name === 'home' ? p.data.dashboard : p.data.pet, null)
    p.onHide()
  })
  test(`${name} switching during a Mock response cannot publish demo data in live mode`, async () => {
    const pending = deferred(); const h = harness('preview', { getBoundPet: () => pending.promise })
    const p = h.page(name); const showing = p.onShow(); await p.useRealDevice()
    const before = h.writes.length; pending.resolve(pet); await showing
    assert.equal(h.writes.length, before); assert.equal(h.timers.size, 0)
    assert.equal(name === 'home' ? p.data.dashboard : p.data.pet, null); p.onHide()
  })
  test(`${name} hidden page rejects late state and disables live event handlers`, async () => {
    const h = harness('live'); const p = h.page(name); await p.onShow(); p.onHide()
    const before = h.writes.length
    h.emit({ phase: 'live', snapshot: { device: { connected: true, batteryPercent: 12 } } })
    await p.liveLogin(); await p.liveRefresh(); await p.liveSelect({ detail: { petId: 'authorized' } })
    assert.equal(h.writes.length, before); assert.equal(h.count('login'), 0); assert.equal(h.count('refresh'), 0)
    assert.equal(h.listeners(), 0)
  })
  test(`${name} live pull refresh is read-only and unchanged state avoids bridge rendering`, async () => {
    const h = harness('live'); const p = h.page(name); await p.onShow()
    const before = h.writes.length; h.emit({}); assert.equal(h.writes.length, before)
    await p.onPullDownRefresh(); assert.equal(h.count('refresh'), 1); assert.equal(h.stopped(), 1)
    assert.equal(h.count('getBoundPet'), 0); p.onUnload()
  })
  test(`${name} preview exit restores original local data flow`, async () => {
    const h = harness('live'); const p = h.page(name); await p.onShow(); await p.showPreview(); await flush()
    assert.equal(p.data.liveMode, false); assert.equal(h.count('getBoundPet'), 1)
    assert.equal(p.data.liveState.signedIn, false); p.onHide()
  })
  test(`${name} preview entry is reachable even when no Mock account exists`, async () => {
    const h = harness('preview', {}, false); const p = h.page(name); await p.onShow()
    assert.equal(h.redirects.length, 0); assert.equal(p.data.loading, false)
    await p.useRealDevice(); assert.equal(p.data.liveMode, true); p.onHide()
  })
}
test('home-to-connection navigation retains shared identity but does not retain old snapshot', async () => {
  const h = harness('live'); const home = h.page('home'); await home.onShow()
  h.emit({ snapshot: realSnapshot() }); assert.equal(home.data.liveOnline, true)
  home.onHide(); const device = h.page('device'); await device.onShow()
  assert.equal(device.data.liveState.signedIn, true); assert.equal(device.data.liveState.selectedPetId, 'authorized')
  assert.equal(device.data.liveState.snapshot, null); assert.equal(device.data.liveOnline, false)
  assert.equal(h.count('login'), 0); device.onHide()
})
test('header never claims online for a malformed, unauthorized or failed snapshot', async () => {
  const h = harness('live'); const p = h.page('home'); await p.onShow()
  h.emit({ snapshot: { device: { connected: true } } }); assert.equal(p.data.liveOnline, false)
  h.emit({ snapshot: realSnapshot() }); assert.equal(p.data.liveOnline, true)
  h.emit({ error: '服务暂不可用' }); assert.equal(p.data.liveOnline, false)
  h.emit({ error: '', pets: [] }); assert.equal(p.data.liveOnline, false)
  p.onHide()
})
test('Mock bind/update/reconnect cannot execute in real mode even through direct method invocation', async () => {
  const h = harness('live'); const p = h.page('device'); await p.onShow()
  p.data.pet = pet
  await p.bindPet(); await p.updateStatus(); await p.reconnect()
  for (const method of ['bindPet', 'updateDeviceStatus', 'reconnectDevice']) assert.equal(h.count(method), 0)
  p.onHide()
})
test('backgrounding clears the shared identity without touching preview storage or K1', () => {
  const h = harness('live'); h.app().onHide()
  assert.equal(h.state().signedIn, false); assert.equal(h.state().mode, 'live')
  assert.equal(h.state().snapshot, null); assert.ok(h.calls.includes('clear'))
  assert.equal(h.count('bindPet'), 0); assert.equal(h.count('updateDeviceStatus'), 0)
})
test('real UI is separated from demo content with an explicit frame banner and component events', () => {
  for (const name of ['home', 'device']) {
    const view = fs.readFileSync(path.join(root, `pages/${name}/index.wxml`), 'utf8')
    assert.ok(view.includes('liveDevice="{{liveMode}}"'))
    assert.ok(view.includes(name === 'home' ? '<live-product-panel wx:if="{{liveMode}}"' : '<live-device-panel wx:if="{{liveMode && liveState.authKind !== \'local\'}}"'))
    assert.ok(view.includes(name === 'home' ? 'bindtap="openRealDevice"' : '<local-connect '))
    assert.ok(view.includes(name === 'home' ? 'bindpreview="showPreview"' : 'bindpreview="openPreviewTools"'))
    if (name === 'device') {
      assert.ok(view.includes('previewToolsOpen && !liveMode'), 'demo editing stays opt-in and outside every real mode')
      assert.ok(view.includes('wx:if="{{advancedOpen}}"'), 'legacy gateways do not clutter the primary inline connection')
    }
    assert.ok(view.includes('loading="{{false}}"'), 'preview loading must never obscure real entry')
  }
  const frame = fs.readFileSync(path.join(root, 'components/app-page/index.wxml'), 'utf8')
  assert.ok(frame.includes('真实数据 · 培养互动需确认，动作另有开关'))
  assert.ok(frame.includes('界面预览 · 未连接设备'))
})
;(async () => {
  let failed = 0
  for (const { name, run } of cases) {
    try { await run(); console.log('PASS ' + name) }
    catch (error) { failed++; console.error('FAIL ' + name + ': ' + error.stack) }
  }
  console.log(`live-pages-smoke: ${cases.length - failed}/${cases.length} passed`)
  if (failed) process.exitCode = 1
})()
