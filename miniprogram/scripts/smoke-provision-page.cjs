// Actual provision TS page + controller + vendored AES. All wx I/O is offline.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), crypto = require('node:crypto'), ts = require('typescript')
const root = path.resolve(__dirname, '..'), KEY = '22'.repeat(32), DEVICE = 'dev_' + '1'.repeat(32), SESSION = '3'.repeat(32)
const QR = 'AICAT-PROVISION:1:' + DEVICE + ':' + SESSION + ':' + KEY, PASSWORD = 'synthetic-password ', SSID = '家庭测试网 '
const BSSID = '12:34:56:78:9A:BC'
const ARCHIVE_CHECKS = ['window-ended', 'usb-checked', 'accept-unknown']
const ORIGINAL = { v: 1, deviceId: DEVICE, sessionId: SESSION, requestId: 'wifi_archive_original' }
const ORIGINAL_IDS = { deviceId: DEVICE, sessionId: SESSION, requestId: ORIGINAL.requestId }
const plain = value => JSON.parse(JSON.stringify(value)), flush = async () => { for (let i = 0; i < 60; i++) await Promise.resolve() }
const input = value => ({ detail: { value } })
function harness(options = {}, storage = new Map()) {
  const scans = [], nativeScans = [], modals = [], sockets = [], writes = [], logs = [], wifiStarts = [], wifiReads = [], storageWrites = [], timers = new Map(), modules = new Map(), events = [], navigations = []
  let id = 0, entropy = 0, handoffTakes = 0, handoffDisposals = 0
  class HarnessDate extends Date { static now() { return typeof options.now === 'number' ? options.now : Date.now() } }
  const wx = { getDeviceInfo: () => ({ platform: options.platform || 'android' }),
    base64ToArrayBuffer(value) { const bytes = Buffer.from(value, 'base64'); return bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) },
    arrayBufferToBase64(value) { return Buffer.from(value).toString('base64') },
    scanCode(value) { scans.push(value); nativeScans.push(value) }, request() { throw Error('No HTTP allowed') },
    startWifi(value) {
      wifiStarts.push(value)
      if (options.deferStartWifi) return
      if (options.startWifiFail) return value.fail({ errCode: options.startWifiFail, errMsg: 'PRIVATE_PAGE_WIFI_INIT_ERROR' })
      value.success({ errMsg: 'startWifi:ok' })
    },
    getConnectedWifi(value) {
      wifiReads.push(value); assert.equal(value.partialInfo, false)
      if (options.deferGetConnectedWifi) return
      if (options.readWifiFail) return value.fail({ errCode: options.readWifiFail, errMsg: 'PRIVATE_PAGE_WIFI_READ_ERROR' })
      const current = Object.prototype.hasOwnProperty.call(options, 'bssid') ? options.bssid : BSSID
      const hotspot = Object.prototype.hasOwnProperty.call(options, 'wifiSSID') ? options.wifiSSID : 'AI-CAT-ABC123'
      value.success({ wifi: { BSSID: current, SSID: hotspot }, errMsg: 'getConnectedWifi:ok' })
    },
    connectWifi() { throw Error('No automatic phone network change allowed') },
    getStorageSync(key) { return storage.has(key) ? plain(storage.get(key)) : '' },
    setStorageSync(key, value) { storageWrites.push([key, plain(value)]); storage.set(key, plain(value)) },
    getUserCryptoManager: () => ({ getRandomValues(value) { const bytes = new Uint8Array(value.length).fill(++entropy); value.success({ randomValues: bytes.buffer }) } }),
    showModal(value) {
      if (options.throwModal) throw Error('raw-private-modal-error')
      if ([value.confirmText, value.cancelText].some(text => text !== undefined && Array.from(text).length > 4)) { value.fail({ errMsg: 'native max four characters' }); return }
      modals.push(value)
    }, stopPullDownRefresh() {}, navigateTo(value) { navigations.push(plain(value)) },
    createTCPSocket() {
      const listeners = {}, socket = { listeners, packets: [], closed: false,
        onBindWifi(fn) { listeners.bind = fn }, onConnect(fn) { listeners.connect = fn }, onMessage(fn) { listeners.message = fn }, onError(fn) { listeners.error = fn }, onClose(fn) { listeners.close = fn },
        bindWifi(value) {
          const expected = Object.prototype.hasOwnProperty.call(options, 'bssid') ? options.bssid : BSSID
          assert.deepEqual(plain(value), { BSSID: expected }, 'native binding requires the current Wi-Fi BSSID')
          socket.boundWifi = plain(value)
        }, connect(value) { socket.destination = value }, write(value) { socket.packets.push(Buffer.from(value)) },
        close() { socket.closed = true; if (listeners.close) listeners.close() } }
      sockets.push(socket); return socket
    } }
  const context = vm.createContext({ wx, Uint8Array, ArrayBuffer, DataView, BigInt, Date: HarnessDate,
    setTimeout(fn) { const key = ++id; timers.set(key, fn); return key }, clearTimeout(key) { timers.delete(key) },
    console: Object.fromEntries(['log', 'warn', 'error', 'info', 'debug'].map(name => [name, (...values) => logs.push(values)])) })
  function load(relative, parent = root) {
    let filename = path.resolve(parent, relative); if (!filename.endsWith('.ts')) filename += '.ts'
    if (modules.has(filename)) return modules.get(filename)
    const record = { exports: {}, definition: null }; modules.set(filename, record)
    const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2019, module: ts.ModuleKind.CommonJS } }).outputText
    new vm.Script('(function(require,module,exports,Page,Component){' + output + '\n})', { filename }).runInContext(context)(name => load(name, path.dirname(filename)).exports, record, record.exports, definition => { record.definition = definition }, definition => { record.definition = definition })
    return record
  }
  if (options.inline) {
    const local = load('services/local-network').exports, take = local.takeLocalProvisionHandoff
    local.takeLocalProvisionHandoff = () => {
      handoffTakes++
      const actual = take()
      if (!actual) return null
      const delivered = actual.deliver
      return { ...actual,
        deliver(value) { return options.declineHandoff ? false : delivered(value) },
        dispose() { handoffDisposals++; actual.dispose() }
      }
    }
  }
  const definition = load(options.inline ? 'components/provision-flow/index' : 'pages/provision/index').definition
  const page = { ...(options.inline ? definition.methods : definition), data: { ...plain(definition.data), ...(options.inline ? { revision: 1 } : {}) },
    triggerEvent(name, detail) { events.push([name, plain(detail || {})]) } }
  if (options.inline) {
    page.onShow = async () => { if (!page.attachedFlow) definition.lifetimes.attached.call(page); else definition.pageLifetimes.show.call(page); await flush() }
    page.onHide = () => definition.pageLifetimes.hide.call(page)
    page.onUnload = () => definition.lifetimes.detached.call(page)
  }
  const seenCameras = new Set()
  page.setData = patch => {
    Object.assign(page.data, plain(patch)); writes.push(plain(patch))
    for (const key of patch.cameraKeys || []) {
      if (seenCameras.has(key)) continue
      seenCameras.add(key)
      const event = detail => ({ currentTarget: { dataset: { scanId: key } }, detail })
      scans.push({ key,
        success(value) { page.cameraDecoded(event({ type: value.scanType, result: value.result })) },
        fail() { page.cameraError(event({ errMsg: 'private-camera-error' })) },
        cancel() { page.cancelEmbeddedScan(event({})) },
        stop() { page.cameraStopped(event({})) }
      })
    }
  }
  const h = { page, scans, nativeScans, modals, sockets, writes, logs, storage, storageWrites, options, timers, wifiStarts, wifiReads, events, navigations,
    module(relative) { return load(relative).exports }, wx,
    takes: () => handoffTakes, disposals: () => handoffDisposals,
    async revision(value) { const before = page.data.revision; page.data.revision = value; definition.properties.revision.observer.call(page, value, before); await flush() },
    async boot() { await page.onShow(); return page },
    async scan(qr = QR) { const task = page.scanInvitation(); await flush(); if (scans.length) scans.at(-1).success({ result: qr, scanType: 'QR_CODE' }); await task },
    draft() { page.inputSSID(input(SSID)); page.inputPassword(input(PASSWORD)); page.hotspotChanged(input(['hotspot'])) },
    async open() { const task = page.configure(); await flush(); assert.ok(modals.at(-1)); return { task, modal: modals.at(-1) } },
    async connect() { await flush(); const socket = sockets.at(-1); assert.ok(socket); socket.listeners.bind({ errMsg: 'bindWifi:ok' }); socket.listeners.connect(); return socket },
    mode(value) { page.transportChanged(input(value)) },
    archiveChecks(value = ARCHIVE_CHECKS) { page.archiveAcksChanged(input(value)) },
    async openArchive() { const task = page.archiveRequest(); await flush(); assert.ok(modals.at(-1)); return { task, modal: modals.at(-1) } },
    async probe() {
      const task = page.probeCompatibility(); await flush(); const socket = sockets.at(-1); assert.ok(socket)
      assert.deepEqual(plain(socket.destination), { address: '10.47.0.1', port: 8766, timeout: 5 })
      socket.listeners.connect(); await task; return socket
    },
    packet(socket = sockets.at(-1)) { const raw = socket.packets.at(-1); assert.ok(raw); return JSON.parse(raw.subarray(4).toString()) },
    decrypt(socket = sockets.at(-1)) { const request = h.packet(socket), raw = Buffer.from(request.ciphertext, 'hex'), decipher = crypto.createDecipheriv('aes-256-gcm', Buffer.from(KEY, 'hex'), Buffer.from(request.nonce, 'hex')); decipher.setAAD(Buffer.from(JSON.stringify(['aicat-provision-v1', 'request', DEVICE, SESSION, request.requestId, request.kind]))); decipher.setAuthTag(raw.subarray(-16)); return JSON.parse(Buffer.concat([decipher.update(raw.subarray(0, -16)), decipher.final()]).toString()) },
    receiptQR(status, patch = {}) {
      const channel = [...sockets].reverse().find(item => item.packets.length)
      const stored = storage.get('aiCatProvisionPendingV1')
      const request = channel ? h.packet(channel) : stored && (stored.pending || stored)
      assert.ok(request && request.requestId)
      const nonce = Buffer.alloc(12, 8); nonce[0] = 1
      const cipher = crypto.createCipheriv('aes-256-gcm', Buffer.from(KEY, 'hex'), nonce)
      cipher.setAAD(Buffer.from(JSON.stringify(['aicat-provision-v1', 'response', request.deviceId, request.sessionId, request.requestId, 'configure'])))
      const ciphertext = Buffer.concat([cipher.update(JSON.stringify({ status })), cipher.final(), cipher.getAuthTag()])
      const value = { v: 1, deviceId: request.deviceId, sessionId: request.sessionId, requestId: request.requestId, kind: 'configure', nonce: nonce.toString('hex'), ciphertext: ciphertext.toString('hex'), ...patch }
      return 'AICAT-PROVISION-RECEIPT:1:' + Buffer.from(JSON.stringify(value)).toString('base64url')
    },
    reply(status, socket = sockets.at(-1)) {
      const request = h.packet(socket), nonce = Buffer.alloc(12, 7); nonce[0] = 1
      const cipher = crypto.createCipheriv('aes-256-gcm', Buffer.from(KEY, 'hex'), nonce)
      cipher.setAAD(Buffer.from(JSON.stringify(['aicat-provision-v1', 'response', request.deviceId, request.sessionId, request.requestId, request.kind])))
      const ciphertext = Buffer.concat([cipher.update(JSON.stringify({ status })), cipher.final(), cipher.getAuthTag()])
      const raw = Buffer.from(JSON.stringify({ v: 1, deviceId: request.deviceId, sessionId: request.sessionId, requestId: request.requestId, kind: request.kind, nonce: nonce.toString('hex'), ciphertext: ciphertext.toString('hex') })), frame = Buffer.alloc(raw.length + 4)
      frame.writeUInt32BE(raw.length); raw.copy(frame, 4)
      socket.listeners.message({ remoteInfo: { address: '10.47.0.1', port: 8766 }, message: frame.buffer.slice(frame.byteOffset, frame.byteOffset + frame.byteLength) })
    },
    visible() { return JSON.stringify([page.data, writes, logs, storageWrites, [...storage], events, navigations]) }
  }
  return h
}
async function archiveHarness(options = {}) {
  const h = harness(options, new Map([['aiCatProvisionPendingV1', plain(ORIGINAL)]]))
  await h.boot(); await h.scan()
  const task = h.page.scanFinalReceipt(); await flush()
  h.scans.at(-1).success({ scanType: 'QR_CODE', result: h.receiptQR('unknown') }); await task
  return h
}
const cases = {
  async 'new invitation receipt query and transport actions discard the previous operator declarations before another review'() {
    const h = await archiveHarness(); h.archiveChecks(); assert.equal(h.page.data.canArchive, true)
    let task = h.page.scanInvitation(); await flush(); assert.deepEqual(plain(h.page.data.archiveAcks), [])
    h.scans.at(-1).success({ scanType: 'QR_CODE', result: QR }); await task
    task = h.page.scanFinalReceipt(); await flush(); h.scans.at(-1).success({ scanType: 'QR_CODE', result: h.receiptQR('unknown') }); await task
    h.archiveChecks(); assert.equal(h.page.data.canArchive, true)
    task = h.page.scanFinalReceipt(); await flush(); assert.deepEqual(plain(h.page.data.archiveAcks), [])
    h.scans.at(-1).success({ scanType: 'QR_CODE', result: h.receiptQR('unknown') }); await task
    assert.equal(h.page.data.archiveReady, true); assert.equal(h.page.data.canArchive, false)
    h.archiveChecks(); h.mode('direct'); assert.deepEqual(plain(h.page.data.archiveAcks), [])
    h.mode('bound'); task = h.page.scanFinalReceipt(); await flush()
    h.scans.at(-1).success({ scanType: 'QR_CODE', result: h.receiptQR('unknown') }); await task
    h.page.hotspotChanged(input(['hotspot'])); h.archiveChecks(); assert.equal(h.page.data.canArchive, true)
    const queried = h.page.queryOriginal(); await flush(); assert.deepEqual(plain(h.page.data.archiveAcks), [])
    const socket = await h.connect(); h.reply('unknown', socket); await queried
    assert.equal(h.page.data.archiveReady, false); assert.equal(h.page.data.canArchive, false)
    assert.equal(h.storageWrites.length, 0); assert.deepEqual(h.storage.get('aiCatProvisionPendingV1'), ORIGINAL); h.page.onHide()
  },
  async 'operator archive requires this visit original invitation final unknown receipt and all three explicit declarations without a hotspot'() {
    const h = harness({}, new Map([['aiCatProvisionPendingV1', plain(ORIGINAL)]])); await h.boot()
    assert.equal(h.page.data.archiveAvailable, true); assert.deepEqual(plain(h.page.data.reviewIdentity), ORIGINAL_IDS)
    h.archiveChecks(); assert.equal(h.page.data.canArchive, false); await h.page.archiveRequest(); assert.equal(h.modals.length, 0)
    await h.scan(); assert.equal(h.page.data.archiveReady, false); assert.equal(h.page.data.canArchive, false)
    let task = h.page.scanFinalReceipt(); await flush(); h.scans.at(-1).success({ scanType: 'QR_CODE', result: h.receiptQR('accepted') }); await task
    assert.equal(h.page.data.archiveReady, false); assert.equal(h.page.data.canArchive, false)
    task = h.page.scanFinalReceipt(); await flush(); h.scans.at(-1).success({ scanType: 'QR_CODE', result: h.receiptQR('unknown') }); await task
    assert.equal(h.page.data.archiveReady, true)
    h.archiveChecks(['window-ended', 'usb-checked']); assert.equal(h.page.data.canArchive, false)
    h.archiveChecks([...ARCHIVE_CHECKS, 'PRIVATE_BAD_ACK']); assert.equal(h.page.data.canArchive, false)
    h.archiveChecks(['accept-unknown', 'window-ended', 'usb-checked']); assert.equal(h.page.data.canArchive, true)
    assert.equal(h.page.data.hotspotReady, false); assert.equal(h.sockets.length, 0); assert.equal(h.wifiReads.length, 0); assert.equal(h.storageWrites.length, 0)
    assert.doesNotMatch(h.visible(), /PRIVATE_BAD_ACK|AICAT-PROVISION|2222222222222222/); h.page.onHide()
  },
  async 'one explicit operator archive blocks other operations and records unknown history once without a success state or a network packet'() {
    const h = await archiveHarness(); h.archiveChecks(); const opened = await h.openArchive()
    assert.match(opened.modal.content, /人工声明/); assert.match(opened.modal.content, /未知/); assert.match(opened.modal.content, /30\s*秒/)
    assert.equal(h.page.data.confirming, true); assert.equal(h.page.data.canArchive, false)
    h.mode('direct'); h.page.hotspotChanged(input(['hotspot'])); h.archiveChecks([])
    await h.page.scanInvitation(); await h.page.scanFinalReceipt(); await h.page.queryOriginal(); await h.page.probeCompatibility(); await h.page.archiveRequest()
    assert.equal(h.page.data.transportMode, 'bound'); assert.equal(h.page.data.hotspotReady, false)
    assert.deepEqual(plain(h.page.data.archiveAcks), ARCHIVE_CHECKS); assert.equal(h.modals.length, 1); assert.equal(h.storageWrites.length, 0)
    const confirmed = opened.modal.success({ confirm: true }), duplicate = opened.modal.success({ confirm: true })
    opened.modal.fail({ errMsg: 'PRIVATE_LATE_ARCHIVE_MODAL' }); await confirmed; await duplicate; await opened.task
    assert.equal(h.storageWrites.length, 1); assert.equal(h.page.data.archiveCount, 1); assert.equal(h.page.data.pending, false)
    assert.equal(h.page.data.status, 'unknown'); assert.equal(h.page.data.phaseLabel, '原请求已归档，结果仍未知')
    assert.deepEqual(plain(h.page.data.lastArchiveIdentity), ORIGINAL_IDS); assert.equal(h.page.data.reviewIdentity, null)
    assert.equal(h.page.privatePassword, ''); assert.equal(h.page.ssidDraft, ''); assert.equal(h.page.data.ssid, '')
    assert.equal(h.page.data.hotspotReady, false); assert.equal(h.page.data.scanned, false); assert.deepEqual(plain(h.page.data.archiveAcks), [])
    assert.equal(h.sockets.length, 0); assert.equal(h.wifiReads.length, 0)
    const record = h.storage.get('aiCatProvisionPendingV1'); assert.equal(record.pending, null); assert.equal(record.archives.length, 1)
    assert.deepEqual(record.archives[0].original, ORIGINAL); assert.equal(record.archives[0].status, 'unknown')
    assert.doesNotMatch(h.visible(), /PRIVATE_LATE_ARCHIVE_MODAL|AICAT-PROVISION|2222222222222222|synthetic-password/); h.page.onHide()
    await h.page.onShow(); assert.equal(h.page.data.archiveCount, 1); assert.deepEqual(plain(h.page.data.lastArchiveIdentity), ORIGINAL_IDS); h.page.onHide()
  },
  async 'operator archive cancellation and native modal failures leave the pending request and history unchanged'() {
    for (const kind of ['cancel', 'fail', 'throw']) {
      const h = await archiveHarness({ throwModal: kind === 'throw' }); h.archiveChecks()
      const task = h.page.archiveRequest(); await flush()
      if (kind === 'cancel') await h.modals.at(-1).success({ confirm: false })
      if (kind === 'fail') h.modals.at(-1).fail({ errMsg: 'PRIVATE_ARCHIVE_NATIVE_FAILURE' })
      await task
      assert.equal(h.page.data.confirming, false); assert.equal(h.page.data.pending, true); assert.equal(h.page.data.archiveCount, 0)
      assert.deepEqual(h.storage.get('aiCatProvisionPendingV1'), ORIGINAL); assert.equal(h.storageWrites.length, 0)
      assert.equal(h.sockets.length, 0); assert.doesNotMatch(h.visible(), /PRIVATE_ARCHIVE_NATIVE_FAILURE|raw-private-modal-error|synthetic-password/); h.page.onHide()
    }
  },
  async 'hiding revokes archive declarations and old callbacks while an expired thirty second intent cannot write history'() {
    const h = await archiveHarness(); h.archiveChecks(); const opened = await h.openArchive()
    h.page.onHide(); const count = h.writes.length
    await opened.modal.success({ confirm: true }); opened.modal.fail({ errMsg: 'PRIVATE_OLD_ARCHIVE' }); await opened.task
    assert.equal(h.writes.length, count); assert.equal(h.storageWrites.length, 0); assert.deepEqual(plain(h.page.data.archiveAcks), [])
    await h.page.onShow(); assert.equal(h.page.data.archiveReady, false); assert.equal(h.page.data.canArchive, false)
    assert.equal(h.page.data.pending, true); assert.deepEqual(h.storage.get('aiCatProvisionPendingV1'), ORIGINAL); h.page.onHide()
    const expired = await archiveHarness(); expired.archiveChecks(); const intent = await expired.openArchive()
    expired.options.now = Date.now() + 31001; await intent.modal.success({ confirm: true }); await intent.task
    assert.equal(expired.storageWrites.length, 0); assert.equal(expired.page.data.pending, true); assert.equal(expired.page.data.archiveCount, 0)
    assert.notEqual(expired.page.data.phaseLabel, '原请求已归档，结果仍未知'); assert.doesNotMatch(expired.visible(), /PRIVATE_OLD_ARCHIVE/); expired.page.onHide()
  },
  async 'archive API rejection and exceptions show fixed feedback rather than raw details and never manufacture a successful archive'() {
    for (const reject of [false, true]) {
      const h = await archiveHarness(); h.archiveChecks()
      h.page.provision.confirmArchive = () => { if (reject) throw Error('PRIVATE_ARCHIVE_BACKEND_ERROR'); return false }
      const opened = await h.openArchive(); await opened.modal.success({ confirm: true }); await opened.task
      assert.equal(h.page.data.confirming, false); assert.equal(h.page.data.pending, true); assert.equal(h.page.data.archiveCount, 0)
      assert.equal(h.storageWrites.length, 0); assert.match(h.page.data.notice, /归档.*未完成|归档.*失败/)
      assert.doesNotMatch(h.visible(), /PRIVATE_ARCHIVE_BACKEND_ERROR/); h.page.onHide()
    }
  },
  async 'archive rendering only admits public original identifiers and older controllers or non Android previews fail closed'() {
    const h = await archiveHarness(), state = h.page.provision.getState()
    h.page.render({ ...state, archiveCount: 1, lastArchive: { original: { ...ORIGINAL, private: 'PRIVATE_ARCHIVE_DETAILS' }, status: 'unknown', resolution: 'operator_archived', checks: ['PRIVATE_ARCHIVE_ACK'], private: 'PRIVATE_ARCHIVE_RAW' } })
    assert.deepEqual(plain(h.page.data.lastArchiveIdentity), ORIGINAL_IDS); assert.equal(h.page.data.lastArchive, undefined)
    assert.doesNotMatch(h.visible(), /PRIVATE_ARCHIVE_DETAILS|PRIVATE_ARCHIVE_ACK|PRIVATE_ARCHIVE_RAW/)
    h.archiveChecks(); h.page.render({ ...state, archiveCount: 20 })
    assert.equal(h.page.data.archiveLimitReached, true); assert.equal(h.page.data.canArchive, false)
    await h.page.archiveRequest(); assert.equal(h.storageWrites.length, 0); assert.equal(h.modals.length, 0)
    assert.deepEqual(h.storage.get('aiCatProvisionPendingV1'), ORIGINAL)
    delete h.page.provision.prepareArchive; delete h.page.provision.cancelArchive; delete h.page.provision.confirmArchive
    h.page.render(state); h.archiveChecks(); await h.page.archiveRequest()
    assert.equal(h.page.data.archiveAvailable, false); assert.equal(h.page.data.canArchive, false); assert.equal(h.modals.length, 0); assert.equal(h.storageWrites.length, 0); h.page.onHide()
    for (const platform of ['devtools', 'ios']) {
      const preview = harness({ platform }, new Map([['aiCatProvisionPendingV1', plain(ORIGINAL)]])); await preview.boot()
      preview.archiveChecks(); await preview.page.archiveRequest(); assert.equal(preview.modals.length, 0); assert.equal(preview.storageWrites.length, 0); preview.page.onHide()
    }
  },
  async 'an archived window cannot be reused and a new window still requires explicit configuration confirmation'() {
    const h = await archiveHarness(); h.archiveChecks(); const archive = await h.openArchive(); await archive.modal.success({ confirm: true }); await archive.task
    await h.scan(); h.draft(); await h.page.configure(); assert.equal(h.page.data.canConfigure, false); assert.equal(h.modals.length, 1); assert.equal(h.sockets.length, 0)
    const nextSession = '4'.repeat(32); await h.scan(QR.replace(':' + SESSION + ':', ':' + nextSession + ':')); h.draft()
    assert.equal(h.page.data.canConfigure, true); const opened = await h.open(); assert.equal(h.sockets.length, 0); assert.equal(h.storageWrites.length, 1)
    const accepted = opened.modal.success({ confirm: true }), socket = await h.connect()
    assert.equal(h.packet(socket).sessionId, nextSession); assert.equal(h.packet(socket).kind, 'configure')
    h.reply('accepted', socket); await accepted; await opened.task
    assert.equal(h.page.data.archiveCount, 1); assert.equal(h.page.data.status, 'accepted'); assert.equal(socket.packets.length, 1); h.page.onHide()
  },
  async 'standard binding is the default and selecting compatibility preserves the draft without probing or sending automatically'() {
    const h = harness(); await h.boot(); assert.equal(h.page.data.transportMode, 'bound'); assert.equal(h.page.data.transportAvailable, true)
    await h.scan(); h.draft(); h.mode('direct')
    assert.equal(h.page.data.transportMode, 'direct'); assert.equal(h.page.data.compatibilityReady, false)
    assert.equal(h.page.data.canConfigure, false); assert.equal(h.page.data.canProbe, true)
    assert.equal(h.page.privatePassword, PASSWORD); assert.equal(h.page.ssidDraft, SSID)
    assert.equal(h.sockets.length, 0); assert.equal(h.wifiReads.length, 0); assert.equal(h.storageWrites.length, 0)
    h.mode('PRIVATE_INVALID_TRANSPORT'); assert.equal(h.page.data.transportMode, 'direct')
    assert.doesNotMatch(h.visible(), /PRIVATE_INVALID_TRANSPORT|synthetic-password|AICAT-PROVISION/); h.page.onHide()
  },
  async 'compatibility probe requires manual hotspot acknowledgement and tests only the fixed port with zero writes'() {
    const h = harness(); await h.boot(); await h.scan()
    h.page.inputSSID(input(SSID)); h.page.inputPassword(input(PASSWORD)); h.mode('direct')
    assert.equal(h.page.data.canProbe, false); await h.page.probeCompatibility(); assert.equal(h.sockets.length, 0)
    h.page.hotspotChanged(input(['hotspot'])); assert.equal(h.page.data.canProbe, true)
    const socket = await h.probe()
    assert.equal(socket.boundWifi, undefined); assert.equal(socket.packets.length, 0); assert.equal(socket.closed, true)
    assert.equal(h.storageWrites.length, 0); assert.equal(h.page.data.probeStatus, 'reachable')
    assert.equal(h.page.data.compatibilityReady, true); assert.equal(h.page.data.canConfigure, true)
    assert.notEqual(h.page.data.status, 'connected')
    assert.doesNotMatch(h.visible(), /12:34:56:78:9A:BC|AI-CAT-ABC123|synthetic-password/); h.page.onHide()
  },
  async 'a compatibility send discloses system routing in the native modal and encrypts only after a new confirmation'() {
    const h = harness(); await h.boot(); await h.scan(); h.draft(); h.mode('direct'); await h.probe()
    const opened = await h.open()
    assert.match(opened.modal.content, /本地直连兼容/); assert.match(opened.modal.content, /系统.*选路|系统.*路由/)
    assert.match(opened.modal.content, /不.*强制.*Wi-Fi|不.*绑定.*Wi-Fi/)
    assert.equal(h.sockets.length, 1); assert.equal(h.storageWrites.length, 0)
    const accepted = opened.modal.success({ confirm: true }); await flush(); const socket = h.sockets.at(-1)
    assert.equal(h.sockets.length, 2); assert.equal(socket.boundWifi, undefined); socket.listeners.connect(); await flush()
    assert.deepEqual(h.decrypt(socket), { ssid: SSID, password: PASSWORD, security: 'wpa2-personal' })
    h.reply('accepted', socket); await accepted; await opened.task
    assert.equal(h.page.data.status, 'accepted'); assert.equal(socket.packets.length, 1)
    assert.doesNotMatch(h.visible(), /12:34:56:78:9A:BC|AI-CAT-ABC123|synthetic-password/); h.page.onHide()
  },
  async 'changing transport cancels the old native confirmation and expired proof cannot authorize a new send'() {
    const h = harness(); await h.boot(); await h.scan(); h.draft(); const opened = await h.open()
    assert.match(opened.modal.content, /标准.*绑定/); h.mode('direct')
    assert.equal(h.page.data.confirming, false); assert.match(h.page.data.notice, /旧.*确认|确认.*撤销|重新.*确认/)
    await opened.modal.success({ confirm: true }); await opened.task; assert.equal(h.sockets.length, 0)
    assert.equal(h.page.privatePassword, PASSWORD); await h.probe()
    for (const timeout of [...h.timers.values()]) timeout()
    await flush(); assert.equal(h.page.data.probeStatus, 'expired'); assert.equal(h.page.data.compatibilityReady, false)
    assert.equal(h.page.data.canConfigure, false); await h.page.configure(); assert.equal(h.modals.length, 1)
    assert.equal(h.storageWrites.length, 0); h.page.onHide()
  },
  async 'compatibility probing cannot clear unknown or unlock another configuration and query still uses the original request'() {
    const h = harness(); await h.boot(); await h.scan(); h.draft(); const opened = await h.open()
    const accepted = opened.modal.success({ confirm: true }), first = await h.connect()
    first.listeners.error({ errMsg: 'PRIVATE_UNKNOWN_ROUTE' }); await accepted; await opened.task
    const original = plain(h.storage.get('aiCatProvisionPendingV1')), writes = h.storageWrites.length
    h.mode('direct'); assert.equal(h.page.data.pending, true); assert.equal(h.page.data.canConfigure, false); assert.equal(h.page.data.canQuery, false)
    await h.page.queryOriginal(); assert.equal(h.sockets.length, 1)
    await h.probe(); assert.equal(h.storageWrites.length, writes); assert.deepEqual(h.storage.get('aiCatProvisionPendingV1'), original)
    assert.equal(h.page.data.canConfigure, false); assert.equal(h.page.data.canQuery, true)
    const task = h.page.queryOriginal(); await flush(); const socket = h.sockets.at(-1); socket.listeners.connect(); await flush()
    assert.equal(h.packet(socket).kind, 'status'); assert.equal(h.packet(socket).requestId, original.requestId)
    h.reply('connected', socket); await task; assert.equal(h.page.data.status, 'connected')
    assert.doesNotMatch(h.visible(), /PRIVATE_UNKNOWN_ROUTE|synthetic-password|12:34:56:78:9A:BC/); h.page.onHide()
  },
  async 'hiding cancels an unfinished probe and the next visit restores binding with no stale proof or private inputs'() {
    const h = harness(); await h.boot(); await h.scan(); h.draft(); h.mode('direct')
    const task = h.page.probeCompatibility(); await flush(); const socket = h.sockets.at(-1); assert.ok(socket)
    h.page.onHide(); await task; const count = h.writes.length
    socket.listeners.connect(); socket.listeners.error({ errMsg: 'PRIVATE_LATE_PROBE' }); await flush()
    assert.equal(h.writes.length, count); assert.equal(socket.packets.length, 0); assert.equal(socket.closed, true)
    assert.equal(h.page.privatePassword, ''); assert.equal(h.storageWrites.length, 0)
    await h.page.onShow(); assert.equal(h.page.data.transportMode, 'bound'); assert.equal(h.page.data.probeStatus, 'idle')
    assert.equal(h.page.data.compatibilityReady, false); assert.equal(h.page.data.canProbe, false)
    assert.equal(h.page.data.scanned, false); assert.equal(h.page.privatePassword, ''); assert.equal(h.page.ssidDraft, '')
    assert.doesNotMatch(h.visible(), /PRIVATE_LATE_PROBE|synthetic-password|AICAT-PROVISION/); h.page.onHide()

    const fresh = harness(); await fresh.boot(); await fresh.scan(); fresh.draft(); fresh.mode('direct'); await fresh.probe()
    const opened = await fresh.open(), accepted = opened.modal.success({ confirm: true }); await flush()
    const channel = fresh.sockets.at(-1); fresh.options.deferGetConnectedWifi = true
    channel.listeners.connect(); await flush(); const callback = fresh.wifiReads.at(-1)
    assert.equal(fresh.page.data.busy, true); assert.equal(fresh.page.data.canConfigure, false); assert.equal(fresh.page.data.canProbe, false)
    fresh.mode('bound'); await fresh.page.probeCompatibility(); await fresh.page.configure()
    assert.equal(fresh.page.data.transportMode, 'direct'); assert.equal(fresh.sockets.length, 2); assert.equal(fresh.modals.length, 1)
    assert.equal(channel.packets.length, 0); fresh.page.onHide(); await accepted; await opened.task
    const afterHide = fresh.writes.length
    callback.success({ wifi: { BSSID, SSID: 'AI-CAT-ABC123' }, errMsg: 'PRIVATE_LATE_FRESH_READ' }); await flush()
    assert.equal(fresh.writes.length, afterHide); assert.equal(channel.packets.length, 0); assert.equal(channel.closed, true)
    assert.equal(fresh.page.privatePassword, ''); assert.ok(fresh.storage.get('aiCatProvisionPendingV1'))
    assert.doesNotMatch(fresh.visible(), /PRIVATE_LATE_FRESH_READ|12:34:56:78:9A:BC|AI-CAT-ABC123|synthetic-password/)
  },
  async 'older controllers without compatibility APIs and non Android previews fail closed without crashing'() {
    const h = harness(); await h.boot(); delete h.page.provision.setTransportMode; delete h.page.provision.probe
    h.page.render(h.page.provision.getState()); h.mode('direct'); await h.page.probeCompatibility()
    assert.equal(h.page.data.transportAvailable, false); assert.equal(h.page.data.transportMode, 'bound'); assert.equal(h.sockets.length, 0)
    h.page.onHide()
    for (const platform of ['devtools', 'ios']) {
      const preview = harness({ platform }); await preview.boot(); preview.mode('direct'); await preview.page.probeCompatibility()
      assert.equal(preview.page.data.transportMode, 'bound'); assert.equal(preview.sockets.length, 0); preview.page.onHide()
    }
  },
  async 'a non device or unreadable current Wi-Fi name fails locally without reflecting private metadata in the page'() {
    for (const wifiSSID of [undefined, '<unknown ssid>', 'PRIVATE_PAGE_HOME_ROUTER', 'AI-CAT-abc123']) {
      const h = harness({ wifiSSID }); await h.boot(); await h.scan(); h.draft(); const opened = await h.open()
      const accepted = opened.modal.success({ confirm: true }); await flush()
      assert.equal(h.sockets.length, 0); await accepted; await opened.task
      assert.equal(h.page.data.status, 'not_sent'); assert.equal(h.page.data.pending, false); assert.equal(h.page.privatePassword, '')
      assert.match(h.page.data.error, /热点|Wi-Fi/)
      assert.doesNotMatch(h.visible(), /unknown ssid|PRIVATE_PAGE_HOME_ROUTER|AI-CAT-abc123|12:34:56:78:9A:BC|synthetic-password/)
      h.page.onHide()
    }
  },
  async 'the page shows a sanitized binding diagnostic without treating native text as safe guidance'() {
    const h = harness(); await h.boot(); await h.scan(); h.draft(); const opened = await h.open()
    const accepted = opened.modal.success({ confirm: true }); await flush(); const socket = h.sockets.at(-1); assert.ok(socket)
    socket.listeners.error({ errCode: 4, errMsg: 'PRIVATE_PAGE_NATIVE_ERROR AI-CAT-ABC123 12:34:56:78:9A:BC', SSID: 'PRIVATE_PAGE_HOME_ROUTER' })
    await accepted; await opened.task
    assert.equal(h.page.data.status, 'not_sent'); assert.equal(h.page.data.pending, false); assert.equal(h.page.privatePassword, '')
    assert.match(h.page.data.error, /\bWIFI_BIND_EVENT_4\b/); assert.equal(socket.packets.length, 0)
    assert.match(h.page.data.statusHint, /密码.*安全.*清空/)
    assert.doesNotMatch(h.visible(), /PRIVATE_PAGE_NATIVE_ERROR|PRIVATE_PAGE_HOME_ROUTER|AI-CAT-ABC123|12:34:56:78:9A:BC|synthetic-password/)
    h.page.onHide()
  },
  async 'native Wi-Fi metadata is read only after explicit confirmation and never enters the rendering or persistent bridge'() {
    const h = harness(); await h.boot(); await h.scan(); h.draft(); const opened = await h.open()
    assert.equal(h.wifiStarts.length, 0); assert.equal(h.wifiReads.length, 0); assert.equal(h.sockets.length, 0)
    assert.match(opened.modal.content, /BSSID/); assert.match(opened.modal.content, /不保存或上传/)
    const accepted = opened.modal.success({ confirm: true }), socket = await h.connect()
    assert.equal(h.wifiStarts.length, 1); assert.equal(h.wifiReads.length, 1); assert.deepEqual(socket.boundWifi, { BSSID })
    h.reply('accepted'); await accepted; await opened.task
    assert.doesNotMatch(h.visible(), /12:34:56:78:9A:BC|AI-CAT-ABC123|synthetic-password/); h.page.onHide()
  },
  async 'Wi-Fi initialization read and BSSID failures show not sent guidance without opening the device channel'() {
    for (const options of [{ startWifiFail: 12005 }, { readWifiFail: 12007 }, { bssid: undefined }, { bssid: '02:00:00:00:00:00' }]) {
      const h = harness(options); await h.boot(); await h.scan(); h.draft(); const opened = await h.open()
      await opened.modal.success({ confirm: true }); await opened.task
      assert.equal(h.page.data.status, 'not_sent'); assert.equal(h.page.data.pending, false); assert.equal(h.sockets.length, 0)
      assert.match(h.page.data.error, /Wi-Fi|热点|BSSID|权限/); assert.equal(h.page.privatePassword, '')
      assert.match(h.page.data.statusHint, /密码.*安全.*清空/); assert.match(h.page.data.statusHint, /不代表.*未填写/)
      assert.doesNotMatch(h.visible(), /PRIVATE_PAGE_WIFI|synthetic-password|12:34:56:78:9A:BC/); h.page.onHide()
    }
  },
  async 'hide during Wi-Fi initialization or read drops late callbacks without starting TCP or reviving private inputs'() {
    for (const options of [{ deferStartWifi: true }, { deferGetConnectedWifi: true }]) {
      const h = harness(options); await h.boot(); await h.scan(); h.draft(); const opened = await h.open()
      const accepted = opened.modal.success({ confirm: true }); await flush()
      assert.equal(h.wifiStarts.length, 1); assert.equal(h.wifiReads.length, options.deferStartWifi ? 0 : 1)
      const callback = options.deferStartWifi ? h.wifiStarts[0] : h.wifiReads[0]
      h.page.onHide(); await accepted; await opened.task
      const count = h.writes.length, reads = h.wifiReads.length
      callback.success(options.deferStartWifi ? { errMsg: 'startWifi:ok' } : { wifi: { BSSID, SSID: 'AI-CAT-ABC123' }, errMsg: 'getConnectedWifi:ok' })
      callback.fail({ errCode: 12007, errMsg: 'PRIVATE_PAGE_LATE_WIFI_ERROR' }); await flush()
      assert.equal(h.sockets.length, 0); assert.equal(h.wifiReads.length, reads); assert.equal(h.writes.length, count)
      assert.equal(h.page.privatePassword, ''); assert.deepEqual(plain(h.page.data.formKeys), [])
      assert.doesNotMatch(h.visible(), /PRIVATE_PAGE_WIFI|synthetic-password|12:34:56:78:9A:BC/)
    }
  },
  async 'network fields explain the current lock and unlock after a valid same-visit QR without an automatic packet'() {
    const h = harness(); await h.boot()
    assert.equal(h.page.data.canEditNetwork, false); assert.match(h.page.data.networkLockReason, /先.*扫码|先.*扫描/)
    const task = h.page.scanInvitation(); await flush()
    assert.equal(h.page.data.canEditNetwork, false); assert.match(h.page.data.networkLockReason, /正在.*处理|正在.*扫码/)
    h.scans.at(-1).success({ result: QR, scanType: 'QR_CODE' }); await task
    assert.equal(h.page.data.canEditNetwork, true); assert.equal(h.page.data.networkLockReason, '')
    h.page.inputSSID(input('中文家庭网络 ')); h.page.inputPassword(input(PASSWORD))
    assert.equal(h.page.ssidDraft, '中文家庭网络 '); assert.equal(h.page.privatePassword, PASSWORD)
    assert.equal(h.page.data.canConfigure, false); assert.equal(h.sockets.length, 0)
    h.page.onHide(); assert.equal(h.page.data.canEditNetwork, false); assert.equal(h.page.privatePassword, '')
  },
  async 'an old unknown marker locks even a validated QR and explains original-only recovery without erasing the marker'() {
    const original = { v: 1, deviceId: DEVICE, sessionId: SESSION, requestId: 'wifi_original' }
    const storage = new Map([['aiCatProvisionPendingV1', original]])
    const h = harness({}, storage); await h.boot(); await h.scan()
    assert.equal(h.page.data.scanned, true); assert.equal(h.page.data.pending, true)
    assert.equal(h.page.data.canEditNetwork, false); assert.match(h.page.data.networkLockReason, /未决|待核对/)
    assert.match(h.page.data.networkLockReason, /原请求|原编号/)
    assert.match(h.page.data.statusHint, /原请求|原编号/); assert.doesNotMatch(h.page.data.statusHint, /再填写/)
    h.draft(); await h.page.configure()
    assert.equal(h.page.ssidDraft, ''); assert.equal(h.page.privatePassword, '')
    assert.deepEqual(storage.get('aiCatProvisionPendingV1'), original)
    assert.equal(h.modals.length, 0); assert.equal(h.sockets.length, 0); h.page.onHide()
  },
  async 'preview inputs give the platform reason rather than pretending a QR will enable a real configuration'() {
    for (const platform of ['devtools', 'ios']) {
      const h = harness({ platform }); await h.boot()
      assert.equal(h.page.data.canEditNetwork, false); assert.match(h.page.data.networkLockReason, /安卓.*微信/)
      h.draft(); assert.equal(h.page.privatePassword, ''); assert.equal(h.sockets.length, 0); h.page.onHide()
    }
  },
  async 'Chinese SSID is encrypted as the exact entered text without a shell command or an encoding escape'() {
    const h = harness(); await h.boot(); await h.scan()
    const chinese = '中文网络 '
    h.page.inputSSID(input(chinese)); h.page.inputPassword(input(PASSWORD)); h.page.hotspotChanged(input(['hotspot']))
    assert.equal(h.page.data.canEditNetwork, true)
    const opened = await h.open(), accepted = opened.modal.success({ confirm: true }), socket = await h.connect()
    assert.equal(h.decrypt(socket).ssid, chinese)
    h.reply('connected'); await accepted; await opened.task
    assert.equal(h.sockets.length, 1); assert.doesNotMatch(h.visible(), /synthetic-password|AICAT-PROVISION/); h.page.onHide()
  },
  async 'manual hotspot acknowledgement works before scanning and survives a same-visit scan without authorizing a packet'() {
    const h = harness(); await h.boot(); h.page.hotspotChanged(input(['hotspot']))
    assert.equal(h.page.data.hotspotReady, true); assert.equal(h.page.data.canConfigure, false)
    await h.page.configure(); assert.equal(h.modals.length, 0)
    await h.scan(); assert.equal(h.page.data.scanned, true); assert.equal(h.page.data.hotspotReady, true)
    h.page.inputSSID(input(SSID)); h.page.inputPassword(input(PASSWORD))
    assert.equal(h.page.data.canConfigure, true); assert.equal(h.sockets.length, 0)
    h.page.onHide(); await h.page.onShow(); assert.equal(h.page.data.hotspotReady, false); h.page.onHide()
  },
  async 'a user scan uses one embedded camera without opening the native scanner page or exposing QR content'() {
    const h = harness(); await h.boot(); assert.deepEqual(plain(h.page.data.cameraKeys), [])
    const task = h.page.scanInvitation(); await flush()
    assert.equal(h.nativeScans.length, 0); assert.equal(h.page.data.cameraKeys.length, 1)
    h.scans.at(-1).success({ result: QR, scanType: 'QR_CODE' }); await task
    assert.equal(h.page.data.scanned, true); assert.match(h.page.data.phaseLabel, /已校验/)
    assert.deepEqual(plain(h.page.data.cameraKeys), []); assert.equal(h.sockets.length, 0)
    assert.doesNotMatch(h.visible(), /2222222222222222|AICAT-PROVISION/); h.page.onHide()
  },
  async 'embedded camera cancel permission failure stop and timeout settle visibly without any network write'() {
    for (const reason of ['cancel', 'fail', 'stop', 'timeout']) {
      const h = harness(); await h.boot(); const task = h.page.scanInvitation(); await flush()
      assert.equal(h.nativeScans.length, 0)
      if (reason === 'timeout') { assert.equal(h.timers.size, 1); [...h.timers.values()][0]() }
      else h.scans.at(-1)[reason]()
      await task; assert.equal(h.page.data.busy, false); assert.equal(h.page.data.scanned, false)
      assert.deepEqual(plain(h.page.data.cameraKeys), []); assert.match(h.page.data.notice, /扫码|相机/)
      assert.equal(h.sockets.length, 0); assert.equal(h.storage.size, 0)
      assert.doesNotMatch(h.visible(), /private-camera-error|AICAT-PROVISION/); h.page.onHide()
    }
  },
  async 'QR camera metadata aliases and a missing type still require valid invitation and authenticated original receipt'() {
    for (const scanType of ['QR_CODE', 'qrCode', 'qrcode', 'qr_code', undefined]) {
      const h = harness(); await h.boot(); const scanning = h.page.scanInvitation(); await flush()
      h.scans.at(-1).success({ result: QR, scanType }); await flush()
      assert.equal(h.page.data.scanned, true, 'a supported QR metadata variant must reach strict invitation validation')
      await scanning; assert.equal(h.sockets.length, 0)
      h.draft(); const opened = await h.open(), accepted = opened.modal.success({ confirm: true }), socket = await h.connect()
      socket.listeners.error(); await accepted; await opened.task
      assert.equal(h.page.data.status, 'unknown'); assert.ok(h.storage.get('aiCatProvisionPendingV1'))
      const receipt = h.page.scanFinalReceipt(); await flush()
      h.scans.at(-1).success({ result: h.receiptQR('connected'), scanType }); await flush()
      assert.equal(h.page.data.status, 'connected', 'metadata normalization cannot replace receipt AEAD verification')
      await receipt; assert.equal(h.page.data.pending, false); assert.equal(h.sockets.length, 1)
      assert.doesNotMatch(h.visible(), /synthetic-password|2222222222222222|AICAT-PROVISION/); h.page.onHide()
    }
  },
  async 'unsupported metadata and invalid or oversized results fail visibly without reflecting input or waiting for timeout'() {
    for (const value of [
      { result: QR, scanType: 'PRIVATE_UNSUPPORTED_TYPE' }, { result: QR, scanType: null },
      { result: QR, scanType: 1 }, { result: '', scanType: 'QR_CODE' },
      { result: 'PRIVATE_OVERSIZED_RESULT'.repeat(100), scanType: 'QR_CODE' },
      { result: {}, scanType: 'QR_CODE' }, { result: 'https://invalid.example/PRIVATE_NOT_A_PROVISION_CODE', scanType: undefined }
    ]) {
      const h = harness(); await h.boot(); const scanning = h.page.scanInvitation(); await flush()
      h.scans.at(-1).success(value); await flush()
      assert.equal(h.page.data.busy, false, 'unsupported events must not silently wait for the camera deadline')
      assert.equal(h.page.data.scanned, false); assert.match(h.page.data.error + h.page.data.notice, /扫码|二维码/)
      assert.deepEqual(plain(h.page.data.cameraKeys), []); assert.equal(h.timers.size, 0)
      await scanning; assert.equal(h.sockets.length, 0); assert.equal(h.storage.size, 0)
      assert.doesNotMatch(h.visible(), /PRIVATE_UNSUPPORTED_TYPE|PRIVATE_OVERSIZED_RESULT|PRIVATE_NOT_A_PROVISION_CODE|AICAT-PROVISION/)
      h.page.onHide()
    }
  },
  async 'rejected receipt metadata preserves the exact unknown request instead of accepting a valid body from a non QR event'() {
    const h = harness(); await h.boot(); await h.scan(); h.draft()
    const opened = await h.open(), accepted = opened.modal.success({ confirm: true }), socket = await h.connect()
    socket.listeners.error(); await accepted; await opened.task
    const marker = JSON.stringify([...h.storage]), receipt = h.page.scanFinalReceipt(); await flush()
    h.scans.at(-1).success({ result: h.receiptQR('connected'), scanType: 'PRIVATE_RECEIPT_BARCODE' }); await flush()
    assert.equal(h.page.data.busy, false); assert.equal(h.page.data.status, 'unknown')
    assert.equal(h.page.data.pending, true); assert.equal(JSON.stringify([...h.storage]), marker)
    assert.match(h.page.data.notice, /原.*请求.*核对|原.*编号/); assert.doesNotMatch(h.visible(), /PRIVATE_RECEIPT_BARCODE/)
    await receipt
    const forged = h.page.scanFinalReceipt(); await flush()
    h.scans.at(-1).success({ result: h.receiptQR('connected', { ciphertext: '00'.repeat(16) }), scanType: undefined }); await forged
    assert.equal(h.page.data.status, 'unknown'); assert.equal(h.page.data.pending, true)
    assert.equal(JSON.stringify([...h.storage]), marker); assert.match(h.page.data.error, /校验/)
    assert.equal(h.sockets.length, 1); h.page.onHide()
  },
  async 'unsupported explicit type fails visibly and duplicate or old component frames cannot replace a new scan'() {
    const h = harness(); await h.boot(); let task = h.page.scanInvitation(); await flush(); const old = h.scans.at(-1)
    assert.equal(h.nativeScans.length, 0)
    old.success({ result: QR, scanType: 'CODE_128' }); await flush()
    assert.equal(h.page.data.busy, false); assert.equal(h.page.data.scanned, false); assert.match(h.page.data.notice, /类型|二维码/)
    await task
    task = h.page.scanInvitation(); await flush(); const fresh = h.scans.at(-1), count = h.writes.length
    old.fail(); old.success({ result: QR, scanType: 'qrcode' }); old.success({ result: 'PRIVATE_LATE_EVENT'.repeat(200), scanType: 'PRIVATE_LATE_TYPE' })
    assert.equal(h.writes.length, count)
    assert.equal(h.page.data.busy, true); fresh.success({ result: QR, scanType: 'QR_CODE' }); await task
    assert.equal(h.page.data.scanned, true); h.page.onHide()
  },
  async 'hiding or unloading destroys the camera and its timer and cannot revive a QR on the next visit'() {
    for (const leave of ['onHide', 'onUnload']) {
      const h = harness(); await h.boot(); const oldTask = h.page.scanInvitation(); await flush()
      const old = h.scans.at(-1), timeout = [...h.timers.values()][0]
      h.page[leave](); await oldTask
      assert.equal(h.page.cameraAttempt, null); assert.equal(h.timers.size, 0)
      const count = h.writes.length; old.success({ result: QR, scanType: 'QR_CODE' }); timeout()
      assert.equal(h.writes.length, count)
      await h.page.onShow(); assert.equal(h.page.data.scanned, false); assert.equal(h.page.data.hotspotReady, false)
      const currentTask = h.page.scanInvitation(); await flush(); const fresh = h.scans.at(-1), key = h.page.data.cameraKeys[0]
      old.cancel(); timeout(); assert.equal(h.page.data.cameraKeys[0], key); assert.equal(h.page.data.busy, true)
      fresh.success({ result: QR, scanType: 'QR_CODE' }); await currentTask
      assert.equal(h.page.data.scanned, true); assert.equal(h.sockets.length, 0); h.page.onHide()
    }
  },
  async 'cancelled receipt camera preserves the exact unknown marker and a later scan reconciles it without another packet'() {
    const h = harness(); await h.boot(); await h.scan(); h.draft(); const opened = await h.open()
    const accepted = opened.modal.success({ confirm: true }), socket = await h.connect()
    socket.listeners.error(); await accepted; await opened.task
    const marker = plain(h.storage.get('aiCatProvisionPendingV1')), receipt = h.receiptQR('connected')
    let task = h.page.scanFinalReceipt(); await flush(); h.scans.at(-1).cancel(); await task
    assert.deepEqual(h.storage.get('aiCatProvisionPendingV1'), marker)
    assert.equal(h.page.data.canConfigure, false); assert.equal(h.page.data.status, 'unknown')
    assert.match(h.page.data.notice, /未发起新的配网请求.*原配网请求结果仍待核对/)
    assert.doesNotMatch(h.page.data.notice, /已取消扫码，未发送网络信息/)
    task = h.page.scanFinalReceipt(); await flush(); h.scans.at(-1).success({ result: receipt, scanType: 'QR_CODE' }); await task
    assert.equal(h.page.data.status, 'connected'); assert.equal(h.page.data.pending, false)
    assert.equal(h.sockets.length, 1); assert.equal(h.nativeScans.length, 0); h.page.onHide()
  },
  async 'a failed invitation rescan never claims an earlier unknown configuration did not execute'() {
    const storage = new Map([['aiCatProvisionPendingV1', { v: 1, deviceId: DEVICE, sessionId: SESSION, requestId: 'wifi_original' }]])
    const h = harness({}, storage); await h.boot(); await h.scan('not-an-invitation')
    assert.equal(h.page.data.pending, true); assert.equal(h.page.data.scanned, false)
    assert.match(h.page.data.error, /本次扫码未发起新的网络配置.*原配网请求结果仍待核对/)
    assert.doesNotMatch(h.page.data.error, /未更改设备网络/)
    assert.doesNotMatch(h.page.data.statusHint, /未更改设备网络/)
    assert.equal(storage.get('aiCatProvisionPendingV1').requestId, 'wifi_original')
    assert.equal(h.sockets.length, 0); h.page.onHide()
  },
  async 'opening and typing require a scan and explicit hotspot acknowledgement with no automatic packet'() {
    const h = harness(); await h.boot(); assert.equal(h.scans.length, 0); assert.equal(h.sockets.length, 0)
    await h.scan(); h.page.inputSSID(input(SSID)); h.page.inputPassword(input(PASSWORD)); await h.page.configure()
    assert.equal(h.modals.length, 0); assert.equal(h.sockets.length, 0)
    h.page.hotspotChanged(input(['hotspot'])); assert.equal(h.page.data.canConfigure, true)
    assert.doesNotMatch(h.visible(), /synthetic-password|2222222222222222|AICAT-PROVISION/); h.page.onHide()
  },
  async 'devtools and iOS preview cannot invoke camera or configure even through direct handlers'() {
    for (const platform of ['devtools', 'ios']) {
      const h = harness({ platform }); await h.boot(); await h.page.scanInvitation(); h.draft(); await h.page.configure(); await h.page.queryOriginal(); await h.page.scanFinalReceipt()
      assert.equal(h.scans.length, 0); assert.equal(h.sockets.length, 0); assert.equal(h.modals.length, 0); assert.equal(h.page.data.androidAvailable, false); h.page.onHide()
    }
  },
  async 'configuration uses a native-valid modal and one encrypted fixed TCP packet only after confirmation'() {
    const h = harness(); await h.boot(); await h.scan(); h.draft(); const opened = await h.open()
    assert.equal(h.sockets.length, 0); assert.match(opened.modal.content, /SSH.*暂|暂.*SSH/)
    assert.match(opened.modal.content, /不.*绑定|不等于.*绑定/)
    const confirmed = opened.modal.success({ confirm: true }), socket = await h.connect()
    assert.deepEqual(plain(socket.destination), { address: '10.47.0.1', port: 8766, timeout: 5 })
    assert.deepEqual(h.decrypt(socket), { ssid: SSID, password: PASSWORD, security: 'wpa2-personal' })
    h.reply('accepted'); await confirmed; await opened.task
    assert.equal(h.sockets.length, 1); assert.equal(socket.packets.length, 1); assert.equal(h.page.data.pending, true)
    assert.doesNotMatch(h.visible(), /synthetic-password|2222222222222222|AICAT-PROVISION/)
    assert.equal(h.page.privatePassword, ''); h.page.onHide()
  },
  async 'cancellation and both native modal failure paths remain visibly unsent without a packet'() {
    for (const failure of ['cancel', 'fail', 'throw']) {
      const h = harness(); await h.boot(); await h.scan(); h.draft()
      if (failure === 'throw') { h.options.throwModal = true; await h.page.configure() }
      else { const opened = await h.open(); if (failure === 'cancel') await opened.modal.success({ confirm: false }); else opened.modal.fail({ errMsg: 'raw-private-modal-error' }); await opened.task }
      assert.equal(h.page.data.confirming, false); assert.match(h.page.data.notice, /未发送/)
      assert.equal(h.sockets.length, 0); assert.equal(h.storage.size, 0); assert.doesNotMatch(h.visible(), /raw-private/); h.page.onHide()
    }
  },
  async 'unchanged native input preserves confirmation while real edits revoke it'() {
    const h = harness(); await h.boot(); await h.scan(); h.draft(); const first = await h.open()
    h.page.inputPassword(input(PASSWORD)); h.page.inputSSID(input(SSID)); assert.equal(h.page.data.confirming, true)
    h.page.inputPassword(input('a-new-password')); await first.modal.success({ confirm: true }); await first.task
    assert.equal(h.sockets.length, 0); assert.match(h.page.data.notice, /未发送.*更改/); h.page.onHide()
  },
  async 'late old cancel confirm and failure callbacks cannot erase a newer visit intent'() {
    for (const callback of ['cancel', 'confirm', 'fail']) {
      const h = harness(); await h.boot(); await h.scan(); h.draft(); const old = await h.open()
      h.page.onHide(); assert.equal(h.page.privatePassword, ''); assert.deepEqual(plain(h.page.data.formKeys), [])
      await h.page.onShow(); await h.scan(); h.draft(); const fresh = await h.open(), target = h.page.confirmation, count = h.writes.length
      if (callback === 'fail') old.modal.fail({ errMsg: 'private-late-error' }); else await old.modal.success({ confirm: callback === 'confirm' })
      await old.task; assert.equal(h.page.confirmation, target); assert.equal(h.writes.length, count)
      const accepted = fresh.modal.success({ confirm: true }); await h.connect(); h.reply('accepted'); await accepted; await fresh.task
      assert.equal(h.sockets.length, 1); h.page.onHide()
    }
  },
  async 'unknown permits only original receipt query without resending credentials'() {
    const h = harness(); await h.boot(); await h.scan(); h.draft(); const opened = await h.open(), confirmed = opened.modal.success({ confirm: true }), socket = await h.connect()
    socket.listeners.error(); await confirmed; await opened.task
    assert.equal(h.page.data.status, 'unknown'); assert.equal(h.page.data.canConfigure, false)
    await h.page.configure(); assert.equal(h.modals.length, 1)
    const check = h.page.queryOriginal(), receipt = await h.connect(); assert.equal(h.packet(receipt).kind, 'status'); assert.deepEqual(h.decrypt(receipt), {})
    h.reply('connected', receipt); await check
    assert.equal(h.page.data.status, 'connected'); assert.equal(h.page.data.pending, false)
    assert.equal(h.sockets.filter(value => h.packet(value).kind === 'configure').length, 1); h.page.onHide()
  },
  async 'hide during camera or an inflight packet drops late UI and clears native/private inputs'() {
    const h = harness(); await h.boot(); const scanning = h.page.scanInvitation(); await flush(); const callback = h.scans.at(-1); h.page.onHide(); const count = h.writes.length
    callback.success({ result: QR, scanType: 'QR_CODE' }); await scanning; assert.equal(h.writes.length, count)
    await h.page.onShow(); await h.scan(); h.draft(); const opened = await h.open(), accepted = opened.modal.success({ confirm: true }), socket = await h.connect()
    h.page.onHide(); const after = h.writes.length; socket.listeners.error(); await accepted; await opened.task
    assert.equal(h.writes.length, after); assert.equal(h.page.privatePassword, ''); assert.deepEqual(plain(h.page.data.formKeys), [])
    assert.ok(h.storage.get('aiCatProvisionPendingV1')); assert.doesNotMatch(h.visible(), /synthetic-password|2222222222222222/)
  },
  async 'final receipt scan never invents success if the controller lacks the method or original QR context'() {
    const h = harness(); await h.boot(); await h.page.scanFinalReceipt(); assert.equal(h.scans.length, 0)
    await h.scan(); h.draft(); const opened = await h.open(), accepted = opened.modal.success({ confirm: true }), socket = await h.connect()
    socket.listeners.error(); await accepted; await opened.task
    h.page.provision.scanReceipt = undefined
    await h.page.scanFinalReceipt(); assert.equal(h.page.data.status, 'unknown'); assert.equal(h.sockets.length, 1)
    assert.match(h.page.data.notice, /不能|尚不能/); assert.ok(h.storage.get('aiCatProvisionPendingV1')); h.page.onHide()
  },
  async 'after AP disconnect the original invitation plus encrypted final receipt verifies success without another TCP write'() {
    const h = harness(); await h.boot(); await h.scan(); h.draft(); const opened = await h.open(), accepted = opened.modal.success({ confirm: true }), socket = await h.connect()
    socket.listeners.error(); await accepted; await opened.task
    const receipt = h.receiptQR('connected')
    h.page.onHide(); await h.page.onShow()
    assert.equal(h.page.data.canScanReceipt, false); const before = h.scans.length; await h.page.scanFinalReceipt(); assert.equal(h.scans.length, before)
    await h.scan(); assert.equal(h.page.data.hotspotReady, false); assert.equal(h.page.data.canScanReceipt, true)
    const task = h.page.scanFinalReceipt(); await flush(); h.scans.at(-1).success({ scanType: 'QR_CODE', result: receipt }); await task
    assert.equal(h.page.data.status, 'connected'); assert.equal(h.page.data.pending, false)
    assert.equal(h.sockets.length, 1); assert.doesNotMatch(h.visible(), /synthetic-password|2222222222222222|AICAT-PROVISION/); h.page.onHide()
  },
  async 'unload clears private input without a late view write or an automatic network operation'() {
    const h = harness(); await h.boot(); await h.scan(); h.draft(); const before = h.writes.length
    h.page.onUnload(); assert.equal(h.page.privatePassword, ''); assert.equal(h.page.ssidDraft, '')
    assert.equal(h.writes.length, before); assert.equal(h.sockets.length, 0)
    assert.doesNotMatch(h.visible(), /synthetic-password|2222222222222222|AICAT-PROVISION/)
  },
  async 'template has an uncontrolled password input and clear manual-network and non-binding disclosure'() {
    const wxml = fs.readFileSync(path.join(root, 'pages/provision/index.wxml'), 'utf8')
    const password = wxml.match(/<input\b[^>]*password="\{\{true\}\}"[^>]*>/)
    assert.ok(password); assert.doesNotMatch(password[0], /\bvalue=/)
    assert.match(wxml, /AI-CAT-XXXXXX/); assert.match(wxml, /扫描收尾回执/); assert.match(wxml, /不等于.*绑定/)
    assert.match(wxml, /操作员.*180|180.*操作员/); assert.match(wxml, /不知道|未知|不确定/)
    const checkbox = wxml.match(/<checkbox\b[^>]*value="hotspot"[^>]*>/)
    assert.ok(checkbox); assert.doesNotMatch(checkbox[0], /!scanned/)
    assert.match(wxml, /<camera\b[^>]*mode="scanCode"[^>]*data-scan-id/)
    assert.match(wxml, /wx:for="\{\{cameraKeys\}\}"\s+wx:key="\*this"/)
    assert.match(wxml, /88888888/); assert.match(wxml, /固定弱口令.*本地测试/)
    assert.match(wxml, /20261030\.5/)
    assert.match(wxml, /radio-group\b[^>]*bindchange="transportChanged"/)
    assert.match(wxml, /<radio\b[^>]*value="bound"/); assert.match(wxml, /<radio\b[^>]*value="direct"/)
    assert.equal((wxml.match(/<radio\b[^>]*class="transport-radio"/g) || []).length, 2)
    assert.doesNotMatch(fs.readFileSync(path.join(root, 'pages/provision/index.wxss'), 'utf8'), /\.transport-option\s+radio\b/)
    assert.match(wxml, /bindtap="probeCompatibility"/); assert.match(wxml, /\{\{probeHint\}\}/)
    assert.match(wxml, /系统.*选路|系统.*路由/); assert.match(wxml, /不是身份认证|不等于身份认证/)
    assert.match(wxml, /不代表配网成功|不等于配网成功/); assert.match(wxml, /不会自动.*回退|不自动.*切换/)
    for (const value of ARCHIVE_CHECKS) assert.ok(wxml.includes('value="' + value + '"'))
    assert.match(wxml, /bindchange="archiveAcksChanged"/); assert.match(wxml, /bindtap="archiveRequest"/)
    assert.match(wxml, /受信.*本地试点/); assert.match(wxml, /人工声明/); assert.match(wxml, /不是.*自动.*USB|不.*自动.*USB/)
    assert.match(wxml, /原程序.*结束|原配网程序.*结束/); assert.match(wxml, /检查点/); assert.match(wxml, /凭据.*可能.*保存/)
    assert.match(wxml, /reviewIdentity\.requestId/); assert.match(wxml, /lastArchiveIdentity\.requestId/)
    assert.match(wxml, /pending\s*\?\s*'先核对原窗口'/)
    assert.match(wxml, /archiveLimitReached/); assert.match(wxml, /20.*归档.*上限|20.*上限/)
    assert.match(wxml, /不会.*删除.*历史|不会.*删除.*记录/)
    const fields = wxml.match(/<input\b[^>]*class="field-input"[^>]*>/g)
    assert.equal(fields.length, 2)
    for (const field of fields) assert.match(field, /disabled="\{\{!canEditNetwork\}\}"/)
    assert.match(wxml, /\{\{networkLockReason\}\}/); assert.match(wxml, /中文.*直接.*输入|中文.*直接.*填写/)
    assert.doesNotMatch(wxml, /二维码已校验，可以继续填写/)
  }
}
module.exports = { harness, KEY, DEVICE, SESSION, QR, PASSWORD, SSID, flush, plain }
if (require.main === module) (async () => { let failed = 0; for (const [name, run] of Object.entries(cases)) { try { await run(); console.log('PASS ' + name) } catch (error) { failed++; console.error('FAIL ' + name + ': ' + error.stack) } }; console.log('Provision page smoke: ' + (Object.keys(cases).length - failed) + '/' + Object.keys(cases).length + ' (offline actual TS page/controller/AES)'); if (failed) process.exitCode = 1 })().catch(error => { console.error(error); process.exitCode = 1 })
