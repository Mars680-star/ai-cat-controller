// Actual TS profile/shared session/client/controller with offline wx I/O only.
// No device, credentials, real network or plaintext local persistence is used.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..'), UTC = '2026-11-01T00:00:00Z'
const plain = value => JSON.parse(JSON.stringify(value))
const flush = async () => { for (let i = 0; i < 90; i++) await Promise.resolve() }
const input = value => ({ detail: { value } })
const snapshot = (enabled = true) => ({ verified: true, settingsEnabled: enabled, feedbackEnabled: enabled,
  canChangeName: enabled, canChangeVolume: enabled, canChangeFollowUp: enabled, canSubmitFeedback: enabled,
  name: '真实小安', storedVolume: 48, outputVolume: 150, outputMuted: false, outputVolumeAvailable: true,
  followUpSeconds: 25, followUpSource: 'persisted', minimumSeconds: 5, maximumSeconds: 120,
  settingsReviewRequired: false, feedbackReviewRequired: false, manualReviewRequired: false,
  reason: enabled ? '每次保存须确认' : '设置和反馈尚未开放', sampledAt: UTC })
const device = () => ({ readOnly: true, sampledAt: UTC, health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' },
  device: { connected: true, deviceSerial: '7c2****a128', networkName: null, batteryPercent: 66, batteryState: 'discharging', batteryVoltage: 3.7, chargerOnline: false, batteryPresent: true, lastSeenAt: null },
  dialog: { state: 'ready', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false } })
function receipt(request, status = 'recorded', steps) {
  const kind = request.data.kind
  if (!steps) steps = kind === 'feedback' ? [{ step: 'feedback', status: status === 'recorded' ? 'applied' : status === 'not_sent' ? 'not_sent' : status === 'sending' ? 'sending' : 'unknown' }] :
    [...(Object.keys(request.data.changes).some(key => key !== 'followUpSeconds') ? ['pet_settings'] : []), ...('followUpSeconds' in request.data.changes ? ['follow_up'] : [])].map(step => ({ step, status: status === 'recorded' ? 'applied' : status === 'not_sent' ? 'not_sent' : status === 'sending' ? 'sending' : 'unknown' }))
  return { requestId: request.data.requestId, kind, status, steps, updatedAt: UTC }
}
function harness(options = {}) {
  const requests = [], logins = [], writes = [], calls = [], navigation = [], modals = [], notices = [], modules = new Map(), timers = new Map(), storage = new Map()
  let timerId = 0, now = Date.parse(UTC), values = options.snapshot || snapshot(options.enabled !== false)
  const previewPet = { petId: 'preview_pet', deviceId: 'preview_device', name: '演示小安', personalityName: '演示性格' }
  const service = { getBoundPet: async () => previewPet, getSettings: async () => ({ volume: 62, followUpSeconds: 30 }),
    updateSettings: async (id, changes) => ({ pet: { ...previewPet, name: changes.name } }), submitFeedback: async () => {},
    unbindPet: async () => {}, clearMemory: async () => {}, resetProductData: async () => ({ backupId: 'preview-only' }) }
  for (const name of Object.keys(service)) { const method = service[name]; service[name] = (...args) => { calls.push({ name, args }); return method(...args) } }
  const wx = { login(value) { logins.push(value) }, request(value) { requests.push(value); return { abort() { if (!value.done) { value.done = true; value.fail({ errMsg: 'aborted-private' }) } } } },
    getStorageSync(key) { return key === 'aiCatSession' ? { userId: 'preview_user', displayName: '演示账号' } : storage.get(key) },
    setStorageSync(key, value) { storage.set(key, plain(value)) }, removeStorageSync(key) { storage.delete(key) },
    showToast(value) { notices.push(value) }, showModal(value) {
      if (options.throwModal) throw Error('raw-private-native-failure')
      if (options.strictModal && [value.confirmText, value.cancelText].some(text => text !== undefined && Array.from(text).length > 4)) {
        value.fail({ errMsg: 'showModal:fail confirmText exceeds four characters' }); return
      }
      modals.push(value)
    }, stopPullDownRefresh() {},
    redirectTo(value) { navigation.push(value) }, reLaunch(value) { navigation.push(value) } }
  class ClockDate extends Date { constructor(...args) { if (args.length) super(...args); else super(now) } static now() { return now } }
  const context = vm.createContext({ wx, Date: ClockDate, console,
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, at: now + Math.max(1, delay) }); return id }, clearTimeout(id) { timers.delete(id) },
    setInterval() { throw Error('Only serial polling allowed') }, clearInterval() {} })
  function load(relative) {
    const filename = path.resolve(root, relative)
    if (modules.has(filename)) return modules.get(filename)
    const record = { exports: {}, definition: null }; modules.set(filename, record)
    const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 } }).outputText
    new vm.Script('(function(require,module,exports,Page,App){' + output + '\n})', { filename }).runInContext(context)(name => name.endsWith('/pet-service') ? { getPetService: () => service } : load(path.relative(root, path.resolve(path.dirname(filename), name + '.ts'))).exports,
      record, record.exports, definition => { record.definition = definition }, definition => { record.definition = definition })
    return record
  }
  const h = { requests, logins, writes, calls, modals, notices, navigation, storage, service,
    session: () => load('services/live-device-session.ts').exports.getLiveDeviceSession(),
    page() { const definition = load('pages/profile/index.ts').definition, page = { ...definition, data: plain(definition.data) }; page.setData = patch => { Object.assign(page.data, plain(patch)); writes.push(plain(patch)) }; return page },
    values(value) { values = value }, pending(suffix) { return requests.find(value => !value.done && value.url.endsWith(suffix)) },
    posts() { return requests.filter(value => value.method === 'POST' && !value.url.endsWith('/auth/wechat')) },
    success(request, value, statusCode = 200) { assert.ok(request && !request.done, 'current request required'); request.done = true; request.success({ statusCode, data: { success: true, data: value } }) },
    fail(request) { assert.ok(request && !request.done); request.done = true; request.fail({ errMsg: 'private-upstream-error' }) },
    async drain() {
      for (let i = 0; i < 25; i++) {
        await flush()
        const pending = requests.filter(value => !value.done && value.method === 'GET' && !value.url.includes('/preferences/requests/'))
        if (!pending.length) break
        for (const request of pending) {
          const route = new URL(request.url).pathname
          const sample = route === '/health' ? { mode: 'local-development', wechatConfigured: true, readOnly: true } : route === '/v1/pets' ? [{ petId: 'pet_1', name: '真实小安' }, { petId: 'pet_2', name: '第二台猫' }] : route.endsWith('/status') ? device() : route.endsWith('/preferences') ? values : null
          assert.ok(sample, 'unhandled GET ' + route); h.success(request, sample, route.endsWith('/preferences') ? options.preferencesStatus || 200 : 200)
        }
      }
      await flush()
    },
    async authenticate(page) {
      const enabled = page.useRealDevice(); await h.drain(); await enabled
      const login = page.liveLogin(); logins.at(-1).success({ code: 'synthetic-wx-code' }); await flush()
      h.success(h.pending('/auth/wechat'), { accessToken: 'synthetic-private-token-not-stored', userId: 'user_1', expiresAt: '2026-11-01T01:00:00Z' }); await h.drain(); await login
      const selected = page.liveSelect({ detail: { petId: 'pet_1' } }); await h.drain(); await selected
      return page
    },
    async boot() { const page = h.page(); await page.onShow(); return h.authenticate(page) },
    async open(page, kind = 'settings') {
      const task = kind === 'settings' ? page.saveSettings() : page.submitFeedback(); await flush()
      const modal = modals.at(-1); assert.ok(modal, 'explicit native confirmation required')
      return { task, modal, async accept() { const completion = modal.success({ confirm: true }); await h.drain(); return { completion, request: h.posts().at(-1) } }, async cancel() { await modal.success({ confirm: false }); await task } }
    },
    async save(page, kind = 'settings', status = 'recorded', steps) {
      const confirmation = await h.open(page, kind), sent = await confirmation.accept()
      assert.ok(sent.request && !sent.request.done, 'one explicit command expected')
      h.success(sent.request, receipt(sent.request, status, steps)); await h.drain(); await sent.completion; await confirmation.task
      return sent.request
    },
    async advance(ms) { now += ms; for (const [id, timer] of [...timers]) if (timer.at <= now) { timers.delete(id); timer.fn() }; await flush() }
  }
  return h
}
const cases = {
  async 'original preview keeps local settings and feedback with no real requests'() {
    const h = harness(), page = h.page(); await page.onShow()
    assert.equal(page.data.volume, 62); page.inputName(input('预览修改')); await page.saveSettings()
    page.inputFeedback(input('预览反馈')); await page.submitFeedback()
    assert.equal(h.calls.filter(call => call.name === 'updateSettings').length, 1); assert.equal(h.calls.filter(call => call.name === 'submitFeedback').length, 1)
    assert.equal(h.requests.length, 0); assert.equal(h.logins.length, 0); page.onHide()
  },
  async 'real GET uses shared login and distinguishes 48 saved volume from 150 actual output'() {
    const h = harness(), page = await h.boot()
    assert.equal(page.data.liveMode, true); assert.equal(page.data.name, '真实小安'); assert.equal(page.data.volume, 48); assert.equal(page.data.followUpSeconds, 25)
    assert.equal(page.data.realStoredLabel, '48%'); assert.equal(page.data.realOutputLabel, '150%'); assert.equal(h.logins.length, 1); assert.equal(h.posts().length, 0)
    assert.doesNotMatch(JSON.stringify(page.data), /synthetic-private-token/); page.onHide()
  },
  async 'closed server flags block save and feedback direct handlers without falling back to preview'() {
    const h = harness({ enabled: false }), page = await h.boot(), before = h.calls.length
    page.inputName(input('绝不能保存')); page.onVolumeChange(input(10)); page.onFollowChange(input(100)); page.inputFeedback(input('仅草稿'))
    await page.saveSettings(); await page.submitFeedback(); await page.requestRealPreferences('settings'); await page.requestRealPreferences('feedback')
    assert.equal(h.posts().length, 0); assert.equal(h.modals.length, 0); assert.equal(h.calls.length, before); assert.equal(page.data.volume, 48); page.onHide()
  },
  async 'missing preferences API stays disabled and never displays preview defaults'() {
    const h = harness({ preferencesStatus: 503 }), page = await h.boot()
    assert.equal(page.data.name, null); assert.equal(page.data.volume, null); assert.equal(page.data.followUpSeconds, null); assert.equal(page.data.realOutputLabel, '未知')
    assert.equal(page.data.canSaveReal, false); assert.equal(page.data.canFeedbackReal, false); assert.match(page.data.preferencesError, /不可用|暂停/)
    assert.match(page.data.preferencesReason, /读取失败.*刷新/); assert.doesNotMatch(page.data.preferencesReason, /尚未部署/); page.onHide()
  },
  async 'unavailable output stays unknown and disables only volume rather than inventing zero'() {
    const h = harness({ snapshot: { ...snapshot(), outputVolume: null, outputMuted: null, outputVolumeAvailable: false, canChangeVolume: false } }), page = await h.boot()
    assert.equal(page.data.realOutputLabel, '未知'); assert.equal(page.data.realMutedLabel, '未提供静音状态'); assert.equal(page.data.canChangeVolume, false)
    page.onVolumeChange(input(0)); assert.equal(page.data.volume, 48); assert.equal(page.data.canChangeName, true); assert.equal(page.data.canChangeFollowUp, true); page.onHide()
  },
  async 'preview and real drafts are isolated across explicit mode switches'() {
    const h = harness(), page = h.page(); await page.onShow(); page.inputName(input('演示草稿')); page.onVolumeChange(input(31)); page.inputFeedback(input('演示反馈草稿'))
    await h.authenticate(page); page.inputName(input('真实草稿')); page.inputFeedback(input('真实反馈草稿'))
    await page.showPreview(); assert.equal(page.data.name, '演示草稿'); assert.equal(page.data.volume, 31); assert.equal(page.data.feedbackContent, '演示反馈草稿'); assert.equal(page.data.settingsDirty, true)
    assert.equal(h.posts().length, 0); page.onHide()
  },
  async 'all three danger buttons and forged danger confirmation remain inert in real mode'() {
    const h = harness(), page = await h.boot(), before = h.calls.length
    for (const kind of ['memory', 'unbind', 'reset']) { page.showDanger({ currentTarget: { dataset: { kind } } }); page.setData({ dangerKind: kind, danger: {} }); await page.confirmDanger() }
    assert.equal(h.calls.length, before); assert.equal(h.posts().length, 0); assert.equal(h.modals.length, 0)
    page.inputApiKey(input('must-not-retain')); assert.equal(page.data.apiKey, ''); page.onHide()
  },
  async 'sliding and editing only change drafts; confirmed command contains only changed fields'() {
    const h = harness(), page = await h.boot(); page.onVolumeChange(input(0)); page.inputName(input('真实新名字'))
    assert.equal(h.posts().length, 0); assert.equal(page.data.volume, 0)
    const request = await h.save(page)
    assert.deepEqual(plain(request.data.changes), { name: '真实新名字', volume: 0 }); assert.equal(request.method, 'POST'); assert.match(request.url, /\/preferences\/commands$/)
    assert.match(h.modals.at(-1).content, /真实扬声器/); assert.match(h.modals.at(-1).content, /分两步/); page.onHide()
  },
  async 'actual volume 150 is not silently included in a name-only save'() {
    const h = harness(), page = await h.boot(); page.inputName(input('只改名'))
    const request = await h.save(page); assert.deepEqual(plain(request.data.changes), { name: '只改名' }); assert.equal(page.data.realOutputLabel, '150%'); page.onHide()
  },
  async 'explicitly selecting stored 48 still requests actual 150 output to become 48'() {
    const h = harness(), page = await h.boot(); page.onVolumeChange(input(48))
    assert.equal(page.data.settingsDirty, true); assert.equal(page.data.canSaveReal, true)
    const request = await h.save(page); assert.deepEqual(plain(request.data.changes), { volume: 48 }); page.onHide()
  },
  async 'poll and manual refresh preserve dirty name volume and feedback form'() {
    const h = harness(), page = await h.boot(); page.inputName(input('保留编辑')); page.onVolumeChange(input(28)); page.inputFeedback(input('保留反馈\n第二行'))
    h.values({ ...snapshot(), name: '远端后来名字', storedVolume: 90, followUpSeconds: 75 }); const read = page.refreshPreferences(); await h.drain(); await read
    assert.equal(page.data.name, '保留编辑'); assert.equal(page.data.volume, 28); assert.equal(page.data.feedbackContent, '保留反馈\n第二行'); assert.equal(page.data.followUpSeconds, 75)
    assert.equal(page.data.realStoredLabel, '90%'); assert.equal(h.posts().length, 0); page.onHide()
  },
  async 'name accepts 20 astral Unicode scalars intact and rejects 21 without a modal'() {
    const h = harness(), page = await h.boot(); const name = '猫'.repeat(19) + '🦊'; page.inputName(input(name))
    const request = await h.save(page); assert.equal(request.data.changes.name, name)
    page.inputName(input('🦊'.repeat(21))); const count = h.modals.length; await page.saveSettings(); assert.equal(h.modals.length, count); assert.equal(h.posts().length, 1); assert.ok(page.data.settingsDraftError); page.onHide()
  },
  async 'real feedback supports all five categories and preserves LF TAB and 500 Unicode scalars'() {
    const h = harness(), page = await h.boot(); const content = '🦊'.repeat(496) + '\n\t中文'
    page.onFeedbackType(input(4)); page.inputFeedback(input(content)); assert.equal(page.data.feedbackRemaining, 0)
    const request = await h.save(page, 'feedback'); assert.equal(request.data.category, 'other'); assert.equal(request.data.content, content)
    assert.match(page.data.feedbackMessage, /保存在设备本地/); assert.match(page.data.feedbackMessage, /不代表/); assert.equal(page.data.feedbackContent, '')
    for (const [index, category] of ['device', 'dialog', 'motion', 'account', 'other'].entries()) { page.onFeedbackType(input(index)); assert.equal(page.realPreferences.getState().feedbackDraft.category, category) }
    page.onHide()
  },
  async 'invalid feedback and unsupported controls never reach confirmation or POST'() {
    const h = harness(), page = await h.boot()
    for (const text of ['单', '汉'.repeat(501), '坏\u0000字符', '坏\u202e字符', '\ud800坏']) { page.inputFeedback(input(text)); await page.submitFeedback(); assert.equal(h.posts().length, 0); assert.equal(page.data.canFeedbackReal, false) }
    assert.equal(h.modals.length, 0); page.onHide()
  },
  async 'feedback retains meaningful trailing newline instead of silently trimming its contract length'() {
    const h = harness(), page = await h.boot(); page.inputFeedback(input('A\n')); assert.equal(page.data.canFeedbackReal, true)
    const request = await h.save(page, 'feedback'); assert.equal(request.data.content, 'A\n'); page.onHide()
  },
  async 'native cancellation has a visible unsent notice and no command'() {
    const h = harness(), page = await h.boot(); page.inputName(input('取消的名字')); const confirm = await h.open(page); await confirm.cancel()
    assert.equal(h.posts().length, 0); assert.match(page.data.preferencesError, /未发送.*取消/); page.onHide()
  },
  async 'both real preference kinds meet native four-character modal limits then fresh-verify before one POST'() {
    for (const kind of ['settings', 'feedback']) {
      const h = harness({ strictModal: true }), page = await h.boot()
      page.inputName(input('待保存名称')); page.inputFeedback(input('待保存反馈'))
      const task = page.requestRealPreferences(kind); await flush(); assert.equal(h.modals.length, 1)
      assert.equal(h.posts().length, 0)
      const completion = h.modals[0].success({ confirm: true }); await flush()
      assert.ok(h.pending('/preferences')); assert.equal(h.posts().length, 0)
      await h.drain(); const post = h.posts().at(-1); assert.ok(post); assert.equal(post.data.kind, kind)
      h.success(post, receipt(post)); await completion; await task; await h.drain(); assert.equal(h.posts().length, 1); page.onHide()
    }
  },
  async 'native preferences modal failures visibly stay unsent and allow a new explicit confirmation'() {
    for (const kind of ['settings', 'feedback']) {
      const h = harness(), page = await h.boot(); page.inputName(input('待保存名称')); page.inputFeedback(input('待保存反馈'))
      const task = page.requestRealPreferences(kind); await flush(); h.modals[0].fail({ errMsg: 'raw-private-native-failure' }); await task
      assert.match(page.data.preferencesError, /请求未发送.*确认弹窗.*失败/); assert.doesNotMatch(JSON.stringify(page.data), /raw-private/)
      assert.equal(page.data.preferencesConfirm, false); assert.equal(page.realPreferences.getState().confirmPending, false)
      assert.equal(h.posts().length, 0); assert.equal(h.storage.size, 0)
      const retry = page.requestRealPreferences(kind); await flush(); assert.equal(h.modals.length, 2)
      const completion = h.modals[1].success({ confirm: true }); await h.drain(); const post = h.posts().at(-1)
      assert.ok(post); h.success(post, receipt(post)); await completion; await retry; await h.drain(); assert.equal(h.posts().length, 1); page.onHide()
    }
  },
  async 'synchronous preference modal failure settles with no persistent marker or stuck confirmation'() {
    for (const kind of ['settings', 'feedback']) {
      const h = harness({ throwModal: true }), page = await h.boot(); page.inputName(input('待保存名称')); page.inputFeedback(input('待保存反馈'))
      await page.requestRealPreferences(kind)
      assert.match(page.data.preferencesError, /请求未发送.*确认弹窗.*失败/)
      assert.equal(page.data.preferencesConfirm, false); assert.equal(page.realPreferences.getState().confirmPending, false)
      assert.equal(h.posts().length, 0); assert.equal(h.storage.size, 0); page.onHide()
    }
  },
  async 'late old preferences cancel confirm and fail callbacks cannot change a new visit modal'() {
    for (const callback of ['cancel', 'confirm', 'fail']) {
      const h = harness(), page = await h.boot(); page.inputName(input('旧名称'))
      const old = await h.open(page); page.onHide(); page.onShow(); await h.drain(); page.inputFeedback(input('新反馈'))
      const next = await h.open(page, 'feedback'), target = page.preferenceConfirmation, before = h.writes.length
      if (callback === 'fail') old.modal.fail({ errMsg: 'raw-private-late-failure' }); else await old.modal.success({ confirm: callback === 'confirm' })
      await old.task; assert.equal(page.preferenceConfirmation, target); assert.equal(h.writes.length, before)
      assert.equal(page.data.preferencesConfirm, true); assert.equal(h.posts().length, 0)
      const sent = await next.accept(); assert.ok(sent.request); assert.equal(sent.request.data.kind, 'feedback')
      h.success(sent.request, receipt(sent.request)); await sent.completion; await next.task; await h.drain(); assert.equal(h.posts().length, 1); page.onHide()
    }
  },
  async 'unchanged input and ordinary polls preserve settings and feedback native confirmations'() {
    for (const kind of ['settings', 'feedback']) {
      const h = harness(), page = await h.boot(); page.inputName(input('相同名称')); page.inputFeedback(input('相同反馈'))
      const confirm = await h.open(page, kind), reads = h.requests.filter(request => request.url.endsWith('/preferences')).length
      if (kind === 'settings') page.inputName(input('相同名称')); else page.inputFeedback(input('相同反馈'))
      await h.advance(40000); await h.drain()
      assert.equal(page.data.preferencesConfirm, true); assert.equal(h.requests.filter(request => request.url.endsWith('/preferences')).length, reads)
      const sent = await confirm.accept(); assert.ok(sent.request); h.success(sent.request, receipt(sent.request))
      await sent.completion; await confirm.task; await h.drain(); assert.equal(h.posts().length, 1); page.onHide()
    }
  },
  async 'a pending preferences GET disables new confirmations but preserves editable drafts until verification settles'() {
    const h = harness(), page = await h.boot(); page.inputName(input('保留名称')); page.inputFeedback(input('保留反馈'))
    const read = page.refreshPreferences(); await flush()
    assert.equal(page.data.canSaveReal, false); assert.equal(page.data.canFeedbackReal, false); assert.equal(page.data.canChangeName, true)
    assert.match(page.data.preferencesStatus, /正在核对/); await page.saveSettings(); await page.submitFeedback(); assert.equal(h.modals.length, 0)
    await h.drain(); await read; assert.equal(page.data.canSaveReal, true); assert.equal(page.data.canFeedbackReal, true)
    assert.equal(page.data.name, '保留名称'); assert.equal(page.data.feedbackContent, '保留反馈'); assert.equal(h.posts().length, 0); page.onHide()
  },
  async 'editing a setting during native confirmation revokes old permission and shows unsent reason'() {
    const h = harness(), page = await h.boot(); page.inputName(input('旧名')); const confirm = await h.open(page)
    page.inputName(input('新名')); await confirm.modal.success({ confirm: true }); await confirm.task
    assert.equal(h.posts().length, 0); assert.match(page.data.preferencesError, /未发送.*变化/); assert.equal(page.data.name, '新名'); page.onHide()
  },
  async 'feedback body and category changes each invalidate a pending confirmation'() {
    const h = harness(), page = await h.boot(); page.inputFeedback(input('原反馈')); const first = await h.open(page, 'feedback')
    page.onFeedbackType(input(2)); await first.modal.success({ confirm: true }); await first.task; assert.equal(h.posts().length, 0)
    const second = await h.open(page, 'feedback'); page.inputFeedback(input('新反馈')); await second.modal.success({ confirm: true }); await second.task
    assert.equal(h.posts().length, 0); assert.match(page.data.preferencesError, /未发送/); page.onHide()
  },
  async 'hide and return invalidate the old modal without losing the real draft or writing'() {
    const h = harness(), page = await h.boot(); page.inputName(input('离页草稿')); const confirm = await h.open(page)
    page.onHide(); const writes = h.writes.length; await confirm.modal.success({ confirm: true }); await confirm.task
    assert.equal(h.writes.length, writes); const show = page.onShow(); await h.drain(); await show
    assert.equal(h.posts().length, 0); assert.match(page.data.preferencesError, /未发送|撤销/); assert.equal(page.data.name, '离页草稿'); page.onHide()
  },
  async 'switching authorized device cancels old confirmation and clears its private drafts'() {
    const h = harness(), page = await h.boot(); page.inputName(input('不应给第二台')); page.inputFeedback(input('第一台私有反馈')); const confirm = await h.open(page)
    h.values({ ...snapshot(), name: '第二台名字' }); const changed = page.liveSelect({ detail: { petId: 'pet_2' } }); await h.drain(); await changed
    await confirm.modal.success({ confirm: true }); await confirm.task
    assert.equal(h.posts().length, 0); assert.equal(page.data.name, '第二台名字'); assert.equal(page.data.feedbackContent, ''); assert.match(page.data.preferencesError, /未发送/); page.onHide()
  },
  async 'permission refresh while modal is open revokes confirmation without auto-saving'() {
    const h = harness(), page = await h.boot(); page.inputName(input('禁止后不提交')); const confirm = await h.open(page)
    h.values(snapshot(false)); const read = page.refreshPreferences(); await h.drain(); await read; await confirm.modal.success({ confirm: true }); await confirm.task
    assert.equal(h.posts().length, 0); assert.equal(page.data.canSaveReal, false); assert.match(page.data.preferencesError, /未发送/); page.onHide()
  },
  async 'external parameter changes during confirmation revoke its old snapshot without discarding dirty edits'() {
    const h = harness(), page = await h.boot(); page.inputName(input('仍保留的名字')); const confirm = await h.open(page)
    h.values({ ...snapshot(), outputVolume: 120, followUpSeconds: 60 }); const read = page.refreshPreferences(); await h.drain(); await read
    await confirm.modal.success({ confirm: true }); await confirm.task
    assert.equal(h.posts().length, 0); assert.equal(page.data.name, '仍保留的名字'); assert.equal(page.data.realOutputLabel, '120%'); assert.match(page.data.preferencesError, /未发送.*变化/); page.onHide()
  },
  async 'fresh preflight refusal after confirm does not send or claim recorded'() {
    const h = harness(), page = await h.boot(); page.inputName(input('最新权限拒绝')); const confirm = await h.open(page)
    h.values(snapshot(false)); const completion = confirm.modal.success({ confirm: true }); await h.drain(); await completion; await confirm.task
    assert.equal(h.posts().length, 0); assert.match(page.data.settingsMessage, /未发送|不允许|核验/); assert.equal(page.data.settingsReceiptStatus, ''); page.onHide()
  },
  async 'confirmed settings preserve edits made while its command is in flight'() {
    const h = harness(), page = await h.boot(); page.inputName(input('提交名')); const confirm = await h.open(page), sent = await confirm.accept()
    page.inputName(input('保存期间新名')); page.onVolumeChange(input(12)); h.success(sent.request, receipt(sent.request)); await h.drain(); await sent.completion; await confirm.task
    assert.equal(page.data.name, '保存期间新名'); assert.equal(page.data.volume, 12); assert.equal(page.data.settingsDirty, true); page.onHide()
  },
  async 'recorded feedback only clears the exact submitted snapshot not newer text or category'() {
    const h = harness(), page = await h.boot(); page.inputFeedback(input('提交内容')); const confirm = await h.open(page, 'feedback'), sent = await confirm.accept()
    page.inputFeedback(input('新编辑内容')); page.onFeedbackType(input(3)); h.success(sent.request, receipt(sent.request)); await h.drain(); await sent.completion; await confirm.task
    assert.equal(page.data.feedbackContent, '新编辑内容'); assert.equal(page.data.feedbackIndex, 3); page.onHide()
  },
  async 'non-atomic partial result shows per-step truth and cannot automatically resend'() {
    const h = harness(), page = await h.boot(); page.inputName(input('已生效名字')); page.onFollowChange(input(80))
    const request = await h.save(page, 'settings', 'partially_applied', [{ step: 'pet_settings', status: 'applied' }, { step: 'follow_up', status: 'not_sent' }])
    assert.match(page.data.settingsMessage, /部分/); assert.deepEqual(plain(page.data.settingsSteps), [{ name: '名字与音量', status: '已应用' }, { name: '连续聊天时间', status: '已确认未派发' }])
    assert.equal(h.posts().length, 1); assert.equal(page.data.canSaveReal, false); assert.equal(request.data.changes.followUpSeconds, 80); page.onHide()
  },
  async 'partial result needs manual fresh GET and a new explicit confirmation of remaining differences only'() {
    const h = harness(), page = await h.boot(); page.inputName(input('已应用名称')); page.onFollowChange(input(80))
    await h.save(page, 'settings', 'partially_applied', [{ step: 'pet_settings', status: 'applied' }, { step: 'follow_up', status: 'not_sent' }])
    const modals = h.modals.length; await page.saveSettings(); assert.equal(h.modals.length, modals); assert.equal(h.posts().length, 1)
    h.values({ ...snapshot(), name: '已应用名称' }); const read = page.refreshPreferences(); await h.drain(); await read
    const request = await h.save(page); assert.deepEqual(plain(request.data.changes), { followUpSeconds: 80 }); assert.equal(h.posts().length, 2); page.onHide()
  },
  async 'unknown settings only GET original receipt; feedback lane remains independent'() {
    const h = harness(), page = await h.boot(); page.inputName(input('未知保存')); const request = await h.save(page, 'settings', 'unknown')
    assert.equal(page.data.canSaveReal, false); assert.equal(page.data.canCheckSettings, true); assert.match(page.data.settingsMessage, /未知|核对/)
    await page.saveSettings(); assert.equal(h.posts().length, 1)
    const check = page.checkPreferenceReceipt({ currentTarget: { dataset: { kind: 'settings' } } }); await flush()
    const get = h.pending('/preferences/requests/' + request.data.requestId); assert.equal(get.method, 'GET'); h.success(get, {}, 404); await check
    assert.equal(page.data.canCheckSettings, true); assert.equal(page.data.canSaveReal, false); assert.equal(h.posts().length, 1)
    page.inputFeedback(input('独立反馈')); await h.save(page, 'feedback'); assert.equal(h.posts().length, 2); assert.equal(page.data.canCheckSettings, true); page.onHide()
  },
  async 'timeout keeps one durable marker without plaintext and never retries POST'() {
    const h = harness(), page = await h.boot(); const secretName = '名字只在内存'; page.inputName(input(secretName)); const confirm = await h.open(page), sent = await confirm.accept()
    h.fail(sent.request); await sent.completion; await confirm.task; assert.equal(page.data.canCheckSettings, true)
    const stored = JSON.stringify([...h.storage]); assert.doesNotMatch(stored, /名字只在内存|synthetic-private-token|changes|content|accessToken/)
    assert.match(stored, /requestId/); assert.match(page.data.settingsMessage, /未确认|未知/); page.onHide()
  },
  async 'corrupt pending storage locks both submit lanes while preserving readable real values'() {
    const h = harness(), page = await h.boot(); h.storage.set('aiCatPreferencesPendingV1', { version: 1, settings: { invalid: true }, feedback: null })
    const read = page.realPreferences.refresh(); await h.drain(); await read
    page.inputName(input('不可提交')); page.inputFeedback(input('不可提交反馈')); await page.saveSettings(); await page.submitFeedback()
    assert.equal(page.data.canSaveReal, false); assert.equal(page.data.canFeedbackReal, false); assert.equal(h.posts().length, 0); assert.equal(page.data.realOutputLabel, '150%')
    assert.match(page.data.preferencesError, /存储/); page.onHide()
  },
  async 'switching away from unresolved settings keeps foreign-scope lock and hides old IDs'() {
    const h = harness(), page = await h.boot(); page.inputName(input('第一台未确认')); const request = await h.save(page, 'settings', 'unknown')
    const selected = page.liveSelect({ detail: { petId: 'pet_2' } }); await h.drain(); await selected
    assert.equal(page.data.realSettingsBlocked, true); assert.equal(page.data.canCheckSettings, false); assert.equal(page.data.canSaveReal, false)
    assert.match(page.data.settingsMessage, /另一个身份或设备/); assert.doesNotMatch(JSON.stringify(page.data), new RegExp(request.data.requestId)); assert.equal(h.posts().length, 1); page.onHide()
  },
  async 'same-scope reentry and fresh page retain unknown marker rather than resend'() {
    const h = harness(), page = await h.boot(); page.inputFeedback(input('未确认反馈')); const request = await h.save(page, 'feedback', 'unknown'); page.onHide()
    const next = h.page(), show = next.onShow(); await h.drain(); await show
    assert.equal(h.logins.length, 1); assert.equal(next.data.canCheckFeedback, true); assert.equal(next.data.canFeedbackReal, false)
    const check = next.checkPreferenceReceipt({ currentTarget: { dataset: { kind: 'feedback' } } }); await flush(); const get = h.pending('/preferences/requests/' + request.data.requestId)
    h.success(get, receipt(request, 'unknown')); await check; assert.equal(h.posts().length, 1); next.onHide()
  },
  async 'recorded response cannot hide a subsequent timeout or current pending request'() {
    const h = harness(), page = await h.boot(); page.inputFeedback(input('首次反馈')); await h.save(page, 'feedback'); assert.equal(page.data.feedbackReceiptStatus, 'recorded')
    page.inputFeedback(input('第二次反馈')); const confirm = await h.open(page, 'feedback'), sent = await confirm.accept()
    assert.equal(page.data.feedbackReceiptStatus, ''); assert.match(page.data.feedbackMessage, /尚未确认/)
    h.fail(sent.request); await sent.completion; await confirm.task; assert.equal(page.data.feedbackReceiptStatus, ''); assert.match(page.data.feedbackMessage, /未确认|未知/); assert.doesNotMatch(page.data.feedbackMessage, /^反馈已保存/); page.onHide()
  },
  async 'old recorded settings cannot cover next pending timeout or preflight refusal'() {
    const h = harness(), page = await h.boot(); page.inputName(input('首次成功')); await h.save(page); assert.equal(page.data.settingsReceiptStatus, 'recorded')
    page.inputName(input('后来核验失败')); h.values(snapshot(false)); const denied = await h.open(page), deny = denied.modal.success({ confirm: true }); await h.drain(); await deny; await denied.task
    assert.equal(page.data.settingsReceiptStatus, ''); assert.match(page.data.settingsMessage, /未发送|不允许/); assert.equal(h.posts().length, 1)
    h.values(snapshot()); const read = page.refreshPreferences(); await h.drain(); await read
    page.inputName(input('再一次超时')); const confirm = await h.open(page), sent = await confirm.accept()
    assert.equal(page.data.settingsReceiptStatus, ''); assert.match(page.data.settingsMessage, /尚未确认/)
    h.fail(sent.request); await sent.completion; await confirm.task; assert.equal(page.data.settingsReceiptStatus, ''); assert.match(page.data.settingsMessage, /未确认|未知/); page.onHide()
  },
  async 'real logout revokes shared identity not the preview session or pending ledger'() {
    const h = harness(), page = await h.boot(); page.inputName(input('退出前未知')); await h.save(page, 'settings', 'unknown')
    const before = JSON.stringify([...h.storage]); const logout = page.logout(); await flush(); const request = h.requests.find(value => !value.done && value.url.endsWith('/auth/logout'))
    if (request) h.success(request, {}); await logout
    assert.equal(h.session().getState().signedIn, false); assert.equal(page.data.pet, null); assert.equal(JSON.stringify([...h.storage]), before); assert.equal(h.navigation.length, 0); page.onHide()
  },
  async 'authorization failure clears real controls and drafts without leaving stale verified values'() {
    const h = harness(), page = await h.boot(); page.inputName(input('私人编辑')); const refresh = page.realPreferences.refresh(); await flush(); h.success(h.pending('/preferences'), {}, 401); await refresh
    assert.equal(h.session().getState().signedIn, false); assert.equal(page.data.name, null); assert.equal(page.data.canSaveReal, false); assert.equal(page.data.canFeedbackReal, false); page.onHide()
  },
  async 'unchanged real callbacks avoid repeated setData while preserving dirty text'() {
    const h = harness(), page = await h.boot(); page.inputFeedback(input('正在编辑')); const before = h.writes.length
    for (let i = 0; i < 5; i++) page.renderPreferences(page.realPreferences.getState())
    assert.equal(h.writes.length, before); assert.equal(page.data.feedbackContent, '正在编辑'); page.onHide()
  },
  async 'GET loading and actual-output changes do not resend dirty input text over the render bridge'() {
    const h = harness(), page = await h.boot(); page.inputName(input('输入中的名字')); page.inputFeedback(input('多行草稿\n保持光标')); const before = h.writes.length
    h.values({ ...snapshot(), outputVolume: 129 }); const read = page.refreshPreferences(); await h.drain(); await read
    const updates = h.writes.slice(before); assert.ok(updates.length); assert.ok(updates.every(patch => !Object.hasOwn(patch, 'name') && !Object.hasOwn(patch, 'feedbackContent')))
    assert.equal(page.data.realOutputLabel, '129%'); page.onHide()
  },
  async 'profile registers only existing presentation panel and real fields retain explicit safety labels'() {
    const config = JSON.parse(fs.readFileSync(path.join(root, 'pages/profile/index.json'), 'utf8')), view = fs.readFileSync(path.join(root, 'pages/profile/index.wxml'), 'utf8')
    assert.equal(config.usingComponents['live-product-panel'], '/components/live-product-panel/index'); assert.match(view, /liveDevice="\{\{liveMode\}\}"/)
    assert.match(view, /wx:if="\{\{!liveMode\}\}" class="field"><text class="field-label">访问密钥/)
    assert.equal((view.match(/disabled="\{\{liveMode \|\| processingDanger\}\}"/g) || []).length, 3)
    for (const text of ['实际扬声器', '已保存偏好', '尚未接入', '不代表客服已收到', '核对原设置回执', '核对原反馈回执']) assert.ok(view.includes(text), text)
    assert.match(view, /preferencesKnown \? '真实资料已核验/); assert.match(view, /保存回执不代表实际发声或客服处理/)
    assert.doesNotMatch(view, /开发接入不等于已部署/)
    assert.match(view, /maxlength="\{\{liveMode \? 40 : 20\}\}"/); assert.match(view, /maxlength="\{\{liveMode \? 1000 : 500\}\}"/)
  }
}
;(async () => {
  let passed = 0
  for (const [name, run] of Object.entries(cases)) {
    try { await run(); passed++; console.log('PASS ' + name) }
    catch (error) { console.error('FAIL ' + name); console.error(error); process.exitCode = 1 }
  }
  console.log(`live-preferences-pages-smoke: ${passed}/${Object.keys(cases).length} passed`)
})()
