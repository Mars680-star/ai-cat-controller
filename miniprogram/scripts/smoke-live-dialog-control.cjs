// Real TS auth/session/control code; synthetic clock/wx/storage/HTTP only.
// No device, real credentials, OS network or local secret files are used.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..'), UTC = '2026-11-01T00:00:00Z', NOW = Date.parse(UTC)
const TOKEN = 'synthetic-private-dialog-control-token'
const STORAGE = 'aiCatDialogPendingV1'
const plain = value => JSON.parse(JSON.stringify(value))
const flush = async () => { for (let i = 0; i < 45; i++) await Promise.resolve() }
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const problem = code => Object.assign(new Error('private upstream details'), { code })
const control = (patch = {}) => ({ enabled: true, verified: true, canListen: true, canInterrupt: true, canSendText: true, manualReviewRequired: false, reason: '', state: 'ready', stale: false, sampledAt: UTC, ...patch })
const receipt = (requestId = 'dialog_1', kind = 'text', status = 'accepted') => ({ requestId, kind, status, updatedAt: UTC })
const device = () => ({ readOnly: true, sampledAt: UTC, health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' }, device: { connected: true, deviceSerial: '7c2****a128', networkName: null, batteryPercent: 66, batteryState: 'charging', batteryVoltage: 4.1, chargerOnline: true, batteryPresent: true, lastSeenAt: null }, dialog: { state: 'offline', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false } })
const pending = (kind = 'text', requestId = 'old_1', userId = 'user_1', petId = 'pet_1') => ({ userId, petId, requestId, kind })
const stored = (main = null, interrupt = null) => ({ version: 1, main, interrupt })

function harness(existingStorage) {
  let now = NOW, timerId = 0, storageReads = 0, storageWrites = 0, readFails = false, writeFails = false
  const timers = new Map(), modules = new Map(), requests = [], logins = [], logs = [], storage = existingStorage || new Map(), trace = []
  class ClockDate extends Date { constructor(...args) { if (args.length) super(...args); else super(now) } static now() { return now } }
  const wx = { login(value) { logins.push(value) }, request(value) { requests.push(value); trace.push({ type: 'http', method: value.method, url: value.url }); return { abort() { value.fail({ errMsg: 'private-token aborted' }) } } },
    getStorageSync(key) { storageReads++; if (readFails) throw Error('private storage failure'); return storage.has(key) ? plain(storage.get(key)) : '' },
    setStorageSync(key, value) { storageWrites++; if (writeFails) throw Error('private storage failure'); storage.set(key, plain(value)); trace.push({ type: 'storage', value: plain(value) }) } }
  const context = vm.createContext({ wx, Date: ClockDate,
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, at: now + Math.max(1, delay) }); return id }, clearTimeout(id) { timers.delete(id) },
    setInterval() { throw Error('No interval permitted') }, clearInterval() {}, console: Object.fromEntries(['log', 'warn', 'error', 'info', 'debug'].map(name => [name, (...args) => logs.push(args)])) })
  function load(relative, parent = root) {
    let filename = path.resolve(parent, relative)
    if (!filename.endsWith('.ts')) filename += '.ts'
    if (modules.has(filename)) return modules.get(filename).exports
    const module = { exports: {} }; modules.set(filename, module)
    const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 } }).outputText
    new vm.Script('(function(require,module,exports){' + output + '\n})', { filename }).runInContext(context)(name => load(name, path.dirname(filename)), module, module.exports)
    return module.exports
  }
  const api = load('services/live-dialog-control')
  const h = { api, wx, load, requests, logins, logs, storage, trace, timers,
    storageReads: () => storageReads, storageWrites: () => storageWrites,
    failStorageRead(value = true) { readFails = value }, failStorageWrite(value = true) { writeFails = value },
    success(value, index = requests.length - 1, statusCode = 200) { requests[index].success({ statusCode, data: { success: true, data: value } }) },
    failure(index = requests.length - 1) { requests[index].fail({ errMsg: 'private-token timeout' }) },
    async authenticate() {
      this.session = load('services/live-device-session').getLiveDeviceSession(); this.anchor = this.session.attach(() => {})
      const ready = this.session.enable(); this.success({ mode: 'local-development', wechatConfigured: true, readOnly: false }); await ready
      const login = this.session.login(); logins.at(-1).success({ code: 'synthetic-code' }); await flush()
      this.success({ accessToken: TOKEN, userId: 'user_1', expiresAt: '2026-11-01T01:00:00Z' }); await flush()
      this.success([{ petId: 'pet_1', name: '小安' }, { petId: 'pet_2', name: '另一只猫' }]); await login
      const select = this.session.selectPet('pet_1'); this.success(device()); await select
    },
    factory(signedIn = true) {
      this.values = []; this.calls = []; this.reads = []; this.posts = []; this.receipts = []
      this.identity = { mode: signedIn ? 'live' : 'preview', signedIn, expiresAt: '2026-11-01T01:00:00Z', pets: [{ petId: 'pet_1' }, { petId: 'pet_2' }], selectedPetId: signedIn ? 'pet_1' : '', loading: false, listLoading: false }
      this.userId = 'user_1'
      const listeners = new Set(), fake = { getState: () => plain(this.identity), getIdentity: () => this.identity.signedIn ? { userId: this.userId, expiresAt: this.identity.expiresAt } : null,
        attach(fn) { listeners.add(fn); fn(); return () => listeners.delete(fn) } }
      fake.readDialogControl = () => { this.calls.push({ name: 'read' }); const next = deferred(); this.reads.push(next); return next.promise }
      fake.sendDialogCommand = command => { this.calls.push({ name: 'post', command: plain(command) }); trace.push({ type: 'command', command: plain(command), marker: plain(storage.get(STORAGE)) }); const next = deferred(); this.posts.push({ ...next, command: plain(command) }); return next.promise }
      fake.readDialogReceipt = requestId => { this.calls.push({ name: 'receipt', requestId }); const next = deferred(); this.receipts.push({ ...next, requestId }); return next.promise }
      this.change = (patch, userId) => { Object.assign(this.identity, patch); if (userId !== undefined) this.userId = userId; for (const fn of [...listeners]) fn() }
      let ids = 0
      this.makeController = () => api.createLiveDialogControl(fake, prefix => prefix + '_' + (++ids), wx)
      this.controller = this.makeController(); this.show = controller => (controller || this.controller).show(value => this.values.push(value)); this.show()
      return this.controller
    },
    async ready(sample = control()) { const controller = this.factory(); this.reads[0].resolve(sample); await flush(); return controller },
    async submit(kind = 'text', content = '你好') {
      const c = this.controller
      if (kind === 'text') c.setDraft(content)
      const token = c.prepare(kind); assert.equal(typeof token, 'string')
      const result = c.confirm(token); this.reads.at(-1).resolve(control()); await flush()
      assert.ok(this.posts.length); return { result, post: this.posts.at(-1), token }
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
  async 'control status is strict and cross-field availability cannot bypass gates'() {
    const h = harness(), v = h.api.verifyDialogControl
    assert.equal(v(control()).canListen, true)
    assert.equal(v(control({ canListen: false, canSendText: false, manualReviewRequired: true, stale: true, state: 'unavailable', reason: '需检查' })).canInterrupt, true)
    for (const value of [{ ...control(), secret: TOKEN }, control({ enabled: false }), control({ verified: false }), control({ stale: true }), control({ manualReviewRequired: true }), control({ state: 'thinking' }), control({ reason: '\u202ehidden' }), control({ canListen: false, canInterrupt: false, canSendText: false }), control({ sampledAt: '2026-02-30T00:00:00Z' }), control({ canListen: 1 })]) assert.throws(() => v(value), /UNVERIFIED/)
    for (const state of ['processing_audio', 'followup_preparing', 'connected', 'proactive_queued', 'proactive_speaking', 'proactive_finishing', 'stopping']) assert.equal(v(control({ state, canListen: false, canSendText: false, reason: '忙碌' })).state, state)
  },
  async 'fixed command rejects extras nontext content controls invalid unicode and overlength'() {
    const h = harness(), verify = h.api.verifyDialogCommand
    assert.deepEqual(plain(verify({ requestId: 'r1', kind: 'listen' })), { requestId: 'r1', kind: 'listen' })
    assert.equal(verify({ requestId: 'r1', kind: 'text', content: '😀'.repeat(500) }).content.length, 1000)
    assert.throws(() => verify({ requestId: 'r1\n', kind: 'listen' }), /UNVERIFIED/)
    for (const value of [{ requestId: 'r1', kind: 'listen', content: null }, { requestId: 'r1', kind: 'interrupt', content: '' }, { requestId: 'r1', kind: 'text', content: 'x', url: 'http://device/' }, { requestId: 'r1', kind: 'text', content: 'x', userId: 'u' }, { requestId: '../bad', kind: 'listen' }, { requestId: 'r1', kind: 'text', content: '  ' }, ...['x'.repeat(501), '\ud800', '\udc00', 'x\ny', 'x\ty', '\u0080hidden', '\u202ehidden'].map(content => ({ requestId: 'r1', kind: 'text', content }))]) assert.throws(() => verify(value), /UNVERIFIED/)
  },
  async 'receipt accepts only bounded unknown sending not sent or acknowledgement never completion'() {
    const h = harness(), verify = h.api.verifyDialogReceipt
    for (const state of ['sending', 'unknown', 'accepted', 'not_sent']) assert.equal(verify(receipt('r1', 'listen', state)).status, state)
    for (const value of [{ ...receipt(), status: 'completed' }, { ...receipt(), content: 'private text' }, { ...receipt(), kind: 'wake' }, { ...receipt(), requestId: '../bad' }, { ...receipt(), updatedAt: '2026-04-31T00:00:00Z' }]) assert.throws(() => verify(value), /UNVERIFIED/)
  },
  async 'real shared identity getter is explicit safe copied and not broadcast into UI'() {
    const h = harness(); await h.authenticate(); const identity = h.session.getIdentity()
    assert.deepEqual(plain(identity), { userId: 'user_1', expiresAt: '2026-11-01T01:00:00Z' }); identity.userId = 'mutated'
    assert.equal(h.session.getIdentity().userId, 'user_1'); assert.ok(!('userId' in h.session.getState())); assert.ok(!JSON.stringify(h.session.getState()).includes(TOKEN))
  },
  async 'client rejects paths extras and controls before HTTP'() {
    const h = harness(), c = h.load('services/cloud-auth-client').createCloudAuthClient(h.wx)
    for (const promise of [c.readDialogControl('../bad'), c.readDialogReceipt('pet_1', '../bad'), c.sendDialogCommand('pet_1', { kind: 'text', requestId: 'r1', content: 'x', parameters: {} }), c.sendDialogCommand('pet_1', { kind: 'listen', requestId: 'r1', content: null })]) await assert.rejects(promise, error => error.code === 'INVALID_ID')
    assert.equal(h.requests.length, 0); assert.equal(h.logins.length, 0)
  },
  async 'client fixed GET and POST routes only expose contracted values with 5 and 10 second deadlines'() {
    const h = harness(); await h.authenticate()
    const read = h.session.readDialogControl(), get = h.requests.at(-1)
    assert.equal(get.url, 'http://127.0.0.1:8787/v1/pets/pet_1/dialog/control'); assert.equal(get.method, 'GET'); assert.equal(get.timeout, 5000); assert.ok(!('data' in get))
    h.success(control()); await read
    const write = h.session.sendDialogCommand({ requestId: 'r1', kind: 'text', content: '问题' }), post = h.requests.at(-1)
    assert.equal(post.url, 'http://127.0.0.1:8787/v1/pets/pet_1/dialog/commands'); assert.equal(post.timeout, 10000); assert.deepEqual(plain(post.data), { requestId: 'r1', kind: 'text', content: '问题' })
    h.success(receipt('r1')); await write
    const lookup = h.session.readDialogReceipt('r1'), request = h.requests.at(-1)
    assert.equal(request.url, 'http://127.0.0.1:8787/v1/pets/pet_1/dialog/requests/r1'); assert.equal(request.method, 'GET'); assert.equal(request.header.Authorization, 'Bearer ' + TOKEN)
    h.success(receipt('r1')); await lookup; assert.equal(h.storageWrites(), 0)
  },
  async 'client refuses mismatched receipt IDs kinds and arbitrary server fields'() {
    const h = harness(); await h.authenticate()
    for (const response of [receipt('wrong', 'listen'), receipt('r1', 'text'), { ...receipt('r1', 'listen'), rawSecret: TOKEN }]) {
      const p = h.session.sendDialogCommand({ requestId: 'r1', kind: 'listen' }), rejected = assert.rejects(p, error => error.code === 'UNVERIFIED')
      h.success(response); await rejected
    }
    const get = h.session.readDialogReceipt('r1'), rejected = assert.rejects(get, error => error.code === 'UNVERIFIED'); h.success(receipt('wrong')); await rejected
  },
  async 'client POST timeout never replays and late acceptance is ignored until explicit receipt GET'() {
    const h = harness(); await h.authenticate(); const write = h.session.sendDialogCommand({ requestId: 'r1', kind: 'listen' }), post = h.requests.at(-1), rejected = assert.rejects(write, error => error.code === 'TIMEOUT')
    await h.advance(10000); await rejected; post.success({ statusCode: 200, data: { success: true, data: receipt('r1', 'listen') } }); await flush()
    assert.equal(h.requests.filter(r => r.url.endsWith('/dialog/commands')).length, 1)
    const lookup = h.session.readDialogReceipt('r1'); h.success(receipt('r1', 'listen', 'sending')); assert.equal((await lookup).status, 'sending')
  },
  async 'show refresh and preview never login or submit control automatically'() {
    const h = harness(), c = h.factory(false); await c.refresh(); await c.checkReceipt(); await c.confirm('unknown'); await h.advance(60000)
    assert.equal(h.calls.length, 0); assert.equal(h.logins.length, 0); assert.equal(h.requests.length, 0)
    const real = harness(), live = await real.ready(); const refresh = live.refresh(); real.reads.at(-1).resolve(control()); await refresh
    assert.equal(real.posts.length, 0); assert.equal(real.storageWrites(), 0)
  },
  async 'prepare and cancel submit nothing and a canceled token cannot post'() {
    const h = harness(), c = await h.ready(); c.setDraft('问题'); const token = c.prepare('text')
    assert.equal(typeof token, 'string'); assert.equal(c.getState().confirmPending, true); assert.equal(h.reads.length, 1)
    c.cancel(token); await c.confirm(token); assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0)
  },
  async 'identical input events preserve both an empty listen confirmation and an unchanged text confirmation'() {
    for (const kind of ['listen', 'text']) {
      const h = harness(), c = await h.ready(), content = kind === 'text' ? '保持不变的问题' : ''
      c.setDraft(content); const version = c.getState().draftVersion, token = c.prepare(kind)
      c.setDraft(content); c.setDraft(content)
      assert.equal(c.getState().confirmPending, true); assert.equal(c.getState().draftVersion, version)
      const send = c.confirm(token); assert.equal(h.reads.length, 2); assert.equal(h.posts.length, 0)
      h.reads.at(-1).resolve(control()); await flush(); assert.equal(h.posts.length, 1)
      const post = h.posts.at(-1); post.resolve(receipt(post.command.requestId, kind)); await send
    }
  },
  async 'prepared native confirmation pauses optional polls but still requires a fresh permission GET before POST'() {
    const h = harness(), c = await h.ready(), token = c.prepare('listen')
    await h.advance(30000); assert.equal(h.reads.length, 1); assert.equal(c.getState().confirmPending, true)
    const send = c.confirm(token); assert.equal(h.reads.length, 2); assert.equal(h.posts.length, 0)
    h.reads.at(-1).resolve(control({ enabled: false, verified: false, canListen: false, canInterrupt: false, canSendText: false, reason: '权限已撤销' }))
    await send; assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0)
  },
  async 'cancel and a real draft edit each resume serial polling after a paused native confirmation'() {
    for (const edit of [false, true]) {
      const h = harness(), c = await h.ready(), token = c.prepare('listen')
      await h.advance(3000)
      if (edit) c.setDraft('真正修改正文'); else c.cancel(token)
      assert.equal(c.getState().confirmPending, false)
      await h.advance(9999); assert.equal(h.reads.length, 1)
      await h.advance(1); assert.equal(h.reads.length, 2)
      await h.advance(30000); assert.equal(h.reads.length, 2)
      h.reads.at(-1).resolve(control()); await flush(); assert.equal(h.posts.length, 0)
    }
  },
  async 'an expired paused confirmation resumes read polling without generating an intent'() {
    const h = harness(), c = await h.ready(), token = c.prepare('listen')
    await h.advance(60001); assert.equal(h.reads.length, 1)
    await c.confirm(token); await h.advance(10000)
    assert.equal(h.reads.length, 2); assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0)
    h.reads.at(-1).resolve(control()); await flush()
  },
  async 'an already running permission read cannot open a native confirmation on an old snapshot'() {
    const h = harness(), c = await h.ready(), read = c.refresh()
    assert.equal(c.prepare('listen'), null); assert.equal(c.prepare('interrupt'), null)
    assert.match(c.getState().mainError, /正在核对/); assert.equal(c.getState().confirmPending, false)
    h.reads.at(-1).resolve(control()); await read
    assert.equal(typeof c.prepare('listen'), 'string'); assert.equal(h.posts.length, 0)
  },
  async 'repeating retained valid text clears a previous over-bound error without retaining the rejected body'() {
    const h = harness(), c = await h.ready(); c.setDraft('保留原文'); c.setDraft('x'.repeat(1001))
    assert.equal(c.getState().draftText, '保留原文'); assert.ok(c.getState().draftError)
    c.setDraft('保留原文'); assert.equal(c.getState().draftError, '')
    assert.equal(typeof c.prepare('text'), 'string'); assert.equal(h.posts.length, 0)
  },
  async 'every confirmation performs a new GET and commits only id marker before one POST'() {
    const h = harness(), c = await h.ready(); const { result, post, token } = await h.submit()
    assert.equal(c.confirm(token), result); assert.equal(h.reads.length, 2); assert.equal(h.posts.length, 1)
    const event = h.trace.find(item => item.type === 'command')
    assert.deepEqual(event.marker, stored(pending('text', post.command.requestId)))
    assert.ok(!JSON.stringify(event.marker).includes('你好')); assert.ok(!JSON.stringify(event.marker).includes(TOKEN))
    assert.deepEqual(Object.keys(event.marker.main).sort(), ['kind', 'petId', 'requestId', 'userId'])
    post.resolve(receipt(post.command.requestId)); await result; assert.equal(h.storage.get(STORAGE).main, null); assert.equal(c.getState().draftText, '')
  },
  async 'text changed while native confirmation is open invalidates token even if later restored'() {
    const h = harness(), c = await h.ready(); c.setDraft('原文'); const token = c.prepare('text'); c.setDraft('新文'); c.setDraft('原文'); await c.confirm(token)
    assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0)
  },
  async 'text changed during fresh GET prevents storage and POST without erasing new draft'() {
    const h = harness(), c = await h.ready(); c.setDraft('原文'); const token = c.prepare('text'), send = c.confirm(token)
    c.setDraft('新文'); h.reads.at(-1).resolve(control()); await send
    assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0); assert.equal(c.getState().draftText, '新文')
  },
  async 'hide selection refresh re-login or different owner during confirmation cannot POST'() {
    for (const change of [h => h.controller.hide(), h => h.change({ selectedPetId: 'pet_2' }), h => h.change({ listLoading: true }), h => h.change({ expiresAt: '2026-11-01T02:00:00Z' }), h => h.change({}, 'user_2')]) {
      const h = harness(), c = await h.ready(); const token = c.prepare('listen'), send = c.confirm(token), read = h.reads.at(-1)
      change(h); read.resolve(control()); await send; assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0)
      assert.equal(c.getState().mainError, ''); assert.equal(c.getState().mainReceipt, null)
    }
  },
  async 'fresh disabled stale review or busy flags safely reject a formerly valid confirmation'() {
    for (const change of [{ enabled: false, verified: false, canListen: false, canInterrupt: false, canSendText: false, reason: '禁用' }, { stale: true, canListen: false, canSendText: false, reason: '过期' }, { manualReviewRequired: true, canListen: false, canSendText: false, reason: '待核对' }, { state: 'thinking', canListen: false, canSendText: false, reason: '忙碌' }]) {
      const h = harness(), c = await h.ready(), token = c.prepare('listen'), send = c.confirm(token)
      h.reads.at(-1).resolve(control(change)); await send; assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0)
    }
  },
  async 'fresh GET failure is clearly not sent rather than manufacturing an unknown marker'() {
    const h = harness(), c = await h.ready(), send = c.confirm(c.prepare('listen'))
    h.reads.at(-1).reject(problem('TIMEOUT')); await send
    assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0); assert.match(c.getState().mainError, /请求未发送.*核验失败/)
  },
  async 'a newer successful control GET cancels the older pending confirmation before storage and POST'() {
    const h = harness(), c = await h.ready(), send = c.confirm(c.prepare('listen')), confirmationRead = h.reads.at(-1), newer = c.refresh()
    h.reads.at(-1).resolve(control({ state: 'thinking', canListen: false, canSendText: false, reason: '设备阶段已更新' })); await newer
    confirmationRead.resolve(control()); await send
    assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0); assert.equal(c.getState().snapshot.state, 'thinking'); assert.match(c.getState().mainError, /请求未发送.*已更新或失效/)
  },
  async 'a newer failed control GET cancels the older pending confirmation without resurrecting permissions'() {
    const h = harness(), c = await h.ready(), send = c.confirm(c.prepare('listen')), confirmationRead = h.reads.at(-1), newer = c.refresh()
    h.reads.at(-1).reject(problem('TIMEOUT')); await newer; confirmationRead.resolve(control()); await send
    assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0); assert.equal(c.getState().snapshot, null); assert.match(c.getState().mainError, /请求未发送.*已更新或失效/)
  },
  async 'superseded preflight failure cannot erase newer GET success failure or polling policy'() {
    for (const fail of [false, true]) {
      const h = harness(), c = await h.ready(), send = c.confirm(c.prepare('listen')), oldRead = h.reads.at(-1), newer = c.refresh()
      if (fail) h.reads.at(-1).reject(problem('TIMEOUT')); else h.reads.at(-1).resolve(control({ state: 'thinking', canListen: false, canSendText: false, reason: '新阶段' }))
      await newer; const previous = c.getState(), count = h.reads.length
      oldRead.reject(problem('TIMEOUT')); await send
      assert.deepEqual(plain(c.getState().snapshot), plain(previous.snapshot)); assert.equal(c.getState().error, previous.error); assert.match(c.getState().mainError, /请求未发送/)
      assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0)
      await h.advance(10000); assert.equal(h.reads.length, count + (fail ? 0 : 1))
    }
  },
  async 'pre-receipt GET success or failure cannot replace acknowledged or unresolved newer context'() {
    for (const status of ['accepted', 'not_sent', 'sending', 'unknown']) for (const fail of [false, true]) {
      const h = harness(), c = await h.ready(), { result, post } = await h.submit(), oldRead = c.refresh()
      post.resolve(receipt(post.command.requestId, 'text', status)); await result
      const previous = c.getState()
      if (fail) h.reads.at(-1).reject(problem('TIMEOUT')); else h.reads.at(-1).resolve(control({ state: 'thinking', canListen: false, canSendText: false, reason: '旧阶段' }))
      await oldRead
      assert.equal(c.getState().snapshot.state, previous.snapshot.state); assert.deepEqual(plain(c.getState().mainReceipt), plain(previous.mainReceipt)); assert.equal(c.getState().mainError, previous.mainError); assert.equal(c.getState().error, '')
      assert.equal(c.getState().mainBlocked, ['sending', 'unknown'].includes(status)); assert.equal(h.posts.length, 1)
    }
  },
  async 'storage read write corruption or failed readback never submits'() {
    for (const scenario of ['read', 'write', 'corrupt', 'readback']) {
      const h = harness(), c = await h.ready(); c.setDraft('敏感问题'); let token = c.prepare('text')
      if (scenario === 'read') { h.failStorageRead(); assert.equal(c.prepare('text'), null); continue }
      if (scenario === 'write') h.failStorageWrite()
      if (scenario === 'corrupt') h.storage.set(STORAGE, { version: 1, main: { ...pending(), content: '不应保存的正文' }, interrupt: null })
      if (scenario === 'readback') { const original = h.wx.setStorageSync; h.wx.setStorageSync = (key, value) => { original(key, value); h.failStorageRead() } }
      const send = c.confirm(token); h.reads.at(-1).resolve(control()); await send
      assert.equal(h.posts.length, 0); assert.ok(c.getState().storageError || /未决|存储/.test(c.getState().mainError))
    }
  },
  async 'unknown keeps original marker only GET can follow never another main POST'() {
    const h = harness(), c = await h.ready(), { result, post } = await h.submit()
    post.reject(problem('TIMEOUT')); await result; assert.equal(c.getState().canCheckMain, true); assert.equal(c.getState().mainBlocked, true)
    assert.equal(c.prepare('listen'), null); assert.equal(c.prepare('text'), null)
    const query = c.checkReceipt(); assert.equal(h.calls.at(-1).name, 'receipt'); h.receipts.at(-1).resolve(receipt(post.command.requestId, 'text', 'unknown')); await query
    assert.equal(h.posts.length, 1); assert.equal(c.getState().mainBlocked, true); assert.equal(c.getState().mainReceipt.status, 'unknown')
  },
  async 'request not found or failed lookup never proves not sent and never clears lock'() {
    const h = harness(), c = await h.ready(), { result, post } = await h.submit('listen')
    post.reject(problem('TIMEOUT')); await result; const query = c.checkReceipt(); h.receipts.at(-1).reject(problem('UNAVAILABLE')); await query
    assert.match(c.getState().mainError, /不证明请求未提交/); assert.equal(c.getState().mainBlocked, true); assert.ok(h.storage.get(STORAGE).main)
  },
  async 'sending can be queried until accepted with no repeat POST and clear only unchanged submitted draft'() {
    const h = harness(), c = await h.ready(), { result, post } = await h.submit()
    post.reject(problem('TIMEOUT')); await result
    let query = c.checkReceipt(); h.receipts.at(-1).resolve(receipt(post.command.requestId, 'text', 'sending')); await query
    assert.equal(c.getState().mainBlocked, true); assert.equal(c.getState().draftText, '你好')
    c.setDraft('新草稿'); query = c.checkReceipt(); h.receipts.at(-1).resolve(receipt(post.command.requestId)); await query
    assert.equal(c.getState().draftText, '新草稿'); assert.equal(c.getState().mainBlocked, false); assert.equal(h.posts.length, 1)
    assert.equal(c.getState().mainError, ''); assert.equal(c.getState().mainReceipt.status, 'accepted')
  },
  async 'not sent terminates marker but does not automatically retry or erase draft'() {
    const h = harness(), c = await h.ready(), { result, post } = await h.submit()
    post.resolve(receipt(post.command.requestId, 'text', 'not_sent')); await result
    assert.equal(c.getState().mainBlocked, false); assert.equal(c.getState().draftText, '你好'); assert.equal(h.posts.length, 1)
  },
  async 'interrupt is independent during a pending main and its acceptance never clears main marker'() {
    const h = harness(), c = await h.ready(), first = await h.submit()
    const token = c.prepare('interrupt'); assert.equal(typeof token, 'string'); const stop = c.confirm(token)
    h.reads.at(-1).resolve(control({ state: 'unavailable', stale: true, manualReviewRequired: true, canListen: false, canSendText: false, reason: '待核对' })); await flush()
    assert.equal(h.posts.length, 2); const second = h.posts[1]; assert.equal(second.command.kind, 'interrupt')
    second.resolve(receipt(second.command.requestId, 'interrupt')); await stop
    assert.ok(h.storage.get(STORAGE).main); assert.equal(h.storage.get(STORAGE).interrupt, null)
    first.post.reject(problem('TIMEOUT')); await first.result; assert.equal(c.getState().mainBlocked, true)
  },
  async 'main acknowledgement while interrupt fresh GET is pending never cancels its explicit independent intent'() {
    const h = harness(), c = await h.ready(), first = await h.submit(), token = c.prepare('interrupt'), stop = c.confirm(token), interruptRead = h.reads.at(-1)
    first.post.resolve(receipt(first.post.command.requestId)); await first.result
    const mainReceipt = c.getState().mainReceipt
    interruptRead.resolve(control({ state: 'answering', canListen: false, canSendText: false, reason: '回答中仍可打断' })); await flush()
    assert.equal(h.posts.length, 2); assert.equal(h.posts[1].command.kind, 'interrupt'); assert.equal(c.confirm(token), stop)
    assert.deepEqual(plain(c.getState().mainReceipt), plain(mainReceipt)); assert.equal(c.getState().mainError, '')
    assert.equal(c.getState().snapshot.state, 'ready'); assert.equal(c.getState().interruptError, '')
    assert.equal(h.storage.get(STORAGE).main, null); assert.equal(h.storage.get(STORAGE).interrupt.requestId, h.posts[1].command.requestId)
    h.posts[1].resolve(receipt(h.posts[1].command.requestId, 'interrupt')); await stop; assert.equal(h.posts.length, 2)
  },
  async 'failed interrupt preflight after a main receipt cannot erase that newer context or pause polling'() {
    for (const status of ['accepted', 'unknown']) {
      const h = harness(), c = await h.ready(), first = await h.submit(), token = c.prepare('interrupt'), stop = c.confirm(token), oldRead = h.reads.at(-1)
      first.post.resolve(receipt(first.post.command.requestId, 'text', status)); await first.result; const previous = c.getState(), count = h.reads.length
      oldRead.reject(problem('TIMEOUT')); await stop
      assert.deepEqual(plain(c.getState().snapshot), plain(previous.snapshot)); assert.deepEqual(plain(c.getState().mainReceipt), plain(previous.mainReceipt)); assert.equal(c.getState().mainError, previous.mainError); assert.equal(c.getState().error, previous.error)
      assert.match(c.getState().interruptError, /请求未发送/); assert.equal(h.posts.length, 1); assert.equal(h.storage.get(STORAGE).interrupt, null)
      assert.equal(Boolean(h.storage.get(STORAGE).main), status === 'unknown'); await h.advance(10000); assert.equal(h.reads.length, count + 1)
    }
  },
  async 'unknown independent interrupt retains both bounded channels without overwriting requests'() {
    const h = harness(), c = await h.ready(), first = await h.submit('listen'); first.post.reject(problem('TIMEOUT')); await first.result
    const second = await h.submit('interrupt'); second.post.resolve(receipt(second.post.command.requestId, 'interrupt', 'unknown')); await second.result
    const saved = h.storage.get(STORAGE); assert.equal(saved.main.requestId, first.post.command.requestId); assert.equal(saved.interrupt.requestId, second.post.command.requestId)
    assert.equal(Object.keys(saved).length, 3); assert.equal(c.prepare('interrupt'), null); assert.equal(h.posts.length, 2)
  },
  async 'unresolved marker survives controller recreation app recreation and same-user re-login'() {
    const h = harness(), c = await h.ready(), { result, post } = await h.submit(); post.reject(problem('TIMEOUT')); await result; c.hide()
    const replacement = h.makeController(); h.show(replacement); h.reads.at(-1).resolve(control()); await flush()
    assert.equal(replacement.prepare('listen'), null); assert.equal(replacement.getState().canCheckMain, true)
    const app = harness(h.storage), again = await app.ready(); app.change({ expiresAt: '2026-11-01T02:00:00Z' }); app.reads.at(-1).resolve(control()); await flush()
    assert.equal(again.prepare('text'), null); assert.equal(again.getState().canCheckMain, true); assert.equal(app.posts.length, 0)
  },
  async 'other user or device never sees old receipt id but cannot overwrite unresolved same-channel marker'() {
    const h = harness(); h.storage.set(STORAGE, stored(pending())); const c = await h.ready()
    h.change({}, 'user_2'); h.reads.at(-1).resolve(control()); await flush()
    assert.equal(c.getState().canCheckMain, false); assert.equal(c.getState().mainReceipt, null); assert.equal(c.prepare('listen'), null)
    assert.ok(!JSON.stringify(c.getState()).includes('old_1')); assert.ok(!JSON.stringify(c.getState()).includes('user_1')); assert.equal(h.storage.get(STORAGE).main.userId, 'user_1')
  },
  async 'accepted receipt cannot describe a later unconfirmed command on either channel'() {
    for (const kind of ['listen', 'interrupt']) {
      const h = harness(), c = await h.ready(), first = await h.submit(kind), lane = kind === 'interrupt' ? 'interrupt' : 'main', other = lane === 'main' ? 'interrupt' : 'main'
      first.post.resolve(receipt(first.post.command.requestId, kind)); await first.result
      assert.equal(c.getState()[lane + 'Receipt'].status, 'accepted'); assert.equal(c.getState()[lane + 'Error'], '')
      const firstReceipt = c.getState()[lane + 'Receipt'], second = await h.submit(kind)
      assert.equal(c.getState()[lane + 'Receipt'], null); assert.equal(c.getState()[other + 'Receipt'], null)
      second.post.reject(problem('TIMEOUT')); await second.result
      assert.equal(c.getState()[lane + 'Receipt'], null); assert.match(c.getState()[lane + 'Error'], /尚未确认/)
      assert.equal(h.storage.get(STORAGE)[lane].requestId, second.post.command.requestId)
      assert.notEqual(second.post.command.requestId, firstReceipt.requestId)
    }
  },
  async 'foreign unresolved channels expose only fixed actionable blockers and preserve both markers'() {
    const h = harness(); h.storage.set(STORAGE, stored(pending('text', 'private_old_main'), pending('interrupt', 'private_old_interrupt')))
    const c = await h.ready(); h.change({}, 'user_2'); h.reads.at(-1).resolve(control()); await flush()
    const snapshot = c.getState()
    assert.equal(snapshot.mainBlocked, true); assert.equal(snapshot.interruptBlocked, true)
    assert.equal(snapshot.canCheckMain, false); assert.equal(snapshot.canCheckInterrupt, false)
    assert.match(snapshot.mainError, /另一个身份或设备.*不能清数据绕过/); assert.equal(snapshot.mainError, snapshot.interruptError)
    assert.ok(!JSON.stringify(snapshot).includes('private_old_')); assert.ok(!JSON.stringify(snapshot).includes('user_1'))
    assert.equal(c.prepare('listen'), null); assert.equal(c.prepare('interrupt'), null); assert.equal(h.posts.length, 0); assert.equal(h.storageWrites(), 0)
    assert.equal(h.storage.get(STORAGE).main.requestId, 'private_old_main'); assert.equal(h.storage.get(STORAGE).interrupt.requestId, 'private_old_interrupt')
  },
  async 'hidden accepted response never clears marker and a restored explicit receipt query can reconcile'() {
    const h = harness(), c = await h.ready(), { result, post } = await h.submit(), count = h.values.length
    c.hide(); post.resolve(receipt(post.command.requestId)); await result; assert.equal(h.values.length, count); assert.ok(h.storage.get(STORAGE).main)
    h.show(); h.reads.at(-1).resolve(control()); await flush(); const query = c.checkReceipt(); h.receipts.at(-1).resolve(receipt(post.command.requestId)); await query
    assert.equal(c.getState().mainBlocked, false)
  },
  async 'mismatched or malformed acknowledgement cannot clear persistent marker or draft'() {
    for (const response of [receipt('wrong'), receipt('dialog_1', 'listen'), { ...receipt(), status: 'completed' }]) {
      const h = harness(), c = await h.ready(), { result, post } = await h.submit(); post.resolve(response); await result
      assert.equal(c.getState().mainBlocked, true); assert.equal(c.getState().draftText, '你好'); assert.equal(c.getState().mainReceipt, null)
    }
  },
  async 'control polling is serial stops after failure and never polls receipt or retries POST automatically'() {
    const h = harness(), c = await h.ready(); await h.advance(10000); assert.equal(h.reads.length, 2)
    await h.advance(30000); assert.equal(h.reads.length, 2); h.reads.at(-1).reject(problem('TIMEOUT')); await flush(); await h.advance(60000)
    assert.equal(h.reads.length, 2); assert.equal(h.posts.length, 0); assert.equal(h.receipts.length, 0); assert.match(c.getState().error, /自动读取已暂停/)
  },
  async 'stored marker shape and global bounds reject credential content overflow aliases and duplicates'() {
    for (const value of [null, [], { ...stored(), secret: TOKEN }, { version: 2, main: null, interrupt: null }, stored({ ...pending(), content: '秘密正文' }), stored({ ...pending(), kind: 'interrupt' }), stored(pending(), pending('interrupt')), stored({ ...pending(), userId: '../bad' }), stored({ ...pending(), requestId: 'old_1\n' }), stored({ ...pending(), userId: 'user_1\n' }), stored({ ...pending(), petId: 'pet_1\n' })]) {
      const h = harness(); h.storage.set(STORAGE, value); const c = await h.ready(); assert.ok(c.getState().storageError); assert.equal(c.prepare('listen'), null); assert.equal(h.posts.length, 0)
      assert.match(c.getState().mainError, /存储不可用.*禁止发送/); assert.equal(c.getState().mainError, c.getState().interruptError)
    }
  }
}

;(async () => { for (const [name, run] of Object.entries(cases)) { await run(); console.log('PASS ' + name) }; console.log('Live dialog control smoke passed: ' + Object.keys(cases).length) })().catch(error => { console.error(error); process.exitCode = 1 })
