// Real inline component/factory/original controller/AES; only native/HTTP I/O fake.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path')
const { harness, KEY, DEVICE, SESSION, QR, PASSWORD, SSID, flush, plain } = require('./smoke-provision-page.cjs')
const ROOT = path.resolve(__dirname, '..'), OUTER = 'aiCatLocalNetworkRequestV1', INNER = 'aiCatProvisionPendingV1', CARD = 'a'.repeat(32)
const cases = [], check = (name, run) => cases.push([name, run])
async function setup(options = {}) {
  const h = harness({ inline: true, ...options }), listeners = new Set()
  let auth = { mode: 'live', authKind: 'local', signedIn: true, localCardRequired: false,
    localContext: { deviceId: DEVICE, cardId: CARD, address: '10.47.0.1', port: 8789 } }, record
  const api = h.module('services/local-network')
  const session = { getState: () => plain(auth), attach(fn) { listeners.add(fn); fn(plain(auth)); return () => listeners.delete(fn) },
    async prepareLocalProvision(requestId) { record = { requestId, deviceId: DEVICE, windowId: SESSION, status: 'open', apName: 'AI-CAT-ABC123', expiresAt: Date.now() / 1000 + 180, networkRequestId: null, receiptQr: null }; return { ...record, invitation: QR } },
    async resumeLocalProvision(requestId) { assert.equal(requestId, record.requestId); return { ...record, invitation: QR } },
    async statusLocalProvision(requestId) { assert.equal(requestId, record.requestId); return plain(record) },
    async currentLocalProvision() { return { window: record ? plain(record) : null } } }
  const c = api.createLocalNetwork(h.wx, session); c.show(() => {})
  h.offer = async () => c.openProvision('configure')
  h.offerReceipt = async (status, patch) => {
    record.status = status; record.networkRequestId = h.storage.get(INNER).requestId
    if (!record.networkRequestId) record.networkRequestId = h.storage.get(INNER).pending.requestId
    record.receiptQr = h.receiptQR(status, patch || {})
    await c.refresh(); return c.openProvision('receipt')
  }
  h.local = c
  h.cleanup = () => { h.page.onUnload(); c.hide(); api.clearLocalProvisionHandoff() }
  h.terminal = () => h.events.filter(([name]) => name === 'terminal')
  h.back = () => h.events.filter(([name]) => name === 'back')
  h.safe = () => {
    assert.doesNotMatch(h.visible(), new RegExp(KEY + '|AICAT-PROVISION|synthetic-password|PRIVATE_'))
    assert.equal(h.navigations.length, 0)
  }
  const token = await c.prepareOpening(); await c.confirmOpening(token)
  return h
}
async function configure(h, status) {
  h.draft(); const opened = await h.open()
  const confirmed = opened.modal.success({ confirm: true }); await flush()
  const socket = await h.connect()
  if (status) h.reply(status, socket)
  await confirmed; await opened.task
  return socket
}
async function failBinding(h) {
  h.draft(); const opened = await h.open()
  const confirmed = opened.modal.success({ confirm: true }); await flush()
  const socket = h.sockets.at(-1); assert.ok(socket)
  socket.listeners.error({ errCode: 4, errMsg: 'PRIVATE_NATIVE_BIND_TEXT' })
  await confirmed; await opened.task
  assert.equal(h.page.data.status, 'not_sent'); assert.equal(socket.packets.length, 0)
  return socket
}

check('missing handoff mounts safely without camera, Wi-Fi, writes or a terminal event', async () => {
  const h = harness({ inline: true }); await h.boot()
  assert.match(h.page.data.error, /资料已清除/); assert.equal(h.takes(), 1)
  assert.equal(h.scans.length, 0); assert.equal(h.sockets.length, 0); assert.equal(h.wifiStarts.length, 0); assert.equal(h.storageWrites.length, 0)
  assert.equal(h.events.filter(([name]) => name === 'terminal').length, 0)
  h.page.onUnload()
})
check('attached plus ordinary show and same revision consume the private invitation once', async () => {
  const h = await setup(); assert.equal(await h.offer(), true); await h.boot()
  assert.equal(h.takes(), 1); assert.equal(h.page.data.scanned, true); assert.equal(h.page.data.localNetworkSource, true)
  await h.page.onShow(); await h.revision(1)
  assert.equal(h.takes(), 1); assert.equal(h.scans.length, 0); assert.equal(h.sockets.length, 0)
  assert.equal(h.events.filter(([name]) => name === 'terminal').length, 0)
  h.safe(); h.cleanup()
})
check('a queued revision supersedes an unstarted mount without consuming the invitation twice', async () => {
  const h = await setup(); await h.offer()
  const mounting = h.boot(), changing = h.revision(2)
  await mounting; await changing
  assert.equal(h.takes(), 1); assert.equal(h.page.data.scanned, true); assert.equal(h.page.flowStartedRevision, 2)
  assert.equal(h.scans.length, 0); assert.equal(h.sockets.length, 0); h.safe(); h.cleanup()
})
check('revision changes cannot replace an open confirmation or dispatch the old request', async () => {
  const h = await setup(); await h.offer(); await h.boot(); h.draft(); const opened = await h.open()
  await h.revision(2)
  assert.equal(h.page.data.confirming, true); assert.equal(h.takes(), 1); assert.equal(h.sockets.length, 0)
  await opened.modal.success({ confirm: false }); await opened.task
  assert.equal(h.sockets.length, 0); assert.equal(h.storage.get(OUTER).settled, false); h.safe(); h.cleanup()
})
check('a declined one-time private delivery fails closed rather than opening an operator camera', async () => {
  const h = await setup({ declineHandoff: true }); assert.equal(await h.offer(), true); await h.boot()
  assert.equal(h.scans.length, 0); assert.equal(h.nativeScans.length, 0); assert.equal(h.page.data.scanned, false)
  assert.equal(h.sockets.length, 0); assert.equal(h.wifiStarts.length, 0); assert.equal(h.terminal().length, 0)
  h.safe(); h.cleanup()
})
check('a hidden revision cannot consume a fresh handoff or restart the previous private form', async () => {
  const h = await setup(); await h.offer(); await h.boot(); h.page.onHide()
  assert.equal(await h.offer(), true)
  await h.revision(2); assert.equal(h.takes(), 1); assert.equal(h.page.pageVisible, false)
  await h.page.onShow(); assert.equal(h.takes(), 1)
  assert.ok(h.events.some(([name]) => name === 'error'))
  await h.revision(3); assert.equal(h.takes(), 2); assert.equal(h.page.data.scanned, true)
  h.safe(); h.cleanup()
})
check('a new missing handoff never reuses an old connected result in the mounted view', async () => {
  const h = await setup(); await h.offer(); await h.boot(); await configure(h, 'connected')
  assert.equal(h.page.data.status, 'connected'); assert.equal(h.terminal().length, 1)
  await h.revision(2)
  assert.equal(h.page.data.status, ''); assert.equal(h.page.data.scanned, false)
  assert.equal(h.page.data.canScanReceipt, false); assert.match(h.page.data.error, /资料已清除/)
  assert.equal(h.terminal().length, 1); h.safe(); h.cleanup()
})
check('actual encrypted configure keeps password out of data/events/storage and emits one verified terminal', async () => {
  const h = await setup(); await h.offer(); await h.boot(); const socket = await configure(h, 'connected')
  assert.equal(h.decrypt(socket).ssid, SSID); assert.equal(h.decrypt(socket).password, PASSWORD)
  assert.equal(h.terminal().length, 1); assert.equal(h.terminal()[0][1].status, 'connected')
  assert.equal(h.storage.get(INNER), ''); assert.equal(h.storage.get(OUTER).settled, true)
  assert.equal(h.page.privatePassword, '')
  const eventCount = h.terminal().length; h.page.render(h.page.provision.getState()); assert.equal(h.terminal().length, eventCount)
  h.page.returnToLocalNetwork(); assert.equal(h.back().length, 1); assert.equal(h.page.pageVisible, false)
  h.safe(); h.cleanup()
})
check('accepted local configure explains hotspot switch and targets same-card result review without claiming success', async () => {
  const h = await setup(); await h.offer(); await h.boot(); await configure(h, 'accepted')
  assert.equal(h.page.data.pending, true); assert.equal(h.page.data.status, 'accepted')
  assert.match(h.page.data.statusHint, /热点可能因此消失/)
  assert.match(h.page.data.statusHint, /重扫机身卡/); assert.doesNotMatch(h.page.data.statusHint, /操作员/)
  assert.equal(h.terminal().length, 0); assert.equal(h.storage.get(OUTER).settled, false)
  const before = plain([...h.storage]), sockets = h.sockets.length
  h.page.returnToLocalNetwork(); assert.equal(h.back().length, 1)
  assert.deepEqual(plain([...h.storage]), before); assert.equal(h.sockets.length, sockets)
  h.safe(); h.cleanup()
})
check('native binding failure exposes an inline explicit recovery without automatic mode change or retry', async () => {
  const h = await setup(); await h.offer(); await h.boot(); await failBinding(h)
  assert.equal(h.page.data.transportMode, 'bound'); assert.equal(h.page.data.bindingRecoveryAvailable, true)
  assert.equal(h.page.data.canRecoverBinding, true); assert.equal(h.page.privatePassword, '')
  assert.equal(h.sockets.length, 1); assert.equal(h.storage.get(INNER), ''); assert.equal(h.storage.get(OUTER).settled, false)
  h.page.hotspotChanged({ detail: { value: [] } }); assert.equal(h.page.data.canRecoverBinding, false)
  const before = plain([...h.storage]); await h.page.recoverBinding()
  assert.equal(h.page.data.transportMode, 'bound'); assert.equal(h.sockets.length, 1); assert.deepEqual(plain([...h.storage]), before)
  h.safe(); h.cleanup()
})
check('one explicit recovery selects compatibility and probes with zero credentials while keeping both ledgers unchanged', async () => {
  const h = await setup(); await h.offer(); await h.boot(); await failBinding(h)
  const before = plain([...h.storage]), writeCount = h.storageWrites.length, keys = plain(h.page.data.formKeys)
  const task = h.page.recoverBinding(); await flush()
  const socket = h.sockets.at(-1); assert.equal(h.sockets.length, 2)
  assert.equal(socket.boundWifi, undefined); assert.deepEqual(plain(socket.destination), { address: '10.47.0.1', port: 8766, timeout: 5 })
  assert.equal(socket.packets.length, 0); socket.listeners.connect(); await task
  assert.equal(h.page.data.transportMode, 'direct'); assert.equal(h.page.data.compatibilityReady, true)
  assert.equal(h.page.data.bindingRecoveryAvailable, false); assert.equal(h.page.data.canConfigure, false)
  assert.equal(h.page.privatePassword, ''); assert.notDeepEqual(plain(h.page.data.formKeys), keys)
  assert.match(h.page.data.notice, /重新填写密码/); assert.equal(h.modals.length, 1)
  assert.deepEqual(plain([...h.storage]), before); assert.equal(h.storageWrites.length, writeCount)
  assert.equal(h.terminal().length, 0); h.safe(); h.cleanup()
})
check('compatibility recovery still requires fresh password and final explicit confirmation before exactly one encrypted send', async () => {
  const h = await setup(); await h.offer(); await h.boot(); await failBinding(h)
  const recovery = h.page.recoverBinding(); await flush(); h.sockets.at(-1).listeners.connect(); await recovery
  await h.page.configure(); assert.equal(h.modals.length, 1); assert.equal(h.sockets.length, 2)
  h.page.inputPassword({ detail: { value: PASSWORD } }); const opened = await h.open()
  assert.match(opened.modal.content, /由系统选路/); assert.match(opened.modal.content, /确认只发送一次/)
  assert.equal(h.sockets.length, 2); assert.equal(h.page.data.confirming, true)
  const confirmed = opened.modal.success({ confirm: true }); await flush()
  const socket = h.sockets.at(-1); assert.equal(socket.boundWifi, undefined); socket.listeners.connect(); await flush()
  assert.equal(socket.packets.length, 1); assert.equal(h.decrypt(socket).password, PASSWORD)
  h.reply('connected', socket); await confirmed; await opened.task
  assert.equal(h.sockets.length, 3); assert.equal(h.terminal().length, 1); h.safe(); h.cleanup()
})
check('failed compatibility probe remains unsent and an old hidden recovery callback cannot restart or write', async () => {
  const h = await setup(); await h.offer(); await h.boot(); await failBinding(h)
  const before = plain([...h.storage]), task = h.page.recoverBinding(); await flush()
  const socket = h.sockets.at(-1); socket.listeners.error({ errCode: 4, errMsg: 'PRIVATE_PROBE_FAILURE' }); await task
  assert.equal(h.page.data.probeStatus, 'failed'); assert.equal(h.page.data.compatibilityReady, false)
  assert.equal(h.page.data.canConfigure, false); assert.match(h.page.data.notice, /未发送网络信息/)
  assert.deepEqual(plain([...h.storage]), before); assert.equal(h.terminal().length, 0); h.safe(); h.cleanup()
  const late = await setup(); await late.offer(); await late.boot(); await failBinding(late)
  late.options.deferGetConnectedWifi = true
  const recovery = late.page.recoverBinding(); await flush(); const reading = late.wifiReads.at(-1)
  late.page.onHide(); const writes = late.writes.length
  reading.success({ wifi: { SSID: 'AI-CAT-ABC123', BSSID: '12:34:56:78:9A:BC' } }); await recovery
  assert.equal(late.sockets.length, 1); assert.equal(late.writes.length, writes)
  assert.equal(late.page.privatePassword, ''); assert.equal(late.terminal().length, 0); late.safe(); late.cleanup()
})
check('recovery handler cannot bypass an unknown request or current-hotspot BSSID validation', async () => {
  const h = await setup(); await h.offer(); await h.boot(); await configure(h, 'unknown')
  const before = plain([...h.storage]); h.page.setData({ canRecoverBinding: true, bindingRecoveryAvailable: true })
  await h.page.recoverBinding(); assert.equal(h.sockets.length, 1); assert.equal(h.page.provision.getState().transportMode, 'bound')
  assert.deepEqual(plain([...h.storage]), before); assert.equal(h.terminal().length, 0); h.safe(); h.cleanup()
  const changed = await setup(); await changed.offer(); await changed.boot(); await failBinding(changed)
  const recovery = changed.page.recoverBinding(); await flush(); changed.sockets.at(-1).listeners.connect(); await recovery
  changed.page.inputPassword({ detail: { value: PASSWORD } }); const opened = await changed.open()
  changed.options.bssid = 'AA:BB:CC:DD:EE:FF'
  await opened.modal.success({ confirm: true }); await opened.task
  assert.equal(changed.sockets.length, 2); assert.equal(changed.page.data.status, 'not_sent')
  assert.equal(changed.page.data.compatibilityReady, false); assert.equal(changed.terminal().length, 0)
  changed.safe(); changed.cleanup()
})
check('unknown response retains both original ledgers and cannot announce terminal success', async () => {
  const h = await setup(); await h.offer(); await h.boot(); await configure(h, 'unknown')
  assert.equal(h.page.data.status, 'unknown'); assert.equal(h.page.data.pending, true); assert.equal(h.storage.get(OUTER).settled, false)
  assert.equal(h.terminal().length, 0); assert.ok(h.storage.get(INNER))
  const before = plain([...h.storage]); h.page.returnToLocalNetwork()
  assert.equal(h.back().length, 1); assert.deepEqual(plain([...h.storage]), before)
  h.safe(); h.cleanup()
})
check('an outer settlement write fault cannot emit a completed terminal event', async () => {
  const h = await setup(); await h.offer(); await h.boot()
  const original = h.wx.setStorageSync
  h.wx.setStorageSync = (key, value) => { if (key === OUTER) throw Error('PRIVATE_OUTER_WRITE'); original(key, value) }
  await configure(h, 'connected')
  assert.equal(h.page.data.status, 'connected'); assert.equal(h.terminal().length, 0)
  assert.equal(h.storage.get(OUTER).settled, false); assert.equal(h.page.flowSettlementError, true)
  assert.match(h.page.data.notice, /收尾保存未通过/); h.safe(); h.cleanup()
})
check('hide clears native-input generations and late confirmation never sends network data', async () => {
  const h = await setup(); await h.offer(); await h.boot(); h.draft(); const opened = await h.open()
  const before = h.storageWrites.length; h.page.onHide()
  assert.equal(h.page.privatePassword, ''); assert.equal(h.page.ssidDraft, ''); assert.deepEqual(plain(h.page.data.formKeys), [])
  await opened.modal.success({ confirm: true }); await opened.task
  assert.equal(h.sockets.length, 0); assert.equal(h.wifiStarts.length, 0); assert.equal(h.storageWrites.length, before); assert.equal(h.terminal().length, 0)
  h.safe(); h.cleanup()
})
check('late native Wi-Fi initialization after hide cannot create or write a socket', async () => {
  const h = await setup({ deferStartWifi: true }); await h.offer(); await h.boot(); h.draft(); const opened = await h.open()
  const confirmed = opened.modal.success({ confirm: true }); await flush(); assert.equal(h.wifiStarts.length, 1)
  h.page.onHide(); h.wifiStarts[0].success({ errMsg: 'PRIVATE_LATE_START' }); await confirmed; await opened.task
  assert.equal(h.sockets.length, 0); assert.equal(h.terminal().length, 0); h.safe(); h.cleanup()
})
check('receipt terminal requires original core AES authentication and matching IDs', async () => {
  for (const patch of [{ ciphertext: '00'.repeat(32) }, { requestId: 'wifi_wrong_original' }, { sessionId: '9'.repeat(32) }, {}]) {
    const valid = Object.keys(patch).length === 0
    const h = await setup(); await h.offer(); await h.boot(); await configure(h, 'unknown'); h.page.onHide()
    assert.equal(await h.offerReceipt('connected', patch), true)
    await h.page.onShow(); await h.revision(2)
    if (valid) {
      assert.equal(h.terminal().length, 1); assert.equal(h.page.data.status, 'connected'); assert.equal(h.storage.get(OUTER).settled, true)
    } else {
      assert.equal(h.terminal().length, 0); assert.equal(h.page.data.pending, true); assert.equal(h.storage.get(OUTER).settled, false)
    }
    assert.equal(h.scans.length, 0); h.safe(); h.cleanup()
  }
})
check('inline template has private uncontrolled inputs and does not expose operator scan/archive UI', () => {
  const view = fs.readFileSync(path.join(ROOT, 'components/provision-flow/index.wxml'), 'utf8')
  const password = view.match(/<input\b[^>]*password="\{\{true\}\}"[^>]*>/)
  assert.ok(password); assert.doesNotMatch(password[0], /\bvalue=/)
  assert.doesNotMatch(view, /<camera\b|bindtap="scanInvitation"|bindtap="archiveRequest"/)
  assert.doesNotMatch(view, /\{\{[^}]*\b(?:key|cardQR|accessToken|password)\b/)
  assert.ok(view.indexOf('bindtap="recoverBinding"') < view.indexOf('wx:if="{{diagnosticsOpen}}"'))
  assert.match(view, /使用兼容通道并检查/); assert.match(view, /不会自动重试/)
})

;(async () => { let failed = 0; for (const [name, run] of cases) { try { await run(); console.log('PASS ' + name) }
  catch (error) { failed++; console.error('FAIL ' + name + ': ' + error.stack) } }
  console.log('provision-flow-smoke: ' + (cases.length - failed) + '/' + cases.length + ' (actual inline TS/controller/AES)')
  if (failed) process.exitCode = 1
})().catch(error => { console.error(error); process.exitCode = 1 })
