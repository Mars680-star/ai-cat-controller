// Explicit Android SoftAP provisioning. QR/key/password remain inside closure.
// No wx.request, phone connectWifi, auto configure, cloud token or arbitrary URL.
const PROTOCOL = 'aicat-provision-v1', AP_ADDRESS = '10.47.0.1', TCP_PORT = 8766, MAX_FRAME = 4096
const STORAGE = 'aiCatProvisionPendingV1'
const QR = /^AICAT-PROVISION:1:(dev_[0-9a-f]{32}):([0-9a-f]{32}):([0-9a-f]{64})$/
const RECEIPT_QR_PREFIX = 'AICAT-PROVISION-RECEIPT:1:'
const ID = /^[A-Za-z0-9_.:-]{1,64}$/
const STATUSES = ['accepted', 'connecting', 'connected', 'failed', 'unknown', 'not_sent']
const PROBE_VALID_MS = 60000
const ARCHIVE_LIMIT = 20, ARCHIVE_CHECKS = ['window-ended', 'usb-checked', 'accept-unknown']
const LOCAL_ARCHIVE_CHECKS = ['window-ended', 'network-stable', 'device-idle', 'accept-unknown']
const HEX32 = /^[0-9a-f]{32}$/, DEVICE_ID = /^dev_[0-9a-f]{32}$/
const SEND_ERRORS: any = {
  random: '安全随机数不可用，请检查或升级安卓微信后重新确认。',
  encryption: '本次安全加密准备失败，请重新校验本次二维码后确认。',
  wifi_api: '当前微信不支持读取并绑定 Wi-Fi，请升级安卓微信。',
  wifi_init: '手机 Wi-Fi 模块初始化失败，请确认手机 Wi-Fi 已开启。',
  wifi_read: '无法读取当前设备热点信息，请检查微信的系统权限及小程序隐私授权。',
  wifi_permission: '微信未获准读取当前 Wi-Fi，请检查系统权限及小程序隐私授权。',
  wifi_off: '手机 Wi-Fi 开关未开启，请手动连接本次设备热点。',
  wifi_location_off: '读取 Wi-Fi 时系统返回定位开关未开启，请检查系统定位开关后重新确认。',
  wifi_bssid: '当前 Wi-Fi 没有返回可用的热点标识（BSSID），请确认仍连接设备热点并检查微信的系统权限。',
  wifi_timeout: '读取当前设备热点信息超时，请检查手机 Wi-Fi 和微信权限。',
  wifi_hotspot_unavailable: '系统未返回可核对的设备热点名称，请检查微信的系统及隐私权限；不要切换后直接沿用旧的热点勾选。',
  wifi_hotspot_other: '手机当前连接的不是 AI-CAT 设备热点，可能已自动切回其他 Wi-Fi；请连接本次设备热点后重新确认。',
  wifi_bind: '绑定当前 Wi-Fi 失败，请确认仍连接本次 AI-CAT 设备热点。',
  wifi_bind_call: '调用 Wi-Fi 绑定失败，请查看诊断码并确认微信运行环境及设备热点连接。',
  wifi_bind_event: '绑定成功前安卓微信本地通道报错，请查看诊断码并确认仍连接设备热点。',
  wifi_bind_closed: 'Wi-Fi 绑定完成前通道已关闭，请确认设备热点仍开启、手机仍保持连接。',
  wifi_bind_timeout: '绑定当前 Wi-Fi 超时，请确认仍连接本次 AI-CAT 设备热点。',
  socket_create: '无法创建本地设备通道，请检查安卓微信是否支持 TCP 通信。',
  socket_events: '注册本地通道事件失败，请查看诊断码并检查安卓微信运行环境。',
  tcp_connect: '无法连接设备配网端口，请检查设备热点及终端配网窗口是否仍开启。',
  tcp_connect_closed: '连接设备配网端口前通道已关闭，请确认设备热点及终端配网窗口仍开启。',
  tcp_connect_timeout: '连接设备配网端口超时，请检查设备热点及终端配网窗口是否仍开启。',
  direct_probe: '本地直连端口自检未能连接，请确认手机仍连接设备热点、设备配网窗口仍开启。',
  direct_probe_timeout: '本地直连端口自检超时，请确认设备热点与配网窗口仍开启。',
  direct_probe_required: '兼容模式需要本次二维码的有效连接自检，请先手动自检，再重新确认。',
  direct_probe_changed: '模式、二维码、热点或自检有效期已变化，请重新手动自检并确认。',
  context_changed: '本次页面或确认已失效，请返回页面重新核对。'
}
const NATIVE_SOCKET_CODES = [-4, -3, -2, -1, 0, 1, 2, 3, 4, 5, 6]
const SOCKET_DIAGNOSTICS: any = {
  socket_create: 'SOCKET_CREATE', socket_events: 'SOCKET_EVENTS',
  wifi_bind_call: 'WIFI_BIND_CALL', wifi_bind_event: 'WIFI_BIND_EVENT', wifi_bind_closed: 'WIFI_BIND_CLOSED',
  wifi_bind_timeout: 'WIFI_BIND_TIMEOUT', tcp_connect: 'TCP_CONNECT',
  tcp_connect_closed: 'TCP_CONNECT_CLOSED', tcp_connect_timeout: 'TCP_CONNECT_TIMEOUT',
  direct_probe: 'DIRECT_PROBE', direct_probe_timeout: 'DIRECT_PROBE_TIMEOUT'
}
function socketCode(value: any): number | null {
  try {
    const code = value && value.errCode
    return typeof code === 'number' && NATIVE_SOCKET_CODES.includes(code) ? code : null
  } catch (_) { return null }
}
function sendFailure(reason: string, dispatched = false, nativeCode: number | null = null) {
  return Object.assign(new Error('配网通信未完成'), { reason, dispatched, nativeCode })
}
function failureDetail(error: any) {
  const reason = error && typeof error.reason === 'string' && Object.prototype.hasOwnProperty.call(SEND_ERRORS, error.reason)
    ? SEND_ERRORS[error.reason] : '发送前准备失败，请检查安卓运行环境和当前设备热点后重新确认。'
  const stage = error && typeof error.reason === 'string' && Object.prototype.hasOwnProperty.call(SOCKET_DIAGNOSTICS, error.reason)
    ? SOCKET_DIAGNOSTICS[error.reason] : ''
  // Diagnostic values are a fixed local stage and a small numeric allowlist.
  // Do not parse or echo native errMsg, addresses, BSSID, SSID or credentials.
  const code = error && typeof error.nativeCode === 'number' && NATIVE_SOCKET_CODES.includes(error.nativeCode) ? String(error.nativeCode) : 'NA'
  return reason + (stage ? '（诊断码：[' + stage + '_' + code + ']）' : '')
}
function notSentMessage(error: any) { return '网络信息未发送：' + failureDetail(error) }
function reject(): never { throw new Error('配网资料或响应未通过校验') }
function exact(value: any, fields: string[]) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== fields.length || fields.some(field => !Object.prototype.hasOwnProperty.call(value, field))) reject()
}
function utf8(value: string): Uint8Array {
  const encoded = encodeURIComponent(value), bytes = []
  for (let i = 0; i < encoded.length; i++) {
    if (encoded[i] === '%') { bytes.push(parseInt(encoded.slice(i + 1, i + 3), 16)); i += 2 }
    else bytes.push(encoded.charCodeAt(i))
  }
  return new Uint8Array(bytes)
}
function text(bytes: Uint8Array): string { return decodeURIComponent(Array.from(bytes, byte => '%' + byte.toString(16).padStart(2, '0')).join('')) }
function hex(bytes: Uint8Array): string { return Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('') }
function unhex(value: string): Uint8Array {
  if (typeof value !== 'string' || !/^(?:[0-9a-f]{2})+$/.test(value)) reject()
  return new Uint8Array(value.match(/../g).map(byte => parseInt(byte, 16)))
}
function parseProvisioningQR(value: any) {
  if (typeof value !== 'string' || value.length > 192) reject()
  const match = QR.exec(value)
  if (!match || match[0] !== value) reject()
  return { deviceId: match[1], sessionId: match[2], keyHex: match[3] }
}
function verifyProvisioningNetwork(value: any) {
  exact(value, ['ssid', 'password', 'security'])
  if (value.security !== 'wpa2-personal' || typeof value.ssid !== 'string' || typeof value.password !== 'string' ||
      /[\x00-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]/.test(value.ssid + value.password)) reject()
  const ssid = utf8(value.ssid), password = utf8(value.password)
  if (ssid.length < 1 || ssid.length > 32 || !(password.length >= 8 && password.length <= 63 || /^[0-9a-fA-F]{64}$/.test(value.password))) reject()
  return { ssid: value.ssid, password: value.password, security: 'wpa2-personal' }
}
function cipher() {
  if (typeof (globalThis as any).BigInt !== 'function' || typeof (DataView.prototype as any).setBigUint64 !== 'function') reject()
  // Lazy require: an unsupported engine must not crash unrelated product pages.
  return require('../vendor/noble-ciphers-2.4.0/aes').gcm
}
function aad(invite: any, requestId: string, kind: string, direction: string) {
  if (!ID.test(requestId) || !['configure', 'status'].includes(kind) || !['request', 'response'].includes(direction)) reject()
  return utf8(JSON.stringify([PROTOCOL, direction, invite.deviceId, invite.sessionId, requestId, kind]))
}
function encodeProvisioningPacket(invite: any, requestId: string, kind: string, body: any, nonce: Uint8Array, direction = 'request') {
  if (!(nonce instanceof Uint8Array) || nonce.length !== 12 || nonce[0] !== (direction === 'request' ? 0 : 1)) reject()
  const plain = utf8(JSON.stringify(body))
  if (plain.length > 1520) reject()
  const data = cipher()(unhex(invite.keyHex), nonce, aad(invite, requestId, kind, direction)).encrypt(plain)
  return { v: 1, deviceId: invite.deviceId, sessionId: invite.sessionId, requestId, kind, nonce: hex(nonce), ciphertext: hex(data) }
}
function decodeProvisioningPacket(invite: any, value: any, requestId: string, kind: string, direction = 'response') {
  exact(value, ['v', 'deviceId', 'sessionId', 'requestId', 'kind', 'nonce', 'ciphertext'])
  if (value.v !== 1 || value.deviceId !== invite.deviceId || value.sessionId !== invite.sessionId || value.requestId !== requestId || value.kind !== kind ||
      typeof value.nonce !== 'string' || !/^[0-9a-f]{24}$/.test(value.nonce) || !value.nonce.startsWith(direction === 'request' ? '00' : '01') ||
      typeof value.ciphertext !== 'string' || value.ciphertext.length % 2 || !/^[0-9a-f]{32,3072}$/.test(value.ciphertext)) reject()
  const raw = text(cipher()(unhex(invite.keyHex), unhex(value.nonce), aad(invite, requestId, kind, direction)).decrypt(unhex(value.ciphertext)))
  const body = JSON.parse(raw)
  if (raw !== JSON.stringify(body)) reject()
  return body
}
function frame(packet: any): Uint8Array {
  const body = utf8(JSON.stringify(packet))
  if (body.length < 1 || body.length > MAX_FRAME) reject()
  const data = new Uint8Array(body.length + 4)
  new DataView(data.buffer).setUint32(0, body.length, false)
  data.set(body, 4); return data
}
function marker(value: any) {
  if (value === undefined || value === '') return null
  exact(value, ['v', 'deviceId', 'sessionId', 'requestId'])
  if (value.v !== 1 || typeof value.deviceId !== 'string' || typeof value.sessionId !== 'string' || typeof value.requestId !== 'string' ||
      !/^dev_[0-9a-f]{32}$/.test(value.deviceId) || !/^[0-9a-f]{32}$/.test(value.sessionId) || !ID.test(value.requestId)) reject()
  return { v: 1, deviceId: value.deviceId, sessionId: value.sessionId, requestId: value.requestId }
}
function same(a: any, b: any) { return JSON.stringify(a) === JSON.stringify(b) }
function reviewChecks(value: any, expected = ARCHIVE_CHECKS) {
  if (!Array.isArray(value) || value.length !== expected.length || expected.some(item => value.filter(candidate => candidate === item).length !== 1)) reject()
  return expected.slice()
}
function archivedEntry(value: any) {
  const local = value && value.source === 'local_gateway'
  exact(value, local ? ['original', 'status', 'resolution', 'reviewedAt', 'checks', 'source', 'reviewId', 'outerRequestId', 'cardId']
    : ['original', 'status', 'resolution', 'reviewedAt', 'checks'])
  const original = marker(value.original)
  if (!original || value.status !== 'unknown' || value.resolution !== 'operator_archived' ||
      !Number.isSafeInteger(value.reviewedAt) || value.reviewedAt < 0) reject()
  if (local) {
    if (typeof value.reviewId !== 'string' || !HEX32.test(value.reviewId) || typeof value.cardId !== 'string' || !HEX32.test(value.cardId) ||
        typeof value.outerRequestId !== 'string' || !ID.test(value.outerRequestId)) reject()
    return { original, status: 'unknown', resolution: 'operator_archived', reviewedAt: value.reviewedAt,
      checks: reviewChecks(value.checks, LOCAL_ARCHIVE_CHECKS), source: 'local_gateway', reviewId: value.reviewId,
      outerRequestId: value.outerRequestId, cardId: value.cardId }
  }
  return { original, status: 'unknown', resolution: 'operator_archived', reviewedAt: value.reviewedAt, checks: reviewChecks(value.checks) }
}
function sameWindow(left: any, right: any) { return left.deviceId === right.deviceId && left.sessionId === right.sessionId }
function readLedger(value: any): any {
  if (value && value.v === 2) {
    exact(value, ['v', 'revision', 'pending', 'archives'])
    if (!Number.isSafeInteger(value.revision) || value.revision < 1 || !Array.isArray(value.archives) ||
        value.archives.length < 1 || value.archives.length > ARCHIVE_LIMIT || value.revision < value.archives.length) reject()
    const archives = value.archives.map(archivedEntry)
    const pending = value.pending === null ? null : marker(value.pending)
    if (value.pending !== null && !pending) reject()
    if (archives.some((item: any, index: number) => archives.slice(0, index).some((prior: any) => sameWindow(prior.original, item.original)) ||
        pending && sameWindow(pending, item.original))) reject()
    const local = archives.filter((item: any) => item.source === 'local_gateway')
    if (local.some((item: any, index: number) => local.slice(0, index).some((prior: any) => prior.reviewId === item.reviewId ||
        prior.original.deviceId === item.original.deviceId && prior.cardId === item.cardId && prior.outerRequestId === item.outerRequestId))) reject()
    return { version: 2, revision: value.revision, pending, archives }
  }
  return { version: 1, revision: 0, pending: marker(value), archives: [] }
}
function ledgerValue(ledger: any) {
  return ledger.version === 1 ? ledger.pending || ''
    : { v: 2, revision: ledger.revision, pending: ledger.pending, archives: ledger.archives }
}
function copied(value: any) { return JSON.parse(JSON.stringify(value)) }
function localArchiveContext(value: any) {
  exact(value, ['cardId', 'outerRequestId'])
  if (typeof value.cardId !== 'string' || !HEX32.test(value.cardId) || typeof value.outerRequestId !== 'string' || !ID.test(value.outerRequestId)) reject()
  return { cardId: value.cardId, outerRequestId: value.outerRequestId }
}
function localServerReview(value: any) {
  exact(value, ['eligible', 'cardId', 'original', 'status', 'resolution', 'reviewId', 'archivedAt', 'acknowledgements'])
  exact(value.original, ['deviceId', 'requestId', 'windowId', 'networkRequestId'])
  const original = value.original
  if (value.eligible !== false || value.status !== 'unknown' || value.resolution !== 'operator_archived' ||
      typeof value.cardId !== 'string' || !HEX32.test(value.cardId) || typeof value.reviewId !== 'string' || !HEX32.test(value.reviewId) ||
      !Number.isSafeInteger(value.archivedAt) || value.archivedAt < 0 ||
      typeof original.deviceId !== 'string' || !DEVICE_ID.test(original.deviceId) ||
      typeof original.requestId !== 'string' || !ID.test(original.requestId) || typeof original.windowId !== 'string' || !HEX32.test(original.windowId) ||
      typeof original.networkRequestId !== 'string' || !ID.test(original.networkRequestId) ||
      !same(value.acknowledgements, LOCAL_ARCHIVE_CHECKS)) reject()
  return copied(value)
}
function lookupLocalArchive(wxApi: any, context: any) {
  exact(context, ['deviceId', 'cardId', 'outerRequestId', 'windowId', 'networkRequestId', 'reviewId'])
  localArchiveContext({ cardId: context.cardId, outerRequestId: context.outerRequestId })
  if (typeof context.deviceId !== 'string' || !DEVICE_ID.test(context.deviceId) || typeof context.windowId !== 'string' || !HEX32.test(context.windowId) ||
      typeof context.networkRequestId !== 'string' || !ID.test(context.networkRequestId) || typeof context.reviewId !== 'string' || !HEX32.test(context.reviewId)) reject()
  // Read only: absence is not permission to manufacture the original marker
  // on a different client, or to turn a server review into a local archive.
  const ledger = readLedger(wxApi.getStorageSync(STORAGE))
  const found = ledger.archives.find((item: any) => item.source === 'local_gateway' && item.original.deviceId === context.deviceId &&
    item.original.sessionId === context.windowId && item.original.requestId === context.networkRequestId && item.cardId === context.cardId &&
    item.outerRequestId === context.outerRequestId && item.reviewId === context.reviewId)
  return found ? copied(found) : null
}
function receiptQrPacket(value: any, wxApi: any) {
  if (typeof value !== 'string' || value.length > 2048 || !value.startsWith(RECEIPT_QR_PREFIX)) reject()
  const encoded = value.slice(RECEIPT_QR_PREFIX.length)
  if (!/^[A-Za-z0-9_-]{1,2000}$/.test(encoded) || encoded.length % 4 === 1) reject()
  const base64 = encoded.replace(/-/g, '+').replace(/_/g, '/') + '='.repeat((4 - encoded.length % 4) % 4)
  const buffer = wxApi.base64ToArrayBuffer(base64)
  if (Object.prototype.toString.call(buffer) !== '[object ArrayBuffer]' || buffer.byteLength > MAX_FRAME) reject()
  if (wxApi.arrayBufferToBase64(buffer).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '') !== encoded) reject()
  const raw = text(new Uint8Array(buffer)), packet = JSON.parse(raw)
  if (raw !== JSON.stringify(packet)) reject()
  return packet
}
function verifyUnknownReceipt(wxApi: any, invitationQR: any, receiptQR: any, context: any) {
  try {
    exact(context, ['deviceId', 'windowId', 'networkRequestId'])
    if (typeof context.deviceId !== 'string' || !DEVICE_ID.test(context.deviceId) || typeof context.windowId !== 'string' || !HEX32.test(context.windowId) ||
        typeof context.networkRequestId !== 'string' || !ID.test(context.networkRequestId)) reject()
    const invite = parseProvisioningQR(invitationQR)
    if (invite.deviceId !== context.deviceId || invite.sessionId !== context.windowId) reject()
    const result = decodeProvisioningPacket(invite, receiptQrPacket(receiptQR, wxApi), context.networkRequestId, 'configure')
    exact(result, ['status'])
    if (result.status !== 'unknown') reject()
    return true
  } catch (_) { reject() }
}

function createLiveProvisioning(wxApi: any, cameraScan?: (options: any) => void) {
  // A page can keep scanning inside its own camera component. No QR payload is
  // carried across hide/show; native scanning remains the standalone default.
  const scanCode = cameraScan || ((options: any) => wxApi.scanCode(options))
  let visible = false, epoch = 0, listener: any = null, invitation: any = null, network: any = null, prepared: any = null
  let draftVersion = 0, confirmationSequence = 0, pending: any = null, storageGood = true, cancelSocket: any = null, scanResolve: any = null, scanAttempt: any = null
  let transportMode = 'bound', directProof: any = null, proofTimer: any = null, probeAttempt: any = null
  let ledger: any = { version: 1, revision: 0, pending: null, archives: [] }, archiveProof: any = null, archiveIntent: any = null, storageFault = false
  let state: any = { phase: 'idle', busy: false, scanned: false, networkValid: false, canConfigure: false, canQuery: false, status: '', error: '' }
  function android() {
    try { const info = typeof wxApi.getDeviceInfo === 'function' ? wxApi.getDeviceInfo() : wxApi.getSystemInfoSync(); return info.platform === 'android' } catch (_) { return false }
  }
  function getState() {
    const matching = Boolean(visible && invitation && pending && invitation.deviceId === pending.deviceId && invitation.sessionId === pending.sessionId && !state.busy && storageGood && !archiveIntent)
    const compatibilityReady = validProof(directProof, invitation)
    const routeReady = transportMode === 'bound' || compatibilityReady
    const archiveReady = Boolean(validArchiveProof())
    return { ...state, scanned: Boolean(invitation), networkValid: Boolean(network), pending: Boolean(pending),
      archiveReady, canArchive: Boolean(visible && android() && archiveReady && storageGood && !state.busy && !prepared && !archiveIntent && ledger.archives.length < ARCHIVE_LIMIT),
      reviewIdentity: pending ? { deviceId: pending.deviceId, sessionId: pending.sessionId, requestId: pending.requestId } : null,
      archiveCount: ledger.archives.length, lastArchive: ledger.archives.length ? copied(ledger.archives[ledger.archives.length - 1]) : null,
      transportMode, compatibilityReady, probeStatus: state.probeStatus || 'idle', probeHint: state.probeHint || '',
      canProbe: Boolean(visible && android() && invitation && transportMode === 'direct' && storageGood && !state.busy && !prepared && !archiveIntent && (!pending || matching)),
      canConfigure: Boolean(visible && android() && invitation && !archivedWindow(invitation) && network && storageGood && !pending && !state.busy && !prepared && !archiveIntent && routeReady),
      canQuery: Boolean(matching && routeReady), canScanReceipt: Boolean(matching && android()) }
  }
  function emit() { if (visible && listener) listener(getState()) }
  function patch(value: any) { state = { ...state, ...value }; emit() }
  function loadMarker() {
    try {
      if (storageFault) reject()
      ledger = readLedger(wxApi.getStorageSync(STORAGE)); pending = ledger.pending; storageGood = true
    }
    catch (_) { storageGood = false; storageFault = true; state.error = '未决配网存储不可用，禁止发送；请保留现有数据检查，不能清数据绕过。' }
  }
  function writeLedger(next: any) {
    try {
      if (storageFault) reject()
      const checked = readLedger(ledgerValue(next))
      wxApi.setStorageSync(STORAGE, ledgerValue(checked))
      if (!same(readLedger(wxApi.getStorageSync(STORAGE)), checked)) reject()
      ledger = checked; pending = checked.pending; storageGood = true; return true
    }
    catch (_) {
      // A later valid-looking empty read cannot erase the in-memory unknown
      // or silently recover this controller after an unverified storage write.
      storageGood = false; storageFault = true
      patch({ error: '未决配网编号保存失败，本次不发送；请保留现有数据。' }); return false
    }
  }
  function persist(value: any) {
    const next = { ...ledger, pending: value === null ? null : marker(value) }
    if (next.version === 2) next.revision++
    return writeLedger(next)
  }
  function clearOriginal(expected: any) {
    loadMarker()
    if (!storageGood || !same(pending, expected)) return false
    return persist(null)
  }
  function current(expected: number) { return visible && epoch === expected }
  function archivedWindow(invite: any) { return ledger.archives.some((item: any) => sameWindow(item.original, invite)) }
  function validArchiveProof() {
    return Boolean(visible && archiveProof && archiveProof.epoch === epoch && archiveProof.invitation === invitation &&
      pending && same(archiveProof.original, pending) && archiveProof.fingerprint === JSON.stringify(ledger) && state.status === 'unknown')
  }
  function revokeArchive() { archiveProof = null; archiveIntent = null }
  function prepareArchiveProfile(acknowledgements: any, profile: string, context: any = null) {
    if (!getState().canArchive) return null
    let checks: any
    try {
      checks = reviewChecks(acknowledgements, profile === 'local_gateway' ? LOCAL_ARCHIVE_CHECKS : ARCHIVE_CHECKS)
      if (profile === 'local_gateway') context = localArchiveContext(context)
    } catch (_) { return null }
    loadMarker()
    if (!storageGood || !validArchiveProof() || ledger.archives.length >= ARCHIVE_LIMIT) { revokeArchive(); emit(); return null }
    archiveIntent = { token: 'provision-archive-' + (++confirmationSequence), epoch, proof: archiveProof,
      fingerprint: JSON.stringify(ledger), original: copied(pending), checks, profile, context, expires: Date.now() + 30000 }
    emit(); return archiveIntent.token
  }
  function prepareArchive(acknowledgements: any) { return prepareArchiveProfile(acknowledgements, 'usb') }
  function prepareLocalArchive(acknowledgements: any, context: any) { return prepareArchiveProfile(acknowledgements, 'local_gateway', context) }
  function cancelArchive(token: any) { if (archiveIntent && archiveIntent.token === token) { archiveIntent = null; emit() } }
  function consumeArchiveIntent(token: any, profile: string) {
    const intent = archiveIntent
    if (!intent || intent.token !== token || intent.profile !== profile) return null
    archiveIntent = null
    if (!current(intent.epoch) || !android() || state.busy || prepared || intent.proof !== archiveProof || Date.now() >= intent.expires) return null
    loadMarker()
    if (!storageGood || !validArchiveProof() || intent.fingerprint !== JSON.stringify(ledger) || !same(intent.original, pending) ||
        ledger.archives.length >= ARCHIVE_LIMIT) { revokeArchive(); emit(); return null }
    return intent
  }
  function commitArchiveIntent(intent: any, extension: any = {}) {
    const reviewedAt = Date.now()
    if (!Number.isSafeInteger(reviewedAt) || reviewedAt < 0) return false
    const next = { version: 2, revision: ledger.revision + 1, pending: null, archives: [...ledger.archives,
      { original: copied(pending), status: 'unknown', resolution: 'operator_archived', reviewedAt, checks: intent.checks.slice(), ...extension }] }
    if (!writeLedger(next)) { revokeArchive(); return false }
    // Atomic single-key archival is an operator decision, NOT a terminal
    // network outcome or automatic USB/NetworkManager verification. Never
    // reuse the old key/window or dispatch anything after this local write.
    invitation = null; network = null; prepared = null; draftVersion++
    revokeArchive(); clearProof(); transportMode = 'bound'
    patch({ phase: 'archived', status: 'unknown', busy: false, error: '' })
    return true
  }
  function confirmArchive(token: any) {
    const intent = consumeArchiveIntent(token, 'usb')
    return intent ? commitArchiveIntent(intent) : false
  }
  function confirmLocalArchive(token: any, serverReview: any) {
    const intent = consumeArchiveIntent(token, 'local_gateway')
    if (!intent) return false
    try {
      const review = localServerReview(serverReview), original = review.original
      if (review.cardId !== intent.context.cardId || original.requestId !== intent.context.outerRequestId ||
          original.deviceId !== intent.original.deviceId || original.windowId !== intent.original.sessionId ||
          original.networkRequestId !== intent.original.requestId || !same(review.acknowledgements, intent.checks)) reject()
      return commitArchiveIntent(intent, { source: 'local_gateway', reviewId: review.reviewId,
        outerRequestId: original.requestId, cardId: review.cardId })
    } catch (_) { revokeArchive(); emit(); return false }
  }
  function validProof(proof: any, invite: any) {
    return Boolean(visible && transportMode === 'direct' && proof && proof === directProof &&
      proof.epoch === epoch && proof.invitation === invite && Date.now() < proof.expires)
  }
  function clearProof(status = 'idle', hint = '') {
    if (proofTimer !== null) clearTimeout(proofTimer)
    proofTimer = null; directProof = null
    state = { ...state, probeStatus: status, probeHint: hint }
  }
  function setTransportMode(mode: any) {
    if (!visible || !android() || state.busy || !['bound', 'direct'].includes(mode)) return false
    if (mode === transportMode) return true
    transportMode = mode; prepared = null; draftVersion++
    clearProof(); revokeArchive()
    patch({ error: pending ? state.error : '' })
    return true
  }
  function random(length: number): Promise<Uint8Array> {
    return new Promise((resolve, rejectPromise) => {
      let done = false
      const timer = setTimeout(() => { if (!done) { done = true; rejectPromise(sendFailure('random')) } }, 3000)
      const finish = (value: any) => {
        if (done) return
        done = true; clearTimeout(timer)
        try {
          if (!value || Object.prototype.toString.call(value.randomValues) !== '[object ArrayBuffer]' || value.randomValues.byteLength !== length) reject()
          resolve(new Uint8Array(value.randomValues))
        } catch (_) { rejectPromise(sendFailure('random')) }
      }
      try { wxApi.getUserCryptoManager().getRandomValues({ length, success: finish, fail: () => finish(null) }) } catch (_) { finish(null) }
    })
  }
  function wifiBSSID(expected: number, owner?: { cancel: (() => void) | null }): Promise<string> {
    // bindWifi requires the CURRENT phone Wi-Fi's BSSID, not the target SSID
    // or the checkbox. Read only for an explicit send/query/zero-data probe;
    // never store,
    // render, log or transmit this native result as application data.
    return new Promise((resolve, rejectPromise) => {
      let done = false, reading = false
      const timer = setTimeout(() => finish(null, 'wifi_timeout'), 6000)
      const finish = (bssid: string | null, reason = '') => {
        if (done) return
        done = true; clearTimeout(timer)
        if (owner) { if (owner.cancel === cancel) owner.cancel = null }
        else if (cancelSocket === cancel) cancelSocket = null
        if (bssid) resolve(bssid)
        else rejectPromise(sendFailure(reason))
      }
      const cancel = () => finish(null, 'context_changed')
      if (owner) owner.cancel = cancel
      else cancelSocket = cancel
      const nativeFailure = (value: any, fallback: string) => {
        // Fixed codes only: native errMsg may contain private network details.
        const reason = value && value.errCode === 12005 ? 'wifi_off'
          : value && value.errCode === 12006 ? 'wifi_location_off'
          : value && value.errCode === 12007 ? 'wifi_permission' : fallback
        finish(null, reason)
      }
      const read = () => {
        if (done || reading) return
        if (!current(expected)) { cancel(); return }
        reading = true
        try {
          wxApi.getConnectedWifi({ partialInfo: false,
            success: (value: any) => {
              if (done) return
              if (!current(expected)) { cancel(); return }
              const bssid = value && value.wifi && value.wifi.BSSID
              if (typeof bssid !== 'string' || !/^(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$/.test(bssid) ||
                  ['00:00:00:00:00:00', 'ff:ff:ff:ff:ff:ff', '02:00:00:00:00:00'].includes(bssid.toLowerCase())) {
                finish(null, 'wifi_bssid'); return
              }
              const ssid = value && value.wifi && value.wifi.SSID
              if (typeof ssid !== 'string' || !ssid || /^(?:<unknown ssid>|unknown ssid)$/i.test(ssid)) {
                finish(null, 'wifi_hotspot_unavailable'); return
              }
              // A current-network check, not device authentication. The name
              // must match our existing generated AP shape; AES/QR identity
              // checks still apply, and native network details stay private.
              if (ssid.length !== 13 || !/^AI-CAT-[A-F0-9]{6}$/.test(ssid)) {
                finish(null, 'wifi_hotspot_other'); return
              }
              finish(bssid)
            }, fail: (error: any) => nativeFailure(error, 'wifi_read') })
        } catch (_) { finish(null, 'wifi_read') }
      }
      if (!current(expected) || !android()) { cancel(); return }
      if (typeof wxApi.startWifi !== 'function' || typeof wxApi.getConnectedWifi !== 'function') { finish(null, 'wifi_api'); return }
      try { wxApi.startWifi({ success: read, fail: (error: any) => nativeFailure(error, 'wifi_init') }) }
      catch (_) { finish(null, 'wifi_init') }
    })
  }
  function connectOnly(expected: number): Promise<void> {
    // Compatibility probe: one fixed TCP connect followed by close. There is
    // deliberately no write(), frame, QR/key, request ID or storage operation.
    return new Promise((resolve, rejectPromise) => {
      let socket: any = null, done = false, started = false
      const timer = setTimeout(() => finish(sendFailure('direct_probe_timeout')), 6000)
      const finish = (error: any = null) => {
        if (done) return
        done = true; clearTimeout(timer)
        if (cancelSocket === cancel) cancelSocket = null
        try { if (socket) socket.close() } catch (_) {}
        if (error) rejectPromise(error)
        else resolve()
      }
      const cancel = () => finish(sendFailure('context_changed'))
      cancelSocket = cancel
      try {
        if (!current(expected) || !android() || transportMode !== 'direct') { cancel(); return }
        socket = wxApi.createTCPSocket()
        socket.onError((error: any) => finish(sendFailure('direct_probe', false, socketCode(error))))
        socket.onClose(() => finish(sendFailure('direct_probe')))
        socket.onConnect(() => {
          if (!started || done) return
          if (!current(expected) || transportMode !== 'direct') { cancel(); return }
          finish()
        })
        if (done || !current(expected)) { if (!done) cancel(); return }
        started = true
        socket.connect({ address: AP_ADDRESS, port: TCP_PORT, timeout: 5 })
      } catch (error) { finish(sendFailure('direct_probe', false, socketCode(error))) }
    })
  }
  async function probe() {
    if (!getState().canProbe) return
    const expected = epoch, invite = invitation, attempt = {}, previousPhase = state.phase
    probeAttempt = attempt
    clearProof('testing', '仅尝试连接固定本地端口，不发送配网资料；不验证设备身份。')
    patch({ busy: true, phase: 'probing' })
    const stillCurrent = () => current(expected) && probeAttempt === attempt && transportMode === 'direct' && invitation === invite
    try {
      const before = await wifiBSSID(expected)
      if (!stillCurrent()) return
      await connectOnly(expected)
      if (!stillCurrent()) return
      const after = await wifiBSSID(expected)
      if (!stillCurrent()) return
      if (before.toLowerCase() !== after.toLowerCase()) throw sendFailure('direct_probe_changed')
      const proof = { epoch: expected, invitation: invite, bssid: after.toLowerCase(), expires: Date.now() + PROBE_VALID_MS }
      directProof = proof
      state = { ...state, probeStatus: 'reachable',
        probeHint: '本次自检仅确认端口可连接，不验证设备身份，也不代表配网成功；60 秒内有效，发送前仍会核对热点。' }
      proofTimer = setTimeout(() => {
        if (current(expected) && directProof === proof) {
          clearProof('expired', '本次连接自检已过期，请重新手动自检；原配网请求状态不变。')
          emit()
        }
      }, PROBE_VALID_MS)
    } catch (error) {
      if (stillCurrent()) clearProof('failed', '本次连接自检未通过：' + failureDetail(error) + ' 原配网请求状态不变。')
    } finally {
      if (stillCurrent()) { probeAttempt = null; patch({ busy: false, phase: previousPhase }) }
    }
  }
  async function exchange(invite: any, requestId: string, kind: string, body: any, expected: number,
                          mode = 'bound', proof: any = null) {
    const bssid = await wifiBSSID(expected)
    if (mode !== transportMode || !current(expected)) throw sendFailure('context_changed')
    if (mode === 'direct' && (!validProof(proof, invite) || proof.bssid !== bssid.toLowerCase())) {
      clearProof('expired', '热点或自检状态已变化，请重新手动自检；原配网请求状态不变。')
      throw sendFailure('direct_probe_changed')
    }
    let outgoing: Uint8Array
    try {
      const tail = await random(11), nonce = new Uint8Array(12); nonce.set(tail, 1)
      if (!current(expected)) reject()
      outgoing = frame(encodeProvisioningPacket(invite, requestId, kind, body, nonce))
    } catch (error: any) { throw sendFailure(error && error.reason === 'random' ? 'random' : 'encryption') }
    if (mode !== transportMode || (mode === 'direct' && !validProof(proof, invite))) throw sendFailure('direct_probe_changed')
    return new Promise<any>((resolve, rejectPromise) => {
      let socket: any = null, done = false, written = false, writing = false, buffer = new Uint8Array(0), connected = false
      // Keep the live socket's cancellation installed while a direct write's
      // final Wi-Fi read is pending. Hide/timeout must cancel BOTH operations.
      const readOwner: { cancel: (() => void) | null } = { cancel: null }
      const timer = setTimeout(() => finish(null, mode === 'direct' || connected ? 'tcp_connect_timeout' : 'wifi_bind_timeout'), 10000)
      const finish = (result: any, reason = '', nativeCode: number | null = null) => {
        if (done) return
        done = true; clearTimeout(timer); if (cancelSocket === cancel) cancelSocket = null
        if (readOwner.cancel) readOwner.cancel()
        try { if (socket) socket.close() } catch (_) {}
        if (result) resolve(result)
        else rejectPromise(sendFailure(reason, written, nativeCode))
      }
      const cancel = () => finish(null); cancelSocket = cancel
      let setupReason = 'socket_create'
      try {
        if (!current(expected) || !android()) { finish(null, 'context_changed'); return }
        socket = wxApi.createTCPSocket()
        if (!socket || (mode === 'bound' && (typeof socket.bindWifi !== 'function' || typeof socket.onBindWifi !== 'function'))) { finish(null, 'socket_create'); return }
        setupReason = 'socket_events'
        socket.onError((error: any) => finish(null, mode === 'direct' || connected ? 'tcp_connect' : 'wifi_bind_event', socketCode(error)))
        socket.onClose(() => { if (!done) finish(null, mode === 'direct' || connected ? 'tcp_connect_closed' : 'wifi_bind_closed') })
        const connect = () => {
          if (done || !current(expected) || connected) return
          if (mode !== transportMode || (mode === 'direct' && !validProof(proof, invite))) { finish(null, 'direct_probe_changed'); return }
          connected = true
          try { socket.connect({ address: AP_ADDRESS, port: TCP_PORT, timeout: 5 }) } catch (error) { finish(null, 'tcp_connect', socketCode(error)) }
        }
        if (mode === 'bound') socket.onBindWifi(connect)
        socket.onConnect(() => {
          if (done || !current(expected) || !connected || writing || written) return
          writing = true
          const write = () => {
            if (done || !current(expected)) return
            if (mode !== transportMode || (mode === 'direct' && !validProof(proof, invite))) { finish(null, 'direct_probe_changed'); return }
            written = true
            try { socket.write(outgoing.buffer) } catch (_) { finish(null) }
          }
          if (mode === 'bound') { write(); return }
          // System routing can change while randomness or TCP connect awaits.
          // Re-read immediately before any direct-mode bytes, not just at the
          // start of the exchange. This is an observed-network guard, not an
          // OS-level Wi-Fi binding or a guarantee against a subsequent switch.
          wifiBSSID(expected, readOwner).then(latest => {
            if (done || !current(expected)) return
            if (!validProof(proof, invite) || proof.bssid !== latest.toLowerCase()) {
              clearProof('expired', '热点或自检状态已变化，请重新手动自检；原配网请求状态不变。')
              finish(null, 'direct_probe_changed'); return
            }
            write()
          }, error => finish(null, error && error.reason ? error.reason : 'wifi_read'))
        })
        socket.onMessage((reply: any) => {
          if (done) return
          try {
            if (!current(expected) || !reply || !reply.remoteInfo || reply.remoteInfo.address !== AP_ADDRESS || reply.remoteInfo.port !== TCP_PORT ||
                Object.prototype.toString.call(reply.message) !== '[object ArrayBuffer]') reject()
            const chunk = new Uint8Array(reply.message)
            if (buffer.length + chunk.length > MAX_FRAME + 4) reject()
            const next = new Uint8Array(buffer.length + chunk.length); next.set(buffer); next.set(chunk, buffer.length); buffer = next
            if (buffer.length < 4) return
            const length = new DataView(buffer.buffer).getUint32(0, false)
            if (length < 1 || length > MAX_FRAME || buffer.length > length + 4) reject()
            if (buffer.length < length + 4) return
            const raw = text(buffer.slice(4)), packet = JSON.parse(raw)
            if (raw !== JSON.stringify(packet)) reject()
            const result = decodeProvisioningPacket(invite, packet, requestId, kind)
            exact(result, ['status'])
            if (!STATUSES.includes(result.status)) reject()
            finish(result)
          } catch (_) { finish(null) }
        })
        if (done || !current(expected)) { if (!done) finish(null, 'context_changed'); return }
        if (mode === 'bound') {
          setupReason = 'wifi_bind_call'
          socket.bindWifi({ BSSID: bssid })
        } else {
          setupReason = 'tcp_connect'
          connect()
        }
      } catch (error) { finish(null, setupReason, socketCode(error)) }
    })
  }
  function hide() {
    visible = false; epoch++; listener = null; prepared = null; invitation = null; network = null
    transportMode = 'bound'; probeAttempt = null; clearProof(); revokeArchive()
    if (cancelSocket) cancelSocket()
    if (scanResolve) scanResolve(); scanResolve = null; scanAttempt = null
    state = { phase: 'idle', busy: false, scanned: false, networkValid: false, canConfigure: false, canQuery: false, status: '', error: '' }
  }
  function show(callback: any) { hide(); visible = true; listener = callback; loadMarker(); emit() }
  function scan() {
    if (!visible || state.busy) return Promise.resolve()
    if (!android()) { patch({ phase: 'unavailable', error: '真实配网需要安卓微信真机；电脑模拟器只可预览。' }); return Promise.resolve() }
    const expected = epoch, attempt = {}; scanAttempt = attempt
    invitation = null; network = null; prepared = null; draftVersion++
    clearProof(); revokeArchive()
    patch({ phase: 'scanning', busy: true, error: '' })
    return new Promise<void>(resolve => {
      scanResolve = resolve
      const finish = (value: any) => {
        if (!current(expected) || scanAttempt !== attempt) { resolve(); return }
        scanResolve = null; scanAttempt = null
        try {
          if (!value || value.scanType !== 'QR_CODE') reject()
          invitation = parseProvisioningQR(value.result)
          loadMarker()
          if (!storageGood || archivedWindow(invitation)) reject()
          patch({ phase: 'scanned', busy: false, error: pending ? invitation.deviceId === pending.deviceId && invitation.sessionId === pending.sessionId
            ? '有未决配网请求，仅核对原编号，不重新发送Wi-Fi信息。' : '另一个配网窗口仍有未决请求，请扫描上次原二维码核对。' : '' })
        } catch (_) {
          invitation = null
          patch({ phase: 'error', busy: false, error: '二维码未通过校验、窗口已归档或扫码已取消；本次扫码未发起新的网络配置。' +
            (pending ? '原配网请求结果仍待核对，请保留原编号。' : '') })
        }
        resolve()
      }
      try { scanCode({ onlyFromCamera: true, scanType: ['qrCode'], success: finish, fail: () => finish(null) }) } catch (_) { finish(null) }
    })
  }
  function setNetwork(ssid: any, password: any) {
    if (!visible || state.busy) return
    try {
      const next = verifyProvisioningNetwork({ ssid, password, security: 'wpa2-personal' })
      if (same(next, network)) return
      network = next; draftVersion++; prepared = null; patch({ error: '' })
    } catch (_) { network = null; draftVersion++; prepared = null; patch({ error: 'Wi-Fi名称需1～32 UTF-8字节，密码需8～63字节或64位十六进制；不支持控制字符。' }) }
  }
  function scanReceipt() {
    if (visible && !state.busy) revokeArchive()
    if (!getState().canScanReceipt) return Promise.resolve()
    const expected = epoch, invite = invitation, original = pending, attempt = {}
    revokeArchive()
    scanAttempt = attempt
    patch({ phase: 'scanning-receipt', busy: true, error: '' })
    return new Promise<void>(resolve => {
      scanResolve = resolve
      const finish = (value: any) => {
        if (!current(expected) || scanAttempt !== attempt) { resolve(); return }
        scanResolve = null; scanAttempt = null
        try {
          if (invitation !== invite || !same(pending, original) || !value || value.scanType !== 'QR_CODE') reject()
          const packet = receiptQrPacket(value.result, wxApi)
          const result = decodeProvisioningPacket(invite, packet, original.requestId, 'configure')
          exact(result, ['status'])
          if (!STATUSES.includes(result.status)) reject()
          const terminal = ['connected', 'failed', 'not_sent'].includes(result.status)
          if (terminal && !clearOriginal(original)) {
            patch({ phase: 'error', busy: false, status: result.status, error: '回执已验证，但原未决标记未能安全解除；未清除其他请求，请保留原编号。' })
          } else {
            if (!terminal) {
              loadMarker()
              if (!storageGood || !same(pending, original)) reject()
            }
            if (result.status === 'unknown') {
              archiveProof = { epoch: expected, invitation: invite, original: copied(original), fingerprint: JSON.stringify(ledger) }
            }
            patch({ phase: result.status === 'connected' ? 'connected' : 'pending', busy: false, status: result.status,
              error: terminal ? '' : '已核对原配网回执，结果仍待确认；保留原编号，不重新发送Wi-Fi信息。' })
          }
        } catch (_) { patch({ phase: 'error', busy: false, status: 'unknown', error: '收尾回执未通过校验或扫码已取消；保留原编号，不自动重新配网。' }) }
        resolve()
      }
      try { scanCode({ onlyFromCamera: true, scanType: ['qrCode'], success: finish, fail: () => finish(null) }) } catch (_) { finish(null) }
    })
  }
  function prepare() {
    if (!visible || !android()) return null
    loadMarker()
    if (!getState().canConfigure) return null
    try { cipher(); if (typeof wxApi.getUserCryptoManager !== 'function' || typeof wxApi.createTCPSocket !== 'function' ||
      typeof wxApi.startWifi !== 'function' || typeof wxApi.getConnectedWifi !== 'function') reject() }
    catch (_) { patch({ phase: 'unavailable', error: '当前微信运行环境不支持安全 Wi-Fi 配网，请升级安卓微信；未发送网络信息。' }); return null }
    prepared = { token: 'provision-confirm-' + (++confirmationSequence), epoch, version: draftVersion, invitation,
      mode: transportMode, proof: transportMode === 'direct' ? directProof : null, expires: Date.now() + 30000 }
    emit(); return prepared.token
  }
  function cancel(token: any) { if (prepared && prepared.token === token) { prepared = null; emit() } }
  async function confirm(token: any) {
    const intent = prepared
    if (!intent || intent.token !== token) return
    prepared = null
    if (!current(intent.epoch) || invitation !== intent.invitation || draftVersion !== intent.version || Date.now() >= intent.expires) { patch({ error: '本次确认已失效，网络信息未发送；请重新确认。' }); return }
    if (intent.mode !== transportMode || (intent.mode === 'direct' && !validProof(intent.proof, invitation))) {
      patch({ error: notSentMessage(sendFailure('direct_probe_required')) }); return
    }
    const invite = invitation, credentials = network, expected = epoch
    patch({ busy: true, phase: 'preparing', error: '' })
    let original: any = null
    try {
      const entropy = await random(16)
      if (!current(expected) || invitation !== invite || draftVersion !== intent.version) return
      if (intent.mode !== transportMode || (intent.mode === 'direct' && !validProof(intent.proof, invite))) {
        patch({ error: notSentMessage(sendFailure('direct_probe_changed')) }); return
      }
      loadMarker(); if (!storageGood || pending || archivedWindow(invite)) { patch({ error: '有未决配网请求或该窗口已归档；仅核对原编号，新配置需要新窗口。' }); return }
      original = { v: 1, deviceId: invite.deviceId, sessionId: invite.sessionId, requestId: 'wifi_' + hex(entropy) }
      if (!persist(original)) return
      const result = await exchange(invite, original.requestId, 'configure', credentials, expected, intent.mode, intent.proof)
      if (!current(expected)) return
      network = null
      if (['connected', 'failed', 'not_sent'].includes(result.status)) clearOriginal(original)
      patch({ phase: result.status === 'connected' ? 'connected' : 'pending', status: result.status, error: '' })
    } catch (error: any) {
      if (current(expected)) {
        if (original && error && error.dispatched === false) clearOriginal(original)
        patch({ phase: 'error', status: original && (!error || error.dispatched !== false) ? 'unknown' : 'not_sent',
          error: original && (!error || error.dispatched !== false) ? '配网结果未知，只能核对原请求；不会自动再次发送Wi-Fi信息。' : notSentMessage(error) })
      }
    } finally { if (current(expected)) { if (original) network = null; patch({ busy: false }) } }
  }
  async function query() {
    if (visible && !state.busy) revokeArchive()
    if (!getState().canQuery) return
    const expected = epoch, invite = invitation, original = pending, mode = transportMode, proof = directProof
    revokeArchive()
    patch({ busy: true, phase: 'checking', error: '' })
    try {
      const result = await exchange(invite, original.requestId, 'status', {}, expected, mode, proof)
      if (!current(expected)) return
      if (['connected', 'failed', 'not_sent'].includes(result.status)) clearOriginal(original)
      patch({ phase: result.status === 'connected' ? 'connected' : 'pending', status: result.status })
    } catch (_) { if (current(expected)) patch({ phase: 'error', status: 'unknown', error: '原配网结果尚未核实；保留原编号，不自动重配网络。' }) }
    finally { if (current(expected)) patch({ busy: false }) }
  }
  return { show, hide, getState, scan, scanReceipt, setNetwork, prepare, cancel, confirm, query, setTransportMode, probe,
    prepareArchive, cancelArchive, confirmArchive, prepareLocalArchive, confirmLocalArchive }
}
module.exports = { createLiveProvisioning, parseProvisioningQR, verifyProvisioningNetwork, encodeProvisioningPacket, decodeProvisioningPacket, provisioningFrame: frame,
  provisioningProtocol: PROTOCOL, provisioningAddress: AP_ADDRESS, provisioningPort: TCP_PORT, lookupLocalArchive, verifyUnknownReceipt }
