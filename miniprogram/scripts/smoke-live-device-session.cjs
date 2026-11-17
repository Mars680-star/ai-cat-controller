// Offline deterministic integration of the shared session and real TS clients.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..')
const NOW = Date.parse('2026-11-01T00:00:00Z')
const TOKEN = 'synthetic-private-access-token-no-storage'
const PETS = [{ petId: 'pet_1', name: '小安' }, { petId: 'pet_2', name: '另一只猫' }]
const health = () => ({ mode: 'local-development', wechatConfigured: true, readOnly: true })
const snapshot = (batteryPercent = 66) => ({ readOnly: true, sampledAt: '2026-11-01T00:00:00Z',
  health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' },
  device: { connected: true, deviceSerial: '7c2****a128', networkName: null, batteryPercent,
    batteryState: 'charging', batteryVoltage: 4.1, chargerOnline: true, batteryPresent: true, lastSeenAt: null },
  dialog: { state: 'offline', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false }
})
const plain = value => JSON.parse(JSON.stringify(value))
const flush = async () => { for (let i = 0; i < 35; i++) await Promise.resolve() }

function harness(options, runtime = {}) {
  let now = NOW, nextTimer = 0, aborted = 0, storageCalls = 0
  const requests = [], logins = [], logs = [], timers = new Map(), modules = new Map(), views = []
  class ClockDate extends Date {
    constructor(...args) { if (args.length) super(...args); else super(now) }
    static now() { return now }
  }
  const wx = {
    login(value) { logins.push(value) },
    request(value) { requests.push(value); return { abort() { aborted++; value.fail({ errMsg: 'aborted raw-secret' }) } } },
    setStorageSync() { storageCalls++; throw Error('no storage') }, setStorage() { storageCalls++; throw Error('no storage') },
    getStorageSync() { storageCalls++; throw Error('no storage') }
  }
  Object.assign(wx, runtime)
  const context = vm.createContext({ wx, Date: ClockDate,
    setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, { fn, at: now + delay }); return id },
    clearTimeout(id) { timers.delete(id) },
    console: Object.fromEntries(['log', 'error', 'warn', 'info', 'debug'].map(name => [name, (...args) => logs.push(args)]))
  })
  function load(relative) {
    const filename = path.resolve(root, relative)
    if (modules.has(filename)) return modules.get(filename).exports
    const module = { exports: {} }; modules.set(filename, module)
    const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
    }).outputText
    const wrapper = new vm.Script('(function(require,module,exports){' + output + '\n})', { filename }).runInContext(context)
    wrapper(name => load(path.relative(root, path.resolve(path.dirname(filename), name + '.ts'))), module, module.exports)
    return module.exports
  }
  const api = load('services/live-device-session.ts'), session = api.createLiveDeviceSession(wx, options)
  const h = { session, requests, logins, timers, views, wx, api, aborted: () => aborted, storage: () => storageCalls, logs,
    attach(listener = state => views.push(state)) { return session.attach(listener) },
    success(value, statusCode = 200, index = requests.length - 1) { requests[index].success({ statusCode, data: { success: true, data: value } }) },
    failure(index = requests.length - 1) { requests[index].fail({ errMsg: 'raw-secret AppSecret session_key' }) },
    async ready() { this.detach = this.attach(); const p = session.enable(); this.success(health()); await p },
    async authenticate(pets = PETS, expiresAt = '2026-11-01T01:00:00Z') {
      await this.ready()
      const p = session.login()
      logins.at(-1).success({ code: 'one-time-code' }); await flush()
      this.success({ accessToken: TOKEN, userId: 'user_hidden', expiresAt }); await flush()
      this.success(pets); await p
    },
    async select(petId = 'pet_1', value = snapshot()) { const p = session.selectPet(petId); this.success(value); await p },
    async advance(ms) {
      now += ms
      for (;;) {
        const due = [...timers.entries()].filter(([, timer]) => timer.at <= now).sort((a, b) => a[1].at - b[1].at)[0]
        if (!due) break
        timers.delete(due[0]); due[1].fn(); await flush()
      }
      await flush()
    }
  }
  return h
}

const cases = {
  async 'shared and independent Android develop sessions select the same pinned phone endpoint'() {
    const h = harness(undefined, {
      getAccountInfoSync: () => ({ miniProgram: { envVersion: 'develop' } }),
      getDeviceInfo: () => ({ platform: 'android' })
    })
    assert.equal(h.requests.length, 0)
    const shared = h.api.getLiveDeviceSession()
    assert.equal(h.requests.length, 0)
    const detach = shared.attach(() => {})
    const sharedRead = shared.enable()
    assert.equal(h.requests.at(-1).url, 'http://192.168.1.110:8788/health')
    h.success(health()); await sharedRead; detach()
    await h.ready()
    assert.equal(h.requests.at(-1).url, 'http://192.168.1.110:8788/health')
    assert.equal(h.logins.length, 0); assert.equal(h.storage(), 0)
    h.detach()
  },
  async 'factory starts in preview without network or storage'() {
    const h = harness(), state = h.session.getState()
    assert.equal(state.mode, 'preview'); assert.equal(state.signedIn, false); assert.equal(state.snapshot, null)
    assert.equal(h.requests.length, 0); assert.equal(h.logins.length, 0); assert.equal(h.storage(), 0)
  },
  async 'singleton is shared but independent factory has its own state'() {
    const h = harness(), one = h.api.getLiveDeviceSession(), two = h.api.getLiveDeviceSession()
    assert.equal(one, two); assert.notEqual(one, h.session)
    await h.session.enable(); assert.equal(one.getState().mode, 'preview')
  },
  async 'preview subscriptions and unavailable lifecycle actions are dormant'() {
    const h = harness(), detach = h.attach()
    await h.session.login(); await h.session.refreshPets(); await h.session.refresh(); await h.session.selectPet('pet_1')
    detach(); assert.equal(h.requests.length, 0); assert.equal(h.logins.length, 0)
  },
  async 'enable without observers waits until a page attaches'() {
    const h = harness(); await h.session.enable()
    assert.equal(h.session.getState().mode, 'live'); assert.equal(h.requests.length, 0)
    h.attach(); assert.equal(h.requests.length, 1); h.success(health()); await flush()
    assert.equal(h.session.getState().health.wechatConfigured, true); assert.equal(h.logins.length, 0)
  },
  async 'enable only checks gateway and concurrent health requests are single flight'() {
    const h = harness(); h.attach()
    const first = h.session.enable(), second = h.session.checkHealth()
    assert.equal(first, second); assert.equal(h.requests.length, 1); assert.equal(h.logins.length, 0)
    h.success(health()); await first
    assert.equal(h.session.getState().phase, 'ready')
  },
  async 'unconfigured gateway cannot trigger login'() {
    const h = harness(); h.attach(); const p = h.session.enable()
    h.success({ ...health(), wechatConfigured: false }); await p; await h.session.login()
    assert.equal(h.logins.length, 0); assert.equal(h.session.getState().signedIn, false)
  },
  async 'health failures are sanitized and never substitute demo values'() {
    const h = harness(); h.attach(); const p = h.session.enable(); h.failure(); await p
    const state = h.session.getState()
    assert.equal(state.health, null); assert.equal(state.snapshot, null); assert.equal(state.phase, 'error')
    assert.doesNotMatch(JSON.stringify(state), /raw-secret|AppSecret|session_key/)
  },
  async 'login is explicit and loads pets without automatically choosing a device'() {
    const h = harness(); await h.authenticate()
    const state = h.session.getState()
    assert.equal(h.logins.length, 1); assert.equal(state.signedIn, true)
    assert.deepEqual(plain(state.pets), PETS); assert.equal(state.selectedPetId, ''); assert.equal(state.snapshot, null)
    assert.equal(h.requests.length, 3); assert.equal(state.listLoading, false)
  },
  async 'single authorized device still requires explicit selection'() {
    const h = harness(); await h.authenticate(PETS.slice(0, 1))
    assert.equal(h.session.getState().selectedPetId, ''); assert.equal(h.requests.length, 3)
  },
  async 'concurrent login shares a single wx code and exchange'() {
    const h = harness(); await h.ready()
    const a = h.session.login(), b = h.session.login(); assert.equal(a, b); assert.equal(h.logins.length, 1)
    h.logins[0].success({ code: 'one-time-code' }); await flush()
    h.success({ accessToken: TOKEN, userId: 'user_hidden', expiresAt: '2026-11-01T01:00:00Z' }); await flush()
    h.success([]); await a; assert.equal(h.session.getState().signedIn, true)
  },
  async 'failed login clears all identity and does not throw to page handlers'() {
    const h = harness(); await h.ready(); const p = h.session.login()
    h.logins[0].fail({ errMsg: 'raw-secret' }); await p
    assert.equal(h.session.getState().signedIn, false); assert.equal(h.session.getState().loading, false)
  },
  async 'empty authorized list remains authenticated but contains no fake pet'() {
    const h = harness(); await h.authenticate([])
    assert.equal(h.session.getState().signedIn, true); assert.equal(h.session.getState().pets.length, 0)
    assert.equal(h.session.getState().snapshot, null); assert.equal(h.timers.size, 1)
  },
  async 'unknown or malformed device selection never reaches the network'() {
    const h = harness(); await h.authenticate(); const count = h.requests.length
    for (const value of ['../pet', 'pet_other', null, undefined, {}, '']) await h.session.selectPet(value)
    assert.equal(h.requests.length, count); assert.equal(h.session.getState().selectedPetId, '')
  },
  async 'selection reads validated status including zero battery and voice offline'() {
    const h = harness(); await h.authenticate(); await h.select('pet_1', snapshot(0))
    const state = h.session.getState()
    assert.equal(state.snapshot.device.batteryPercent, 0); assert.equal(state.snapshot.device.connected, true)
    assert.equal(state.snapshot.dialog.state, 'offline'); assert.equal(state.phase, 'live')
    assert.equal(state.statusLoading, false)
  },
  async 'state and each subscription receive independent safe copies'() {
    const h = harness(); await h.authenticate(); await h.select()
    const a = h.session.getState(); a.pets[0].name = 'tampered'; a.snapshot.device.batteryPercent = 1
    assert.equal(h.session.getState().pets[0].name, '小安'); assert.equal(h.session.getState().snapshot.device.batteryPercent, 66)
    h.attach(state => { state.pets.length = 0 })
    let latest; h.attach(state => { latest = state })
    assert.equal(latest.pets.length, 2)
    for (const value of [...h.views, latest, h.session.getState()]) assert.doesNotMatch(JSON.stringify(value), /synthetic-private|user_hidden|one-time-code|Authorization|accessToken/)
    assert.equal(h.storage(), 0); assert.equal(h.logs.length, 0)
  },
  async 'observer failures do not interrupt other views or state reads'() {
    const h = harness(); h.attach(() => { throw Error('view broken') }); let count = 0; h.attach(() => count++)
    const p = h.session.enable(); h.success(health()); await p
    assert.ok(count > 1); assert.equal(h.session.getState().phase, 'ready')
  },
  async 'manual and polled reads share one status flight'() {
    const h = harness(); await h.authenticate(); await h.select()
    const count = h.requests.length, a = h.session.refresh(), b = h.session.refresh()
    assert.equal(a, b); assert.equal(h.requests.length, count + 1)
    await h.advance(4999); assert.equal(h.requests.length, count + 1)
    h.success(snapshot(67)); await a; await h.advance(4999); assert.equal(h.requests.length, count + 1)
    await h.advance(1); assert.equal(h.requests.length, count + 2)
    h.success(snapshot(68)); await flush(); assert.equal(h.session.getState().snapshot.device.batteryPercent, 68)
  },
  async 'status failure clears previous snapshot and retries serially after five seconds'() {
    const h = harness(); await h.authenticate(); await h.select()
    const p = h.session.refresh(); h.failure(); await p
    assert.equal(h.session.getState().snapshot, null); assert.equal(h.session.getState().phase, 'error')
    const count = h.requests.length; await h.advance(5000); assert.equal(h.requests.length, count + 1)
    h.success(snapshot()); await flush(); assert.equal(h.session.getState().snapshot.device.batteryPercent, 66)
  },
  async 'invalid real snapshot is rejected without demo fallback'() {
    const h = harness(); await h.authenticate(); const p = h.session.selectPet('pet_1')
    const value = snapshot(); value.health.adapter = 'mock'; h.success(value); await p
    assert.equal(h.session.getState().snapshot, null); assert.equal(h.session.getState().phase, 'error')
  },
  async 'changing selection cancels old request and ignores late snapshots'() {
    const h = harness(); await h.authenticate()
    const old = h.session.selectPet('pet_1'), oldIndex = h.requests.length - 1
    const fresh = h.session.selectPet('pet_2'); assert.equal(h.aborted(), 1)
    h.success(snapshot(1), 200, oldIndex); h.success(snapshot(88)); await Promise.all([old, fresh])
    assert.equal(h.session.getState().selectedPetId, 'pet_2'); assert.equal(h.session.getState().snapshot.device.batteryPercent, 88)
  },
  async 'late unauthorized old selection cannot clear current replacement read'() {
    const h = harness(); await h.authenticate()
    const old = h.session.selectPet('pet_1'), oldIndex = h.requests.length - 1
    const fresh = h.session.selectPet('pet_2'); h.success({}, 401, oldIndex); h.success(snapshot(88))
    await Promise.all([old, fresh]); assert.equal(h.session.getState().signedIn, true)
    assert.equal(h.session.getState().snapshot.device.batteryPercent, 88)
  },
  async 'refreshing list cancels status and a removed authorization cannot receive late data'() {
    const h = harness(); await h.authenticate()
    const old = h.session.selectPet('pet_1'), index = h.requests.length - 1
    const list = h.session.refreshPets(); h.success(PETS.slice(1)); await list
    h.success(snapshot(), 200, index); await old
    assert.equal(h.session.getState().selectedPetId, ''); assert.equal(h.session.getState().snapshot, null)
  },
  async 'list refreshes are single flight and preserve explicit selection after revalidation'() {
    const h = harness(); await h.authenticate(); await h.select()
    const count = h.requests.length, a = h.session.refreshPets(), b = h.session.refreshPets()
    assert.equal(a, b); assert.equal(h.requests.length, count + 1)
    h.success(PETS); await flush(); assert.match(h.requests.at(-1).url, /pet_1\/status$/)
    h.success(snapshot(70)); await a; assert.equal(h.session.getState().snapshot.device.batteryPercent, 70)
  },
  async 'list 404 clears list selection and snapshot but preserves valid identity'() {
    const h = harness(); await h.authenticate(); await h.select()
    const p = h.session.refreshPets(); h.success({}, 404); await p
    const state = h.session.getState()
    assert.equal(state.signedIn, true); assert.equal(state.pets.length, 0); assert.equal(state.selectedPetId, '')
    assert.equal(state.snapshot, null); assert.equal(state.listLoading, false)
    const count = h.requests.length; await h.advance(5000); assert.equal(h.requests.length, count)
  },
  async 'refresh recovers a failed list without another wx login or device auto selection'() {
    const h = harness(); await h.authenticate(); await h.select()
    const failed = h.session.refreshPets(); h.failure(); await failed
    const retry = h.session.refresh(); h.success(PETS); await retry
    assert.equal(h.session.getState().pets.length, 2); assert.equal(h.session.getState().selectedPetId, '')
    assert.equal(h.session.getState().error, ''); assert.equal(h.logins.length, 1)
  },
  async 'health retry cancels a status read and cannot restore its late snapshot'() {
    const h = harness(); await h.authenticate(); await h.select()
    const old = h.session.refresh(), index = h.requests.length - 1, retry = h.session.checkHealth()
    h.failure(); await retry; h.success(snapshot(), 200, index); await old
    assert.equal(h.session.getState().health, null); assert.equal(h.session.getState().snapshot, null)
    assert.equal(h.session.getState().signedIn, true)
  },
  async 'status 401 clears identity lists and snapshot immediately'() {
    const h = harness(); await h.authenticate(); await h.select()
    const p = h.session.refresh(); h.success({}, 401); await p
    const state = h.session.getState()
    assert.equal(state.signedIn, false); assert.equal(state.selectedPetId, ''); assert.equal(state.snapshot, null)
    assert.equal(state.pets.length, 0); assert.equal(h.timers.size, 0)
  },
  async 'list 401 clears identity and stops all polling'() {
    const h = harness(); await h.authenticate(); await h.select()
    const p = h.session.refreshPets(); h.success({}, 401); await p
    assert.equal(h.session.getState().signedIn, false); assert.equal(h.timers.size, 0)
  },
  async 'visible session expiration clears snapshot without waiting for next request'() {
    const h = harness(); await h.authenticate(PETS, '2026-11-01T00:00:04Z'); await h.select()
    const count = h.requests.length; await h.advance(4000)
    assert.equal(h.session.getState().signedIn, false); assert.equal(h.session.getState().snapshot, null)
    assert.equal(h.requests.length, count); assert.equal(h.timers.size, 0)
  },
  async 'expiration cancels pending status and late response cannot repopulate the view'() {
    const h = harness(); await h.authenticate(PETS, '2026-11-01T00:00:04Z'); await h.select()
    const old = h.session.refresh(), index = h.requests.length - 1; await h.advance(4000); await old
    h.success(snapshot(1), 200, index); await flush()
    assert.equal(h.session.getState().signedIn, false); assert.equal(h.session.getState().snapshot, null)
    assert.equal(h.aborted(), 1)
  },
  async 'last detach cancels request and polling but retains only valid in-memory identity'() {
    const h = harness(); await h.authenticate(); await h.select()
    const p = h.session.refresh(), index = h.requests.length - 1
    h.detach(); await p
    assert.equal(h.aborted(), 1); assert.equal(h.session.getState().snapshot, null)
    assert.equal(h.session.getState().signedIn, true); assert.equal(h.session.getState().selectedPetId, 'pet_1')
    assert.equal(h.timers.size, 0)
    h.success(snapshot(1), 200, index); await flush(); assert.equal(h.session.getState().snapshot, null)
  },
  async 'switching product pages revalidates retained selection without repeating wx login'() {
    const h = harness(); await h.authenticate(); await h.select(); h.detach()
    const count = h.requests.length; h.attach()
    assert.equal(h.requests.length, count + 1); assert.match(h.requests.at(-1).url, /\/v1\/pets$/)
    assert.equal(h.session.getState().snapshot, null)
    h.success(PETS); await flush(); h.success(snapshot(77)); await flush()
    assert.equal(h.session.getState().snapshot.device.batteryPercent, 77); assert.equal(h.logins.length, 1)
  },
  async 'page switch discards a retained selection whose grant was removed while hidden'() {
    const h = harness(); await h.authenticate(); await h.select(); h.detach(); h.attach()
    const count = h.requests.length; h.success(PETS.slice(1)); await flush()
    assert.equal(h.session.getState().selectedPetId, ''); assert.equal(h.session.getState().snapshot, null)
    assert.equal(h.session.getState().signedIn, true); assert.equal(h.requests.length, count)
  },
  async 'clear in default preview leaves preview semantics unchanged'() {
    const h = harness(); h.session.clear()
    assert.equal(h.session.getState().mode, 'preview'); assert.equal(h.session.getState().message, '界面预览 · 未连接设备')
    assert.equal(h.requests.length, 0)
  },
  async 'late hidden response cannot populate a newly attached page'() {
    const h = harness(); await h.authenticate(); await h.select()
    const old = h.session.refresh(), index = h.requests.length - 1; h.detach(); await old
    h.attach(); h.success(snapshot(2), 200, index); await flush()
    assert.equal(h.session.getState().snapshot, null)
    h.success(PETS); await flush(); h.success(snapshot(78)); await flush()
    assert.equal(h.session.getState().snapshot.device.batteryPercent, 78)
  },
  async 'detach while wx login is pending prevents a late code exchange'() {
    const h = harness(); await h.ready(); const p = h.session.login(), count = h.requests.length
    h.detach(); await p; h.logins[0].success({ code: 'late-code' }); await flush()
    assert.equal(h.requests.length, count); assert.equal(h.session.getState().signedIn, false)
  },
  async 'detach while auth exchange pending prevents late identity restoration'() {
    const h = harness(); await h.ready(); const p = h.session.login()
    h.logins[0].success({ code: 'code' }); await flush(); const index = h.requests.length - 1
    h.detach(); await p
    h.success({ accessToken: TOKEN, userId: 'user_hidden', expiresAt: '2026-11-01T01:00:00Z' }, 200, index); await flush()
    assert.equal(h.session.getState().signedIn, false); assert.equal(h.session.getState().pets.length, 0)
  },
  async 'only the final subscriber stops shared polling and detach is idempotent'() {
    const h = harness(); await h.authenticate(); await h.select(); const second = h.attach()
    h.detach(); h.detach(); const count = h.requests.length; await h.advance(5000)
    assert.equal(h.requests.length, count + 1); h.success(snapshot()); await flush()
    second(); assert.equal(h.timers.size, 0); assert.equal(h.session.getState().snapshot, null)
  },
  async 'subscriptions using the same function have independent detach handles'() {
    const h = harness(); const callback = () => {}, one = h.attach(callback), two = h.attach(callback)
    const p = h.session.enable(); one(); h.success(health()); await p
    assert.equal(h.session.getState().health.wechatConfigured, true); two()
  },
  async 'hidden expired identity cannot resume or issue authenticated requests'() {
    const h = harness(); await h.authenticate(PETS, '2026-11-01T00:00:04Z'); await h.select(); h.detach()
    await h.advance(4001); const count = h.requests.length; h.attach()
    assert.equal(h.session.getState().signedIn, false); assert.match(h.requests[count].url, /\/health$/)
    h.success(health()); await flush(); assert.equal(h.session.getState().selectedPetId, '')
    assert.equal(h.logins.length, 1)
  },
  async 'application hide clear drops identity while keeping explicit live mode'() {
    const h = harness(); await h.authenticate(); await h.select(); h.session.clear()
    const state = h.session.getState()
    assert.equal(state.mode, 'live'); assert.equal(state.signedIn, false); assert.equal(state.health, null)
    assert.equal(state.pets.length, 0); assert.equal(state.selectedPetId, ''); assert.equal(state.snapshot, null)
    assert.equal(h.timers.size, 0)
  },
  async 'preview exit clears live identity cancels polling and never auto logs in on reentry'() {
    const h = harness(); await h.authenticate(); await h.select(); const p = h.session.refresh(), index = h.requests.length - 1
    h.session.preview(); await p; h.success(snapshot(), 200, index); await flush()
    assert.equal(h.session.getState().mode, 'preview'); assert.equal(h.session.getState().signedIn, false)
    assert.equal(h.session.getState().snapshot, null); assert.equal(h.timers.size, 0)
    const enable = h.session.enable(); h.success(health()); await enable; assert.equal(h.logins.length, 1)
  },
  async 'logout clears locally before its authenticated server call completes'() {
    const h = harness(); await h.authenticate(); await h.select()
    const p = h.session.logout()
    assert.equal(h.session.getState().signedIn, false); assert.equal(h.session.getState().snapshot, null)
    assert.equal(h.requests.at(-1).method, 'POST'); assert.match(h.requests.at(-1).url, /\/auth\/logout$/)
    h.success({ loggedOut: true }); await p
    assert.equal(h.session.getState().loading, false); assert.equal(h.timers.size, 0)
  },
  async 'failed logout remains signed out with a safe message'() {
    const h = harness(); await h.authenticate(); const p = h.session.logout(); h.failure(); await p
    assert.equal(h.session.getState().signedIn, false); assert.equal(h.session.getState().loading, false)
    assert.doesNotMatch(JSON.stringify(h.session.getState()), /raw-secret|synthetic-private/)
  },
  async 'only safe auth and read-only device routes can be requested'() {
    const h = harness(); await h.authenticate(); await h.select()
    const p = h.session.logout(); h.success({ loggedOut: true }); await p
    const methods = h.requests.map(request => request.method + ' ' + new URL(request.url).pathname)
    assert.deepEqual(methods, ['GET /health', 'POST /v1/auth/wechat', 'GET /v1/pets', 'GET /v1/pets/pet_1/status', 'POST /v1/auth/logout'])
  }
}

async function main() {
  let failures = 0
  for (const [name, run] of Object.entries(cases)) {
    try { await run() }
    catch (error) { failures++; console.error('FAIL ' + name + ': ' + error.stack) }
  }
  console.log(`live-device-session-smoke: ${Object.keys(cases).length - failures}/${Object.keys(cases).length} cases passed (offline injected wx; no real login, device writes or storage)`)
  if (failures) process.exitCode = 1
}
main().catch(error => { console.error(error); process.exitCode = 1 })
