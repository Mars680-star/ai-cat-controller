// Actual page, shared identity, controller and HTTP boundary; all wx I/O is injected.
// No real credentials, network, persistent data or physical actions are used.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..'), UTC = '2026-11-01T00:00:00Z'
const plain = value => JSON.parse(JSON.stringify(value))
const flush = async () => { for (let index = 0; index < 65; index++) await Promise.resolve() }
const intimacyData = () => ({ points: 23, level: 1, levelName: '逐渐熟悉', minPoints: 20, nextPoints: 50, progressPercent: 10, address: '真实的朋友', currentUnlocks: ['tail_wag'], nextUnlocks: ['greeting_combo'] })
const historyItem = () => ({ eventType: 'touch', label: '真实抚摸', pointsDelta: 1, pointsAfter: 23, createdAt: UTC })
const dashboard = () => ({ interactionsEnabled: true, petName: '真实小安', personality: { personalityId: 'gentle_companion', name: '真实温柔陪伴者', description: '真实描述', languageStyle: '真实表达', voiceName: '真实音色' }, intimacy: intimacyData(), unlockedActionIds: ['head_nod', 'head_shake', 'tail_wag'], recentInteractions: [historyItem()], historyScope: 'latest_30', today: { interactionCount: null, pointsGained: null }, sampledAt: UTC })
const intimacy = () => ({ interactionsEnabled: true, intimacy: intimacyData(), dailyGrowthCap: 30, dailyCheckInCompleted: false, rules: ['daily_check_in', 'touch', 'completed_task', 'ignored_greeting', 'valid_dialog'].map((eventType, index) => ({ eventType, label: eventType, points: [3, 1, 5, -1, 2][index], maxPerDay: index === 0 ? 1 : 5 })), history: [historyItem()], historyScope: 'latest_30', sampledAt: UTC })
const growth = () => ({ enabled: true, attributes: ['curiosity', 'empathy', 'knowledge', 'energy', 'mischief', 'discipline'].map((attributeId, index) => ({ attributeId, label: '真实属性' + index, tendency: '普通', value: 10.25 + index })), activeTags: [], tagHistory: [], recentEvents: [], behavior: { basePersonalityId: 'gentle_companion', basePersonalityName: '真实温柔陪伴者', intimacyLevel: 1, directives: [], activeTagNames: [] }, sampledAt: UTC })
const device = (batteryPercent = 0) => ({ readOnly: true, sampledAt: UTC, health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' }, device: { connected: true, deviceSerial: '7c2****a128', networkName: null, batteryPercent, batteryState: 'discharging', batteryVoltage: 3.7, chargerOnline: false, batteryPresent: true, lastSeenAt: null }, dialog: { state: 'offline', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false } })
const receipt = (request, status = 'recorded') => ({ receiptId: 'receipt_1', ...request.data, status, pointsDelta: status === 'recorded' ? 1 : null, pointsAfter: status === 'recorded' ? 24 : null, duplicate: status === 'recorded' ? false : null, celebrationScheduled: status === 'recorded' ? false : null, updatedAt: UTC })
const event = eventType => ({ currentTarget: { dataset: { event: eventType } } })

function harness(options = {}) {
  const requests = [], logins = [], modals = [], writes = [], calls = [], logs = [], modules = new Map(), timers = new Map()
  let timerId = 0, now = Date.parse(UTC), storageWrites = 0, stopped = 0
  const demoPet = { petId: 'preview', name: '演示宠物', personalityName: '演示性格', batteryPercent: 82, batteryState: 'discharging', connectionState: 'online' }
  const demoIntimacy = { points: 286, today: { met: false, taskCompleted: false }, recent: [] }
  const demoGrowth = { enabled: true, attributes: [], tags: ['演示标签'], portraits: [], recent: [], debugValuesVisible: true }
  const service = Object.fromEntries(Object.entries({ getBoundPet: demoPet, getDashboard: { pet: demoPet, intimacy: {}, today: {}, recent: [] }, getIntimacy: demoIntimacy, getGrowth: demoGrowth }).map(([name, value]) => [name, async () => { calls.push(name); return plain(value) }]))
  for (const name of ['recordInteraction', 'generateGrowth']) service[name] = async () => { calls.push(name); return { pointsAdded: 1, growth: demoGrowth, processed: 20 } }
  const wx = { login(value) { logins.push(value) }, request(value) { requests.push(value); return { abort() { if (!value.done) { value.done = true; value.fail({ errMsg: 'aborted private-secret' }) } } } },
    getStorageSync() { return true }, setStorageSync() { storageWrites++ }, setStorage() { storageWrites++ },
    showModal(value) {
      if (options.throwModal) throw Error('raw-private-native-error')
      if (options.strictModal && [value.confirmText, value.cancelText].some(text => text !== undefined && Array.from(text).length > 4)) {
        value.fail({ errMsg: 'showModal:fail confirmText length should not exceed 4 characters' }); return
      }
      modals.push(value)
    }, showToast() {}, stopPullDownRefresh() { stopped++ }, redirectTo() {}, reLaunch() {}, navigateTo() {} }
  class ClockDate extends Date { constructor(...args) { if (args.length) super(...args); else super(now) } static now() { return now } }
  const context = vm.createContext({ wx, Date: ClockDate,
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, at: now + Math.max(1, delay) }); return id }, clearTimeout(id) { timers.delete(id) },
    setInterval(fn, delay) { const id = ++timerId; timers.set(id, { fn, at: now + delay }); return id }, clearInterval(id) { timers.delete(id) },
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
  const h = { requests, logins, modals, writes, calls, timers, logs,
    session: () => load('services/live-device-session.ts').exports.getLiveDeviceSession(),
    page(name) { const definition = load(`pages/${name}/index.ts`).definition, page = { ...definition, data: plain(definition.data) }; page.setData = patch => { Object.assign(page.data, plain(patch)); writes.push({ page: name, patch: plain(patch) }) }; return page },
    pending(suffix) { return requests.find(value => !value.done && value.url.endsWith(suffix)) },
    posts: () => requests.filter(value => value.method === 'POST' && value.url.endsWith('/interactions')),
    success(request, value, statusCode = 200) { assert.ok(request && !request.done, 'a current request is required'); request.done = true; request.success({ statusCode, data: { success: true, data: value } }) },
    fail(request) { request.done = true; request.fail({ errMsg: 'private raw failure' }) },
    async drain(overrides = {}) {
      for (let index = 0; index < 15; index++) {
        await flush()
        const pending = requests.filter(value => !value.done && value.method === 'GET')
        if (!pending.length) break
        for (const request of pending) {
          const route = new URL(request.url).pathname.split('/').at(-1)
          const samples = { health: { mode: 'local-development', wechatConfigured: true, readOnly: true }, pets: [{ petId: 'pet_1', name: '真实小安' }, { petId: 'pet_2', name: '另一台真实猫' }], status: device(), dashboard: dashboard(), intimacy: intimacy(), growth: growth(), ...overrides }
          if (Object.prototype.hasOwnProperty.call(samples, route)) h.success(request, samples[route])
        }
      }
      await flush()
    },
    async boot(name = 'home', select = true) {
      const page = h.page(name); await page.onShow()
      const enabled = page.useRealDevice(); await h.drain(); await enabled
      const login = page.liveLogin(); logins.at(-1).success({ code: 'synthetic-one-time-code' }); await flush()
      h.success(h.pending('/auth/wechat'), { accessToken: 'synthetic-private-token-never-stored', userId: 'user_1', expiresAt: '2026-11-01T01:00:00Z' }); await h.drain(); await login
      if (select) { const selected = page.liveSelect({ detail: { petId: 'pet_1' } }); await h.drain(); await selected }
      return page
    },
    async advance(ms) { now += ms; for (const [id, value] of [...timers]) if (value.at <= now) { timers.delete(id); value.fn() }; await flush() },
    stored: () => storageWrites, stopped: () => stopped
  }
  return h
}

const cases = {
  async 'all original preview pages retain their local content and never log in or POST automatically'() {
    for (const name of ['home', 'growth', 'growth-personality']) {
      const h = harness(), page = h.page(name); await page.onShow()
      assert.equal(page.data.liveMode, false); assert.equal(h.logins.length, 0); assert.equal(h.requests.length, 0)
      assert.ok(name === 'home' ? page.data.dashboard : name === 'growth' ? page.data.intimacy : page.data.growth)
      page.onHide(); assert.equal(h.timers.size, 0)
    }
  },
  async 'home uses real personality address intimacy and zero battery without fabricating Wi-Fi or today totals'() {
    const h = harness(), page = await h.boot('home')
    assert.equal(page.data.dashboard.pet.name, '真实小安'); assert.equal(page.data.dashboard.pet.personalityName, '真实温柔陪伴者')
    assert.equal(page.data.dashboard.pet.address, '真实的朋友'); assert.equal(page.data.dashboard.intimacy.points, 23)
    assert.equal(page.data.batteryLabel, '0%'); assert.equal(page.data.dashboard.today.interactionCount, null)
    assert.equal(page.data.dashboard.recent[0].title, '真实抚摸'); assert.doesNotMatch(JSON.stringify(page.data.dashboard), /Home-WiFi|286|演示/)
    assert.equal(h.posts().length, 0); page.onHide()
  },
  async 'three pages share login and selection but independently verify product data after navigation'() {
    const h = harness(); let page = await h.boot('home')
    for (const name of ['growth', 'growth-personality', 'home']) {
      page.onHide(); page = h.page(name); const shown = page.onShow()
      assert.equal(page.data.liveMode, true); assert.equal(page.data.liveState.signedIn, true)
      assert.equal(name === 'home' ? page.data.dashboard : name === 'growth' ? page.data.intimacy : page.data.growth, null)
      await h.drain(); await shown
      assert.ok(name === 'home' ? page.data.dashboard : name === 'growth' ? page.data.intimacy : page.data.growth)
    }
    assert.equal(h.logins.length, 1); assert.equal(h.posts().length, 0); page.onHide()
  },
  async 'growth renders real level rules unlocks and partial history without assuming today growth or task completion'() {
    const h = harness(), page = await h.boot('growth')
    assert.equal(page.data.intimacy.points, 23); assert.equal(page.data.intimacy.today.taskCompleted, null); assert.equal(page.data.intimacy.dailyGrowth, null)
    assert.equal(page.data.liveRules.ignored_greeting.pointsText, '-1 亲密度'); assert.equal(page.data.liveRules.touch.maxPerDay, 5)
    assert.deepEqual(plain(page.data.intimacy.currentUnlocks), ['开心摇尾']); assert.equal(page.data.intimacy.recent[0].value, 1)
    assert.equal(h.posts().length, 0); page.onHide()
  },
  async 'personality keeps exact fractional six attributes and honest empty tags portrait and event history'() {
    const h = harness(), page = await h.boot('growth-personality')
    assert.equal(page.data.growth.attributes.length, 6); assert.equal(page.data.growth.attributes[0].value, 10.25)
    assert.deepEqual(plain(page.data.growth.tags), []); assert.deepEqual(plain(page.data.growth.portraits), []); assert.deepEqual(plain(page.data.growth.recent), [])
    assert.equal(page.data.growth.debugValuesVisible, false); assert.equal(h.posts().length, 0); page.onHide()
  },
  async 'personality maps real active and replaced tags directives and changed dimensions without inventing deltas'() {
    const h = harness(), page = await h.boot('growth-personality'), sample = growth(), tag = { tagId: 'tag_1', name: '真实探索者', description: '真实标签说明', earnedAt: UTC }
    sample.activeTags = [tag]; sample.tagHistory = [{ ...tag, active: false, replacedAt: UTC }]; sample.behavior.directives = ['真实行为指令']
    sample.recentEvents = [{ eventType: 'question', label: '真实问题', topic: 'knowledge', emotion: 'neutral', changedAttributes: ['curiosity'], createdAt: UTC }]
    const read = page.realProduct.read(); await h.drain({ growth: sample }); await read
    assert.deepEqual(plain(page.data.growth.tags), ['真实探索者']); assert.deepEqual(plain(page.data.growth.portraits), ['真实行为指令'])
    assert.equal(page.data.tagHistory[0].active, false); assert.match(page.data.growth.recent[0].change, /真实属性0/)
    assert.doesNotMatch(page.data.growth.recent[0].change, /\+/); page.onHide()
  },
  async 'live debug handlers cannot generate growth even if called directly with forged demo flags'() {
    const h = harness(), page = await h.boot('growth-personality'), count = h.calls.length
    page.data.growth.debugValuesVisible = true; page.data.debugCount = '500'
    await page.generateGrowth(); page.onDebugEvent({ detail: { value: 1 } }); page.inputDebugCount({ detail: { value: '100' } })
    assert.equal(h.calls.length, count); assert.equal(h.posts().length, 0); assert.equal(page.data.debugCount, '500'); page.onHide()
  },
  async 'all product read failures remove current product data and never switch to demo fallback'() {
    for (const name of ['home', 'growth', 'growth-personality']) {
      const h = harness(), page = await h.boot(name), read = page.realProduct.read()
      h.fail(h.requests.at(-1)); await read
      assert.equal(name === 'home' ? page.data.dashboard : name === 'growth' ? page.data.intimacy : page.data.growth, null)
      assert.match(page.data.productError, /真实数据暂不可用/); assert.equal(page.data.liveMode, true); assert.equal(h.posts().length, 0); page.onHide()
    }
  },
  async 'growth without a selected device cannot open an interaction and refresh remains read only'() {
    const h = harness(), page = await h.boot('growth', false)
    assert.equal(page.data.liveState.signedIn, true); assert.equal(page.data.liveState.selectedPetId, '')
    assert.equal(page.data.intimacy, null); assert.equal(page.data.interactionBlocked, true)
    await page.addInteraction(event('touch')); const refresh = page.liveProductRefresh(); await h.drain(); await refresh
    assert.equal(h.modals.length, 0); assert.equal(h.posts().length, 0); page.onHide()
  },
  async 'growth failed read blocks interaction until a successful explicit read only refresh'() {
    const h = harness(), page = await h.boot('growth'), read = page.realProduct.read()
    h.fail(h.requests.at(-1)); await read
    assert.equal(page.data.liveState.selectedPetId, 'pet_1'); assert.equal(page.data.intimacy, null)
    assert.equal(page.data.productLoading, false); assert.equal(page.data.interactionBlocked, true)
    await page.addInteraction(event('touch')); assert.equal(h.modals.length, 0); assert.equal(h.posts().length, 0)
    const refresh = page.liveProductRefresh(); await h.drain(); await refresh
    assert.ok(page.data.intimacy); assert.equal(page.data.productError, ''); assert.equal(page.data.interactionBlocked, false)
    assert.equal(h.posts().length, 0); page.onHide()
  },
  async 'unknown interaction warning survives a failed read while new writes and same id retry remain blocked'() {
    const h = harness(), page = await h.boot('growth'), send = page.addInteraction(event('touch'))
    const confirmed = h.modals[0].success({ confirm: true }); await flush(); h.fail(h.posts()[0]); await confirmed; await send
    const read = page.realProduct.read(); h.fail(h.requests.at(-1)); await read
    assert.equal(page.data.intimacy, null); assert.equal(page.data.interactionUncertain, true)
    assert.match(page.data.interactionError, /尚未确认/); assert.equal(page.data.interactionRetry, false)
    await page.addInteraction(event('completed_task')); await page.retryInteraction()
    assert.equal(h.modals.length, 1); assert.equal(h.posts().length, 1); page.onHide()
  },
  async '401 product response clears shared identity and all current product data'() {
    const h = harness(), page = await h.boot('home'), read = page.realProduct.read()
    h.success(h.requests.at(-1), {}, 401); await read; await flush()
    assert.equal(page.data.liveState.signedIn, false); assert.equal(page.data.dashboard, null); assert.equal(page.data.liveOnline, false)
    assert.equal(h.posts().length, 0); page.onHide()
  },
  async 'hidden and deselected pages reject late product results and direct interaction handlers'() {
    for (const name of ['home', 'growth', 'growth-personality']) {
      const h = harness(), page = await h.boot(name), read = page.realProduct.read(), request = h.requests.at(-1)
      page.onHide(); const count = h.writes.length
      request.success({ statusCode: 200, data: { success: true, data: name === 'home' ? dashboard() : name === 'growth' ? intimacy() : growth() } }); await read
      await page.liveProductRefresh(); if (name === 'growth') await page.addInteraction(event('touch'))
      assert.equal(h.writes.length, count); assert.equal(h.posts().length, 0); assert.equal(h.timers.size, 0)
    }
  },
  async 'all four interactions require confirmation and fixed event/request only with no automatic check-in'() {
    for (const eventType of ['daily_check_in', 'touch', 'completed_task', 'ignored_greeting']) {
      const h = harness(), page = await h.boot('growth'), send = page.addInteraction(event(eventType))
      assert.equal(h.modals.length, 1); assert.equal(h.posts().length, 0); assert.match(h.modals[0].content, /真实亲密度.*庆祝动作/)
      const confirmed = h.modals[0].success({ confirm: true }); await flush()
      assert.equal(h.posts().length, 1); const request = h.posts()[0]
      assert.deepEqual(Object.keys(request.data).sort(), ['eventType', 'requestId']); assert.equal(request.data.eventType, eventType)
      h.success(request, receipt(request)); await confirmed; await send; await h.drain()
      assert.match(page.data.interactionNotice, /互动已记录/); page.onHide()
    }
  },
  async 'cancelled confirmation sends nothing and duplicates while modal open create no second dialog'() {
    const h = harness(), page = await h.boot('growth'), send = page.addInteraction(event('touch'))
    await page.addInteraction(event('completed_task')); assert.equal(h.modals.length, 1)
    await h.modals[0].success({ confirm: false }); await send
    assert.equal(h.posts().length, 0); assert.equal(page.data.interactionConfirm, false); page.onHide()
  },
  async 'all four real interactions open a native-valid four-character confirmation before one explicit request'() {
    for (const eventType of ['daily_check_in', 'touch', 'completed_task', 'ignored_greeting']) {
      const h = harness({ strictModal: true }), page = await h.boot('growth'), send = page.addInteraction(event(eventType))
      assert.equal(h.modals.length, 1, eventType + ' must open a native-valid modal'); assert.equal(h.posts().length, 0)
      const confirmed = h.modals[0].success({ confirm: true }); await flush()
      assert.equal(h.posts().length, 1); const request = h.posts()[0]
      assert.equal(request.data.eventType, eventType); h.success(request, receipt(request)); await confirmed; await send; await h.drain()
      assert.equal(h.posts().length, 1); page.onHide()
    }
  },
  async 'same-number retry also meets native button limits and never creates a second intent'() {
    const h = harness({ strictModal: true }), page = await h.boot('growth'), send = page.addInteraction(event('touch'))
    assert.equal(h.modals.length, 1)
    const firstConfirmed = h.modals[0].success({ confirm: true }); await flush(); const first = h.posts()[0]
    h.fail(first); await firstConfirmed; await send
    const retry = page.retryInteraction(); assert.equal(h.modals.length, 2); assert.equal(h.posts().length, 1)
    const retryConfirmed = h.modals[1].success({ confirm: true }); await flush()
    const second = h.posts()[1]; assert.deepEqual(plain(second.data), plain(first.data)); h.success(second, receipt(second))
    await retryConfirmed; await retry; await h.drain(); assert.equal(h.posts().length, 2); page.onHide()
  },
  async 'native interaction modal failure stays visibly unsent across read polling and permits new confirmation'() {
    for (const eventType of ['daily_check_in', 'touch', 'completed_task', 'ignored_greeting']) {
      const h = harness(), page = await h.boot('growth'), send = page.addInteraction(event(eventType))
      h.modals[0].fail({ errMsg: 'raw-private-native-error' }); await send
      assert.equal(page.data.interactionConfirm, false); assert.equal(h.posts().length, 0)
      assert.match(page.data.interactionError, /未发送.*确认弹窗.*失败/)
      await h.advance(10000); await h.drain()
      assert.match(page.data.interactionError, /未发送.*确认弹窗.*失败/)
      assert.doesNotMatch(JSON.stringify(page.data), /raw-private/)
      const retry = page.addInteraction(event(eventType)); assert.equal(h.modals.length, 2)
      const confirmed = h.modals[1].success({ confirm: true }); await flush(); const request = h.posts()[0]
      h.success(request, receipt(request)); await confirmed; await retry; await h.drain()
      assert.equal(h.posts().length, 1); assert.equal(page.data.interactionError, ''); page.onHide()
    }
  },
  async 'synchronous native interaction modal throw settles cleanly without a write or lingering confirmation'() {
    const h = harness({ throwModal: true }), page = await h.boot('growth')
    await page.addInteraction(event('touch'))
    assert.equal(page.data.interactionConfirm, false); assert.equal(h.posts().length, 0)
    assert.match(page.data.interactionError, /未发送.*确认弹窗.*失败/)
    assert.equal(h.stored(), 0); assert.deepEqual(h.logs, []); page.onHide()
  },
  async 'a failed retry modal preserves the original unknown intent and a later retry uses only that number'() {
    const options = {}, h = harness(options), page = await h.boot('growth'), send = page.addInteraction(event('touch'))
    const confirmed = h.modals[0].success({ confirm: true }); await flush(); const first = h.posts()[0]
    h.fail(first); await confirmed; await send
    for (const failure of ['fail', 'throw']) {
      const before = h.posts().length
      options.throwModal = failure === 'throw'
      const retry = page.retryInteraction()
      if (failure === 'fail') h.modals.at(-1).fail({ errMsg: 'raw-private-retry-error' })
      await retry
      assert.equal(page.data.interactionConfirm, false); assert.equal(page.data.interactionUncertain, true)
      assert.equal(page.data.interactionRetry, true); assert.equal(h.posts().length, before)
      assert.match(page.data.interactionError, /未发送.*确认弹窗.*失败/)
      assert.match(page.data.interactionError, /尚未确认|不确定|原互动/)
    }
    options.throwModal = false
    const retry = page.retryInteraction(), retryConfirmed = h.modals.at(-1).success({ confirm: true }); await flush()
    const second = h.posts()[1]; assert.deepEqual(plain(second.data), plain(first.data)); h.success(second, receipt(second))
    await retryConfirmed; await retry; await h.drain(); assert.equal(h.posts().length, 2); page.onHide()
  },
  async 'late old interaction cancel confirm and fail cannot change a newer visit modal or send an old intent'() {
    for (const callback of ['cancel', 'confirm', 'fail']) {
      const h = harness(), page = await h.boot('growth'), oldSend = page.addInteraction(event('touch'))
      page.onHide(); const shown = page.onShow(); await h.drain(); await shown
      const newSend = page.addInteraction(event('completed_task')); assert.equal(h.modals.length, 2)
      const target = page.interactionTarget, before = h.writes.length
      if (callback === 'fail') h.modals[0].fail({ errMsg: 'raw-private-late-error' })
      else await h.modals[0].success({ confirm: callback === 'confirm' })
      await oldSend
      assert.equal(page.interactionTarget, target); assert.equal(page.data.interactionConfirm, true)
      assert.equal(h.writes.length, before); assert.equal(h.posts().length, 0)
      const confirmed = h.modals[1].success({ confirm: true }); await flush(); const request = h.posts()[0]
      assert.equal(request.data.eventType, 'completed_task'); h.success(request, receipt(request))
      await confirmed; await newSend; await h.drain(); assert.equal(h.posts().length, 1); page.onHide()
    }
  },
  async 'non-boolean native confirm results never count as an explicit interaction confirmation'() {
    for (const answer of [null, {}, { confirm: 'true' }, { confirm: 1 }]) {
      const h = harness(), page = await h.boot('growth'), send = page.addInteraction(event('touch'))
      await h.modals[0].success(answer); await send
      assert.equal(h.posts().length, 0); assert.equal(page.data.interactionConfirm, false); page.onHide()
    }
  },
  async 'switch-away-and-back cancels a captured confirmation rather than using the same pet id unsafely'() {
    const h = harness(), page = await h.boot('growth'), send = page.addInteraction(event('touch'))
    const next = page.liveSelect({ detail: { petId: 'pet_2' } }); await h.drain(); await next
    const back = page.liveSelect({ detail: { petId: 'pet_1' } }); await h.drain(); await back
    await h.modals[0].success({ confirm: true }); await send
    assert.equal(h.posts().length, 0); assert.equal(page.data.interactionConfirm, false); page.onHide()
  },
  async 'hide while a confirmation is open cancels the write even when late native modal confirms'() {
    const h = harness(), page = await h.boot('growth'), send = page.addInteraction(event('touch')); page.onHide()
    const count = h.writes.length; await h.modals[0].success({ confirm: true }); await send
    assert.equal(h.posts().length, 0); assert.equal(h.writes.length, count)
  },
  async 'authorization list revalidation invalidates an open interaction confirmation even when selection is retained'() {
    const h = harness(), page = await h.boot('growth'), send = page.addInteraction(event('touch'))
    const list = page.liveList(); await h.drain(); await list
    assert.equal(page.data.liveState.selectedPetId, 'pet_1'); assert.equal(page.data.interactionConfirm, false)
    await h.modals[0].success({ confirm: true }); await send
    assert.equal(h.posts().length, 0); page.onHide()
  },
  async 'already completed daily check-in remains blocked through direct handler and does not record again'() {
    const h = harness(), page = await h.boot('growth'), read = page.realProduct.read()
    await h.drain({ intimacy: { ...intimacy(), dailyCheckInCompleted: true } }); await read
    await page.addInteraction(event('daily_check_in')); assert.equal(h.modals.length, 0); assert.equal(h.posts().length, 0); page.onHide()
  },
  async 'server disabled write gate blocks every direct interaction and retry handler'() {
    const h = harness(), page = await h.boot('growth'), read = page.realProduct.read()
    await h.drain({ intimacy: { ...intimacy(), interactionsEnabled: false } }); await read
    for (const eventType of ['daily_check_in', 'touch', 'completed_task', 'ignored_greeting']) await page.addInteraction(event(eventType))
    await page.retryInteraction(); assert.equal(h.modals.length, 0); assert.equal(h.posts().length, 0); assert.equal(page.data.interactionBlocked, true); page.onHide()
  },
  async 'unknown write keeps one request id and explicit retry never creates a second interaction id'() {
    const h = harness(), page = await h.boot('growth'), send = page.addInteraction(event('touch'))
    const confirmed = h.modals[0].success({ confirm: true }); await flush(); const first = h.posts()[0]
    h.fail(first); await confirmed; await send
    assert.equal(page.data.interactionUncertain, true); assert.equal(page.data.interactionRetry, true); assert.doesNotMatch(page.data.interactionNotice, /已记录/)
    await page.addInteraction(event('completed_task')); assert.equal(h.modals.length, 1)
    const retry = page.retryInteraction(), retryConfirmed = h.modals[1].success({ confirm: true }); await flush()
    const second = h.posts()[1]; assert.deepEqual(plain(second.data), plain(first.data)); h.success(second, receipt(second)); await retryConfirmed; await retry; await h.drain()
    assert.match(page.data.interactionNotice, /互动已记录/); assert.equal(h.posts().length, 2); page.onHide()
  },
  async 'repeated unchanged presentation callbacks do not resend the three pages data to the view bridge'() {
    for (const name of ['home', 'growth', 'growth-personality']) {
      const h = harness(), page = await h.boot(name), count = h.writes.length
      for (let index = 0; index < 3; index++) page.renderLiveProduct(page.realProduct.getState())
      assert.equal(h.writes.length, count); assert.equal(h.posts().length, 0); page.onHide()
    }
  },
  async 'unknown request retry is unavailable until the current product snapshot and write gate are verified again'() {
    const h = harness(), page = await h.boot('growth'), send = page.addInteraction(event('touch'))
    const confirmed = h.modals[0].success({ confirm: true }); await flush(); h.fail(h.posts()[0]); await confirmed; await send
    assert.equal(page.data.interactionRetry, true)
    let read = page.realProduct.read(); h.fail(h.requests.at(-1)); await read
    assert.equal(page.data.interactionRetry, false); await page.retryInteraction(); assert.equal(h.modals.length, 1)
    read = page.realProduct.read(); await h.drain({ intimacy: { ...intimacy(), interactionsEnabled: false } }); await read
    assert.equal(page.data.interactionRetry, false); assert.equal(h.posts().length, 1)
    read = page.realProduct.read(); await h.drain(); await read
    assert.equal(page.data.interactionRetry, true); assert.equal(h.posts().length, 1); page.onHide()
  },
  async 'pull refresh is read-only and stops its spinner for every live product page'() {
    for (const name of ['home', 'growth', 'growth-personality']) {
      const h = harness(), page = await h.boot(name), refresh = page.onPullDownRefresh(); await h.drain(); await refresh
      assert.equal(h.stopped(), 1); assert.equal(h.posts().length, 0); page.onHide()
    }
  },
  async 'tokens and login codes never enter page data persistent storage or console'() {
    const h = harness(), page = await h.boot('home')
    assert.equal(h.stored(), 0); assert.deepEqual(h.logs, [])
    for (const write of h.writes) assert.doesNotMatch(JSON.stringify(write), /synthetic-private-token|synthetic-one-time-code|Authorization|accessToken/)
    page.onHide()
  },
  async 'all three templates expose the real selector keep original cards and hide live growth acceleration'() {
    for (const name of ['home', 'growth', 'growth-personality']) {
      const source = fs.readFileSync(path.join(root, `pages/${name}/index.wxml`), 'utf8')
      assert.ok(source.includes('<live-product-panel')); assert.ok(source.includes('bindselect="liveSelect"')); assert.ok(source.includes('liveDevice="{{liveMode}}"'))
      const config = JSON.parse(fs.readFileSync(path.join(root, `pages/${name}/index.json`), 'utf8'))
      assert.equal(config.usingComponents['live-product-panel'], '/components/live-product-panel/index')
    }
    const personality = fs.readFileSync(path.join(root, 'pages/growth-personality/index.wxml'), 'utf8')
    assert.ok(personality.includes('!liveMode && growth.debugValuesVisible')); assert.ok(personality.includes('成长标签历史'))
    const home = fs.readFileSync(path.join(root, 'pages/home/index.wxml'), 'utf8')
    assert.ok(home.includes('今日总数暂未提供')); assert.ok(home.includes('最近 30 条'))
  },
  async 'growth template exposes safe empty states keeps uncertain warnings outside snapshot and gives four cards tap feedback'() {
    const source = fs.readFileSync(path.join(root, 'pages/growth/index.wxml'), 'utf8')
    const style = fs.readFileSync(path.join(root, 'pages/growth/index.wxss'), 'utf8')
    const content = source.indexOf('<view wx:if="{{intimacy}}" class="growth-content">')
    const warning = source.indexOf('liveMode && (interactionNotice || interactionError)')
    assert.ok(warning >= 0 && warning < content, 'interaction outcome must remain visible without a product snapshot')
    const empty = source.match(/<view wx:if="\{\{liveMode && !intimacy && !productLoading\}\}" class="cultivation-empty"[\s\S]*?<\/view>/)
    assert.ok(empty, 'a missing product snapshot must have an explicit empty/error state')
    assert.match(empty[0], /请选择已授权设备/); assert.match(empty[0], /培养数据暂不可用/)
    assert.match(empty[0], /网络或设备连接/); assert.match(empty[0], /尚未加载真实数据，暂不能记录互动/)
    assert.match(empty[0], /bindtap="liveProductRefresh"/); assert.doesNotMatch(empty[0], /bindtap="(?:addInteraction|retryInteraction)"/)
    const cards = [...source.matchAll(/<button class="interaction-button[^\"]*"[\s\S]*?<\/button>/g)]
    assert.equal(cards.length, 4)
    for (const [card] of cards) {
      assert.match(card, /hover-class="interaction-pressed"/); assert.match(card, /class="interaction-action"/)
      assert.match(card, /点击后确认记录/); assert.match(card, /interactionBlocked/)
    }
    assert.match(source, /disabled="\{\{!intimacy \|\| submittingEvent \|\| interactionConfirm\}\}"/)
    assert.match(style, /\.interaction-pressed\s*\{/)
  }
}

;(async () => {
  let failed = 0
  for (const [name, run] of Object.entries(cases)) {
    try { await run(); console.log('PASS ' + name) }
    catch (error) { failed++; console.error('FAIL ' + name + ': ' + error.stack) }
  }
  console.log(`Live product pages smoke: ${Object.keys(cases).length - failed}/${Object.keys(cases).length} (offline actual TS, not WeChat GUI)`)
  if (failed) process.exitCode = 1
})().catch(error => { console.error(error); process.exitCode = 1 })
