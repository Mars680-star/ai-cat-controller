// Device-card pairing is local physical-possession authorization, NOT wx.login.
// Secret card material and decrypted bearer exist only inside these closures.
const { createGatewayClientCore } = require('./cloud-auth-client')
const PROTOCOL = 'aicat-local-v1', PORT = 8789
const CARD = /^AICAT-LOCAL:1:(dev_[0-9a-f]{32}):([0-9a-f]{32}):([0-9a-f]{64})$/
const HEX32 = /^[0-9a-f]{32}$/
const MAX_PLAIN_RESPONSE = 2 * 1024 * 1024
const GET_ROUTE = /^\/v1\/(?:session|pets(?:\/[A-Za-z0-9_-]{1,128}\/(?:status|motion(?:\/history)?|dashboard|intimacy|growth|preferences(?:\/requests\/[A-Za-z0-9_.:-]{1,64})?|dialog(?:\/control|\/requests\/[A-Za-z0-9_.:-]{1,64}|\/conversations(?:\/since\/[0-9a-f]{64}|\/[A-Za-z0-9_-]{1,128}(?:\/since\/[0-9a-f]{64})?)?)?))?)$/
const POST_ROUTE = /^\/(?:local\/(?:pair|provision\/(?:prepare|current|resume|status|receipt|archive-status|archive-prepare|archive-confirm))|v1\/(?:auth\/logout|pets\/[A-Za-z0-9_-]{1,128}\/(?:motion\/(?:execute|stop)|interactions|dialog\/commands|preferences\/commands)))$/
const REVIEW_ACKS = ['window-ended', 'network-stable', 'device-idle', 'accept-unknown']
function invalid(): never { throw new Error('本地配对资料或加密响应未通过校验') }
function exact(value: any, fields: string[]) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== fields.length || fields.some(field => !Object.prototype.hasOwnProperty.call(value, field))) invalid()
}
function utf8(value: string): Uint8Array {
  const encoded = encodeURIComponent(value), result = []
  for (let i = 0; i < encoded.length; i++) {
    if (encoded[i] === '%') { result.push(parseInt(encoded.slice(i + 1, i + 3), 16)); i += 2 }
    else result.push(encoded.charCodeAt(i))
  }
  return new Uint8Array(result)
}
function text(value: Uint8Array) { return decodeURIComponent(Array.from(value, byte => '%' + byte.toString(16).padStart(2, '0')).join('')) }
function hex(value: Uint8Array) { return Array.from(value, byte => byte.toString(16).padStart(2, '0')).join('') }
function unhex(value: string) {
  if (typeof value !== 'string' || !/^(?:[0-9a-f]{2})+$/.test(value)) invalid()
  return new Uint8Array((value.match(/../g) as string[]).map(byte => parseInt(byte, 16)))
}
function serverExpiry(value: any): number {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(value)) invalid()
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp) || new Date(timestamp).toISOString().slice(0, 19) !== value.slice(0, 19)) invalid()
  return timestamp
}
function localRuntimeAvailable(wxApi: any) {
  try {
    const info = wxApi.getAccountInfoSync()
    const device = typeof wxApi.getDeviceInfo === 'function' ? wxApi.getDeviceInfo() : wxApi.getSystemInfoSync()
    return info.miniProgram.envVersion === 'develop' && device.platform === 'android'
  } catch (_) { return false }
}
function verifyLocalAddress(value: any): string {
  if (typeof value !== 'string' || value.length > 15 || !/^[0-9]+(?:\.[0-9]+){3}$/.test(value)) invalid()
  const parts = value.split('.').map(Number)
  if (parts.some((part, index) => !Number.isInteger(part) || part > 255 || String(part) !== value.split('.')[index])) invalid()
  if (!(parts[0] === 10 || parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31 || parts[0] === 192 && parts[1] === 168) || parts[3] === 0 || parts[3] === 255) invalid()
  return value
}
function cardParts(value: any) {
  if (typeof value !== 'string' || value.length > 192) invalid()
  const match = CARD.exec(value)
  if (!match || match[0] !== value) invalid()
  return { deviceId: match[1], cardId: match[2], keyHex: match[3] }
}
function localCardIdentity(value: any) { const card = cardParts(value); return { deviceId: card.deviceId, cardId: card.cardId } }

function createLocalDeviceClient(wxApi: any, cardQR: string, address: string) {
  if (!localRuntimeAvailable(wxApi)) invalid()
  const host = verifyLocalAddress(address), card = cardParts(cardQR), key = unhex(card.keyHex)
  card.keyHex = ''
  const origin = 'http://' + host + ':' + PORT
  const expectedUser = 'loc_' + card.deviceId.slice(4) + '_' + card.cardId
  const expectedPet = 'lp_' + card.deviceId.slice(4)
  let closed = false, expiresAt = '', pairLocalExpiry = 0, pairServerExpiry = 0
  const requests = new Set<any>(), usedChallenges = new Set<string>(), usedNonces = new Set<string>(), usedIds = new Set<string>()
  const diagnostics = new WeakMap<object, string>()
  function nativeFailure(error: any): string {
    // Consume only a bounded string for a fixed category. Raw native details
    // may include private origins or bodies and never leave this closure.
    let message = ''
    try { if (error && typeof error.errMsg === 'string') message = error.errMsg.slice(0, 1024).toLowerCase() } catch (_) {}
    if (/not in (?:the )?domain list|url not in domain|不在合法域名/.test(message)) return 'LOCAL_NATIVE_DOMAIN'
    if (/timeout|timed out/.test(message)) return 'LOCAL_NATIVE_TIMEOUT'
    if (/permission denied|privacy permission|authorize denied|权限拒绝/.test(message)) return 'LOCAL_NATIVE_PERMISSION'
    return 'LOCAL_NATIVE_NETWORK'
  }
  function cipher() {
    if (closed || typeof (globalThis as any).BigInt !== 'function' || typeof (DataView.prototype as any).setBigUint64 !== 'function') invalid()
    const gcm = require('../vendor/noble-ciphers-2.4.0/aes').gcm
    if (typeof gcm !== 'function') invalid()
    return gcm
  }
  function aad(direction: string, challenge: any, requestId: string) {
    return utf8(JSON.stringify([PROTOCOL, direction, card.deviceId, card.cardId, challenge.challengeId, requestId]))
  }
  function verifyProvisionMetadata(value: any, invitation: boolean, requestId?: string) {
    const fields = ['requestId', 'deviceId', 'windowId', 'status', 'apName', 'expiresAt', 'networkRequestId', 'receiptQr']
    exact(value, invitation ? [...fields, 'invitation'] : fields)
    if (typeof value.requestId !== 'string' || !/^[A-Za-z0-9_.:-]{1,64}$/.test(value.requestId) || requestId !== undefined && value.requestId !== requestId ||
        value.deviceId !== card.deviceId || typeof value.windowId !== 'string' || !HEX32.test(value.windowId) ||
        !['prepared', 'opening', 'open', 'accepted', 'connecting', 'connected', 'failed', 'unknown', 'not_sent'].includes(value.status) ||
        typeof value.apName !== 'string' || !/^AI-CAT-[A-F0-9]{6}$/.test(value.apName) || typeof value.expiresAt !== 'number' || !Number.isFinite(value.expiresAt) || value.expiresAt < 0 ||
        value.networkRequestId !== null && (typeof value.networkRequestId !== 'string' || !/^[A-Za-z0-9_.:-]{1,64}$/.test(value.networkRequestId)) ||
        value.receiptQr !== null && (typeof value.receiptQr !== 'string' || value.receiptQr.length > 2048 || !/^AICAT-PROVISION-RECEIPT:1:[A-Za-z0-9_-]+$/.test(value.receiptQr))) invalid()
    if (invitation) {
      const parsed = /^AICAT-PROVISION:1:(dev_[0-9a-f]{32}):([0-9a-f]{32}):([0-9a-f]{64})$/.exec(value.invitation)
      if (!parsed || parsed[0] !== value.invitation || parsed[1] !== card.deviceId || parsed[2] !== value.windowId) invalid()
    }
    // The signed device deadline may use an unsynchronised offline RTC. Map
    // using the fixed offset authenticated at pair time, never "now + window"
    // on a query: refreshing must not extend the original network window.
    const deadline = (value.expiresAt * 1000 + pairLocalExpiry - pairServerExpiry) / 1000
    if (!Number.isFinite(deadline) || deadline < 0) invalid()
    return { ...value, expiresAt: deadline }
  }
  function reviewOriginal(value: any, body: any) {
    exact(value, ['deviceId', 'requestId', 'windowId', 'networkRequestId'])
    if (value.deviceId !== card.deviceId || value.requestId !== body.requestId ||
        typeof value.windowId !== 'string' || !HEX32.test(value.windowId) ||
        typeof value.networkRequestId !== 'string' || !/^[A-Za-z0-9_.:-]{1,64}$/.test(value.networkRequestId) ||
        body.windowId !== undefined && value.windowId !== body.windowId ||
        body.networkRequestId !== undefined && value.networkRequestId !== body.networkRequestId) invalid()
  }
  function verifyArchive(value: any, route: string, body: any) {
    if (route.endsWith('/archive-prepare')) {
      exact(value, ['reviewToken', 'reviewId', 'expiresAt', 'cardId', 'original'])
      if (value.cardId !== card.cardId || typeof value.reviewToken !== 'string' || !/^[A-Za-z0-9_-]{32,128}$/.test(value.reviewToken) ||
          typeof value.reviewId !== 'string' || !HEX32.test(value.reviewId) || typeof value.expiresAt !== 'number' || !Number.isFinite(value.expiresAt)) invalid()
      reviewOriginal(value.original, body)
      return { ...value, expiresAt: (value.expiresAt * 1000 + pairLocalExpiry - pairServerExpiry) / 1000 }
    }
    exact(value, ['eligible', 'cardId', 'original', 'status', 'resolution', 'reviewId', 'archivedAt', 'acknowledgements'])
    reviewOriginal(value.original, body)
    if (typeof value.eligible !== 'boolean' || value.cardId !== card.cardId || value.status !== 'unknown' ||
        !['pending', 'operator_archived'].includes(value.resolution) || !Array.isArray(value.acknowledgements)) invalid()
    if (value.resolution === 'pending') {
      if (value.reviewId !== null || value.archivedAt !== null || value.acknowledgements.length) invalid()
    } else if (value.eligible || typeof value.reviewId !== 'string' || !HEX32.test(value.reviewId) ||
        !Number.isSafeInteger(value.archivedAt) || value.archivedAt < 0 ||
        JSON.stringify(value.acknowledgements) !== JSON.stringify(REVIEW_ACKS)) invalid()
    if (route.endsWith('/archive-confirm') && value.resolution !== 'operator_archived') invalid()
    return value
  }
  function random(length: number): Promise<Uint8Array> {
    return new Promise((resolve, reject) => {
      let done = false
      const timer = setTimeout(() => finish(null), 3000)
      function finish(value: any) {
        if (done) return
        done = true; clearTimeout(timer)
        if (!closed && value && Object.prototype.toString.call(value.randomValues) === '[object ArrayBuffer]' && value.randomValues.byteLength === length) resolve(new Uint8Array(value.randomValues))
        else reject(new Error('安全随机数不可用'))
      }
      try { wxApi.getUserCryptoManager().getRandomValues({ length, success: finish, fail: () => finish(null) }) } catch (_) { finish(null) }
    })
  }
  function wire(options: any, method: string, path: string, body: any, run: any): Promise<any> {
    return new Promise((resolve, reject) => {
      if (closed || run.cancelled) { reject(new Error('本地请求已取消')); return }
      let settled = false, task: any = null
      const finish = (ok: boolean, value: any) => {
        if (settled) return
        settled = true; run.tasks.delete(abort)
        if (ok) resolve(value); else reject(new Error('本地通信未完成'))
      }
      const abort = () => { try { if (task) task.abort() } catch (_) {}; finish(false, null) }
      run.tasks.add(abort)
      try {
        task = wxApi.request({ url: origin + path, method, header: { 'content-type': 'application/json' }, timeout: options.timeout,
          ...(body === undefined ? {} : { data: body }),
          success(result: any) { if (closed || run.cancelled) finish(false, null); else finish(true, result) },
          fail(error: any) { run.diagnostic = nativeFailure(error); finish(false, null) } })
        if (run.cancelled) abort()
      } catch (error) { run.diagnostic = nativeFailure(error); finish(false, null) }
    })
  }
  function request(options: any) {
    const run: any = { cancelled: false, tasks: new Set<any>(), diagnostic: 'LOCAL_REQUEST_INVALID' }
    requests.add(run)
    const abort = () => { run.cancelled = true; for (const cancel of Array.from(run.tasks) as any[]) cancel() }
    ;(async () => {
      if (closed) invalid()
      if (options.method === 'GET' && options.url === '/health') {
        run.diagnostic = 'LOCAL_NATIVE_NETWORK'
        const response = await wire(options, 'GET', '/local/health', undefined, run)
        if (!run.cancelled) options.success(response)
        return
      }
      if (!(options.method === 'GET' ? GET_ROUTE.test(options.url) : options.method === 'POST' && POST_ROUTE.test(options.url))) invalid()
      if (options.url.startsWith('/local/provision/')) {
        const fields = options.url.endsWith('/archive-confirm') ? ['requestId', 'windowId', 'networkRequestId', 'reviewToken', 'acknowledgements', 'confirmation']
          : options.url.endsWith('/archive-prepare') ? ['requestId', 'windowId', 'networkRequestId', 'acknowledgements']
          : options.url.endsWith('/current') ? [] : options.url.endsWith('/prepare') ? ['requestId', 'confirmation'] : ['requestId']
        exact(options.data, fields)
        if (!options.url.endsWith('/current') && (typeof options.data.requestId !== 'string' || !/^[A-Za-z0-9_.:-]{1,64}$/.test(options.data.requestId))) invalid()
        if (options.url.endsWith('/prepare') && options.data.confirmation !== 'open-local-provisioning') invalid()
        if (options.url.endsWith('/archive-prepare') || options.url.endsWith('/archive-confirm')) {
          if (host === '10.47.0.1' || typeof options.data.windowId !== 'string' || !HEX32.test(options.data.windowId) ||
              typeof options.data.networkRequestId !== 'string' || !/^[A-Za-z0-9_.:-]{1,64}$/.test(options.data.networkRequestId) ||
              JSON.stringify(options.data.acknowledgements) !== JSON.stringify(REVIEW_ACKS)) invalid()
        }
        if (options.url.endsWith('/archive-confirm') && (options.data.confirmation !== 'archive-unknown-provisioning' ||
            typeof options.data.reviewToken !== 'string' || !/^[A-Za-z0-9_-]{32,128}$/.test(options.data.reviewToken))) invalid()
      }
      const scopedPet = /^\/v1\/pets\/([^/]+)/.exec(options.url)
      if (scopedPet && scopedPet[1] !== expectedPet) invalid()
      if (usedChallenges.size >= 4096 || usedIds.size >= 4096) invalid()
      run.diagnostic = 'LOCAL_CRYPTO'
      cipher() // Check the required runtime before contacting the device.
      // Client freshness must be bound to an authenticated challenge BEFORE
      // encrypting under this long-lived card key. An unauthenticated nonce
      // could otherwise be replayed after a new scan/closure to reuse GCM IVs.
      run.diagnostic = 'LOCAL_RANDOM'
      const requestId = hex(await random(16))
      if (run.cancelled || closed || usedIds.has(requestId)) invalid()
      usedIds.add(requestId)
      const acquired = Date.now()
      run.diagnostic = 'LOCAL_NATIVE_NETWORK'
      const response = await wire(options, 'GET', '/local/v1/challenge/' + requestId, undefined, run)
      run.diagnostic = 'LOCAL_CHALLENGE_HTTP'
      if (run.cancelled || closed || response.statusCode !== 200) invalid()
      run.diagnostic = 'LOCAL_CHALLENGE_AUTH'
      exact(response.data, ['success', 'data']); if (response.data.success !== true) invalid()
      const challenge = response.data.data
      exact(challenge, ['v', 'deviceId', 'cardId', 'challengeId', 'requestId', 'requestNonce', 'ttlSeconds', 'proof'])
      if (challenge.deviceId !== card.deviceId || challenge.cardId !== card.cardId) { run.diagnostic = 'LOCAL_CARD_MISMATCH'; invalid() }
      if (challenge.v !== 1 ||
          typeof challenge.challengeId !== 'string' || !HEX32.test(challenge.challengeId) ||
          typeof challenge.requestNonce !== 'string' || !/^00[0-9a-f]{22}$/.test(challenge.requestNonce) || challenge.ttlSeconds !== 30 ||
          challenge.requestId !== requestId || typeof challenge.proof !== 'string' || !/^[0-9a-f]{32}$/.test(challenge.proof) ||
          usedChallenges.has(challenge.challengeId) || usedNonces.has(challenge.requestNonce)) invalid()
      const challengeAAD = utf8(JSON.stringify([PROTOCOL, 'challenge', card.deviceId, card.cardId,
        challenge.challengeId, requestId, challenge.requestNonce, 30]))
      const checked = cipher()(key, unhex('02' + challenge.requestNonce.slice(2)), challengeAAD).decrypt(unhex(challenge.proof))
      if (checked.length !== 0 || run.cancelled || closed || Date.now() - acquired >= 30000) invalid()
      usedChallenges.add(challenge.challengeId); usedNonces.add(challenge.requestNonce)
      run.diagnostic = 'LOCAL_REQUEST_INVALID'
      const bearer = options.header.Authorization
      if (bearer !== undefined && (typeof bearer !== 'string' || !/^Bearer [A-Za-z0-9._~-]{24,2048}$/.test(bearer))) invalid()
      const plain = utf8(JSON.stringify({ method: options.method, path: options.url, body: options.data === undefined ? null : options.data,
        accessToken: bearer === undefined ? null : bearer.slice(7) }))
      if (plain.length > 8192) invalid()
      const ciphertext = cipher()(key, unhex(challenge.requestNonce), aad('request', challenge, requestId)).encrypt(plain)
      const packet = { v: 1, deviceId: card.deviceId, cardId: card.cardId, challengeId: challenge.challengeId, requestId,
        nonce: challenge.requestNonce, ciphertext: hex(ciphertext) }
      if (utf8(JSON.stringify(packet)).length > 20480) invalid()
      run.diagnostic = 'LOCAL_NATIVE_NETWORK'
      const reply = await wire(options, 'POST', '/local/v1/packet', packet, run)
      run.diagnostic = 'LOCAL_RESPONSE_HTTP'
      if (run.cancelled || closed || reply.statusCode !== 200) invalid()
      run.diagnostic = 'LOCAL_RESPONSE_AUTH'
      const value = reply.data
      exact(value, ['v', 'deviceId', 'cardId', 'challengeId', 'requestId', 'nonce', 'ciphertext'])
      if (value.v !== 1 || value.deviceId !== card.deviceId || value.cardId !== card.cardId || value.challengeId !== challenge.challengeId ||
          value.requestId !== requestId || value.nonce !== '01' + challenge.requestNonce.slice(2) || typeof value.ciphertext !== 'string' ||
          value.ciphertext.length > MAX_PLAIN_RESPONSE * 2 + 32 || !/^(?:[0-9a-f]{2}){16,}$/.test(value.ciphertext)) invalid()
      const raw = cipher()(key, unhex(value.nonce), aad('response', challenge, requestId)).decrypt(unhex(value.ciphertext))
      if (raw.length > MAX_PLAIN_RESPONSE) invalid()
      const envelope = JSON.parse(text(raw)); exact(envelope, ['status', 'body'])
      if (!Number.isInteger(envelope.status) || envelope.status < 200 || envelope.status > 599 || !envelope.body || typeof envelope.body !== 'object' || Array.isArray(envelope.body)) invalid()
      if (envelope.body.success === true) {
        const data = envelope.body.data
        if (options.url === '/local/pair') {
          exact(data, ['userId', 'expiresAt', 'accessToken', 'ttlSeconds'])
          if (data.userId !== expectedUser || data.ttlSeconds !== 3600) invalid()
          pairServerExpiry = serverExpiry(data.expiresAt); pairLocalExpiry = Date.now() + 3600000
          expiresAt = new Date(pairLocalExpiry).toISOString()
          envelope.body.data = { userId: data.userId, expiresAt, accessToken: data.accessToken }
        } else if (options.url === '/v1/session') {
          if (!data || data.userId !== expectedUser || !expiresAt || Date.parse(expiresAt) <= Date.now()) invalid()
          const shortened = Math.min(Date.parse(expiresAt), pairLocalExpiry + serverExpiry(data.expiresAt) - pairServerExpiry)
          if (shortened <= Date.now()) invalid()
          expiresAt = new Date(shortened).toISOString()
          envelope.body.data = { ...data, expiresAt }
        } else if (options.url === '/v1/pets') {
          if (!Array.isArray(data) || data.length !== 1 || data[0].petId !== expectedPet) invalid()
        }
      }
      if (!run.cancelled && !closed) options.success({ statusCode: envelope.status, data: envelope.body })
    })().catch(() => {
      if (!run.cancelled) {
        const error = { errMsg: 'local encrypted request unavailable' }
        diagnostics.set(error, run.diagnostic)
        options.fail(error)
      }
    })
      .finally(() => requests.delete(run))
    return { abort }
  }
  const transport = { request, diagnosticCode(error: any) {
    return error && typeof error === 'object' ? diagnostics.get(error) || '' : ''
  }, validateProvision(value: any, route: string, body: any) {
    if (route.includes('/archive-')) return verifyArchive(value, route, body)
    if (route === '/local/provision/current') { exact(value, ['window']); return { window: value.window === null ? null : verifyProvisionMetadata(value.window, false) } }
    return verifyProvisionMetadata(value, route === '/local/provision/resume' || route === '/local/provision/prepare' && value && value.status === 'prepared', body.requestId)
  }, validateHealth(value: any) {
    exact(value, ['mode', 'wechatConfigured', 'localConfigured', 'readOnly'])
    if (value.mode !== 'local-device' || value.wechatConfigured !== false || value.localConfigured !== true || typeof value.readOnly !== 'boolean') invalid()
    return { mode: 'local-device', wechatConfigured: false, localConfigured: true, readOnly: value.readOnly }
  } }
  const core = createGatewayClientCore(wxApi, undefined, transport)
  return { ...core, authKind: 'local',
    getLocalContext: () => ({ deviceId: card.deviceId, cardId: card.cardId, address: host, port: PORT }),
    destroy() { core.clearSession(); closed = true; key.fill(0); for (const run of requests) { run.cancelled = true; for (const cancel of Array.from(run.tasks) as any[]) cancel() }; requests.clear() } }
}

module.exports = { createLocalDeviceClient, localRuntimeAvailable, localCardIdentity, verifyLocalAddress }
