// Entirely offline: real TS client, injected fake wx, fake time, no WeChat login or HTTP.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..')
const TOKEN = 'synthetic-access-token-private-in-memory-only'
const NOW = Date.parse('2026-11-01T00:00:00Z')
const authData = () => ({ accessToken: TOKEN, userId: 'user_1', expiresAt: '2026-11-01T01:00:00Z' })
const statusData = () => ({ readOnly: true, sampledAt: '2026-11-01T00:00:00Z',
  health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' },
  device: { connected: true, deviceSerial: '7c2****a128', networkName: null, batteryPercent: 93, batteryState: 'charging',
    batteryVoltage: 4.175, chargerOnline: true, batteryPresent: true, lastSeenAt: null },
  dialog: { state: 'offline', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false }
})
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }

function harness(options, runtime = {}) {
  let now = NOW, nextTimer = 0, aborted = 0
  const requests = [], logins = [], logs = [], timers = new Map(), modules = new Map()
  class ClockDate extends Date {
    constructor(...args) { if (args.length) super(...args); else super(now) }
    static now() { return now }
  }
  const wx = {
    login(value) { logins.push(value) },
    request(value) { requests.push(value); return { abort() { aborted++; value.fail({ errMsg: 'abort: secret response' }) } } },
    setStorageSync() { throw Error('storage must not be used') }, setStorage() { throw Error('storage must not be used') },
    getStorageSync() { throw Error('storage must not be used') }
  }
  Object.assign(wx, runtime)
  const context = vm.createContext({ Date: ClockDate,
    setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, { fn, at: now + delay }); return id },
    clearTimeout(id) { timers.delete(id) },
    console: Object.fromEntries(['log', 'error', 'warn', 'info', 'debug'].map(name => [name, (...args) => logs.push(args)]))
  })
  function load(relative) {
    const filename = path.resolve(root, relative)
    if (modules.has(filename)) return modules.get(filename).exports
    const module = { exports: {} }
    modules.set(filename, module)
    const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
    }).outputText
    const wrapper = new vm.Script('(function(require,module,exports){' + output + '\n})', { filename }).runInContext(context)
    wrapper(name => load(path.relative(root, path.resolve(path.dirname(filename), name + '.ts'))), module, module.exports)
    return module.exports
  }
  const factory = load('services/cloud-auth-client.ts').createCloudAuthClient
  const client = factory(wx, options)
  return {
    client, requests, logins, logs, timers, aborted: () => aborted,
    factory: value => factory(wx, value),
    success(data, statusCode = 200, index = requests.length - 1) { requests[index].success({ statusCode, data: { success: true, data } }) },
    async authenticate() {
      const pending = client.login()
      logins.at(-1).success({ code: 'one-time-code-' + logins.length })
      await flush()
      this.success(authData())
      return pending
    },
    async advance(ms) {
      now += ms
      for (;;) {
        const next = [...timers.entries()].find(([, timer]) => timer.at <= now)
        if (!next) break
        timers.delete(next[0]); next[1].fn(); await flush()
      }
      await flush()
    }
  }
}

function rejected(promise, code) {
  return assert.rejects(promise, error => error.code === code && !/synthetic|one-time-code|raw-secret|session_key|AppSecret/.test(error.message))
}

const cases = {
  async 'Android develop uses only the explicit trusted LAN pin across health and login'() {
    const h = harness(undefined, {
      getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
      getDeviceInfo: () => ({ platform: 'android' })
    })
    const health = h.client.getHealth()
    assert.equal(h.requests.at(-1).url, 'http://192.168.1.110:8788/health')
    h.success({ mode: 'local-development', wechatConfigured: true, readOnly: false }); await health
    await h.authenticate()
    assert.equal(h.requests.at(-1).url, 'http://192.168.1.110:8788/v1/auth/wechat')
    const list = h.client.listPets()
    assert.equal(h.requests.at(-1).url, 'http://192.168.1.110:8788/v1/pets')
    assert.equal(h.requests.at(-1).header.Authorization, 'Bearer ' + TOKEN)
    h.success([]); await list
    assert.equal(h.logs.length, 0)
  },
  async 'only Android develop may explicitly request the fixed LAN origin'() {
    const runtime = { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), getSystemInfoSync: () => ({ platform: 'android' }) }
    const h = harness({ baseUrl: 'http://192.168.1.110:8788' }, runtime)
    const pending = h.client.getHealth()
    assert.equal(h.requests.at(-1).url, 'http://192.168.1.110:8788/health')
    h.success({ mode: 'local-development', wechatConfigured: true, readOnly: false }); await pending
    for (const baseUrl of ['http://192.168.1.111:8788', 'http://192.168.1.110:8787', 'http://192.168.1.110:8788/', 'http://192.168.1.110:8788?x=1', 'http://user@192.168.1.110:8788']) {
      assert.throws(() => h.factory({ baseUrl }), error => error.code === 'CONFIG_INVALID')
    }
  },
  async 'Android develop cannot override an explicit HTTPS or loopback configuration'() {
    const runtime = { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), getDeviceInfo: () => ({ platform: 'android' }) }
    for (const baseUrl of ['http://127.0.0.1:8787', 'https://api.example.com']) {
      const h = harness({ baseUrl }, runtime), pending = h.client.getHealth()
      assert.equal(h.requests.at(-1).url, baseUrl + '/health')
      h.success({ mode: 'local-development', wechatConfigured: true, readOnly: false }); await pending
    }
    const h = harness({}, runtime), pending = h.client.getHealth()
    assert.equal(h.requests.at(-1).url, 'http://192.168.1.110:8788/health')
    h.success({ mode: 'local-development', wechatConfigured: true, readOnly: false }); await pending
  },
  async 'only fixed motion POSTs get a ten second deadline and never auto retry'() {
    for (const operation of ['execute', 'stop']) {
      const h = harness(); await h.authenticate()
      const before = h.requests.length
      const pending = operation === 'execute' ? h.client.executeMotion('pet_1', 'head_nod', 'motion_1') : h.client.stopMotion('pet_1', 'stop_1')
      const rejectedRequest = rejected(pending, 'TIMEOUT')
      assert.equal(h.requests.at(-1).timeout, 10000)
      assert.equal(h.requests.at(-1).method, 'POST')
      assert.ok(h.requests.at(-1).url.endsWith('/motion/' + operation))
      await h.advance(5000)
      assert.equal(h.aborted(), 0); assert.equal(h.requests.length, before + 1)
      await h.advance(4999)
      assert.equal(h.aborted(), 0)
      await h.advance(1); await rejectedRequest
      assert.equal(h.aborted(), 1); assert.equal(h.requests.length, before + 1)
      h.success({ invented: 'late response must be ignored' }); await flush()
      assert.equal(h.aborted(), 1); assert.equal(h.requests.length, before + 1)
    }
  },
  async 'motion and other GETs keep five second native and local deadlines'() {
    for (const operation of ['health', 'motion', 'history', 'pets']) {
      const h = harness(); await h.authenticate()
      const pending = operation === 'health' ? h.client.getHealth() : operation === 'motion' ? h.client.readMotion('pet_1') : operation === 'history' ? h.client.readMotionHistory('pet_1') : h.client.listPets()
      const rejectedRequest = rejected(pending, 'TIMEOUT')
      assert.equal(h.requests.at(-1).timeout, 5000); assert.equal(h.requests.at(-1).method, 'GET')
      await h.advance(4999); assert.equal(h.aborted(), 0)
      await h.advance(1); await rejectedRequest
      assert.equal(h.aborted(), 1)
    }
  },
  async 'factory is dormant and does not change the Mock service'() {
    const h = harness()
    assert.equal(h.requests.length, 0); assert.equal(h.logins.length, 0); assert.equal(h.logs.length, 0)
    const source = fs.readFileSync(path.join(root, 'services/pet-service.ts'), 'utf8')
    assert.ok(source.includes('new MockPetService()'))
    assert.ok(!source.includes('cloud-auth-client'))
  },
  async 'health is fixed unauthenticated GET and preserves unconfigured readiness'() {
    const h = harness()
    const pending = h.client.getHealth()
    assert.equal(h.requests[0].url, 'http://127.0.0.1:8787/health')
    assert.equal(h.requests[0].method, 'GET'); assert.equal(h.requests[0].timeout, 5000)
    assert.equal(h.requests[0].header.Authorization, undefined)
    h.success({ mode: 'local-development', wechatConfigured: false, readOnly: true, secret: 'raw-secret' })
    assert.deepEqual(JSON.parse(JSON.stringify(await pending)), { mode: 'local-development', wechatConfigured: false, readOnly: true })
  },
  async 'login uses fresh wx code in POST body and only returns public metadata'() {
    const h = harness()
    const pending = h.client.login()
    assert.equal(h.logins.length, 1); assert.equal(h.logins[0].timeout, 5000)
    h.logins[0].success({ code: 'one-time-code' })
    await flush()
    const request = h.requests[0]
    assert.equal(request.url, 'http://127.0.0.1:8787/v1/auth/wechat')
    assert.equal(request.method, 'POST'); assert.equal(request.data.code, 'one-time-code')
    assert.equal(request.timeout, 5000)
    assert.equal(request.header.Authorization, undefined)
    h.success({ ...authData(), session_key: 'raw-secret' })
    const metadata = await pending
    assert.deepEqual(JSON.parse(JSON.stringify(metadata)), { userId: 'user_1', expiresAt: authData().expiresAt })
    assert.ok(!JSON.stringify(metadata).includes(TOKEN)); assert.equal(h.logs.length, 0)
  },
  async 'concurrent login is single flight'() {
    const h = harness()
    const first = h.client.login(), second = h.client.login()
    assert.equal(first, second); assert.equal(h.logins.length, 1)
    h.logins[0].success({ code: 'one-time-code' }); await flush(); h.success(authData())
    await Promise.all([first, second])
    assert.equal(h.requests.length, 1)
  },
  async 'failed login retry gets a new wx code instead of resending the old one'() {
    const h = harness()
    const first = h.client.login()
    h.logins[0].success({ code: 'old-code' }); await flush()
    h.success({ message: 'raw-secret' }, 502); await rejected(first, 'UNAVAILABLE')
    const retry = h.client.login()
    assert.equal(h.logins.length, 2)
    h.logins[1].success({ code: 'new-code' }); await flush()
    assert.equal(h.requests[1].data.code, 'new-code'); h.success(authData()); await retry
  },
  async 'wx login failure is sanitized and never reaches auth HTTP'() {
    const h = harness(), pending = h.client.login()
    h.logins[0].fail({ errMsg: 'raw-secret session_key AppSecret' })
    await rejected(pending, 'LOGIN_FAILED'); assert.equal(h.requests.length, 0)
  },
  async 'native and remote errors cannot impersonate internal local diagnostic brands'() {
    const h = harness(), pending = h.client.getHealth()
    const failed = rejected(pending, 'UNAVAILABLE')
    h.requests.at(-1).fail({ errMsg: 'raw-secret', code: 'LOCAL_RANDOM', localDiagnostic: 'LOCAL_RANDOM', diagnosticCode: 'LOCAL_RANDOM' })
    await failed
    const response = h.client.getHealth(), rejectedResponse = rejected(response, 'UNAVAILABLE')
    h.requests.at(-1).success({ statusCode: 503, data: { success: false, code: 'LOCAL_RANDOM', localDiagnostic: 'LOCAL_RANDOM', message: 'raw-secret' } })
    await rejectedResponse; assert.equal(h.logs.length, 0)
  },
  async 'wx login has an independent deadline and late code cannot be exchanged'() {
    const h = harness(), pending = h.client.login()
    const failed = rejected(pending, 'TIMEOUT')
    await h.advance(5000); await failed
    h.logins[0].success({ code: 'late-code' }); await flush()
    assert.equal(h.requests.length, 0)
  },
  async 'HTTP timeout aborts and late authentication cannot restore session'() {
    const h = harness(), pending = h.client.login()
    h.logins[0].success({ code: 'one-time-code' }); await flush()
    const failed = rejected(pending, 'TIMEOUT')
    await h.advance(5000); await failed
    assert.equal(h.aborted(), 1)
    h.success(authData()); await flush()
    await rejected(h.client.listPets(), 'AUTH_REQUIRED')
  },
  async 'clear during wx login cancels promptly and ignores late code'() {
    const h = harness(), pending = h.client.login()
    h.client.clearSession(); await rejected(pending, 'CANCELLED')
    h.logins[0].success({ code: 'late-code' }); await flush()
    assert.equal(h.requests.length, 0)
  },
  async 'clear during auth exchange aborts and ignores late token'() {
    const h = harness(), pending = h.client.login()
    h.logins[0].success({ code: 'code' }); await flush()
    h.client.clearSession(); await rejected(pending, 'CANCELLED')
    assert.equal(h.aborted(), 1); h.success(authData()); await flush()
    await rejected(h.client.getSession(), 'AUTH_REQUIRED')
  },
  async 'protected session lookup uses bearer and strips extra fields'() {
    const h = harness(); await h.authenticate()
    const pending = h.client.getSession()
    assert.equal(h.requests.at(-1).url, 'http://127.0.0.1:8787/v1/session')
    assert.equal(h.requests.at(-1).header.Authorization, 'Bearer ' + TOKEN)
    h.success({ userId: 'user_1', expiresAt: authData().expiresAt, accessToken: 'raw-secret' })
    assert.equal(Object.keys(await pending).sort().join(','), 'expiresAt,userId')
  },
  async 'listPets uses fixed GET and returns cloned approved fields'() {
    const h = harness(); await h.authenticate()
    const pending = h.client.listPets()
    assert.equal(h.requests.at(-1).url, 'http://127.0.0.1:8787/v1/pets')
    assert.equal(h.requests.at(-1).method, 'GET')
    h.success([{ petId: 'pet_1', name: 'AI 猫', internal: 'raw-secret' }])
    assert.deepEqual(JSON.parse(JSON.stringify(await pending)), [{ petId: 'pet_1', name: 'AI 猫' }])
  },
  async 'status reuses the existing strict read-only validator without demo substitution'() {
    const h = harness(); await h.authenticate()
    const pending = h.client.readPetStatus('pet_1')
    assert.equal(h.requests.at(-1).url, 'http://127.0.0.1:8787/v1/pets/pet_1/status')
    assert.equal(h.requests.at(-1).method, 'GET')
    h.success(statusData())
    assert.deepEqual(JSON.parse(JSON.stringify(await pending)), statusData())
  },
  async 'Mock adapter status is rejected rather than silently downgraded'() {
    const h = harness(); await h.authenticate()
    const pending = h.client.readPetStatus('pet_1'), value = statusData()
    value.health.adapter = 'mock'; h.success(value)
    await rejected(pending, 'UNVERIFIED')
  },
  async '401 clears the session and cancels other in-flight reads'() {
    const h = harness(); await h.authenticate()
    const first = h.client.listPets(), other = h.client.readPetStatus('pet_1')
    h.success({ message: 'raw-secret' }, 401, 1)
    await rejected(first, 'UNAUTHORIZED'); await rejected(other, 'CANCELLED')
    await rejected(h.client.getSession(), 'AUTH_REQUIRED')
  },
  async 'clear cancels reads and stale 401 cannot clear a replacement login'() {
    const h = harness(); await h.authenticate()
    const old = h.client.listPets(), oldIndex = h.requests.length - 1
    h.client.clearSession(); await rejected(old, 'CANCELLED')
    await h.authenticate(); h.success({ message: 'raw-secret' }, 401, oldIndex)
    const current = h.client.listPets(); h.success([]); assert.equal((await current).length, 0)
  },
  async 'logout clears locally immediately and POSTs only the previous bearer'() {
    const h = harness(); await h.authenticate()
    const pending = h.client.logout()
    assert.equal(h.requests.at(-1).url, 'http://127.0.0.1:8787/v1/auth/logout')
    assert.equal(h.requests.at(-1).method, 'POST')
    await rejected(h.client.listPets(), 'AUTH_REQUIRED')
    h.success({ loggedOut: true }); assert.equal((await pending).loggedOut, true)
  },
  async 'failed logout still leaves local memory logged out'() {
    const h = harness(); await h.authenticate()
    const pending = h.client.logout(); h.success({ message: 'raw-secret' }, 502)
    await rejected(pending, 'UNAVAILABLE'); await rejected(h.client.getSession(), 'AUTH_REQUIRED')
  },
  async 'logout without a session is local and idempotent'() {
    const h = harness(); assert.equal((await h.client.logout()).loggedOut, true)
    assert.equal(h.requests.length, 0)
  },
  async 'expired local session cannot issue an authenticated request'() {
    const h = harness(); await h.authenticate(); await h.advance(3600001)
    await rejected(h.client.listPets(), 'AUTH_REQUIRED'); assert.equal(h.requests.length, 1)
  },
  async 'safe session peek is local and never exposes the bearer or user ID'() {
    const h = harness(); assert.equal(h.client.peekSession(), null); await h.authenticate()
    assert.deepEqual(JSON.parse(JSON.stringify(h.client.peekSession())), { expiresAt: authData().expiresAt })
    const value = h.client.peekSession(); value.expiresAt = 'tampered'
    assert.equal(h.client.peekSession().expiresAt, authData().expiresAt); assert.equal(h.requests.length, 1)
  },
  async 'safe session peek expires locally without issuing HTTP'() {
    const h = harness(); await h.authenticate(); await h.advance(3600001)
    assert.equal(h.client.peekSession(), null); assert.equal(h.requests.length, 1)
    await rejected(h.client.listPets(), 'AUTH_REQUIRED')
  },
  async 'cancelPending aborts old reads without losing valid authentication'() {
    const h = harness(); await h.authenticate()
    const old = h.client.listPets(), index = h.requests.length - 1
    h.client.cancelPending(); await rejected(old, 'CANCELLED'); assert.equal(h.aborted(), 1)
    assert.ok(h.client.peekSession()); h.success({}, 401, index)
    const fresh = h.client.listPets(); h.success([]); assert.equal((await fresh).length, 0)
  },
  async 'cancelPending during wx login prevents late code exchange'() {
    const h = harness(), p = h.client.login(); h.client.cancelPending(); await rejected(p, 'CANCELLED')
    h.logins[0].success({ code: 'late-code' }); await flush()
    assert.equal(h.requests.length, 0); assert.equal(h.client.peekSession(), null)
  },
  async 'session identity mismatch clears local authentication'() {
    const h = harness(); await h.authenticate()
    const pending = h.client.getSession(); h.success({ userId: 'other_user', expiresAt: authData().expiresAt })
    await rejected(pending, 'UNVERIFIED'); await rejected(h.client.listPets(), 'AUTH_REQUIRED')
  },
  async 'clear during old login cannot settle or unlock its replacement flight'() {
    const h = harness(), old = h.client.login()
    h.client.clearSession(); await rejected(old, 'CANCELLED')
    const current = h.client.login()
    h.logins[0].success({ code: 'old-code' }); await flush()
    assert.equal(h.client.login(), current); assert.equal(h.logins.length, 2)
    h.logins[1].success({ code: 'new-code' }); await flush(); h.success(authData()); await current
  },
  async 'invalid health shape does not pretend WeChat is configured'() {
    const h = harness(), pending = h.client.getHealth()
    h.success({ mode: 'local-development', wechatConfigured: 'false', readOnly: true })
    await rejected(pending, 'UNVERIFIED')
  }
}

for (const [name, runtime] of [
  ['trial Android', { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'trial' } }), getDeviceInfo: () => ({ platform: 'android' }) }],
  ['release Android', { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'release' } }), getDeviceInfo: () => ({ platform: 'android' }) }],
  ['develop devtools', { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), getDeviceInfo: () => ({ platform: 'devtools' }) }],
  ['develop iOS', { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), getDeviceInfo: () => ({ platform: 'ios' }) }],
  ['missing account API', { getDeviceInfo: () => ({ platform: 'android' }) }],
  ['missing device API', { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }) }],
  ['account API throws', { getAccountInfoSync: () => { throw Error('not available') }, getDeviceInfo: () => ({ platform: 'android' }) }],
  ['device API throws', { getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }), getDeviceInfo: () => { throw Error('not available') } }],
  ['malformed account result', { getAccountInfoSync: () => null, getDeviceInfo: () => ({ platform: 'android' }) }]
]) {
  cases['LAN disabled without explicit runtime proof: ' + name] = async () => {
    const h = harness(undefined, runtime), pending = h.client.getHealth()
    assert.equal(h.requests.at(-1).url, 'http://127.0.0.1:8787/health')
    h.success({ mode: 'local-development', wechatConfigured: true, readOnly: false }); await pending
    assert.throws(() => h.factory({ baseUrl: 'http://192.168.1.110:8788' }), error => error.code === 'CONFIG_INVALID')
  }
}

for (const baseUrl of ['http://127.0.0.1:8787', 'https://api.example.com', 'https://api.example.com:8443']) {
  cases['explicit accepted origin ' + baseUrl] = async () => {
    const h = harness({ baseUrl }), pending = h.client.getHealth()
    assert.equal(h.requests[0].url, baseUrl + '/health')
    h.success({ mode: 'local-development', wechatConfigured: true, readOnly: true }); await pending
  }
}
for (const baseUrl of ['http://localhost:8787', 'http://127.0.0.1:8788', 'http://api.example.com',
  'https://api.example.com/', 'https://user:pass@api.example.com', 'https://api.example.com/path',
  'https://api.example.com?token=x', 'https://api.example.com#fragment', 'https://169.254.169.254',
  'https://127.0.0.1', 'https://[::1]', 'https://2130706433', 'https://0x7f000001',
  'https://metadata.google.internal', 'https://localhost', 'https://api.example.com:0',
  'https://api.example.com:65536', 'https://api.example.com\\evil', ' https://api.example.com']) {
  cases['reject unsafe origin ' + baseUrl] = async () => {
    const h = harness()
    assert.throws(() => h.factory({ baseUrl }), error => error.code === 'CONFIG_INVALID')
    assert.equal(h.requests.length, 0); assert.equal(h.logins.length, 0)
  }
}
for (const [name, edit] of [
  ['short token', value => { value.accessToken = 'short' }],
  ['header injection', value => { value.accessToken = TOKEN + '\r\nx: secret' }],
  ['invalid user ID', value => { value.userId = '../user' }],
  ['missing token', value => { delete value.accessToken }],
  ['expired token', value => { value.expiresAt = '2026-10-31T23:59:00Z' }],
  ['non-UTC expiry', value => { value.expiresAt = '2026-11-01T01:00:00+00:00' }],
  ['invalid calendar expiry', value => { value.expiresAt = '2026-11-31T01:00:00Z' }]
]) {
  cases['reject authentication response ' + name] = async () => {
    const h = harness(), pending = h.client.login()
    h.logins[0].success({ code: 'one-time-code' }); await flush()
    const value = authData(); edit(value); h.success(value)
    await rejected(pending, 'UNVERIFIED'); await rejected(h.client.listPets(), 'AUTH_REQUIRED')
  }
}
for (const petId of ['', '../pet', 'a/b', 'a?token=x', 'a#x', 'x'.repeat(129), '猫']) {
  cases['reject invalid pet ID ' + JSON.stringify(petId)] = async () => {
    const h = harness(); await h.authenticate()
    await rejected(h.client.readPetStatus(petId), 'INVALID_ID'); assert.equal(h.requests.length, 1)
  }
}
for (const value of [null, {}, [{ petId: '../pet', name: 'AI 猫' }], [{ petId: 'pet', name: '' }],
  [{ petId: 'pet', name: 'name\nraw-secret' }], [{ petId: 'pet', name: 'one' }, { petId: 'pet', name: 'duplicate' }]]) {
  cases['reject invalid pet list ' + JSON.stringify(value)] = async () => {
    const h = harness(); await h.authenticate()
    const pending = h.client.listPets(); h.success(value); await rejected(pending, 'UNVERIFIED')
  }
}

async function main() {
  let failures = 0
  for (const [name, run] of Object.entries(cases)) {
    try { await run() }
    catch (error) { failures++; console.error('FAIL ' + name + ': ' + error.message) }
  }
  console.log(`cloud-auth-client-smoke: ${Object.keys(cases).length - failures}/${Object.keys(cases).length} cases passed (injected wx only; no real login, HTTP or storage)`)
  if (failures) process.exitCode = 1
}
main().catch(error => { console.error(error); process.exitCode = 1 })
