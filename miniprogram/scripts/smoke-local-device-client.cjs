// Offline actual TS/noble transport checked independently against Node AES-GCM.
// All identities, cards, keys, clocks and wx calls below are synthetic fixtures.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), crypto = require('node:crypto'), ts = require('typescript')
const root = path.resolve(__dirname, '..'), DEVICE = 'dev_' + '1'.repeat(32), CARD = '3'.repeat(32), KEY = '22'.repeat(32)
const QR = 'AICAT-LOCAL:1:' + DEVICE + ':' + CARD + ':' + KEY, USER = 'loc_' + DEVICE.slice(4) + '_' + CARD, PET = 'lp_' + DEVICE.slice(4)
const TOKEN = 'synthetic-local-private-access-token', NOW = Date.parse('2026-11-02T00:00:00Z')
const WINDOW = '4'.repeat(32), INVITATION = 'AICAT-PROVISION:1:' + DEVICE + ':' + WINDOW + ':' + '55'.repeat(32)
const plain = value => JSON.parse(JSON.stringify(value))
const flush = async () => { for (let i = 0; i < 70; i++) await Promise.resolve() }
const status = () => ({ readOnly: true, sampledAt: '2026-11-02T00:00:00Z', health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' },
  device: { connected: true, deviceSerial: '7c2****a128', networkName: null, lastSeenAt: null, batteryPercent: 66, batteryState: 'charging', batteryVoltage: 4.1, chargerOnline: true, batteryPresent: true },
  dialog: { state: 'ready', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false } })
function aad(direction, packet) { return Buffer.from(JSON.stringify(['aicat-local-v1', direction, DEVICE, CARD, packet.challengeId, packet.requestId])) }
function encrypt(packet, body) {
  const nonce = '01' + packet.nonce.slice(2), cipher = crypto.createCipheriv('aes-256-gcm', Buffer.from(KEY, 'hex'), Buffer.from(nonce, 'hex'))
  cipher.setAAD(aad('response', packet))
  const data = Buffer.concat([cipher.update(Buffer.from(JSON.stringify(body))), cipher.final(), cipher.getAuthTag()])
  return { v: 1, deviceId: DEVICE, cardId: CARD, challengeId: packet.challengeId, requestId: packet.requestId, nonce, ciphertext: data.toString('hex') }
}
function decrypt(packet) {
  const bytes = Buffer.from(packet.ciphertext, 'hex'), cipher = crypto.createDecipheriv('aes-256-gcm', Buffer.from(KEY, 'hex'), Buffer.from(packet.nonce, 'hex'))
  cipher.setAAD(aad('request', packet)); cipher.setAuthTag(bytes.subarray(-16))
  return JSON.parse(Buffer.concat([cipher.update(bytes.subarray(0, -16)), cipher.final()]).toString('utf8'))
}
function challengeProof(value) {
  const nonce = '02' + value.requestNonce.slice(2), cipher = crypto.createCipheriv('aes-256-gcm', Buffer.from(KEY, 'hex'), Buffer.from(nonce, 'hex'))
  cipher.setAAD(Buffer.from(JSON.stringify(['aicat-local-v1', 'challenge', DEVICE, CARD, value.challengeId, value.requestId, value.requestNonce, 30])))
  cipher.final(); return cipher.getAuthTag().toString('hex')
}
function harness(options = {}) {
  let counter = 0, random = 0, now = NOW, timerId = 0, logins = 0, storageWrites = 0, appDefinition
  const calls = [], rpc = [], logs = [], timers = new Map(), modules = new Map(), storage = options.storage || new Map()
  class Clock extends Date { constructor(...args) { super(...(args.length ? args : [now])) } static now() { return now } }
  const wx = {
    getAccountInfoSync: () => ({ miniProgram: { envVersion: options.env || 'develop' } }), getDeviceInfo: () => ({ platform: options.platform || 'android' }),
    login() { logins++; throw Error('No fake wx.login allowed') },
    getStorageSync(key) { if (options.storageFailure) throw Error('PRIVATE_STORAGE_ERROR'); return storage.has(key) ? plain(storage.get(key)) : '' },
    setStorageSync() { storageWrites++; throw Error('No new persistence allowed') },
    getUserCryptoManager: () => ({ getRandomValues(value) { if (options.randomFailure) return value.fail(); const data = new Uint8Array(value.length).fill(options.sameRandom ? 9 : ++random); value.success({ randomValues: data.buffer }) } }),
    request(value) {
      calls.push(value)
      assert.equal(value.header.Authorization, undefined, 'never expose a bearer in native HTTP headers')
      if (options.computerHealth && value.url === 'http://192.168.1.110:8788/health') {
        queueMicrotask(() => value.success({ statusCode: 200, data: { success: true, data: { mode: 'local-development', wechatConfigured: true, readOnly: false } } }))
        return { abort() {} }
      }
      assert.ok(/^http:\/\/192\.168\.1\.152:8789\/local\/(?:health|v1\/(?:challenge\/[0-9a-f]{32}|packet))$/.test(value.url))
      let cancelled = false
      queueMicrotask(() => {
        if (cancelled || options.deferAll || options.deferChallenge && value.url.includes('/challenge/')) return
        const nativeError = typeof options.nativeFailure === 'function' ? options.nativeFailure(value) : options.nativeFailure
        if (nativeError) return value.fail(nativeError)
        if (value.url.endsWith('/health')) return value.success({ statusCode: 200, data: { success: true, data: { mode: 'local-device', wechatConfigured: false, localConfigured: true, readOnly: false, ...(options.health || {}) } } })
        if (options.httpFailure && (options.httpFailure === 'challenge') === value.url.includes('/challenge/'))
          return value.success({ statusCode: 503, data: { code: 'LOCAL_RANDOM', localDiagnostic: 'LOCAL_RANDOM', message: 'PRIVATE_NATIVE_DETAILS' } })
        if (value.url.includes('/challenge/')) {
          const count = ++counter
          const result = { v: 1, deviceId: DEVICE, cardId: CARD, challengeId: count.toString(16).padStart(32, '0'), requestId: value.url.split('/').at(-1), requestNonce: '00' + (options.sameNonce ? 'a'.repeat(22) : count.toString(16).padStart(22, '0')), ttlSeconds: 30 }
          result.proof = challengeProof(result)
          if (options.challenge) options.challenge(result)
          return value.success({ statusCode: 200, data: { success: true, data: result } })
        }
        assert.equal(value.method, 'POST')
        const packet = value.data
        assert.equal(packet.deviceId, DEVICE); assert.equal(packet.cardId, CARD); assert.match(packet.requestId, /^[a-f0-9]{32}$/)
        assert.match(packet.nonce, /^00[a-f0-9]{22}$/)
        assert.doesNotMatch(JSON.stringify(packet), new RegExp(TOKEN + '|' + KEY + '|私人文字'))
        const request = decrypt(packet); rpc.push(request)
        assert.equal(request.accessToken, request.path === '/local/pair' ? null : TOKEN)
        let data
        if (request.path === '/local/pair') {
          assert.deepEqual(request.body, { confirmation: 'pair-local-device' })
          data = { userId: USER, expiresAt: '2000-01-01T00:00:00Z', accessToken: TOKEN, ttlSeconds: 3600, ...(options.pair || {}) }
        } else if (request.path === '/v1/pets') data = [{ petId: PET, name: '小安' }]
        else if (request.path === '/v1/session') data = { userId: USER, expiresAt: options.sessionExpiry || '2000-01-01T00:00:00Z' }
        else if (request.path.endsWith('/status')) data = status()
        else if (request.path === '/v1/auth/logout') data = { loggedOut: true }
        else data = { unsupportedSyntheticFixture: true }
        if (request.path.startsWith('/local/provision/')) {
          const metadata = { requestId: request.body.requestId || 'window-request', deviceId: DEVICE, windowId: WINDOW,
            status: request.path.endsWith('/prepare') ? 'prepared' : 'unknown', apName: 'AI-CAT-ABC123', expiresAt: Date.parse('1999-12-31T23:03:00Z') / 1000, networkRequestId: null, receiptQr: null,
            ...(options.provision || {}) }
          data = request.path.endsWith('/current') ? { window: options.noWindow ? null : metadata } :
            request.path.endsWith('/resume') || request.path.endsWith('/prepare') && metadata.status === 'prepared' ? { ...metadata, invitation: INVITATION } : metadata
        }
        const result = options.inner ? options.inner(request, data) : { status: 200, body: { success: true, data } }
        const reply = encrypt(packet, result)
        if (options.reply) options.reply(reply)
        value.success({ statusCode: 200, data: reply })
      })
      return { abort() { cancelled = true; value.fail({ errMsg: 'PRIVATE_NATIVE_DETAILS' }) } }
    }
  }
  const context = vm.createContext({ wx, Uint8Array, ArrayBuffer, DataView, BigInt: options.cryptoUnavailable ? undefined : BigInt, Date: Clock, App(value) { appDefinition = value },
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, at: now + delay }); return id }, clearTimeout(id) { timers.delete(id) },
    console: Object.fromEntries(['log', 'info', 'warn', 'error', 'debug'].map(name => [name, (...args) => logs.push(args)])) })
  function load(relative, parent = root) {
    let filename = path.resolve(parent, relative); if (!filename.endsWith('.ts')) filename += '.ts'
    if (options.gcmMissing && filename.endsWith('/vendor/noble-ciphers-2.4.0/aes.ts')) return { gcm: null }
    if (modules.has(filename)) return modules.get(filename).exports
    const module = { exports: {} }; modules.set(filename, module)
    const source = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { target: ts.ScriptTarget.ES2019, module: ts.ModuleKind.CommonJS } }).outputText
    new vm.Script('(function(require,module,exports){' + source + '\n})', { filename }).runInContext(context)(name => load(name, path.dirname(filename)), module, module.exports)
    return module.exports
  }
  const api = load('services/local-device-client'), shared = load('services/live-device-session')
  return { api, shared, wx, calls, rpc, storage, logs, timers,
    legacyLedger: () => load('services/provision-ledger-guard'),
    app: () => { load('app'); return appDefinition },
    client: () => api.createLocalDeviceClient(wx, QR, '192.168.1.152'),
    logins: () => logins, writes: () => storageWrites,
    async tick(ms) { now += ms; for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn() }; await flush() }
  }
}
const cases = []
const check = (name, run) => cases.push([name, run])
check('card and canonical RFC1918 validators reject URLs, public/loopback/linklocal addresses and provisioning QR', () => {
  const h = harness()
  for (const address of ['192.168.1.152', '10.1.2.3', '172.16.1.1', '172.31.1.254']) assert.equal(h.api.verifyLocalAddress(address), address)
  for (const address of ['http://192.168.1.152', '192.168.1.152:8789', '127.0.0.1', '169.254.169.254', '172.32.1.1', '8.8.8.8', '192.168.01.1', '192.168.1.0', '192.168.1.255', ' 192.168.1.152', '192.168.1.152/']) assert.throws(() => h.api.verifyLocalAddress(address))
  assert.deepEqual(plain(h.api.localCardIdentity(QR)), { deviceId: DEVICE, cardId: CARD })
  for (const card of ['AICAT-PROVISION:1:' + DEVICE + ':' + CARD + ':' + KEY, QR + '\n', QR.toUpperCase(), '{}']) assert.throws(() => h.api.localCardIdentity(card))
  assert.equal(h.calls.length, 0)
})
check('trial/release/devtools/iOS cannot instantiate local transport or send a request', () => {
  for (const options of [{ env: 'trial' }, { env: 'release' }, { platform: 'devtools' }, { platform: 'ios' }]) { const h = harness(options); assert.throws(h.client); assert.equal(h.calls.length, 0) }
})
check('factory and public context never contact hardware, persist a secret or expose card key', () => {
  const h = harness(), c = h.client()
  assert.deepEqual(plain(c.getLocalContext()), { deviceId: DEVICE, cardId: CARD, address: '192.168.1.152', port: 8789 })
  assert.doesNotMatch(JSON.stringify(c), new RegExp(KEY + '|' + TOKEN)); assert.equal(h.calls.length, 0); assert.equal(h.writes(), 0); c.destroy()
})
check('public health is local identity, never pretend WeChat is configured', async () => {
  const h = harness(), c = h.client(), health = await c.getHealth()
  assert.equal(health.mode, 'local-device'); assert.equal(health.wechatConfigured, false); assert.equal(health.localConfigured, true)
  assert.equal(h.calls.length, 1); assert.equal(h.rpc.length, 0); assert.equal(h.logins(), 0); c.destroy()
  const bad = harness({ health: { wechatConfigured: true } }), d = bad.client(); await assert.rejects(d.getHealth(), error => error.code === 'UNVERIFIED'); d.destroy()
})
check('pair and business GET interoperate with independent AES and never call wx.login', async () => {
  const h = harness(), c = h.client(), metadata = await c.login()
  assert.equal(metadata.userId, USER); assert.equal(Date.parse(metadata.expiresAt), NOW + 3600000)
  assert.equal(c.peekSession().expiresAt, metadata.expiresAt)
  assert.deepEqual(plain(await c.listPets()), [{ petId: PET, name: '小安' }])
  assert.equal((await c.readPetStatus(PET)).device.batteryPercent, 66)
  assert.equal((await c.getSession()).expiresAt, metadata.expiresAt)
  assert.equal(h.logins(), 0); assert.equal(h.writes(), 0); assert.equal(h.logs.length, 0)
  assert.equal(h.calls.filter(call => call.url.includes('/challenge/')).length, 4)
  assert.equal(new Set(h.calls.filter(call => call.data).map(call => call.data.nonce)).size, 4)
  c.destroy()
})
check('Python-produced public synthetic fixture matches actual TS request and response byte-for-byte', async () => {
  const fixture = JSON.parse(fs.readFileSync(path.join(root, 'scripts/fixtures/local-gateway-aesgcm-v1.json'), 'utf8'))
  assert.equal(fixture.fixture, 'synthetic-only-not-a-real-device-card')
  const h = harness(), requests = []
  const wx = { getAccountInfoSync: h.wx.getAccountInfoSync, getDeviceInfo: h.wx.getDeviceInfo,
    getUserCryptoManager: () => ({ getRandomValues(value) { assert.equal(value.length, 16); value.success({ randomValues: new Uint8Array(16).fill(0xcc).buffer }) } }),
    request(value) {
      requests.push(value); assert.equal(value.header.Authorization, undefined)
      queueMicrotask(() => {
        if (requests.length === 1) {
          assert.equal(value.url, 'http://192.168.1.152:8789/local/v1/challenge/' + 'c'.repeat(32))
          value.success({ statusCode: 200, data: { success: true, data: cloneFixture(fixture.challenge) } })
        } else {
          assert.equal(value.url, 'http://192.168.1.152:8789/local/v1/packet'); assert.deepEqual(plain(value.data), fixture.request)
          value.success({ statusCode: 200, data: cloneFixture(fixture.response) })
        }
      })
      return { abort() {} }
    } }
  function cloneFixture(value) { return JSON.parse(JSON.stringify(value)) }
  const c = h.api.createLocalDeviceClient(wx, fixture.card, '192.168.1.152'), metadata = await c.login()
  assert.equal(metadata.userId, fixture.responsePlain.body.data.userId); assert.equal(requests.length, 2)
  const secret = Buffer.from(fixture.card.split(':').at(-1), 'hex'), p = fixture.request
  const proof = crypto.createDecipheriv('aes-256-gcm', secret, Buffer.from('02' + p.nonce.slice(2), 'hex'))
  proof.setAAD(Buffer.from(JSON.stringify(['aicat-local-v1', 'challenge', p.deviceId, p.cardId, p.challengeId, p.requestId, p.nonce, 30])))
  proof.setAuthTag(Buffer.from(fixture.challenge.proof, 'hex')); assert.equal(proof.final().length, 0)
  c.destroy()
})
check('local user, TTL and returned pet stay pinned to this exact card/device', async () => {
  for (const pair of [{ userId: 'user_other' }, { userId: 'loc_' + 'f'.repeat(64) }, { ttlSeconds: 7200 }]) {
    const h = harness({ pair }), c = h.client(); await assert.rejects(c.login()); assert.equal(c.peekSession(), null); c.destroy()
  }
  const h = harness(), c = h.client(); await c.login(); const count = h.calls.length
  await assert.rejects(c.readPetStatus('lp_' + 'f'.repeat(32))); assert.equal(h.calls.filter(call => call.url.endsWith('/packet')).length, 1)
  assert.equal(h.calls.length, count); c.destroy()
})
for (const [name, modify] of [
  ['device mismatch', c => c.deviceId = 'dev_' + '9'.repeat(32)], ['card mismatch', c => c.cardId = '9'.repeat(32)],
  ['TTL mismatch', c => c.ttlSeconds = 31], ['wrong nonce direction', c => c.requestNonce = '01' + '0'.repeat(22)],
  ['unexpected field', c => c.secret = 'PRIVATE'], ['bad challenge type', c => c.challengeId = ['1'.repeat(32)]],
  ['missing proof', c => delete c.proof], ['forged proof', c => c.proof = '0'.repeat(32)], ['wrong client ID', c => c.requestId = '9'.repeat(32)]
]) check('invalid challenge fails before encrypted dispatch: ' + name, async () => {
  const h = harness({ challenge: modify }), c = h.client(); await assert.rejects(c.login()); assert.equal(h.calls.length, 1); assert.equal(h.rpc.length, 0); c.destroy()
})
for (const [name, modify] of [
  ['wrong device', r => r.deviceId = 'dev_' + 'f'.repeat(32)], ['wrong card', r => r.cardId = 'f'.repeat(32)],
  ['wrong challenge', r => r.challengeId = 'f'.repeat(32)], ['wrong request', r => r.requestId = 'f'.repeat(32)],
  ['wrong nonce', r => r.nonce = '00' + r.nonce.slice(2)], ['modified tag', r => r.ciphertext = r.ciphertext.slice(0, -2) + (r.ciphertext.endsWith('00') ? '01' : '00')],
  ['extra plaintext field', r => r.accessToken = TOKEN], ['overlong ciphertext', r => r.ciphertext = 'ab'.repeat(2 * 1024 * 1024 + 17)]
]) check('tampered response fails closed: ' + name, async () => {
  const h = harness({ reply: modify }), c = h.client(); await assert.rejects(c.login()); assert.equal(c.peekSession(), null); c.destroy()
})
check('server nonce repetition and client request ID repetition fail without a second packet', async () => {
  for (const options of [{ sameNonce: true }, { sameRandom: true }]) {
    const h = harness(options), c = h.client(); await c.login(); await assert.rejects(c.listPets())
    assert.equal(h.rpc.length, 1); c.destroy()
  }
})
check('an old authenticated challenge cannot be replayed across a new card closure, even with rewritten fresh ID', async () => {
  for (const rewriteId of [false, true]) {
    let old, count = 0
    const h = harness({ challenge(value) {
      if (++count === 1) old = plain(value)
      else { const freshId = value.requestId; Object.assign(value, old); if (rewriteId) value.requestId = freshId }
    } }), first = h.client()
    await first.login(); first.destroy()
    const second = h.client(); await assert.rejects(second.login()); assert.equal(h.rpc.length, 1)
    assert.equal(h.calls.filter(value => value.method === 'POST').length, 1); second.destroy()
  }
})
check('random failure never sends a card key, bearer or plaintext fallback', async () => {
  const h = harness({ randomFailure: true }), c = h.client(); await assert.rejects(c.login()); assert.equal(h.rpc.length, 0); assert.equal(h.calls.length, 0); c.destroy()
})
check('native failures expose only a fixed local category, not arbitrary codes details or retry requests', async () => {
  for (const [message, code] of [
    ['request:fail url not in domain list PRIVATE_NATIVE_DETAILS', 'LOCAL_NATIVE_DOMAIN'],
    ['request:fail timeout PRIVATE_NATIVE_DETAILS', 'LOCAL_NATIVE_TIMEOUT'],
    ['request:fail permission denied PRIVATE_NATIVE_DETAILS', 'LOCAL_NATIVE_PERMISSION'],
    ['request:fail PRIVATE_NATIVE_DETAILS', 'LOCAL_NATIVE_NETWORK']
  ]) {
    const h = harness({ nativeFailure: { errMsg: message, code: 'LOCAL_RANDOM', localDiagnostic: 'LOCAL_RANDOM' } }), c = h.client()
    await assert.rejects(c.login(), error => {
      assert.equal(error.code, code); assert.ok(error.message.includes('[' + code + ']'))
      assert.doesNotMatch(error.message, /PRIVATE_NATIVE_DETAILS|192\.168\.1\.152|AICAT-LOCAL|synthetic/)
      return true
    })
    assert.equal(h.calls.length, 1); assert.equal(h.rpc.length, 0); assert.equal(c.peekSession(), null)
    assert.equal(h.logs.length, 0); assert.equal(h.writes(), 0); c.destroy()
  }
})
check('runtime random identity proof and response failures remain distinct and fail closed', async () => {
  for (const [options, code, calls] of [
    [{ cryptoUnavailable: true }, 'LOCAL_CRYPTO', 0],
    [{ gcmMissing: true }, 'LOCAL_CRYPTO', 0],
    [{ randomFailure: true }, 'LOCAL_RANDOM', 0],
    [{ challenge: value => { value.cardId = 'f'.repeat(32) } }, 'LOCAL_CARD_MISMATCH', 1],
    [{ challenge: value => { value.proof = '00'.repeat(16) } }, 'LOCAL_CHALLENGE_AUTH', 1],
    [{ reply: value => { value.ciphertext = value.ciphertext.slice(0, -2) + (value.ciphertext.endsWith('00') ? '01' : '00') } }, 'LOCAL_RESPONSE_AUTH', 2],
    [{ httpFailure: 'challenge' }, 'LOCAL_CHALLENGE_HTTP', 1],
    [{ httpFailure: 'packet' }, 'LOCAL_RESPONSE_HTTP', 2]
  ]) {
    const h = harness(options), c = h.client()
    await assert.rejects(c.login(), error => error.code === code && !/PRIVATE_NATIVE_DETAILS|192\.168\.1\.152|AICAT-LOCAL/.test(error.message))
    assert.equal(h.calls.length, calls); assert.equal(c.peekSession(), null); assert.equal(h.logs.length, 0); assert.equal(h.writes(), 0); c.destroy()
  }
})
check('successful public health followed by blocked encrypted pairing exposes the safe diagnostic in real session state', async () => {
  const h = harness({ nativeFailure: value => value.url.includes('/challenge/') ? { errMsg: 'request:fail url not in domain list PRIVATE_NATIVE_DETAILS' } : null })
  const session = h.shared.createLiveDeviceSession(h.wx), views = [], detach = session.attach(value => views.push(plain(value)))
  const intent = session.prepareLocal(QR, '192.168.1.152'); await session.confirmLocal(intent.token)
  assert.equal(session.getState().signedIn, false); assert.ok(session.getState().error.includes('[LOCAL_NATIVE_DOMAIN]'))
  assert.doesNotMatch(JSON.stringify(views), new RegExp(KEY + '|' + TOKEN + '|AICAT-LOCAL:|PRIVATE_NATIVE_DETAILS'))
  assert.equal(h.calls.length, 2); assert.equal(h.rpc.length, 0); assert.equal(h.writes(), 0)
  session.clear(); detach()
})
check('late native diagnostic after hide cannot revive pairing or restore authorization', async () => {
  const h = harness({ deferChallenge: true }), session = h.shared.createLiveDeviceSession(h.wx), detach = session.attach(() => {})
  const intent = session.prepareLocal(QR, '192.168.1.152'), pairing = session.confirmLocal(intent.token)
  await flush(); const challenge = h.calls.find(value => value.url.includes('/challenge/')); assert.ok(challenge)
  session.clear(); challenge.fail({ errMsg: 'request:fail url not in domain list PRIVATE_NATIVE_DETAILS' }); await pairing
  assert.equal(session.getState().signedIn, false); assert.equal(session.getState().error, ''); assert.equal(h.rpc.length, 0); detach()
})
check('cancel during challenge and destroy prevent late dispatch and any retries', async () => {
  const h = harness({ deferChallenge: true }), c = h.client(), pending = c.login()
  await flush(); assert.equal(h.calls.length, 1); c.cancelPending(); await assert.rejects(pending, error => error.code === 'CANCELLED')
  assert.equal(h.rpc.length, 0); c.destroy(); const count = h.calls.length; await assert.rejects(c.getHealth()); assert.equal(h.calls.length, count)
})
check('encrypted 401 clears the token and future protected reads do not send anything', async () => {
  const h = harness({ inner: (request, data) => request.path === '/local/pair' ? { status: 200, body: { success: true, data } } : { status: 401, body: { success: false, message: 'invalid' } } }), c = h.client()
  await c.login(); await assert.rejects(c.listPets(), error => error.code === 'UNAUTHORIZED'); assert.equal(c.peekSession(), null)
  const count = h.calls.length; await assert.rejects(c.listPets(), error => error.code === 'AUTH_REQUIRED'); assert.equal(h.calls.length, count); c.destroy()
})
check('authenticated local network RPCs use strict encrypted DTOs without requiring pet selection', async () => {
  const h = harness(), s = h.shared.createLiveDeviceSession(h.wx), detach = s.attach(() => {})
  const intent = s.prepareLocal(QR, '192.168.1.152'); await s.confirmLocal(intent.token)
  assert.equal(s.getState().selectedPetId, '')
  const prepared = await s.prepareLocalProvision('window-request')
  assert.equal(prepared.invitation, INVITATION); assert.equal(prepared.expiresAt, NOW / 1000 + 180)
  assert.equal((await s.currentLocalProvision()).window.windowId, WINDOW)
  assert.equal((await s.resumeLocalProvision('window-request')).invitation, INVITATION)
  assert.equal((await s.statusLocalProvision('window-request')).status, 'unknown')
  assert.equal((await s.receiptLocalProvision('window-request')).receiptQr, null)
  await h.tick(20000)
  assert.equal((await s.statusLocalProvision('window-request')).expiresAt, prepared.expiresAt, 'fixed RTC offset must not renew a deadline on query')
  assert.doesNotMatch(JSON.stringify(s.getState()), new RegExp(INVITATION + '|' + TOKEN + '|' + KEY))
  assert.equal(h.logins(), 0); s.clear(); detach()
})
check('network current can be empty, invalid request IDs fail before a challenge and malformed metadata fails closed', async () => {
  const h = harness({ noWindow: true }), c = h.client(); await c.login()
  assert.equal((await c.currentLocalProvision()).window, null); const count = h.calls.length
  for (const requestId of ['', '../bad', 'x'.repeat(65), ['coerce']]) await assert.rejects(c.prepareLocalProvision(requestId))
  assert.equal(h.calls.length, count); c.destroy()
  for (const provision of [{ deviceId: 'dev_' + 'f'.repeat(32) }, { requestId: 'wrong-request' }, { windowId: 'f'.repeat(32) }, { status: 'invented' }, { expiresAt: '1800000180' }]) {
    const h = harness({ provision }), c = h.client(); await c.login(); await assert.rejects(c.prepareLocalProvision('window-request')); c.destroy()
  }
})
check('session refresh maps server clock offsets and can only shorten the original local expiry', async () => {
  for (const [sessionExpiry, offset] of [['2000-01-01T00:30:00Z', 3600000], ['1999-12-31T23:30:00Z', 1800000]]) {
    const h = harness({ sessionExpiry }), c = h.client(); await c.login(); assert.equal(Date.parse((await c.getSession()).expiresAt), NOW + offset); c.destroy()
  }
})
check('shared pairing requires explicit current confirmation, exposes only local metadata and still requires selection', async () => {
  const h = harness(), s = h.shared.createLiveDeviceSession(h.wx), views = [], detach = s.attach(value => views.push(value))
  const intent = s.prepareLocal(QR, '192.168.1.152'); assert.equal(h.calls.length, 0)
  await s.confirmLocal(intent.token)
  assert.equal(s.getState().authKind, 'local'); assert.equal(s.getState().signedIn, true); assert.equal(s.getState().selectedPetId, '')
  assert.deepEqual(plain(s.getState().pets), [{ petId: PET, name: '小安' }]); assert.equal(h.rpc.length, 2)
  await s.selectPet(PET); assert.equal(s.getState().snapshot.device.batteryPercent, 66)
  assert.doesNotMatch(JSON.stringify(views), new RegExp(KEY + '|' + TOKEN + '|AICAT-LOCAL:'))
  s.clear(); assert.equal(s.getState().localCardRequired, true); assert.equal(s.getState().signedIn, false); assert.equal(h.writes(), 0); detach()
})
check('expired/cancelled pairing intent never contacts a device', async () => {
  const h = harness(), s = h.shared.createLiveDeviceSession(h.wx), detach = s.attach(() => {})
  const a = s.prepareLocal(QR, '192.168.1.152'); s.cancelLocal(a.token); await assert.rejects(s.confirmLocal(a.token))
  const b = s.prepareLocal(QR, '192.168.1.152'); await h.tick(30000); await assert.rejects(s.confirmLocal(b.token)); assert.equal(h.calls.length, 0); detach()
})
check('an old confirmed card health continuation cannot log in the newly selected computer gateway', async () => {
  const options = { deferAll: true, computerHealth: true }, h = harness(options), s = h.shared.createLiveDeviceSession(h.wx), detach = s.attach(() => {})
  const intent = s.prepareLocal(QR, '192.168.1.152'), oldPair = s.confirmLocal(intent.token)
  assert.equal(h.calls.length, 1)
  const switched = s.useComputerGateway()
  await Promise.all([oldPair, switched]); await flush()
  assert.equal(s.getState().authKind, 'wechat'); assert.equal(s.getState().signedIn, false)
  assert.equal(h.logins(), 0); assert.equal(h.rpc.length, 0); detach()
})
check('a re-entrant view switching at activation cannot transfer the old card confirmation to computer login', async () => {
  const h = harness({ computerHealth: true }), s = h.shared.createLiveDeviceSession(h.wx)
  let switching = null
  const detach = s.attach(value => {
    if (value.authKind === 'local' && !value.loading && !value.signedIn && !switching) switching = s.useComputerGateway()
  })
  const intent = s.prepareLocal(QR, '192.168.1.152'); await s.confirmLocal(intent.token)
  await switching; await flush()
  assert.equal(s.getState().authKind, 'wechat'); assert.equal(h.logins(), 0); assert.equal(h.rpc.length, 0); detach()
})
check('actual app hide clears active card/session and prepared card before late confirmations or challenge replies', async () => {
  const h = harness(), s = h.shared.getLiveDeviceSession(), detach = s.attach(() => {})
  const intent = s.prepareLocal(QR, '192.168.1.152'); await s.confirmLocal(intent.token)
  assert.equal(s.getState().signedIn, true)
  h.app().onHide(); const count = h.calls.length
  assert.equal(s.getState().signedIn, false); assert.equal(s.getState().localCardRequired, true)
  await s.login(); assert.equal(h.calls.length, count); assert.equal(h.writes(), 0); detach()
  const candidate = harness(), cs = candidate.shared.getLiveDeviceSession(), cd = cs.attach(() => {})
  const pending = cs.prepareLocal(QR, '192.168.1.152'); candidate.app().onHide()
  await assert.rejects(cs.confirmLocal(pending.token)); assert.equal(candidate.calls.length, 0); cd()
  const delayed = harness({ deferChallenge: true }), ds = delayed.shared.getLiveDeviceSession(), dd = ds.attach(() => {})
  const preparing = ds.prepareLocal(QR, '192.168.1.152'), confirming = ds.confirmLocal(preparing.token)
  await flush(); const challengeCall = delayed.calls.find(value => value.url.includes('/challenge/'))
  assert.ok(challengeCall); delayed.app().onHide()
  const challenge = { v: 1, deviceId: DEVICE, cardId: CARD, challengeId: 'e'.repeat(32), requestId: challengeCall.url.split('/').at(-1), requestNonce: '000000000000000000000001', ttlSeconds: 30 }
  challenge.proof = challengeProof(challenge)
  challengeCall.success({ statusCode: 200, data: { success: true, data: challenge } })
  await confirming; await flush()
  assert.equal(delayed.rpc.length, 0); assert.equal(ds.getState().signedIn, false); assert.equal(ds.getState().localCardRequired, true); dd()
})
check('foreign unresolved dialog/settings and unreadable storage block mode change without erasing markers', () => {
  for (const [key, value] of [
    ['aiCatDialogPendingV1', { version: 1, main: { userId: 'user_old', petId: 'pet_old', requestId: 'old', kind: 'listen' }, interrupt: null }],
    ['aiCatPreferencesPendingV1', { version: 1, settings: { userId: USER, petId: 'lp_' + 'f'.repeat(32), requestId: 'old', kind: 'settings' }, feedback: null }]
  ]) {
    const map = new Map([[key, value]]), h = harness({ storage: map }), s = h.shared.createLiveDeviceSession(h.wx)
    assert.throws(() => s.prepareLocal(QR, '192.168.1.152')); assert.deepEqual(map.get(key), value); assert.equal(h.calls.length, 0); assert.equal(h.writes(), 0)
  }
  const h = harness({ storageFailure: true }), s = h.shared.createLiveDeviceSession(h.wx); assert.throws(() => s.prepareLocal(QR, '192.168.1.152')); assert.equal(h.calls.length, 0)
})
check('a local pending receipt can re-pair only the same card and cannot escape into the PC mode', async () => {
  const map = new Map([['aiCatDialogPendingV1', { version: 1, main: { userId: USER, petId: PET, requestId: 'old', kind: 'listen' }, interrupt: null }]])
  const h = harness({ storage: map }), s = h.shared.createLiveDeviceSession(h.wx), detach = s.attach(() => {})
  const intent = s.prepareLocal(QR, '192.168.1.152'); await s.confirmLocal(intent.token)
  await assert.rejects(s.useComputerGateway()); assert.equal(s.getState().authKind, 'local'); assert.equal(h.writes(), 0)
  s.clear(); detach()
})
check('unsettled network permits original-card recovery at AP/new-station addresses but never foreign cards or PC', async () => {
  const marker = { v: 1, deviceId: DEVICE, cardId: CARD, requestId: 'network-original', windowId: WINDOW, settled: false }
  const map = new Map([['aiCatLocalNetworkRequestV1', marker]]), h = harness({ storage: map }), s = h.shared.createLiveDeviceSession(h.wx)
  for (const address of ['10.47.0.1', '192.168.1.153']) { const intent = s.prepareLocal(QR, address); s.cancelLocal(intent.token) }
  await assert.rejects(s.useComputerGateway()); assert.throws(() => s.prepareLocal(QR.replace(CARD, '8'.repeat(32)), '192.168.1.152'))
  assert.deepEqual(map.get('aiCatLocalNetworkRequestV1'), marker); assert.equal(h.calls.length, 0); assert.equal(h.writes(), 0)
})
check('bad switch storage remains fail-closed after a later apparently empty read', () => {
  const options = { storageFailure: true }, h = harness(options), s = h.shared.createLiveDeviceSession(h.wx)
  assert.throws(() => s.prepareLocal(QR, '192.168.1.152')); options.storageFailure = false
  assert.throws(() => s.prepareLocal(QR, '192.168.1.152')); assert.equal(h.calls.length, 0)
})
const LEGACY_STORAGE = 'aiCatProvisionPendingV1'
const legacyMarker = (deviceId = DEVICE, sessionId = WINDOW) => ({ v: 1, deviceId, sessionId, requestId: 'original-wifi-request' })
const legacyArchive = () => ({ original: legacyMarker(DEVICE, 'a'.repeat(32)), status: 'unknown', resolution: 'operator_archived', reviewedAt: 123,
  checks: ['window-ended', 'usb-checked', 'accept-unknown'] })
check('real original provisioning core validates v1/v2 pending and archived unknown ledgers read-only', () => {
  for (const [value, pending] of [
    ['', false], [undefined, false], [legacyMarker(), true],
    [{ v: 2, revision: 1, pending: legacyMarker(), archives: [legacyArchive()] }, true],
    [{ v: 2, revision: 1, pending: null, archives: [legacyArchive()] }, false]
  ]) {
    const map = value === undefined ? new Map() : new Map([[LEGACY_STORAGE, value]]), h = harness({ storage: map })
    if (value === undefined) h.wx.getStorageSync = () => undefined
    const inspected = h.legacyLedger().inspectLegacyProvisionPending(h.wx)
    assert.deepEqual(plain(inspected), { pending, reviewIdentity: pending ? { deviceId: DEVICE, sessionId: WINDOW, requestId: 'original-wifi-request' } : null })
    assert.deepEqual(map.get(LEGACY_STORAGE), value); assert.equal(h.calls.length, 0); assert.equal(h.writes(), 0)
  }
})
check('old v1/v2 pending permits only original-device local recovery, never PC or another device', async () => {
  for (const value of [legacyMarker(), { v: 2, revision: 1, pending: legacyMarker(), archives: [legacyArchive()] }]) {
    const map = new Map([[LEGACY_STORAGE, value]]), h = harness({ storage: map }), s = h.shared.createLiveDeviceSession(h.wx)
    for (const address of ['10.47.0.1', '192.168.1.153']) { const intent = s.prepareLocal(QR, address); s.cancelLocal(intent.token) }
    await assert.rejects(s.useComputerGateway())
    assert.throws(() => s.prepareLocal(QR.replace(DEVICE, 'dev_' + '9'.repeat(32)), '192.168.1.152'))
    assert.deepEqual(map.get(LEGACY_STORAGE), value); assert.equal(h.calls.length, 0); assert.equal(h.writes(), 0)
  }
})
check('a legacy pending appearing while the pair modal is open is rechecked before any native request', async () => {
  const map = new Map(), h = harness({ storage: map }), s = h.shared.createLiveDeviceSession(h.wx), detach = s.attach(() => {})
  const intent = s.prepareLocal(QR, '192.168.1.152'), pending = legacyMarker('dev_' + '9'.repeat(32))
  map.set(LEGACY_STORAGE, pending)
  await assert.rejects(s.confirmLocal(intent.token))
  assert.deepEqual(map.get(LEGACY_STORAGE), pending); assert.equal(s.getState().authKind, undefined)
  assert.equal(h.calls.length, 0); assert.equal(h.writes(), 0); detach()
})
check('legacy malformed archives or storage failures stay sticky across inspection/session recreation and later empty reads', () => {
  for (const value of [null, {}, { ...legacyMarker(), extra: true },
    { v: 2, revision: 1, pending: null, archives: [{ ...legacyArchive(), checks: ['window-ended'] }] },
    { v: 2, revision: 2, pending: legacyMarker(), archives: [{ ...legacyArchive(), original: legacyMarker() }] }
  ]) {
    const map = new Map([[LEGACY_STORAGE, value]]), h = harness({ storage: map })
    assert.throws(() => h.legacyLedger().inspectLegacyProvisionPending(h.wx), /未决配网记录不可验证/)
    map.set(LEGACY_STORAGE, '')
    assert.throws(() => h.legacyLedger().inspectLegacyProvisionPending(h.wx), /未决配网记录不可验证/)
    assert.throws(() => h.shared.createLiveDeviceSession(h.wx).prepareLocal(QR, '192.168.1.152'))
    assert.equal(h.calls.length, 0); assert.equal(h.writes(), 0)
  }
  const options = { storageFailure: true }, h = harness(options)
  assert.throws(() => h.legacyLedger().inspectLegacyProvisionPending(h.wx))
  options.storageFailure = false
  assert.throws(() => h.legacyLedger().inspectLegacyProvisionPending(h.wx))
})

const REVIEW_ACKS = ['window-ended', 'network-stable', 'device-idle', 'accept-unknown']
const reviewOriginal = () => ({ deviceId: DEVICE, requestId: 'original_outer', windowId: WINDOW, networkRequestId: 'wifi_original' })
const review = (archived = false) => ({ eligible: !archived, cardId: CARD, original: reviewOriginal(), status: 'unknown',
  resolution: archived ? 'operator_archived' : 'pending', reviewId: archived ? '9'.repeat(32) : null,
  archivedAt: archived ? 941700000000 : null, acknowledgements: archived ? REVIEW_ACKS : [] })
const stageBody = () => ({ requestId: 'original_outer', windowId: WINDOW, networkRequestId: 'wifi_original', acknowledgements: REVIEW_ACKS })
const stageReply = () => ({ cardId: CARD, original: reviewOriginal(), reviewToken: 'a'.repeat(43), reviewId: '9'.repeat(32), expiresAt: Date.parse('1999-12-31T23:00:30Z') / 1000 })
check('closeout metadata and four-ack confirmation use original IDs but fresh authenticated AES nonces only', async () => {
  const h = harness({ inner(request, original) {
    let data = original
    if (request.path.endsWith('/archive-status')) data = review()
    if (request.path.endsWith('/archive-prepare')) { assert.deepEqual(request.body, stageBody()); data = stageReply() }
    if (request.path.endsWith('/archive-confirm')) data = review(true)
    return { status: 200, body: { success: true, data } }
  } }), c = h.client(); await c.login()
  assert.equal((await c.readLocalProvisionArchive('original_outer')).resolution, 'pending')
  const stage = await c.prepareLocalProvisionArchive(stageBody()); assert.equal(stage.expiresAt * 1000, NOW + 30000)
  const result = await c.confirmLocalProvisionArchive({ ...stageBody(), reviewToken: stage.reviewToken, confirmation: 'archive-unknown-provisioning' })
  assert.equal(result.resolution, 'operator_archived'); assert.equal(result.status, 'unknown')
  const packets = h.calls.filter(x => x.data).map(x => x.data)
  assert.equal(new Set(packets.map(x => x.nonce)).size, 4)
  assert.equal(h.rpc.some(x => ['/local/provision/prepare', '/v1/pets/' + PET + '/motion/execute'].includes(x.path)), false)
  assert.equal(h.writes(), 0); assert.equal(h.logs.length, 0); c.destroy()
})
check('authenticated closeout metadata with wrong original card tuple outcome or extra fields is rejected', async () => {
  for (const patch of [x => { x.cardId = 'f'.repeat(32) }, x => { x.original.networkRequestId = 'different' },
    x => { x.original.deviceId = 'dev_' + 'f'.repeat(32) }, x => { x.extra = true },
    x => { x.status = 'connected' }, x => { x.eligible = true }, x => { x.acknowledgements.reverse() }, x => { x.archivedAt = -1 }]) {
    const h = harness({ inner(request, original) { let data = original; if (request.path.endsWith('/archive-confirm')) { data = plain(review(true)); patch(data) }; return { status: 200, body: { success: true, data } } } }), c = h.client()
    await c.login()
    await assert.rejects(c.confirmLocalProvisionArchive({ ...stageBody(), reviewToken: 'a'.repeat(43), confirmation: 'archive-unknown-provisioning' }))
    assert.equal(h.writes(), 0); c.destroy()
  }
})
check('missing facts malformed original IDs or forbidden closeout arguments never reach native request', async () => {
  const h = harness(), c = h.client(); await c.login(); const calls = h.calls.length
  for (const body of [{ ...stageBody(), acknowledgements: REVIEW_ACKS.slice(0, 3) }, { ...stageBody(), windowId: 'bad' },
    { ...stageBody(), extra: 'not allowed' }, { ...stageBody(), networkRequestId: '' }]) await assert.rejects(c.prepareLocalProvisionArchive(body))
  await assert.rejects(c.confirmLocalProvisionArchive({ ...stageBody(), reviewToken: 'a'.repeat(43), confirmation: 'mark-connected' }))
  assert.equal(h.calls.length, calls); assert.equal(h.writes(), 0); c.destroy()
})

const watchdog = setTimeout(() => { throw Error('Synthetic pending test did not settle') }, 60000)
;(async () => { for (const [name, run] of cases) { await run(); console.log('PASS ' + name) } console.log('local-device-client-smoke: ' + cases.length + '/' + cases.length + ' (synthetic wx, independent Node AES; no real device)') })().catch(error => { console.error(error); process.exitCode = 1 }).finally(() => clearTimeout(watchdog))
