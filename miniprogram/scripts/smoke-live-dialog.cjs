// Strictly offline: fake wx, session, clock and transcript fixtures only.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..')
const NOW = Date.parse('2026-11-01T00:00:00Z'), UTC = '2026-11-01T00:00:00Z'
const TOKEN = 'synthetic-private-dialog-token-never-stored'
const ID = 'conv_' + 'a'.repeat(48), ID2 = 'conv_' + 'b'.repeat(48), REV = '1'.repeat(64), REV2 = '2'.repeat(64)
const plain = value => JSON.parse(JSON.stringify(value))
const flush = async () => { for (let i = 0; i < 45; i++) await Promise.resolve() }
const deferred = () => { let resolve, reject; const promise = new Promise((yes, no) => { resolve = yes; reject = no }); return { promise, resolve, reject } }
const problem = code => Object.assign(new Error('private upstream details'), { code })
const status = () => ({ state: 'offline', sessionActive: false, audioPlaying: null, stale: false, updatedAt: null, sequence: 0, followUpDeadlineAt: null, revision: REV, sampledAt: UTC, readOnly: true })
const summary = (conversationId = ID) => ({ conversationId, status: 'complete', source: 'device', startedAt: UTC, updatedAt: UTC, durationSeconds: 0, messageCount: 2, userMessageCount: 1, assistantMessageCount: 1, preview: '你好', assistantPreview: '你好呀' })
const conversations = () => ({ items: [summary()], revision: REV, limit: 30, scope: 'persisted_only', unchanged: false, sampledAt: UTC })
const messages = () => [{ messageId: 'msg_' + 'a'.repeat(48), role: 'user', content: '你好\n第二行\t文字', createdAt: UTC }, { messageId: 'msg_' + 'b'.repeat(48), role: 'assistant', content: '你好呀', createdAt: UTC }]
const conversation = (id = ID) => ({ conversation: summary(id), messages: messages(), revision: REV, limit: 100, windowMessageCount: 2, truncated: false, scope: 'persisted_only', unchanged: false, sampledAt: UTC })
const device = () => ({ readOnly: true, sampledAt: UTC, health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' }, device: { connected: true, deviceSerial: '7c2****a128', networkName: null, batteryPercent: 66, batteryState: 'charging', batteryVoltage: 4.1, chargerOnline: true, batteryPresent: true, lastSeenAt: null }, dialog: { state: 'offline', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false } })

function harness() {
  let now = NOW, sequence = 0, storageCalls = 0
  const timers = new Map(), modules = new Map(), requests = [], logins = [], logs = []
  class ClockDate extends Date { constructor(...args) { if (args.length) super(...args); else super(now) } static now() { return now } }
  const wx = { login(value) { logins.push(value) }, request(value) { requests.push(value); return { abort() { value.fail({ errMsg: 'aborted private-token' }) } } }, getStorageSync() { storageCalls++; throw Error('No storage') }, setStorageSync() { storageCalls++; throw Error('No storage') } }
  const context = vm.createContext({ wx, Date: ClockDate,
    setTimeout(fn, delay) { const id = ++sequence; timers.set(id, { fn, at: now + Math.max(1, delay) }); return id }, clearTimeout(id) { timers.delete(id) },
    setInterval() { throw Error('Dialog must use serial polling') }, clearInterval() {}, console: Object.fromEntries(['log', 'warn', 'error', 'info', 'debug'].map(name => [name, (...args) => logs.push(args)])) })
  function load(relative, parent = root) {
    let filename = path.resolve(parent, relative)
    if (!filename.endsWith('.ts')) filename += '.ts'
    if (modules.has(filename)) return modules.get(filename).exports
    const module = { exports: {} }; modules.set(filename, module)
    const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 } }).outputText
    new vm.Script('(function(require,module,exports){' + output + '\n})', { filename }).runInContext(context)(name => load(name, path.dirname(filename)), module, module.exports)
    return module.exports
  }
  const api = load('services/live-dialog')
  const h = { api, wx, load, requests, logins, timers, logs,
    success(value, index = requests.length - 1, statusCode = 200) { requests[index].success({ statusCode, data: { success: true, data: value } }) },
    failure(index = requests.length - 1) { requests[index].fail({ errMsg: 'private-token AppSecret' }) }, storageCalls: () => storageCalls,
    async authenticate() {
      this.session = load('services/live-device-session').getLiveDeviceSession(); this.anchor = this.session.attach(() => {})
      const ready = this.session.enable(); this.success({ mode: 'local-development', wechatConfigured: true, readOnly: true }); await ready
      const login = this.session.login(); logins.at(-1).success({ code: 'synthetic-code' }); await flush()
      this.success({ accessToken: TOKEN, userId: 'user_1', expiresAt: '2026-11-01T01:00:00Z' }); await flush()
      this.success([{ petId: 'pet_1', name: '小安' }, { petId: 'pet_2', name: '另一只猫' }]); await login
      const select = this.session.selectPet('pet_1'); this.success(device()); await select
    },
    controller(options, signedIn = true) {
      this.values = []; this.calls = []; this.pendingStatus = []; this.pendingLists = []; this.pendingDetails = []
      this.identity = { mode: signedIn ? 'live' : 'preview', signedIn, expiresAt: '2026-11-01T01:00:00Z', pets: [{ petId: 'pet_1' }, { petId: 'pet_2' }], selectedPetId: signedIn ? 'pet_1' : '', loading: false, listLoading: false }
      const listeners = new Set(), fake = { getState: () => plain(this.identity), attach(fn) { listeners.add(fn); fn(); return () => listeners.delete(fn) } }
      for (const [name, target] of [['readDialogStatus', this.pendingStatus], ['readDialogConversations', this.pendingLists], ['readDialogConversation', this.pendingDetails]]) fake[name] = (...args) => { this.calls.push({ name, args }); const next = deferred(); target.push(next); return next.promise }
      this.setIdentity = patch => { Object.assign(this.identity, patch); for (const fn of [...listeners]) fn() }
      this.dialog = api.createLiveDialog(fake, options); this.show = () => this.dialog.show(value => this.values.push(value)); this.show(); return this.dialog
    },
    async finishMain(nextStatus = status(), nextList = conversations()) { this.pendingStatus.at(-1).resolve(nextStatus); this.pendingLists.at(-1).resolve(nextList); await flush() },
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
  async 'native processing and playback-drain phases remain truthful instead of breaking status reads'() {
    const h = harness()
    for (const state of ['processing_audio', 'followup_preparing', 'connected', 'proactive_queued', 'proactive_speaking', 'proactive_finishing', 'stopping']) {
      const result = h.api.verifyDialogStatus({ ...status(), state, sessionActive: true })
      assert.equal(result.state, state)
      assert.equal(result.sessionActive, true)
      assert.equal(result.readOnly, true)
    }
    assert.equal(h.requests.length, 0)
  },
  async 'strict status preserves truthful nullable times and drops arbitrary message or process details'() {
    const h = harness(), value = h.api.verifyDialogStatus({ ...status(), message: '/private/path', process: TOKEN, elapsedSeconds: 999 })
    assert.equal(value.updatedAt, null); assert.equal(value.followUpDeadlineAt, null)
    assert.equal(value.audioPlaying, null); assert.equal(h.api.verifyDialogStatus({ ...status(), audioPlaying: true }).audioPlaying, true)
    assert.ok(!('elapsedSeconds' in value)); assert.ok(!('message' in value)); assert.ok(!JSON.stringify(value).includes(TOKEN))
    for (const bad of [{ ...status(), readOnly: false }, { ...status(), audioPlaying: 'false' }, { ...status(), audioPlaying: undefined }, { ...status(), state: 'invented' }, { ...status(), sequence: '0' }, { ...status(), sequence: true }, { ...status(), sequence: Infinity }, { ...status(), updatedAt: '2026-02-30T00:00:00Z' }, { ...status(), revision: 'raw-private-revision' }]) assert.throws(() => h.api.verifyDialogStatus(bad), /UNVERIFIED/)
  },
  async 'summary boundary is finite public persisted data with distinct mock source'() {
    const h = harness(), sample = conversations(); sample.items[0].source = 'mock'; sample.items[0].rawOwner = TOKEN
    const result = h.api.verifyDialogConversations(sample)
    assert.equal(result.items[0].source, 'mock'); assert.equal(result.scope, 'persisted_only'); assert.ok(!JSON.stringify(result).includes(TOKEN))
    for (const bad of [{ ...conversations(), items: Array.from({ length: 31 }, summary) }, { ...conversations(), items: [summary(), summary()] }, { ...conversations(), items: [{ ...summary(), conversationId: 'raw-id' }] }, { ...conversations(), items: [{ ...summary(), messageCount: 3 }] }, { ...conversations(), items: [{ ...summary(), preview: '\u202econcealed' }] }, { ...conversations(), items: [{ ...summary(), durationSeconds: -1 }] }, { ...conversations(), scope: 'all' }, { ...conversations(), unchanged: true }]) assert.throws(() => h.api.verifyDialogConversations(bad), /UNVERIFIED/)
  },
  async 'detail keeps long legitimate multiline content without leaking raw IDs or voice metadata'() {
    const h = harness(), sample = conversation(); sample.messages[0].content = '长'.repeat(8192); sample.messages[1].voiceId = TOKEN
    const result = h.api.verifyDialogConversation(sample)
    assert.equal(result.messages[0].content.length, 8192); assert.ok(!('voiceId' in result.messages[1]))
    const emoji = conversation(); emoji.messages[0].content = '😀'.repeat(8192)
    assert.equal(h.api.verifyDialogConversation(emoji).messages[0].content, emoji.messages[0].content)
    assert.equal(h.api.verifyDialogConversation(conversation()).messages[0].content, messages()[0].content)
    for (const bad of [{ ...conversation(), messages: [] }, { ...conversation(), messages: [messages()[0], messages()[0]] }, { ...conversation(), messages: [{ ...messages()[0], content: 'x'.repeat(8193) }] }, { ...conversation(), messages: [{ ...messages()[0], role: 'system' }] }, { ...conversation(), messages: [{ ...messages()[0], content: '\u0000bad' }] }, { ...conversation(), messages: [{ ...messages()[0], content: 'bad\rnewline' }] }, { ...conversation(), messages: [{ ...messages()[0], createdAt: 'not-time' }] }, { ...conversation(), truncated: true }, { ...conversation(), unchanged: true }]) assert.throws(() => h.api.verifyDialogConversation(bad), /UNVERIFIED/)
  },
  async 'conditional and truncated windows are explicit never fabricated full history'() {
    const h = harness()
    const unchangedList = h.api.verifyDialogConversations({ ...conversations(), items: [], unchanged: true })
    const unchangedDetail = h.api.verifyDialogConversation({ ...conversation(), messages: [], unchanged: true })
    assert.equal(unchangedList.items.length, 0); assert.equal(unchangedDetail.messages.length, 0)
    const window = conversation(); window.conversation.messageCount = 120; window.conversation.userMessageCount = 60; window.conversation.assistantMessageCount = 60; window.truncated = true; window.windowMessageCount = 100
    window.messages = Array.from({ length: 100 }, (_, i) => ({ ...messages()[i % 2], messageId: 'msg_' + i.toString(16).padStart(48, '0') }))
    assert.equal(h.api.verifyDialogConversation(window).truncated, true)
  },
  async 'multibyte windows enforce exact UTF8 budget and reject lone surrogates without runtime polyfills'() {
    const h = harness(), sample = conversation()
    sample.conversation.messageCount = 100; sample.conversation.userMessageCount = 50; sample.conversation.assistantMessageCount = 50; sample.truncated = true; sample.windowMessageCount = 8
    sample.messages = Array.from({ length: 8 }, (_, i) => ({ ...messages()[0], messageId: 'msg_' + i.toString(16).padStart(48, '0'), content: '中'.repeat(8192) }))
    assert.equal(h.api.verifyDialogConversation(sample).windowMessageCount, 8)
    const tooBig = plain(sample); tooBig.windowMessageCount = 9; tooBig.messages.push({ ...messages()[0], messageId: 'msg_' + 'f'.repeat(48), content: '中'.repeat(8192) })
    assert.throws(() => h.api.verifyDialogConversation(tooBig), /UNVERIFIED/)
    const escaped = plain(sample); escaped.windowMessageCount = 24; escaped.messages = Array.from({ length: 24 }, (_, i) => ({ ...messages()[0], messageId: 'msg_' + i.toString(16).padStart(48, '0'), content: '\t'.repeat(8192) }))
    assert.throws(() => h.api.verifyDialogConversation(escaped), /UNVERIFIED/)
    for (const content of ['\ud800', '\udc00']) assert.throws(() => h.api.verifyDialogConversation({ ...conversation(), messages: [{ ...messages()[0], content }, messages()[1]] }), /UNVERIFIED/)
  },
  async 'client rejects malformed IDs revisions or paths before login and request'() {
    const h = harness(), client = h.load('services/cloud-auth-client').createCloudAuthClient(h.wx)
    await assert.rejects(client.readDialogStatus('../bad'), error => error.code === 'INVALID_ID')
    await assert.rejects(client.readDialogConversations('pet_1', 'secret'), error => error.code === 'INVALID_ID')
    await assert.rejects(client.readDialogConversation('pet_1', '../raw'), error => error.code === 'INVALID_ID')
    await assert.rejects(client.readDialogConversation('pet_1', ID, false), error => error.code === 'INVALID_ID')
    assert.equal(h.requests.length, 0); assert.equal(h.logins.length, 0)
  },
  async 'authenticated read methods use fixed GET paths and five second deadline only'() {
    const h = harness(); await h.authenticate()
    for (const [name, args, route, data] of [['readDialogStatus', [], '/dialog', status()], ['readDialogConversations', [], '/dialog/conversations', conversations()], ['readDialogConversations', [REV], '/dialog/conversations/since/' + REV, { ...conversations(), items: [], unchanged: true }], ['readDialogConversation', [ID], '/dialog/conversations/' + ID, conversation()], ['readDialogConversation', [ID, REV], '/dialog/conversations/' + ID + '/since/' + REV, { ...conversation(), messages: [], unchanged: true }]]) {
      const read = h.session[name](...args), req = h.requests.at(-1)
      assert.equal(req.url, 'http://127.0.0.1:8787/v1/pets/pet_1' + route); assert.equal(req.method, 'GET'); assert.equal(req.timeout, 5000)
      assert.equal(req.header.Authorization, 'Bearer ' + TOKEN); assert.ok(!('data' in req)); assert.ok(!req.url.includes('?'))
      h.success(data); await read
    }
    assert.equal(h.requests.filter(req => req.method === 'POST').length, 1); assert.equal(h.storageCalls(), 0); assert.deepEqual(h.logs, [])
  },
  async 'unrelated conditional revision or conversation response is never accepted'() {
    const h = harness(); await h.authenticate()
    for (const [name, args, data] of [['readDialogConversations', [], { ...conversations(), items: [], unchanged: true }], ['readDialogConversations', [REV2], { ...conversations(), items: [], unchanged: true }], ['readDialogConversation', [ID], conversation(ID2)], ['readDialogConversation', [ID, REV2], { ...conversation(), messages: [], unchanged: true }]]) {
      const read = h.session[name](...args), rejected = assert.rejects(read, error => error.code === 'UNVERIFIED'); h.success(data); await rejected
    }
  },
  async 'shared authorization recheck rejects transcripts and device change cancels late private response'() {
    const h = harness(); await h.authenticate(); const read = h.session.readDialogConversation(ID), rejected = assert.rejects(read, error => error.code === 'CANCELLED'), req = h.requests.at(-1)
    const select = h.session.selectPet('pet_2'); h.success(device()); await select; req.success({ statusCode: 200, data: { success: true, data: conversation() } }); await rejected
    const list = h.session.refreshPets(); await assert.rejects(h.session.readDialogConversations(), error => error.code === 'AUTH_REQUIRED')
    h.success([{ petId: 'pet_2', name: '另一只猫' }]); await flush(); h.success(device()); await list
  },
  async '401 on transcript read resets shared identity not just the page copy'() {
    const h = harness(); await h.authenticate(); const read = h.session.readDialogStatus(), rejected = assert.rejects(read, error => error.code === 'UNAUTHORIZED')
    h.success({}, undefined, 401); await rejected; assert.equal(h.session.getState().signedIn, false)
  },
  async 'preview and no identity never read real transcripts or automatically log in'() {
    const h = harness(), controller = h.controller(undefined, false)
    await controller.refresh(); await controller.loadConversation(ID); await h.advance(60000)
    assert.equal(h.calls.length, 0); assert.equal(h.requests.length, 0); assert.equal(h.logins.length, 0)
    assert.equal(controller.getState().conversations, null)
  },
  async 'main status polls five seconds while summaries stay at least ten seconds and reads do not overlap'() {
    const h = harness(), controller = h.controller(), first = controller.refresh(), second = controller.refresh()
    assert.equal(first, second); assert.deepEqual(h.calls.map(call => call.name), ['readDialogStatus', 'readDialogConversations'])
    await h.advance(15000); assert.equal(h.calls.length, 2)
    await h.finishMain(); await first; await h.advance(4999); assert.equal(h.calls.length, 2)
    await h.advance(1); assert.equal(h.calls.length, 3); assert.equal(h.calls.at(-1).name, 'readDialogStatus')
    h.pendingStatus.at(-1).resolve(status()); await flush(); await h.advance(5000)
    assert.equal(h.calls.length, 5); assert.deepEqual(h.calls.at(-1).args, [REV])
  },
  async 'conditional list keeps verified existing items only for same revision and identity'() {
    const h = harness(), controller = h.controller(); await h.finishMain()
    const refresh = controller.refresh(); await h.finishMain(status(), { ...conversations(), items: [], unchanged: true }); await refresh
    assert.equal(controller.getState().conversations.items.length, 1)
    const bad = controller.refresh(); await h.finishMain(status(), { ...conversations(), items: [], unchanged: true, revision: REV2 }); await bad
    assert.equal(controller.getState().conversations, null); assert.match(controller.getState().conversationsError, /纯只读/)
  },
  async 'failed status and summary clear stale readings independently with no Mock fallback'() {
    const h = harness(), controller = h.controller(); await h.finishMain()
    const refresh = controller.refresh(); h.pendingStatus.at(-1).reject(problem('UNAVAILABLE')); h.pendingLists.at(-1).resolve(conversations()); await refresh
    assert.equal(controller.getState().snapshot, null); assert.ok(controller.getState().conversations); assert.match(controller.getState().error, /状态暂不可用/)
    const again = controller.refresh(); h.pendingStatus.at(-1).resolve(status()); h.pendingLists.at(-1).reject(problem('UNAVAILABLE')); await again
    assert.ok(controller.getState().snapshot); assert.equal(controller.getState().conversations, null)
  },
  async 'hide discards all transcript callbacks and stops polling without writing or controlling'() {
    const h = harness(), controller = h.controller(), count = h.values.length
    controller.hide(); await h.finishMain(); await h.advance(60000)
    assert.equal(h.values.length, count); assert.equal(h.calls.length, 2); assert.equal(controller.getState().snapshot, null); assert.equal(controller.getState().conversations, null)
  },
  async 'authorization refresh clears cached text immediately and sends full read after revalidation'() {
    const h = harness(), controller = h.controller(); await h.finishMain()
    h.setIdentity({ listLoading: true }); assert.equal(controller.getState().conversations, null); await controller.refresh(); await h.advance(60000); assert.equal(h.calls.length, 2)
    h.setIdentity({ listLoading: false }); assert.equal(h.calls.length, 4); assert.deepEqual(h.calls.at(-1).args, [undefined])
  },
  async 'new device discards old list and voice responses before displaying new identity'() {
    const h = harness(), controller = h.controller(); h.setIdentity({ selectedPetId: 'pet_2' }); assert.equal(h.calls.length, 4)
    h.pendingStatus[0].resolve({ ...status(), state: 'thinking' }); h.pendingLists[0].resolve(conversations()); await flush()
    assert.equal(controller.getState().snapshot, null); assert.equal(controller.getState().conversations, null)
    h.pendingStatus[1].resolve(status()); h.pendingLists[1].resolve({ ...conversations(), items: [summary(ID2)] }); await flush()
    assert.equal(controller.getState().conversations.items[0].conversationId, ID2)
  },
  async 'standalone detail reads only requested opaque conversation no status or list'() {
    const h = harness(), controller = h.controller({ view: 'conversation', conversationId: ID, petId: 'pet_1' })
    assert.deepEqual(h.calls, [{ name: 'readDialogConversation', args: [ID, undefined] }])
    const first = controller.refresh(), second = controller.refresh(); assert.equal(first, second)
    h.pendingDetails[0].resolve(conversation()); await first; assert.equal(controller.getState().conversation.messages.length, 2)
    await h.advance(10000); assert.deepEqual(h.calls.at(-1), { name: 'readDialogConversation', args: [ID, REV] })
  },
  async 'detail conditional keeps messages unchanged but changed revision replaces never appends'() {
    const h = harness(), controller = h.controller({ view: 'conversation', conversationId: ID, petId: 'pet_1' }); h.pendingDetails[0].resolve(conversation()); await flush()
    const same = controller.refresh(); h.pendingDetails[1].resolve({ ...conversation(), messages: [], unchanged: true }); await same
    assert.equal(controller.getState().conversation.messages.length, 2)
    const changed = controller.refresh(), data = conversation(); data.revision = REV2; data.messages[1].content = '修订后的回答'
    h.pendingDetails[2].resolve(data); await changed
    assert.equal(controller.getState().conversation.messages.length, 2); assert.equal(controller.getState().conversation.messages[1].content, '修订后的回答')
  },
  async 'detail error clears text and rejects old mapping or unrelated conversation without fallback'() {
    const h = harness(), controller = h.controller({ view: 'conversation', conversationId: ID, petId: 'pet_1' }); h.pendingDetails[0].resolve(conversation()); await flush()
    const wrong = controller.refresh(); h.pendingDetails[1].resolve(conversation(ID2)); await wrong
    assert.equal(controller.getState().conversation, null); assert.match(controller.getState().detailsError, /返回会话列表/)
    const refresh = controller.refresh(); h.pendingDetails[2].reject(problem('UNAVAILABLE')); await refresh
    assert.equal(controller.getState().conversation, null); assert.deepEqual(h.calls.at(-1).args, [ID, undefined])
  },
  async 'unchanged detail cannot silently resize or relabel cached messages'() {
    const h = harness(), controller = h.controller({ view: 'conversation', conversationId: ID, petId: 'pet_1' }); h.pendingDetails[0].resolve(conversation()); await flush()
    const read = controller.refresh(), bad = { ...conversation(), messages: [], unchanged: true, windowMessageCount: 1, truncated: true }
    h.pendingDetails[1].resolve(bad); await read; assert.equal(controller.getState().conversation, null)
  },
  async 'detail pinned pet prevents cross-device same-id read or delayed response display'() {
    const h = harness(), controller = h.controller({ view: 'conversation', conversationId: ID, petId: 'pet_1' })
    h.setIdentity({ selectedPetId: 'pet_2' }); h.pendingDetails[0].resolve(conversation()); await flush(); await controller.refresh(); await h.advance(60000)
    assert.equal(h.calls.length, 1); assert.equal(controller.getState().conversation, null); assert.match(controller.getState().detailsError, /设备选择已变化/)
  },
  async 'hidden detail never exposes late content and reentry has no cached private transcript'() {
    const h = harness(), controller = h.controller({ view: 'conversation', conversationId: ID, petId: 'pet_1' }), count = h.values.length
    controller.hide(); h.pendingDetails[0].resolve(conversation()); await flush(); assert.equal(h.values.length, count)
    h.show(); assert.equal(controller.getState().conversation, null); assert.deepEqual(h.calls.at(-1).args, [ID, undefined])
  },
  async 'unsupported or unavailable lanes pause automatic retries until explicit refresh'() {
    const h = harness(), controller = h.controller()
    h.pendingStatus[0].reject(problem('UNAVAILABLE')); h.pendingLists[0].reject(problem('UNAVAILABLE')); await flush(); await h.advance(60000)
    assert.equal(h.calls.length, 2); assert.match(controller.getState().conversationsError, /自动读取已暂停/)
    const refresh = controller.refresh(); assert.equal(h.calls.length, 4); await h.finishMain(); await refresh
    const detailHarness = harness(), details = detailHarness.controller({ view: 'conversation', conversationId: ID, petId: 'pet_1' })
    detailHarness.pendingDetails[0].reject(problem('UNAVAILABLE')); await flush(); await detailHarness.advance(60000)
    assert.equal(detailHarness.calls.length, 1); const retry = details.refresh(); assert.equal(detailHarness.calls.length, 2)
    detailHarness.pendingDetails[1].resolve(conversation()); await retry
  },
  async 'main detail helper accepts only a visible current authorized summary'() {
    const h = harness(), controller = h.controller(); await controller.loadConversation(ID); assert.equal(h.pendingDetails.length, 0)
    await h.finishMain(); await controller.loadConversation(ID2); assert.equal(h.pendingDetails.length, 0)
    const read = controller.loadConversation(ID); h.pendingDetails[0].resolve(conversation()); await read
    assert.equal(controller.getState().conversation.conversation.conversationId, ID)
  }
}

;(async () => { for (const [name, run] of Object.entries(cases)) { await run(); console.log('PASS ' + name) }; console.log('Live dialog smoke passed: ' + Object.keys(cases).length) })().catch(error => { console.error(error); process.exitCode = 1 })
