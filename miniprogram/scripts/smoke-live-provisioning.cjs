// Actual TS + vendored noble AES, independently checked with Node crypto.
// All wx/TCP/random/storage calls are synthetic; no real packets or credentials.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), crypto = require('node:crypto'), ts = require('typescript')
const root = path.resolve(__dirname, '..'), KEY = '22'.repeat(32), DEVICE = 'dev_' + '1'.repeat(32), SESSION = '3'.repeat(32)
const QR = 'AICAT-PROVISION:1:' + DEVICE + ':' + SESSION + ':' + KEY, STORAGE = 'aiCatProvisionPendingV1'
const BSSID = '12:34:56:78:9A:BC'
const ARCHIVE_CHECKS = ['window-ended', 'usb-checked', 'accept-unknown']
const LOCAL_ARCHIVE_CHECKS = ['window-ended', 'network-stable', 'device-idle', 'accept-unknown']
const CARD = 'a'.repeat(32), REVIEW = 'b'.repeat(32), OUTER = 'local_wifi_original'
const plain = value => JSON.parse(JSON.stringify(value)), flush = async () => { for (let i = 0; i < 30; i++) await Promise.resolve() }
function harness(options = {}, existing = new Map()) {
  const scans = [], sockets = [], logs = [], writes = [], storageKeys = [], calls = [], wifiStarts = [], wifiReads = [], randomRequests = [], modules = new Map(), timers = new Map(), timerDelays = new Map()
  let timerId = 0, randomId = 0
  const clock = { now: 1800000000000 }
  class SmokeDate extends Date { static now() { return clock.now } }
  const nativeError = stage => Object.assign(Error('PRIVATE_NATIVE_' + stage), { errCode: options.nativeCode, errMsg: 'PRIVATE_NATIVE_DETAILS', BSSID, SSID: 'PRIVATE_NATIVE_SSID' })
  const wx = { getDeviceInfo: () => ({ platform: options.platform || 'android' }),
    base64ToArrayBuffer(value) { const data = Buffer.from(value, 'base64'); return data.buffer.slice(data.byteOffset, data.byteOffset + data.byteLength) },
    arrayBufferToBase64(value) { return Buffer.from(value).toString('base64') },
    getStorageSync(key) { if (options.storageReadFailure) throw Error('PRIVATE_STORAGE_READ'); return existing.has(key) ? plain(existing.get(key)) : '' },
    setStorageSync(key, value) {
      if (options.storageFailure) throw Error('private error')
      if (!options.storageNoop) existing.set(key, options.storeWriteEmpty ? '' : options.corruptWrite ? { v: 2, revision: 'PRIVATE_CORRUPTION' } : plain(value))
      writes.push(plain(value)); storageKeys.push(key)
      if (options.postWriteReadFailure) options.storageReadFailure = true
    },
    scanCode(value) { scans.push(value) },
    startWifi(value) {
      calls.push('startWifi'); wifiStarts.push(value)
      if (options.startWifiThrow) throw Error('PRIVATE_WIFI_INIT_ERROR')
      if (options.deferStartWifi) return
      if (options.startWifiFail) return value.fail({ errCode: options.startWifiFail, errMsg: 'PRIVATE_WIFI_INIT_ERROR' })
      value.success({ errMsg: 'startWifi:ok' })
    },
    getConnectedWifi(value) {
      calls.push('getConnectedWifi'); wifiReads.push(value); assert.equal(value.partialInfo, false)
      if (options.readWifiThrow) throw Error('PRIVATE_WIFI_READ_ERROR')
      if (options.deferGetConnectedWifi) return
      if (options.readWifiFail) return value.fail({ errCode: options.readWifiFail, errMsg: 'PRIVATE_WIFI_READ_ERROR' })
      const index = wifiReads.length - 1
      const current = options.bssids ? options.bssids[Math.min(index, options.bssids.length - 1)] : Object.prototype.hasOwnProperty.call(options, 'bssid') ? options.bssid : BSSID
      const hotspot = Object.prototype.hasOwnProperty.call(options, 'wifiSSID') ? options.wifiSSID : 'AI-CAT-ABC123'
      value.success({ wifi: { SSID: hotspot, BSSID: current }, errMsg: 'getConnectedWifi:ok' })
    },
    connectWifi() { throw Error('No automatic phone network change allowed') },
    request() { throw Error('No HTTP allowed') },
    getUserCryptoManager() { return { getRandomValues(value) {
      calls.push('random'); randomRequests.push(value)
      if (options.randomFailure) return value.fail()
      if (options.deferRandomAt === randomRequests.length) return
      const bytes = new Uint8Array(value.length).fill(++randomId); value.success({ randomValues: bytes.buffer })
    } } },
    createTCPSocket() {
      if (options.socketFailure) throw nativeError('CREATE')
      const listeners = {}
      const socket = { listeners, packets: [], closed: false,
        onBindWifi(fn) { listeners.bind = fn }, onConnect(fn) { listeners.connect = fn }, onError(fn) { if (options.eventsThrow) throw nativeError('EVENTS'); listeners.error = fn }, onClose(fn) { listeners.close = fn }, onMessage(fn) { listeners.message = fn },
        bindWifi(value) {
          calls.push('bindWifi')
          const expected = Object.prototype.hasOwnProperty.call(options, 'bssid') ? options.bssid : BSSID
          assert.deepEqual(plain(value), { BSSID: expected }, 'native binding requires the current Wi-Fi BSSID')
          socket.boundWifi = plain(value)
          if (options.bindThrow) throw nativeError('BIND')
        },
        connect(value) { if (options.connectThrow) throw Error('private connect error'); socket.destination = value; calls.push('connect') },
        write(value) { if (options.writeThrow) throw Error('private write error'); socket.packets.push(Buffer.from(value)); calls.push('write') },
        close() { socket.closed = true; if (listeners.close) listeners.close() } }
      sockets.push(socket); return socket
    } }
  const context = vm.createContext({ Uint8Array, ArrayBuffer, DataView, BigInt, Date: SmokeDate, wx,
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, fn); timerDelays.set(id, delay); return id }, clearTimeout(id) { timers.delete(id); timerDelays.delete(id) },
    console: Object.fromEntries(['log', 'error', 'warn', 'info', 'debug'].map(name => [name, (...values) => logs.push(values)])) })
  function load(relative, parent = root) {
    let filename = path.resolve(parent, relative); if (!filename.endsWith('.ts')) filename += '.ts'
    if (modules.has(filename)) return modules.get(filename).exports
    const module = { exports: {} }; modules.set(filename, module)
    const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2019, module: ts.ModuleKind.CommonJS } }).outputText
    new vm.Script('(function(require,module,exports){' + output + '\n})', { filename }).runInContext(context)(name => load(name, path.dirname(filename)), module, module.exports)
    return module.exports
  }
  const api = load('services/live-provisioning'), controller = api.createLiveProvisioning(wx), states = []
  controller.show(value => states.push(plain(value)))
  const h = { api, load, controller, states, wx, scans, sockets, logs, writes, storageKeys, storage: existing, timers, timerDelays, calls, wifiStarts, wifiReads, options, randomRequests,
    async scan(value = QR) { const before = scans.length, task = controller.scan(); if (scans.length > before) scans.at(-1).success({ result: value, scanType: 'QR_CODE' }); await task },
    draft() { controller.setNetwork('家庭测试网', 'synthetic-password') },
    async connect() { await flush(); const socket = sockets.at(-1); assert.ok(socket); if (socket.boundWifi) socket.listeners.bind({ errMsg: 'bindWifi:ok' }); socket.listeners.connect(); await flush(); return socket },
    async probe() {
      const task = controller.probe(); await flush(); const socket = sockets.at(-1); assert.ok(socket)
      socket.listeners.connect(); await task; return socket
    },
    advance(milliseconds) { clock.now += milliseconds },
    expireProof() { const values = [...timers].filter(([id]) => timerDelays.get(id) === 60000); assert.equal(values.length, 1); values[0][1]() },
    packet(socket = sockets.at(-1)) { const raw = socket.packets.at(-1); assert.ok(raw); assert.equal(raw.readUInt32BE(), raw.length - 4); return JSON.parse(raw.subarray(4).toString('utf8')) },
    decrypt(socket = sockets.at(-1)) {
      const packet = h.packet(socket), raw = Buffer.from(packet.ciphertext, 'hex')
      const decipher = crypto.createDecipheriv('aes-256-gcm', Buffer.from(KEY, 'hex'), Buffer.from(packet.nonce, 'hex'))
      decipher.setAAD(Buffer.from(JSON.stringify(['aicat-provision-v1', 'request', DEVICE, SESSION, packet.requestId, packet.kind])))
      decipher.setAuthTag(raw.subarray(-16))
      return JSON.parse(Buffer.concat([decipher.update(raw.subarray(0, -16)), decipher.final()]).toString())
    },
    receiptQR(status, patch = {}) {
      const value = existing.get(STORAGE), original = value && value.v === 2 ? value.pending : value; assert.ok(original)
      const header = { v: 1, deviceId: original.deviceId, sessionId: original.sessionId, requestId: original.requestId, kind: 'configure', nonce: '01' + '07'.repeat(11), ...patch }
      const aad = Buffer.from(JSON.stringify(['aicat-provision-v1', 'response', header.deviceId, header.sessionId, header.requestId, header.kind]))
      const cipher = crypto.createCipheriv('aes-256-gcm', Buffer.from(KEY, 'hex'), Buffer.from(header.nonce, 'hex')); cipher.setAAD(aad)
      const ciphertext = Buffer.concat([cipher.update(JSON.stringify({ status })), cipher.final(), cipher.getAuthTag()]).toString('hex')
      const envelope = { ...header, ciphertext, ...patch }
      return 'AICAT-PROVISION-RECEIPT:1:' + Buffer.from(JSON.stringify(envelope)).toString('base64url')
    },
    async receipt(status = 'unknown', patch = {}) {
      const qr = h.receiptQR(status, patch), before = scans.length, task = controller.scanReceipt()
      if (scans.length > before) scans.at(-1).success({ scanType: 'QR_CODE', result: qr })
      await task
    },
    reply(status, socket = sockets.at(-1), patch = {}, partial = false) {
      const request = h.packet(socket)
      const nonce = Buffer.alloc(12, 9); nonce[0] = 1
      const aad = Buffer.from(JSON.stringify(['aicat-provision-v1', 'response', request.deviceId, request.sessionId, request.requestId, request.kind]))
      const cipher = crypto.createCipheriv('aes-256-gcm', Buffer.from(KEY, 'hex'), nonce); cipher.setAAD(aad)
      const ciphertext = Buffer.concat([cipher.update(JSON.stringify({ status })), cipher.final(), cipher.getAuthTag()])
      const envelope = { v: 1, deviceId: request.deviceId, sessionId: request.sessionId, requestId: request.requestId, kind: request.kind, nonce: nonce.toString('hex'), ciphertext: ciphertext.toString('hex'), ...patch }
      const body = Buffer.from(JSON.stringify(envelope)), frame = Buffer.alloc(body.length + 4); frame.writeUInt32BE(body.length); body.copy(frame, 4)
      const send = chunk => socket.listeners.message({ remoteInfo: { address: '10.47.0.1', port: 8766 }, message: chunk.buffer.slice(chunk.byteOffset, chunk.byteOffset + chunk.byteLength) })
      if (partial) { send(frame.subarray(0, 2)); send(frame.subarray(2, 21)); send(frame.subarray(21)) } else send(frame)
    } }
  return h
}
const originalMarker = () => ({ v: 1, deviceId: DEVICE, sessionId: SESSION, requestId: 'wifi_original' })
const archiveEntry = (index = 0) => ({ original: { v: 1, deviceId: DEVICE, sessionId: (index + 10).toString(16).padStart(32, '0'), requestId: 'wifi_archived_' + index },
  status: 'unknown', resolution: 'operator_archived', reviewedAt: 1799999999000, checks: ARCHIVE_CHECKS.slice() })
async function archiveHarness(options = {}, value = originalMarker(), receipt = true) {
  const h = harness(options, new Map([[STORAGE, plain(value)]])); await h.scan()
  if (receipt) await h.receipt('unknown')
  return h
}
const localContext = () => ({ cardId: CARD, outerRequestId: OUTER })
const localLookup = () => ({ deviceId: DEVICE, cardId: CARD, outerRequestId: OUTER, windowId: SESSION, networkRequestId: 'wifi_original', reviewId: REVIEW })
const localReview = () => ({ eligible: false, cardId: CARD,
  original: { deviceId: DEVICE, requestId: OUTER, windowId: SESSION, networkRequestId: 'wifi_original' },
  status: 'unknown', resolution: 'operator_archived', reviewId: REVIEW, archivedAt: 1799999999000, acknowledgements: LOCAL_ARCHIVE_CHECKS.slice() })
const cases = {
  async 'local gateway archival requires original AEAD unknown proof four explicit checks and its own confirmation profile'() {
    const noProof = await archiveHarness({}, originalMarker(), false)
    assert.equal(noProof.controller.prepareLocalArchive(LOCAL_ARCHIVE_CHECKS, localContext()), null)
    assert.equal(noProof.api.lookupLocalArchive(noProof.wx, localLookup()), null); assert.equal(noProof.writes.length, 0)
    const h = await archiveHarness()
    for (const checks of [undefined, [], ARCHIVE_CHECKS, [...LOCAL_ARCHIVE_CHECKS, 'extra'],
      ['window-ended', 'network-stable', 'device-idle', 'device-idle'], { 'window-ended': true }]) {
      assert.equal(h.controller.prepareLocalArchive(checks, localContext()), null)
    }
    for (const context of [undefined, {}, { ...localContext(), cardId: [CARD] }, { ...localContext(), outerRequestId: ['other'] },
      { ...localContext(), key: KEY }]) assert.equal(h.controller.prepareLocalArchive(LOCAL_ARCHIVE_CHECKS, context), null)
    const usb = h.controller.prepareArchive(ARCHIVE_CHECKS)
    assert.equal(h.controller.confirmLocalArchive(usb, localReview()), false)
    h.controller.cancelArchive(usb)
    const local = h.controller.prepareLocalArchive(LOCAL_ARCHIVE_CHECKS.slice().reverse(), localContext())
    assert.ok(local); assert.equal(h.controller.confirmArchive(local), false)
    assert.equal(h.controller.confirmLocalArchive(local, localReview()), true)
    assert.equal(h.controller.confirmLocalArchive(local, localReview()), false)
    assert.deepEqual(h.storage.get(STORAGE).archives[0].checks, LOCAL_ARCHIVE_CHECKS)
    assert.equal(h.sockets.length, 0); assert.equal(h.randomRequests.length, 0)
  },
  async 'local archival keeps original unknown audit and strictly reads only an existing exact archive for outer recovery'() {
    const h = await archiveHarness(), before = { scans: h.scans.length, calls: h.calls.length }
    const token = h.controller.prepareLocalArchive(LOCAL_ARCHIVE_CHECKS, localContext())
    assert.equal(h.controller.confirmLocalArchive(token, localReview()), true)
    const expected = { original: originalMarker(), status: 'unknown', resolution: 'operator_archived', reviewedAt: 1800000000000,
      checks: LOCAL_ARCHIVE_CHECKS, source: 'local_gateway', reviewId: REVIEW, outerRequestId: OUTER, cardId: CARD }
    assert.deepEqual(h.storage.get(STORAGE), { v: 2, revision: 1, pending: null, archives: [expected] })
    const checkpoint = plain(h.storage.get(STORAGE)), writes = h.writes.length
    assert.deepEqual(plain(h.api.lookupLocalArchive(h.wx, localLookup())), expected)
    for (const [field, value] of Object.entries({ deviceId: 'dev_' + '9'.repeat(32), cardId: '9'.repeat(32), outerRequestId: 'other',
      windowId: '9'.repeat(32), networkRequestId: 'other', reviewId: '9'.repeat(32) })) {
      assert.equal(h.api.lookupLocalArchive(h.wx, { ...localLookup(), [field]: value }), null, field)
    }
    const result = h.api.lookupLocalArchive(h.wx, localLookup()); result.original.requestId = 'not-original'; result.checks.push('not-original')
    assert.deepEqual(h.storage.get(STORAGE), checkpoint); assert.equal(h.writes.length, writes)
    assert.equal(h.calls.length, before.calls); assert.equal(h.scans.length, before.scans)
    assert.equal(h.sockets.length, 0); assert.equal(h.randomRequests.length, 0)
    assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes]), /22222222|synthetic-password|usb-checked/)
    await h.scan(); h.draft(); assert.equal(h.controller.getState().canConfigure, false)
    const fresh = harness(); await fresh.scan()
    assert.equal(fresh.controller.prepareLocalArchive(LOCAL_ARCHIVE_CHECKS, localContext()), null)
    assert.equal(fresh.api.lookupLocalArchive(fresh.wx, localLookup()), null); assert.equal(fresh.writes.length, 0)
  },
  async 'readonly unknown receipt verification works after local archival but never creates markers or treats other outcomes as proof'() {
    const h = await archiveHarness(), context = { deviceId: DEVICE, windowId: SESSION, networkRequestId: 'wifi_original' }
    const qr = h.receiptQR('unknown'), other = ['connected', 'failed', 'not_sent', 'accepted', 'connecting'].map(status => h.receiptQR(status))
    const forged = [{ kind: 'status' }, { requestId: 'other' }, { sessionId: 'd'.repeat(32) }, { nonce: '00' + '07'.repeat(11) }, { ciphertext: '00'.repeat(32) }].map(patch => h.receiptQR('unknown', patch))
    const token = h.controller.prepareLocalArchive(LOCAL_ARCHIVE_CHECKS, localContext())
    assert.equal(h.controller.confirmLocalArchive(token, localReview()), true)
    const original = plain(h.storage.get(STORAGE)), before = { writes: h.writes.length, sockets: h.sockets.length, random: h.randomRequests.length, scans: h.scans.length }
    assert.equal(h.api.verifyUnknownReceipt(h.wx, QR, qr, context), true)
    for (const receipt of [...other, ...forged, qr + ' ', 'not a receipt']) assert.throws(() => h.api.verifyUnknownReceipt(h.wx, QR, receipt, context))
    for (const invitation of [QR.replace(KEY, '44'.repeat(32)), QR.replace(SESSION, 'd'.repeat(32)), QR + ' ']) assert.throws(() => h.api.verifyUnknownReceipt(h.wx, invitation, qr, context))
    for (const invalid of [{ ...context, deviceId: 'dev_' + 'd'.repeat(32) }, { ...context, windowId: 'd'.repeat(32) },
      { ...context, networkRequestId: 'other' }, { ...context, windowId: [SESSION] }, { ...context, extra: true }, {}]) {
      assert.throws(() => h.api.verifyUnknownReceipt(h.wx, QR, qr, invalid))
    }
    assert.deepEqual(h.storage.get(STORAGE), original)
    assert.deepEqual({ writes: h.writes.length, sockets: h.sockets.length, random: h.randomRequests.length, scans: h.scans.length }, before)
    const fresh = harness()
    assert.equal(fresh.api.verifyUnknownReceipt(fresh.wx, QR, qr, context), true)
    assert.equal(fresh.storage.size, 0); assert.equal(fresh.writes.length, 0)
    assert.equal(fresh.api.lookupLocalArchive(fresh.wx, localLookup()), null)
    assert.equal(fresh.controller.prepareLocalArchive(LOCAL_ARCHIVE_CHECKS, localContext()), null)
  },
  async 'local archive rejects missing extra malformed or mismatched server review fields without clearing the original'() {
    const valid = localReview()
    const invalid = [null, { ...valid, eligible: true }, { ...valid, eligible: 0 }, { ...valid, cardId: 'c'.repeat(32) },
      { ...valid, cardId: [CARD] }, { ...valid, status: 'connected' }, { ...valid, resolution: 'pending' },
      { ...valid, reviewId: null }, { ...valid, reviewId: ['b'.repeat(32)] }, { ...valid, archivedAt: -1 },
      { ...valid, archivedAt: 1.5 }, { ...valid, archivedAt: true }, { ...valid, acknowledgements: ARCHIVE_CHECKS },
      { ...valid, acknowledgements: LOCAL_ARCHIVE_CHECKS.slice().reverse() }, { ...valid, key: KEY }]
    for (const [field, value] of Object.entries({ deviceId: 'dev_' + 'c'.repeat(32), requestId: 'other', windowId: 'c'.repeat(32), networkRequestId: 'other' })) {
      invalid.push({ ...valid, original: { ...valid.original, [field]: value } })
      invalid.push({ ...valid, original: { ...valid.original, [field]: [valid.original[field]] } })
    }
    for (const field of Object.keys(valid)) { const missing = { ...valid }; delete missing[field]; invalid.push(missing) }
    for (const server of invalid) {
      const h = await archiveHarness(), token = h.controller.prepareLocalArchive(LOCAL_ARCHIVE_CHECKS, localContext())
      assert.ok(token); assert.equal(h.controller.confirmLocalArchive(token, server), false)
      assert.deepEqual(h.storage.get(STORAGE), originalMarker()); assert.equal(h.writes.length, 0)
      assert.equal(h.controller.getState().pending, true); assert.equal(h.controller.getState().canConfigure, false)
      assert.equal(h.sockets.length, 0); assert.equal(h.randomRequests.length, 0)
      h.controller.hide()
    }
  },
  async 'local closeout tickets expire revoke and CAS fail without applying a previously committed server review to new context'() {
    for (const scenario of ['expire', 'hide', 'cancel', 'mode', 'rescan', 'pending', 'history', 'revision']) {
      const h = await archiveHarness({}, { v: 2, revision: 1, pending: originalMarker(), archives: [archiveEntry()] })
      const token = h.controller.prepareLocalArchive(LOCAL_ARCHIVE_CHECKS, localContext()); assert.ok(token)
      if (scenario === 'expire') h.advance(30000)
      else if (scenario === 'hide') { h.controller.hide(); h.controller.show(() => {}) }
      else if (scenario === 'cancel') h.controller.cancelArchive(token)
      else if (scenario === 'mode') h.controller.setTransportMode('direct')
      else if (scenario === 'rescan') await h.scan()
      else {
        const stored = plain(h.storage.get(STORAGE))
        if (scenario === 'pending') stored.pending.requestId = 'newer'
        if (scenario === 'history') stored.archives[0].reviewedAt++
        if (scenario === 'revision') stored.revision++
        h.storage.set(STORAGE, stored)
      }
      const original = plain(h.storage.get(STORAGE))
      assert.equal(h.controller.confirmLocalArchive(token, localReview()), false, scenario)
      assert.deepEqual(h.storage.get(STORAGE), original); assert.equal(h.writes.length, 0)
      assert.equal(h.controller.getState().canConfigure, false); assert.equal(h.sockets.length, 0)
      h.controller.hide()
    }
  },
  async 'server committed then local ticket expired recovers only by original readonly receipt and fresh explicit confirmation'() {
    const h = await archiveHarness(), token = h.controller.prepareLocalArchive(LOCAL_ARCHIVE_CHECKS, localContext())
    h.advance(30000)
    assert.equal(h.controller.confirmLocalArchive(token, localReview()), false)
    assert.deepEqual(h.storage.get(STORAGE), originalMarker()); assert.equal(h.writes.length, 0)
    h.controller.hide()
    const resumed = await archiveHarness({}, h.storage.get(STORAGE))
    const fresh = resumed.controller.prepareLocalArchive(LOCAL_ARCHIVE_CHECKS, localContext()); assert.ok(fresh)
    assert.equal(resumed.controller.confirmLocalArchive(fresh, localReview()), true)
    assert.equal(resumed.storage.get(STORAGE).archives.length, 1)
    assert.equal(resumed.sockets.length, 0); assert.equal(resumed.randomRequests.length, 0)
    assert.equal(resumed.api.lookupLocalArchive(resumed.wx, localLookup()).reviewId, REVIEW)
  },
  async 'local archive write failure no-op or corrupt readback preserves fail-closed pending and sends no packets'() {
    for (const scenario of ['storageFailure', 'storageNoop', 'corruptWrite', 'postWriteReadFailure']) {
      const h = await archiveHarness(), token = h.controller.prepareLocalArchive(LOCAL_ARCHIVE_CHECKS, localContext())
      h.options[scenario] = true
      assert.equal(h.controller.confirmLocalArchive(token, localReview()), false, scenario)
      assert.equal(h.controller.getState().pending, true); assert.equal(h.controller.getState().canConfigure, false)
      assert.equal(h.controller.prepareLocalArchive(LOCAL_ARCHIVE_CHECKS, localContext()), null)
      assert.equal(h.sockets.length, 0); assert.equal(h.randomRequests.length, 0)
      h.controller.hide()
    }
  },
  async 'mixed USB and local v2 archives remain readable while malformed local profiles and duplicated review identities reject'() {
    const local = { original: originalMarker(), status: 'unknown', resolution: 'operator_archived', reviewedAt: 1800000000000,
      checks: LOCAL_ARCHIVE_CHECKS.slice(), source: 'local_gateway', reviewId: REVIEW, outerRequestId: OUTER, cardId: CARD }
    const mixed = { v: 2, revision: 2, pending: null, archives: [archiveEntry(), local] }
    const h = harness({}, new Map([[STORAGE, plain(mixed)]]))
    assert.equal(h.controller.getState().archiveCount, 2); assert.equal(h.controller.getState().error, '')
    assert.deepEqual(plain(h.api.lookupLocalArchive(h.wx, localLookup())), local); assert.equal(h.writes.length, 0)
    const guard = h.load('services/provision-ledger-guard')
    assert.deepEqual(plain(guard.inspectLegacyProvisionPending(h.wx)), { pending: false, reviewIdentity: null })
    assert.equal(h.writes.length, 0); assert.equal(h.sockets.length, 0)
    const invalid = []
    for (const change of [{ source: 'other' }, { reviewId: null }, { cardId: [CARD] }, { outerRequestId: ['other'] },
      { checks: ARCHIVE_CHECKS }, { key: KEY }]) invalid.push({ ...mixed, archives: [archiveEntry(), { ...local, ...change }] })
    for (const field of ['source', 'reviewId', 'cardId', 'outerRequestId']) { const missing = { ...local }; delete missing[field]; invalid.push({ ...mixed, archives: [missing] }) }
    invalid.push({ ...mixed, revision: 3, archives: [...mixed.archives, { ...local, original: { ...local.original, sessionId: 'd'.repeat(32) } }] })
    for (const ledger of invalid) {
      const broken = harness({}, new Map([[STORAGE, plain(ledger)]]))
      assert.notEqual(broken.controller.getState().error, '')
      assert.throws(() => broken.api.lookupLocalArchive(broken.wx, localLookup()))
      assert.equal(broken.writes.length, 0); assert.equal(broken.sockets.length, 0)
      broken.controller.hide()
    }
  },
  async 'manual archive requires an authenticated unknown final receipt and exactly three explicit review checks'() {
    const h = await archiveHarness({}, originalMarker(), false)
    assert.equal(h.controller.getState().canArchive, false); assert.equal(h.controller.prepareArchive(ARCHIVE_CHECKS), null)
    const query = h.controller.query(); await h.connect(); h.reply('unknown'); await query
    assert.equal(h.controller.getState().status, 'unknown'); assert.equal(h.controller.getState().canArchive, false)
    assert.equal(h.controller.prepareArchive(ARCHIVE_CHECKS), null)
    for (const patch of [{ ciphertext: '00'.repeat(32) }, { requestId: 'wifi_other' }, { sessionId: '4'.repeat(32) }, { kind: 'status' }]) {
      await h.receipt('unknown', patch); assert.equal(h.controller.getState().canArchive, false)
    }
    for (const status of ['accepted', 'connecting']) {
      await h.receipt(status); assert.equal(h.controller.getState().canArchive, false)
    }
    await h.receipt('unknown')
    assert.equal(h.controller.getState().canArchive, true); assert.equal(h.controller.getState().archiveReady, true)
    assert.deepEqual(plain(h.controller.getState().reviewIdentity), { deviceId: DEVICE, sessionId: SESSION, requestId: 'wifi_original' })
    for (const invalid of [undefined, null, [], ['window-ended'], [...ARCHIVE_CHECKS, 'extra'], ['window-ended', 'window-ended', 'accept-unknown'],
      { 'window-ended': true, 'usb-checked': true, 'accept-unknown': true }, ['window-ended', 'usb-checked', true]]) {
      assert.equal(h.controller.prepareArchive(invalid), null)
    }
    assert.ok(h.controller.prepareArchive(ARCHIVE_CHECKS.slice().reverse()))
    assert.equal(h.writes.length, 0); h.controller.hide()
  },
  async 'confirmed manual archive atomically preserves the original unknown identity without network crypto randomness or success claims'() {
    const h = await archiveHarness(), before = { sockets: h.sockets.length, random: h.randomRequests.length, scans: h.scans.length, calls: h.calls.length }
    h.draft(); const acknowledgements = ARCHIVE_CHECKS.slice().reverse(), token = h.controller.prepareArchive(acknowledgements); assert.ok(token)
    acknowledgements.push('PRIVATE_CHECK_AFTER_PREPARE')
    assert.equal(await h.controller.confirmArchive(token), true)
    const expected = { original: originalMarker(), status: 'unknown', resolution: 'operator_archived', reviewedAt: 1800000000000, checks: ARCHIVE_CHECKS }
    assert.deepEqual(h.storage.get(STORAGE), { v: 2, revision: 1, pending: null, archives: [expected] })
    assert.deepEqual(h.storageKeys, [STORAGE]); assert.equal(h.writes.length, 1)
    assert.deepEqual({ sockets: h.sockets.length, random: h.randomRequests.length, scans: h.scans.length, calls: h.calls.length }, before)
    const state = h.controller.getState()
    assert.equal(state.status, 'unknown'); assert.equal(state.phase, 'archived'); assert.equal(state.pending, false)
    assert.equal(state.scanned, false); assert.equal(state.networkValid, false); assert.equal(state.compatibilityReady, false)
    assert.equal(state.canConfigure, false); assert.equal(state.canArchive, false); assert.equal(state.reviewIdentity, null)
    assert.equal(state.archiveCount, 1); assert.deepEqual(plain(state.lastArchive), expected)
    state.lastArchive.original.requestId = 'PRIVATE_MUTATION'; state.lastArchive.checks.push('PRIVATE_CHECK')
    assert.deepEqual(plain(h.controller.getState().lastArchive), expected); assert.deepEqual(h.storage.get(STORAGE).archives, [expected])
    assert.equal(await h.controller.confirmArchive(token), false)
    assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes]), /synthetic-password|家庭测试网|22222222|12:34:56:78:9A:BC|PRIVATE_MUTATION|PRIVATE_CHECK/)
    h.controller.hide()
  },
  async 'manual archive review expires or is revoked by cancellation mode rescan query background and newer receipt state'() {
    for (const scenario of ['expiry', 'cancel', 'mode', 'scan', 'query', 'hide', 'receipt', 'terminal']) {
      const h = await archiveHarness(), token = h.controller.prepareArchive(ARCHIVE_CHECKS); assert.ok(token, scenario)
      const original = plain(h.storage.get(STORAGE))
      if (scenario === 'expiry') h.advance(30001)
      else if (scenario === 'cancel') h.controller.cancelArchive(token)
      else if (scenario === 'mode') h.controller.setTransportMode('direct')
      else if (scenario === 'scan') await h.scan()
      else if (scenario === 'query') { const task = h.controller.query(); await h.connect(); h.reply('unknown'); await task }
      else if (scenario === 'hide') { h.controller.hide(); h.controller.show(value => h.states.push(plain(value))) }
      else await h.receipt(scenario === 'terminal' ? 'connected' : 'unknown')
      const writes = h.writes.length, sockets = h.sockets.length, random = h.randomRequests.length
      assert.equal(await h.controller.confirmArchive(token), false, scenario)
      assert.equal(h.writes.length, writes); assert.equal(h.sockets.length, sockets); assert.equal(h.randomRequests.length, random)
      if (scenario === 'terminal') assert.equal(h.storage.get(STORAGE), '')
      else assert.deepEqual(h.storage.get(STORAGE), original)
      assert.equal(h.controller.getState().canConfigure, false)
      if (['mode', 'scan', 'query', 'hide', 'terminal'].includes(scenario)) assert.equal(h.controller.getState().archiveReady, false, scenario)
      h.controller.hide()
    }
  },
  async 'archive commit freshly compares ledger revision and active marker instead of overwriting concurrent changes'() {
    for (const scenario of ['revision', 'pending', 'history', 'schema', 'read-failure']) {
      const ledger = { v: 2, revision: 4, pending: originalMarker(), archives: [archiveEntry()] }
      const h = await archiveHarness({}, ledger), token = h.controller.prepareArchive(ARCHIVE_CHECKS); assert.ok(token, scenario)
      const foreign = plain(ledger)
      if (scenario === 'revision') foreign.revision++
      else if (scenario === 'pending') foreign.pending.requestId = 'wifi_changed_concurrently'
      else if (scenario === 'history') foreign.archives[0].reviewedAt++
      else if (scenario === 'schema') { h.storage.set(STORAGE, originalMarker()) }
      else h.options.storageReadFailure = true
      if (scenario !== 'schema' && scenario !== 'read-failure') h.storage.set(STORAGE, foreign)
      const before = plain(h.storage.get(STORAGE))
      assert.equal(await h.controller.confirmArchive(token), false, scenario)
      assert.deepEqual(h.storage.get(STORAGE), before); assert.equal(h.writes.length, 0)
      assert.equal(h.sockets.length, 0); assert.equal(h.randomRequests.length, 0); assert.equal(h.controller.getState().canConfigure, false)
      h.controller.hide()
    }
  },
  async 'archive storage write no-op corruption or readback failure never unlocks a fresh configuration'() {
    for (const scenario of ['storageFailure', 'storageNoop', 'corruptWrite', 'postWriteReadFailure']) {
      const h = await archiveHarness(), token = h.controller.prepareArchive(ARCHIVE_CHECKS); assert.ok(token, scenario)
      h.options[scenario] = true
      assert.equal(await h.controller.confirmArchive(token), false, scenario)
      assert.equal(h.controller.getState().canConfigure, false); assert.notEqual(h.controller.getState().phase, 'archived')
      assert.equal(h.sockets.length, 0); assert.equal(h.randomRequests.length, 0)
      assert.doesNotMatch(JSON.stringify([h.states, h.logs]), /PRIVATE_CORRUPTION|PRIVATE_STORAGE_READ/)
      h.controller.hide()
    }
  },
  async 'an archive storage fault remains latched for this controller even after reads recover or a corrupt write resembles an empty legacy store'() {
    for (const scenario of ['postWriteReadFailure', 'storeWriteEmpty']) {
      const h = await archiveHarness(), token = h.controller.prepareArchive(ARCHIVE_CHECKS); assert.ok(token, scenario)
      h.options[scenario] = true
      assert.equal(await h.controller.confirmArchive(token), false, scenario)
      h.options[scenario] = false; h.options.storageReadFailure = false
      const writes = h.writes.length, original = { deviceId: DEVICE, sessionId: SESSION, requestId: 'wifi_original' }
      for (const step of ['prepare', 'scan', 'show']) {
        if (step === 'show') { h.controller.hide(); h.controller.show(value => h.states.push(plain(value))) }
        if (step !== 'prepare') await h.scan(QR.replace(SESSION, '5'.repeat(32)))
        h.draft(); assert.equal(h.controller.prepare(), null, scenario + '/' + step)
        const state = h.controller.getState()
        assert.equal(state.pending, true, scenario + '/' + step)
        assert.deepEqual(plain(state.reviewIdentity), original, scenario + '/' + step)
        assert.equal(state.canConfigure, false); assert.equal(state.canArchive, false)
        assert.equal(h.controller.prepareArchive(ARCHIVE_CHECKS), null)
        assert.equal(h.writes.length, writes); assert.equal(h.sockets.length, 0); assert.equal(h.randomRequests.length, 0)
      }
      h.controller.hide()
    }
  },
  async 'malformed version two ledgers and a full twenty-entry archive fail closed without pruning or removing the original pending identity'() {
    const valid = { v: 2, revision: 1, pending: originalMarker(), archives: [archiveEntry()] }
    const malformed = [
      { ...valid, revision: 0 }, { ...valid, revision: Number.MAX_SAFE_INTEGER + 1 }, { ...valid, revision: true },
      { ...valid, pending: { ...originalMarker(), key: 'PRIVATE_KEY' } }, { ...valid, archives: [] },
      { ...valid, archives: [archiveEntry(), archiveEntry()] },
      { ...valid, archives: [{ ...archiveEntry(), original: originalMarker() }] },
      { ...valid, archives: [{ ...archiveEntry(), status: 'connected' }] },
      { ...valid, archives: [{ ...archiveEntry(), resolution: 'success' }] },
      { ...valid, archives: [{ ...archiveEntry(), reviewedAt: -1 }] },
      { ...valid, archives: [{ ...archiveEntry(), reviewedAt: true }] },
      { ...valid, archives: [{ ...archiveEntry(), checks: ['window-ended', 'usb-checked', 'usb-checked'] }] },
      { ...valid, archives: [{ ...archiveEntry(), password: 'PRIVATE_PASSWORD' }] },
      { ...valid, revision: 1, archives: [archiveEntry(), archiveEntry(1)] },
      { ...valid, revision: 21, archives: Array.from({ length: 21 }, (_, index) => archiveEntry(index)) },
    ]
    for (const [index, value] of malformed.entries()) {
      const h = harness({}, new Map([[STORAGE, value]])); await h.scan(); h.draft()
      assert.equal(h.controller.getState().scanned, false, 'malformed ' + index)
      assert.equal(h.controller.getState().canConfigure, false, 'malformed ' + index)
      assert.equal(h.controller.getState().canArchive, false, 'malformed ' + index)
      assert.equal(h.controller.prepareArchive(ARCHIVE_CHECKS), null); assert.equal(h.writes.length, 0)
      assert.deepEqual(h.storage.get(STORAGE), value)
      assert.doesNotMatch(JSON.stringify([h.states, h.logs]), /PRIVATE_KEY|PRIVATE_PASSWORD/); h.controller.hide()
    }
    const full = { v: 2, revision: 20, pending: originalMarker(), archives: Array.from({ length: 20 }, (_, index) => archiveEntry(index)) }
    const h = await archiveHarness({}, full)
    assert.equal(h.controller.getState().archiveCount, 20); assert.equal(h.controller.getState().archiveReady, true)
    assert.equal(h.controller.getState().canArchive, false); assert.equal(h.controller.prepareArchive(ARCHIVE_CHECKS), null)
    assert.deepEqual(h.storage.get(STORAGE), full); assert.equal(h.writes.length, 0); h.controller.hide()
  },
  async 'array-coerced marker identities in legacy pending new pending or archived originals are invalid and cannot unlock networking'() {
    for (const field of ['deviceId', 'sessionId', 'requestId']) {
      const malformed = { ...originalMarker(), [field]: [originalMarker()[field]] }
      const archive = archiveEntry()
      const malformedArchive = { ...archive, original: { ...archive.original, [field]: [archive.original[field]] } }
      for (const value of [malformed,
        { v: 2, revision: 1, pending: malformed, archives: [archive] },
        { v: 2, revision: 1, pending: null, archives: [malformedArchive] }]) {
        const h = harness({}, new Map([[STORAGE, value]])); await h.scan(); h.draft()
        assert.equal(h.controller.getState().scanned, false, field)
        assert.equal(h.controller.getState().canConfigure, false); assert.equal(h.controller.prepare(), null)
        assert.equal(h.controller.prepareArchive(ARCHIVE_CHECKS), null); assert.equal(h.controller.getState().canArchive, false)
        assert.equal(h.writes.length, 0); assert.equal(h.sockets.length, 0); assert.equal(h.randomRequests.length, 0)
        assert.deepEqual(h.storage.get(STORAGE), value); h.controller.hide()
      }
    }
  },
  async 'unsafe review timestamps and an exhausted ledger revision cannot commit or erase the pending identity'() {
    for (const scenario of ['negative-time', 'fractional-time', 'revision-overflow']) {
      const value = scenario === 'revision-overflow' ? { v: 2, revision: Number.MAX_SAFE_INTEGER, pending: originalMarker(), archives: [archiveEntry()] } : originalMarker()
      const h = await archiveHarness({}, value), token = h.controller.prepareArchive(ARCHIVE_CHECKS); assert.ok(token)
      if (scenario === 'negative-time') h.advance(-1800000000001)
      else if (scenario === 'fractional-time') h.advance(.5)
      assert.equal(await h.controller.confirmArchive(token), false, scenario)
      assert.deepEqual(h.storage.get(STORAGE), value); assert.equal(h.writes.length, 0)
      assert.equal(h.controller.getState().canConfigure, false); assert.equal(h.sockets.length, 0); assert.equal(h.randomRequests.length, 0)
      h.controller.hide()
    }
  },
  async 'archived windows cannot be reused and a new explicit configuration preserves archive history and increments the one-key ledger'() {
    const h = await archiveHarness(), token = h.controller.prepareArchive(ARCHIVE_CHECKS)
    assert.equal(await h.controller.confirmArchive(token), true)
    const saved = plain(h.storage.get(STORAGE).archives)
    h.controller.hide(); h.controller.show(value => h.states.push(plain(value)))
    assert.equal(h.controller.getState().archiveCount, 1); assert.equal(h.controller.getState().pending, false)
    for (const oldQR of [QR, QR.replace(KEY, '44'.repeat(32))]) {
      await h.scan(oldQR); h.draft(); assert.equal(h.controller.getState().scanned, false)
      assert.equal(h.controller.getState().canConfigure, false); assert.equal(h.controller.prepare(), null)
    }
    const freshQR = QR.replace(SESSION, '5'.repeat(32)); await h.scan(freshQR); h.draft()
    assert.equal(h.controller.getState().canConfigure, true); assert.equal(h.sockets.length, 0); assert.equal(h.randomRequests.length, 0)
    const configure = h.controller.confirm(h.controller.prepare()), socket = await h.connect()
    assert.equal(h.packet(socket).sessionId, '5'.repeat(32)); h.reply('accepted', socket); await configure
    const inFlight = h.storage.get(STORAGE)
    assert.equal(inFlight.v, 2); assert.equal(inFlight.revision, 2); assert.equal(inFlight.pending.sessionId, '5'.repeat(32))
    assert.deepEqual(inFlight.archives, saved)
    await h.receipt('connected')
    assert.deepEqual(h.storage.get(STORAGE), { v: 2, revision: 3, pending: null, archives: saved })
    assert.equal(h.controller.getState().status, 'connected'); assert.equal(h.controller.getState().archiveCount, 1)
    assert.equal(h.storageKeys.every(key => key === STORAGE), true)
    assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes]), /synthetic-password|家庭测试网|22222222|12:34:56:78:9A:BC/)
    h.controller.hide()
  },
  async 'compatibility is explicit and cannot configure before a zero-payload connection probe'() {
    const h = harness(); await h.scan(); h.draft()
    assert.equal(h.controller.getState().transportMode, 'bound'); assert.equal(h.sockets.length, 0)
    assert.equal(h.controller.setTransportMode('untrusted'), false)
    assert.equal(h.controller.setTransportMode('direct'), true)
    assert.equal(h.controller.getState().canConfigure, false); assert.equal(h.controller.prepare(), null)
    await h.controller.confirm('not-a-prepared-confirmation')
    assert.equal(h.controller.getState().canProbe, true); assert.equal(h.sockets.length, 0)
    const before = h.calls.filter(value => value === 'random').length, task = h.controller.probe(); await flush()
    assert.equal(h.controller.getState().busy, true); assert.equal(h.controller.getState().canProbe, false)
    assert.equal(h.controller.setTransportMode('bound'), false); assert.equal(h.controller.getState().transportMode, 'direct')
    const socket = h.sockets.at(-1); assert.ok(socket); socket.listeners.connect(); await task
    assert.deepEqual(plain(socket.destination), { address: '10.47.0.1', port: 8766, timeout: 5 })
    assert.equal(socket.boundWifi, undefined); assert.equal(socket.packets.length, 0); assert.equal(socket.closed, true)
    assert.equal(h.writes.length, 0); assert.equal(h.storage.size, 0); assert.equal(h.calls.filter(value => value === 'random').length, before)
    assert.equal(h.controller.getState().probeStatus, 'reachable'); assert.equal(h.controller.getState().compatibilityReady, true)
    assert.equal(h.controller.getState().canConfigure, true); assert.equal(h.controller.getState().status, '')
    assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes]), /12:34:56:78:9A:BC|AI-CAT-ABC123|synthetic-password|22222222/)
    h.controller.hide()
  },
  async 'an explicitly probed direct configuration encrypts exactly one fixed-destination packet without binding'() {
    const h = harness(); await h.scan(); h.draft(); h.controller.setTransportMode('direct'); const probe = await h.probe()
    const token = h.controller.prepare(); assert.ok(token); assert.equal(h.sockets.length, 1)
    assert.equal(h.controller.getState().canProbe, false); await h.controller.probe(); assert.equal(h.sockets.length, 1)
    const task = h.controller.confirm(token), socket = await h.connect()
    assert.notEqual(socket, probe); assert.equal(socket.boundWifi, undefined)
    assert.deepEqual(plain(socket.destination), { address: '10.47.0.1', port: 8766, timeout: 5 })
    assert.deepEqual(h.decrypt(socket), { ssid: '家庭测试网', password: 'synthetic-password', security: 'wpa2-personal' })
    assert.equal(probe.packets.length, 0); assert.equal(socket.packets.length, 1); assert.equal(h.calls.includes('bindWifi'), false)
    h.reply('accepted'); await task
    assert.equal(h.controller.getState().pending, true); assert.equal(h.controller.getState().canConfigure, false)
    assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes]), /12:34:56:78:9A:BC|AI-CAT-ABC123|synthetic-password|22222222/)
    h.controller.hide()
  },
  async 'a bound-mode native failure never selects direct mode or silently opens another connection'() {
    const h = harness(); await h.scan(); h.draft(); const task = h.controller.confirm(h.controller.prepare()); await flush()
    h.sockets[0].listeners.error({ errCode: 4, errMsg: 'PRIVATE_BIND_FAILURE' }); await task
    assert.equal(h.controller.getState().status, 'not_sent'); assert.equal(h.controller.getState().transportMode, 'bound')
    assert.equal(h.controller.getState().compatibilityReady, false); assert.equal(h.sockets.length, 1)
    assert.equal(h.calls.filter(value => value === 'bindWifi').length, 1)
    await h.controller.probe(); assert.equal(h.sockets.length, 1); assert.equal(h.controller.getState().status, 'not_sent')
    h.controller.hide()
  },
  async 'direct probe errors timeouts and changed current hotspot cannot authorize a configuration or mutate request state'() {
    for (const scenario of ['event', 'timeout', 'wrong-ssid', 'changed-bssid']) {
      const options = scenario === 'wrong-ssid' ? { wifiSSID: 'PRIVATE_OTHER_WIFI' }
        : scenario === 'changed-bssid' ? { bssids: [BSSID, '22:34:56:78:9A:BC'] } : {}
      const h = harness(options); await h.scan(); h.draft(); h.controller.setTransportMode('direct')
      const originalStatus = h.controller.getState().status, task = h.controller.probe(); await flush()
      const socket = h.sockets.at(-1)
      if (scenario === 'wrong-ssid') assert.equal(socket, undefined)
      else if (scenario === 'event') socket.listeners.error({ errCode: 4, errMsg: 'PRIVATE_PROBE_NATIVE_ERROR' })
      else if (scenario === 'timeout') for (const timeout of [...h.timers.values()]) timeout()
      else socket.listeners.connect()
      await task
      assert.equal(h.controller.getState().probeStatus, 'failed'); assert.equal(h.controller.getState().compatibilityReady, false)
      assert.equal(h.controller.getState().canConfigure, false); assert.equal(h.controller.prepare(), null)
      assert.equal(h.controller.getState().status, originalStatus); assert.equal(h.writes.length, 0); assert.equal(h.storage.size, 0)
      assert.equal(h.calls.includes('bindWifi'), false); assert.equal(h.calls.includes('random'), false)
      if (socket) {
        assert.equal(socket.packets.length, 0); assert.equal(socket.closed, true)
        const states = h.states.length; socket.listeners.connect(); socket.listeners.error({ errMsg: 'PRIVATE_LATE_PROBE' }); await flush()
        assert.equal(h.states.length, states); assert.equal(socket.packets.length, 0)
      }
      assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes]), /PRIVATE_OTHER_WIFI|PRIVATE_PROBE|PRIVATE_LATE_PROBE|12:34:56:78:9A:BC|22:34:56:78:9A:BC|synthetic-password/)
      h.controller.hide()
    }
  },
  async 'proof expiry mode changes and rescans revoke an old direct confirmation without erasing the network draft'() {
    for (const scenario of ['expiry', 'mode', 'scan']) {
      const h = harness(); await h.scan(); h.draft(); h.controller.setTransportMode('direct'); await h.probe()
      const token = h.controller.prepare(); assert.ok(token)
      if (scenario === 'expiry') { h.advance(60001); h.expireProof() }
      else if (scenario === 'mode') h.controller.setTransportMode('bound')
      else await h.scan(QR.replace(SESSION, '4'.repeat(32)))
      assert.equal(h.controller.getState().compatibilityReady, false)
      await h.controller.confirm(token)
      assert.equal(h.sockets.length, 1); assert.equal(h.writes.length, 0); assert.equal(h.storage.size, 0)
      if (scenario === 'mode') { assert.equal(h.controller.getState().transportMode, 'bound'); assert.equal(h.controller.getState().networkValid, true) }
      if (scenario === 'expiry') assert.equal(h.controller.getState().canConfigure, false)
      h.controller.hide()
    }
  },
  async 'direct sending rechecks current BSSID and absolute proof expiry after asynchronous work and before writing'() {
    for (const scenario of ['bssid', 'random', 'connect']) {
      const h = harness(scenario === 'random' ? { deferRandomAt: 2 } : {})
      await h.scan(); h.draft(); h.controller.setTransportMode('direct'); await h.probe()
      const token = h.controller.prepare(); assert.ok(token)
      if (scenario === 'bssid') h.options.bssid = '22:34:56:78:9A:BC'
      const task = h.controller.confirm(token); await flush()
      if (scenario === 'random') {
        assert.equal(h.randomRequests.length, 2); h.advance(60001)
        const value = h.randomRequests[1]; value.success({ randomValues: new Uint8Array(value.length).fill(9).buffer })
      } else if (scenario === 'connect') {
        const socket = h.sockets.at(-1); assert.equal(h.sockets.length, 2); h.advance(60001); socket.listeners.connect()
      }
      await task
      assert.equal(h.controller.getState().status, 'not_sent'); assert.equal(Boolean(h.storage.get(STORAGE)), false)
      assert.equal(h.controller.getState().compatibilityReady, false)
      assert.equal(h.sockets.every(socket => socket.packets.length === 0), true)
      assert.equal(h.calls.includes('bindWifi'), false)
      assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes]), /12:34:56:78:9A:BC|22:34:56:78:9A:BC|synthetic-password/)
      h.controller.hide()
    }
  },
  async 'a direct connected socket freshly checks the current hotspot and sends nothing after a BSSID change or read failure'() {
    for (const scenario of ['changed-bssid', 'read-failure']) {
      const h = harness(); await h.scan(); h.draft(); h.controller.setTransportMode('direct'); await h.probe()
      const task = h.controller.confirm(h.controller.prepare()); await flush(); const socket = h.sockets.at(-1)
      assert.equal(h.wifiReads.length, 3); assert.equal(socket.packets.length, 0)
      if (scenario === 'changed-bssid') h.options.bssid = '22:34:56:78:9A:BC'
      else h.options.readWifiFail = 12006
      socket.listeners.connect(); await flush()
      assert.equal(h.wifiReads.length, 4); assert.equal(socket.packets.length, 0)
      await task
      assert.equal(socket.closed, true); assert.equal(h.controller.getState().status, 'not_sent')
      assert.equal(Boolean(h.storage.get(STORAGE)), false); assert.equal(h.calls.includes('bindWifi'), false)
      assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes]), /PRIVATE_WIFI_READ_ERROR|12:34:56:78:9A:BC|22:34:56:78:9A:BC|synthetic-password/)
      h.controller.hide()
    }
  },
  async 'hiding or timing out a fresh direct prewrite WiFi read closes its socket and late callbacks cannot send'() {
    for (const scenario of ['hide', 'wifi-timeout', 'tcp-timeout']) {
      const h = harness(); await h.scan(); h.draft(); h.controller.setTransportMode('direct'); await h.probe()
      const task = h.controller.confirm(h.controller.prepare()); await flush(); const socket = h.sockets.at(-1)
      const original = plain(h.storage.get(STORAGE)); h.options.deferGetConnectedWifi = true
      socket.listeners.connect(); await flush()
      assert.equal(h.wifiReads.length, 4); assert.equal(socket.packets.length, 0)
      const callback = h.wifiReads.at(-1)
      if (scenario === 'hide') h.controller.hide()
      else {
        const delay = scenario === 'wifi-timeout' ? 6000 : 10000
        const timer = [...h.timers].find(([id]) => h.timerDelays.get(id) === delay); assert.ok(timer); timer[1]()
      }
      await task
      assert.equal(socket.closed, true); assert.equal(socket.packets.length, 0)
      if (scenario === 'hide') assert.deepEqual(h.storage.get(STORAGE), original)
      else { assert.equal(h.controller.getState().status, 'not_sent'); assert.equal(Boolean(h.storage.get(STORAGE)), false) }
      const states = h.states.length, writes = h.writes.length
      callback.success({ wifi: { SSID: 'AI-CAT-ABC123', BSSID } }); callback.fail({ errCode: 12006, errMsg: 'PRIVATE_LATE_WIFI' })
      socket.listeners.connect(); await flush()
      assert.equal(h.states.length, states); assert.equal(h.writes.length, writes); assert.equal(socket.packets.length, 0)
      assert.equal(h.calls.includes('bindWifi'), false)
      h.controller.hide()
    }
  },
  async 'duplicate direct connect events perform one fresh WiFi read and at most one encrypted write'() {
    const h = harness(); await h.scan(); h.draft(); h.controller.setTransportMode('direct'); await h.probe()
    const task = h.controller.confirm(h.controller.prepare()); await flush(); const socket = h.sockets.at(-1)
    h.options.deferGetConnectedWifi = true
    socket.listeners.connect(); socket.listeners.connect(); socket.listeners.connect(); await flush()
    assert.equal(h.wifiReads.length, 4); assert.equal(socket.packets.length, 0)
    const callback = h.wifiReads.at(-1); callback.success({ wifi: { SSID: 'AI-CAT-ABC123', BSSID } }); await flush()
    assert.equal(socket.packets.length, 1); assert.deepEqual(h.decrypt(socket), { ssid: '家庭测试网', password: 'synthetic-password', security: 'wpa2-personal' })
    callback.success({ wifi: { SSID: 'AI-CAT-ABC123', BSSID } }); socket.listeners.connect(); await flush()
    assert.equal(h.wifiReads.length, 4); assert.equal(socket.packets.length, 1); assert.equal(h.calls.includes('bindWifi'), false)
    h.reply('accepted', socket); await task; h.controller.hide()
  },
  async 'hide cancels a direct probe and late callbacks or its old expiry cannot revive compatibility on another visit'() {
    for (const scenario of ['wifi', 'socket', 'ready']) {
      const h = harness(scenario === 'wifi' ? { deferGetConnectedWifi: true } : {})
      await h.scan(); h.draft(); h.controller.setTransportMode('direct')
      const task = h.controller.probe(); await flush(); const callback = h.wifiReads.at(-1), socket = h.sockets.at(-1)
      let expiry
      if (scenario === 'ready') {
        socket.listeners.connect(); await task
        expiry = [...h.timers].find(([id]) => h.timerDelays.get(id) === 60000)[1]
      }
      h.controller.hide(); await task; const count = h.states.length, sockets = h.sockets.length
      if (scenario === 'wifi') callback.success({ wifi: { BSSID, SSID: 'AI-CAT-ABC123' } })
      else { socket.listeners.connect(); socket.listeners.error({ errCode: 4, errMsg: 'PRIVATE_OLD_PROBE' }) }
      if (expiry) expiry()
      await flush(); assert.equal(h.states.length, count); assert.equal(h.sockets.length, sockets)
      h.controller.show(value => h.states.push(plain(value)))
      assert.equal(h.controller.getState().transportMode, 'bound'); assert.equal(h.controller.getState().compatibilityReady, false)
      assert.equal(h.controller.getState().networkValid, false); assert.equal(h.controller.getState().scanned, false)
      assert.equal(h.writes.length, 0); assert.equal(h.storage.size, 0)
      assert.equal(h.sockets.every(value => value.packets.length === 0 && value.closed), true)
      assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes]), /PRIVATE_OLD_PROBE|12:34:56:78:9A:BC|synthetic-password/)
      h.controller.hide()
    }
  },
  async 'an unknown direct request can only be queried with its original identity after a new explicit probe'() {
    const h = harness(); await h.scan(); h.draft(); h.controller.setTransportMode('direct'); await h.probe()
    const task = h.controller.confirm(h.controller.prepare()), socket = await h.connect()
    socket.listeners.error({ errCode: 4, errMsg: 'PRIVATE_AFTER_WRITE' }); await task
    assert.equal(h.controller.getState().status, 'unknown'); const original = plain(h.storage.get(STORAGE)), writes = h.writes.length
    h.controller.hide(); h.controller.show(value => h.states.push(plain(value))); await h.scan()
    assert.equal(h.controller.getState().transportMode, 'bound'); h.controller.setTransportMode('direct')
    assert.equal(h.controller.getState().canQuery, false); assert.equal(h.controller.getState().canConfigure, false)
    const beforeQuery = h.sockets.length; await h.controller.query()
    assert.equal(h.sockets.length, beforeQuery); assert.equal(h.writes.length, writes); assert.deepEqual(h.storage.get(STORAGE), original)
    h.draft(); assert.equal(h.controller.prepare(), null)
    const probe = await h.probe(); assert.equal(probe.packets.length, 0); assert.equal(h.writes.length, writes)
    assert.deepEqual(h.storage.get(STORAGE), original); assert.equal(h.controller.getState().canQuery, true)
    const query = h.controller.query(), receipt = await h.connect()
    assert.equal(h.packet(receipt).kind, 'status'); assert.equal(h.packet(receipt).requestId, original.requestId)
    assert.deepEqual(h.decrypt(receipt), {}); h.reply('connected', receipt); await query
    assert.equal(h.storage.get(STORAGE), ''); assert.equal(h.controller.getState().status, 'connected')
    assert.equal(h.sockets.filter(value => value.packets.length && h.packet(value).kind === 'configure').length, 1)
    assert.equal(h.calls.includes('bindWifi'), false); h.controller.hide()
  },
  async 'unmatched pending identities invalid storage and non Android visits cannot run a compatibility probe'() {
    for (const scenario of ['foreign', 'storage', 'platform', 'hidden']) {
      const original = scenario === 'storage' ? { v: 99, private: 'PRIVATE_INVALID_MARKER' }
        : { v: 1, deviceId: DEVICE, sessionId: SESSION, requestId: 'wifi_original' }
      const store = new Map(scenario === 'foreign' || scenario === 'storage' ? [[STORAGE, original]] : [])
      const h = harness(scenario === 'platform' ? { platform: 'devtools' } : {}, store)
      await h.scan(scenario === 'foreign' ? QR.replace(SESSION, '4'.repeat(32)) : QR)
      if (scenario === 'hidden') h.controller.hide()
      h.controller.setTransportMode('direct'); await h.controller.probe()
      assert.equal(h.controller.getState().canProbe, false); assert.equal(h.sockets.length, 0)
      assert.equal(h.wifiStarts.length, 0); assert.equal(h.writes.length, 0)
      if (store.has(STORAGE)) assert.deepEqual(store.get(STORAGE), original)
      assert.doesNotMatch(JSON.stringify(h.states), /PRIVATE_INVALID_MARKER/); h.controller.hide()
    }
  },
  async 'native binding events expose only whitelisted numeric diagnostic codes and never native details'() {
    const allowed = [-4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6]
    for (const nativeCode of [...allowed, undefined, null, '4', 10009, -99, NaN, Infinity, {}, 4n]) {
      const h = harness(); await h.scan(); h.draft(); const task = h.controller.confirm(h.controller.prepare()); await flush()
      const socket = h.sockets.at(-1); assert.ok(socket)
      socket.listeners.error({ errCode: nativeCode, errMsg: 'PRIVATE_NATIVE_DETAILS AI-CAT-ABC123 12:34:56:78:9A:BC synthetic-password', detail: 'PRIVATE_NATIVE_DETAIL_FIELD' })
      await task
      assert.equal(h.controller.getState().status, 'not_sent'); assert.equal(h.storage.get(STORAGE), '')
      const suffix = typeof nativeCode === 'number' && allowed.includes(nativeCode) ? String(nativeCode) : 'NA'
      assert.match(h.controller.getState().error, new RegExp('\\bWIFI_BIND_EVENT_' + suffix + '\\b'))
      assert.equal(socket.packets.length, 0); assert.equal(socket.closed, true)
      assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes]), /PRIVATE_NATIVE|AI-CAT-ABC123|12:34:56:78:9A:BC|synthetic-password/)
    }
  },
  async 'creation event registration binding call and premature close use distinct fixed diagnostic stages'() {
    for (const [options, stage] of [[{ socketFailure: true }, 'SOCKET_CREATE'], [{ eventsThrow: true }, 'SOCKET_EVENTS'], [{ bindThrow: true }, 'WIFI_BIND_CALL'], [{}, 'WIFI_BIND_CLOSED']]) {
      const h = harness({ ...options, nativeCode: 4 }); await h.scan(); h.draft()
      const task = h.controller.confirm(h.controller.prepare()); await flush()
      const socket = h.sockets.at(-1)
      if (!options.socketFailure && !options.eventsThrow && !options.bindThrow) socket.listeners.close({ errCode: 4, errMsg: 'PRIVATE_NATIVE_CLOSE' })
      await task
      const suffix = stage === 'WIFI_BIND_CLOSED' ? 'NA' : '4'
      assert.match(h.controller.getState().error, new RegExp('\\b' + stage + '_' + suffix + '\\b'))
      assert.equal(h.controller.getState().status, 'not_sent'); assert.equal(h.storage.get(STORAGE), '')
      if (socket) { assert.equal(socket.packets.length, 0); assert.equal(socket.closed, true) }
      assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes]), /PRIVATE_NATIVE|AI-CAT-ABC123|12:34:56:78:9A:BC|synthetic-password/)
    }
  },
  async 'native error codes after write do not downgrade unknown or clear the original request marker'() {
    const h = harness(); await h.scan(); h.draft(); const task = h.controller.confirm(h.controller.prepare()), socket = await h.connect()
    const original = plain(h.storage.get(STORAGE)); assert.equal(socket.packets.length, 1)
    socket.listeners.error({ errCode: 4, errMsg: 'PRIVATE_NATIVE_AFTER_WRITE' }); await task
    assert.equal(h.controller.getState().status, 'unknown'); assert.deepEqual(h.storage.get(STORAGE), original)
    assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes]), /PRIVATE_NATIVE|TCP_CONNECT_4|12:34:56:78:9A:BC|synthetic-password/)
  },
  async 'only a readable complete device hotspot name may reach binding and other Wi-Fi stays not sent'() {
    for (const wifiSSID of [undefined, null, '', '<unknown ssid>', 'PRIVATE_HOME_ROUTER', 'AI-CAT-abc123', 'AI-CAT-ABC123-private']) {
      const h = harness({ wifiSSID }); await h.scan(); h.draft()
      const task = h.controller.confirm(h.controller.prepare()); await flush()
      assert.equal(h.sockets.length, 0); await task
      assert.equal(h.controller.getState().status, 'not_sent'); assert.equal(h.storage.get(STORAGE), '')
      assert.match(h.controller.getState().error, /热点|Wi-Fi/)
      assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes, [...h.storage]]), /PRIVATE_HOME_ROUTER|unknown ssid|AI-CAT-abc123|ABC123-private|12:34:56:78:9A:BC|synthetic-password/)
    }
  },
  async 'connect callbacks cannot write before successful Wi-Fi binding and a later bound connection writes only once'() {
    const h = harness(); await h.scan(); h.draft(); const task = h.controller.confirm(h.controller.prepare()); await flush()
    const socket = h.sockets.at(-1); assert.ok(socket)
    socket.listeners.connect(); assert.equal(socket.packets.length, 0); assert.equal(socket.destination, undefined)
    socket.listeners.bind({ errMsg: 'bindWifi:ok' }); socket.listeners.connect(); socket.listeners.connect()
    assert.equal(socket.packets.length, 1); h.reply('accepted'); await task
    assert.equal(h.controller.getState().status, 'accepted')
  },
  async 'native Wi-Fi binding requires initialization and the current full BSSID before fixed TCP connection'() {
    const h = harness(); await h.scan(); h.draft(); assert.equal(h.wifiStarts.length, 0); assert.equal(h.wifiReads.length, 0)
    const task = h.controller.confirm(h.controller.prepare()); await flush()
    assert.equal(h.wifiStarts.length, 1); assert.equal(h.wifiReads.length, 1)
    assert.deepEqual(h.calls.filter(value => ['startWifi', 'getConnectedWifi', 'bindWifi'].includes(value)), ['startWifi', 'getConnectedWifi', 'bindWifi'])
    const socket = h.sockets.at(-1); assert.deepEqual(socket.boundWifi, { BSSID })
    assert.equal(socket.destination, undefined); assert.equal(socket.packets.length, 0)
    socket.listeners.bind({ errMsg: 'bindWifi:ok' }); socket.listeners.connect(); h.reply('accepted'); await task
    assert.equal(socket.packets.length, 1)
    assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes, [...h.storage]]), /12:34:56:78:9A:BC|AI-CAT-ABC123|synthetic-password/)
  },
  async 'missing Wi-Fi native capabilities block preparation without reading or creating a socket'() {
    for (const missing of ['startWifi', 'getConnectedWifi']) {
      const h = harness(); delete h.wx[missing]; await h.scan(); h.draft()
      assert.equal(h.controller.prepare(), null); assert.match(h.controller.getState().error, /Wi-Fi|热点|BSSID|权限/)
      assert.equal(h.wifiStarts.length, 0); assert.equal(h.wifiReads.length, 0); assert.equal(h.sockets.length, 0)
    }
  },
  async 'Wi-Fi init and current-network read failures remain not sent with fixed private-safe guidance'() {
    for (const options of [{ startWifiFail: 12005 }, { startWifiThrow: true }, { readWifiFail: 12006 }, { readWifiFail: 12007 }, { readWifiThrow: true }]) {
      const h = harness(options); await h.scan(); h.draft(); await h.controller.confirm(h.controller.prepare())
      assert.equal(h.controller.getState().status, 'not_sent'); assert.equal(h.storage.get(STORAGE), '')
      assert.equal(h.sockets.length, 0); assert.equal(h.wifiStarts.length, 1)
      assert.equal(h.wifiReads.length, options.startWifiFail || options.startWifiThrow ? 0 : 1)
      assert.match(h.controller.getState().error, /Wi-Fi|热点|BSSID|权限/)
      assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes]), /PRIVATE_WIFI|synthetic-password|12:34:56:78:9A:BC/)
    }
  },
  async 'missing malformed and masked BSSID never reach socket creation or a plaintext write'() {
    for (const bssid of [undefined, null, '', 'invalid', '12:34:56:78:9A', '00:00:00:00:00:00', 'ff:ff:ff:ff:ff:ff', 'FF:FF:FF:FF:FF:FF', '02:00:00:00:00:00']) {
      const h = harness({ bssid }); await h.scan(); h.draft(); await h.controller.confirm(h.controller.prepare())
      assert.equal(h.controller.getState().status, 'not_sent'); assert.equal(h.storage.get(STORAGE), '')
      assert.equal(h.sockets.length, 0); assert.match(h.controller.getState().error, /Wi-Fi|热点|BSSID|权限/)
      assert.equal(h.wifiStarts.length, 1); assert.equal(h.wifiReads.length, 1)
    }
  },
  async 'hide cancels a pending Wi-Fi callback and late native success cannot create a socket or advance to the next read'() {
    for (const options of [{ deferStartWifi: true }, { deferGetConnectedWifi: true }]) {
      const h = harness(options); await h.scan(); h.draft(); const task = h.controller.confirm(h.controller.prepare()); await flush()
      assert.equal(h.wifiStarts.length, 1); assert.equal(h.wifiReads.length, options.deferStartWifi ? 0 : 1)
      const callback = options.deferStartWifi ? h.wifiStarts[0] : h.wifiReads[0]
      h.controller.hide(); await task; const count = h.states.length, reads = h.wifiReads.length
      const foreign = { v: 1, deviceId: DEVICE, sessionId: SESSION, requestId: 'wifi_foreign_after_hide' }; h.storage.set(STORAGE, foreign)
      callback.success(options.deferStartWifi ? { errMsg: 'startWifi:ok' } : { wifi: { BSSID, SSID: 'AI-CAT-ABC123' }, errMsg: 'getConnectedWifi:ok' })
      callback.fail({ errCode: 12007, errMsg: 'PRIVATE_LATE_WIFI_ERROR' }); await flush()
      assert.equal(h.sockets.length, 0); assert.equal(h.wifiReads.length, reads); assert.equal(h.states.length, count)
      assert.deepEqual(h.storage.get(STORAGE), foreign)
    }
  },
  async 'Wi-Fi callback timeouts are not sent and late success cannot start a native network channel'() {
    for (const options of [{ deferStartWifi: true }, { deferGetConnectedWifi: true }]) {
      const h = harness(options); await h.scan(); h.draft(); const task = h.controller.confirm(h.controller.prepare()); await flush()
      const callback = options.deferStartWifi ? h.wifiStarts[0] : h.wifiReads[0]
      assert.ok(callback); assert.ok(h.timers.size)
      for (const timeout of [...h.timers.values()]) timeout()
      await task
      assert.equal(h.controller.getState().status, 'not_sent'); assert.equal(h.storage.get(STORAGE), '')
      assert.match(h.controller.getState().error, /Wi-Fi|热点|BSSID|权限/)
      const reads = h.wifiReads.length, states = h.states.length
      callback.success(options.deferStartWifi ? { errMsg: 'startWifi:ok' } : { wifi: { BSSID, SSID: 'AI-CAT-ABC123' }, errMsg: 'getConnectedWifi:ok' })
      callback.fail({ errMsg: 'PRIVATE_LATE_TIMEOUT_ERROR' }); await flush()
      assert.equal(h.sockets.length, 0); assert.equal(h.wifiReads.length, reads); assert.equal(h.states.length, states)
      assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes]), /PRIVATE_LATE|12:34:56:78:9A:BC|synthetic-password/)
    }
  },
  async 'duplicate native callbacks and completed bind or connect callbacks never create a second packet'() {
    for (const outcome of ['accepted', 'prewrite_error']) {
      const h = harness({ deferStartWifi: true, deferGetConnectedWifi: true }); await h.scan(); h.draft()
      const task = h.controller.confirm(h.controller.prepare()); await flush()
      h.wifiStarts[0].success({ errMsg: 'startWifi:ok' }); h.wifiStarts[0].success({ errMsg: 'startWifi:ok' })
      assert.equal(h.wifiReads.length, 1)
      h.wifiReads[0].success({ wifi: { BSSID, SSID: 'AI-CAT-ABC123' } }); h.wifiReads[0].success({ wifi: { BSSID, SSID: 'AI-CAT-ABC123' } }); await flush()
      assert.equal(h.sockets.length, 1)
      const socket = h.sockets[0]
      if (outcome === 'accepted') {
        socket.listeners.bind({ errMsg: 'bindWifi:ok' }); socket.listeners.bind({ errMsg: 'bindWifi:ok' })
        assert.equal(h.calls.filter(value => value === 'connect').length, 1)
        socket.listeners.connect(); h.reply('accepted')
      } else socket.listeners.error({ errMsg: 'PRIVATE_EARLY_BIND_ERROR' })
      await task
      const packets = socket.packets.length, calls = h.calls.length, states = h.states.length
      socket.listeners.bind({ errMsg: 'bindWifi:ok' }); socket.listeners.connect()
      h.wifiStarts[0].success({}); h.wifiReads[0].success({ wifi: { BSSID, SSID: 'AI-CAT-ABC123' } }); await flush()
      assert.equal(socket.packets.length, packets); assert.equal(h.calls.length, calls); assert.equal(h.states.length, states)
      assert.equal(packets, outcome === 'accepted' ? 1 : 0)
      assert.equal(h.controller.getState().status, outcome === 'accepted' ? 'accepted' : 'not_sent')
      assert.doesNotMatch(JSON.stringify([h.states, h.logs, h.writes]), /PRIVATE_EARLY|12:34:56:78:9A:BC|synthetic-password/)
    }
  },
  async 'binding and TCP connection failures before write stay not sent while write failures retain unknown'() {
    for (const options of [{ bindThrow: true }, { connectThrow: true }, { writeThrow: true }]) {
      const h = harness(options); await h.scan(); h.draft(); const task = h.controller.confirm(h.controller.prepare()); await flush()
      const socket = h.sockets.at(-1); assert.ok(socket)
      if (!options.bindThrow) socket.listeners.bind({ errMsg: 'bindWifi:ok' })
      if (options.writeThrow) socket.listeners.connect()
      await task
      assert.equal(h.controller.getState().status, options.writeThrow ? 'unknown' : 'not_sent')
      assert.equal(Boolean(h.storage.get(STORAGE)), Boolean(options.writeThrow))
      if (!options.writeThrow) assert.match(h.controller.getState().error, /Wi-Fi|热点|BSSID|权限/)
      assert.doesNotMatch(JSON.stringify([h.states, h.logs]), /PRIVATE_WIFI|private connect|private write|synthetic-password/)
      assert.equal(socket.closed, true)
    }
  },
  async 'loading hiding and scanning never connect or send credentials automatically'() {
    const h = harness(); assert.equal(h.sockets.length, 0); await h.scan(); h.draft()
    assert.equal(h.sockets.length, 0); assert.equal(h.writes.length, 0)
    assert.equal(h.scans[0].onlyFromCamera, true); assert.deepEqual(plain(h.scans[0].scanType), ['qrCode'])
    assert.equal(h.controller.getState().canConfigure, true)
    assert.doesNotMatch(JSON.stringify(h.states), /synthetic-password|家庭测试网|22222222/)
    h.controller.hide()
  },
  async 'QR rejects URL overrides whitespace wrong version legacy serial and extra fields'() {
    const h = harness()
    for (const invalid of ['http://evil/' + QR, QR + '\n', QR + ':extra', QR.replace(':1:', ':2:'), QR.replace(DEVICE, '7c2b63fd4a128')]) assert.throws(() => h.api.parseProvisioningQR(invalid))
    assert.equal(h.api.parseProvisioningQR(QR).deviceId, DEVICE)
  },
  async 'late old camera callbacks cannot replace a newer scan or erase its private network draft'() {
    const h = harness(); await h.scan(); const old = h.scans[0]
    await h.scan(); h.draft()
    old.fail({ errMsg: 'private old scan error' })
    assert.equal(h.controller.getState().scanned, true); assert.equal(h.controller.getState().networkValid, true)
    assert.equal(h.controller.getState().error, '')
  },
  async 'network UTF8 bounds preserve spaces and reject controls extra keys and invalid security'() {
    const h = harness(), valid = { ssid: '家庭测试网 ', password: ' test password ', security: 'wpa2-personal' }
    assert.deepEqual(plain(h.api.verifyProvisioningNetwork(valid)), valid)
    for (const patch of [{ ssid: '猫'.repeat(11) }, { password: 'short' }, { password: 'z'.repeat(64) }, { ssid: 'a\nb' }, { ssid: '\u202eabc' }, { security: 'open' }, { url: 'http://evil' }]) assert.throws(() => h.api.verifyProvisioningNetwork({ ...valid, ...patch }))
  },
  async 'noble packet ciphertext matches independent Node AESGCM and authenticates every context field'() {
    const h = harness(), invite = h.api.parseProvisioningQR(QR), nonce = new Uint8Array(12).fill(4); nonce[0] = 0
    const body = { ssid: '家庭', password: 'test-password', security: 'wpa2-personal' }
    const packet = h.api.encodeProvisioningPacket(invite, 'wifi_1', 'configure', body, nonce)
    const aad = Buffer.from(JSON.stringify(['aicat-provision-v1', 'request', DEVICE, SESSION, 'wifi_1', 'configure']))
    const cipher = crypto.createCipheriv('aes-256-gcm', Buffer.from(KEY, 'hex'), nonce); cipher.setAAD(aad)
    const reference = Buffer.concat([cipher.update(JSON.stringify(body)), cipher.final(), cipher.getAuthTag()])
    assert.equal(packet.ciphertext, reference.toString('hex'))
    assert.deepEqual(plain(h.api.decodeProvisioningPacket(invite, packet, 'wifi_1', 'configure', 'request')), body)
    for (const patch of [{ requestId: 'other' }, { kind: 'status' }, { deviceId: 'dev_' + 'a'.repeat(32) }, { sessionId: 'a'.repeat(32) }, { nonce: '01' + packet.nonce.slice(2) }, { ciphertext: packet.ciphertext.slice(0, -2) + 'ff' }, { extra: true }]) assert.throws(() => h.api.decodeProvisioningPacket(invite, { ...packet, ...patch }, 'wifi_1', 'configure', 'request'))
  },
  async 'actual TS noble AESGCM matches shared Python Node reference including UTF8 and trailing spaces'() {
    const h = harness(), vector = JSON.parse(fs.readFileSync(path.join(root, 'scripts/fixtures/provisioning-aesgcm-v1.json'), 'utf8'))
    const invite = { deviceId: vector.deviceId, sessionId: vector.sessionId, keyHex: vector.keyHex }
    const packet = h.api.encodeProvisioningPacket(invite, vector.requestId, vector.kind, vector.body, new Uint8Array(Buffer.from(vector.nonceHex, 'hex')))
    assert.equal(packet.ciphertext, vector.ciphertextHex)
    assert.deepEqual(plain(h.api.decodeProvisioningPacket(invite, packet, vector.requestId, vector.kind, 'request')), vector.body)
  },
  async 'confirmed request persists only IDs then writes once to fixed bound WiFi TCP with encrypted payload'() {
    const h = harness(); await h.scan(); h.draft(); const token = h.controller.prepare()
    assert.ok(token); const task = h.controller.confirm(token), socket = await h.connect()
    assert.deepEqual(plain(socket.destination), { address: '10.47.0.1', port: 8766, timeout: 5 })
    assert.ok(h.writes.length); assert.equal(h.packet(socket).kind, 'configure')
    assert.doesNotMatch(JSON.stringify(h.writes), /password|家庭|2222222222/)
    assert.doesNotMatch(socket.packets[0].toString(), /synthetic-password|家庭测试网/)
    socket.listeners.connect(); assert.equal(socket.packets.length, 1)
    h.reply('accepted', socket, {}, true); await task
    assert.equal(h.controller.getState().status, 'accepted'); assert.equal(h.controller.getState().canConfigure, false); assert.equal(h.controller.getState().canQuery, true)
    assert.equal(h.controller.prepare(), null); assert.deepEqual(h.logs, [])
  },
  async 'original status queries contain no credentials and never replay configure and terminal reply clears matching marker'() {
    const h = harness(); await h.scan(); h.draft(); let task = h.controller.confirm(h.controller.prepare()); await h.connect(); h.reply('accepted'); await task
    task = h.controller.query(); const socket = await h.connect()
    assert.equal(h.packet(socket).kind, 'status')
    const invite = h.api.parseProvisioningQR(QR), body = h.api.decodeProvisioningPacket(invite, h.packet(socket), h.packet(socket).requestId, 'status', 'request')
    assert.deepEqual(plain(body), {})
    h.reply('connected'); await task
    assert.equal(h.controller.getState().status, 'connected'); assert.equal(h.controller.getState().canQuery, false)
    assert.equal(h.storage.get(STORAGE), ''); assert.equal(h.sockets.filter(s => s.packets.length && h.packet(s).kind === 'configure').length, 1)
  },
  async 'unknown survives controller recreation and requires original QR before receipt only query'() {
    const h = harness(); await h.scan(); h.draft(); const task = h.controller.confirm(h.controller.prepare()), socket = await h.connect()
    socket.listeners.error({ errMsg: 'private error' }); await task
    assert.equal(h.controller.getState().status, 'unknown'); assert.ok(h.storage.get(STORAGE))
    h.controller.hide()
    const next = harness({}, h.storage); await next.scan(); next.draft(); assert.equal(next.controller.prepare(), null)
    assert.equal(next.controller.getState().canQuery, true)
    const query = next.controller.query(), check = await next.connect(); assert.equal(next.packet(check).kind, 'status')
    next.reply('unknown'); await query
    assert.equal(next.controller.getState().canConfigure, false)
  },
  async 'offline encrypted receipt closes original request after AP disconnect without another TCP configure'() {
    const h = harness(); await h.scan(); h.draft()
    const task = h.controller.confirm(h.controller.prepare()), socket = await h.connect()
    socket.listeners.error({ errMsg: 'AP changed to station' }); await task
    assert.equal(h.controller.getState().status, 'unknown')
    assert.equal(h.controller.getState().canScanReceipt, true)
    const count = h.sockets.length, qr = h.receiptQR('connected')
    const scanned = h.controller.scanReceipt(); h.scans.at(-1).success({ scanType: 'QR_CODE', result: qr }); await scanned
    assert.equal(h.controller.getState().status, 'connected')
    assert.equal(h.storage.get(STORAGE), ''); assert.equal(h.controller.getState().canScanReceipt, false)
    assert.equal(h.sockets.length, count)
    assert.equal(h.sockets.filter(s => s.packets.length && h.packet(s).kind === 'configure').length, 1)
    assert.deepEqual(h.logs, [])
  },
  async 'receipt scanning needs original invitation and nonterminal receipts never clear pending marker'() {
    const h = harness(); await h.scan(); h.draft()
    const task = h.controller.confirm(h.controller.prepare()); await h.connect(); h.reply('accepted'); await task
    const original = plain(h.storage.get(STORAGE))
    h.controller.hide()
    const next = harness({}, h.storage), count = next.scans.length
    await next.controller.scanReceipt()
    assert.equal(next.scans.length, count); assert.equal(next.controller.getState().canScanReceipt, false)
    await next.scan()
    for (const status of ['accepted', 'connecting', 'unknown']) {
      const scanned = next.controller.scanReceipt(); next.scans.at(-1).success({ scanType: 'QR_CODE', result: next.receiptQR(status) }); await scanned
      assert.deepEqual(next.storage.get(STORAGE), original)
      assert.equal(next.controller.getState().status, status); assert.equal(next.controller.getState().canConfigure, false)
    }
    assert.equal(next.sockets.length, 0)
  },
  async 'forged other-request other-window and reflected receipt QR cannot clear the original marker'() {
    const h = harness(); await h.scan(); h.draft()
    const task = h.controller.confirm(h.controller.prepare()); await h.connect(); h.reply('accepted'); await task
    const original = plain(h.storage.get(STORAGE)), count = h.sockets.length
    const invalid = [h.receiptQR('connected', { requestId: 'another' }), h.receiptQR('connected', { sessionId: 'a'.repeat(32) }),
      h.receiptQR('connected', { kind: 'status' }), h.receiptQR('connected', { ciphertext: '00'.repeat(32) }), QR, 'https://evil/receipt']
    for (const qr of invalid) {
      const scanned = h.controller.scanReceipt(); h.scans.at(-1).success({ scanType: 'QR_CODE', result: qr }); await scanned
      assert.deepEqual(h.storage.get(STORAGE), original)
      assert.equal(h.controller.getState().status, 'unknown')
      assert.equal(h.controller.getState().canConfigure, false)
    }
    assert.equal(h.sockets.length, count)
  },
  async 'late old receipt camera callback cannot clear newer visit marker or change its pending scanner'() {
    const h = harness(); await h.scan(); h.draft()
    const task = h.controller.confirm(h.controller.prepare()); await h.connect(); h.reply('accepted'); await task
    const oldTask = h.controller.scanReceipt(), oldScan = h.scans.at(-1), qr = h.receiptQR('connected')
    h.controller.hide(); await oldTask
    h.controller.show(value => h.states.push(plain(value))); await h.scan()
    const currentTask = h.controller.scanReceipt(), currentScan = h.scans.at(-1), count = h.states.length
    oldScan.success({ scanType: 'QR_CODE', result: qr })
    assert.ok(h.storage.get(STORAGE)); assert.equal(h.states.length, count); assert.equal(h.controller.getState().busy, true)
    currentScan.success({ scanType: 'QR_CODE', result: qr }); await currentTask
    assert.equal(h.storage.get(STORAGE), ''); assert.equal(h.sockets.length, 1)
  },
  async 'valid offline receipt never deletes a different marker changed concurrently in storage'() {
    const h = harness(); await h.scan(); h.draft()
    const task = h.controller.confirm(h.controller.prepare()); await h.connect(); h.reply('accepted'); await task
    const qr = h.receiptQR('connected'), scanned = h.controller.scanReceipt()
    const foreign = { ...plain(h.storage.get(STORAGE)), requestId: 'different-marker' }; h.storage.set(STORAGE, foreign)
    h.scans.at(-1).success({ scanType: 'QR_CODE', result: qr }); await scanned
    assert.deepEqual(h.storage.get(STORAGE), foreign); assert.match(h.controller.getState().error, /未决标记未能安全解除/)
    assert.equal(h.controller.getState().canConfigure, false)
  },
  async 'failed persistent marker or secure randomness prevents any connection'() {
    for (const options of [{ storageFailure: true }, { randomFailure: true }]) {
      const h = harness(options); await h.scan(); h.draft()
      await h.controller.confirm(h.controller.prepare())
      assert.equal(h.sockets.length, 0); assert.match(h.controller.getState().error, /未发送|不发送/)
    }
  },
  async 'a native failure before write is not sent while a forged acknowledgement remains unknown'() {
    const h = harness({ socketFailure: true }); await h.scan(); h.draft(); await h.controller.confirm(h.controller.prepare())
    assert.equal(h.controller.getState().status, 'not_sent'); assert.equal(h.storage.get(STORAGE), '')
    const next = harness(); await next.scan(); next.draft(); const task = next.controller.confirm(next.controller.prepare()); await next.connect()
    next.reply('connected', undefined, { requestId: 'forged' }); await task
    assert.equal(next.controller.getState().status, 'unknown'); assert.ok(next.storage.get(STORAGE))
  },
  async 'synchronous native connect and write errors settle honestly instead of escaping callback'() {
    for (const options of [{ connectThrow: true }, { writeThrow: true }]) {
      const h = harness(options); await h.scan(); h.draft()
      const task = h.controller.confirm(h.controller.prepare()), socket = await h.connect()
      await task
      assert.equal(h.controller.getState().status, options.writeThrow ? 'unknown' : 'not_sent')
      assert.equal(Boolean(h.storage.get(STORAGE)), Boolean(options.writeThrow)); assert.equal(socket.closed, true)
    }
  },
  async 'hiding confirmation cancels later intent and hiding inflight preserves unknown marker without late UI'() {
    const h = harness(); await h.scan(); h.draft(); const token = h.controller.prepare(); h.controller.hide(); await h.controller.confirm(token); assert.equal(h.sockets.length, 0)
    h.controller.show(value => h.states.push(plain(value))); await h.scan(); h.draft()
    const task = h.controller.confirm(h.controller.prepare()); const socket = await h.connect(); h.controller.hide(); const count = h.states.length
    socket.listeners.error(); await task
    assert.equal(h.states.length, count); assert.ok(h.storage.get(STORAGE)); assert.equal(socket.closed, true)
  },
  async 'editing draft revokes confirmation and devtools never opens camera or network'() {
    const h = harness(); await h.scan(); h.draft(); const token = h.controller.prepare()
    h.controller.setNetwork('另一个网络', 'another-password'); await h.controller.confirm(token); assert.equal(h.sockets.length, 0)
    const devtools = harness({ platform: 'devtools' }); await devtools.scan(); assert.equal(devtools.scans.length, 0); assert.equal(devtools.sockets.length, 0); assert.match(devtools.controller.getState().error, /安卓/)
  }
}
;(async () => {
  let failed = 0
  for (const [name, run] of Object.entries(cases)) {
    try { await run(); console.log('PASS ' + name) } catch (error) { failed++; console.error('FAIL ' + name + ': ' + error.stack) }
  }
  console.log('Live provisioning smoke: ' + (Object.keys(cases).length - failed) + '/' + Object.keys(cases).length + ' (offline actual TS/noble/Node AESGCM)')
  if (failed) process.exitCode = 1
})().catch(error => { console.error(error); process.exitCode = 1 })
