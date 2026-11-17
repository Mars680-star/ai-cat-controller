const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const root = path.resolve(__dirname, '..')
const pet = { petId: 'preview-pet', deviceId: 'preview-device', name: '小安', connectionState: 'online' }
const status = { state: 'ready', elapsedSeconds: 0, followUpRemaining: 0, sessionActive: false, canInterrupt: false }
const record = { conversationId: 'conv-1', status: 'complete', startedAt: '2026-10-19T12:00:00+08:00', userPreview: '你好', assistantPreview: '你好呀', messageCount: 2 }
const flush = async () => { for (let index = 0; index < 16; index++) await Promise.resolve() }
function deferred() {
  let resolve, reject
  const promise = new Promise((ok, fail) => { resolve = ok; reject = fail })
  return { promise, resolve, reject }
}

function harness() {
  const storage = new Map([['aiCatSession', { userId: 'preview-user' }], ['aiCatPet', pet]])
  const timers = new Map()
  const navigation = []
  const toasts = []
  const modules = new Map()
  let nextTimer = 0
  let nextRequest = 0
  const service = {
    getBoundPet: async () => storage.get('aiCatPet') || null,
    getDialogStatus: async () => ({ ...status }),
    listConversations: async () => ({ items: [{ ...record }], syncText: '已同步' }),
    startListening: async () => {}, interruptDialog: async () => {}, sendText: async () => {},
    getConversation: async () => ({ ...record, messages: [{ messageId: 'm1', role: 'user', content: '你好', createdAt: '14:18' }] })
  }
  const wx = {
    getStorageSync: (key) => storage.get(key),
    setStorageSync: (key, value) => storage.set(key, value),
    removeStorageSync: (key) => storage.delete(key),
    showToast: (options) => toasts.push(options),
    stopPullDownRefresh() {},
    navigateTo: (options) => navigation.push({ kind: 'to', ...options }),
    navigateBack: (options) => navigation.push({ kind: 'back', ...options }),
    redirectTo: (options) => navigation.push({ kind: 'redirect', ...options })
  }
  function readModule(filename, capture) {
    if (!capture && modules.has(filename)) return modules.get(filename)
    const module = { exports: {} }
    const sandbox = {
      module, exports: module.exports, wx,
      setTimeout: (fn, delay) => { const id = ++nextTimer; timers.set(id, { fn, delay, repeat: false }); return id },
      clearTimeout: (id) => timers.delete(id),
      setInterval: (fn, delay) => { const id = ++nextTimer; timers.set(id, { fn, delay, repeat: true }); return id },
      clearInterval: (id) => timers.delete(id),
      Page: capture,
      require: (id) => {
        if (id.endsWith('/services/pet-service')) return { getPetService: () => service }
        if (id.endsWith('/utils/request-id')) return { createRequestId: (prefix) => `${prefix}-${++nextRequest}` }
        if (id.endsWith('/utils/format')) return { formatDateTime: (value) => `日期 ${value}` }
        if (!id.startsWith('.')) throw new Error(`Unexpected dependency: ${id}`)
        return readModule(path.resolve(path.dirname(filename), `${id}.ts`))
      }
    }
    const source = fs.readFileSync(filename, 'utf8')
    vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2019, module: ts.ModuleKind.CommonJS } }).outputText, sandbox, { filename })
    if (!capture) modules.set(filename, module.exports)
    return module.exports
  }
  function createPage(name) {
    let page
    readModule(path.join(root, 'pages', name, 'index.ts'), (definition) => { page = definition })
    page.data = JSON.parse(JSON.stringify(page.data))
    page.writes = []
    page.setData = function (patch, done) { this.writes.push(patch); Object.assign(this.data, patch); if (done) done() }
    return page
  }
  function fireTimer() {
    const [id, timer] = timers.entries().next().value || []
    assert.ok(timer, 'a visible page should schedule a poll')
    if (!timer.repeat) timers.delete(id)
    return timer.fn()
  }
  return { storage, timers, service, navigation, toasts, createPage, fireTimer }
}

const cases = []
const test = (name, run) => cases.push({ name, run })

test('hide during initial load discards late data and further requests', async () => {
  const h = harness(), bound = deferred(), page = h.createPage('dialog')
  let requests = 0
  h.service.getBoundPet = () => bound.promise
  h.service.getDialogStatus = async () => { requests++; return status }
  const showing = page.onShow(); await flush(); page.onHide()
  const writes = page.writes.length
  bound.resolve(pet); await showing
  assert.equal(page.writes.length, writes)
  assert.equal(requests, 0)
  assert.equal(h.timers.size, 0)
})

test('old list response cannot overwrite a later show', async () => {
  const h = harness(), oldList = deferred(), page = h.createPage('dialog')
  let calls = 0
  h.service.listConversations = () => ++calls === 1 ? oldList.promise : Promise.resolve({ items: [{ ...record, userPreview: '新的记录' }] })
  const first = page.onShow(); await flush(); page.onHide()
  await page.onShow()
  oldList.resolve({ items: [{ ...record, userPreview: '过时的记录' }] }); await first
  assert.equal(page.data.conversations[0].userPreview, '新的记录')
  assert.equal(h.timers.size, 1)
})

test('manual status refreshes share a single in-flight request', async () => {
  const h = harness(), page = h.createPage('dialog'); await page.onShow()
  const next = deferred(); let calls = 0
  h.service.getDialogStatus = () => { calls++; return next.promise }
  const first = page.refreshStatus(), second = page.refreshStatus(); await flush()
  assert.equal(calls, 1)
  next.resolve({ ...status }); await Promise.all([first, second])
})

test('poll schedules only after the preceding request settles', async () => {
  const h = harness(), page = h.createPage('dialog'); await page.onShow()
  const next = deferred(); h.service.getDialogStatus = () => next.promise
  const polling = h.fireTimer(); await flush()
  assert.equal(h.timers.size, 0)
  next.resolve({ ...status }); await polling; await flush()
  assert.equal(h.timers.size, 1)
})

test('unchanged status produces no rendering bridge update', async () => {
  const h = harness(), page = h.createPage('dialog'); await page.onShow()
  page.writes.length = 0
  await page.refreshStatus()
  assert.equal(page.writes.length, 0)
  h.service.getDialogStatus = async () => ({ ...status, elapsedSeconds: 80 })
  await page.refreshStatus()
  assert.equal(page.writes.length, 0)
  assert.equal(page.data.statusElapsed, '—')
  h.service.getDialogStatus = async () => ({ ...status, state: 'listening', elapsedSeconds: 2 })
  await page.refreshStatus()
  assert.equal(page.data.status.elapsedSeconds, 2)
  assert.ok(page.writes.every((patch) => !Object.hasOwn(patch, 'conversations')))
})

test('conversation list sends summaries, never full message bodies, to rendering', async () => {
  const h = harness(), page = h.createPage('dialog')
  h.service.listConversations = async () => ({ items: [{ ...record, messages: [{ content: '完整历史不得传入列表渲染层' }], internalMetadata: 'not rendered' }] })
  await page.onShow()
  assert.equal(page.data.conversations.length, 1)
  assert.equal(page.data.conversations[0].userPreview, record.userPreview)
  assert.equal(Object.hasOwn(page.data.conversations[0], 'messages'), false)
  assert.equal(Object.hasOwn(page.data.conversations[0], 'internalMetadata'), false)
  assert.equal(page.writes.some((patch) => JSON.stringify(patch).includes('完整历史不得传入列表渲染层')), false)
})

test('late polling and voice-control completions do not touch hidden UI', async () => {
  for (const method of ['refreshStatus', 'startListening', 'interrupt']) {
    const h = harness(), page = h.createPage('dialog'); await page.onShow()
    page.data.status = { ...status, canInterrupt: true, sessionActive: true }
    const next = deferred()
    if (method === 'refreshStatus') h.service.getDialogStatus = () => next.promise
    else h.service[method === 'interrupt' ? 'interruptDialog' : method] = () => next.promise
    const operation = page[method](); await flush(); page.onHide(); page.onUnload()
    const writes = page.writes.length
    next.resolve({ ...status, state: 'listening' }); await operation; await flush()
    assert.equal(page.writes.length, writes, method)
    assert.equal(h.timers.size, 0, method)
    assert.equal(h.toasts.length, 0, method)
  }
})

test('draft survives redirect-style page recreation without persistent storage', async () => {
  const h = harness(), page = h.createPage('dialog'); await page.onShow()
  page.onInput({ detail: { value: '还没问完的问题' } }); page.onHide(); page.onUnload()
  const next = h.createPage('dialog'); await next.onShow()
  assert.equal(next.data.inputText, '还没问完的问题')
  assert.equal(h.storage.size, 2)
})

test('failed send keeps the draft and retries with the original requestId', async () => {
  const h = harness(), page = h.createPage('dialog'); await page.onShow()
  const ids = []; let attempts = 0
  h.service.sendText = async (petId, content, requestId) => { ids.push(requestId); if (++attempts === 1) throw new Error('preview failure') }
  page.onInput({ detail: { value: '同一个问题' } }); await page.sendText()
  assert.equal(page.data.inputText, '同一个问题')
  assert.equal(page.data.pendingMessage.state, 'failed')
  await page.retrySend(); await flush()
  assert.equal(ids.length, 2); assert.equal(ids[0], ids[1]); assert.equal(page.data.inputText, '')
})

test('successful send does not erase a draft typed while it was pending', async () => {
  const h = harness(), page = h.createPage('dialog'); await page.onShow()
  const next = deferred(); h.service.sendText = () => next.promise
  page.onInput({ detail: { value: '正在发送的问题' } }); const sending = page.sendText(); await flush()
  assert.equal(page.data.inputText, '正在发送的问题')
  page.onInput({ detail: { value: '下一条新问题' } }); next.resolve(); await sending
  assert.equal(page.data.inputText, '下一条新问题')
  page.onHide(); page.onUnload(); const fresh = h.createPage('dialog'); await fresh.onShow()
  assert.equal(fresh.data.inputText, '下一条新问题')
})

test('hidden send completion reconciles the cache without UI updates', async () => {
  const h = harness(), page = h.createPage('dialog'); await page.onShow()
  const next = deferred(); h.service.sendText = () => next.promise
  page.onInput({ detail: { value: '切页前发送' } }); const sending = page.sendText(); await flush()
  page.onHide(); page.onUnload(); const writes = page.writes.length
  next.resolve(); await sending; await flush()
  assert.equal(page.writes.length, writes); assert.equal(h.toasts.length, 0)
  const fresh = h.createPage('dialog'); await fresh.onShow(); assert.equal(fresh.data.inputText, '')
})

test('a recreated page observes a still-pending send without sending it twice', async () => {
  const h = harness(), page = h.createPage('dialog'); await page.onShow()
  const next = deferred(); let calls = 0
  h.service.sendText = () => { calls++; return next.promise }
  page.onInput({ detail: { value: '跨页等待' } }); const sending = page.sendText(); await flush()
  page.onHide(); page.onUnload()
  const fresh = h.createPage('dialog'); await fresh.onShow()
  assert.equal(fresh.data.sending, true); fresh.sendText(); assert.equal(calls, 1)
  next.resolve(); await sending; await flush()
  assert.equal(fresh.data.sending, false); assert.equal(fresh.data.inputText, '')
})

test('retrying an older failed question preserves a newer draft', async () => {
  const h = harness(), page = h.createPage('dialog'); await page.onShow()
  const requests = []; let calls = 0
  h.service.sendText = async (petId, content, requestId) => { requests.push({ content, requestId }); if (++calls === 1) throw Error('preview failure') }
  page.onInput({ detail: { value: '失败的问题' } }); await page.sendText()
  page.onInput({ detail: { value: '正在编辑的新问题' } }); await page.retrySend()
  assert.equal(requests.length, 2); assert.equal(requests[0].content, requests[1].content); assert.equal(requests[0].requestId, requests[1].requestId)
  assert.equal(page.data.inputText, '正在编辑的新问题')
})

test('failed retry state and requestId survive page recreation', async () => {
  const h = harness(), page = h.createPage('dialog'); await page.onShow()
  const ids = []; let calls = 0
  h.service.sendText = async (petId, content, requestId) => { ids.push(requestId); if (++calls === 1) throw Error('preview failure') }
  page.onInput({ detail: { value: '保留原请求' } }); await page.sendText(); page.onHide(); page.onUnload()
  const fresh = h.createPage('dialog'); await fresh.onShow(); await fresh.retrySend()
  assert.equal(ids.length, 2); assert.equal(ids[0], ids[1]); assert.equal(fresh.data.inputText, '')
})

test('old status cannot overwrite a voice-control result', async () => {
  const h = harness(), page = h.createPage('dialog'); await page.onShow()
  const old = deferred(); let calls = 0
  h.service.getDialogStatus = () => ++calls === 1 ? old.promise : Promise.resolve({ ...status, state: 'listening', sessionActive: true })
  const polling = page.refreshStatus(); await flush()
  const listening = page.startListening(); await flush()
  assert.equal(calls, 1)
  old.resolve({ ...status }); await Promise.all([polling, listening])
  assert.equal(page.data.dialogState, 'listening'); assert.equal(calls, 2)
})

test('scope changes and unbound visits do not reveal a previous draft', async () => {
  const h = harness(), page = h.createPage('dialog'); await page.onShow()
  page.onInput({ detail: { value: '原账号草稿' } }); page.onHide(); page.onUnload()
  h.storage.set('aiCatSession', { userId: 'another-user' })
  const other = h.createPage('dialog'); await other.onShow(); assert.equal(other.data.inputText, '')
  other.onInput({ detail: { value: '新账号草稿' } }); other.onHide(); other.onUnload()
  h.storage.delete('aiCatPet'); const unbound = h.createPage('dialog'); await unbound.onShow()
  h.storage.set('aiCatPet', pet); const rebound = h.createPage('dialog'); await rebound.onShow(); assert.equal(rebound.data.inputText, '')
  rebound.onInput({ detail: { value: '未登录应清除' } }); rebound.onHide(); rebound.onUnload()
  h.storage.delete('aiCatSession'); const loggedOut = h.createPage('dialog'); await loggedOut.onShow()
  h.storage.set('aiCatSession', { userId: 'another-user' }); const relogged = h.createPage('dialog'); await relogged.onShow(); assert.equal(relogged.data.inputText, '')
})

test('conversation navigation ignores repeated taps and unlocks on failure', async () => {
  const h = harness(), page = h.createPage('dialog'); await page.onShow()
  const event = { currentTarget: { dataset: { id: 'conv/a?b' } } }
  page.openConversation(event); page.openConversation(event)
  assert.equal(h.navigation.length, 1); assert.ok(h.navigation[0].url.endsWith('conv%2Fa%3Fb'))
  h.navigation[0].fail(); page.openConversation(event); assert.equal(h.navigation.length, 2)
})

test('detail discards a response arriving after hide/unload', async () => {
  const h = harness(), page = h.createPage('conversation'), next = deferred()
  h.service.getConversation = () => next.promise
  page.onLoad({ id: 'conv-1' }); const showing = page.onShow ? page.onShow() : undefined; await flush()
  if (page.onHide) page.onHide(); if (page.onUnload) page.onUnload()
  const writes = page.writes.length
  next.resolve({ ...record, messages: [] }); await showing; await flush()
  assert.equal(page.writes.length, writes)
})

test('detail preserves long content, valid times, and guards repeated back taps', async () => {
  const h = harness(), page = h.createPage('conversation'), long = `${'一段很长的回答。'.repeat(1000)}\nhttps://example.invalid/${'a'.repeat(500)}`
  h.service.getConversation = async () => ({ ...record, messages: [{ messageId: 'a', role: 'assistant', content: long, createdAt: '14:18' }, { messageId: 'b', role: 'user', content: '你好', createdAt: '2026-10-19T12:00:00+08:00' }] })
  page.onLoad({ id: 'conv-1' }); if (page.onShow) await page.onShow(); await flush()
  assert.equal(page.data.conversation.messages[0].content, long)
  assert.equal(page.data.conversation.messages[0].createdLabel, '14:18')
  assert.ok(page.data.conversation.messages[1].createdLabel.startsWith('日期 '))
  page.goBack(); page.goBack(); assert.equal(h.navigation.length, 1)
})

test('detail latest show wins and unchanged refresh does not resend long messages', async () => {
  const h = harness(), page = h.createPage('conversation'), old = deferred()
  let calls = 0
  const latest = { ...record, messages: [{ messageId: 'new', role: 'assistant', content: '新的内容', createdAt: '14:18' }] }
  h.service.getConversation = () => ++calls === 1 ? old.promise : Promise.resolve(latest)
  page.onLoad({ id: 'conv-1' }); const first = page.onShow(); await flush(); page.onHide(); await page.onShow()
  old.resolve({ ...record, messages: [{ messageId: 'old', content: '旧内容' }] }); await first
  assert.equal(page.data.conversation.messages[0].content, '新的内容')
  page.writes.length = 0; await page.load(); assert.equal(page.writes.length, 0)
})

;(async () => {
  let failed = 0
  for (const { name, run } of cases) {
    try { await run(); console.log(`ok - ${name}`) }
    catch (error) { failed++; console.error(`not ok - ${name}\n  ${error.message}`) }
  }
  console.log(`dialog-runtime-smoke: ${cases.length - failed}/${cases.length} passed`)
  if (failed) process.exitCode = 1
})()
