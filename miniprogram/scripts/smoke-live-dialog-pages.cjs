// Real TS pages/shared session/read and control clients, with offline wx I/O.
// This script never connects to a real device, persists transcripts or sends real commands.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..'), UTC = '2026-11-01T00:00:00Z'
const CONVERSATION_ID = 'conv_' + 'a'.repeat(48), OTHER_ID = 'conv_' + 'b'.repeat(48)
const plain = value => JSON.parse(JSON.stringify(value))
const flush = async () => { for (let i = 0; i < 70; i++) await Promise.resolve() }
const summary = () => ({ conversationId: CONVERSATION_ID, status: 'complete', source: 'device', startedAt: UTC, updatedAt: UTC, durationSeconds: 0, messageCount: 2, userMessageCount: 1, assistantMessageCount: 1, preview: '真实用户摘要', assistantPreview: '真实回答摘要' })
const dialog = () => ({ state: 'waiting_wake', sessionActive: false, audioPlaying: false, stale: false, updatedAt: UTC, sequence: 2, followUpDeadlineAt: null, revision: 'c'.repeat(64), sampledAt: UTC, readOnly: true })
const conversations = () => ({ items: [summary()], revision: 'd'.repeat(64), limit: 30, scope: 'persisted_only', unchanged: false, sampledAt: UTC })
const conversation = () => ({ conversation: summary(), messages: [{ messageId: 'msg_' + '1'.repeat(48), role: 'user', content: '真实问题完整正文', createdAt: UTC }, { messageId: 'msg_' + '2'.repeat(48), role: 'assistant', content: '真实回答\n保留换行', createdAt: UTC }], revision: 'e'.repeat(64), limit: 100, windowMessageCount: 2, truncated: false, scope: 'persisted_only', unchanged: false, sampledAt: UTC })
const control = (enabled = false) => ({ enabled, verified: true, canListen: enabled, canInterrupt: enabled, canSendText: enabled, manualReviewRequired: false, reason: enabled ? '需要逐次确认' : '真实控制尚未开放', state: 'ready', stale: false, sampledAt: UTC })
const commandReceipt = (request, status = 'accepted') => ({ requestId: request.data.requestId, kind: request.data.kind, status, updatedAt: UTC })
const device = () => ({ readOnly: true, sampledAt: UTC, health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' }, device: { connected: true, deviceSerial: '7c2****a128', networkName: null, batteryPercent: 66, batteryState: 'discharging', batteryVoltage: 3.7, chargerOnline: false, batteryPresent: true, lastSeenAt: null }, dialog: { state: 'offline', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false } })

function harness(controlEnabled = false, options = {}) {
  const requests = [], logins = [], writes = [], calls = [], navigation = [], logs = [], modals = [], modules = new Map(), timers = new Map(), privateStorage = new Map()
  let timerId = 0, now = Date.parse(UTC), storageWrites = 0, stopped = 0
  const previewPet = { petId: 'preview_pet', deviceId: 'preview_device', name: '演示小安', personalityName: '演示性格', avatarUrl: '/assets/ai-cat-avatar.png' }
  const previewRecord = { conversationId: 'preview_conv', status: 'complete', startedAt: UTC, userPreview: '演示用户摘要', assistantPreview: '演示回答摘要', messageCount: 2 }
  const service = {
    getBoundPet: async () => previewPet,
    getDialogStatus: async () => ({ state: 'ready', elapsedSeconds: 0, followUpRemaining: 0, sessionActive: false, canInterrupt: false }),
    listConversations: async () => ({ items: [previewRecord], syncText: '演示已同步' }),
    getConversation: async () => ({ ...previewRecord, messages: [{ messageId: 'preview_msg', role: 'user', content: '演示正文', createdAt: UTC }] }),
    startListening: async () => {}, interruptDialog: async () => {}, sendText: async () => {}
  }
  for (const name of Object.keys(service)) { const method = service[name]; service[name] = (...args) => { calls.push({ name, args }); return method(...args) } }
  const wx = { login(value) { logins.push(value) }, request(value) { requests.push(value); return { abort() { if (!value.done) { value.done = true; value.fail({ errMsg: 'aborted raw-private' }) } } } },
    getStorageSync(key) { return key === 'aiCatSession' ? { userId: 'preview_user' } : privateStorage.get(key) }, setStorageSync(key, value) { storageWrites++; privateStorage.set(key, plain(value)) }, removeStorageSync(key) { privateStorage.delete(key) }, setStorage() { storageWrites++ },
    showToast() {}, showModal(value) {
      if (options.throwModal) throw Error('raw-private-native-error')
      if (options.strictModal && [value.confirmText, value.cancelText].some(text => text !== undefined && Array.from(text).length > 4)) {
        value.fail({ errMsg: 'showModal:fail confirmText length should not exceed 4 characters' }); return
      }
      modals.push(value)
    }, stopPullDownRefresh() { stopped++ }, navigateTo(value) { navigation.push({ method: 'to', ...value }) }, redirectTo(value) { navigation.push({ method: 'redirect', ...value }) }, navigateBack(value) { navigation.push({ method: 'back', ...value }) }, reLaunch() {} }
  class ClockDate extends Date { constructor(...args) { if (args.length) super(...args); else super(now) } static now() { return now } }
  const context = vm.createContext({ wx, Date: ClockDate,
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, at: now + Math.max(1, delay) }); return id }, clearTimeout(id) { timers.delete(id) },
    setInterval() { throw Error('Only serial polling is allowed') }, clearInterval() {},
    console: Object.fromEntries(['log', 'error', 'warn', 'info', 'debug'].map(name => [name, (...args) => logs.push(args)])) })
  function load(relative) {
    const filename = path.resolve(root, relative)
    if (modules.has(filename)) return modules.get(filename)
    const record = { exports: {}, definition: null }; modules.set(filename, record)
    const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 } }).outputText
    new vm.Script('(function(require,module,exports,Page,App){' + output + '\n})', { filename }).runInContext(context)(name => {
      if (name.endsWith('/pet-service')) return { getPetService: () => service }
      return load(path.relative(root, path.resolve(path.dirname(filename), name + '.ts'))).exports
    }, record, record.exports, definition => { record.definition = definition }, definition => { record.definition = definition })
    return record
  }
  const h = { requests, logins, writes, calls, navigation, logs, timers, modals, privateStorage,
    session: () => load('services/live-device-session.ts').exports.getLiveDeviceSession(),
    page(name) { const definition = load(`pages/${name}/index.ts`).definition, page = { ...definition, data: plain(definition.data) }; page.setData = patch => { Object.assign(page.data, plain(patch)); writes.push({ page: name, patch: plain(patch) }) }; return page },
    pending(suffix) { return requests.find(value => !value.done && value.url.endsWith(suffix)) },
    success(request, value, statusCode = 200) { assert.ok(request && !request.done, 'a current request is required'); request.done = true; request.success({ statusCode, data: { success: true, data: value } }) },
    fail(request) { request.done = true; request.fail({ errMsg: 'raw-private-upstream-error' }) },
    async drain(overrides = {}) {
      for (let i = 0; i < 20; i++) {
        await flush()
        const pending = requests.filter(value => !value.done && value.method === 'GET')
        if (!pending.length) break
        for (const request of pending) {
          const route = new URL(request.url).pathname
          const kind = route === '/health' ? 'health' : route === '/v1/pets' ? 'pets' : route.endsWith('/status') ? 'status' : route.endsWith('/dialog/control') ? 'control' : route.endsWith('/dialog') ? 'dialog' : /\/dialog\/conversations\/conv_/.test(route) ? 'conversation' : route.includes('/dialog/conversations') ? 'conversations' : ''
          const samples = { health: { mode: 'local-development', wechatConfigured: true, readOnly: true }, pets: [{ petId: 'pet_1', name: '真实小安' }, { petId: 'pet_2', name: '另一台真实猫' }], status: device(), dialog: dialog(), conversations: conversations(), conversation: conversation(), control: control(controlEnabled), ...overrides }
          if (Object.prototype.hasOwnProperty.call(samples, kind)) h.success(request, samples[kind])
        }
      }
      await flush()
    },
    async authenticate(page) {
      const enabled = page.useRealDevice(); await h.drain(); await enabled
      const login = page.liveLogin(); logins.at(-1).success({ code: 'synthetic-one-time-code' }); await flush()
      h.success(h.pending('/auth/wechat'), { accessToken: 'synthetic-private-token-never-stored', userId: 'user_1', expiresAt: '2026-11-01T01:00:00Z' }); await h.drain(); await login
      const selected = page.liveSelect({ detail: { petId: 'pet_1' } }); await h.drain(); await selected
      return page
    },
    async boot() { const page = h.page('dialog'); await page.onShow(); return h.authenticate(page) },
    async detail(page, options = { id: CONVERSATION_ID, mode: 'live', petId: 'pet_1' }) {
      page.onHide(); const detail = h.page('conversation'); detail.onLoad(options); const shown = detail.onShow(); await h.drain(); await shown; return detail
    },
    async advance(ms) { now += ms; for (const [id, value] of [...timers]) if (value.at <= now) { timers.delete(id); value.fn() }; await flush() },
    stored: () => storageWrites, stopped: () => stopped
  }
  return h
}

const cases = {
  async 'original preview remains independent and never logs in or requests a real endpoint'() {
    const h = harness(), page = h.page('dialog'); await page.onShow()
    assert.equal(page.data.liveMode, false); assert.equal(page.data.conversations[0].userPreview, '演示用户摘要')
    assert.equal(h.requests.length, 0); assert.equal(h.logins.length, 0); page.onHide()
  },
  async 'real dialog uses shared identity real summaries and no invented elapsed or follow-up countdown'() {
    const h = harness(), page = await h.boot()
    assert.equal(page.data.liveMode, true); assert.equal(page.data.pet.name, '真实小安'); assert.equal(page.data.statusView.text, '等待唤醒词')
    assert.equal(page.data.statusElapsed, '未提供'); assert.equal(page.data.status.followUpRemaining, undefined)
    assert.equal(page.data.conversations[0].userPreview, '真实用户摘要'); assert.equal(page.data.liveListKnown, true)
    assert.doesNotMatch(JSON.stringify(page.data.conversations), /完整正文|演示/); page.onHide()
  },
  async 'closed real control gate keeps readonly pages and prevents every direct write handler'() {
    const h = harness(), page = await h.boot(), calls = h.calls.length, requests = h.requests.length
    await page.startListening(); await page.interrupt(); await page.controlVoice('listen'); await page.mainAction()
    page.onInput({ detail: { value: 'must not send' } }); await page.sendText(); await page.sendSnapshot('must not send', 'req_1'); await page.retrySend()
    assert.equal(h.calls.length, calls); assert.equal(h.requests.length, requests); assert.equal(page.data.inputText, ''); assert.equal(h.modals.length, 0)
    assert.ok(h.requests.filter(value => value.method !== 'GET').every(value => value.url.endsWith('/auth/wechat'))); page.onHide()
  },
  async 'switching real mode does not leak or destroy the original preview draft'() {
    const h = harness(), page = h.page('dialog'); await page.onShow(); page.onInput({ detail: { value: '保留的演示草稿' } })
    await h.authenticate(page); assert.equal(page.data.inputText, ''); assert.equal(page.data.pendingMessage, null)
    await page.showPreview(); assert.equal(page.data.liveMode, false); assert.equal(page.data.inputText, '保留的演示草稿'); page.onHide()
  },
  async 'empty persisted-only list is a verified empty state not a demo fallback'() {
    const h = harness(), page = await h.boot(), read = page.realDialog.refresh()
    await h.drain({ conversations: { ...conversations(), items: [] } }); await read
    assert.equal(page.data.liveListKnown, true); assert.deepEqual(plain(page.data.conversations), []); assert.equal(page.data.liveListError, ''); page.onHide()
  },
  async 'offline voice does not hide readable saved conversation summaries or declare the device disconnected'() {
    const h = harness(), page = await h.boot(), read = page.realDialog.refresh()
    await h.drain({ dialog: { ...dialog(), state: 'offline' } }); await read
    assert.equal(page.data.statusView.text, '语音服务离线'); assert.equal(page.data.liveOnline, true)
    assert.equal(page.data.conversations.length, 1); assert.match(page.data.statusView.hint, /不代表设备网络断开/); page.onHide()
  },
  async 'stale active status is never rendered as currently listening or answering'() {
    const h = harness(), page = await h.boot(), read = page.realDialog.refresh()
    await h.drain({ dialog: { ...dialog(), state: 'answering', sessionActive: true, audioPlaying: true, stale: true } }); await read
    assert.equal(page.data.liveStatusStale, true); assert.equal(page.data.dialogState, 'unavailable'); assert.equal(page.data.status, null)
    assert.equal(page.data.statusView.text, '语音状态已过期'); page.onHide()
  },
  async 'status and summary errors are independent and clear only their unverified area'() {
    const h = harness(), page = await h.boot(), read = page.realDialog.refresh()
    h.fail(h.pending('/dialog')); await h.drain(); await read
    assert.equal(page.data.liveStatusKnown, false); assert.equal(page.data.liveListKnown, true); assert.match(page.data.liveDialogError, /语音状态暂不可用/)
    const again = page.realDialog.refresh(), list = h.requests.find(value => !value.done && value.url.includes('/dialog/conversations'))
    h.fail(list); await h.drain(); await again
    assert.equal(page.data.liveStatusKnown, true); assert.equal(page.data.liveListKnown, false); assert.equal(page.data.conversations.length, 0)
    assert.match(page.data.liveListError, /摘要暂不可用/); page.onHide()
  },
  async 'conditional unchanged summaries retain content and a changed revision replaces instead of appending'() {
    const h = harness(), page = await h.boot(), read = page.realDialog.refresh()
    const list = h.requests.find(value => !value.done && value.url.includes('/dialog/conversations'))
    assert.match(list.url, /\/since\/[a-f0-9]{64}$/)
    await h.drain({ conversations: { ...conversations(), items: [], unchanged: true } }); await read
    assert.equal(page.data.conversations.length, 1); assert.equal(page.data.conversations[0].userPreview, '真实用户摘要')
    const next = page.realDialog.refresh(); await h.drain({ conversations: { ...conversations(), revision: 'f'.repeat(64), items: [{ ...summary(), conversationId: OTHER_ID, preview: '新的记录' }] } }); await next
    assert.equal(page.data.conversations.length, 1); assert.equal(page.data.conversations[0].userPreview, '新的记录'); page.onHide()
  },
  async 'real detail navigation accepts only a current summary and binds selected device in the URL'() {
    const h = harness(), page = await h.boot()
    page.openConversation({ currentTarget: { dataset: { id: OTHER_ID } } }); assert.equal(h.navigation.length, 0)
    page.openConversation({ currentTarget: { dataset: { id: CONVERSATION_ID } } }); page.openConversation({ currentTarget: { dataset: { id: CONVERSATION_ID } } })
    assert.equal(h.navigation.length, 1); assert.match(h.navigation[0].url, /mode=live&petId=pet_1$/); assert.ok(h.navigation[0].url.includes(CONVERSATION_ID)); page.onHide()
  },
  async 'real detail reads only its bound conversation after reauthorization and preserves full bounded message text'() {
    const h = harness(), page = await h.boot(), before = h.requests.length, detail = await h.detail(page)
    assert.equal(detail.data.detailLive, true); assert.equal(detail.data.conversation.messages.length, 2)
    assert.equal(detail.data.conversation.messages[1].content, '真实回答\n保留换行'); assert.equal(detail.data.pet.name, '真实小安')
    assert.ok(h.requests.slice(before).every(value => !value.url.endsWith('/dialog') && !/\/dialog\/conversations$/.test(value.url)))
    assert.equal(h.logins.length, 1); detail.onHide()
  },
  async 'detail marks mock-source records honestly instead of implying a real spoken exchange'() {
    const h = harness(), page = await h.boot(), detail = await h.detail(page), read = detail.realDetail.refresh()
    await h.drain({ conversation: { ...conversation(), conversation: { ...summary(), source: 'mock' } } }); await read
    assert.match(detail.data.conversation.sourceText, /Mock 来源/); detail.onHide()
  },
  async 'truncated detail labels its actual smaller window without claiming complete history'() {
    const h = harness(), page = await h.boot(), detail = await h.detail(page), read = detail.realDetail.refresh()
    const sample = conversation(); sample.truncated = true; sample.conversation = { ...summary(), messageCount: 102, userMessageCount: 51, assistantMessageCount: 51 }
    await h.drain({ conversation: sample }); await read
    assert.equal(detail.data.conversation.truncated, true); assert.equal(detail.data.conversation.limit, 100); assert.equal(detail.data.conversation.messageCount, 102); assert.equal(detail.data.conversation.windowMessageCount, 2)
    assert.equal(detail.data.conversation.messages.length, 2); detail.onHide()
  },
  async 'unchanged detail preserves prior messages and unchanged callbacks avoid long-message bridge writes'() {
    const h = harness(), page = await h.boot(), detail = await h.detail(page), read = detail.realDetail.refresh()
    await h.drain({ conversation: { ...conversation(), messages: [], unchanged: true } }); await read
    assert.equal(detail.data.conversation.messages.length, 2)
    const before = h.writes.length
    for (let i = 0; i < 3; i++) detail.renderRealDetail(detail.realDetail.getState())
    assert.equal(h.writes.length, before); detail.onHide()
  },
  async 'a valid 192 KiB Chinese suffix window keeps every original character without repeated large bridge writes'() {
    const h = harness(), page = await h.boot(), detail = await h.detail(page), read = detail.realDetail.refresh()
    const content = '汉'.repeat(8192), sample = conversation()
    sample.messages = Array.from({ length: 8 }, (_, index) => ({ messageId: 'msg_' + (index + 3).toString(16).padStart(48, '0'), role: index % 2 ? 'assistant' : 'user', content, createdAt: UTC }))
    sample.conversation = { ...summary(), messageCount: 120, userMessageCount: 60, assistantMessageCount: 60 }
    sample.windowMessageCount = 8; sample.truncated = true; sample.revision = 'f'.repeat(64)
    assert.equal(sample.messages.reduce((sum, item) => sum + Buffer.byteLength(item.content, 'utf8'), 0), 192 * 1024)
    await h.drain({ conversation: sample }); await read
    assert.equal(detail.data.conversation.windowMessageCount, 8); assert.equal(detail.data.conversation.messages.length, 8)
    assert.ok(detail.data.conversation.messages.every(item => item.content === content)); assert.equal(detail.data.conversation.truncated, true)
    const before = h.writes.length
    for (let index = 0; index < 3; index++) detail.renderRealDetail(detail.realDetail.getState())
    assert.equal(h.writes.length, before); detail.onHide()
  },
  async 'detail failure clears old content and gives a return-to-list recovery instead of showing demo messages'() {
    const h = harness(), page = await h.boot(), detail = await h.detail(page), read = detail.realDetail.refresh()
    h.success(h.requests.at(-1), {}, 404); await read
    assert.equal(detail.data.conversation, null); assert.match(detail.data.liveDetailError, /返回会话列表/); detail.onHide()
  },
  async 'changing selected device invalidates a bound detail and never fetches the same id from the other device'() {
    const h = harness(), page = await h.boot(), detail = await h.detail(page), before = h.requests.length
    const selected = detail.liveSelect({ detail: { petId: 'pet_2' } }); await h.drain(); await selected
    assert.equal(detail.data.conversation, null); assert.match(detail.data.liveDetailError, /设备.*改变|设备选择已变化/)
    assert.ok(h.requests.slice(before).every(value => !value.url.includes('/dialog/conversations/'))); detail.onHide()
  },
  async 'invalid real detail links never fall through to the mock detail service'() {
    const h = harness(), page = await h.boot(), before = h.calls.length, detail = await h.detail(page, { id: 'preview_conv', mode: 'live', petId: 'pet_1' })
    assert.equal(detail.data.conversation, null); assert.match(detail.data.liveDetailError, /链接无效/); assert.equal(h.calls.length, before)
    await detail.load(); assert.equal(h.calls.length, before); detail.onHide()
  },
  async 'hide during status list or detail requests prevents all late page bridge writes'() {
    const h = harness(), page = await h.boot(), read = page.realDialog.refresh(), pending = h.requests.filter(value => !value.done)
    page.onHide(); let before = h.writes.length
    for (const request of pending) request.success({ statusCode: 200, data: { success: true, data: request.url.endsWith('/dialog') ? dialog() : conversations() } })
    await read; assert.equal(h.writes.length, before)
    await page.onShow(); await h.drain(); const detail = await h.detail(page), detailRead = detail.realDetail.refresh(), late = h.requests.at(-1)
    detail.onHide(); before = h.writes.length
    late.success({ statusCode: 200, data: { success: true, data: conversation() } }); await detailRead
    assert.equal(h.writes.length, before); assert.equal(h.timers.size, 0)
  },
  async '401 clears shared identity and removes prior real summaries and transcript details'() {
    const h = harness(), page = await h.boot(), read = page.realDialog.refresh()
    h.success(h.pending('/dialog'), {}, 401); await read; await flush()
    assert.equal(page.data.liveState.signedIn, false); assert.equal(page.data.conversations.length, 0); assert.equal(page.data.liveStatusKnown, false); page.onHide()
    const other = harness(), live = await other.boot(), detail = await other.detail(live), detailRead = detail.realDetail.refresh()
    other.success(other.requests.at(-1), {}, 401); await detailRead
    assert.equal(detail.data.liveState.signedIn, false); assert.equal(detail.data.conversation, null); detail.onHide()
  },
  async 'real page and detail pull refresh remain GET-only and stop their refresh indicators'() {
    const h = harness(), page = await h.boot(), refresh = page.onPullDownRefresh(); await h.drain(); await refresh
    assert.equal(h.stopped(), 1); const detail = await h.detail(page), detailRefresh = detail.onPullDownRefresh(); await h.drain(); await detailRefresh
    assert.equal(h.stopped(), 2); assert.ok(h.requests.filter(value => value.method !== 'GET').every(value => value.url.endsWith('/auth/wechat'))); detail.onHide()
  },
  async 'no credential or transcript is persisted or sent to logging during real reads'() {
    const h = harness(), page = await h.boot(), detail = await h.detail(page)
    assert.equal(h.stored(), 0); assert.deepEqual(h.logs, [])
    for (const write of h.writes) assert.doesNotMatch(JSON.stringify(write), /synthetic-private-token|synthetic-one-time-code|Authorization|accessToken/)
    detail.onHide()
  },
  async 'real template guards all three writes by verified permissions and distinguishes saved history windows'() {
    const list = fs.readFileSync(path.join(root, 'pages/dialog/index.wxml'), 'utf8'), detail = fs.readFileSync(path.join(root, 'pages/conversation/index.wxml'), 'utf8')
    for (const text of ['!liveCanListen', 'liveMode && !liveDraftEditable', '!liveCanSendText', '!liveCanInterrupt', '核对原回执', '并非全部历史', '接口接受不代表回答或播音完成']) assert.ok(list.includes(text))
    assert.ok(detail.includes('仅显示最近')); assert.ok(detail.includes('conversation.windowMessageCount')); assert.ok(detail.includes('受条数与内容大小限制')); assert.ok(detail.includes('不是完整历史')); assert.ok(detail.includes("!detailLive && conversation.status === 'waiting_assistant'"))
    assert.ok(detail.includes('历史记录不代表当前正在回答'))
  },
  async 'a missing control API keeps the old readonly summaries working with every write disabled'() {
    const h = harness(), page = await h.boot(), refresh = page.refreshRealControl()
    h.success(h.pending('/dialog/control'), {}, 404); await refresh
    assert.equal(page.data.liveCanListen, false); assert.equal(page.data.liveCanInterrupt, false); assert.equal(page.data.liveCanSendText, false)
    assert.equal(page.data.liveListKnown, true); assert.equal(page.data.conversations.length, 1)
    await page.startListening(); await page.interrupt(); await page.sendText(); assert.equal(h.modals.length, 0); page.onHide()
  },
  async 'all three real commands need a native confirmation and accepted never claims sound or answer completion'() {
    for (const kind of ['listen', 'interrupt', 'text']) {
      const h = harness(true), page = await h.boot()
      if (kind === 'text') page.onInput({ detail: { value: '真实问题' } })
      const click = kind === 'listen' ? page.startListening() : kind === 'interrupt' ? page.interrupt() : page.sendText()
      await flush(); assert.equal(h.modals.length, 1); assert.match(h.modals[0].content, /麦克风.*云端.*扬声器.*自动动作/)
      assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0)
      const confirmed = h.modals[0].success({ confirm: true }); await h.drain()
      const request = h.pending('/dialog/commands'); assert.ok(request)
      assert.equal(request.data.kind, kind)
      assert.deepEqual(Object.keys(request.data).sort(), kind === 'text' ? ['content', 'kind', 'requestId'] : ['kind', 'requestId'])
      if (kind === 'text') assert.equal(request.data.content, '真实问题')
      h.success(request, commandReceipt(request)); await confirmed; await click; await h.drain()
      assert.match(kind === 'interrupt' ? page.data.liveInterruptMessage : page.data.liveMainMessage, /请求已接收.*不代表回答或声音播放已经完成/)
      assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 1); page.onHide()
    }
  },
  async 'cancelled real confirmation and repeated clicks do not dispatch or open a second confirmation'() {
    const h = harness(true), page = await h.boot(), click = page.startListening(); await flush()
    await page.startListening(); assert.equal(h.modals.length, 1)
    await h.modals[0].success({ confirm: false }); await click
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0)
    assert.match(page.data.liveControlError, /请求未发送.*取消/); page.onHide()
  },
  async 'all real command modals meet native four-character button limits before fresh verified dispatch'() {
    for (const kind of ['listen', 'interrupt', 'text']) {
      const h = harness(true, { strictModal: true }), page = await h.boot()
      if (kind === 'text') page.onInput({ detail: { value: '真实问题' } })
      const click = page.requestRealControl(kind); await flush()
      assert.equal(h.modals.length, 1, kind + ' must open a native-valid modal')
      assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0)
      const confirmed = h.modals[0].success({ confirm: true }); await flush()
      assert.ok(h.pending('/dialog/control')); assert.equal(h.pending('/dialog/commands'), undefined)
      await h.drain(); const request = h.pending('/dialog/commands'); assert.ok(request)
      assert.equal(request.data.kind, kind); h.success(request, commandReceipt(request))
      await confirmed; await click; await h.drain()
      assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 1); page.onHide()
    }
  },
  async 'native modal failure is visibly unsent and permits a newly confirmed attempt on each channel'() {
    for (const kind of ['listen', 'interrupt', 'text']) {
      const h = harness(true), page = await h.boot()
      if (kind === 'text') page.onInput({ detail: { value: '保留的问题' } })
      const click = page.requestRealControl(kind); await flush()
      h.modals[0].fail({ errMsg: 'raw-private-native-error' }); await click
      assert.match(page.data.liveControlError, /请求未发送.*确认弹窗.*失败/)
      assert.doesNotMatch(JSON.stringify(page.data), /raw-private/)
      assert.equal(page.data.liveControlConfirm, false)
      assert.equal(page.realControl.getState().confirmPending, false)
      assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0)
      assert.equal(h.privateStorage.size, 0)
      const retry = page.requestRealControl(kind); await flush(); assert.equal(h.modals.length, 2)
      const confirmed = h.modals[1].success({ confirm: true }); await h.drain()
      const request = h.pending('/dialog/commands'); assert.ok(request); h.success(request, commandReceipt(request))
      await confirmed; await retry; await h.drain()
      assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 1); page.onHide()
    }
  },
  async 'synchronous native modal failure settles without leaving confirmation or credentials behind'() {
    const h = harness(true, { throwModal: true }), page = await h.boot()
    await page.startListening()
    assert.match(page.data.liveControlError, /请求未发送.*确认弹窗.*失败/)
    assert.equal(page.data.liveControlConfirm, false); assert.equal(page.realControl.getState().confirmPending, false)
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0)
    assert.equal(h.privateStorage.size, 0); assert.deepEqual(h.logs, []); page.onHide()
  },
  async 'late old modal callbacks cannot cancel a newer visit confirmation or dispatch the old intent'() {
    for (const callback of ['cancel', 'confirm', 'fail']) {
      const h = harness(true), page = await h.boot(), oldClick = page.startListening(); await flush()
      page.onHide(); await page.onShow(); await h.drain()
      const newClick = page.interrupt(); await flush(); assert.equal(h.modals.length, 2)
      const target = page.controlConfirmation, before = h.writes.length
      if (callback === 'fail') h.modals[0].fail({ errMsg: 'raw-private-late-native-error' })
      else await h.modals[0].success({ confirm: callback === 'confirm' })
      await oldClick
      assert.equal(page.controlConfirmation, target, callback + ' must not change the new target')
      assert.equal(page.data.liveControlConfirm, true); assert.equal(h.writes.length, before)
      assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0)
      const confirmed = h.modals[1].success({ confirm: true }); await flush()
      assert.ok(h.pending('/dialog/control')); assert.equal(h.pending('/dialog/commands'), undefined)
      await h.drain(); const request = h.pending('/dialog/commands'); assert.ok(request)
      assert.equal(request.data.kind, 'interrupt'); h.success(request, commandReceipt(request))
      await confirmed; await newClick; await h.drain()
      assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 1); page.onHide()
    }
  },
  async 'real status polling never empties a draft and preview text remains independently cached'() {
    const h = harness(true), page = h.page('dialog'); await page.onShow(); page.onInput({ detail: { value: '预览保留草稿' } })
    await h.authenticate(page); page.onInput({ detail: { value: '真实输入中的问题' } })
    const read = page.realDialog.refresh(); await h.drain(); await read
    assert.equal(page.data.inputText, '真实输入中的问题'); assert.equal(page.data.realDraftCount, 8)
    await page.showPreview(); assert.equal(page.data.inputText, '预览保留草稿'); page.onHide()
  },
  async 'editing the real question while its confirmation is open visibly revokes the prepared content'() {
    const h = harness(true), page = await h.boot(); page.onInput({ detail: { value: '第一个问题' } })
    const click = page.sendText(); await flush(); page.onInput({ detail: { value: '修改后的问题' } })
    await h.modals[0].success({ confirm: true }); await click
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0)
    assert.equal(page.data.inputText, '修改后的问题'); assert.match(page.data.liveControlError, /请求未发送.*正文/); page.onHide()
  },
  async 'a changed control permission revokes an open modal before it can send a stale confirmation'() {
    const h = harness(true), page = await h.boot(), click = page.startListening(); await flush()
    const read = page.refreshRealControl(); await h.drain({ control: control(false) }); await read
    await h.modals[0].success({ confirm: true }); await click
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0); assert.match(page.data.liveControlError, /请求未发送/); page.onHide()
  },
  async 'explicit permission refresh clears only an old unsent guard notice and never dispatches a command'() {
    const h = harness(true), page = await h.boot(), click = page.startListening(); await flush()
    let read = page.refreshRealControl(); await h.drain({ control: control(false) }); await read
    await h.modals[0].success({ confirm: true }); await click
    assert.match(page.data.liveControlError, /请求未发送/)
    read = page.refreshRealControl(); await h.drain(); await read
    assert.equal(page.data.liveControlError, ''); assert.equal(page.data.liveCanListen, true)
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0); page.onHide()
  },
  async 'a pending permission GET disables only new confirmations and preserves editable text until verification settles'() {
    const h = harness(true), page = await h.boot(); page.onInput({ detail: { value: '保留输入' } })
    const read = page.refreshRealControl(); await flush()
    assert.match(page.data.liveControlStatus, /正在核对/)
    assert.equal(page.data.liveCanListen, false); assert.equal(page.data.liveCanInterrupt, false); assert.equal(page.data.liveCanSendText, false)
    assert.equal(page.data.liveDraftEditable, true); assert.equal(page.data.inputText, '保留输入')
    await page.startListening(); assert.equal(h.modals.length, 0)
    await h.drain(); await read
    assert.equal(page.data.liveCanListen, true); assert.equal(page.data.liveCanInterrupt, true); assert.equal(page.data.liveCanSendText, true)
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0); page.onHide()
  },
  async 'listen confirmation survives duplicate empty input and ordinary polls then dispatches once only after a fresh GET'() {
    const h = harness(true), page = await h.boot(), click = page.startListening(); await flush()
    const reads = h.requests.filter(item => item.url.endsWith('/dialog/control')).length
    page.onInput({ detail: { value: '' } }); await h.advance(10000); await h.drain()
    assert.equal(page.data.liveControlConfirm, true); assert.equal(page.data.liveControlError, '')
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/control')).length, reads)
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0)
    const confirmed = h.modals[0].success({ confirm: true }); await h.drain()
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/control')).length, reads + 1)
    const request = h.pending('/dialog/commands'); assert.ok(request); assert.equal(request.data.kind, 'listen')
    h.success(request, commandReceipt(request)); await confirmed; await click; await h.drain()
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 1); page.onHide()
  },
  async 'missing permission response is described as unverified rather than declaring the deployed service absent'() {
    const h = harness(true), page = await h.boot(), read = page.refreshRealControl(); await flush()
    h.success(h.pending('/dialog/control'), {}, 503); await read
    assert.equal(page.data.liveCanListen, false); assert.equal(page.data.liveCanInterrupt, false)
    assert.match(page.data.liveControlReason, /核对控制权限|读取失败/)
    assert.doesNotMatch(page.data.liveControlReason, /操作员更新服务|开启独立控制开关/)
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0); page.onHide()
  },
  async 'permission refresh preserves unresolved request markers and their original receipt-only warning'() {
    const h = harness(true), page = await h.boot(), click = page.startListening(); await flush()
    const confirmed = h.modals[0].success({ confirm: true }); await h.drain()
    const original = h.pending('/dialog/commands'); h.fail(original); await confirmed; await click
    const marker = JSON.stringify([...h.privateStorage.entries()])
    const read = page.refreshRealControl(); await h.drain(); await read
    assert.equal(page.data.liveCanListen, false); assert.equal(page.data.liveCanCheckMain, true)
    assert.match(page.data.liveMainMessage, /尚未确认|未知/)
    assert.equal(JSON.stringify([...h.privateStorage.entries()]), marker)
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 1); page.onHide()
  },
  async 'verified controls show deployed guidance while loading and disabled explanations remain conditional'() {
    const source = fs.readFileSync(path.join(root, 'pages/dialog/index.wxml'), 'utf8')
    assert.match(source, /liveDraftEditable\s*\?\s*'对话控制已开放/)
    assert.match(source, /disabled="\{\{liveControlLoading \|\| liveControlConfirm \|\| liveControlSubmitting \|\| liveControlInterrupting\}\}"/)
    assert.match(source, /dialogState === 'offline' && liveCanListen/)
  },
  async 'hide during confirmation prevents later native confirmation from sending a device request'() {
    const h = harness(true), page = await h.boot(), click = page.startListening(); await flush(); page.onHide()
    const before = h.writes.length; await h.modals[0].success({ confirm: true }); await click
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0); assert.equal(h.writes.length, before)
  },
  async 'switching selected devices during confirmation cannot send the captured command to either device'() {
    const h = harness(true), page = await h.boot(), click = page.startListening(); await flush()
    const select = page.liveSelect({ detail: { petId: 'pet_2' } }); await h.drain(); await select
    await h.modals[0].success({ confirm: true }); await click
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0); page.onHide()
  },
  async 'unknown text outcome is checked only by original receipt GET and a 404 does not unlock new commands'() {
    const h = harness(true), page = await h.boot(); page.onInput({ detail: { value: '不能重复发送的真实问题' } })
    const click = page.sendText(); await flush(); const confirmed = h.modals[0].success({ confirm: true }); await h.drain()
    const request = h.pending('/dialog/commands'); h.fail(request); await confirmed; await click; await h.drain()
    assert.equal(page.data.liveCanCheckMain, true); assert.equal(page.data.liveCanSendText, false)
    const check = page.checkOriginalReceipt({ currentTarget: { dataset: { channel: 'main' } } }); await flush()
    const lookup = h.requests.at(-1); assert.equal(lookup.method, 'GET'); assert.ok(lookup.url.endsWith('/dialog/requests/' + request.data.requestId))
    h.success(lookup, {}, 404); await check
    assert.equal(page.data.liveCanSendText, false); assert.equal(page.data.liveCanCheckMain, true)
    await page.sendText(); assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 1); page.onHide()
  },
  async 'an independent interrupt does not erase an unresolved original text receipt'() {
    const h = harness(true), page = await h.boot(); page.onInput({ detail: { value: '未知结果问题' } })
    const click = page.sendText(); await flush(); const confirmed = h.modals[0].success({ confirm: true }); await h.drain()
    const original = h.pending('/dialog/commands'); h.fail(original); await confirmed; await click; await h.drain()
    assert.equal(page.data.liveCanInterrupt, true)
    const interrupt = page.interrupt(); await flush(); const interruptConfirmed = h.modals[1].success({ confirm: true }); await h.drain()
    const second = h.pending('/dialog/commands'); assert.equal(second.data.kind, 'interrupt'); h.success(second, commandReceipt(second)); await interruptConfirmed; await interrupt; await h.drain()
    assert.equal(page.data.liveCanCheckMain, true); assert.equal(page.data.liveCanSendText, false)
    const check = page.checkOriginalReceipt({ currentTarget: { dataset: { channel: 'main' } } }); await flush()
    assert.ok(h.requests.at(-1).url.endsWith(original.data.requestId)); h.success(h.requests.at(-1), commandReceipt(original, 'unknown')); await check; page.onHide()
  },
  async 'accepted text clears only its original draft and never a newer question typed during the request'() {
    const h = harness(true), page = await h.boot(); page.onInput({ detail: { value: '正在发送的问题' } })
    const click = page.sendText(); await flush(); const confirmed = h.modals[0].success({ confirm: true }); await h.drain()
    const request = h.pending('/dialog/commands'); page.onInput({ detail: { value: '发送期间输入的下一个问题' } })
    h.success(request, commandReceipt(request)); await confirmed; await click; await h.drain()
    assert.equal(page.data.inputText, '发送期间输入的下一个问题'); assert.equal(request.data.content, '正在发送的问题'); page.onHide()
  },
  async 'exactly five hundred Unicode characters are preserved while overlong or control text remains unsent'() {
    const h = harness(true), page = await h.boot(), content = '🐱'.repeat(500)
    page.onInput({ detail: { value: content } }); assert.equal(page.data.realDraftCount, 500); assert.equal(page.data.inputText, content)
    const click = page.sendText(); await flush(); const confirmed = h.modals[0].success({ confirm: true }); await h.drain()
    const request = h.pending('/dialog/commands'); assert.equal(request.data.content, content); h.success(request, commandReceipt(request)); await confirmed; await click; await h.drain()
    for (const invalid of ['猫'.repeat(501), '第一行\n第二行', 'bad\ud800']) {
      page.onInput({ detail: { value: invalid } }); assert.equal(page.data.inputText, invalid); assert.equal(page.data.liveCanSendText, false)
      assert.ok(page.data.realDraftError); await page.sendText()
    }
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 1); page.onHide()
  },
  async 'request bookkeeping never persists the real question body or authentication token'() {
    const h = harness(true), page = await h.boot(), content = '仅在内存的真实私密问题'
    page.onInput({ detail: { value: content } }); const click = page.sendText(); await flush()
    const confirmed = h.modals[0].success({ confirm: true }); await h.drain(); const request = h.pending('/dialog/commands')
    h.fail(request); await confirmed; await click
    const stored = JSON.stringify([...h.privateStorage.values()]); assert.doesNotMatch(stored, /仅在内存的真实私密问题|synthetic-private-token|synthetic-one-time-code/)
    assert.deepEqual(h.logs, []); page.onHide()
  },
  async 'all native busy stages have Chinese labels without enabling new listen or text commands'() {
    const labels = { processing_audio: '正在处理语音问题', followup_preparing: '正在准备继续追问', connected: '语音云端已连接', proactive_queued: '设备自主短语已排队', proactive_speaking: '设备正在自主播报', proactive_finishing: '设备自主播报收尾中', stopping: '语音服务正在停止' }
    const h = harness(true), page = await h.boot()
    for (const [state, label] of Object.entries(labels)) {
      const read = page.liveDialogRefresh(); await h.drain({ dialog: { ...dialog(), state }, control: { ...control(true), state, canListen: false, canSendText: false } }); await read
      assert.equal(page.data.statusView.text, label); assert.equal(page.data.liveCanListen, false); assert.equal(page.data.liveCanSendText, false)
    }
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0); page.onHide()
  },
  async 'stale or manual-review control never enables main commands but can keep explicitly allowed interrupt independent'() {
    const h = harness(true), page = await h.boot(), read = page.refreshRealControl()
    await h.drain({ control: { ...control(true), stale: true, manualReviewRequired: true, canListen: false, canSendText: false } }); await read
    assert.equal(page.data.liveCanListen, false); assert.equal(page.data.liveCanSendText, false); assert.equal(page.data.liveCanInterrupt, true)
    assert.match(page.data.liveControlStatus, /独立打断/); page.onHide()
  },
  async 'fresh permission verification after native confirmation can reject dispatch without sending a command'() {
    const h = harness(true), page = await h.boot(), click = page.startListening(); await flush()
    const confirmed = h.modals[0].success({ confirm: true }); await h.drain({ control: control(false) }); await confirmed; await click
    assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 0)
    assert.match(page.data.liveMainMessage, /请求未发送/); page.onHide()
  },
  async 'a prior accepted receipt never masks the next timed-out request on either independent channel'() {
    for (const kind of ['listen', 'interrupt']) {
      const h = harness(true), page = await h.boot()
      for (let index = 0; index < 2; index++) {
        const click = kind === 'listen' ? page.startListening() : page.interrupt(); await flush()
        const confirmed = h.modals[index].success({ confirm: true }); await h.drain()
        const request = h.pending('/dialog/commands')
        if (index) {
          assert.match(kind === 'listen' ? page.data.liveMainMessage : page.data.liveInterruptMessage, /正在发送/)
          assert.equal(kind === 'listen' ? page.data.liveMainReceipt : page.data.liveInterruptReceipt, null)
        }
        if (!index) h.success(request, commandReceipt(request)); else h.fail(request)
        await confirmed; await click; await h.drain()
        const message = kind === 'listen' ? page.data.liveMainMessage : page.data.liveInterruptMessage
        if (!index) assert.match(message, /已接收/)
        else { assert.match(message, /尚未确认|未知/); assert.doesNotMatch(message, /已接收|已接受/) }
      }
      page.onHide()
    }
  },
  async 'a prior accepted receipt cannot mask the next pre-dispatch verification failure'() {
    for (const kind of ['listen', 'interrupt']) {
      const h = harness(true), page = await h.boot()
      const first = kind === 'listen' ? page.startListening() : page.interrupt(); await flush()
      const accepted = h.modals[0].success({ confirm: true }); await h.drain()
      const request = h.pending('/dialog/commands'); h.success(request, commandReceipt(request)); await accepted; await first
      const second = kind === 'listen' ? page.startListening() : page.interrupt(); await flush()
      const checked = h.modals[1].success({ confirm: true }); await flush()
      h.success(h.pending('/dialog/control'), {}, 503); await checked; await second
      assert.equal(h.requests.filter(item => item.url.endsWith('/dialog/commands')).length, 1)
      const message = kind === 'listen' ? page.data.liveMainMessage : page.data.liveInterruptMessage
      assert.match(message, /请求未发送.*核验失败/); assert.doesNotMatch(message, /已接收|已接受/)
      assert.equal(kind === 'listen' ? page.data.liveMainReceipt : page.data.liveInterruptReceipt, null); page.onHide()
    }
  }
}

;(async () => {
  let failed = 0
  for (const [name, run] of Object.entries(cases)) {
    try { await run(); console.log('PASS ' + name) }
    catch (error) { failed++; console.error('FAIL ' + name + ': ' + error.stack) }
  }
  console.log(`Live dialog pages smoke: ${Object.keys(cases).length - failed}/${Object.keys(cases).length} (offline actual TS, not WeChat GUI)`)
  if (failed) process.exitCode = 1
})().catch(error => { console.error(error); process.exitCode = 1 })
