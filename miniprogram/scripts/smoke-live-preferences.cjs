// Actual TS/client/shared-session execution with synthetic wx/HTTP/clock/storage.
// No network, device, credentials or on-disk runtime databases are accessed.
const assert = require('node:assert/strict')
const fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), ts = require('typescript')
const root = path.resolve(__dirname, '..'), UTC = '2026-11-01T00:00:00Z', NOW = Date.parse(UTC)
const TOKEN = 'synthetic-private-preferences-token', STORAGE = 'aiCatPreferencesPendingV1'
const plain = value => JSON.parse(JSON.stringify(value))
const flush = async () => { for (let index = 0; index < 45; index++) await Promise.resolve() }
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const problem = code => Object.assign(new Error('private upstream details'), { code })
const snapshot = (patch = {}) => ({ verified: true, settingsEnabled: true, feedbackEnabled: true, canChangeName: true, canChangeVolume: true, canChangeFollowUp: true, canSubmitFeedback: true,
  name: '小安', storedVolume: 37, outputVolume: 37, outputMuted: false, outputVolumeAvailable: true, followUpSeconds: 25, followUpSource: 'persisted', minimumSeconds: 5, maximumSeconds: 120,
  settingsReviewRequired: false, feedbackReviewRequired: false, manualReviewRequired: false, reason: '', sampledAt: UTC, ...patch })
const unavailable = () => snapshot({ verified: false, canChangeName: false, canChangeVolume: false, canChangeFollowUp: false, canSubmitFeedback: false, name: null, storedVolume: null, outputVolume: null, outputMuted: null, outputVolumeAvailable: null, followUpSeconds: null, followUpSource: null, minimumSeconds: null, maximumSeconds: null, reason: '未验证设备' })
const stepNames = command => command.kind === 'feedback' ? ['feedback'] : [...('name' in command.changes || 'volume' in command.changes ? ['pet_settings'] : []), ...('followUpSeconds' in command.changes ? ['follow_up'] : [])]
const receipt = (requestId = 'preferences_1', kind = 'settings', status = 'recorded', steps = [{ step: kind === 'feedback' ? 'feedback' : 'pet_settings', status: status === 'recorded' ? 'applied' : status === 'not_sent' ? 'not_sent' : status === 'sending' ? 'sending' : 'unknown' }]) => ({ requestId, kind, status, steps, updatedAt: UTC })
const reply = (command, status = 'recorded') => receipt(command.requestId, command.kind, status, stepNames(command).map(step => ({ step, status: status === 'recorded' ? 'applied' : status === 'not_sent' ? 'not_sent' : status === 'sending' ? 'sending' : 'unknown' })))
const pending = (kind = 'settings', requestId = 'old_1', userId = 'user_1', petId = 'pet_1') => ({ userId, petId, requestId, kind })
const stored = (settings = null, feedback = null) => ({ version: 1, settings, feedback })
const device = () => ({ readOnly: true, sampledAt: UTC, health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' }, device: { connected: true, deviceSerial: '7c2****a128', networkName: null, batteryPercent: 66, batteryState: 'charging', batteryVoltage: 4.1, chargerOnline: true, batteryPresent: true, lastSeenAt: null }, dialog: { state: 'offline', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false } })

function harness(existingStorage) {
  let now = NOW, timerId = 0, readFails = false, writeFails = false, storageWrites = 0
  const timers = new Map(), modules = new Map(), requests = [], logins = [], logs = [], storage = existingStorage || new Map(), trace = []
  class ClockDate extends Date { constructor(...args) { if (args.length) super(...args); else super(now) } static now() { return now } }
  const wx = { login(value) { logins.push(value) }, request(value) { requests.push(value); return { abort() { value.fail({ errMsg: 'private-token aborted' }) } } },
    getStorageSync(key) { if (readFails) throw Error('private storage error'); return storage.has(key) ? plain(storage.get(key)) : '' },
    setStorageSync(key, value) { storageWrites++; if (writeFails) throw Error('private storage error'); storage.set(key, plain(value)); trace.push({ type: 'storage', value: plain(value) }) } }
  const context = vm.createContext({ wx, Date: ClockDate, setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, at: now + Math.max(1, delay) }); return id }, clearTimeout(id) { timers.delete(id) },
    setInterval() { throw Error('No interval permitted') }, clearInterval() {}, console: Object.fromEntries(['log', 'warn', 'error', 'info', 'debug'].map(name => [name, (...args) => logs.push(args)])) })
  function load(relative, parent = root) {
    let filename = path.resolve(parent, relative); if (!filename.endsWith('.ts')) filename += '.ts'
    if (modules.has(filename)) return modules.get(filename).exports
    const module = { exports: {} }; modules.set(filename, module)
    const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 } }).outputText
    new vm.Script('(function(require,module,exports){' + output + '\n})', { filename }).runInContext(context)(name => load(name, path.dirname(filename)), module, module.exports)
    return module.exports
  }
  const api = load('services/live-preferences'), h = { api, wx, load, requests, logins, logs, storage, trace, timers,
    storageWrites: () => storageWrites, failStorageRead(value = true) { readFails = value }, failStorageWrite(value = true) { writeFails = value },
    success(value, index = requests.length - 1, statusCode = 200) { requests[index].success({ statusCode, data: { success: true, data: value } }) },
    async authenticate() {
      this.session = load('services/live-device-session').getLiveDeviceSession(); this.anchor = this.session.attach(() => {})
      const enable = this.session.enable(); this.success({ mode: 'local-development', wechatConfigured: true, readOnly: false }); await enable
      const login = this.session.login(); logins.at(-1).success({ code: 'synthetic-code' }); await flush()
      this.success({ accessToken: TOKEN, userId: 'user_1', expiresAt: '2026-11-01T01:00:00Z' }); await flush()
      this.success([{ petId: 'pet_1', name: '小安' }, { petId: 'pet_2', name: '另一只猫' }]); await login
      const select = this.session.selectPet('pet_1'); this.success(device()); await select
    },
    factory(signedIn = true) {
      this.values = []; this.calls = []; this.reads = []; this.posts = []; this.receipts = []
      this.identity = { mode: signedIn ? 'live' : 'preview', signedIn, expiresAt: '2026-11-01T01:00:00Z', pets: [{ petId: 'pet_1' }, { petId: 'pet_2' }], selectedPetId: signedIn ? 'pet_1' : '', loading: false, listLoading: false }; this.userId = 'user_1'
      const listeners = new Set(), fake = { getState: () => plain(this.identity), getIdentity: () => this.identity.signedIn ? { userId: this.userId, expiresAt: this.identity.expiresAt } : null,
        attach(fn) { listeners.add(fn); fn(); return () => listeners.delete(fn) } }
      fake.readPreferences = () => { this.calls.push({ name: 'read' }); const value = deferred(); this.reads.push(value); return value.promise }
      fake.sendPreferencesCommand = command => { this.calls.push({ name: 'post', command: plain(command) }); trace.push({ type: 'command', command: plain(command), marker: plain(storage.get(STORAGE)) }); const value = deferred(); this.posts.push({ ...value, command: plain(command) }); return value.promise }
      fake.readPreferencesReceipt = requestId => { this.calls.push({ name: 'receipt', requestId }); const value = deferred(); this.receipts.push({ ...value, requestId }); return value.promise }
      this.change = (patch, userId) => { Object.assign(this.identity, patch); if (userId !== undefined) this.userId = userId; for (const fn of [...listeners]) fn() }
      let ids = 0
      this.makeController = () => api.createLivePreferences(fake, prefix => prefix + '_' + (++ids), wx)
      this.controller = this.makeController(); this.show = controller => (controller || this.controller).show(value => this.values.push(value)); this.show(); return this.controller
    },
    async ready(value = snapshot()) { const controller = this.factory(); this.reads[0].resolve(value); await flush(); return controller },
    settings(patch = {}) { this.controller.setDraft('settings', patch) },
    feedback(content = '设备有些异常\n请帮忙查看', category = 'device') { this.controller.setDraft('feedback', { category, content }) },
    async submit(kind = 'settings', payload, fresh = snapshot()) {
      const c = this.controller
      if (kind === 'settings') this.settings(payload || { name: '新名字' }); else this.feedback(payload === undefined ? '反馈原文\n请检查' : payload)
      const token = c.prepare(kind); assert.equal(typeof token, 'string'); const result = c.confirm(token)
      this.reads.at(-1).resolve(fresh); await flush(); assert.ok(this.posts.length); return { result, post: this.posts.at(-1), token }
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
  }; return h
}

const cases = {
  async 'strict snapshot separates saved preference actual amplification and nullable unavailable values'() {
    const { api } = harness(), v = api.verifyPreferences
    const value = v(snapshot({ storedVolume: 37, outputVolume: 135 })); assert.equal(value.outputVolume, 135); assert.equal(value.storedVolume, 37)
    assert.equal(v(snapshot({ outputVolume: null, outputMuted: null })).outputVolume, null)
    assert.equal(v(snapshot({ canChangeVolume: false, outputVolumeAvailable: null, outputVolume: null, outputMuted: null })).outputVolumeAvailable, null)
    assert.equal(v(unavailable()).name, null)
  },
  async 'snapshot cross fields reject invented readings booleans bypasses aliases review and metadata'() {
    const v = harness().api.verifyPreferences
    assert.equal(v(snapshot({ settingsReviewRequired: true, manualReviewRequired: true, canChangeName: false, canChangeVolume: false, canChangeFollowUp: false })).canSubmitFeedback, true)
    assert.equal(v(snapshot({ feedbackReviewRequired: true, manualReviewRequired: true, canSubmitFeedback: false })).canChangeName, true)
    for (const value of [{ ...snapshot(), credentials: TOKEN }, snapshot({ storedVolume: true }), snapshot({ outputVolume: 151 }), snapshot({ outputVolumeAvailable: false }), snapshot({ outputVolumeAvailable: null, outputVolume: null, outputMuted: null }), snapshot({ settingsEnabled: false }), snapshot({ settingsReviewRequired: true, manualReviewRequired: true }), snapshot({ feedbackReviewRequired: true }), snapshot({ minimumSeconds: 0 }), snapshot({ followUpSeconds: '25' }), snapshot({ followUpSource: 'guessed' }), snapshot({ name: '\u202e小安' }), snapshot({ sampledAt: '2026-02-30T00:00:00Z' }), { ...unavailable(), name: '演示名字' }, snapshot({ canChangeName: false, canChangeVolume: false, canChangeFollowUp: false, canSubmitFeedback: false })]) assert.throws(() => v(value), /UNVERIFIED/)
  },
  async 'command accepts exact Unicode names and multiline feedback but no extras unsafe text or coercion'() {
    const v = harness().api.verifyPreferencesCommand
    assert.equal(v({ requestId: 'r1', kind: 'settings', changes: { name: '😀'.repeat(20), volume: 0, followUpSeconds: 120 } }).changes.name.length, 40)
    assert.equal(v({ requestId: 'r1', kind: 'feedback', category: 'other', content: ' 原文\n\t保留 ' }).content, ' 原文\n\t保留 ')
    const invalid = [null, { requestId: '../bad', kind: 'settings', changes: { volume: 1 } }, { requestId: 'r1\n', kind: 'settings', changes: { volume: 1 } }, { requestId: 'r1', kind: 'settings', changes: {} }, ...[{ volume: '30' }, { volume: true }, { volume: 101 }, { followUpSeconds: 4 }, { followUpSeconds: 20.5 }, { name: null }, { name: '\n名字' }, { name: '😀'.repeat(21) }, { name: '\ud800' }, { name: '  ' }, { url: 'http://private/' }].map(changes => ({ requestId: 'r1', kind: 'settings', changes })), ...['一', 'x'.repeat(501), '\ud800', 'x\ry', 'x\u0000y', 'x\u0085y', 'x\u202ey'].map(content => ({ requestId: 'r1', kind: 'feedback', category: 'device', content })), { requestId: 'r1', kind: 'feedback', category: 'private', content: '反馈' }, { requestId: 'r1', kind: 'feedback', category: 'device', content: '反馈', attachments: [] }, { requestId: 'r1', kind: 'settings', changes: { name: '名字' }, userId: 'u' }]
    for (const value of invalid) assert.throws(() => v(value), /UNVERIFIED/)
  },
  async 'receipts enforce selected fixed steps and cross field honest partial unknown and no op states'() {
    const v = harness().api.verifyPreferencesReceipt
    for (const status of ['recorded', 'sending', 'unknown', 'not_sent']) assert.equal(v(receipt('r', 'feedback', status)).status, status)
    const partial = receipt('r', 'settings', 'partially_applied', [{ step: 'pet_settings', status: 'applied' }, { step: 'follow_up', status: 'not_sent' }]); assert.equal(v(partial).status, 'partially_applied')
    for (const value of [{ ...receipt(), content: '私有' }, receipt('r', 'settings', 'recorded', [{ step: 'pet_settings', status: 'unknown' }]), receipt('r', 'settings', 'unknown', [{ step: 'pet_settings', status: 'pending' }]), receipt('r', 'settings', 'unknown', [{ step: 'pet_settings', status: 'unknown' }, { step: 'follow_up', status: 'sending' }]), receipt('r', 'settings', 'not_sent', [{ step: 'pet_settings', status: 'applied' }]), receipt('r', 'settings', 'sending', [{ step: 'pet_settings', status: 'applied' }]), receipt('r', 'settings', 'partially_applied'), receipt('r', 'feedback', 'partially_applied'), receipt('r', 'settings', 'recorded', [{ step: 'follow_up', status: 'applied' }, { step: 'pet_settings', status: 'applied' }]), receipt('r', 'settings', 'recorded', [{ step: 'pet_settings', status: 'applied' }, { step: 'pet_settings', status: 'applied' }]), receipt('r', 'feedback', 'recorded', [{ step: 'pet_settings', status: 'applied' }])]) assert.throws(() => v(value), /UNVERIFIED/)
  },
  async 'invalid paths arbitrary params credentials and attachments are rejected without login or HTTP'() {
    const h = harness(), c = h.load('services/cloud-auth-client').createCloudAuthClient(h.wx)
    for (const p of [c.readPreferences('../bad'), c.readPreferencesReceipt('pet_1', '../bad'), c.sendPreferencesCommand('pet_1', { requestId: 'r', kind: 'settings', changes: { apiKey: TOKEN } }), c.sendPreferencesCommand('pet_1', { requestId: 'r', kind: 'feedback', category: 'device', content: '问题', attachments: [] })]) await assert.rejects(p, error => error.code === 'INVALID_ID')
    assert.equal(h.requests.length, 0); assert.equal(h.logins.length, 0)
  },
  async 'real client and shared wrappers use fixed authorized routes with five second GET and ten second POST'() {
    const h = harness(); await h.authenticate(); const read = h.session.readPreferences(), get = h.requests.at(-1)
    assert.equal(get.url, 'http://127.0.0.1:8787/v1/pets/pet_1/preferences'); assert.equal(get.method, 'GET'); assert.equal(get.timeout, 5000); assert.ok(!('data' in get)); h.success(snapshot()); await read
    const command = { requestId: 'r1', kind: 'feedback', category: 'device', content: '问题\n原文' }, write = h.session.sendPreferencesCommand(command), post = h.requests.at(-1)
    assert.equal(post.url, 'http://127.0.0.1:8787/v1/pets/pet_1/preferences/commands'); assert.equal(post.method, 'POST'); assert.equal(post.timeout, 10000); assert.deepEqual(plain(post.data), command); assert.ok(!post.url.includes(command.content))
    h.success(reply(command)); await write
    const query = h.session.readPreferencesReceipt('r1'), lookup = h.requests.at(-1); assert.equal(lookup.url, 'http://127.0.0.1:8787/v1/pets/pet_1/preferences/requests/r1'); assert.equal(lookup.method, 'GET'); assert.equal(lookup.header.Authorization, 'Bearer ' + TOKEN)
    h.success(reply(command)); await query; assert.equal(h.storageWrites(), 0); assert.equal(h.logs.length, 0)
  },
  async 'client rejects unrelated request kind and step responses rather than trusting reflected success'() {
    const h = harness(); await h.authenticate()
    for (const response of [receipt('wrong'), receipt('r1', 'feedback'), receipt('r1', 'settings', 'recorded', [{ step: 'follow_up', status: 'applied' }]), { ...receipt('r1'), token: TOKEN }]) {
      const send = h.session.sendPreferencesCommand({ requestId: 'r1', kind: 'settings', changes: { name: '名字' } }), rejected = assert.rejects(send, error => error.code === 'UNVERIFIED'); h.success(response); await rejected
    }
    const read = h.session.readPreferencesReceipt('r1'), rejected = assert.rejects(read, error => error.code === 'UNVERIFIED'); h.success(receipt('wrong')); await rejected
  },
  async 'ten second client timeout has no automatic replay and ignores late acknowledgement'() {
    const h = harness(); await h.authenticate(); const command = { requestId: 'r1', kind: 'settings', changes: { volume: 25 } }, send = h.session.sendPreferencesCommand(command), post = h.requests.at(-1), rejected = assert.rejects(send, error => error.code === 'TIMEOUT')
    await h.advance(10000); await rejected; post.success({ statusCode: 200, data: { success: true, data: reply(command) } }); await flush()
    assert.equal(h.requests.filter(request => request.url.endsWith('/preferences/commands')).length, 1)
  },
  async 'shared auth rechecks block writes and 401 clears only the current identity'() {
    const h = harness(); await h.authenticate(); const read = h.session.readPreferences(), rejected = assert.rejects(read, error => error.code === 'UNAUTHORIZED'); h.success({}, h.requests.length - 1, 401); await rejected
    assert.equal(h.session.getState().signedIn, false); assert.ok(!JSON.stringify(h.session.getState()).includes(TOKEN))
    const blocked = h.session.sendPreferencesCommand({ requestId: 'r', kind: 'settings', changes: { name: '名字' } }); await assert.rejects(blocked, error => error.code === 'AUTH_REQUIRED')
  },
  async 'preview opening reading changing a slider and canceled confirmation never login or submit'() {
    const h = harness(), c = h.factory(false); c.setDraft('feedback', { category: 'device', content: '反馈' }); await c.refresh(); await c.checkReceipt('feedback'); await c.confirm('unknown'); await h.advance(60000)
    assert.equal(h.calls.length, 0); assert.equal(h.logins.length, 0); assert.equal(h.requests.length, 0)
    const real = harness(), live = await real.ready(); real.settings({ volume: 20 }); real.settings({ volume: 35 }); real.settings({ volume: 40 }); const token = live.prepare('settings'); live.cancel(token); await live.confirm(token)
    assert.equal(real.posts.length, 0); assert.equal(real.storageWrites(), 0); assert.equal(real.reads.length, 1)
  },
  async 'initial real forms use saved values and never clamp or fabricate unavailable actual output'() {
    const h = harness(), c = await h.ready(snapshot({ outputVolume: 135 })); assert.equal(c.getState().settingsDraft.volume, 37); assert.equal(c.getState().snapshot.outputVolume, 135); assert.equal(c.getState().settingsDirty, false)
    const missing = harness(), blank = await missing.ready(unavailable()); assert.deepEqual(plain(blank.getState().settingsDraft), { name: null, volume: null, followUpSeconds: null }); assert.equal(blank.prepare('settings'), null)
  },
  async 'identical settings and feedback input events preserve their original explicit confirmation'() {
    for (const kind of ['settings', 'feedback']) {
      const h = harness(), c = await h.ready()
      if (kind === 'settings') h.settings({ name: '待保存名称' }); else h.feedback('待保存反馈')
      const version = c.getState()[kind + 'Version'], token = c.prepare(kind)
      if (kind === 'settings') h.settings({ name: '待保存名称' }); else h.feedback('待保存反馈')
      assert.equal(c.getState()[kind + 'Version'], version); assert.equal(c.getState().confirmPending, true)
      const send = c.confirm(token); assert.equal(h.posts.length, 0)
      h.reads.at(-1).resolve(snapshot()); await flush(); assert.equal(h.posts.length, 1)
      h.posts[0].resolve(reply(h.posts[0].command)); await send
    }
  },
  async 'prepared native confirmations pause optional polls and still require a fresh permission GET'() {
    for (const kind of ['settings', 'feedback']) {
      const h = harness(), c = await h.ready()
      if (kind === 'settings') h.settings({ name: '只保存名字' }); else h.feedback('只保存反馈')
      const token = c.prepare(kind); await h.advance(40000)
      assert.equal(h.reads.length, 1); assert.equal(c.getState().confirmPending, true); assert.equal(h.posts.length, 0)
      const send = c.confirm(token); assert.equal(h.reads.length, 2); assert.equal(h.posts.length, 0)
      h.reads.at(-1).resolve(snapshot()); await flush(); assert.equal(h.posts.length, 1)
      h.posts[0].resolve(reply(h.posts[0].command)); await send
    }
  },
  async 'cancel and actual draft edits resume polling after a paused preferences confirmation'() {
    for (const edited of [false, true]) {
      const h = harness(), c = await h.ready(); h.settings({ name: '准备的名称' })
      const token = c.prepare('settings'); await h.advance(20000); assert.equal(h.reads.length, 1)
      if (edited) h.settings({ name: '真正的新名称' }); else c.cancel(token)
      assert.equal(c.getState().confirmPending, false); await h.advance(10000)
      assert.equal(h.reads.length, 2); h.reads.at(-1).resolve(snapshot()); await flush(); assert.equal(h.posts.length, 0)
    }
  },
  async 'expired paused preferences confirmation resumes GET polling without persisting an intent'() {
    const h = harness(), c = await h.ready(); h.feedback('过期的反馈')
    const token = c.prepare('feedback'); await h.advance(60001); assert.equal(h.reads.length, 1)
    await c.confirm(token); assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0)
    await h.advance(10000); assert.equal(h.reads.length, 2); h.reads.at(-1).resolve(snapshot()); await flush()
  },
  async 'inflight preference GET blocks a new native confirmation on an older verified snapshot'() {
    for (const kind of ['settings', 'feedback']) {
      const h = harness(), c = await h.ready(); h.settings({ name: '待保存名称' }); h.feedback('待保存反馈')
      const read = c.refresh(); assert.equal(c.prepare(kind), null); assert.equal(c.getState().confirmPending, false)
      h.reads.at(-1).resolve(snapshot()); await read; assert.equal(typeof c.prepare(kind), 'string'); assert.equal(h.posts.length, 0)
    }
  },
  async 'returning to retained valid drafts clears rejected input errors without retaining oversized plaintext'() {
    const h = harness(), c = await h.ready(); h.settings({ name: '保留名字' }); h.feedback('保留反馈')
    h.settings({ name: 'x'.repeat(1000) }); assert.ok(c.getState().settingsDraftError); assert.equal(c.getState().settingsDraft.name, '保留名字')
    h.settings({ name: '保留名字' }); assert.equal(c.getState().settingsDraftError, '')
    h.feedback('x'.repeat(2000)); assert.ok(c.getState().feedbackDraftError); assert.equal(c.getState().feedbackDraft.content, '保留反馈')
    h.feedback('保留反馈'); assert.equal(c.getState().feedbackDraftError, ''); assert.equal(h.posts.length, 0)
  },
  async 'explicit saved volume edit corrects different actual output but name editing never submits volume'() {
    const h = harness(), source = snapshot({ storedVolume: 50, outputVolume: 125 }), c = await h.ready(source)
    assert.equal(c.getState().settingsDraft.volume, 50); h.settings({ name: '仅修改名字' }); assert.deepEqual(plain(c.getState().settingsDirtyFields), ['name'])
    const nameSend = c.confirm(c.prepare('settings')); h.reads.at(-1).resolve(source); await flush(); assert.deepEqual(plain(h.posts[0].command.changes), { name: '仅修改名字' }); h.posts[0].resolve(reply(h.posts[0].command)); await nameSend
    const read = c.refresh(); h.reads.at(-1).resolve({ ...source, name: '仅修改名字' }); await read
    h.settings({ volume: 50 }); assert.deepEqual(plain(c.getState().settingsDirtyFields), ['volume'])
    const volumeSend = c.confirm(c.prepare('settings')); h.reads.at(-1).resolve({ ...source, name: '仅修改名字' }); await flush(); assert.deepEqual(plain(h.posts[1].command.changes), { volume: 50 }); h.posts[1].resolve(reply(h.posts[1].command)); await volumeSend
  },
  async 'polling updates only untouched settings and preserves all dirty feedback or settings input'() {
    const h = harness(), c = await h.ready(); h.settings({ name: '未保存名字' }); h.feedback('未保存反馈\n原文')
    const read = c.refresh(); h.reads.at(-1).resolve(snapshot({ name: '设备新名', storedVolume: 55, outputVolume: 55, followUpSeconds: 40 })); await read
    assert.deepEqual(plain(c.getState().settingsDraft), { name: '未保存名字', volume: 55, followUpSeconds: 40 }); assert.deepEqual(plain(c.getState().settingsDirtyFields), ['name']); assert.equal(c.getState().feedbackDraft.content, '未保存反馈\n原文')
  },
  async 'fresh confirmation sends only explicitly changed fields and stores no names content or credentials'() {
    const h = harness(), c = await h.ready(), send = await h.submit('settings', { name: '隐私名字' }); assert.deepEqual(plain(send.post.command.changes), { name: '隐私名字' }); assert.equal(c.confirm(send.token), send.result)
    const event = h.trace.find(item => item.type === 'command'); assert.deepEqual(event.marker, stored(pending('settings', send.post.command.requestId))); assert.ok(!JSON.stringify(event.marker).includes('隐私名字')); assert.ok(!JSON.stringify(event.marker).includes(TOKEN))
    send.post.resolve(reply(send.post.command)); await send.result; assert.equal(c.getState().settingsDirty, false); assert.equal(c.getState().settingsError, ''); assert.equal(h.storage.get(STORAGE).settings, null)
  },
  async 'fresh GET removes already applied fields rather than replaying the whole old form'() {
    const h = harness(), c = await h.ready(); h.settings({ name: '已更新名字', followUpSeconds: 45 }); const token = c.prepare('settings'), send = c.confirm(token)
    h.reads.at(-1).resolve(snapshot({ name: '已更新名字' })); await flush(); assert.deepEqual(plain(h.posts[0].command.changes), { followUpSeconds: 45 }); h.posts[0].resolve(reply(h.posts[0].command)); await send
    const refresh = c.refresh(); h.reads.at(-1).resolve(snapshot({ name: '已更新名字', followUpSeconds: 45 })); await refresh
    assert.equal(c.prepare('settings'), null); assert.equal(h.posts.length, 1)
  },
  async 'volume changes need actual availability and never silently attach unknown unchanged fields'() {
    const h = harness(), c = await h.ready(snapshot({ canChangeVolume: false, outputVolumeAvailable: null, outputVolume: null, outputMuted: null })); h.settings({ volume: 45 }); assert.equal(c.prepare('settings'), null)
    h.settings({ name: '新名', volume: 37 }); assert.equal(c.prepare('settings'), null)
    const fresh = harness(), allowed = await fresh.ready(snapshot({ canChangeVolume: false, outputVolumeAvailable: null, outputVolume: null, outputMuted: null })); const send = await fresh.submit('settings', { name: '独立名称' }, snapshot({ canChangeVolume: false, outputVolumeAvailable: null, outputVolume: null, outputMuted: null })); assert.deepEqual(plain(send.post.command.changes), { name: '独立名称' }); send.post.resolve(reply(send.post.command)); await send.result; assert.equal(allowed.getState().settingsError, '')
  },
  async 'changed input invalidates confirmation while open and during fresh GET even when restored'() {
    for (const kind of ['settings', 'feedback']) {
      const h = harness(), c = await h.ready(); kind === 'settings' ? h.settings({ name: '名字甲' }) : h.feedback('原反馈')
      const token = c.prepare(kind); kind === 'settings' ? h.settings({ name: '名字乙' }) : h.feedback('新反馈'); kind === 'settings' ? h.settings({ name: '名字甲' }) : h.feedback('原反馈'); await c.confirm(token); assert.equal(h.posts.length, 0)
      const token2 = c.prepare(kind), send = c.confirm(token2); kind === 'settings' ? h.settings({ name: '最新名字' }) : h.feedback('最新反馈'); h.reads.at(-1).resolve(snapshot()); await send; assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0)
    }
  },
  async 'hide device auth refresh re-login and different user invalidate preflight without leaking old errors'() {
    for (const change of [h => h.controller.hide(), h => h.change({ selectedPetId: 'pet_2' }), h => h.change({ listLoading: true }), h => h.change({ expiresAt: '2026-11-01T02:00:00Z' }), h => h.change({}, 'user_2')]) {
      const h = harness(), c = await h.ready(); h.settings({ name: '旧私有名' }); const send = c.confirm(c.prepare('settings')), read = h.reads.at(-1); change(h); read.resolve(snapshot()); await send
      assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0); assert.equal(c.getState().settingsError, '')
    }
  },
  async 'disabled or changed permissions reject fresh confirmations and preflight failure is honestly not sent'() {
    for (const fresh of [snapshot({ settingsEnabled: false, canChangeName: false, canChangeVolume: false, canChangeFollowUp: false }), snapshot({ settingsReviewRequired: true, manualReviewRequired: true, canChangeName: false, canChangeVolume: false, canChangeFollowUp: false }), unavailable()]) {
      const h = harness(), c = await h.ready(); h.settings({ name: '名字' }); const send = c.confirm(c.prepare('settings')); h.reads.at(-1).resolve(fresh); await send; assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0)
    }
    const h = harness(), c = await h.ready(); h.feedback(); const send = c.confirm(c.prepare('feedback')); h.reads.at(-1).reject(problem('TIMEOUT')); await send; assert.match(c.getState().feedbackError, /请求未发送.*核验失败/); assert.equal(h.storageWrites(), 0)
  },
  async 'a superseded confirmation GET cannot dispatch after a newer manual read succeeds or fails'() {
    for (const fail of [false, true]) {
      const h = harness(), c = await h.ready(); h.settings({ name: '旧确认目标' }); const send = c.confirm(c.prepare('settings')), confirmationRead = h.reads.at(-1)
      const newer = c.refresh(); if (fail) h.reads.at(-1).reject(problem('TIMEOUT')); else h.reads.at(-1).resolve(snapshot({ name: '其他已更新' })); await newer
      confirmationRead.resolve(snapshot()); await send
      assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0); assert.match(c.getState().settingsError, /请求未发送.*已更新或失效/)
      if (fail) assert.equal(c.getState().snapshot, null); else assert.equal(c.getState().snapshot.name, '其他已更新')
    }
  },
  async 'superseded preferences preflight failure preserves newer snapshot global error and polling policy'() {
    for (const fail of [false, true]) {
      const h = harness(), c = await h.ready(); h.settings({ name: '原目标' }); const send = c.confirm(c.prepare('settings')), oldRead = h.reads.at(-1), newer = c.refresh()
      if (fail) h.reads.at(-1).reject(problem('TIMEOUT')); else h.reads.at(-1).resolve(snapshot({ name: '最新已知名字', storedVolume: 71, outputVolume: 71 }))
      await newer; const previous = c.getState(), count = h.reads.length
      oldRead.reject(problem('TIMEOUT')); await send
      assert.deepEqual(plain(c.getState().snapshot), plain(previous.snapshot)); assert.equal(c.getState().error, previous.error); assert.match(c.getState().settingsError, /请求未发送/)
      assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0); await h.advance(10000); assert.equal(h.reads.length, count + (fail ? 0 : 1))
    }
  },
  async 'bad storage read write corruption or readback fail closed before any POST on either kind'() {
    for (const kind of ['settings', 'feedback']) for (const scenario of ['read', 'write', 'corrupt', 'readback']) {
      const h = harness(), c = await h.ready(); kind === 'settings' ? h.settings({ name: '私有名字' }) : h.feedback('私有反馈'); const token = c.prepare(kind)
      if (scenario === 'read') h.failStorageRead()
      if (scenario === 'write') h.failStorageWrite()
      if (scenario === 'corrupt') h.storage.set(STORAGE, { ...stored(), secret: TOKEN })
      if (scenario === 'readback') { const original = h.wx.setStorageSync; h.wx.setStorageSync = (...args) => { original(...args); h.failStorageRead() } }
      const send = c.confirm(token); h.reads.at(-1).resolve(snapshot()); await send; assert.equal(h.posts.length, 0); assert.match(c.getState()[kind + 'Error'], /存储不可用.*禁止发送/)
    }
  },
  async 'timeout unknown and 404 keep one original marker with explicit GET only and no automatic retry'() {
    for (const kind of ['settings', 'feedback']) {
      const h = harness(), c = await h.ready(), send = await h.submit(kind); send.post.reject(problem('TIMEOUT')); await send.result
      assert.equal(c.getState()[kind + 'Blocked'], true); assert.equal(c.getState()[kind === 'settings' ? 'canCheckSettings' : 'canCheckFeedback'], true); assert.equal(c.prepare(kind), null)
      let query = c.checkReceipt(kind); h.receipts.at(-1).reject(problem('NOT_FOUND')); await query; assert.match(c.getState()[kind + 'Error'], /不证明请求未提交/)
      query = c.checkReceipt(kind); h.receipts.at(-1).resolve(reply(send.post.command, 'unknown')); await query; assert.equal(h.storage.get(STORAGE)[kind].requestId, send.post.command.requestId)
      await h.advance(60000); assert.equal(h.posts.length, 1); assert.equal(h.receipts.length, 2)
    }
  },
  async 'feedback has independent lane during unknown settings and cannot clear its request or dirty fields'() {
    const h = harness(), c = await h.ready(), first = await h.submit('settings'); first.post.reject(problem('TIMEOUT')); await first.result
    const second = await h.submit('feedback'); assert.ok(h.storage.get(STORAGE).settings); assert.ok(h.storage.get(STORAGE).feedback); second.post.resolve(reply(second.post.command)); await second.result
    assert.equal(c.getState().settingsBlocked, true); assert.equal(c.getState().settingsDirty, true); assert.equal(h.storage.get(STORAGE).feedback, null); assert.equal(c.getState().feedbackDraft.content, '')
  },
  async 'settings recorded while feedback fresh GET is pending never cancels its explicit independent intent'() {
    const h = harness(), c = await h.ready(), first = await h.submit('settings'); h.feedback('独立反馈正文')
    const token = c.prepare('feedback'), send = c.confirm(token), feedbackRead = h.reads.at(-1)
    first.post.resolve(reply(first.post.command)); await first.result
    const savedDraft = c.getState().settingsDraft.name, savedReceipt = c.getState().settingsReceipt
    feedbackRead.resolve(snapshot({ name: '回执前旧读数' })); await flush()
    assert.equal(h.posts.length, 2); assert.equal(h.posts[1].command.kind, 'feedback'); assert.equal(c.confirm(token), send)
    assert.equal(c.getState().settingsDraft.name, savedDraft); assert.deepEqual(plain(c.getState().settingsReceipt), plain(savedReceipt)); assert.equal(c.getState().feedbackError, '')
    assert.equal(h.storage.get(STORAGE).settings, null); assert.equal(h.storage.get(STORAGE).feedback.requestId, h.posts[1].command.requestId)
    h.posts[1].resolve(reply(h.posts[1].command)); await send; assert.equal(h.posts.length, 2)
  },
  async 'failed feedback preflight after settings receipt never erases the newer form receipt or poll policy'() {
    for (const status of ['recorded', 'unknown']) {
      const h = harness(), c = await h.ready(), first = await h.submit('settings'); h.feedback('独立的反馈')
      const send = c.confirm(c.prepare('feedback')), oldRead = h.reads.at(-1); first.post.resolve(reply(first.post.command, status)); await first.result
      const previous = c.getState(), count = h.reads.length; oldRead.reject(problem('TIMEOUT')); await send
      assert.deepEqual(plain(c.getState().snapshot), plain(previous.snapshot)); assert.deepEqual(plain(c.getState().settingsDraft), plain(previous.settingsDraft)); assert.deepEqual(plain(c.getState().settingsReceipt), plain(previous.settingsReceipt))
      assert.equal(c.getState().settingsError, previous.settingsError); assert.equal(c.getState().error, previous.error); assert.match(c.getState().feedbackError, /请求未发送/)
      assert.equal(h.posts.length, 1); assert.equal(h.storage.get(STORAGE).feedback, null); assert.equal(Boolean(h.storage.get(STORAGE).settings), status === 'unknown')
      await h.advance(10000); assert.equal(h.reads.length, count + 1)
    }
  },
  async 'settings remain independent when feedback has an unknown result or a feedback review gate'() {
    const h = harness(), c = await h.ready(), first = await h.submit('feedback'); first.post.reject(problem('TIMEOUT')); await first.result
    const read = c.refresh(), reviewed = snapshot({ feedbackReviewRequired: true, manualReviewRequired: true, canSubmitFeedback: false }); h.reads.at(-1).resolve(reviewed); await read
    const second = await h.submit('settings', { name: '独立设置' }, reviewed); second.post.resolve(reply(second.post.command)); await second.result
    assert.ok(h.storage.get(STORAGE).feedback); assert.equal(h.storage.get(STORAGE).settings, null); assert.equal(c.getState().feedbackBlocked, true); assert.equal(c.getState().settingsBlocked, false)
  },
  async 'known partial preserves requested draft needs fresh read and never automatically replays applied fields'() {
    const h = harness(), c = await h.ready(), send = await h.submit('settings', { name: '保存新名', followUpSeconds: 50 })
    send.post.resolve(receipt(send.post.command.requestId, 'settings', 'partially_applied', [{ step: 'pet_settings', status: 'applied' }, { step: 'follow_up', status: 'not_sent' }])); await send.result
    assert.equal(c.getState().settingsBlocked, false); assert.equal(c.getState().settingsNeedsRefresh, true); assert.equal(c.getState().settingsDirty, true); assert.equal(c.prepare('settings'), null); assert.equal(h.posts.length, 1)
    const read = c.refresh(); h.reads.at(-1).resolve(snapshot({ name: '保存新名' })); await read; assert.equal(c.getState().settingsNeedsRefresh, false)
    const token = c.prepare('settings'), correction = c.confirm(token); h.reads.at(-1).resolve(snapshot({ name: '保存新名' })); await flush()
    assert.deepEqual(plain(h.posts[1].command.changes), { followUpSeconds: 50 }); assert.notEqual(h.posts[1].command.requestId, send.post.command.requestId)
    h.posts[1].resolve(reply(h.posts[1].command)); await correction
  },
  async 'pre-receipt GET cannot unlock known partial or overwrite a just recorded form'() {
    for (const partial of [false, true]) {
      const h = harness(), c = await h.ready(), send = await h.submit('settings', { name: '刚保存名', followUpSeconds: 50 }), oldRead = c.refresh()
      const response = partial ? receipt(send.post.command.requestId, 'settings', 'partially_applied', [{ step: 'pet_settings', status: 'applied' }, { step: 'follow_up', status: 'not_sent' }]) : reply(send.post.command)
      send.post.resolve(response); await send.result; h.reads.at(-1).resolve(snapshot()); await oldRead
      assert.equal(c.getState().settingsDraft.name, '刚保存名')
      if (partial) { assert.equal(c.getState().settingsNeedsRefresh, true); assert.equal(c.prepare('settings'), null) }
      const after = c.refresh(); h.reads.at(-1).resolve(snapshot({ name: '刚保存名', followUpSeconds: partial ? 25 : 50 })); await after
      assert.equal(c.getState().settingsNeedsRefresh, false)
    }
  },
  async 'sending can only be queried and later recorded clears only an unchanged submitted draft'() {
    for (const kind of ['settings', 'feedback']) {
      const h = harness(), c = await h.ready(), send = await h.submit(kind); send.post.resolve(reply(send.post.command, 'sending')); await send.result
      assert.equal(c.getState()[kind + 'Blocked'], true); kind === 'settings' ? h.settings({ name: '更新的名字' }) : h.feedback('更新的反馈')
      const query = c.checkReceipt(kind); h.receipts.at(-1).resolve(reply(send.post.command)); await query
      assert.equal(c.getState()[kind + 'Blocked'], false); assert.equal(c.getState()[kind + 'Error'], '')
      if (kind === 'settings') { assert.equal(c.getState().settingsDraft.name, '更新的名字'); assert.equal(c.getState().settingsDirty, true) }
      else assert.equal(c.getState().feedbackDraft.content, '更新的反馈')
    }
  },
  async 'not sent is terminal without automatic resubmit or clearing feedback and setting drafts'() {
    for (const kind of ['settings', 'feedback']) {
      const h = harness(), c = await h.ready(), send = await h.submit(kind); send.post.resolve(reply(send.post.command, 'not_sent')); await send.result
      assert.equal(c.getState()[kind + 'Blocked'], false); assert.match(c.getState()[kind + 'Error'], /确认本次未发送/); assert.equal(h.posts.length, 1)
      if (kind === 'settings') assert.equal(c.getState().settingsDirty, true); else assert.ok(c.getState().feedbackDraft.content)
    }
  },
  async 'old recorded receipt never masks a later timeout on either independent kind'() {
    for (const kind of ['settings', 'feedback']) {
      const h = harness(), c = await h.ready(), first = await h.submit(kind); first.post.resolve(reply(first.post.command)); await first.result; assert.equal(c.getState()[kind + 'Receipt'].status, 'recorded')
      const second = await h.submit(kind, kind === 'settings' ? { name: '再改名字' } : '第二条反馈'); assert.equal(c.getState()[kind + 'Receipt'], null)
      second.post.reject(problem('TIMEOUT')); await second.result; assert.equal(c.getState()[kind + 'Receipt'], null); assert.match(c.getState()[kind + 'Error'], /尚未确认/)
    }
  },
  async 'unresolved markers survive hide app recreation and same user re-login without storing private drafts'() {
    const h = harness(), c = await h.ready(), send = await h.submit('feedback', '私有反馈正文'); send.post.reject(problem('TIMEOUT')); await send.result; c.hide()
    const restored = harness(h.storage), next = await restored.ready(); restored.change({ expiresAt: '2026-11-01T02:00:00Z' }); restored.reads.at(-1).resolve(snapshot()); await flush()
    assert.equal(next.prepare('feedback'), null); assert.equal(next.getState().canCheckFeedback, true); assert.equal(next.getState().feedbackDraft.content, ''); assert.ok(!JSON.stringify(h.storage.get(STORAGE)).includes('私有反馈正文'))
  },
  async 'foreign identity or pet markers block replacement with generic explanation and no IDs or private data'() {
    const h = harness(); h.storage.set(STORAGE, stored(pending('settings', 'private_settings'), pending('feedback', 'private_feedback'))); const c = await h.ready(); h.change({}, 'user_2'); h.reads.at(-1).resolve(snapshot()); await flush()
    const value = c.getState(); assert.equal(value.settingsBlocked, true); assert.equal(value.feedbackBlocked, true); assert.equal(value.canCheckSettings, false); assert.equal(value.canCheckFeedback, false); assert.match(value.settingsError, /另一个身份或设备.*不能清数据绕过/)
    assert.ok(!JSON.stringify(value).includes('private_')); assert.ok(!JSON.stringify(value).includes('user_1')); assert.equal(c.prepare('settings'), null); assert.equal(c.prepare('feedback'), null); assert.equal(h.storageWrites(), 0)
  },
  async 'late hidden recorded responses keep marker until a visible explicit receipt query reconciles'() {
    const h = harness(), c = await h.ready(), send = await h.submit('settings'), count = h.values.length; c.hide(); send.post.resolve(reply(send.post.command)); await send.result
    assert.equal(h.values.length, count); assert.ok(h.storage.get(STORAGE).settings); h.show(); h.reads.at(-1).resolve(snapshot()); await flush()
    const query = c.checkReceipt('settings'); h.receipts.at(-1).resolve(reply(send.post.command)); await query; assert.equal(c.getState().settingsBlocked, false)
  },
  async 'malformed mismatched IDs kinds or step receipts cannot clear pending marker or form'() {
    for (const response of [receipt('wrong'), receipt('preferences_1', 'feedback'), receipt('preferences_1', 'settings', 'recorded', [{ step: 'follow_up', status: 'applied' }]), { ...receipt(), status: 'success' }]) {
      const h = harness(), c = await h.ready(), send = await h.submit(); send.post.resolve(response); await send.result; assert.equal(c.getState().settingsBlocked, true); assert.equal(c.getState().settingsDirty, true); assert.equal(c.getState().settingsReceipt, null)
    }
  },
  async 'refresh stays single flight serial ten seconds errors pause and reads never overwrite dirty input'() {
    const h = harness(), c = await h.ready(); h.settings({ volume: 20 }); const first = c.refresh(); assert.equal(c.refresh(), first); await h.advance(30000); assert.equal(h.reads.length, 2)
    h.reads.at(-1).reject(problem('TIMEOUT')); await first; await h.advance(60000); assert.equal(h.reads.length, 2); assert.equal(c.getState().snapshot, null); assert.equal(c.getState().settingsDraft.volume, 20); assert.match(c.getState().error, /自动读取已暂停/)
    const retry = c.refresh(); h.reads.at(-1).resolve(snapshot()); await retry; await h.advance(10000); assert.equal(h.reads.length, 4); assert.equal(h.posts.length, 0); assert.equal(h.receipts.length, 0)
  },
  async 'corrupt extra slots plaintext markers aliases and duplicate IDs fail closed without overwriting storage'() {
    for (const value of [null, [], { ...stored(), secret: TOKEN }, { ...stored(), version: 2 }, stored({ ...pending(), name: '私有名字' }), stored(null, { ...pending('feedback'), content: '反馈正文' }), stored(pending(), pending('feedback')), stored(pending('feedback')), stored(null, pending('settings'))]) {
      const h = harness(); h.storage.set(STORAGE, value); const c = await h.ready(); assert.match(c.getState().settingsError, /存储不可用.*禁止发送/); assert.equal(c.getState().settingsError, c.getState().feedbackError); assert.equal(c.prepare('settings'), null); assert.equal(c.prepare('feedback'), null); assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0)
    }
  }
}
;(async () => { for (const [name, run] of Object.entries(cases)) { await run(); console.log('PASS ' + name) }; console.log('Live preferences smoke passed: ' + Object.keys(cases).length) })().catch(error => { console.error(error); process.exitCode = 1 })
