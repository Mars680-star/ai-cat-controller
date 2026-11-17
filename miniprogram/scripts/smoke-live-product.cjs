// Entirely offline: real TS boundary/controller + injected session, wx and clock.
// No network, real credentials, persistent session or physical action is used.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..')
const NOW = Date.parse('2026-11-01T00:00:00Z')
const UTC = '2026-11-01T00:00:00Z'
const TOKEN = 'synthetic-private-product-token-never-stored'
const plain = value => JSON.parse(JSON.stringify(value))
const flush = async () => { for (let i = 0; i < 45; i++) await Promise.resolve() }
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const problem = code => Object.assign(new Error('sensitive upstream failure'), { code })
const intimacyData = () => ({ points: 23, level: 1, levelName: '逐渐熟悉', minPoints: 20, nextPoints: 50, progressPercent: 10, address: '朋友', currentUnlocks: ['亲近称呼'], nextUnlocks: ['组合动作'] })
const historyItem = () => ({ eventType: 'daily_check_in', label: '每日见面', pointsDelta: 3, pointsAfter: 23, createdAt: UTC })
const dashboard = () => ({ interactionsEnabled: true, petName: '小安', personality: { personalityId: 'gentle_companion', name: '温柔陪伴者', description: '温暖回应', languageStyle: '简短温柔', voiceName: '温柔音色' }, intimacy: intimacyData(), unlockedActionIds: ['head_nod', 'head_shake'], recentInteractions: [historyItem()], historyScope: 'latest_30', today: { interactionCount: null, pointsGained: null }, sampledAt: UTC })
const intimacy = () => ({ interactionsEnabled: true, intimacy: intimacyData(), dailyGrowthCap: 20, dailyCheckInCompleted: false, rules: [{ eventType: 'daily_check_in', label: '每日见面', points: 3, maxPerDay: 1 }, { eventType: 'valid_dialog', label: '有效对话', points: 2, maxPerDay: 5 }], history: [historyItem()], historyScope: 'latest_30', sampledAt: UTC })
const tag = () => ({ tagId: 'little_explorer', name: '小小探索者', description: '探索新问题', earnedAt: UTC })
const growth = () => ({ enabled: true, attributes: ['curiosity', 'empathy', 'knowledge', 'energy', 'mischief', 'discipline'].map((attributeId, i) => ({ attributeId, label: '属性' + i, tendency: '普通', value: i + 0.25 })), activeTags: [], tagHistory: [], recentEvents: [], behavior: { basePersonalityId: 'gentle_companion', basePersonalityName: '温柔陪伴者', intimacyLevel: 1, directives: [], activeTagNames: [] }, sampledAt: UTC })
const receipt = (status = 'recorded', requestId = 'interaction_1', eventType = 'daily_check_in') => ({ receiptId: 'int_1', eventType, requestId, status, pointsDelta: status === 'recorded' ? 3 : null, pointsAfter: status === 'recorded' ? 26 : null, duplicate: status === 'recorded' ? false : null, celebrationScheduled: status === 'recorded' ? false : null, updatedAt: UTC })
const device = () => ({ readOnly: true, sampledAt: UTC, health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' }, device: { connected: true, deviceSerial: '7c2****a128', networkName: null, batteryPercent: 66, batteryState: 'charging', batteryVoltage: 4.1, chargerOnline: true, batteryPresent: true, lastSeenAt: null }, dialog: { state: 'offline', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false } })

function harness() {
  let now = NOW, sequence = 0, storageCalls = 0
  const timers = new Map(), modules = new Map(), requests = [], logins = [], logs = []
  class ClockDate extends Date { constructor(...args) { if (args.length) super(...args); else super(now) } static now() { return now } }
  const wx = { login(value) { logins.push(value) }, request(value) { requests.push(value); return { abort() { value.fail({ errMsg: 'aborted private-token' }) } } },
    getStorageSync() { storageCalls++; throw Error('No session storage') }, setStorageSync() { storageCalls++; throw Error('No session storage') } }
  const context = vm.createContext({ wx, Date: ClockDate,
    setTimeout(fn, delay) { const id = ++sequence; timers.set(id, { fn, at: now + Math.max(1, delay) }); return id }, clearTimeout(id) { timers.delete(id) },
    setInterval() { throw Error('Product reads must use serial polling') }, clearInterval() {}, console: Object.fromEntries(['log', 'warn', 'error', 'info', 'debug'].map(name => [name, (...args) => logs.push(args)])) })
  function load(relative, parent = root) {
    let filename = path.resolve(parent, relative)
    if (!filename.endsWith('.ts')) filename += '.ts'
    if (modules.has(filename)) return modules.get(filename).exports
    const module = { exports: {} }; modules.set(filename, module)
    const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 } }).outputText
    new vm.Script('(function(require,module,exports){' + output + '\n})', { filename }).runInContext(context)(name => load(name, path.dirname(filename)), module, module.exports)
    return module.exports
  }
  const api = load('services/live-product')
  const h = { api, wx, load, requests, logins, timers, logs,
    success(value, index = requests.length - 1, statusCode = 200) { requests[index].success({ statusCode, data: { success: true, data: value } }) },
    failure(index = requests.length - 1) { requests[index].fail({ errMsg: 'private-token AppSecret' }) }, storageCalls: () => storageCalls,
    async authenticate() {
      this.session = load('services/live-device-session').getLiveDeviceSession()
      this.anchor = this.session.attach(() => {})
      const ready = this.session.enable(); this.success({ mode: 'local-development', wechatConfigured: true, readOnly: false }); await ready
      const login = this.session.login(); logins.at(-1).success({ code: 'synthetic-code' }); await flush()
      this.success({ accessToken: TOKEN, userId: 'user_1', expiresAt: '2026-11-01T01:00:00Z' }); await flush()
      this.success([{ petId: 'pet_1', name: '小安' }, { petId: 'pet_2', name: '另一只猫' }]); await login
      const select = this.session.selectPet('pet_1'); this.success(device()); await select
    },
    controller(view = 'growth', signedIn = true) {
      this.values = []; this.calls = []; this.pendingReads = []; this.pendingWrites = []
      this.identity = { mode: signedIn ? 'live' : 'preview', signedIn, expiresAt: '2026-11-01T01:00:00Z', pets: [{ petId: 'pet_1' }, { petId: 'pet_2' }], selectedPetId: signedIn ? 'pet_1' : '', loading: false, listLoading: false }
      const listeners = new Set()
      const fake = { getState: () => plain(this.identity), attach(fn) { listeners.add(fn); fn(); return () => listeners.delete(fn) } }
      for (const name of ['readDashboard', 'readIntimacy', 'readGrowth']) fake[name] = () => { this.calls.push({ name }); const next = deferred(); this.pendingReads.push(next); return next.promise }
      fake.recordInteraction = (eventType, requestId) => { this.calls.push({ name: 'recordInteraction', eventType, requestId }); const next = deferred(); this.pendingWrites.push(next); return next.promise }
      this.setIdentity = patch => { Object.assign(this.identity, patch); for (const fn of [...listeners]) fn() }
      let ids = 0
      this.product = api.createLiveProduct(fake, view, prefix => prefix + '_' + (++ids))
      this.show = () => this.product.show(value => this.values.push(value))
      this.show(); return this.product
    },
    async advance(ms) {
      const end = now + ms
      for (let guard = 0; guard < 1000; guard++) {
        const next = [...timers.entries()].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0]
        if (!next) { now = end; await flush(); return }
        now = next[1].at; timers.delete(next[0]); next[1].fn(); await flush()
      }
      throw Error('timer loop')
    }
  }
  return h
}

const cases = {
  async 'all product boundaries return only bounded public fields and honest unavailable totals'() {
    const h = harness()
    for (const [verify, value] of [[h.api.verifyDashboard, dashboard()], [h.api.verifyIntimacy, intimacy()], [h.api.verifyGrowth, growth()], [h.api.verifyInteraction, receipt()]]) {
      const result = verify({ ...value, rawOwner: 'private-owner', secret: TOKEN })
      assert.ok(!('rawOwner' in result)); assert.ok(!('secret' in result)); assert.ok(!JSON.stringify(result).includes(TOKEN))
    }
    assert.deepEqual(plain(h.api.verifyDashboard(dashboard()).today), { interactionCount: null, pointsGained: null })
    assert.deepEqual(plain(h.api.verifyGrowth(growth()).activeTags), [])
    const emptyDescription = dashboard(); emptyDescription.personality.description = ''; emptyDescription.personality.languageStyle = ''; emptyDescription.personality.voiceName = ''
    assert.equal(h.api.verifyDashboard(emptyDescription).personality.description, '')
  },
  async 'dashboard rejects malformed points identities dates text controls and invented day totals'() {
    const h = harness(), variants = [null, [], { ...dashboard(), petName: '\u202e小安' }, { ...dashboard(), sampledAt: '2026-02-30T00:00:00Z' },
      { ...dashboard(), personality: { ...dashboard().personality, personalityId: 'unknown' } }, { ...dashboard(), today: { interactionCount: 1, pointsGained: 3 } },
      { ...dashboard(), intimacy: { ...intimacyData(), points: Infinity } }, { ...dashboard(), intimacy: { ...intimacyData(), level: 5 } },
      { ...dashboard(), intimacy: { ...intimacyData(), nextPoints: 23 } }, { ...dashboard(), unlockedActionIds: ['head_nod', 'head_nod'] },
      { ...dashboard(), recentInteractions: Array.from({ length: 31 }, historyItem) }, { ...dashboard(), recentInteractions: [{ ...historyItem(), pointsAfter: -1 }] }]
    for (const value of variants) assert.throws(() => h.api.verifyDashboard(value), /UNVERIFIED/)
  },
  async 'intimacy requires explicit gate and accepts negative rule but not raw metadata'() {
    const h = harness(), sample = intimacy()
    sample.rules.push({ eventType: 'ignored_greeting', label: '忽略问候', points: -1, maxPerDay: 2 })
    assert.equal(h.api.verifyIntimacy(sample).rules.at(-1).points, -1)
    for (const value of [{ ...sample, interactionsEnabled: undefined }, { ...sample, dailyCheckInCompleted: 0 }, { ...sample, historyScope: 'all' }, { ...sample, rules: [sample.rules[0], sample.rules[0]] }, { ...sample, dailyGrowthCap: -1 }]) assert.throws(() => h.api.verifyIntimacy(value), /UNVERIFIED/)
  },
  async 'growth accepts real decimals and empty histories while rejecting hidden internals or invalid enums'() {
    const h = harness(), sample = growth()
    sample.activeTags = [{ ...tag(), description: '' }]; sample.tagHistory = [{ ...tag(), active: false, replacedAt: UTC }]
    sample.recentEvents = [{ eventType: 'question', label: '提问', topic: '', emotion: 'neutral', changedAttributes: ['curiosity'], createdAt: UTC }]
    assert.equal(h.api.verifyGrowth(sample).attributes[0].value, 0.25)
    for (const value of [{ ...sample, attributes: sample.attributes.slice(1) }, { ...sample, attributes: [sample.attributes[0], ...sample.attributes.slice(0, 5)] },
      { ...sample, attributes: sample.attributes.map((item, i) => i ? item : { ...item, value: NaN }) }, { ...sample, attributes: sample.attributes.map((item, i) => i ? item : { ...item, tendency: '未知' }) },
      { ...sample, activeTags: [tag(), tag()] }, { ...sample, tagHistory: [{ ...tag(), active: false, replacedAt: 'not-time' }] },
      { ...sample, behavior: { ...sample.behavior, directives: ['\u0000hidden'] } }, { ...sample, recentEvents: [{ ...sample.recentEvents[0], changedAttributes: ['private_revision'] }] }]) assert.throws(() => h.api.verifyGrowth(value), /UNVERIFIED/)
  },
  async 'receipt never promotes unknown or sending to recorded points and rejects invalid request identities'() {
    const h = harness()
    for (const status of ['recorded', 'sending', 'unknown', 'failed']) assert.equal(h.api.verifyInteraction(receipt(status)).status, status)
    for (const value of [{ ...receipt('unknown'), pointsDelta: 3 }, { ...receipt('sending'), duplicate: false }, { ...receipt(), status: 'completed' }, { ...receipt(), eventType: 'valid_dialog' }, { ...receipt(), requestId: '../bad' }, { ...receipt(), duplicate: null }, { ...receipt(), updatedAt: '2026-04-31T00:00:00Z' }]) assert.throws(() => h.api.verifyInteraction(value), /UNVERIFIED/)
  },
  async 'client rejects malformed identifiers and events before any network or login'() {
    const h = harness(), client = h.load('services/cloud-auth-client').createCloudAuthClient(h.wx)
    for (const method of ['readDashboard', 'readIntimacy', 'readGrowth']) await assert.rejects(client[method]('../bad'), error => error.code === 'INVALID_ID')
    for (const args of [['pet_1', 'valid_dialog', 'req_1'], ['pet_1', 'touch', '../bad'], ['../pet', 'touch', 'req_1']]) await assert.rejects(client.recordInteraction(...args), error => error.code === 'INVALID_ID')
    assert.equal(h.requests.length, 0); assert.equal(h.logins.length, 0)
  },
  async 'client uses only current bearer fixed GETs and strips raw upstream data'() {
    const h = harness(); await h.authenticate()
    for (const [method, route, sample] of [['readDashboard', 'dashboard', dashboard()], ['readIntimacy', 'intimacy', intimacy()], ['readGrowth', 'growth', growth()]]) {
      const read = h.session[method](), req = h.requests.at(-1)
      assert.equal(req.url, 'http://127.0.0.1:8787/v1/pets/pet_1/' + route); assert.equal(req.method, 'GET'); assert.equal(req.timeout, 5000)
      assert.equal(req.header.Authorization, 'Bearer ' + TOKEN); assert.ok(!('data' in req))
      h.success({ ...sample, privateSecret: TOKEN }); assert.ok(!JSON.stringify(await read).includes(TOKEN))
    }
    assert.equal(h.storageCalls(), 0); assert.deepEqual(h.logs, [])
  },
  async 'write is explicit bounded ten second event and request id only with no automatic retry'() {
    const h = harness(); await h.authenticate(); const write = h.session.recordInteraction('touch', 'manual_1'), req = h.requests.at(-1)
    const rejected = assert.rejects(write, error => error.code === 'TIMEOUT')
    assert.equal(req.url, 'http://127.0.0.1:8787/v1/pets/pet_1/interactions'); assert.equal(req.method, 'POST'); assert.equal(req.timeout, 10000)
    assert.deepEqual(plain(req.data), { eventType: 'touch', requestId: 'manual_1' })
    await h.advance(9999); assert.equal(h.requests.filter(item => item.url.endsWith('/interactions')).length, 1)
    await h.advance(1); await rejected; req.success({ statusCode: 200, data: { success: true, data: receipt('recorded', 'manual_1', 'touch') } }); await flush()
    assert.equal(h.requests.filter(item => item.url.endsWith('/interactions')).length, 1)
  },
  async 'selected pet changes cancel old reads without mutating new identity'() {
    const h = harness(); await h.authenticate(); const read = h.session.readDashboard(); const rejected = assert.rejects(read, error => error.code === 'CANCELLED'), old = h.requests.at(-1)
    const select = h.session.selectPet('pet_2'); h.success(device()); await select
    old.success({ statusCode: 401 }); await rejected
    assert.equal(h.session.getState().signedIn, true); assert.equal(h.session.getState().selectedPetId, 'pet_2')
  },
  async 'authorization checks block all product calls until list finishes'() {
    const h = harness(); await h.authenticate(); const refresh = h.session.refreshPets()
    for (const method of ['readDashboard', 'readIntimacy', 'readGrowth']) await assert.rejects(h.session[method](), error => error.code === 'AUTH_REQUIRED')
    await assert.rejects(h.session.recordInteraction('touch', 'req_1'), error => error.code === 'AUTH_REQUIRED')
    h.success([{ petId: 'pet_1', name: '小安' }]); await flush(); h.success(device()); await refresh
  },
  async '401 on product response clears shared identity and no secret appears in errors'() {
    const h = harness(); await h.authenticate(); const read = h.session.readGrowth(), rejected = assert.rejects(read, error => error.code === 'UNAUTHORIZED' && !error.message.includes(TOKEN))
    h.success({}, undefined, 401); await rejected; assert.equal(h.session.getState().signedIn, false)
  },
  async 'client refuses unrelated receipt identity without accepting reflected upstream success'() {
    for (const reflected of [receipt('recorded', 'another_id', 'touch'), receipt('recorded', 'manual_1', 'daily_check_in')]) {
      const h = harness(); await h.authenticate(); const write = h.session.recordInteraction('touch', 'manual_1'), rejected = assert.rejects(write, error => error.code === 'UNVERIFIED')
      h.success(reflected); await rejected; assert.equal(h.session.getState().signedIn, true)
    }
  },
  async 'preview and unsigned views never login write or read product endpoints'() {
    const h = harness(), product = h.controller('home', false)
    await product.read(); await product.interact('daily_check_in'); await product.retryInteraction(); await h.advance(60000)
    assert.equal(h.calls.length, 0); assert.equal(h.requests.length, 0); assert.equal(h.logins.length, 0)
  },
  async 'each view reads one correct lane and manual refresh shares a single flight'() {
    for (const [view, method, sample] of [['home', 'readDashboard', dashboard()], ['growth', 'readIntimacy', intimacy()], ['personality', 'readGrowth', growth()]]) {
      const h = harness(), product = h.controller(view)
      const first = product.read(), second = product.read()
      assert.equal(first, second); assert.deepEqual(h.calls, [{ name: method }]); h.pendingReads[0].resolve(sample); await first
      assert.deepEqual(plain(product.getState().snapshot), sample)
    }
  },
  async 'serial polling starts ten seconds after settling not while a read is in flight'() {
    const h = harness(), product = h.controller()
    await h.advance(30000); assert.equal(h.calls.length, 1)
    h.pendingReads[0].resolve(intimacy()); await flush(); await h.advance(9999); assert.equal(h.calls.length, 1)
    await h.advance(1); assert.equal(h.calls.length, 2); assert.equal(h.calls[1].name, 'readIntimacy')
    assert.equal(product.getState().loading, true)
  },
  async 'failed GET clears old real readings with actionable error never demo fallback'() {
    const h = harness(), product = h.controller()
    h.pendingReads[0].resolve(intimacy()); await flush(); const read = product.read(); h.pendingReads[1].reject(problem('UNAVAILABLE')); await read
    assert.equal(product.getState().snapshot, null); assert.match(product.getState().error, /真实数据.*刷新.*网页授权/)
  },
  async 'hidden page discards pending GETs and timers without emitting late UI'() {
    const h = harness(), product = h.controller(); const count = h.values.length
    product.hide(); h.pendingReads[0].resolve(intimacy()); await flush(); await h.advance(60000)
    assert.equal(h.values.length, count); assert.equal(h.calls.length, 1); assert.equal(product.getState().snapshot, null)
  },
  async 'selection or identity replacement discards old responses and reads newly selected pet'() {
    const h = harness(), product = h.controller()
    h.setIdentity({ selectedPetId: 'pet_2' }); assert.equal(h.calls.length, 2)
    h.pendingReads[0].resolve(intimacy()); await flush(); assert.equal(product.getState().snapshot, null)
    const sample = { ...intimacy(), dailyGrowthCap: 17 }; h.pendingReads[1].resolve(sample); await flush(); assert.equal(product.getState().snapshot.dailyGrowthCap, 17)
  },
  async 'rechecking authorization clears view and pauses all product use until settled'() {
    const h = harness(), product = h.controller(); h.pendingReads[0].resolve(intimacy()); await flush()
    h.setIdentity({ listLoading: true }); assert.equal(product.getState().snapshot, null); await product.interact('touch'); await h.advance(30000)
    assert.equal(h.calls.length, 1); assert.match(product.getState().interactionError, /互动未发送.*授权/)
    h.setIdentity({ listLoading: false }); assert.equal(h.calls.length, 2)
  },
  async 'no live data or disabled writes visibly rejects without creating id or mutation'() {
    const h = harness(), product = h.controller(); await product.interact('touch'); assert.match(product.getState().interactionError, /尚未验证/)
    h.pendingReads[0].resolve({ ...intimacy(), interactionsEnabled: false }); await flush(); await product.interact('touch')
    assert.match(product.getState().interactionError, /暂未开放/); assert.equal(h.pendingWrites.length, 0)
  },
  async 'unknown event type never creates a write or id even with a verified snapshot'() {
    const h = harness(), product = h.controller(); h.pendingReads[0].resolve(intimacy()); await flush()
    for (const value of ['valid_dialog', 'system', 'touch\n', '../touch', false, null]) await product.interact(value)
    assert.equal(h.pendingWrites.length, 0); assert.match(product.getState().interactionError, /不支持/)
  },
  async 'one explicitly confirmed interaction only pending taps share request and recorded follows GET'() {
    const h = harness(), product = h.controller(); h.pendingReads[0].resolve(intimacy()); await flush()
    const first = product.interact('daily_check_in'), duplicate = product.interact('daily_check_in')
    assert.equal(first, duplicate); assert.deepEqual(h.calls.at(-1), { name: 'recordInteraction', eventType: 'daily_check_in', requestId: 'interaction_1' })
    h.pendingWrites[0].resolve(receipt()); await first
    assert.equal(product.getState().result.status, 'recorded'); assert.equal(product.getState().snapshot, null); assert.equal(h.calls.at(-1).name, 'readIntimacy')
    assert.equal(h.pendingWrites.length, 1)
  },
  async 'unknown receipt preserves exact retry id and no point success is invented'() {
    const h = harness(), product = h.controller(); h.pendingReads[0].resolve(intimacy()); await flush()
    const write = product.interact('touch'); h.pendingWrites[0].resolve(receipt('unknown', 'interaction_1', 'touch')); await write
    assert.equal(product.getState().uncertain, true); assert.equal(product.getState().result.pointsAfter, null); assert.equal(product.getState().retryAvailable, true)
    await product.interact('completed_task'); assert.equal(h.pendingWrites.length, 1)
    const retry = product.retryInteraction(); assert.deepEqual(h.calls.at(-1), { name: 'recordInteraction', eventType: 'touch', requestId: 'interaction_1' })
    h.pendingWrites[1].resolve(receipt('unknown', 'interaction_1', 'touch')); await retry
    assert.equal(product.getState().uncertain, true); assert.match(product.getState().interactionError, /结果不确定/)
  },
  async 'network loss retains retry context across hide and same identity re-entry'() {
    const h = harness(), product = h.controller(); h.pendingReads[0].resolve(intimacy()); await flush()
    const write = product.interact('completed_task'); h.pendingWrites[0].reject(problem('TIMEOUT')); await write
    product.hide(); h.show(); h.pendingReads.at(-1).resolve(intimacy()); await flush()
    assert.equal(product.getState().retryAvailable, true)
    const retry = product.retryInteraction(); assert.deepEqual(h.calls.at(-1), { name: 'recordInteraction', eventType: 'completed_task', requestId: 'interaction_1' })
    h.pendingWrites[1].resolve(receipt('recorded', 'interaction_1', 'completed_task')); await retry
  },
  async 'same id retry requires a currently verified snapshot and server write gate'() {
    const h = harness(), product = h.controller(); h.pendingReads[0].resolve(intimacy()); await flush()
    const write = product.interact('touch'); h.pendingWrites[0].reject(problem('TIMEOUT')); await write
    const read = product.read(); h.pendingReads[1].reject(problem('UNAVAILABLE')); await read; await product.retryInteraction()
    assert.equal(h.pendingWrites.length, 1); assert.match(product.getState().interactionError, /真实数据或培养权限尚未验证/)
    const disabledRead = product.read(); h.pendingReads[2].resolve({ ...intimacy(), interactionsEnabled: false }); await disabledRead; await product.retryInteraction()
    assert.equal(h.pendingWrites.length, 1); assert.equal(product.getState().retryAvailable, true)
  },
  async 'authorization refresh and hide while submitting preserve id but never auto retry'() {
    const h = harness(), product = h.controller(); h.pendingReads[0].resolve(intimacy()); await flush()
    const write = product.interact('touch'); h.setIdentity({ listLoading: true }); h.pendingWrites[0].resolve(receipt('recorded', 'interaction_1', 'touch')); await write
    h.setIdentity({ listLoading: false }); h.pendingReads.at(-1).resolve(intimacy()); await flush()
    assert.equal(product.getState().result, null); assert.equal(product.getState().retryAvailable, true); assert.equal(h.pendingWrites.length, 1)
    const retry = product.retryInteraction(); assert.deepEqual(h.calls.at(-1), { name: 'recordInteraction', eventType: 'touch', requestId: 'interaction_1' })
    product.hide(); h.pendingWrites[1].resolve(receipt('recorded', 'interaction_1', 'touch')); await retry
    h.show(); h.pendingReads.at(-1).resolve(intimacy()); await flush(); assert.equal(h.pendingWrites.length, 2); assert.equal(product.getState().retryAvailable, true)
  },
  async 'hidden or changed device old confirmation never submits; old write never refreshes new device'() {
    const h = harness(), product = h.controller(); h.pendingReads[0].resolve(intimacy()); await flush()
    const write = product.interact('touch'); h.setIdentity({ selectedPetId: 'pet_2' }); h.pendingWrites[0].resolve(receipt('recorded', 'interaction_1', 'touch')); await write
    assert.equal(product.getState().result, null); assert.equal(product.getState().retryAvailable, false); assert.equal(h.calls.length, 3)
    product.hide(); await product.interact('touch'); await product.retryInteraction(); assert.equal(h.calls.length, 3)
  },
  async 'mismatched receipt remains uncertain and same request retriable instead of accepting false success'() {
    const h = harness(), product = h.controller(); h.pendingReads[0].resolve(intimacy()); await flush()
    const write = product.interact('touch'); h.pendingWrites[0].resolve(receipt('recorded', 'wrong_id', 'daily_check_in')); await write
    assert.equal(product.getState().result, null); assert.equal(product.getState().uncertain, true); assert.equal(product.getState().retryAvailable, true)
  },
  async 'GET initiated before interaction cannot overwrite recorded newer data or fetch twice'() {
    const h = harness(), product = h.controller(); h.pendingReads[0].resolve(intimacy()); await flush()
    const oldRead = product.read(), write = product.interact('touch')
    h.pendingWrites[0].resolve(receipt('recorded', 'interaction_1', 'touch')); await write
    assert.equal(h.pendingReads.length, 2); assert.equal(product.getState().snapshot, null)
    h.pendingReads[1].resolve(intimacy()); await oldRead; assert.equal(product.getState().snapshot, null)
    assert.equal(h.pendingReads.length, 3); const latest = { ...intimacy(), intimacy: { ...intimacyData(), points: 26, progressPercent: 20 } }
    h.pendingReads[2].resolve(latest); await flush()
    assert.equal(product.getState().snapshot.intimacy.points, 26)
  }
}

;(async () => {
  for (const [name, run] of Object.entries(cases)) { await run(); console.log('PASS ' + name) }
  console.log('Live product smoke passed: ' + Object.keys(cases).length)
})().catch(error => { console.error(error); process.exitCode = 1 })
