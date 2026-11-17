// Actual product pages + shared controller + strict cloud client in a recursive
// TS VM. wx networking/login are synthetic only; this is NOT WeChat GUI proof.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..')
const NOW = Date.parse('2026-11-01T00:00:00Z')
const TOKEN = 'synthetic-private-integration-token-no-storage'
const PETS = [{ petId: 'pet_local', name: '小安' }]
const health = { mode: 'local-development', wechatConfigured: true, readOnly: true }
const clone = value => JSON.parse(JSON.stringify(value))
const flush = async () => { for (let i = 0; i < 40; i++) await Promise.resolve() }
const snapshot = (batteryPercent = 0) => ({ readOnly: true, sampledAt: '2026-11-01T00:00:00Z',
  health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' },
  device: { connected: true, deviceSerial: '7c2****a128', networkName: null, batteryPercent,
    batteryState: 'discharging', batteryVoltage: 4.1, chargerOnline: false, batteryPresent: true, lastSeenAt: null },
  dialog: { state: 'offline', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false }
})

function harness() {
  let now = NOW, nextTimer = 0, aborts = 0, storageWrites = 0
  const requests = [], logins = [], serviceCalls = [], writes = [], storageReads = [], logs = [], timers = new Map(), modules = new Map()
  const service = Object.fromEntries(['getBoundPet', 'getDashboard', 'getDeviceDetail', 'bindPet', 'updateDeviceStatus', 'reconnectDevice'].map(method => [method, async (...args) => {
    serviceCalls.push({ method, args }); throw Error('Mock service must stay dormant in real mode')
  }]))
  const wx = {
    login(value) { logins.push(value) },
    request(value) { requests.push(value); return { abort() { aborts++; value.fail({ errMsg: 'abort raw-secret' }) } } },
    getStorageSync(key) { storageReads.push(key); return false },
    setStorageSync() { storageWrites++; throw Error('no persistent writes') },
    setStorage() { storageWrites++; throw Error('no persistent writes') },
    showToast() {}, stopPullDownRefresh() {}, navigateTo() {}, redirectTo() {}, reLaunch() {}
  }
  class ClockDate extends Date {
    constructor(...args) { if (args.length) super(...args); else super(now) }
    static now() { return now }
  }
  const context = vm.createContext({ wx, Date: ClockDate,
    setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, { fn, at: now + delay }); return id },
    clearTimeout(id) { timers.delete(id) },
    setInterval() { throw Error('Mock polling must not run in real mode') }, clearInterval() {},
    console: Object.fromEntries(['log', 'error', 'warn', 'info', 'debug'].map(name => [name, (...args) => logs.push(args)]))
  })
  function load(relative) {
    const filename = path.resolve(root, relative)
    if (modules.has(filename)) return modules.get(filename)
    const record = { exports: {}, definition: null }; modules.set(filename, record)
    const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
    }).outputText
    const wrapper = new vm.Script('(function(require,module,exports,Page,App){' + output + '\n})', { filename }).runInContext(context)
    wrapper(id => {
      if (id.endsWith('/pet-service')) return { getPetService: () => service }
      return load(path.relative(root, path.resolve(path.dirname(filename), id + '.ts'))).exports
    }, record, record.exports, value => { record.definition = value }, value => { record.definition = value })
    return record
  }
  const h = {
    requests, logins, timers, writes, serviceCalls, storageReads, logs,
    session: () => load('services/live-device-session.ts').exports.getLiveDeviceSession(),
    view: state => load('utils/live-device-view.ts').exports.buildLiveDeviceView(state),
    app: () => load('app.ts').definition,
    aborts: () => aborts, storageWrites: () => storageWrites,
    page(name) {
      const definition = load(`pages/${name}/index.ts`).definition
      const page = { ...definition, data: clone(definition.data) }
      page.setData = patch => { Object.assign(page.data, patch); writes.push({ page: name, patch: clone(patch) }) }
      return page
    },
    success(value, statusCode = 200, index = requests.length - 1) { requests[index].success({ statusCode, data: { success: true, data: value } }) },
    async authenticatedHome() {
      const home = this.page('home'); await home.onShow()
      const enabled = home.useRealDevice(); this.success(health); await enabled
      assert.equal(logins.length, 0)
      const login = home.liveLogin(); logins.at(-1).success({ code: 'one-time-integration-code' }); await flush()
      this.success({ accessToken: TOKEN, userId: 'user_private', expiresAt: '2026-11-01T01:00:00Z' }); await flush()
      this.success(PETS); await login
      const selected = home.liveSelect({ detail: { petId: PETS[0].petId } }); this.success(snapshot()); await selected
      return home
    },
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
  async 'actual home authenticates explicitly and displays real zero battery without Mock binding'() {
    const h = harness(), home = await h.authenticatedHome()
    assert.equal(home.data.liveMode, true); assert.equal(home.data.dashboard, null)
    assert.equal(home.data.liveState.signedIn, true); assert.equal(home.data.liveOnline, true)
    const view = h.view(home.data.liveState)
    assert.equal(view.batteryLabel, '0%'); assert.equal(view.voiceLabel, '语音服务离线'); assert.equal(view.online, true)
    assert.equal(h.serviceCalls.length, 0); assert.equal(h.storageWrites(), 0); assert.equal(h.logins.length, 1)
    for (const { patch } of h.writes) assert.doesNotMatch(JSON.stringify(patch), /synthetic-private|user_private|one-time-integration|accessToken|Authorization/)
    home.onHide()
  },
  async 'actual home to connection shares identity but rechecks authorization before reading'() {
    const h = harness(), home = await h.authenticatedHome(); home.onHide()
    assert.equal(h.session().getState().snapshot, null)
    const count = h.requests.length, device = h.page('device'); await device.onShow()
    assert.equal(device.data.liveMode, true); assert.equal(device.data.liveState.signedIn, true)
    assert.equal(device.data.liveState.snapshot, null); assert.equal(device.data.liveOnline, false)
    assert.match(h.requests[count].url, /\/v1\/pets$/); assert.equal(device.data.liveState.listLoading, true)
    h.success(PETS); await flush(); assert.match(h.requests.at(-1).url, /\/pets\/pet_local\/status$/)
    h.success(snapshot(62)); await flush()
    assert.equal(device.data.liveState.snapshot.device.batteryPercent, 62); assert.equal(device.data.pet, null)
    assert.equal(h.logins.length, 1); assert.equal(h.serviceCalls.length, 0)
    for (const name of ['bindPet', 'updateStatus', 'reconnect']) await device[name]()
    assert.equal(h.serviceCalls.length, 0); device.onHide()
  },
  async 'actual app hide clears identity and reattachment cannot reuse it without explicit login'() {
    const h = harness(), home = await h.authenticatedHome(); h.app().onHide()
    assert.equal(home.data.liveState.signedIn, false); assert.equal(home.data.liveState.snapshot, null)
    assert.equal(home.data.liveOnline, false); assert.equal(h.session().getState().mode, 'live')
    home.onHide(); const device = h.page('device'); await device.onShow()
    assert.equal(device.data.liveState.signedIn, false); assert.match(h.requests.at(-1).url, /\/health$/)
    h.success(health); await flush()
    assert.equal(device.data.liveState.pets.length, 0); assert.equal(h.logins.length, 1)
    assert.equal(h.storageWrites(), 0); assert.equal(h.serviceCalls.length, 0); device.onHide()
  },
  async '401 during actual cross-page list revalidation clears current identity and stops polling'() {
    const h = harness(), home = await h.authenticatedHome(); home.onHide()
    const device = h.page('device'); await device.onShow(); h.success({}, 401); await flush()
    assert.equal(device.data.liveState.signedIn, false); assert.equal(device.data.liveState.pets.length, 0)
    assert.equal(device.data.liveState.selectedPetId, ''); assert.equal(device.data.liveState.snapshot, null)
    assert.equal(device.data.liveOnline, false); assert.equal(h.timers.size, 0)
    const count = h.requests.length; await h.advance(5000); assert.equal(h.requests.length, count)
    device.onHide()
  },
  async 'late hidden page status cannot contaminate real connection page after revalidation'() {
    const h = harness(), home = await h.authenticatedHome()
    const old = home.liveRefresh(), index = h.requests.length - 1; home.onHide(); await old
    const hiddenWrites = h.writes.filter(write => write.page === 'home').length
    const device = h.page('device'); await device.onShow()
    h.success(snapshot(1), 200, index); await flush()
    assert.equal(device.data.liveState.snapshot, null); assert.equal(device.data.liveOnline, false)
    h.success(PETS); await flush(); h.success(snapshot(73)); await flush()
    assert.equal(device.data.liveState.snapshot.device.batteryPercent, 73)
    assert.equal(h.writes.filter(write => write.page === 'home').length, hiddenWrites)
    assert.equal(h.aborts(), 2, 'the hidden status and newly added dashboard GET are both cancelled'); device.onHide()
  },
  async 'status error in actual page clears online indicator and never shows demo battery'() {
    const h = harness(), home = await h.authenticatedHome()
    const pending = home.liveRefresh(); h.success({}, 503); await pending
    assert.equal(home.data.liveState.snapshot, null); assert.equal(home.data.liveOnline, false)
    assert.equal(home.data.dashboard, null); assert.equal(h.view(home.data.liveState).batteryLabel, '未验证')
    assert.equal(h.serviceCalls.length, 0); home.onHide()
  },
  async 'actual connection logout only posts auth route and no credential reaches storage or views'() {
    const h = harness(), home = await h.authenticatedHome(); home.onHide()
    const device = h.page('device'); await device.onShow(); h.success(PETS); await flush(); h.success(snapshot(61)); await flush()
    const logout = device.liveLogout(); assert.equal(device.data.liveState.signedIn, false)
    assert.equal(device.data.liveState.snapshot, null); assert.equal(h.requests.at(-1).method, 'POST')
    assert.match(h.requests.at(-1).url, /\/v1\/auth\/logout$/); h.success({ loggedOut: true }); await logout
    assert.equal(h.storageWrites(), 0); assert.equal(h.logs.length, 0)
    assert.ok(h.storageReads.every(key => key === 'aiCatSession'))
    for (const request of h.requests) {
      const route = new URL(request.url).pathname
      assert.ok(['GET /health', 'POST /v1/auth/wechat', 'GET /v1/pets', 'GET /v1/pets/pet_local/status', 'GET /v1/pets/pet_local/dashboard', 'POST /v1/auth/logout'].includes(request.method + ' ' + route))
    }
    assert.equal(h.serviceCalls.length, 0); device.onHide()
  }
}

async function main() {
  let failures = 0
  for (const [name, run] of Object.entries(cases)) {
    try { await run() }
    catch (error) { failures++; console.error('FAIL ' + name + ': ' + error.stack) }
  }
  console.log(`live-pages-network-smoke: ${Object.keys(cases).length - failures}/${Object.keys(cases).length} cases passed (actual TS pages/controller/client; injected wx only, NOT real WeChat GUI)`)
  if (failures) process.exitCode = 1
}
main().catch(error => { console.error(error); process.exitCode = 1 })
