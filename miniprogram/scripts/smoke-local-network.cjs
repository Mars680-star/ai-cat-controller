// Actual controller/pages/core and real AES. All device, wx and network I/O fake.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), crypto = require('node:crypto'), ts = require('typescript')
const ROOT = path.resolve(__dirname, '..'), DEVICE = 'dev_' + '1'.repeat(32), CARD = '2'.repeat(32), WINDOW = '3'.repeat(32), KEY = '44'.repeat(32)
const INVITATION = `AICAT-PROVISION:1:${DEVICE}:${WINDOW}:${KEY}`, STORE = 'aiCatLocalNetworkRequestV1', INNER = 'wifi_original'
const plain = value => JSON.parse(JSON.stringify(value)), flush = async () => { for (let i = 0; i < 80; i++) await Promise.resolve() }
function receipt(status, requestId = INNER, windowId = WINDOW) {
  const nonce = Buffer.from('01' + '55'.repeat(11), 'hex'), cipher = crypto.createCipheriv('aes-256-gcm', Buffer.from(KEY, 'hex'), nonce)
  cipher.setAAD(Buffer.from(JSON.stringify(['aicat-provision-v1', 'response', DEVICE, windowId, requestId, 'configure'])))
  const data = Buffer.concat([cipher.update(JSON.stringify({ status })), cipher.final(), cipher.getAuthTag()])
  return 'AICAT-PROVISION-RECEIPT:1:' + Buffer.from(JSON.stringify({ v: 1, deviceId: DEVICE, sessionId: windowId, requestId, kind: 'configure', nonce: nonce.toString('hex'), ciphertext: data.toString('hex') })).toString('base64url')
}
function harness(options = {}) {
  const storage = new Map(), calls = [], storageWrites = [], views = [], logs = [], modals = [], navigations = [], scrolls = [], timers = new Map(), modules = new Map(), listeners = new Set()
  let time = Date.now(), entropy = 0, timerId = 0, pendingPrepare
  class FakeDate extends Date { static now() { return time } }
  let auth = { mode: 'live', authKind: 'local', signedIn: true, localCardRequired: false, localContext: { deviceId: DEVICE, cardId: CARD, address: options.address || '192.168.1.152', port: 8789 } }
  let record = null
  const metadata = requestId => ({ requestId, deviceId: DEVICE, windowId: WINDOW, status: 'prepared', apName: 'AI-CAT-ABC123', expiresAt: time / 1000 + 180, networkRequestId: null, receiptQr: null })
  const session = {
    getState: () => plain(auth), attach(fn) { listeners.add(fn); fn(plain(auth)); return () => listeners.delete(fn) },
    async prepareLocalProvision(requestId) {
      assert.equal(storage.get(STORE).requestId, requestId, 'original ID must be durably stored before dispatch')
      calls.push(['prepare', requestId]); record = metadata(requestId)
      if (options.prepareFail) throw Error('PRIVATE_RAW_ERROR')
      if (options.deferPrepare) return new Promise(resolve => { pendingPrepare = () => resolve({ ...record, invitation: INVITATION }) })
      return { ...record, invitation: INVITATION }
    },
    async currentLocalProvision() { calls.push(['current']); return { window: record ? plain(record) : null } },
    async statusLocalProvision(requestId) { calls.push(['status', requestId]); assert.ok(record && record.requestId === requestId); return plain(record) },
    async receiptLocalProvision(requestId) { calls.push(['receipt', requestId]); assert.ok(record && record.requestId === requestId); return plain(record) },
    async resumeLocalProvision(requestId) { calls.push(['resume', requestId]); assert.ok(record && record.requestId === requestId); return { ...record, invitation: INVITATION } }
  }
  const wx = {
    getDeviceInfo: () => ({ platform: options.platform || 'android' }), getAccountInfoSync: () => ({ miniProgram: { envVersion: options.env || 'develop' } }),
    getStorageSync(key) { if (options.readFail) throw Error('PRIVATE_STORAGE'); return storage.has(key) ? plain(storage.get(key)) : '' },
    setStorageSync(key, value) { storageWrites.push([key, plain(value)]); if (!options.dropWrite) storage.set(key, plain(value)) },
    getUserCryptoManager: () => ({ getRandomValues(value) { value.success({ randomValues: new Uint8Array(value.length).fill(++entropy).buffer }) } }),
    base64ToArrayBuffer(value) { const b = Buffer.from(value, 'base64'); return b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength) },
    arrayBufferToBase64: value => Buffer.from(value).toString('base64'),
    showModal(value) { assert.ok(Array.from(value.confirmText).length <= 4); assert.ok(Array.from(value.cancelText).length <= 4); modals.push(value) },
    navigateTo(value) { navigations.push(value); if (options.navigateFail) value.fail({ errMsg: 'PRIVATE_NAVIGATION' }) },
    pageScrollTo(value) { scrolls.push(plain(value)) },
    navigateBack(value) { navigations.push({ kind: 'back' }); if (options.navigateFail) value.fail() },
    redirectTo(value) { navigations.push({ kind: 'redirect', url: value.url }) },
    request() { throw Error('No actual HTTP allowed') }, connectWifi() { throw Error('No automatic phone network change') },
    scanCode() { throw Error('No native scanner expected') },
    startWifi() { throw Error('No automatic Wi-Fi access') }, createTCPSocket() { throw Error('No automatic TCP expected') }
  }
  const context = vm.createContext({ wx, Date: FakeDate, Uint8Array, ArrayBuffer, DataView, BigInt,
    setTimeout(fn) { const id = ++timerId; timers.set(id, fn); return id }, clearTimeout(id) { timers.delete(id) },
    console: Object.fromEntries(['log', 'warn', 'error', 'debug', 'info'].map(key => [key, (...args) => logs.push(args)])) })
  function load(relative, parent = ROOT) {
    let filename = path.resolve(parent, relative); if (!filename.endsWith('.ts')) filename += '.ts'
    if (filename === path.join(ROOT, 'services/live-device-session.ts')) return { exports: { getLiveDeviceSession: () => session } }
    if (modules.has(filename)) return modules.get(filename)
    const result = { exports: {}, definition: null }; modules.set(filename, result)
    const js = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 } }).outputText
    new vm.Script('(function(require,module,exports,Page,Component){' + js + '\n})', { filename }).runInContext(context)(name => load(name, path.dirname(filename)).exports, result, result.exports,
      definition => { result.definition = definition }, definition => { result.definition = definition })
    return result
  }
  const api = load('services/local-network').exports
  const h = { wx, api, session, options, storage, calls, storageWrites, views, logs, modals, navigations, scrolls, timers,
    view: load('utils/local-network-view').exports.localNetworkView,
    now(delta) { time += delta },
    auth(patch) { auth = { ...auth, ...patch }; for (const fn of Array.from(listeners)) fn(plain(auth)) },
    address(address) { auth.localContext.address = address; for (const fn of Array.from(listeners)) fn(plain(auth)) },
    record(patch = {}) { record = { ...metadata('outer_boot'), ...patch }; return record },
    finishPrepare() { pendingPrepare() },
    controller() { const c = api.createLocalNetwork(wx, session); c.show(value => views.push(plain(value))); return c },
    page(relative = 'pages/local-network/index') {
      const definition = load(relative).definition, page = { ...definition, data: plain(definition.data) }
      page.setData = value => { Object.assign(page.data, plain(value)); views.push(plain(value)) }
      return page
    },
    component(parent) {
      const definition = load('components/provision-flow/index').definition
      const host = { ...definition.methods, data: { revision: parent.data.flowRevision, ...plain(definition.data) } }
      host.setData = value => { Object.assign(host.data, plain(value)); views.push(plain(value)) }
      host.triggerEvent = (name, detail) => {
        const callback = { state: 'flowStateChanged', terminal: 'flowTerminal', error: 'flowUnavailable', back: 'flowBack' }[name]
        if (callback) return parent[callback]({ detail })
      }
      return { host, attach: () => definition.lifetimes.attached.call(host), detach: () => definition.lifetimes.detached.call(host) }
    },
    marker(requestId = 'outer_boot', settled = false) { const m = { v: 1, deviceId: DEVICE, cardId: CARD, requestId, windowId: WINDOW, settled }; storage.set(STORE, m); return plain(m) },
    inner(requestId = INNER) { storage.set('aiCatProvisionPendingV1', { v: 1, deviceId: DEVICE, sessionId: WINDOW, requestId }) },
    safe() { const raw = JSON.stringify({ views, storageWrites, storage: [...storage], logs, modals, navigations }); for (const secret of [KEY, INVITATION, 'PRIVATE_RAW', 'PRIVATE_STORAGE', 'PRIVATE_NAVIGATION']) assert.equal(raw.includes(secret), false, secret + ' escaped') },
    clean() { api.clearLocalProvisionHandoff() }
  }
  return h
}
let passed = 0
async function check(name, run) { await run(); passed++; console.log('PASS ' + name) }
const CLOSEOUT_ACKS = ['window-ended', 'network-stable', 'device-idle', 'accept-unknown']
async function closeoutUI(options = {}) {
  const h = harness(); h.marker(); h.inner(); h.record({ status: 'unknown', networkRequestId: INNER, receiptQr: receipt('unknown') })
  const p = h.page(); await p.onShow()
  const controller = p.network, calls = [], token = { privateTicket: 'CLOSEOUT_UI_PRIVATE_TOKEN' }
  let pendingPrepare
  let value = { ...controller.getState(), closeout: { loading: false, submitting: false, eligible: options.eligible !== false,
    archived: false, remoteArchived: options.remoteArchived === true, phase: options.phase || (options.remoteArchived ? 'recovering' : 'ready'), error: '',
    original: { requestId: 'outer_boot', windowId: WINDOW, networkRequestId: INNER } } }
  const update = patch => { value = { ...value, ...patch, closeout: { ...value.closeout, ...(patch.closeout || {}) } }; if (p.visible) p.renderWizard(plain(value)) }
  p.network = { ...controller,
    getState() { return plain(value) },
    async refreshCloseout() { calls.push(['review']); update({ closeout: { loading: false, phase: value.closeout.remoteArchived ? 'recovering' : 'ready', eligible: true } }) },
    async prepareCloseout(acks) {
      calls.push(['prepare-closeout', plain(acks)]); update({ closeout: { loading: true, phase: 'preparing' } })
      if (options.deferPrepare) await new Promise(resolve => { pendingPrepare = resolve })
      update({ closeout: { loading: false, phase: 'prepared', eligible: false } }); return token
    },
    async confirmCloseout(selected) {
      assert.equal(selected, token); calls.push(['confirm-closeout', value.closeout.remoteArchived])
      update({ closeout: { submitting: true, phase: 'committing' } })
      if (options.unknown) { update({ closeout: { submitting: false, eligible: false, phase: 'unknown' } }); return false }
      if (options.partial) { update({ closeout: { submitting: false, eligible: false, remoteArchived: true, phase: 'recovering' } }); return false }
      update({ canPrepare: true, closeout: { submitting: false, eligible: false, archived: true, remoteArchived: true, phase: 'archived' } }); return true
    },
    cancelCloseout(selected) { assert.equal(selected, token); calls.push(['cancel-closeout']); update({ closeout: { loading: false, submitting: false, eligible: true, phase: value.closeout.remoteArchived ? 'recovering' : 'ready' } }) }
  }
  p.renderWizard(value)
  return { h, p, calls, token, update, finishPrepare() { pendingPrepare() },
    facts(values = CLOSEOUT_ACKS) { p.closeoutFactsChanged({ detail: { value: values } }) },
    safe() { assert.equal(JSON.stringify(p.data).includes('CLOSEOUT_UI_PRIVATE_TOKEN'), false); h.safe() } }
}

;(async () => {
await check('page show reads only current metadata without automatic scanning hotspot or target network change', async () => {
  const h = harness(), p = h.page(); await p.onShow(); assert.deepEqual(h.calls, [['current']]); assert.equal(h.modals.length, 0)
  assert.equal(p.data.network.canPrepare, true); p.onHide(); h.safe(); h.clean()
})
await check('fresh network visit resets old keyboard/scroll position without dispatching network writes', async () => {
  const h = harness(), p = h.page(); await p.onShow()
  assert.equal(h.scrolls.filter(x => x.scrollTop === 0).length, 1)
  assert.deepEqual(h.calls, [['current']]); p.onHide(); await p.onShow()
  assert.equal(h.scrolls.filter(x => x.scrollTop === 0).length, 2)
  p.onHide(); h.safe(); h.clean()
})
await check('one native confirmation stores and reads original ID before exactly one prepare RPC', async () => {
  const h = harness(), p = h.page(); await p.onShow(); await p.openWindow(); assert.deepEqual(h.calls, [['current']]); assert.equal(h.storage.size, 0)
  const modal = h.modals.at(-1); await modal.success({ confirm: true }); await modal.success({ confirm: true })
  assert.equal(h.calls.filter(call => call[0] === 'prepare').length, 1)
  assert.equal(h.storage.get(STORE).windowId, WINDOW); assert.equal(h.storage.get(STORE).settled, false)
  assert.equal(p.data.network.record.apName, 'AI-CAT-ABC123'); assert.equal('invitation' in p.data.network.record, false)
  p.onHide(); h.safe(); h.clean()
})
await check('a confirmed fresh window survives direct system Wi-Fi hide and show with only the original AP address hint', async () => {
  const h = harness(), p = h.page(); await p.onShow(); await p.openWindow(); await h.modals.at(-1).success({ confirm: true })
  assert.equal(p.data.pairLocation, 'hotspot'); assert.equal(p.data.wizard.pairAddressHint, '10.47.0.1')
  const before = plain([...h.storage]), calls = plain(h.calls)
  p.onHide(); h.auth({ signedIn: false, localCardRequired: true }); await p.onShow()
  assert.equal(p.data.pairLocation, 'hotspot'); assert.equal(p.data.wizard.pairAddressHint, '10.47.0.1')
  assert.equal(p.data.wizard.step, 2); assert.equal(p.data.wizard.title, '连接本次设备热点')
  assert.equal(p.data.wizard.connectionVerified, false); assert.equal(p.data.network.record, null)
  assert.deepEqual([...h.storage], before); assert.deepEqual(h.calls, calls)
  h.address('10.47.0.1'); h.auth({ signedIn: true, localCardRequired: false }); await p.paired()
  assert.equal(p.data.network.canConfigure, true); assert.equal(p.data.wizard.action, 'continueConfiguration')
  await p.continueConfiguration(); assert.equal(p.data.flowMode, 'configure')
  assert.equal(h.calls.filter(call => call[0] === 'prepare').length, 1)
  p.onUnload(); h.safe(); h.clean()
})
await check('fresh-window RAM recovery expires at the original deadline both while hidden and in the foreground', async () => {
  for (const hidden of [false, true]) {
    const h = harness(), p = h.page(); await p.onShow(); await p.openWindow(); await h.modals.at(-1).success({ confirm: true })
    const before = plain([...h.storage]), calls = plain(h.calls), timer = p.hotspotResumeTimer
    if (hidden) { p.onHide(); h.auth({ signedIn: false, localCardRequired: true }) }
    h.now(180001)
    if (hidden) await p.onShow(); else h.timers.get(timer)()
    assert.equal(p.hotspotResumeHint, null); assert.equal(p.data.pairLocation, 'station'); assert.equal(p.data.wizard.pairAddressHint, '')
    assert.equal(p.data.wizard.showHotspot, false); assert.match(p.data.error, /窗口已到期/)
    assert.deepEqual([...h.storage], before); assert.deepEqual(h.calls, calls)
    p.onUnload(); h.safe(); h.clean()
  }
})
await check('an original inner pending or accepted result replaces the fresh AP hint with ordinary-network review', async () => {
  for (const mode of ['pending', 'accepted', 'connecting', 'unknown', 'connected', 'failed', 'not_sent']) {
    const h = harness(), p = h.page(); await p.onShow(); await p.openWindow(); await h.modals.at(-1).success({ confirm: true })
    const original = plain(p.data.network.record)
    if (mode === 'pending') {
      p.onHide(); h.inner(); const before = plain([...h.storage]); h.auth({ signedIn: false, localCardRequired: true }); await p.onShow()
      assert.deepEqual([...h.storage], before)
    } else {
      h.record({ ...original, status: mode, networkRequestId: mode === 'not_sent' ? null : INNER })
      await p.refreshWindow()
    }
    assert.equal(p.hotspotResumeHint, null); assert.equal(p.data.pairLocation, 'station'); assert.equal(p.data.wizard.pairAddressHint, '')
    assert.equal(p.data.wizard.showHotspot, false); assert.equal(h.calls.filter(call => call[0] === 'prepare').length, 1)
    p.onUnload(); h.safe(); h.clean()
  }
})
await check('changed card device or outer request revokes a retained AP hint instead of applying it to another context', async () => {
  for (const change of ['card', 'device', 'request', 'window']) {
    const h = harness(), p = h.page(); await p.onShow(); await p.openWindow(); await h.modals.at(-1).success({ confirm: true }); p.onHide()
    const auth = h.session.getState()
    if (change === 'card' || change === 'device') h.auth({ signedIn: false, localCardRequired: true, localContext: { ...auth.localContext,
      [change === 'card' ? 'cardId' : 'deviceId']: change === 'card' ? 'a'.repeat(32) : 'dev_' + 'a'.repeat(32) } })
    else {
      const saved = h.storage.get(STORE); h.storage.set(STORE, { ...saved, [change === 'request' ? 'requestId' : 'windowId']: change === 'request' ? 'foreign_original' : 'a'.repeat(32) })
      h.auth({ signedIn: false, localCardRequired: true })
    }
    const before = plain([...h.storage]), calls = plain(h.calls); await p.onShow()
    assert.equal(p.hotspotResumeHint, null); assert.equal(p.data.pairLocation, 'station'); assert.equal(p.data.wizard.pairAddressHint, '')
    assert.deepEqual([...h.storage], before); assert.deepEqual(h.calls, calls)
    p.onUnload(); h.safe(); h.clean()
  }
})
await check('ordinary refresh never extends a fresh AP deadline and manual station choice cancels AP recovery', async () => {
  for (const manual of [false, true]) {
    const h = harness(), p = h.page(); await p.onShow(); await p.openWindow(); await h.modals.at(-1).success({ confirm: true })
    const original = plain(p.data.network.record), deadline = p.hotspotResumeHint.expiresAt
    h.record({ ...original, expiresAt: deadline + 600 }); await p.refreshWindow()
    assert.equal(p.hotspotResumeHint.expiresAt, deadline)
    if (manual) { p.pairStation(); assert.equal(p.hotspotResumeHint, null) }
    else {
      h.now(180001); p.renderWizard(); assert.equal(p.hotspotResumeHint, null)
      assert.equal(p.data.wizard.showHotspot, false); assert.notEqual(p.data.wizard.action, 'continueConfiguration')
      p.renderWizard(); assert.equal(p.data.wizard.showHotspot, false)
      h.now(-180001); p.renderWizard(); assert.equal(p.data.wizard.showHotspot, false)
      const calls = plain(h.calls); await p.continueConfiguration(); assert.deepEqual(h.calls, calls); assert.equal(p.data.flowMode, '')
    }
    assert.equal(p.data.pairLocation, 'station'); assert.equal(p.data.wizard.pairAddressHint, '')
    p.onUnload(); h.safe(); h.clean()
  }
})
await check('a cold page cannot derive an AP hint from persisted marker and a late hidden open reply cannot create one', async () => {
  for (const delayed of [false, true]) {
    const h = harness({ deferPrepare: delayed }), p = h.page(); await p.onShow(); await p.openWindow()
    const confirming = h.modals.at(-1).success({ confirm: true }); await flush()
    if (delayed) { p.onHide(); h.auth({ signedIn: false, localCardRequired: true }); h.finishPrepare(); await confirming; assert.equal(p.hotspotResumeHint, undefined) }
    else { await confirming; p.onUnload(); assert.equal(p.hotspotResumeHint, null); h.auth({ signedIn: false, localCardRequired: true }) }
    const fresh = h.page(), before = plain([...h.storage]), calls = plain(h.calls); await fresh.onShow()
    assert.equal(fresh.hotspotResumeHint, undefined); assert.equal(fresh.data.pairLocation, 'station'); assert.equal(fresh.data.wizard.pairAddressHint, '')
    assert.deepEqual([...h.storage], before); assert.deepEqual(h.calls, calls)
    fresh.onUnload(); h.safe(); h.clean()
  }
})
await check('cancel and stale native callbacks cannot create a request or persist an ID', async () => {
  for (const action of ['cancel', 'hide', 'expire']) {
    const h = harness(), p = h.page(); await p.onShow(); await p.openWindow(); const modal = h.modals.at(-1)
    if (action === 'hide') p.onHide(); if (action === 'expire') h.now(30001)
    await modal.success({ confirm: action !== 'cancel' }); assert.deepEqual(h.calls, [['current']]); assert.equal(h.storage.size, 0)
    p.onHide(); h.safe(); h.clean()
  }
})
await check('write readback failure fails closed before prepare and remains sticky across fresh controllers', async () => {
  const h = harness({ dropWrite: true }), c = h.controller(), token = await c.prepareOpening()
  await assert.rejects(c.confirmOpening(token)); assert.equal(h.calls.length, 0)
  h.options.dropWrite = false; c.hide(); const fresh = h.controller()
  assert.equal(fresh.getState().canPrepare, false); assert.throws(() => h.api.readLocalNetworkMarker(h.wx))
  fresh.hide(); h.safe(); h.clean()
})
await check('lost prepare response preserves original ID and only allows original status lookup', async () => {
  const h = harness({ prepareFail: true }), c = h.controller(), token = await c.prepareOpening(); await c.confirmOpening(token)
  const saved = plain(h.storage.get(STORE)); assert.equal(saved.windowId, null); assert.equal(c.getState().canPrepare, false)
  await c.refresh(); assert.deepEqual(h.calls.map(call => call[0]), ['prepare', 'status'])
  assert.equal(h.storage.get(STORE).requestId, saved.requestId); assert.equal(h.storage.get(STORE).windowId, WINDOW)
  await assert.rejects(c.prepareOpening()); c.hide(); h.safe(); h.clean()
})
await check('late prepare response after hide cannot revive an invitation view or overwrite a new visit', async () => {
  const h = harness({ deferPrepare: true }), c = h.controller(), token = await c.prepareOpening(), task = c.confirmOpening(token)
  await flush(); c.hide(); c.show(value => h.views.push(plain(value))); const before = h.views.length
  h.finishPrepare(); await task; assert.equal(h.views.length, before); assert.equal(h.storage.get(STORE).windowId, null)
  await c.refresh(); assert.equal(h.storage.get(STORE).windowId, WINDOW)
  c.hide(); h.safe(); h.clean()
})
await check('reading a boot window adopts only metadata after authenticated explicit lookup', async () => {
  const h = harness({ address: '10.47.0.1' }); h.record(); const c = h.controller()
  assert.equal(h.storage.size, 0); await c.refresh()
  assert.deepEqual(h.calls.map(call => call[0]), ['current']); assert.equal(h.storage.get(STORE).requestId, 'outer_boot')
  assert.equal(c.getState().canConfigure, true); assert.equal('receiptQr' in c.getState().record, false)
  c.hide(); h.safe(); h.clean()
})
await check('unknown metadata never settles or permits a new window and receipt reads do not prepare', async () => {
  const h = harness(); h.marker(); h.record({ status: 'unknown', networkRequestId: INNER, receiptQr: receipt('unknown') })
  const c = h.controller(); await c.refresh(); assert.equal(c.getState().canPrepare, false)
  assert.equal(h.storage.get(STORE).settled, false); await assert.rejects(c.prepareOpening())
  assert.equal(h.calls.some(call => call[0] === 'prepare'), false); c.hide(); h.safe(); h.clean()
})
await check('known not_sent with no inner request settles only its outer marker without deleting records', async () => {
  const h = harness(); h.marker(); h.record({ status: 'not_sent' }); const c = h.controller(); await c.refresh()
  assert.equal(h.storage.get(STORE).settled, true); assert.equal(c.getState().canPrepare, true); assert.equal(h.storage.size, 1)
  c.hide(); h.safe(); h.clean()
})
await check('authenticated not_sent cannot settle around an older real inner pending', async () => {
  const h = harness(); h.marker(); h.inner(); h.record({ status: 'not_sent' })
  const original = plain(h.storage.get('aiCatProvisionPendingV1')), c = h.controller(); await c.refresh()
  assert.equal(h.storage.get(STORE).settled, false); assert.equal(c.getState().canPrepare, false)
  assert.deepEqual(h.storage.get('aiCatProvisionPendingV1'), original); await assert.rejects(c.prepareOpening())
  assert.equal(h.calls.some(call => call[0] === 'prepare'), false); c.hide(); h.safe(); h.clean()
})
await check('authenticated not_sent cannot settle around corrupt or sticky-failed inner storage', async () => {
  const h = harness(); h.marker(); h.record({ status: 'not_sent' }); h.storage.set('aiCatProvisionPendingV1', { v: 'broken-inner' })
  const c = h.controller(); await c.refresh(); assert.equal(h.storage.get(STORE).settled, false)
  assert.deepEqual(h.storage.get('aiCatProvisionPendingV1'), { v: 'broken-inner' }); c.hide()
  h.storage.set('aiCatProvisionPendingV1', ''); const next = h.controller(); await next.refresh()
  assert.equal(h.storage.get(STORE).settled, false); assert.equal(next.getState().canPrepare, false)
  assert.equal(h.calls.some(call => call[0] === 'prepare'), false); next.hide(); h.safe(); h.clean()
})
await check('a verified unsubmitted timeout can prepare a fresh confirmed window only with no inner pending', async () => {
  const h = harness(); h.marker(); h.record({ status: 'not_sent' }); const c = h.controller(); await c.refresh()
  const old = plain(h.storage.get(STORE)); assert.equal(old.settled, true)
  const token = await c.prepareOpening(); await c.confirmOpening(token)
  assert.notEqual(h.storage.get(STORE).requestId, old.requestId); assert.equal(h.storage.get(STORE).settled, false)
  assert.equal(h.calls.filter(call => call[0] === 'prepare').length, 1)
  assert.equal(h.storage.get('aiCatProvisionPendingV1'), undefined); c.hide(); h.safe(); h.clean()
})
await check('same identity can resume after manual Wi-Fi change but a foreign card remains blocked', async () => {
  const h = harness(); h.marker(); h.record(); const c = h.controller(); c.hide()
  h.auth({ signedIn: false, localCardRequired: true }); c.show(() => {}); assert.equal(c.getState().canRead, false)
  h.address('10.47.0.1'); h.auth({ signedIn: true, localCardRequired: false }); await c.refresh(); assert.equal(c.getState().canConfigure, true)
  h.auth({ localContext: { deviceId: DEVICE, cardId: '9'.repeat(32), address: '10.47.0.1', port: 8789 } })
  await assert.rejects(c.refresh()); assert.equal(h.storage.get(STORE).cardId, CARD)
  c.hide(); h.safe(); h.clean()
})
await check('configure handoff requires AP pairing and has one private delivery with no automatic send', async () => {
  const h = harness(); h.marker(); h.record(); const c = h.controller(); await c.refresh()
  assert.equal(await c.openProvision('configure'), false); h.address('10.47.0.1'); await c.refresh()
  assert.equal(await c.openProvision('configure'), true); const transfer = h.api.takeLocalProvisionHandoff(); assert.ok(transfer)
  const received = []; assert.equal(transfer.deliver({ success: value => received.push(value.result), fail: () => assert.fail() }), true)
  assert.deepEqual(received, [INVITATION]); assert.equal(transfer.deliver({ success() {}, fail() {} }), false)
  assert.equal(h.api.takeLocalProvisionHandoff(), null); transfer.dispose(); c.hide(); h.safe(); h.clean()
})
await check('queued or already taken handoff is wiped by app hide and a session identity loss', async () => {
  for (const reason of ['clear-queued', 'clear-taken', 'identity', 'expiry']) {
    const h = harness({ address: '10.47.0.1' }); h.marker(); h.record(); const c = h.controller(); await c.refresh(); await c.openProvision('configure')
    let transfer = reason === 'clear-taken' ? h.api.takeLocalProvisionHandoff() : null
    if (reason.startsWith('clear')) h.api.clearLocalProvisionHandoff()
    if (reason === 'identity') h.auth({ signedIn: false, localCardRequired: true })
    if (reason === 'expiry') h.now(15001)
    assert.equal(h.api.takeLocalProvisionHandoff(), null)
    if (transfer) { let failed = false; transfer.deliver({ success: () => assert.fail('secret revived'), fail: () => { failed = true } }); assert.equal(failed || !transfer.current(), true) }
    c.hide(); h.safe(); h.clean()
  }
})
await check('actual old provision page receives a private invitation without camera or network and clears on hide', async () => {
  const h = harness({ address: '10.47.0.1' }); h.marker(); h.record(); const c = h.controller(); await c.refresh(); await c.openProvision('configure'); c.hide()
  const p = h.page('pages/provision/index'); await p.onShow(); assert.equal(p.data.localNetworkSource, true); assert.equal(p.data.scanned, true)
  assert.equal(p.data.canEditNetwork, true); assert.equal(p.data.canConfigure, false)
  p.onHide(); await p.onShow(); assert.equal(p.data.scanned, false); p.onUnload(); h.safe(); h.clean()
})
await check('real original AES receipt clears only matching inner pending and settles matching outer marker', async () => {
  const h = harness(); h.marker(); h.inner(); h.record({ status: 'connected', networkRequestId: INNER, receiptQr: receipt('connected') })
  const c = h.controller(); await c.refresh(); assert.equal(await c.openProvision('receipt'), true); c.hide()
  const p = h.page('pages/provision/index'); await p.onShow()
  assert.equal(p.data.status, 'connected'); assert.equal(p.data.pending, false); assert.equal(h.storage.get('aiCatProvisionPendingV1'), '')
  assert.equal(h.storage.get(STORE).settled, true); assert.equal(h.calls.some(call => call[0] === 'prepare'), false)
  p.onUnload(); h.safe(); h.clean()
})
await check('authentic unknown receipt keeps both original markers and never fabricates success', async () => {
  const h = harness(); h.marker(); h.inner(); const original = plain(h.storage.get('aiCatProvisionPendingV1'))
  h.record({ status: 'unknown', networkRequestId: INNER, receiptQr: receipt('unknown') }); const c = h.controller(); await c.refresh(); await c.openProvision('receipt'); c.hide()
  const p = h.page('pages/provision/index'); await p.onShow()
  assert.equal(p.data.status, 'unknown'); assert.equal(p.data.pending, true); assert.deepEqual(h.storage.get('aiCatProvisionPendingV1'), original)
  assert.equal(h.storage.get(STORE).settled, false); p.onUnload(); h.safe(); h.clean()
})
await check('wrong request ciphertext or a foreign inner pending cannot clear any marker', async () => {
  for (const kind of ['ciphertext', 'marker']) {
    const h = harness(); h.marker(); h.inner(kind === 'marker' ? 'wifi_foreign' : INNER); const original = plain(h.storage.get('aiCatProvisionPendingV1'))
    h.record({ status: 'connected', networkRequestId: INNER, receiptQr: receipt('connected', kind === 'ciphertext' ? 'wifi_wrong' : INNER) })
    const c = h.controller(); await c.refresh(); await c.openProvision('receipt'); c.hide(); const p = h.page('pages/provision/index'); await p.onShow()
    assert.equal(p.data.pending, true); assert.deepEqual(h.storage.get('aiCatProvisionPendingV1'), original); assert.equal(h.storage.get(STORE).settled, false)
    p.onUnload(); h.safe(); h.clean()
  }
})
await check('inline form mount failure wipes private handoff without any navigation or new opening', async () => {
  const h = harness({ address: '10.47.0.1' }); h.marker(); h.record(); const p = h.page(); await p.onShow(); await p.continueConfiguration()
  assert.equal(p.data.flowMode, 'configure'); assert.equal(h.navigations.length, 0)
  p.flowUnavailable()
  assert.equal(h.api.takeLocalProvisionHandoff(), null); assert.equal(p.data.flowMode, '')
  assert.equal(h.storage.get(STORE).settled, false); assert.equal(h.calls.some(call => call[0] === 'prepare'), false)
  p.onHide(); h.safe(); h.clean()
})
await check('runtime previews cannot invoke new opening or pairing through direct page handlers', async () => {
  for (const options of [{ platform: 'devtools' }, { platform: 'ios' }, { env: 'release' }]) {
    const h = harness(options), p = h.page(); await p.onShow(); await p.openWindow(); p.pairHotspot()
    assert.equal(h.calls.length, 0); assert.equal(h.navigations.length, 0); p.onHide(); h.clean()
  }
})
await check('a completed native opening callback cannot later claim that its dispatched request was unsent', async () => {
  const h = harness(), p = h.page(); await p.onShow(); await p.openWindow(); const modal = h.modals.at(-1)
  await modal.success({ confirm: true }); const before = plain(p.data)
  modal.fail({ errMsg: 'PRIVATE_NATIVE_ERROR' }); assert.deepEqual(plain(p.data), before)
  assert.equal(h.calls.filter(call => call[0] === 'prepare').length, 1); p.onHide(); h.safe(); h.clean()
})
await check('a late failed old modal cannot overwrite the next same-visit confirmation', async () => {
  const h = harness(), p = h.page(); await p.onShow(); await p.openWindow(); const old = h.modals.at(-1)
  await old.success({ confirm: false }); await p.openWindow(); assert.equal(p.data.confirming, true)
  const before = plain(p.data); old.fail({ errMsg: 'PRIVATE_NATIVE_ERROR' }); assert.deepEqual(plain(p.data), before)
  assert.deepEqual(h.calls, [['current']]); p.onHide(); h.safe(); h.clean()
})
await check('legacy inner pending without an outer marker blocks a new AP opening and is never erased', async () => {
  const h = harness(); h.inner(); const before = plain(h.storage.get('aiCatProvisionPendingV1')), c = h.controller()
  assert.equal(c.getState().canPrepare, false); assert.equal(c.getState().legacyBlocked, true)
  await assert.rejects(c.prepareOpening()); assert.equal(h.calls.length, 0); assert.equal(h.storage.has(STORE), false)
  assert.deepEqual(h.storage.get('aiCatProvisionPendingV1'), before); c.hide(); h.safe(); h.clean()
})
await check('an inner pending created during the modal prevents opening before outer storage or RPC', async () => {
  const h = harness(), c = h.controller(), token = await c.prepareOpening(); h.inner()
  await assert.rejects(c.confirmOpening(token)); assert.equal(h.calls.length, 0); assert.equal(h.storage.has(STORE), false)
  assert.equal(c.getState().canPrepare, false); c.hide(); h.safe(); h.clean()
})
await check('corrupt legacy storage keeps opening disabled after a later empty read and fresh controller', async () => {
  const h = harness(); h.storage.set('aiCatProvisionPendingV1', { v: 'bad' }); const c = h.controller()
  assert.equal(c.getState().canPrepare, false); c.hide(); h.storage.set('aiCatProvisionPendingV1', '')
  const next = h.controller(); assert.equal(next.getState().canPrepare, false); await assert.rejects(next.prepareOpening())
  assert.equal(h.calls.length, 0); next.hide(); h.clean()
})
await check('duplicate provision show disposes its old private observer before another window can settle', async () => {
  const h = harness({ address: '10.47.0.1' }); h.marker(); h.record(); const c = h.controller(); await c.refresh(); await c.openProvision('configure'); c.hide()
  const p = h.page('pages/provision/index'); await p.onShow(); await p.onShow(); assert.equal(p.data.localNetworkSource, false)
  const otherWindow = '9'.repeat(32), otherInvite = `AICAT-PROVISION:1:${DEVICE}:${otherWindow}:${KEY}`
  h.storage.set('aiCatProvisionPendingV1', { v: 1, deviceId: DEVICE, sessionId: otherWindow, requestId: INNER })
  const scan = p.scanInvitation(); await flush(); const key = p.data.cameraKeys[0]
  p.cameraDecoded({ currentTarget: { dataset: { scanId: key } }, detail: { type: 'QR_CODE', result: otherInvite } }); await scan
  const final = p.scanFinalReceipt(); await flush(); const receiptKey = p.data.cameraKeys[0]
  p.cameraDecoded({ currentTarget: { dataset: { scanId: receiptKey } }, detail: { type: 'QR_CODE', result: receipt('connected', INNER, otherWindow) } }); await final
  assert.equal(p.data.status, 'connected'); assert.equal(p.data.pending, false)
  assert.equal(h.storage.get(STORE).windowId, WINDOW); assert.equal(h.storage.get(STORE).settled, false)
  p.onUnload(); h.safe(); h.clean()
})
await check('wizard pairs in place and selects only the next relevant action without page navigation', async () => {
  const h = harness(); h.record(); const p = h.page(); await p.onShow()
  assert.equal(p.data.wizard.step, 2); assert.equal(p.data.wizard.action, 'pairHotspot')
  await p.primaryAction(); assert.equal(p.data.pairLocation, 'hotspot'); assert.equal(p.data.pairPrompt, true)
  assert.equal(p.data.wizard.pairAddressHint, '10.47.0.1'); assert.equal(h.navigations.length, 0)
  h.address('10.47.0.1'); await p.paired()
  assert.equal(p.data.pairPrompt, false); assert.equal(p.data.wizard.action, 'continueConfiguration')
  assert.equal(h.calls.some(call => call[0] === 'prepare'), false)
  p.onHide(); h.safe(); h.clean()
})
await check('active inline form blocks a second handoff or parent operation and hides private event fields', async () => {
  const h = harness({ address: '10.47.0.1' }); h.marker(); h.record(); const p = h.page(); await p.onShow()
  await p.continueConfiguration(); const calls = plain(h.calls), revision = p.data.flowRevision
  assert.equal(p.data.flowMode, 'configure'); assert.equal(h.navigations.length, 0)
  await p.continueConfiguration(); await p.refreshWindow(); await p.openWindow(); p.pairStation()
  assert.deepEqual(h.calls, calls); assert.equal(p.data.flowRevision, revision)
  p.flowStateChanged({ detail: { status: 'accepted', pending: true, busy: false, phase: 'pending', error: 'PRIVATE_RAW', invitation: INVITATION } })
  assert.deepEqual(plain(p.data.flowState), { status: 'accepted', pending: true, busy: false, phase: 'pending' })
  p.onHide(); assert.equal(p.data.flowMode, ''); assert.equal(h.api.takeLocalProvisionHandoff(), null)
  p.flowTerminal({ detail: { status: 'connected', pending: false, busy: false } }); assert.equal(p.data.flowComplete, false)
  h.safe(); h.clean()
})
await check('original inner pending drives result review even if outer metadata is still open', async () => {
  const h = harness({ address: '10.47.0.1' }); h.marker(); h.record(); const p = h.page(); await p.onShow(); await p.continueConfiguration()
  h.inner(); const original = plain(h.storage.get('aiCatProvisionPendingV1'))
  p.flowStateChanged({ detail: { status: 'unknown', pending: true, busy: false, phase: 'pending' } })
  await p.flowBack(); assert.equal(p.data.pairLocation, 'station'); assert.equal(p.data.wizard.step, 3)
  assert.equal(p.data.wizard.action, 'pairStation'); assert.equal(p.data.network.canPrepare, false)
  assert.deepEqual(h.storage.get('aiCatProvisionPendingV1'), original); assert.equal(h.storage.get(STORE).settled, false)
  p.onHide(); h.auth({ signedIn: false, localCardRequired: true }); await p.onShow()
  assert.equal(p.data.pairLocation, 'station'); assert.equal(p.data.wizard.showPair, true)
  assert.equal(p.data.wizard.step, 3); assert.equal(h.calls.some(call => call[0] === 'prepare'), false)
  p.onHide(); h.safe(); h.clean()
})
await check('background during the form remembers result recovery without keeping private handoff', async () => {
  const h = harness({ address: '10.47.0.1' }); h.marker(); h.record(); const p = h.page(); await p.onShow(); p.pairHotspot(); await p.paired(); await p.continueConfiguration()
  h.inner(); const original = plain(h.storage.get('aiCatProvisionPendingV1')); p.onHide()
  assert.equal(p.data.pairLocation, 'station'); assert.equal(h.api.takeLocalProvisionHandoff(), null)
  h.auth({ signedIn: false, localCardRequired: true }); await p.onShow()
  assert.equal(p.data.flowMode, ''); assert.equal(p.data.wizard.pairAddressHint, '')
  assert.deepEqual(h.storage.get('aiCatProvisionPendingV1'), original); assert.equal(h.storage.get(STORE).settled, false)
  p.onHide(); h.safe(); h.clean()
})
await check('a child terminal display event cannot mutate either original ledger or fabricate verified parent completion', async () => {
  const h = harness(); h.marker(); h.inner(); h.record({ status: 'unknown', networkRequestId: INNER, receiptQr: receipt('unknown') })
  const p = h.page(); await p.onShow(); await p.verifyReceipt(); const before = plain([...h.storage])
  p.flowTerminal({ detail: { status: 'connected', pending: false, busy: false, phase: 'connected' } })
  assert.deepEqual([...h.storage], before); await p.flowBack()
  assert.equal(p.data.wizard.complete, false); assert.equal(h.storage.get(STORE).settled, false)
  assert.equal(p.data.wizard.action, 'verifyReceipt'); p.onHide(); h.safe(); h.clean()
})
await check('a pairing operation disables other parent intents and location changes', async () => {
  const h = harness(), p = h.page(); await p.onShow(); const before = plain(h.calls)
  p.pairStateChanged({ detail: { busy: true, token: 'PRIVATE_RAW' } })
  await p.openWindow(); await p.refreshWindow(); p.pairHotspot()
  assert.deepEqual(h.calls, before); assert.equal(h.modals.length, 0); assert.equal(p.data.pairLocation, 'station')
  p.pairStateChanged({ detail: { busy: false } }); assert.equal(p.data.wizard.actionEnabled, true)
  p.onHide(); h.safe(); h.clean()
})
await check('read failure keeps the primary action read-only rather than suggesting another hotspot', async () => {
  const h = harness(), p = h.page(); await p.onShow()
  const view = h.view({ ...p.data.network, error: 'original lookup unavailable', canPrepare: true, canRead: true }, { available: true })
  assert.equal(view.action, 'refreshWindow'); assert.equal(view.complete, false)
  assert.equal(h.view({ ...p.data.network, record: { status: 'unknown', networkRequestId: INNER }, marker: { settled: true } }, { available: true }).complete, false)
  p.onHide(); h.safe(); h.clean()
})
await check('window expiry only suggests original lookup and never assumes not_sent or success', async () => {
  const h = harness({ address: '10.47.0.1' }); h.marker(); h.record(); const p = h.page(); await p.onShow()
  h.now(181000); const view = h.view(p.network.getState(), { available: true })
  assert.equal(view.action, 'refreshWindow'); assert.equal(view.showHotspot, false); assert.equal(view.complete, false)
  assert.equal(h.storage.get(STORE).settled, false); p.onHide(); h.safe(); h.clean()
})
await check('real inline component verifies the original AES receipt and leaves only on its explicit back button', async () => {
  const h = harness(); h.marker(); h.inner(); h.record({ status: 'connected', networkRequestId: INNER, receiptQr: receipt('connected') })
  const p = h.page(); await p.onShow(); await p.verifyReceipt()
  const component = h.component(p); component.attach(); await flush()
  assert.equal(component.host.data.status, 'connected'); assert.equal(component.host.data.pending, false)
  assert.equal(h.storage.get('aiCatProvisionPendingV1'), ''); assert.equal(h.storage.get(STORE).settled, true)
  assert.equal(p.data.flowMode, 'receipt'); assert.equal(p.data.flowComplete, true); assert.equal(h.navigations.length, 0)
  component.host.returnToLocalNetwork(); await flush()
  assert.equal(p.data.flowMode, ''); assert.equal(p.data.wizard.complete, true)
  component.detach(); p.onHide(); h.safe(); h.clean()
})
await check('an inline form error refreshes the real legacy guard before suggesting the next action', async () => {
  const h = harness({ address: '10.47.0.1' }); h.marker(); h.record(); const p = h.page(); await p.onShow(); await p.continueConfiguration()
  h.inner(); const before = plain(h.storage.get('aiCatProvisionPendingV1'))
  p.flowUnavailable(); assert.equal(p.data.wizard.step, 3); assert.equal(p.data.network.canPrepare, false)
  assert.notEqual(p.data.wizard.action, 'continueConfiguration'); assert.equal(p.data.wizard.complete, false)
  assert.deepEqual(h.storage.get('aiCatProvisionPendingV1'), before); p.onHide(); h.safe(); h.clean()
})
await check('inner pending hides stale open hotspot and switches progress immediately without settling records', async () => {
  const h = harness({ address: '10.47.0.1' }); h.marker(); h.record(); const p = h.page(); await p.onShow(); await p.continueConfiguration()
  const before = plain([...h.storage]), calls = plain(h.calls)
  p.flowStateChanged({ detail: { status: 'accepted', pending: true, busy: false, phase: 'pending' } })
  assert.equal(p.data.wizard.step, 3); assert.equal(p.data.wizard.stepTwoDone, true)
  assert.equal(p.data.wizard.showHotspot, false); assert.equal(p.data.wizard.connectionVerified, false)
  assert.equal(p.data.flowComplete, false); assert.deepEqual([...h.storage], before); assert.deepEqual(h.calls, calls)
  p.onHide(); h.safe(); h.clean()
})
await check('a real inner pending disables configure even when outer open metadata has not caught up', async () => {
  const h = harness({ address: '10.47.0.1' }); h.marker(); h.record(); const c = h.controller(); await c.refresh()
  assert.equal(c.getState().canConfigure, true); h.inner(); const before = plain([...h.storage]), calls = plain(h.calls)
  assert.equal(c.getState().canConfigure, false); await assert.rejects(c.openProvision('configure'))
  assert.deepEqual(h.calls, calls); assert.deepEqual([...h.storage], before); c.hide(); h.safe(); h.clean()
})
await check('lost old AP after acceptance prompts station pairing instead of old-hotspot retry or green connected', async () => {
  const h = harness({ address: '10.47.0.1' }); h.marker(); h.inner(); h.record(); const p = h.page(); await p.onShow()
  const view = h.view({ ...p.network.getState(), error: 'original lookup unavailable' }, { available: true })
  assert.equal(view.step, 3); assert.equal(view.showHotspot, false); assert.equal(view.complete, false)
  assert.equal(view.action, 'pairStation'); assert.equal(view.connectionVerified, false)
  assert.match(view.copy, /热点消失不代表成功或失败/)
  const calls = plain(h.calls); await p.primaryAction()
  assert.equal(p.data.pairLocation, 'station'); assert.equal(p.data.wizard.pairAddressHint, '')
  assert.equal(p.data.wizard.showPair, true); assert.deepEqual(h.calls, calls)
  p.onHide(); h.safe(); h.clean()
})
await check('station read failure offers same-card reconnect without enabling prepare or using stale connection', async () => {
  const h = harness(); h.marker(); h.inner(); h.record({ status: 'unknown', networkRequestId: INNER }); const p = h.page(); await p.onShow()
  const view = h.view({ ...p.network.getState(), error: 'original lookup unavailable' }, { available: true })
  assert.equal(view.action, 'pairStation'); assert.equal(view.connectionVerified, false)
  assert.equal(view.connectionLabel, '需要重新连接'); assert.equal(view.controlExitEnabled, false)
  assert.equal(p.network.getState().canPrepare, false); p.onHide(); h.safe(); h.clean()
})
await check('explicit return to motion retains both unknown ledgers and does not report provisioning completion', async () => {
  const h = harness(); h.marker(); h.inner(); h.record({ status: 'unknown', networkRequestId: INNER, receiptQr: receipt('unknown') })
  const p = h.page(); await p.onShow(); const before = plain([...h.storage]), calls = plain(h.calls)
  assert.equal(p.data.wizard.showControlExit, true); assert.equal(p.data.wizard.controlExitEnabled, true)
  p.returnToControl(); assert.equal(h.navigations.at(-1).url, '/pages/motion/index')
  assert.equal(p.data.wizard.complete, false); assert.deepEqual([...h.storage], before); assert.deepEqual(h.calls, calls)
  assert.equal(p.network.getState().canPrepare, false); p.onHide(); p.returnToControl()
  assert.equal(h.navigations.length, 1); h.safe(); h.clean()
})
await check('pending recovery cannot resurrect an old hotspot address after background or an explicit stale location choice', async () => {
  const h = harness(); h.marker(); h.inner(); h.auth({ signedIn: false, localCardRequired: true })
  const p = h.page(); await p.onShow(); p.pairHotspot()
  assert.equal(p.data.wizard.step, 3); assert.equal(p.data.wizard.pairLocation, 'station')
  assert.equal(p.data.wizard.pairAddressHint, ''); assert.equal(p.data.wizard.showHotspot, false)
  assert.match(p.data.wizard.copy, /原普通网络/); assert.equal(h.calls.length, 0)
  p.onHide(); h.safe(); h.clean()
})
await check('hotspot and read failures cannot use control exit or mislabel unknown as connected', async () => {
  const h = harness({ address: '10.47.0.1' }); h.marker(); h.inner(); h.record({ status: 'unknown', networkRequestId: INNER })
  const p = h.page(); await p.onShow(); p.returnToControl()
  assert.equal(p.data.wizard.controlExitEnabled, false); assert.equal(h.navigations.length, 0)
  assert.equal(p.data.wizard.connectionVerified, false); assert.equal(p.data.wizard.complete, false)
  p.onHide(); h.safe(); h.clean()
})
await check('closeout stays a separate explicit review with unchecked facts and no automatic request', async () => {
  const t = await closeoutUI({ eligible: false, phase: 'unchecked' }), { p, h } = t
  assert.equal(p.data.wizard.showCloseout, true); assert.equal(p.data.wizard.closeoutChecksEnabled, false)
  assert.deepEqual(p.data.closeoutAcks, []); await p.requestCloseout(); assert.equal(t.calls.length, 0)
  await p.refreshCloseout(); assert.deepEqual(t.calls, [['review']]); assert.equal(p.data.wizard.closeoutChecksEnabled, true)
  assert.deepEqual(p.data.closeoutAcks, []); assert.equal(h.modals.length, 0)
  assert.equal(p.data.wizard.action, 'focusCloseout'); await p.primaryAction()
  assert.equal(h.scrolls.at(-1).selector, '.closeout-card'); assert.deepEqual(t.calls, [['review']])
  p.onHide(); t.safe(); h.clean()
})
await check('closeout requires the four fixed facts and a fresh final confirmation, with no token in page data', async () => {
  const t = await closeoutUI(), { p, h } = t, before = plain([...h.storage])
  t.facts(['window-ended', 'network-stable', 'device-idle']); await p.requestCloseout(); assert.equal(t.calls.length, 0)
  t.facts(['window-ended', 'window-ended', 'device-idle', 'accept-unknown']); assert.deepEqual(p.data.closeoutAcks, [])
  t.facts(); await p.requestCloseout(); assert.equal(h.modals.length, 1)
  assert.deepEqual(t.calls[0], ['prepare-closeout', CLOSEOUT_ACKS]); assert.equal(t.calls.length, 1)
  await h.modals.at(-1).success({ confirm: true }); await h.modals.at(-1).success({ confirm: true })
  assert.equal(t.calls.filter(x => x[0] === 'confirm-closeout').length, 1)
  assert.equal(p.data.wizard.complete, false); assert.equal(p.data.wizard.closeoutArchived, true)
  assert.match(p.data.wizard.title, /归档.*仍未知/); assert.equal(p.data.wizard.actionLabel, '开始新配网')
  assert.equal(h.calls.some(x => x[0] === 'prepare'), false); assert.deepEqual([...h.storage], before)
  assert.deepEqual(p.data.closeoutAcks, []); p.onHide(); t.safe(); h.clean()
})
await check('declining closeout cancels its private token and never changes either ledger or starts a window', async () => {
  const t = await closeoutUI(), { p, h } = t, before = plain([...h.storage])
  t.facts(); await p.requestCloseout(); await h.modals.at(-1).success({ confirm: false })
  assert.equal(t.calls.filter(x => x[0] === 'confirm-closeout').length, 0)
  assert.equal(t.calls.filter(x => x[0] === 'cancel-closeout').length, 1)
  assert.deepEqual(p.data.closeoutAcks, []); assert.deepEqual([...h.storage], before)
  assert.equal(p.data.wizard.complete, false); p.onHide(); t.safe(); h.clean()
})
await check('hidden or changed closeout facts cannot dispatch the prepared native confirmation', async () => {
  for (const change of ['hide', 'facts', 'card', 'auth', 'pending']) {
    const t = await closeoutUI(), { p, h } = t
    t.facts(); await p.requestCloseout(); const modal = h.modals.at(-1)
    if (change === 'hide') p.onHide()
    if (change === 'facts') t.facts(['window-ended'])
    if (change === 'card') t.update({ marker: { ...p.data.network.marker, cardId: '9'.repeat(32) } })
    if (change === 'auth') t.update({ paired: false, closeout: { eligible: false, phase: 'blocked' } })
    if (change === 'pending') t.update({ record: { ...p.data.network.record, networkRequestId: 'different_request' } })
    await modal.success({ confirm: true })
    assert.equal(t.calls.filter(x => x[0] === 'confirm-closeout').length, 0, change)
    assert.equal(h.calls.some(x => x[0] === 'prepare'), false); p.onHide(); t.safe(); h.clean()
  }
})
await check('late closeout prepare after hide is canceled without reviving a modal or checking any facts', async () => {
  const t = await closeoutUI({ deferPrepare: true }), { p, h } = t
  t.facts(); const prepare = p.requestCloseout(); await flush(); p.onHide()
  const writes = h.views.length
  t.finishPrepare(); await prepare
  assert.equal(h.modals.length, 0); assert.equal(t.calls.some(x => x[0] === 'confirm-closeout'), false)
  assert.equal(h.views.length, writes); assert.deepEqual(p.data.closeoutAcks, []); t.safe(); h.clean()
})
await check('unknown closeout submit offers read-only verification and never resubmits on another tap', async () => {
  const t = await closeoutUI({ unknown: true }), { p, h } = t, before = plain([...h.storage])
  t.facts(); await p.requestCloseout(); await h.modals.at(-1).success({ confirm: true })
  assert.equal(p.data.closeoutReviewRequired, true); assert.equal(p.data.wizard.closeoutChecksEnabled, false)
  t.facts(); await p.requestCloseout()
  assert.equal(t.calls.filter(x => x[0] === 'confirm-closeout').length, 1)
  assert.equal(p.data.wizard.complete, false); assert.equal(p.data.wizard.action, 'refreshCloseout')
  assert.deepEqual([...h.storage], before); p.onHide(); t.safe(); h.clean()
})
await check('half-complete server archive remains blocked until a fresh explicit phone-side review', async () => {
  const t = await closeoutUI({ partial: true }), { p, h } = t
  t.facts(); await p.requestCloseout(); await h.modals.at(-1).success({ confirm: true })
  assert.equal(p.data.wizard.closeoutRecovering, true); assert.equal(p.data.wizard.closeoutArchived, false)
  assert.equal(p.data.wizard.closeoutActionLabel, '完成手机端归档'); assert.notEqual(p.data.wizard.action, 'openWindow')
  assert.equal(p.data.wizard.closeoutChecksEnabled, false); assert.deepEqual(p.data.closeoutAcks, [])
  await p.refreshCloseout(); assert.equal(p.data.wizard.closeoutChecksEnabled, true)
  assert.deepEqual(p.data.closeoutAcks, []); assert.equal(t.calls.filter(x => x[0] === 'confirm-closeout').length, 1)
  p.onHide(); t.safe(); h.clean()
})
console.log(`local-network-smoke: ${passed}/${passed} passed (synthetic local RPC; actual provision controller/AES)`)
})().catch(error => { console.error(error); process.exitCode = 1 })
