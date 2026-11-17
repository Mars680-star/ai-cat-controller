// Explicit ordinary preferences only. Names/feedback stay in memory; persistent
// unresolved markers contain only identity, device, request ID and kind.
const STORAGE_KEY = 'aiCatPreferencesPendingV1'
const KINDS = ['settings', 'feedback']
const FIELDS = ['name', 'volume', 'followUpSeconds']
const CATEGORIES = ['device', 'dialog', 'motion', 'account', 'other']
const unsafe = /[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/
const feedbackUnsafe = /[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/
const object = (value: any) => value && typeof value === 'object' && !Array.isArray(value)
const id = (value: any) => typeof value === 'string' && value.length >= 1 && value.length <= 128 && !/[^A-Za-z0-9_-]/.test(value)
const requestIdValid = (value: any) => typeof value === 'string' && value.length >= 1 && value.length <= 64 && !/[^A-Za-z0-9_.:-]/.test(value)
function invalid(): never { throw new Error('UNVERIFIED') }
function exact(value: any, keys: string[]) {
  if (!object(value) || Object.keys(value).length !== keys.length || keys.some(key => !Object.prototype.hasOwnProperty.call(value, key))) return invalid()
}
function bool(value: any): boolean { if (typeof value !== 'boolean') return invalid(); return value }
function integer(value: any, min: number, max: number): number { if (!Number.isInteger(value) || typeof value !== 'number' || value < min || value > max) return invalid(); return value }
function utc(value: any): string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(value) || !Number.isFinite(Date.parse(value)) || new Date(Date.parse(value)).toISOString().slice(0, 19) !== value.slice(0, 19)) return invalid()
  return value
}
function codePoints(value: string): number {
  let length = 0
  for (let index = 0; index < value.length; index++) {
    const code = value.charCodeAt(index)
    if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(++index)
      if (!(next >= 0xdc00 && next <= 0xdfff)) return invalid()
    } else if (code >= 0xdc00 && code <= 0xdfff) return invalid()
    length++
  }
  return length
}
function text(value: any, min: number, max: number, multiline = false): string {
  if (typeof value !== 'string' || value.length > max * 2 || !value.trim() || (multiline ? feedbackUnsafe : unsafe).test(value)) return invalid()
  const length = codePoints(value)
  if (length < min || length > max) return invalid()
  return value
}
function verifyPreferences(value: any) {
  const flags = ['verified', 'settingsEnabled', 'feedbackEnabled', 'canChangeName', 'canChangeVolume', 'canChangeFollowUp', 'canSubmitFeedback', 'settingsReviewRequired', 'feedbackReviewRequired', 'manualReviewRequired']
  const dataKeys = ['name', 'storedVolume', 'outputVolume', 'outputMuted', 'outputVolumeAvailable', 'followUpSeconds', 'followUpSource', 'minimumSeconds', 'maximumSeconds']
  exact(value, [...flags, ...dataKeys, 'reason', 'sampledAt'])
  const result: any = {}
  flags.forEach(key => { result[key] = bool(value[key]) })
  if (typeof value.reason !== 'string' || value.reason.length > 320 || codePoints(value.reason) > 160 || unsafe.test(value.reason) || value.reason && !value.reason.trim()) return invalid()
  result.reason = value.reason; result.sampledAt = utc(value.sampledAt)
  if (result.manualReviewRequired !== (result.settingsReviewRequired || result.feedbackReviewRequired)) return invalid()
  if ((!result.verified || !result.settingsEnabled || result.settingsReviewRequired) && (result.canChangeName || result.canChangeVolume || result.canChangeFollowUp)) return invalid()
  if ((!result.verified || !result.feedbackEnabled || result.feedbackReviewRequired) && result.canSubmitFeedback) return invalid()
  if (!result.canChangeName && !result.canChangeVolume && !result.canChangeFollowUp && !result.canSubmitFeedback && !result.reason.trim()) return invalid()
  if (!result.verified) {
    if (dataKeys.some(key => value[key] !== null)) return invalid()
    dataKeys.forEach(key => { result[key] = null }); return result
  }
  result.name = text(value.name, 1, 20)
  result.storedVolume = integer(value.storedVolume, 0, 100)
  result.outputVolumeAvailable = value.outputVolumeAvailable === null ? null : bool(value.outputVolumeAvailable)
  result.outputVolume = value.outputVolume === null ? null : integer(value.outputVolume, 0, 150)
  result.outputMuted = value.outputMuted === null ? null : bool(value.outputMuted)
  if (result.outputVolumeAvailable !== true && (result.outputVolume !== null || result.outputMuted !== null || result.canChangeVolume)) return invalid()
  result.followUpSeconds = integer(value.followUpSeconds, 5, 120)
  if (!['default', 'persisted'].includes(value.followUpSource) || value.minimumSeconds !== 5 || value.maximumSeconds !== 120) return invalid()
  result.followUpSource = value.followUpSource; result.minimumSeconds = 5; result.maximumSeconds = 120
  return result
}
function verifyPreferencesCommand(value: any) {
  if (!object(value) || !requestIdValid(value.requestId) || !KINDS.includes(value.kind)) return invalid()
  exact(value, value.kind === 'settings' ? ['requestId', 'kind', 'changes'] : ['requestId', 'kind', 'category', 'content'])
  if (value.kind === 'feedback') {
    if (!CATEGORIES.includes(value.category)) return invalid()
    return { requestId: value.requestId, kind: value.kind, category: value.category, content: text(value.content, 2, 500, true) }
  }
  if (!object(value.changes) || !Object.keys(value.changes).length || Object.keys(value.changes).some(key => !FIELDS.includes(key))) return invalid()
  const changes: any = {}
  if (Object.prototype.hasOwnProperty.call(value.changes, 'name')) changes.name = text(value.changes.name, 1, 20)
  if (Object.prototype.hasOwnProperty.call(value.changes, 'volume')) changes.volume = integer(value.changes.volume, 0, 100)
  if (Object.prototype.hasOwnProperty.call(value.changes, 'followUpSeconds')) changes.followUpSeconds = integer(value.changes.followUpSeconds, 5, 120)
  return { requestId: value.requestId, kind: value.kind, changes }
}
function verifyPreferencesReceipt(value: any) {
  exact(value, ['requestId', 'kind', 'status', 'steps', 'updatedAt'])
  if (!requestIdValid(value.requestId) || !KINDS.includes(value.kind) || !['sending', 'recorded', 'partially_applied', 'not_sent', 'unknown'].includes(value.status) || !Array.isArray(value.steps) || value.steps.length < 1 || value.steps.length > 2) return invalid()
  const names = value.steps.map((item: any) => item && item.step)
  if (value.kind === 'feedback' ? names.length !== 1 || names[0] !== 'feedback' : ![['pet_settings'], ['follow_up'], ['pet_settings', 'follow_up']].some(order => JSON.stringify(order) === JSON.stringify(names))) return invalid()
  const steps = value.steps.map((item: any) => {
    exact(item, ['step', 'status'])
    if (!['pending', 'sending', 'applied', 'not_sent', 'unknown'].includes(item.status)) return invalid()
    return { step: item.step, status: item.status }
  })
  const statuses = steps.map((item: any) => item.status), has = (status: string) => statuses.includes(status)
  if (value.status === 'unknown' ? !has('unknown') || has('pending') || has('sending') : has('unknown')) return invalid()
  if (value.status === 'sending' && !has('pending') && !has('sending')) return invalid()
  if (value.status === 'not_sent' && statuses.some((status: string) => status !== 'not_sent')) return invalid()
  if (['recorded', 'partially_applied'].includes(value.status) && (!has('applied') || statuses.some((status: string) => !['applied', 'not_sent'].includes(status)))) return invalid()
  if (value.status === 'partially_applied' && (!has('not_sent') || value.kind !== 'settings')) return invalid()
  return { requestId: value.requestId, kind: value.kind, status: value.status, steps, updatedAt: utc(value.updatedAt) }
}
function preferencesReceiptMatchesCommand(receipt: any, command: any) {
  const names = command.kind === 'feedback' ? ['feedback'] : [...('name' in command.changes || 'volume' in command.changes ? ['pet_settings'] : []), ...('followUpSeconds' in command.changes ? ['follow_up'] : [])]
  return receipt.requestId === command.requestId && receipt.kind === command.kind && JSON.stringify(receipt.steps.map((item: any) => item.step)) === JSON.stringify(names)
}
function marker(value: any, kind: string) {
  exact(value, ['userId', 'petId', 'requestId', 'kind'])
  if (!id(value.userId) || !id(value.petId) || !requestIdValid(value.requestId) || value.kind !== kind) return invalid()
  return { userId: value.userId, petId: value.petId, requestId: value.requestId, kind }
}
function markerStore(value: any) {
  if (value === undefined || value === '') return { version: 1, settings: null, feedback: null }
  exact(value, ['version', 'settings', 'feedback'])
  if (value.version !== 1) return invalid()
  const result = { version: 1, settings: value.settings === null ? null : marker(value.settings, 'settings'), feedback: value.feedback === null ? null : marker(value.feedback, 'feedback') }
  if (result.settings && result.feedback && result.settings.requestId === result.feedback.requestId) return invalid()
  return result
}
let confirmationSequence = 0

function createLivePreferences(session: any, makeRequestId: any, wxApi: any) {
  let visible = false, listener: any = null, detach: any = null, timer: any = null, key = '', scope = '', ready = false, epoch = 0, readSequence = 0, dispatchSequence = 0
  let readFlight: any = null, prepared: any = null, stored: any = { version: 1, settings: null, feedback: null }, storageGood = true, pollingPaused = false
  const flights: any = { settings: null, feedback: null }, checks: any = { settings: null, feedback: null }, pendingDrafts: any = { settings: null, feedback: null }
  const empty = () => ({ snapshot: null, loading: false, error: '', storageError: '', confirmPending: false,
    settingsDraft: { name: null, volume: null, followUpSeconds: null }, settingsVersion: 0, settingsDraftError: '', settingsDirty: false, settingsDirtyFields: [], settingsNeedsRefresh: false,
    feedbackDraft: { category: 'device', content: '' }, feedbackVersion: 0, feedbackDraftError: '',
    settingsSubmitting: false, feedbackSubmitting: false, settingsChecking: false, feedbackChecking: false,
    settingsReceipt: null, feedbackReceipt: null, settingsError: '', feedbackError: '', settingsBlocked: false, feedbackBlocked: false, canCheckSettings: false, canCheckFeedback: false })
  let state: any = empty()
  function identity() {
    const value = session.getState(), metadata = typeof session.getIdentity === 'function' ? session.getIdentity() : null
    if (value.mode !== 'live' || !value.signedIn || !metadata || !id(metadata.userId) || typeof metadata.expiresAt !== 'string' || metadata.expiresAt !== value.expiresAt || !id(value.selectedPetId) || !value.pets.some((pet: any) => pet.petId === value.selectedPetId)) return null
    return { userId: metadata.userId, petId: value.selectedPetId, expiresAt: metadata.expiresAt }
  }
  function selectionKey() { const value = identity(); return value ? value.userId + '|' + value.petId + '|' + value.expiresAt : '' }
  function selectionScope() { const value = identity(); return value ? value.userId + '|' + value.petId : '' }
  function usable() { const value = session.getState(); return Boolean(identity() && !value.loading && !value.listLoading) }
  function current(expected: number) { return visible && ready && epoch === expected && key && selectionKey() === key && usable() }
  function ownMarker(kind: string) { const value = identity(), item = stored[kind]; return value && item && item.userId === value.userId && item.petId === value.petId ? item : null }
  function getState() {
    const reason = (kind: string) => !storageGood ? state.storageError : stored[kind] && !ownMarker(kind) ? '另一个身份或设备有未确认请求，请回原上下文核对；不能清数据绕过' : state[kind + 'Error']
    return JSON.parse(JSON.stringify({ ...state, confirmPending: Boolean(prepared), canCheckSettings: Boolean(ownMarker('settings')), canCheckFeedback: Boolean(ownMarker('feedback')),
      settingsError: reason('settings'), feedbackError: reason('feedback'), settingsBlocked: !storageGood || Boolean(stored.settings), feedbackBlocked: !storageGood || Boolean(stored.feedback) }))
  }
  function emit() { if (visible && listener) listener(getState()) }
  function patch(value: any) { state = { ...state, ...value }; emit() }
  function storageFailure() { storageGood = false; state.storageError = '未决请求存储不可用，为防重复操作已禁止发送；请保留现有数据并检查存储'; emit() }
  function loadStore() {
    try { stored = markerStore(wxApi.getStorageSync(STORAGE_KEY)); storageGood = true; state.storageError = ''; return true } catch (_) { storageFailure(); return false }
  }
  function persist(next: any) {
    try {
      const verified = markerStore(next); wxApi.setStorageSync(STORAGE_KEY, verified)
      const actual = markerStore(wxApi.getStorageSync(STORAGE_KEY))
      if (JSON.stringify(actual) !== JSON.stringify(verified)) throw new Error('UNVERIFIED')
      stored = actual; storageGood = true; state.storageError = ''; emit(); return true
    } catch (_) { storageFailure(); return false }
  }
  function finishMarker(kind: string, expected: any) {
    if (!loadStore()) return false
    if (!stored[kind]) return true
    if (JSON.stringify(stored[kind]) !== JSON.stringify(expected)) return false
    return persist({ ...stored, [kind]: null })
  }
  function cancelTimer() { clearTimeout(timer); timer = null }
  function schedule() {
    cancelTimer()
    if (!visible || !ready || !key || prepared || readFlight || flights.settings || flights.feedback || pollingPaused) return
    const expected = epoch
    timer = setTimeout(() => { timer = null; if (current(expected)) refresh(true) }, 10000)
  }
  function applySnapshot(snapshot: any) {
    const draft = { ...state.settingsDraft }
    FIELDS.forEach(field => { if (!state.settingsDirtyFields.includes(field)) draft[field] = snapshot.verified ? snapshot[field === 'volume' ? 'storedVolume' : field] : null })
    patch({ snapshot, settingsDraft: draft, settingsNeedsRefresh: false, error: '' })
  }
  function refresh(automatic = false): Promise<any> {
    if (!visible || !ready || !key || key !== selectionKey() || !usable()) return Promise.resolve()
    if (readFlight) return readFlight.promise
    if (automatic !== true) { pollingPaused = false; loadStore() }
    cancelTimer()
    const flight: any = { expected: epoch, sequence: ++readSequence }; dispatchSequence++; readFlight = flight; patch({ loading: true })
    flight.promise = session.readPreferences().then((value: any) => {
      const snapshot = verifyPreferences(value)
      if (current(flight.expected) && flight.sequence === readSequence) applySnapshot(snapshot)
    }).catch((error: any) => {
      if (current(flight.expected) && flight.sequence === readSequence && (!error || error.code !== 'CANCELLED')) {
        pollingPaused = true; prepared = null
        patch({ snapshot: null, error: '真实设置暂不可用，自动读取已暂停，请手动刷新；输入内容保留且不会自动提交' })
      }
    }).finally(() => {
      if (!current(flight.expected) || readFlight !== flight) return
      readFlight = null; patch({ loading: false }); schedule()
    })
    return flight.promise
  }
  function failure(kind: string, message: string) { patch({ [kind + 'Error']: message }); return null }
  function equalsSnapshot(field: string, value: any, snapshot: any) {
    if (!snapshot || !snapshot.verified) return false
    if (field === 'volume') return snapshot.storedVolume === value && snapshot.outputVolume === value && snapshot.outputMuted === (value === 0)
    return snapshot[field] === value
  }
  function changesFor(snapshot: any, draft = state.settingsDraft, fields = state.settingsDirtyFields) {
    const changes: any = {}
    fields.forEach((field: string) => { if (!equalsSnapshot(field, draft[field], snapshot)) changes[field] = draft[field] })
    return changes
  }
  function permitted(snapshot: any, kind: string, changes?: any) {
    if (!snapshot || !snapshot.verified) return false
    if (kind === 'feedback') return snapshot.feedbackEnabled && !snapshot.feedbackReviewRequired && snapshot.canSubmitFeedback
    return snapshot.settingsEnabled && !snapshot.settingsReviewRequired && Object.keys(changes || {}).length &&
      Object.keys(changes).every(field => snapshot[field === 'name' ? 'canChangeName' : field === 'volume' ? 'canChangeVolume' : 'canChangeFollowUp'])
  }
  function setDraft(kind: string, payload: any) {
    if (!visible || !KINDS.includes(kind)) return
    const versionKey = kind + 'Version'
    const update = (value: any) => {
      // Native input/change can repeat the current value. Preserve a prepared
      // confirmation only when both the bounded draft and validation agree.
      // Dirty fields are included: explicitly choosing saved volume can still
      // intentionally correct a different actual output volume.
      if (Object.keys(value).every(field => JSON.stringify(value[field]) === JSON.stringify(state[field]))) return
      const wasPrepared = Boolean(prepared)
      prepared = null
      patch({ ...value, [versionKey]: state[versionKey] + 1 })
      if (wasPrepared) schedule()
    }
    try {
      if (kind === 'settings') {
        if (!object(payload) || !Object.keys(payload).length || Object.keys(payload).some(field => !FIELDS.includes(field))) return invalid()
        const draft = { ...state.settingsDraft, ...payload }
        if (draft.name !== null && (typeof draft.name !== 'string' || draft.name.length > 40) || draft.volume !== null && typeof draft.volume !== 'number' || draft.followUpSeconds !== null && typeof draft.followUpSeconds !== 'number') return invalid()
        const dirty = [...state.settingsDirtyFields]
        // A patch identifies fields explicitly edited by the user. In particular,
        // dragging to the saved volume may still change a different actual output.
        Object.keys(payload).forEach(field => {
          const index = dirty.indexOf(field)
          if (equalsSnapshot(field, draft[field], state.snapshot)) { if (index >= 0) dirty.splice(index, 1) }
          else if (index < 0) dirty.push(field)
        })
        let error = ''
        if (dirty.length) { try { verifyPreferencesCommand({ requestId: 'draft', kind, changes: changesFor(null, draft, dirty) }) } catch (_) { error = '名字需1至20个字符，音量需0至100整数，连续聊天时间需5至120整数' } }
        update({ settingsDraft: draft, settingsDirtyFields: dirty, settingsDirty: Boolean(dirty.length), settingsDraftError: error })
      } else {
        exact(payload, ['category', 'content'])
        if (typeof payload.content !== 'string' || payload.content.length > 1000 || typeof payload.category !== 'string' || payload.category.length > 16) return invalid()
        let error = ''
        if (payload.content) { try { verifyPreferencesCommand({ requestId: 'draft', kind, category: payload.category, content: payload.content }) } catch (_) { error = '反馈需2至500个字符，仅支持正常文字、换行和制表符' } }
        else if (!CATEGORIES.includes(payload.category)) error = '请选择有效的问题类型'
        update({ feedbackDraft: { category: payload.category, content: payload.content }, feedbackDraftError: error })
      }
    } catch (_) { update({ [kind + 'DraftError']: '输入内容无效或过长，请调整后重新确认' }) }
  }
  function prepare(kind: string) {
    if (!KINDS.includes(kind)) return null
    if (!visible || !ready || !key || selectionKey() !== key || !usable()) return failure(kind, '请求未发送：登录或设备授权正在更新，请重新核对后确认')
    if (readFlight) return failure(kind, '请求未发送：正在核对设置权限，请完成后再确认')
    if (!loadStore()) return null
    if (stored[kind] || flights[kind] || checks[kind]) return failure(kind, '请求未发送：仍有未决请求，请只读核对原编号，不能重新发送')
    if (kind === 'settings' && state.settingsNeedsRefresh) return failure(kind, '已有部分设置保存，请先刷新实际读数，再重新确认仍需修改的部分')
    let command: any
    try { command = verifyPreferencesCommand(kind === 'settings' ? { requestId: 'draft', kind, changes: changesFor(state.snapshot) } : { requestId: 'draft', kind, ...state.feedbackDraft }) }
    catch (_) { return failure(kind, kind === 'settings' ? '请求未发送：没有有效的待保存差异，或设置输入不符合要求' : '请求未发送：请选择问题类型并填写2至500个合法字符') }
    if (state[kind + 'DraftError']) return failure(kind, '请求未发送：请先修正当前输入内容')
    if (!permitted(state.snapshot, kind, command.changes)) return failure(kind, '请求未发送：当前权限或设备状态不允许该操作，请刷新后重新确认')
    cancelTimer()
    prepared = { token: 'preferences-confirm-' + (++confirmationSequence) + '-' + Date.now(), expected: epoch, key, kind, version: state[kind + 'Version'],
      command, expiresAt: Date.now() + 60000 }
    patch({ [kind + 'Error']: '' }); return prepared.token
  }
  function cancel(token: any) { if (prepared && prepared.token === token) { prepared = null; emit(); schedule() } }
  function validConfirmation(command: any) { return current(command.expected) && key === command.key && Date.now() < command.expiresAt && state[command.kind + 'Version'] === command.version && !state[command.kind + 'DraftError'] }
  function acceptReceipt(kind: string, command: any, receipt: any) {
    receipt = verifyPreferencesReceipt(receipt)
    if (receipt.requestId !== command.requestId || receipt.kind !== kind) throw new Error('UNVERIFIED')
    // A GET begun before a write receipt cannot confirm post-write values, or
    // unlock a partially-applied form using a pre-write snapshot. A receipt
    // expires UI reads, not another kind's already explicit confirmation.
    readSequence++
    if (['recorded', 'partially_applied', 'not_sent'].includes(receipt.status)) finishMarker(kind, command)
    const draft = pendingDrafts[kind]
    if (receipt.status === 'recorded' && draft && draft.requestId === receipt.requestId && draft.version === state[kind + 'Version']) {
      if (kind === 'settings') { state.settingsDirtyFields = []; state.settingsDirty = false; state.settingsDraftError = '' }
      else { state.feedbackDraft = { category: state.feedbackDraft.category, content: '' }; state.feedbackDraftError = ''; state.feedbackVersion++ }
      pendingDrafts[kind] = null
    }
    patch({ [kind + 'Receipt']: receipt, ...(receipt.status === 'partially_applied' ? { settingsNeedsRefresh: true } : {}),
      [kind + 'Error']: receipt.status === 'unknown' ? '请求结果未知，保留原编号；只能只读核对，不会重复发送' : receipt.status === 'sending' ? '服务端仍在处理，请稍后只读核对原编号' : receipt.status === 'not_sent' ? '服务端确认本次未发送；不会自动重发，请刷新后重新确认' : receipt.status === 'partially_applied' ? '仅部分设置已保存；请先刷新实际读数，再确认仍需修改的部分，不会自动补发' : '' })
  }
  function confirm(token: any): Promise<any> {
    for (const kind of KINDS) if (flights[kind] && flights[kind].token === token) return flights[kind].promise
    const command = prepared
    if (!command || command.token !== token) { if (!prepared) schedule(); return Promise.resolve() }
    prepared = null
    const kind = command.kind
    if (!validConfirmation(command)) { failure(kind, '请求未发送：页面、设备、登录或输入已变化，请重新确认'); schedule(); return Promise.resolve() }
    const flight: any = { expected: epoch, token, sequence: ++readSequence, dispatch: ++dispatchSequence, persisted: false }; flights[kind] = flight
    cancelTimer(); patch({ [kind + 'Submitting']: true, [kind + 'Receipt']: null, [kind + 'Error']: '' })
    flight.promise = (async () => {
      const snapshot = verifyPreferences(await session.readPreferences())
      if (!current(flight.expected)) return
      if (flight.dispatch !== dispatchSequence) { failure(kind, '请求未发送：确认期间设置读数已更新或失效，请刷新后重新确认'); return }
      if (!validConfirmation(command)) { failure(kind, '请求未发送：确认期间设备、登录或输入已变化'); return }
      if (flight.sequence === readSequence) applySnapshot(snapshot)
      const body = kind === 'settings' ? { ...command.command, changes: changesFor(snapshot, command.command.changes, Object.keys(command.command.changes)) } : command.command
      if (kind === 'settings' && !Object.keys(body.changes).length) { failure(kind, '请求未发送：最新实际读数已无待保存差异，请刷新核对'); return }
      if (!permitted(snapshot, kind, body.changes)) { failure(kind, '请求未发送：最新权限或设备状态不允许该操作，请刷新后重新确认'); return }
      if (!loadStore() || stored[kind]) { failure(kind, '请求未发送：仍有未决请求或存储不可用，请核对原编号'); return }
      const requestId = makeRequestId('preferences')
      if (!requestIdValid(requestId) || KINDS.some(lane => stored[lane] && stored[lane].requestId === requestId)) { failure(kind, '请求未发送：请求编号无效或冲突'); return }
      const owner = identity()
      if (!owner || !validConfirmation(command)) return
      const pending = { userId: owner.userId, petId: owner.petId, requestId, kind }
      if (!persist({ ...stored, [kind]: pending })) return
      flight.persisted = true
      if (!validConfirmation(command)) { failure(kind, '请求未发送：持久化后身份发生变化，请保留编号并核对'); return }
      pendingDrafts[kind] = { requestId, version: command.version }
      const value = verifyPreferencesCommand({ ...body, requestId })
      const receipt = verifyPreferencesReceipt(await session.sendPreferencesCommand(value))
      if (current(flight.expected)) {
        if (!preferencesReceiptMatchesCommand(receipt, value)) throw new Error('UNVERIFIED')
        acceptReceipt(kind, pending, receipt)
      }
    })().catch((error: any) => {
      if (!current(flight.expected)) return
      if (!error || error.code !== 'CANCELLED') {
        // Late preflight errors may report this unsent lane, never discard a
        // newer read/receipt or change the newer context's polling policy.
        if (!flight.persisted && (flight.sequence !== readSequence || flight.dispatch !== dispatchSequence)) {
          failure(kind, '请求未发送：本次设置核验未完成；保留当前较新的读数和回执，请重新确认'); return
        }
        pollingPaused = true
        if (!flight.persisted) { prepared = null; patch({ snapshot: null }); failure(kind, '请求未发送：最新设置核验失败，请刷新后重新确认') }
        else failure(kind, '请求结果尚未确认，请保留原编号并只读核对；不会自动重发或生成新请求')
      }
    }).finally(() => {
      if (!current(flight.expected) || flights[kind] !== flight) return
      flights[kind] = null; patch({ [kind + 'Submitting']: false }); schedule()
    })
    return flight.promise
  }
  function checkReceipt(kind: string): Promise<any> {
    if (!KINDS.includes(kind) || !visible || !ready || !key || key !== selectionKey() || !usable()) return Promise.resolve()
    if (checks[kind]) return checks[kind].promise
    if (flights[kind]) { failure(kind, '请求仍在处理中，请等待后只读核对'); return Promise.resolve() }
    if (!loadStore()) return Promise.resolve()
    const command = ownMarker(kind)
    if (!command) { failure(kind, '当前身份和设备没有可核对的未决编号；不会提交新请求'); return Promise.resolve() }
    const flight: any = { expected: epoch }; checks[kind] = flight; patch({ [kind + 'Checking']: true })
    flight.promise = session.readPreferencesReceipt(command.requestId).then((receipt: any) => {
      if (current(flight.expected)) acceptReceipt(kind, command, receipt)
    }).catch((error: any) => {
      if (current(flight.expected) && (!error || error.code !== 'CANCELLED')) failure(kind, '回执未能确认（包含查无记录），不证明请求未提交；保留原编号，请稍后只读核对')
    }).finally(() => {
      if (!current(flight.expected) || checks[kind] !== flight) return
      checks[kind] = null; patch({ [kind + 'Checking']: false })
    })
    return flight.promise
  }
  function changed() {
    const next = selectionKey(), nextScope = selectionScope(), nextReady = usable(), switched = next !== key, resumed = !ready && nextReady
    if (switched || ready && !nextReady) {
      epoch++; readSequence++; dispatchSequence++; cancelTimer(); prepared = null; readFlight = null; pollingPaused = false
      KINDS.forEach(kind => { flights[kind] = null; checks[kind] = null })
      const preserve = scope && scope === nextScope && !switched, drafts: any = {}
      if (preserve) ['settingsDraft', 'settingsVersion', 'settingsDraftError', 'settingsDirty', 'settingsDirtyFields', 'settingsNeedsRefresh', 'feedbackDraft', 'feedbackVersion', 'feedbackDraftError'].forEach(field => { drafts[field] = state[field] })
      else KINDS.forEach(kind => { pendingDrafts[kind] = null })
      state = { ...empty(), ...drafts }; key = next; scope = nextScope; loadStore(); emit()
    }
    ready = nextReady
    if (key && ready && (switched || resumed)) refresh()
  }
  function hide() {
    visible = false; listener = null; ready = false; epoch++; readSequence++; dispatchSequence++; prepared = null; cancelTimer(); readFlight = null
    KINDS.forEach(kind => { flights[kind] = null; checks[kind] = null })
    if (detach) detach(); detach = null
    const drafts: any = {}
    ;['settingsDraft', 'settingsVersion', 'settingsDraftError', 'settingsDirty', 'settingsDirtyFields', 'settingsNeedsRefresh', 'feedbackDraft', 'feedbackVersion', 'feedbackDraftError'].forEach(field => { drafts[field] = state[field] })
    state = { ...empty(), ...drafts }
  }
  function show(callback: any) { hide(); visible = true; listener = callback; loadStore(); detach = session.attach(changed); changed(); if (key) refresh(); emit() }
  return { show, hide, getState, refresh, setDraft, prepare, confirm, cancel, checkReceipt }
}

module.exports = { verifyPreferences, verifyPreferencesCommand, verifyPreferencesReceipt, preferencesReceiptMatchesCommand, preferencesRequestIdValid: requestIdValid, createLivePreferences }
