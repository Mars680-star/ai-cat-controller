// Entirely offline: real TS auth/session/motion modules, injected wx, no real HTTP or device.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..')
const NOW = Date.parse('2026-11-01T00:00:00Z')
const TOKEN = 'synthetic-private-motion-token-never-stored'
const plain = value => JSON.parse(JSON.stringify(value))
const flush = async () => { for (let i = 0; i < 45; i++) await Promise.resolve() }
const pets = [{ petId: 'pet_1', name: '小安' }, { petId: 'pet_2', name: '另一只猫' }]
const ACTION_IDS = ['head_nod', 'head_shake', 'tail_wag', 'proud_pose', 'quiet_companion', 'greeting_combo', 'celebration_combo']
const ACTION_NAMES = ['点头', '摇头', '开心摇尾', '骄傲转身', '安静陪伴', '见面问候', '升级庆祝']
function motionSnapshot(enabled = false, execution = null) {
  return { enabled, verified: enabled, deviceBusy: Boolean(execution && ['sending', 'running', 'unknown'].includes(execution.status)),
    actions: ACTION_IDS.map((actionId, i) => ({ actionId, name: ACTION_NAMES[i], available: enabled && !execution, reason: enabled ? '' : '尚未开放实机动作' })),
    execution, sampledAt: '2026-11-01T00:00:00Z' }
}
function noTailSnapshot(execution = null, paused = false) {
  const value = motionSnapshot(true, execution)
  value.hasTail = false; value.deviceMotionEnabled = !paused
  value.actions = value.actions.filter(item => ['head_nod', 'head_shake', 'quiet_companion'].includes(item.actionId)).map(item => paused ? { ...item, available: false, reason: '设备动作已暂停' } : item)
  return value
}
function execution(status = 'running', executionId = 'exec_1', requestId = 'req_1') {
  return { executionId, actionId: 'head_nod', status, requestId, updatedAt: '2026-11-01T00:00:00Z' }
}
function deviceSnapshot() {
  return { readOnly: true, sampledAt: '2026-11-01T00:00:00Z', health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' },
    device: { connected: true, deviceSerial: '7c2****a128', networkName: null, batteryPercent: 66, batteryState: 'charging', batteryVoltage: 4.1, chargerOnline: true, batteryPresent: true, lastSeenAt: null },
    dialog: { state: 'offline', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false } }
}
function harness() {
  let now = NOW, sequence = 0, definition, hidden = false, hiddenWrites = 0, storageCalls = 0, autoHistory = true
  const timers = new Map(), modules = new Map(), requests = [], logins = [], logs = []
  class ClockDate extends Date {
    constructor(...args) { if (args.length) super(...args); else super(now) }
    static now() { return now }
  }
  const wx = {
    login(value) { logins.push(value) },
    request(value) {
      requests.push(value)
      if (autoHistory && value.url.endsWith('/motion/history')) Promise.resolve().then(() => value.success({ statusCode: 200, data: { success: true, data: { items: [], limit: 20 } } }))
      return { abort() { value.fail({ errMsg: 'aborted raw-secret' }) } }
    },
    getStorageSync() { storageCalls++; return undefined },
    setStorageSync() { storageCalls++; throw Error('No storage allowed') },
    redirectTo() {}, stopPullDownRefresh() {}, showToast() {}
  }
  const context = vm.createContext({ wx, Date: ClockDate,
    setTimeout(fn, delay) { const id = ++sequence; timers.set(id, { fn, at: now + Math.max(1, delay) }); return id },
    clearTimeout(id) { timers.delete(id) },
    setInterval() { throw Error('Live motion must not have percentage timers') }, clearInterval() {},
    Page(value) { definition = value },
    console: Object.fromEntries(['log', 'warn', 'error', 'info', 'debug'].map(name => [name, (...args) => logs.push(args)]))
  })
  function load(relative, parent = root) {
    let filename = path.resolve(parent, relative)
    if (!filename.endsWith('.ts')) filename += '.ts'
    if (modules.has(filename)) return modules.get(filename).exports
    const module = { exports: {} }; modules.set(filename, module)
    const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 } }).outputText
    new vm.Script('(function(require,module,exports){' + output + '\n})', { filename }).runInContext(context)(name => load(name, path.dirname(filename)), module, module.exports)
    return module.exports
  }
  const api = load('services/live-device-session'), session = api.getLiveDeviceSession(), motionApi = load('services/live-motion')
  const h = { session, motionApi, requests, logins, timers, logs, load, wx,
    success(value, index = requests.length - 1, statusCode = 200) { requests[index].success({ statusCode, data: { success: true, data: value } }) },
    failure(index = requests.length - 1) { requests[index].fail({ errMsg: 'raw-secret AppSecret' }) },
    async authenticate() {
      this.anchor = session.attach(() => {})
      const ready = session.enable(); this.success({ mode: 'local-development', wechatConfigured: true, readOnly: true }); await ready
      const login = session.login(); logins.at(-1).success({ code: 'synthetic-code' }); await flush()
      this.success({ accessToken: TOKEN, userId: 'user_1', expiresAt: '2026-11-01T01:00:00Z' }); await flush()
      this.success(pets); await login
      const select = session.selectPet('pet_1'); this.success(deviceSnapshot()); await select
    },
    async controller(enabled = false, snapshot = motionSnapshot(enabled)) {
      await this.authenticate()
      let ids = 0
      this.views = []
      this.motion = motionApi.createLiveMotion(session, prefix => prefix + '_' + (++ids))
      this.motion.show(state => this.views.push(state)); this.success(snapshot); await flush()
      return this.motion
    },
    async page(enabled = false, authenticated = true) {
      if (authenticated) await this.authenticate()
      load('pages/motion/index')
      const page = { ...definition, data: plain(definition.data), setData(value) { if (hidden) hiddenWrites++; Object.assign(this.data, plain(value)) } }
      this.page = page
      await page.onShow()
      if (authenticated) this.success(motionSnapshot(enabled), this.requests.findLastIndex(item => item.url.endsWith('/motion')))
      else this.success({ mode: 'local-development', wechatConfigured: true, readOnly: false })
      await flush()
      return page
    },
    hidePage() { this.page.onHide(); hidden = true },
    showPage() { hidden = false; return this.page.onShow() },
    hiddenWrites: () => hiddenWrites,
    autoHistory(value) { autoHistory = value },
    storageCalls: () => storageCalls,
    async advance(ms) {
      const end = now + ms
      for (let guard = 0; guard < 1000; guard++) {
        const next = [...timers.entries()].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at)[0]
        if (!next) { now = end; await flush(); return }
        now = next[1].at; timers.delete(next[0]); next[1].fn(); await flush()
      }
      throw Error('timer loop')
    },
    motionPosts: () => requests.filter(item => item.method === 'POST' && /\/motion\//.test(item.url))
  }
  return h
}

const cases = {
  async 'exact server cooldown schedules a 400ms GET and never unlocks by elapsed client time'() {
    const sample = motionSnapshot(true, execution('ended_unverified'))
    sample.actions.forEach(item => { item.available = false; item.reason = '动作冷却中' })
    const h = harness(), motion = await h.controller(true, sample), initial = h.requests.length
    await h.advance(399); assert.equal(h.requests.length, initial)
    assert.ok(motion.getState().snapshot.actions.every(item => !item.available))
    await h.advance(1)
    assert.equal(h.requests.length, initial + 1); assert.equal(h.requests.at(-1).method, 'GET')
    assert.ok(h.requests.at(-1).url.endsWith('/motion'))
    assert.ok(motion.getState().snapshot.actions.every(item => !item.available))
    h.success(motionSnapshot(true)); await flush()
    assert.ok(motion.getState().snapshot.actions.every(item => item.available))
    const refreshed = h.requests.length
    await h.advance(400); assert.equal(h.requests.length, refreshed)
    assert.equal(h.motionPosts().length, 0)
  },
  async 'cooldown read failure or backgrounding never dispatches or replays an action'() {
    const sample = motionSnapshot(true)
    sample.actions.forEach(item => { item.available = false; item.reason = '动作冷却中' })
    const h = harness(), motion = await h.controller(true, sample)
    await h.advance(400); h.failure(); await flush()
    assert.equal(motion.getState().snapshot, null)
    await motion.execute('head_nod'); assert.equal(h.motionPosts().length, 0)
    motion.hide(); const reads = h.requests.filter(item => item.url.endsWith('/motion')).length
    await h.advance(5000)
    assert.equal(h.requests.filter(item => item.url.endsWith('/motion')).length, reads)
    assert.equal(h.motionPosts().length, 0)
  },
  async 'running and unknown override a cooldown reason while lookalike reasons keep idle polling'() {
    for (const status of ['running', 'unknown', null]) {
      const sample = motionSnapshot(true, status && execution(status))
      sample.actions.forEach(item => { item.available = false; item.reason = status ? '动作冷却中' : '动作冷却中，请稍后' })
      const h = harness(); await h.controller(true, sample)
      const count = h.requests.length
      await h.advance(400); assert.equal(h.requests.length, count)
      await h.advance(600); assert.equal(h.requests.length, count + (status ? 1 : 0))
      assert.equal(h.motionPosts().length, 0)
    }
  },
  async 'single tap context is one-use and cannot survive hide or a device change'() {
    const h = harness(), motion = await h.controller(true)
    const token = motion.prepare('head_nod')
    assert.equal(h.motionPosts().length, 0)
    const send = motion.confirm(token); await motion.confirm(token)
    assert.equal(h.motionPosts().length, 1)
    h.success(motionSnapshot(true, execution('ended_unverified'))); await send
    const stale = motion.prepare('head_shake'); motion.hide()
    await motion.confirm(stale); assert.equal(h.motionPosts().length, 1)
  },
  async 'prepared context cannot bypass a newer busy snapshot or action restriction'() {
    for (const sample of [motionSnapshot(true, execution()), noTailSnapshot()]) {
      const h = harness(), motion = await h.controller(true)
      const token = motion.prepare('tail_wag'), read = motion.read()
      h.success(sample); await read; await motion.confirm(token)
      assert.equal(h.motionPosts().length, 0)
      assert.match(motion.getState().error, /动作未发送/)
    }
  },
  async 'active status is read after one second while idle retains three second polling'() {
    const h = harness(), motion = await h.controller(true)
    const baseline = h.requests.length
    await h.advance(1000); assert.equal(h.requests.length, baseline)
    const send = motion.execute('head_nod')
    h.success(motionSnapshot(true, execution())); await send
    const afterSend = h.requests.length
    await h.advance(999); assert.equal(h.requests.length, afterSend)
    await h.advance(1); assert.equal(h.requests.at(-1).method, 'GET')
    assert.ok(h.requests.at(-1).url.endsWith('/motion'))
    assert.equal(h.motionPosts().length, 1)
  },
  async 'terminal GET for exact lost-ACK request reconciles the local pending state without a POST'() {
    const h = harness(), motion = await h.controller(true)
    const send = motion.execute('head_nod'), payload = plain(h.requests.at(-1).data)
    h.failure(); await send
    assert.equal(motion.getState().uncertain, true)
    const read = motion.read(), receipt = execution('ended_unverified', 'exec_recovered', payload.requestId)
    const sample = motionSnapshot(true); sample.execution = receipt
    h.success(sample); await read
    assert.equal(motion.getState().uncertain, false); assert.equal(motion.getState().retryAvailable, false)
    assert.equal(motion.getState().snapshot.execution.status, 'ended_unverified')
    assert.equal(h.motionPosts().length, 1)
  },
  async 'idle or unrelated terminal GET never clears a lost-ACK request or retries it'() {
    for (const receipt of [null, execution('ended_unverified', 'other', 'other_request'), { ...execution('ended_unverified', 'other', 'motion_1'), actionId: 'tail_wag' }, execution('unknown', 'pending', 'motion_1')]) {
      const h = harness(), motion = await h.controller(true), send = motion.execute('head_nod')
      h.failure(); await send
      const read = motion.read(), sample = motionSnapshot(true); sample.execution = receipt
      if (receipt && receipt.status === 'unknown') { sample.deviceBusy = true; sample.actions.forEach(item => { item.available = false }) }
      h.success(sample); await read
      assert.equal(motion.getState().uncertain, true); assert.equal(motion.getState().retryAvailable, true)
      await motion.execute('head_shake'); assert.equal(h.motionPosts().length, 1)
    }
  },
  async 'unknown server receipt remains unresolved across idle reads and elapsed time'() {
    const h = harness(), motion = await h.controller(true)
    const send = motion.execute('tail_wag'), requestId = h.requests.at(-1).data.requestId
    const sample = motionSnapshot(true, { ...execution('unknown', 'tail_unknown', requestId), actionId: 'tail_wag' })
    h.success(sample); await send
    const read = motion.read(); h.success({ ...sample, deviceBusy: false }); await read
    await h.advance(1000); h.success({ ...sample, deviceBusy: false }); await flush()
    assert.equal(motion.getState().uncertain, true); assert.equal(h.motionPosts().length, 1)
  },
  async 'all seven canonical actions submit only fixed action and request identifiers'() {
    for (const actionId of ACTION_IDS) {
      const h = harness(), motion = await h.controller(true)
      const send = motion.execute(actionId)
      assert.equal(h.motionPosts().length, 1)
      assert.equal(h.requests.at(-1).url, 'http://127.0.0.1:8787/v1/pets/pet_1/motion/execute')
      assert.deepEqual(plain(h.requests.at(-1).data), { actionId, requestId: 'motion_1' })
      h.success(motionSnapshot(true, { ...execution(), actionId })); await send
      assert.equal(motion.getState().snapshot.execution.actionId, actionId)
      assert.equal(h.motionPosts().length, 1)
    }
  },
  async 'server authorization locks and missing tail capabilities never submit actions'() {
    for (const [actionId, reason] of [['celebration_combo', '亲密度等级不足'], ['proud_pose', '当前性格未解锁'], ['tail_wag', '设备未启用尾部'], ['greeting_combo', '需关联网页授权']]) {
      const sample = motionSnapshot(true)
      sample.actions = sample.actions.map(item => item.actionId === actionId ? { ...item, available: false, reason } : item)
      const h = harness(), motion = await h.controller(true, sample)
      await motion.execute(actionId)
      assert.equal(h.motionPosts().length, 0)
      assert.match(motion.getState().error, /动作未发送.*不可执行/)
      assert.equal(motion.getState().snapshot.actions.find(item => item.actionId === actionId).reason, reason)
    }
  },
  async 'full catalog and history accept only canonical names and remove extra upstream fields'() {
    const h = harness(), sample = motionSnapshot(true)
    sample.actions = sample.actions.map(item => ({ ...item, name: '伪造名称', rawPreset: 'private-device-command' }))
    const verified = h.motionApi.verifyMotion(sample)
    assert.deepEqual(plain(verified.actions.map(item => item.actionId)), ACTION_IDS)
    assert.deepEqual(plain(verified.actions.map(item => item.name)), ACTION_NAMES)
    assert.ok(verified.actions.every(item => !('rawPreset' in item)))
    const history = h.motionApi.verifyMotionHistory({ items: ACTION_IDS.map((actionId, index) => ({ ...execution('ended_unverified', 'exec_' + index), actionId })), limit: 20 })
    assert.deepEqual(plain(history.items.map(item => item.actionId)), ACTION_IDS)
  },
  async 'motion history validator accepts only bounded verified receipt records'() {
    const h = harness(), verify = h.motionApi.verifyMotionHistory
    assert.deepEqual(plain(verify({ items: [execution()], limit: 20 })), { items: [execution()], limit: 20 })
    for (const value of [null, [], { items: [], limit: 21 }, { items: [execution(), execution()], limit: 20 },
      { items: Array.from({ length: 21 }, (_, i) => execution('running', 'exec_' + i)), limit: 20 },
      ...[{ actionId: 'tail' }, { status: 'completed' }, { requestId: 'secret\nvalue' }, { updatedAt: '2026-02-30T00:00:00Z' }, { executionId: '../evil' }].map(item => ({ items: [{ ...execution(), ...item }], limit: 20 }))]) {
      assert.throws(() => verify(value), /UNVERIFIED/)
    }
  },
  async 'history client rejects invalid pet ids and unverified records without credentials in results'() {
    const h = harness(); await h.authenticate(); h.autoHistory(false)
    const before = h.requests.length
    const raw = h.load('services/cloud-auth-client').createCloudAuthClient(h.wx)
    await assert.rejects(raw.readMotionHistory('../wrong'), error => error.code === 'INVALID_ID')
    assert.equal(h.requests.length, before)
    const read = h.session.readMotionHistory()
    h.success({ items: [{ ...execution(), status: 'completed', rawSecret: TOKEN }], limit: 20 })
    await assert.rejects(read, error => error.code === 'UNVERIFIED' && !error.message.includes(TOKEN))
  },
  async 'default prepared snapshot cannot execute or stop'() {
    const h = harness(), motion = await h.controller()
    await motion.execute('head_nod'); await motion.stop(); await motion.retryExecute()
    assert.equal(h.motionPosts().length, 0); assert.equal(motion.getState().snapshot.enabled, false)
  },
  async 'unverified sample cannot execute or stop even with enabled true'() {
    const original = motionSnapshot(true)
    const h = harness(), motion = await h.controller(true, { ...original, verified: false, actions: original.actions.map(item => ({ ...item, available: false })) })
    await motion.execute('head_nod')
    assert.match(motion.getState().error, /动作未发送.*未验证.*刷新/)
    await motion.stop(); assert.equal(h.motionPosts().length, 0)
    assert.match(motion.getState().error, /停止请求未发送.*身份或状态未验证.*刷新.*现场检查/)
  },
  async 'disabled stop visibly rejects without creating a stop request'() {
    const h = harness(), motion = await h.controller()
    await motion.stop()
    assert.match(motion.getState().error, /停止请求未发送.*动作开关未开启.*现场检查/)
    assert.equal(motion.getState().uncertain, false); assert.equal(motion.getState().stopping, false)
    assert.equal(h.motionPosts().length, 0)
  },
  async 'missing motion snapshot visibly rejects stop instead of silently succeeding'() {
    const h = harness(), motion = await h.controller(true), read = motion.read()
    h.failure(); await read
    assert.equal(motion.getState().snapshot, null)
    await motion.stop()
    assert.match(motion.getState().error, /停止请求未发送.*状态尚未验证.*刷新动作状态.*现场检查/)
    assert.equal(motion.getState().stopping, false); assert.equal(h.motionPosts().length, 0)
  },
  async 'failed state read visibly rejects confirmation without submitting or retrying'() {
    const h = harness(), motion = await h.controller(true), read = motion.read()
    h.failure(); await read
    await motion.execute('head_nod')
    assert.match(motion.getState().error, /动作未发送.*刷新动作状态/)
    assert.equal(h.motionPosts().length, 0); assert.equal(motion.getState().retryAvailable, false)
  },
  async 'busy or disabled state visibly rejects confirmation without creating requests'() {
    for (const value of [motionSnapshot(false), motionSnapshot(true, execution())]) {
      const h = harness(), motion = await h.controller(value.enabled, value)
      await motion.execute('head_nod')
      assert.match(motion.getState().error, /动作未发送/)
      assert.equal(h.motionPosts().length, 0); assert.equal(motion.getState().retryAvailable, false)
    }
  },
  async 'guarded new action preserves uncertain original request for explicit retry'() {
    const h = harness(), motion = await h.controller(true), send = motion.execute('head_nod')
    const payload = plain(h.requests.at(-1).data)
    h.failure(); await send
    await motion.execute('head_shake')
    assert.match(motion.getState().error, /动作未发送.*结果不确定/)
    assert.equal(h.motionPosts().length, 1); assert.equal(motion.getState().uncertain, true)
    const retry = motion.retryExecute()
    assert.deepEqual(plain(h.requests.at(-1).data), payload)
    h.success(motionSnapshot(true, execution('ended_unverified'))); await retry
  },
  async 'missing explicit retry context is a visible rejection not a fresh command'() {
    const h = harness(), motion = await h.controller(true)
    await motion.retryExecute()
    assert.match(motion.getState().error, /动作未发送.*同编号请求/)
    assert.equal(h.motionPosts().length, 0)
  },
  async 'read uses current authorized pet and only authenticated fixed GET'() {
    const h = harness(); await h.controller()
    const request = h.requests.at(-1)
    assert.equal(request.url, 'http://127.0.0.1:8787/v1/pets/pet_1/motion')
    assert.equal(request.method, 'GET'); assert.equal(request.header.Authorization, 'Bearer ' + TOKEN)
    assert.ok(!('data' in request)); assert.equal(h.storageCalls(), 0)
  },
  async 'command payload excludes hardware parameters and duplicate taps share flight'() {
    const h = harness(), motion = await h.controller(true)
    const first = motion.execute('head_nod'); await motion.execute('head_nod')
    assert.equal(h.motionPosts().length, 1)
    assert.deepEqual(plain(h.requests.at(-1).data), { actionId: 'head_nod', requestId: 'motion_1' })
    h.success(motionSnapshot(true, execution())); await first
    assert.equal(motion.getState().snapshot.execution.status, 'running')
  },
  async 'motion write waits ten seconds then ignores late ACK and explicit retry reuses exact request id'() {
    const h = harness(), motion = await h.controller(true), first = motion.execute('head_nod')
    const old = h.requests.length - 1
    assert.equal(h.requests[old].timeout, 10000)
    await h.advance(5000)
    assert.equal(motion.getState().submitting, true); assert.equal(motion.getState().uncertain, false)
    assert.equal(h.motionPosts().length, 1)
    await h.advance(4999)
    assert.equal(motion.getState().submitting, true); assert.equal(h.motionPosts().length, 1)
    await h.advance(1); await first
    assert.equal(h.motionPosts().length, 1); assert.equal(motion.getState().uncertain, true)
    const timedOut = motion.getState()
    h.success(motionSnapshot(true, execution('ended_unverified')), old); await flush()
    assert.deepEqual(motion.getState(), timedOut)
    const retry = motion.retryExecute(); assert.equal(h.motionPosts().length, 2)
    assert.deepEqual(plain(h.motionPosts()[0].data), plain(h.motionPosts()[1].data))
    h.success(motionSnapshot(true, execution('ended_unverified'))); await retry
    assert.equal(motion.getState().snapshot.execution.status, 'ended_unverified')
  },
  async 'stop is immediate during execute and late execute cannot overwrite stop'() {
    const h = harness(), motion = await h.controller(true), first = motion.execute('head_nod'), firstIndex = h.requests.length - 1
    const stop = motion.stop()
    assert.equal(h.motionPosts().length, 2); assert.ok(h.requests.at(-1).url.endsWith('/stop'))
    h.success(motionSnapshot(true, { ...execution('stopped'), actionId: 'stop' })); await stop
    h.success(motionSnapshot(true, execution()), firstIndex); await first
    assert.equal(motion.getState().snapshot.execution.status, 'stopped')
  },
  async 'stop pins execution id and retains id for explicit stop failure retry'() {
    const h = harness(), motion = await h.controller(true, motionSnapshot(true, execution()))
    const stop = motion.stop(); const payload = plain(h.requests.at(-1).data)
    assert.equal(payload.executionId, 'exec_1'); h.failure(); await stop
    const retry = motion.stop(); assert.deepEqual(plain(h.requests.at(-1).data), payload)
    h.success(motionSnapshot(true, execution('stopped'))); await retry
  },
  async 'authorization recheck visibly rejects stop without losing original unknown stop id'() {
    const h = harness(), motion = await h.controller(true, motionSnapshot(true, execution()))
    const stop = motion.stop(), original = plain(h.requests.at(-1).data)
    h.failure(); await stop
    assert.equal(motion.getState().uncertain, true)
    const recheck = h.session.refreshPets()
    const listIndex = h.requests.findLastIndex(item => item.url.endsWith('/v1/pets'))
    await motion.stop()
    assert.match(motion.getState().error, /停止请求未发送.*登录或设备授权正在更新.*刷新.*现场检查/)
    assert.equal(motion.getState().uncertain, true); assert.equal(h.motionPosts().length, 1)
    h.success(pets, listIndex); await flush()
    h.success(deviceSnapshot(), h.requests.findLastIndex(item => item.url.endsWith('/status')))
    await recheck
    h.success(motionSnapshot(true, execution()), h.requests.findLastIndex(item => item.url.endsWith('/motion')))
    await flush()
    const retry = motion.stop()
    assert.deepEqual(plain(h.requests.at(-1).data), original)
    assert.equal(h.motionPosts().length, 2)
    h.success(motionSnapshot(true, { ...execution('stopped'), actionId: 'stop' })); await retry
  },
  async 'duplicate in-flight stop shares its promise without changing status or posting again'() {
    const h = harness(), motion = await h.controller(true, motionSnapshot(true, execution()))
    const stop = motion.stop(), before = motion.getState(), writes = h.views.length
    const duplicate = motion.stop()
    assert.equal(duplicate, stop); assert.equal(h.motionPosts().length, 1)
    assert.deepEqual(motion.getState(), before); assert.equal(h.views.length, writes)
    assert.equal(motion.getState().stopping, true); assert.equal(motion.getState().error, '')
    h.success(motionSnapshot(true, { ...execution('stopped'), actionId: 'stop' }))
    await Promise.all([stop, duplicate]); assert.equal(h.motionPosts().length, 1)
  },
  async 'disabled or unverified rejection preserves an unknown stop for same-id explicit retry'() {
    for (const change of [{ enabled: false }, { verified: false }]) {
      const h = harness(), motion = await h.controller(true, motionSnapshot(true, execution()))
      const stop = motion.stop(), original = plain(h.requests.at(-1).data)
      h.failure(); await stop
      const lockedRead = motion.read()
      h.success({ ...motionSnapshot(true, execution()), ...change }); await lockedRead
      await motion.stop()
      assert.match(motion.getState().error, /停止请求未发送/)
      assert.equal(motion.getState().uncertain, true); assert.equal(h.motionPosts().length, 1)
      const recoveredRead = motion.read()
      h.success(motionSnapshot(true, execution())); await recoveredRead
      const retry = motion.stop()
      assert.deepEqual(plain(h.requests.at(-1).data), original)
      h.success(motionSnapshot(true, { ...execution('stopped'), actionId: 'stop' })); await retry
      assert.equal(h.motionPosts().length, 2)
    }
  },
  async 'stop during a new unresolved execute never carries older execution target'() {
    const old = motionSnapshot(true, execution('ended_unverified', 'exec_old', 'old_request'))
    old.actions.forEach(item => { item.available = true })
    const h = harness(), motion = await h.controller(true, old)
    const execute = motion.execute('head_nod'), oldIndex = h.requests.length - 1
    const stop = motion.stop(); assert.ok(!Object.prototype.hasOwnProperty.call(h.requests.at(-1).data, 'executionId'))
    h.success(motionSnapshot(true, { ...execution('stopped'), actionId: 'stop' })); await stop
    h.success(motionSnapshot(true, execution()), oldIndex); await execute
  },
  async 'repeat stop never uses a prior stop receipt as head execution target'() {
    const value = motionSnapshot(true, { ...execution('stopped', 'stop_receipt'), actionId: 'stop' })
    value.deviceBusy = true; value.actions.forEach(item => { item.available = false })
    const h = harness(), motion = await h.controller(true, value), stop = motion.stop()
    assert.ok(!Object.prototype.hasOwnProperty.call(h.requests.at(-1).data, 'executionId'))
    h.success(value); await stop
  },
  async 'no background poll or automatic stop when page hides'() {
    const h = harness(), motion = await h.controller(true), count = h.requests.length
    motion.hide(); await h.advance(3100)
    assert.equal(h.requests.filter(item => /\/motion/.test(item.url)).length, 1)
    assert.equal(h.motionPosts().length, 0); assert.equal(h.requests.length, count)
  },
  async 'hidden stop neither submits nor writes guarded error feedback'() {
    const h = harness(), motion = await h.controller(true)
    motion.hide(); const before = motion.getState(), writes = h.views.length, requests = h.requests.length
    await motion.stop(); await h.advance(3100)
    assert.deepEqual(motion.getState(), before); assert.equal(h.views.length, writes)
    assert.equal(h.requests.length, requests); assert.equal(h.motionPosts().length, 0)
  },
  async 'full detach and authorization recheck preserve ambiguous request id for same device'() {
    const h = harness(), motion = await h.controller(true), send = motion.execute('head_nod')
    const original = plain(h.requests.at(-1).data)
    motion.hide(); h.anchor(); await send
    motion.show(() => {}); await flush()
    // Reattachment must recheck ownership without creating a new command.
    const petsIndex = h.requests.findLastIndex(item => item.url.endsWith('/v1/pets'))
    h.success(pets, petsIndex); await flush()
    const deviceIndex = h.requests.findLastIndex(item => item.url.endsWith('/status'))
    h.success(deviceSnapshot(), deviceIndex)
    const motionIndex = h.requests.findLastIndex(item => item.url.endsWith('/motion'))
    h.success(motionSnapshot(true), motionIndex); await flush()
    assert.equal(motion.getState().uncertain, true); assert.equal(motion.getState().retryAvailable, true)
    assert.equal(h.motionPosts().length, 1)
    const retry = motion.retryExecute(); assert.deepEqual(plain(h.requests.at(-1).data), original)
    h.success(motionSnapshot(true, execution('ended_unverified'))); await retry
    assert.equal(h.motionPosts().length, 2)
  },
  async 'old selected pet read cannot update newly selected pet'() {
    const h = harness(), motion = await h.controller(true), old = motion.read(), oldIndex = h.requests.length - 1
    const selected = h.session.selectPet('pet_2'); const currentIndex = h.requests.length - 1
    // Session state change starts an independent safe motion GET; locate status explicitly.
    const statusIndex = h.requests.findLastIndex(item => item.url.endsWith('/status'))
    h.success(deviceSnapshot(), statusIndex); await selected
    const motionIndex = h.requests.findLastIndex(item => item.url.endsWith('/motion'))
    h.success(motionSnapshot(false), motionIndex); await flush()
    h.success(motionSnapshot(true, execution()), oldIndex); await old
    assert.equal(motion.getState().snapshot.enabled, false); assert.equal(h.motionPosts().length, 0)
  },
  async '401 resets identity and all action state'() {
    const h = harness(), motion = await h.controller(true), read = motion.read()
    h.success({}, h.requests.length - 1, 401); await read
    assert.equal(h.session.getState().signedIn, false); assert.equal(motion.getState().snapshot, null)
    await motion.execute('head_nod'); assert.equal(h.motionPosts().length, 0)
  },
  async 'logout or background clear cannot leave actionable state'() {
    const h = harness(), motion = await h.controller(true)
    h.session.clear(); assert.equal(motion.getState().snapshot, null)
    await motion.execute('head_nod'); await motion.stop(); assert.equal(h.motionPosts().length, 0)
  },
  async 'read-only polling does not invent completion and preserves ended-unverified result'() {
    const h = harness(), motion = await h.controller(true, motionSnapshot(true, execution('ended_unverified')))
    await h.advance(3000); assert.equal(h.requests.at(-1).method, 'GET')
    h.success(motionSnapshot(true, execution('ended_unverified'))); await flush()
    assert.equal(motion.getState().snapshot.execution.status, 'ended_unverified'); assert.equal(h.motionPosts().length, 0)
  },
  async 'health accepts explicit boolean write capability without granting command permission'() {
    const h = harness(), client = h.load('services/cloud-auth-client').createCloudAuthClient(h.wx)
    const read = client.getHealth(); h.success({ mode: 'local-development', wechatConfigured: true, readOnly: false })
    assert.equal((await read).readOnly, false)
    await assert.rejects(client.executeMotion('pet_1', 'head_nod', 'req_1'), error => error.code === 'AUTH_REQUIRED')
    for (const invalid of [undefined, null, 0, 1, 'false', {}]) {
      const value = client.getHealth(); h.success({ mode: 'local-development', wechatConfigured: true, readOnly: invalid })
      await assert.rejects(value, error => error.code === 'UNVERIFIED')
    }
  },
  async 'cloud client rejects arbitrary action, routes and identifiers before HTTP'() {
    const h = harness(), client = h.load('services/cloud-auth-client').createCloudAuthClient(h.wx)
    for (const action of ['tail', 'wag-tail', 'proud-turn', 'quiet-company', 'greeting', 'level-up', 'head_nod?angle=10', 'stop', '__proto__', null, ['head_nod'], {}]) await assert.rejects(client.executeMotion('pet_1', action, 'request'), error => error.code === 'INVALID_ID')
    for (const value of ['', '../pet', 'secret/stop', 'a'.repeat(129)]) await assert.rejects(client.readMotion(value), error => error.code === 'INVALID_ID')
    await assert.rejects(client.stopMotion('pet_1', 'request', '../exec'), error => error.code === 'INVALID_ID')
    await assert.rejects(client.executeMotion('pet_1', 'head_nod', 'a'.repeat(65)), error => error.code === 'INVALID_ID')
    assert.equal(h.requests.length, 0)
  },
  async 'cloud response strips extra fields and never returns bearer to views'() {
    const h = harness(); await h.controller(true)
    const serialized = JSON.stringify(h.motion.getState()) + JSON.stringify(h.session.getState())
    assert.ok(!serialized.includes(TOKEN)); assert.equal(h.logs.length, 0); assert.equal(h.storageCalls(), 0)
    const value = h.motionApi.verifyMotion({ ...motionSnapshot(), accessToken: TOKEN, rawSerial: 'private' })
    assert.equal(value.accessToken, undefined); assert.equal(value.rawSerial, undefined)
  },
  async 'strict motion validator rejects malformed or invented status'() {
    const h = harness(), valid = motionSnapshot(true)
    for (const value of [null, {}, { ...valid, enabled: 1 }, { ...valid, verified: 'true' }, { ...valid, deviceBusy: null },
      { ...valid, sampledAt: 'yesterday' }, { ...valid, actions: [...valid.actions, valid.actions[0]] },
      { ...valid, actions: [] }, { ...valid, actions: [valid.actions[0]] },
      { ...valid, actions: valid.actions.slice(0, 2) },
      { ...valid, enabled: false }, { ...valid, verified: false }, { ...valid, deviceBusy: true },
      ...[{ actionId: 'tail' }, { actionId: ['head_nod'] }, { actionId: valid.actions[1].actionId }, { available: 'true' }, { reason: null }, { reason: 'x'.repeat(161) }, { reason: '安全\u202e隐藏' }, { reason: '安全\u0085隐藏' }, { reason: 'raw\nsecret' }, { name: 'x'.repeat(81) }, { name: 'bad\u2066name' }].map(change => ({ ...valid, actions: valid.actions.map((item, index) => index ? item : { ...item, ...change }) })),
      { ...valid, execution: { ...execution(), status: 'completed' } },
      { ...valid, execution: { ...execution(), executionId: '../id' } },
      { ...valid, execution: { ...execution(), requestId: '\nsecret' } }]) assert.throws(() => h.motionApi.verifyMotion(value))
  },
  async 'no-tail validator accepts only explicit verified false and exact three canonical heads'() {
    const h = harness(), sample = noTailSnapshot(), legacy = h.motionApi.verifyMotion(motionSnapshot(true))
    assert.equal(h.motionApi.verifyMotion(sample).actions.length, 3)
    assert.equal(legacy.hasTail, null); assert.equal(legacy.deviceMotionEnabled, null); assert.equal(legacy.actions.length, 7)
    for (const value of [{ ...sample, hasTail: null }, { ...sample, hasTail: true }, { ...sample, hasTail: undefined },
      { ...sample, verified: false }, { ...sample, hasTail: 0 }, { ...sample, deviceMotionEnabled: 'false' },
      { ...sample, actions: motionSnapshot(true).actions }, { ...sample, actions: sample.actions.map((item, index) => index ? item : { ...item, actionId: 'tail_wag' }) },
      { ...sample, deviceMotionEnabled: false }]) assert.throws(() => h.motionApi.verifyMotion(value))
  },
  async 'no-tail controller never sends a missing tail action and still executes a fixed head command'() {
    const h = harness(), motion = await h.controller(true, noTailSnapshot())
    for (const id of ['tail_wag', 'proud_pose', 'greeting_combo', 'celebration_combo']) await motion.execute(id)
    assert.equal(h.motionPosts().length, 0)
    const send = motion.execute('head_nod'); h.success(noTailSnapshot(execution())); await send
    assert.equal(h.motionPosts().length, 1); assert.equal(h.motionPosts()[0].data.actionId, 'head_nod')
  },
  async 'paused device blocks direct execute and same-id retry but leaves authenticated stop independent'() {
    const h = harness(), motion = await h.controller(true, noTailSnapshot(execution(), true))
    await motion.execute('head_nod'); await motion.retryExecute(); assert.equal(h.motionPosts().length, 0)
    const stop = motion.stop(); assert.ok(h.requests.at(-1).url.endsWith('/stop'))
    h.success(noTailSnapshot({ ...execution('stopped'), actionId: 'stop' }, true)); await stop
    assert.equal(h.motionPosts().length, 1); assert.equal(motion.getState().snapshot.execution.status, 'stopped')
  },
  async 'paused refreshed snapshot keeps an uncertain original id without retrying execute'() {
    const h = harness(), motion = await h.controller(true)
    const send = motion.execute('head_nod'); h.failure(); await send
    const read = motion.read(); h.success(noTailSnapshot(null, true)); await read
    await motion.retryExecute(); assert.equal(h.motionPosts().length, 1); assert.equal(motion.getState().retryAvailable, true)
    assert.match(motion.getState().error, /设备动作已暂停/)
  },
  async 'old tail history remains valid after the current device catalog becomes no-tail'() {
    const h = harness(), record = { ...execution('ended_unverified'), actionId: 'tail_wag' }
    assert.equal(h.motionApi.verifyMotion({ ...noTailSnapshot(), execution: record }).execution.actionId, 'tail_wag')
    assert.equal(h.motionApi.verifyMotionHistory({ items: [record], limit: 20 }).items[0].actionId, 'tail_wag')
  }
}

async function main() {
  for (const [name, run] of Object.entries(cases)) {
    try { await run() } catch (error) { console.error('FAIL:', name); throw error }
  }
  console.log('live-motion-smoke: ' + Object.keys(cases).length + '/' + Object.keys(cases).length + ' passed (injected wx; no real HTTP/device writes)')
}
if (require.main === module) main().catch(error => { console.error(error); process.exitCode = 1 })
module.exports = { harness, motionSnapshot, noTailSnapshot, execution, deviceSnapshot, flush, plain, ACTION_IDS }
