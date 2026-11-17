// Strict command/status boundary. No bearer, hardware parameters or automatic POST retries.
const MOTION_NAMES: any = {
  head_nod: '点头', head_shake: '摇头', tail_wag: '开心摇尾', proud_pose: '骄傲转身',
  quiet_companion: '安静陪伴', greeting_combo: '见面问候', celebration_combo: '升级庆祝'
}
const MOTION_STATES: any = {
  sending: '正在发送动作', running: '动作执行中', unknown: '结果不确定，请停止并检查',
  stopped: '停止请求已确认', ended_unverified: '动作已结束，结果待验证', failed: '动作执行失败'
}
const object = (value: any) => value && typeof value === 'object' && !Array.isArray(value)
const id = (value: any) => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value)
const requestIdValid = (value: any) => typeof value === 'string' && /^[A-Za-z0-9_.:-]{1,64}$/.test(value)
const actionValid = (value: any) => typeof value === 'string' && Object.prototype.hasOwnProperty.call(MOTION_NAMES, value)
const HEAD_ACTIONS = ['head_nod', 'head_shake', 'quiet_companion']
// Reasons are rendered as plain text, never HTML. Reject invisible controls that
// could conceal or reorder a safety restriction, and bound all server UI text.
const unsafeText = /[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/
function utc(value: any) {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(value) || !Number.isFinite(Date.parse(value)) || new Date(Date.parse(value)).toISOString().slice(0, 19) !== value.slice(0, 19)) throw new Error('UNVERIFIED')
  return value
}
function verifyExecution(item: any) {
  if (!object(item) || !id(item.executionId) || (!actionValid(item.actionId) && item.actionId !== 'stop') || !Object.prototype.hasOwnProperty.call(MOTION_STATES, item.status) || !requestIdValid(item.requestId)) throw new Error('UNVERIFIED')
  return { executionId: item.executionId, actionId: item.actionId, status: item.status, requestId: item.requestId, updatedAt: utc(item.updatedAt) }
}
function verifyMotionHistory(value: any) {
  if (!object(value) || value.limit !== 20 || !Array.isArray(value.items) || value.items.length > 20) throw new Error('UNVERIFIED')
  const seen = new Set()
  const items = value.items.map((item: any) => {
    const execution = verifyExecution(item)
    if (seen.has(execution.executionId)) throw new Error('UNVERIFIED')
    seen.add(execution.executionId)
    return execution
  })
  return { items, limit: 20 }
}
function verifyMotion(value: any) {
  if (!object(value) || typeof value.enabled !== 'boolean' || typeof value.verified !== 'boolean' || typeof value.deviceBusy !== 'boolean' || !Array.isArray(value.actions)) throw new Error('UNVERIFIED')
  for (const field of ['hasTail', 'deviceMotionEnabled']) {
    if (Object.prototype.hasOwnProperty.call(value, field) && value[field] !== null && typeof value[field] !== 'boolean') throw new Error('UNVERIFIED')
  }
  const hasTail = value.hasTail === undefined ? null : value.hasTail
  const deviceMotionEnabled = value.deviceMotionEnabled === undefined ? null : value.deviceMotionEnabled
  const expected = hasTail === false ? HEAD_ACTIONS : Object.keys(MOTION_NAMES)
  if (value.actions.length !== expected.length || hasTail === false && !value.verified) throw new Error('UNVERIFIED')
  const seen = new Set()
  const actions = value.actions.map((item: any) => {
    if (!object(item) || !actionValid(item.actionId) || seen.has(item.actionId) || typeof item.available !== 'boolean' || typeof item.name !== 'string' || item.name.length > 80 || unsafeText.test(item.name) || typeof item.reason !== 'string' || item.reason.length > 160 || unsafeText.test(item.reason)) throw new Error('UNVERIFIED')
    if (!expected.includes(item.actionId) || item.available && (!value.enabled || !value.verified || value.deviceBusy || deviceMotionEnabled === false)) throw new Error('UNVERIFIED')
    seen.add(item.actionId)
    return { actionId: item.actionId, name: MOTION_NAMES[item.actionId], available: item.available, reason: item.reason.trim() }
  })
  let execution: any = null
  if (value.execution !== null) {
    execution = verifyExecution(value.execution)
  }
  return { enabled: value.enabled, verified: value.verified, deviceBusy: value.deviceBusy, hasTail, deviceMotionEnabled, actions, execution, sampledAt: utc(value.sampledAt) }
}

function createLiveMotion(session: any, makeRequestId: any) {
  let visible = false, detach: any = null, timer: any = null, epoch = 0, key = '', ready = false, callback: any = null
  let readFlight: any = null, executeFlight: any = null, stopFlight: any = null
  let pendingExecute: any = null, pendingStop: any = null, preparedCommand: any = null, revision = 0
  let state: any = { snapshot: null, loading: false, submitting: false, stopping: false, error: '', uncertain: false }
  const clone = (value: any) => JSON.parse(JSON.stringify(value))
  function getState() { return clone({ ...state, retryAvailable: Boolean(pendingExecute) }) }
  function emit() { if (visible && callback) callback(getState()) }
  function patch(value: any) { state = { ...state, ...value }; emit() }
  function sessionKey() {
    const value = session.getState()
    return value.mode === 'live' && value.signedIn && value.selectedPetId && value.pets.some((pet: any) => pet.petId === value.selectedPetId) ? value.selectedPetId + '|' + value.expiresAt : ''
  }
  function selectionReady() { const value = session.getState(); return Boolean(sessionKey() && !value.listLoading && !value.loading) }
  function current(expected: number) { return visible && ready && epoch === expected && key && sessionKey() === key && selectionReady() }
  function cancelTimer() { clearTimeout(timer); timer = null }
  function schedule() {
    cancelTimer()
    if (!visible || !ready || !key || readFlight || executeFlight || stopFlight) return
    const expected = epoch
    const snapshot = state.snapshot
    const active = state.uncertain || snapshot && (snapshot.deviceBusy || snapshot.execution && ['sending', 'running', 'unknown'].includes(snapshot.execution.status))
    const cooling = snapshot && snapshot.verified && snapshot.actions.some((item: any) => !item.available && item.reason === '动作冷却中')
    // Observe a short preset promptly, without increasing idle polling or ever
    // resending a POST. Elapsed time is not evidence of physical completion.
    // A server-reported cooldown gets a short GET, never a client-side unlock.
    // Unknown/running contexts retain their normal one-second review cadence.
    timer = setTimeout(() => { timer = null; if (current(expected)) read() }, active ? 1000 : cooling ? 400 : 3000)
  }
  function read(): Promise<any> {
    if (!visible || !ready || !key || sessionKey() !== key || !selectionReady()) return Promise.resolve()
    if (executeFlight || stopFlight) return Promise.resolve()
    if (readFlight) return readFlight.promise
    cancelTimer()
    const flight: any = { expected: epoch, revision }
    readFlight = flight
    patch({ loading: true })
    flight.promise = session.readMotion().then((snapshot: any) => {
      if (current(flight.expected) && flight.revision === revision) {
        const receipt = snapshot.execution
        const previous = state.snapshot && state.snapshot.execution
        const pending = pendingStop ? { ...pendingStop, actionId: 'stop' } : pendingExecute
        const expectedReceipt = pending || previous
        const sameReceipt = receipt && expectedReceipt && receipt.requestId === expectedReceipt.requestId && receipt.actionId === expectedReceipt.actionId
        const settled = sameReceipt && ['stopped', 'ended_unverified', 'failed'].includes(receipt.status)
        // A lost ACK must not leave the view locked forever after an authenticated
        // GET returns that exact request's terminal receipt. Another request, an
        // idle device, or a timeout alone cannot clear the unresolved context.
        if (settled) { pendingExecute = null; pendingStop = null }
        patch({ snapshot, error: '', uncertain: settled ? false : Boolean(state.uncertain || receipt && receipt.status === 'unknown') })
      }
    }).catch((error: any) => {
      if (current(flight.expected) && flight.revision === revision && error.code !== 'CANCELLED') {
        const previous = state.snapshot
        const mayBeMoving = state.uncertain || previous && (previous.deviceBusy || previous.execution && ['sending', 'running', 'unknown'].includes(previous.execution.status))
        // Retain only an explicitly uncertain stop context, never permit a new action
        // merely because the latest read failed while a real command might be running.
        patch({ snapshot: mayBeMoving ? previous : null, uncertain: Boolean(mayBeMoving), error: '动作状态暂不可用，请刷新重试；若动作可能执行中请停止并检查' })
      }
    }).finally(() => {
      if (!current(flight.expected) || readFlight !== flight) return
      readFlight = null; patch({ loading: false }); schedule()
    })
    return flight.promise
  }
  function reset() {
    epoch++; revision++; cancelTimer(); readFlight = null; executeFlight = null; stopFlight = null
    pendingExecute = null; pendingStop = null; preparedCommand = null
    state = { snapshot: null, loading: false, submitting: false, stopping: false, error: '', uncertain: false }; emit()
  }
  function selectionChanged() {
    const next = sessionKey(), nextReady = selectionReady(), changed = next !== key, resumed = !ready && nextReady
    if (changed) { key = next; ready = nextReady; reset() }
    else if (ready && !nextReady) {
      // Rechecking authorization suspends reads/controls without discarding a
      // possibly sent command's idempotency key for this same identity/device.
      epoch++; revision++; cancelTimer(); readFlight = null; executeFlight = null; stopFlight = null
      preparedCommand = null
      state = { ...state, snapshot: null, loading: false, submitting: false, stopping: false,
        uncertain: Boolean(pendingExecute || pendingStop || state.uncertain) }; emit()
    }
    ready = nextReady
    if (key && ready && (changed || resumed)) read()
  }
  function notSent(message: string): Promise<any> {
    // A safety guard must not look like a successful confirmation. Keep any
    // uncertain request/id intact; this reports a rejection, never retries it.
    patch({ error: '动作未发送：' + message })
    return Promise.resolve()
  }
  function execute(actionId: string): Promise<any> {
    // Hidden views cannot receive UI updates or submit an old confirmation.
    if (!visible) return Promise.resolve()
    if (!ready || !key || sessionKey() !== key || !selectionReady()) return notSent('登录或设备授权正在更新，请刷新状态后重新选择并确认')
    if (executeFlight || stopFlight) return notSent('已有请求正在处理，请等待结果；若设备可能运行请停止并检查')
    const snapshot = state.snapshot
    if (!snapshot) return notSent('动作状态尚未验证，请点击“刷新动作状态”后重新选择并确认')
    if (!snapshot.enabled) return notSent('服务端动作开关未开启，请等待现场测试授权')
    if (snapshot.deviceMotionEnabled === false) return notSent('设备动作已暂停；停止请求仍独立可用')
    if (!snapshot.verified) return notSent('设备身份或状态未验证，请刷新动作状态后重新确认')
    if (state.uncertain) return notSent('上一请求结果不确定，请停止并检查，或使用同一请求编号显式重试')
    if (snapshot.deviceBusy || snapshot.execution && ['sending', 'running', 'unknown'].includes(snapshot.execution.status)) return notSent('设备当前忙或已有动作，请等待并刷新；必要时停止并检查')
    if (!snapshot.actions.some((item: any) => item.actionId === actionId && item.available)) return notSent('所选动作当前不可执行，请刷新动作状态后重新选择')
    const retry = pendingExecute && pendingExecute.actionId === actionId
    if (pendingExecute && !retry) return notSent('仍有未确认请求，请停止并检查，或使用同一请求编号显式重试')
    const command = retry ? pendingExecute : { actionId, requestId: makeRequestId('motion') }
    pendingExecute = command
    const flight: any = { expected: epoch, revision: ++revision }
    executeFlight = flight; cancelTimer()
    patch({ submitting: true, error: '' })
    flight.promise = session.executeMotion(command.actionId, command.requestId).then((value: any) => {
      if (!current(flight.expected) || flight.revision !== revision) return
      pendingExecute = null; patch({ snapshot: value, error: '', uncertain: value.execution && value.execution.status === 'unknown' })
    }).catch((error: any) => {
      if (!current(flight.expected) || flight.revision !== revision || error.code === 'CANCELLED') return
      patch({ error: '动作请求结果未确认；可停止并检查，或使用同一请求编号显式重试', uncertain: true })
    }).finally(() => {
      if (!current(flight.expected) || executeFlight !== flight) return
      executeFlight = null; patch({ submitting: false }); schedule()
    })
    return flight.promise
  }
  function prepare(actionId: string) {
    // This is an in-memory, single-use UI context, not authorization. The same
    // synchronous tap confirms it; execute still rechecks all server policy.
    preparedCommand = null
    if (!visible || !current(epoch) || !actionValid(actionId)) return null
    const token = { actionId, epoch, revision, key }
    preparedCommand = token
    return token
  }
  function confirm(token: any): Promise<any> {
    if (!token || token !== preparedCommand) return Promise.resolve()
    preparedCommand = null
    if (!current(token.epoch) || token.key !== key || token.revision !== revision) return notSent('设备或授权已变化，请在当前设备重新点击动作')
    return execute(token.actionId)
  }
  function retryExecute(): Promise<any> {
    // A retry is explicit and reuses the exact request id; no second physical command is invented.
    if (!visible) return Promise.resolve()
    if (!ready || !key || sessionKey() !== key || !selectionReady()) return notSent('登录或设备授权正在更新，请刷新状态后重新选择并确认')
    if (!pendingExecute || !state.uncertain) return notSent('没有可显式重试的同编号请求，请刷新动作状态后重新选择')
    if (executeFlight || stopFlight) return notSent('已有请求正在处理，请等待结果；若设备可能运行请停止并检查')
    const snapshot = state.snapshot
    if (!snapshot || !snapshot.verified) return notSent('动作状态尚未验证，请刷新后重试同一请求；不要创建新动作')
    if (!snapshot.enabled) return notSent('服务端动作开关未开启，请等待现场测试授权')
    if (snapshot.deviceMotionEnabled === false) return notSent('设备动作已暂停；请停止并检查，不重试执行')
    if (snapshot.hasTail === false && !HEAD_ACTIONS.includes(pendingExecute.actionId)) return notSent('设备没有尾部机构，不能重试该动作；请停止并检查')
    const command = pendingExecute
    const flight: any = { expected: epoch, revision: ++revision }
    executeFlight = flight; cancelTimer(); patch({ submitting: true, error: '' })
    flight.promise = session.executeMotion(command.actionId, command.requestId).then((value: any) => {
      if (!current(flight.expected) || flight.revision !== revision) return
      pendingExecute = null; patch({ snapshot: value, uncertain: Boolean(value.execution && value.execution.status === 'unknown'), error: '' })
    }).catch((error: any) => {
      if (current(flight.expected) && flight.revision === revision && error.code !== 'CANCELLED') patch({ uncertain: true, error: '动作结果仍未确认，请停止并检查' })
    }).finally(() => {
      if (!current(flight.expected) || executeFlight !== flight) return
      executeFlight = null; patch({ submitting: false }); schedule()
    })
    return flight.promise
  }
  function stop(): Promise<any> {
    if (!visible) return Promise.resolve()
    // Repeated taps only share the original request. Do not replace an in-flight
    // stop's status with a guard error or create a second stop request/id.
    if (stopFlight) return stopFlight.promise || Promise.resolve()
    const notStopped = (message: string) => {
      // Report only that this attempt was not submitted. Keep any prior unknown
      // request/id intact; this is not evidence that the device has stopped.
      patch({ error: '停止请求未发送：' + message })
      return Promise.resolve()
    }
    if (!ready || !key || sessionKey() !== key || !selectionReady()) return notStopped('登录或设备授权正在更新，请刷新状态后重试停止，同时现场检查设备')
    const snapshot = state.snapshot
    if (!snapshot) return notStopped('动作状态尚未验证，请点击“刷新动作状态”后重试停止，同时现场检查设备')
    if (!snapshot.enabled) return notStopped('服务端动作开关未开启，无法发送停止，请现场检查设备并联系操作员')
    if (!snapshot.verified) return notStopped('设备身份或状态未验证，请刷新动作状态后重试停止，同时现场检查设备')
    // A previous result must never target a newly submitted, unresolved command.
    // A stop receipt is not a head execution target either. Omitting the target
    // represents the user's explicit request to stop the current real action.
    const executionId = !executeFlight && !pendingExecute && snapshot.execution && snapshot.execution.actionId !== 'stop' ? snapshot.execution.executionId : undefined
    const requestId = pendingStop && pendingStop.executionId === executionId ? pendingStop.requestId : makeRequestId('stop')
    pendingStop = { requestId, ...(executionId ? { executionId } : {}) }
    const flight: any = { expected: epoch, revision: ++revision }
    stopFlight = flight; cancelTimer(); patch({ stopping: true, error: '' })
    flight.promise = session.stopMotion(requestId, executionId || undefined).then((value: any) => {
      if (!current(flight.expected) || flight.revision !== revision) return
      pendingExecute = null; pendingStop = null
      patch({ snapshot: value, uncertain: Boolean(value.execution && value.execution.status === 'unknown'), error: '' })
    }).catch((error: any) => {
      if (current(flight.expected) && flight.revision === revision && error.code !== 'CANCELLED') patch({ uncertain: true, error: '停止结果未确认，请检查设备并重试停止' })
    }).finally(() => {
      if (!current(flight.expected) || stopFlight !== flight) return
      stopFlight = null; patch({ stopping: false }); schedule()
    })
    return flight.promise
  }
  function hide() {
    visible = false; ready = false; callback = null; epoch++; revision++; cancelTimer()
    readFlight = null; executeFlight = null; stopFlight = null
    preparedCommand = null
    if (detach) detach(); detach = null
    // Never stop a real machine automatically when navigating away.
    state = { ...state, snapshot: null, loading: false, submitting: false, stopping: false,
      uncertain: Boolean(pendingExecute || pendingStop || state.uncertain) }
  }
  function show(listener: any) {
    hide(); visible = true; callback = listener
    detach = session.attach(selectionChanged); selectionChanged(); if (key) read(); emit()
  }
  return { show, hide, read, execute, prepare, confirm, retryExecute, stop, getState }
}

// Independent read-only history lane: slow/failing reads never serialize a stop.
function createLiveMotionHistory(session: any) {
  let visible = false, listener: any = null, detach: any = null, flight: any = null
  let key = '', epoch = 0, ready = false, dirty = false
  let state: any = { items: null, loading: false, error: '' }
  function emit() { if (visible && listener) listener(JSON.parse(JSON.stringify(state))) }
  function selection() {
    const value = session.getState()
    return value.mode === 'live' && value.signedIn && value.selectedPetId && value.pets.some((pet: any) => pet.petId === value.selectedPetId) ? value.selectedPetId + '|' + value.expiresAt : ''
  }
  function usable() { const value = session.getState(); return Boolean(selection() && !value.loading && !value.listLoading) }
  function read(latest = false): Promise<any> {
    if (!visible || !ready || !key || key !== selection() || !usable()) return Promise.resolve()
    if (flight) { if (latest) dirty = true; return flight.promise }
    const current: any = { epoch, key }
    flight = current; state = { ...state, loading: true, error: '' }; emit()
    const valid = () => visible && ready && current.epoch === epoch && current.key === selection() && flight === current && usable()
    current.promise = session.readMotionHistory().then((value: any) => {
      if (valid() && !dirty) { state = { items: value.items, loading: true, error: '' }; emit() }
    }).catch((error: any) => {
      if (valid() && error.code !== 'CANCELLED') { state = { items: null, loading: true, error: '动作记录暂不可用，请重试；不影响停止当前动作' }; emit() }
    }).finally(() => {
      if (!valid()) return
      flight = null
      if (dirty) { dirty = false; read(); return }
      state = { ...state, loading: false }; emit()
    })
    return current.promise
  }
  function changed() {
    const next = selection(), nextReady = usable(), resumed = nextReady && !ready, identityChanged = next !== key
    if (next !== key || !nextReady && ready) {
      epoch++; flight = null; dirty = false; key = next; state = { items: null, loading: false, error: '' }; emit()
    }
    const needsRead = identityChanged || resumed
    key = next; ready = nextReady
    if (key && ready && needsRead) read()
  }
  function hide() {
    visible = false; epoch++; flight = null; dirty = false; listener = null; ready = false
    if (detach) detach(); detach = null
    state = { items: null, loading: false, error: '' }
  }
  function show(callback: any) { hide(); visible = true; listener = callback; detach = session.attach(changed); changed(); emit() }
  return { show, hide, read }
}

module.exports = { verifyMotion, verifyMotionHistory, actionValid, requestIdValid, MOTION_NAMES, MOTION_STATES, createLiveMotion, createLiveMotionHistory }
