// Explicit control only. An unresolved identity/request marker is persisted
// before POST; text and credentials are never persisted or automatically sent.
const STORAGE_KEY = 'aiCatDialogPendingV1'
const STATES = ['starting', 'connecting', 'connected', 'ready', 'waiting_wake', 'wake_detected', 'listening', 'thinking', 'answering', 'followup_listening', 'waiting_follow_up', 'interrupted', 'echo_guard', 'recovering', 'waking', 'interrupting', 'submitting_text', 'processing_audio', 'followup_preparing', 'proactive_queued', 'proactive_speaking', 'proactive_finishing', 'stopping', 'offline', 'unavailable']
const LISTEN_STATES = ['ready', 'waiting_wake', 'interrupted', 'offline', 'echo_guard']
const TEXT_STATES = ['ready', 'followup_listening', 'interrupted', 'offline']
const KINDS = ['listen', 'interrupt', 'text']
const unsafe = /[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/
const object = (value: any) => value && typeof value === 'object' && !Array.isArray(value)
const id = (value: any) => typeof value === 'string' && value.length >= 1 && value.length <= 128 && !/[^A-Za-z0-9_-]/.test(value)
const requestIdValid = (value: any) => typeof value === 'string' && value.length >= 1 && value.length <= 64 && !/[^A-Za-z0-9_.:-]/.test(value)
function invalid(): never { throw new Error('UNVERIFIED') }
function exact(value: any, keys: string[]) {
  if (!object(value) || Object.keys(value).length !== keys.length || keys.some(key => !Object.prototype.hasOwnProperty.call(value, key))) return invalid()
}
function bool(value: any): boolean { if (typeof value !== 'boolean') return invalid(); return value }
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
function dialogTextValid(value: any): boolean {
  if (typeof value !== 'string' || !value.trim() || value.length > 1000 || unsafe.test(value)) return false
  try { return codePoints(value) <= 500 } catch (_) { return false }
}
function verifyDialogControl(value: any) {
  exact(value, ['enabled', 'verified', 'canListen', 'canInterrupt', 'canSendText', 'manualReviewRequired', 'reason', 'state', 'stale', 'sampledAt'])
  const result = { enabled: bool(value.enabled), verified: bool(value.verified), canListen: bool(value.canListen), canInterrupt: bool(value.canInterrupt), canSendText: bool(value.canSendText),
    manualReviewRequired: bool(value.manualReviewRequired), reason: value.reason, state: value.state, stale: bool(value.stale), sampledAt: utc(value.sampledAt) }
  if (typeof result.reason !== 'string' || codePoints(result.reason) > 160 || unsafe.test(result.reason) || !STATES.includes(result.state)) return invalid()
  if ((!result.enabled || !result.verified) && (result.canListen || result.canInterrupt || result.canSendText)) return invalid()
  if ((result.stale || result.manualReviewRequired) && (result.canListen || result.canSendText)) return invalid()
  if (result.canListen && !LISTEN_STATES.includes(result.state) || result.canSendText && !TEXT_STATES.includes(result.state)) return invalid()
  if (!result.canListen && !result.canInterrupt && !result.canSendText && !result.reason.trim()) return invalid()
  return result
}
function verifyDialogCommand(value: any) {
  if (!object(value) || !KINDS.includes(value.kind) || !requestIdValid(value.requestId)) return invalid()
  exact(value, value.kind === 'text' ? ['requestId', 'kind', 'content'] : ['requestId', 'kind'])
  if (value.kind === 'text' && !dialogTextValid(value.content)) return invalid()
  return { requestId: value.requestId, kind: value.kind, ...(value.kind === 'text' ? { content: value.content } : {}) }
}
function verifyDialogReceipt(value: any) {
  exact(value, ['requestId', 'kind', 'status', 'updatedAt'])
  if (!requestIdValid(value.requestId) || !KINDS.includes(value.kind) || !['sending', 'accepted', 'not_sent', 'unknown'].includes(value.status)) return invalid()
  return { requestId: value.requestId, kind: value.kind, status: value.status, updatedAt: utc(value.updatedAt) }
}
function marker(value: any, channel: string) {
  exact(value, ['userId', 'petId', 'requestId', 'kind'])
  if (!id(value.userId) || !id(value.petId) || !requestIdValid(value.requestId) || !KINDS.includes(value.kind) || (value.kind === 'interrupt') !== (channel === 'interrupt')) return invalid()
  return { userId: value.userId, petId: value.petId, requestId: value.requestId, kind: value.kind }
}
function markerStore(value: any) {
  if (value === undefined || value === '') return { version: 1, main: null, interrupt: null }
  exact(value, ['version', 'main', 'interrupt'])
  if (value.version !== 1) return invalid()
  const result = { version: 1, main: value.main === null ? null : marker(value.main, 'main'), interrupt: value.interrupt === null ? null : marker(value.interrupt, 'interrupt') }
  if (result.main && result.interrupt && result.main.requestId === result.interrupt.requestId) return invalid()
  return result
}
let confirmationSequence = 0

function createLiveDialogControl(session: any, makeRequestId: any, wxApi: any) {
  let visible = false, listener: any = null, detach: any = null, timer: any = null, key = '', scope = '', ready = false, epoch = 0, readSequence = 0, dispatchSequence = 0
  let readFlight: any = null, mainFlight: any = null, interruptFlight: any = null, mainCheck: any = null, interruptCheck: any = null
  let prepared: any = null, pendingDraft: any = null, stored: any = { version: 1, main: null, interrupt: null }
  let storageGood = true, pollingPaused = false
  const empty = () => ({ snapshot: null, loading: false, submitting: false, interrupting: false, checkingMain: false, checkingInterrupt: false,
    mainReceipt: null, interruptReceipt: null, error: '', mainError: '', interruptError: '', draftText: '', draftVersion: 0, draftError: '', confirmPending: false,
    canCheckMain: false, canCheckInterrupt: false, storageError: '', mainBlocked: false, interruptBlocked: false })
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
  function ownMarker(channel: string) { const value = identity(), item = stored[channel]; return value && item && item.userId === value.userId && item.petId === value.petId ? item : null }
  function getState() {
    const blockedReason = (channel: string) => !storageGood ? state.storageError : stored[channel] && !ownMarker(channel) ?
      '另一个身份或设备有未确认请求，请回原上下文核对；不能清数据绕过' : state[channel === 'main' ? 'mainError' : 'interruptError']
    return JSON.parse(JSON.stringify({ ...state, confirmPending: Boolean(prepared), canCheckMain: Boolean(ownMarker('main')), canCheckInterrupt: Boolean(ownMarker('interrupt')),
      mainError: blockedReason('main'), interruptError: blockedReason('interrupt'),
      mainBlocked: !storageGood || Boolean(stored.main), interruptBlocked: !storageGood || Boolean(stored.interrupt) }))
  }
  function emit() { if (visible && listener) listener(getState()) }
  function patch(value: any) { state = { ...state, ...value }; emit() }
  function storageFailure() { storageGood = false; state.storageError = '未决请求存储不可用，为防重复操作已禁止发送；请保留现有数据并检查存储'; emit() }
  function loadStore() {
    try { stored = markerStore(wxApi.getStorageSync(STORAGE_KEY)); storageGood = true; state.storageError = ''; return true }
    catch (_) { storageFailure(); return false }
  }
  function persist(next: any) {
    try {
      const verified = markerStore(next)
      wxApi.setStorageSync(STORAGE_KEY, verified)
      const actual = markerStore(wxApi.getStorageSync(STORAGE_KEY))
      if (JSON.stringify(actual) !== JSON.stringify(verified)) throw new Error('UNVERIFIED')
      stored = actual; storageGood = true; state.storageError = ''; emit(); return true
    } catch (_) { storageFailure(); return false }
  }
  function finishMarker(channel: string, expected: any) {
    if (!loadStore()) return false
    const item = stored[channel]
    if (!item) return true
    if (JSON.stringify(item) !== JSON.stringify(expected)) return false
    return persist({ ...stored, [channel]: null })
  }
  function cancelTimer() { clearTimeout(timer); timer = null }
  function schedule() {
    cancelTimer()
    if (!visible || !ready || !key || prepared || readFlight || mainFlight || interruptFlight || pollingPaused) return
    const expected = epoch
    timer = setTimeout(() => { timer = null; if (current(expected)) refresh(true) }, 10000)
  }
  function refresh(automatic = false): Promise<any> {
    if (!visible || !ready || !key || key !== selectionKey() || !usable()) return Promise.resolve()
    if (readFlight) return readFlight.promise
    if (automatic !== true) { pollingPaused = false; loadStore() }
    cancelTimer()
    const flight: any = { expected: epoch, sequence: ++readSequence }; dispatchSequence++
    readFlight = flight; patch({ loading: true })
    flight.promise = session.readDialogControl().then((value: any) => {
      const snapshot = verifyDialogControl(value)
      if (current(flight.expected) && flight.sequence === readSequence) patch({ snapshot, error: '' })
    }).catch((error: any) => {
      if (current(flight.expected) && flight.sequence === readSequence && (!error || error.code !== 'CANCELLED')) {
        pollingPaused = true; prepared = null
        patch({ snapshot: null, error: '对话控制状态暂不可用，自动读取已暂停，请手动刷新；不会自动发送请求' })
      }
    }).finally(() => {
      if (!current(flight.expected) || readFlight !== flight) return
      readFlight = null; patch({ loading: false }); schedule()
    })
    return flight.promise
  }
  function failure(channel: string, message: string) { patch({ [channel === 'interrupt' ? 'interruptError' : 'mainError']: message }); return null }
  function eligible(snapshot: any, kind: string) {
    return snapshot && snapshot.enabled && snapshot.verified && (kind === 'interrupt' ? snapshot.canInterrupt : kind === 'listen' ? snapshot.canListen : snapshot.canSendText)
  }
  function setDraft(content: any) {
    if (!visible || typeof content !== 'string') return
    const error = content.length > 1000 ? '文字问题最多500个字符，请缩短后重新输入'
      : !content || dialogTextValid(content) ? '' : '文字问题需1至500个字符，不支持换行、控制字符或无效字符'
    // Native input/blur can repeat the current value. That is not a new edit
    // and must not revoke a text or empty-draft listen confirmation.
    if (content === state.draftText && error === state.draftError) return
    const wasPrepared = Boolean(prepared)
    prepared = null
    // The native textarea bounds input. Refuse over-bound programmatic values
    // without retaining unbounded text or quietly slicing a surrogate pair.
    if (content.length > 1000) {
      patch({ draftVersion: state.draftVersion + 1, draftError: '文字问题最多500个字符，请缩短后重新输入' })
      if (wasPrepared) schedule()
      return
    }
    patch({ draftText: content, draftVersion: state.draftVersion + 1, draftError: error })
    if (wasPrepared) schedule()
  }
  function prepare(kind: string) {
    if (!visible || !ready || !key || selectionKey() !== key || !usable()) return failure(kind === 'interrupt' ? 'interrupt' : 'main', '请求未发送：登录或设备授权正在更新，请重新核对后确认')
    const channel = kind === 'interrupt' ? 'interrupt' : 'main'
    if (!KINDS.includes(kind)) return failure(channel, '请求未发送：不支持该操作')
    if (readFlight) return failure(channel, '请求未发送：正在核对控制权限，请完成后再确认')
    if (!loadStore()) return null
    if (stored[channel] || (channel === 'main' ? mainFlight || mainCheck : interruptFlight || interruptCheck)) return failure(channel, '请求未发送：仍有未决请求，请只读核对原编号，不能重新发送')
    if (!eligible(state.snapshot, kind)) return failure(channel, '请求未发送：当前控制状态不允许该操作，请刷新后重新确认')
    if (kind === 'text' && (state.draftError || !dialogTextValid(state.draftText))) return failure(channel, '请求未发送：文字问题需1至500个合法字符，不支持换行或控制字符')
    cancelTimer()
    prepared = { token: 'dialog-confirm-' + (++confirmationSequence) + '-' + Date.now(), expected: epoch, key, kind, channel,
      content: kind === 'text' ? state.draftText : undefined, draftVersion: state.draftVersion, expiresAt: Date.now() + 60000 }
    patch({ [channel === 'interrupt' ? 'interruptError' : 'mainError']: '' })
    return prepared.token
  }
  function cancel(token: any) { if (prepared && prepared.token === token) { prepared = null; emit(); schedule() } }
  function validConfirmation(command: any) {
    return current(command.expected) && key === command.key && Date.now() < command.expiresAt &&
      (command.kind !== 'text' || state.draftVersion === command.draftVersion && state.draftText === command.content && dialogTextValid(state.draftText))
  }
  function acceptReceipt(channel: string, command: any, receipt: any) {
    receipt = verifyDialogReceipt(receipt)
    if (receipt.requestId !== command.requestId || receipt.kind !== command.kind) throw new Error('UNVERIFIED')
    // A control GET started before this acknowledgement cannot supersede the
    // receipt's newer context, especially a retained unknown/sending marker.
    // This expires UI reads only, not another lane's explicit confirmation.
    readSequence++
    const terminal = ['accepted', 'not_sent'].includes(receipt.status)
    if (terminal) finishMarker(channel, command)
    if (receipt.status === 'accepted' && receipt.kind === 'text' && pendingDraft && pendingDraft.requestId === receipt.requestId &&
        pendingDraft.version === state.draftVersion && pendingDraft.content === state.draftText) {
      state.draftText = ''; state.draftVersion++; state.draftError = ''; pendingDraft = null
    }
    patch({ [channel === 'interrupt' ? 'interruptReceipt' : 'mainReceipt']: receipt,
      [channel === 'interrupt' ? 'interruptError' : 'mainError']: receipt.status === 'unknown' ? '请求结果未知，保留原编号；只能只读核对，不会重复发送' : receipt.status === 'sending' ? '服务端仍在处理，请稍后只读核对原编号' : receipt.status === 'not_sent' ? '服务端确认本次未发送；如需再次操作，请刷新后重新确认' : '' })
  }
  function confirm(token: any): Promise<any> {
    for (const flight of [mainFlight, interruptFlight]) if (flight && flight.token === token) return flight.promise
    const command = prepared
    if (!command || token !== command.token) {
      failure('main', '请求未发送：确认已取消或失效，请重新选择并确认')
      if (!prepared) schedule()
      return Promise.resolve()
    }
    prepared = null
    const channel = command.channel
    if (!validConfirmation(command)) { failure(channel, '请求未发送：页面、设备、登录或草稿已变化，请重新确认'); schedule(); return Promise.resolve() }
    const flight: any = { expected: epoch, token, channel, sequence: ++readSequence, dispatch: ++dispatchSequence, persisted: false }
    if (channel === 'main') mainFlight = flight; else interruptFlight = flight
    cancelTimer(); patch({ [channel === 'interrupt' ? 'interrupting' : 'submitting']: true,
      [channel === 'interrupt' ? 'interruptReceipt' : 'mainReceipt']: null,
      [channel === 'interrupt' ? 'interruptError' : 'mainError']: '' })
    flight.promise = (async () => {
      // Never share a pre-existing/polled GET as the pre-dispatch check.
      const snapshot = verifyDialogControl(await session.readDialogControl())
      if (!current(flight.expected)) return
      if (flight.dispatch !== dispatchSequence) { failure(channel, '请求未发送：确认期间控制读数已更新或失效，请刷新后重新确认'); return }
      if (!validConfirmation(command)) { failure(channel, '请求未发送：确认期间设备、登录或草稿已变化'); return }
      if (flight.sequence === readSequence) patch({ snapshot, error: '' })
      if (!eligible(snapshot, command.kind)) { failure(channel, '请求未发送：最新控制状态不允许该操作，请刷新后重新确认'); return }
      if (!loadStore() || stored[channel]) { failure(channel, '请求未发送：仍有未决请求或存储不可用，请核对原编号'); return }
      const requestId = makeRequestId('dialog')
      if (!requestIdValid(requestId) || ['main', 'interrupt'].some(lane => stored[lane] && stored[lane].requestId === requestId)) { failure(channel, '请求未发送：请求编号无效或冲突'); return }
      const currentIdentity = identity()
      if (!currentIdentity || !validConfirmation(command)) return
      const pending = { userId: currentIdentity.userId, petId: currentIdentity.petId, requestId, kind: command.kind }
      if (!persist({ ...stored, [channel]: pending })) return
      flight.persisted = true
      if (!validConfirmation(command)) { failure(channel, '请求未发送：持久化后身份发生变化，请保留编号并核对'); return }
      if (command.kind === 'text') pendingDraft = { requestId, content: command.content, version: command.draftVersion }
      const receipt = await session.sendDialogCommand({ requestId, kind: command.kind, ...(command.kind === 'text' ? { content: command.content } : {}) })
      if (current(flight.expected)) acceptReceipt(channel, pending, receipt)
    })().catch((error: any) => {
      if (!current(flight.expected)) return
      if (!error || error.code !== 'CANCELLED') {
        // A superseded preflight failure is still honestly not sent, but must
        // not erase newer GET/receipt UI or pause its independent polling.
        if (!flight.persisted && (flight.sequence !== readSequence || flight.dispatch !== dispatchSequence)) {
          failure(channel, '请求未发送：本次控制核验未完成；保留当前较新的读数和回执，请重新确认'); return
        }
        pollingPaused = true
        if (!flight.persisted) { prepared = null; patch({ snapshot: null }); failure(channel, '请求未发送：最新控制状态核验失败，请刷新后重新确认') }
        else failure(channel, '请求结果尚未确认，请保留原编号并只读核对；不会自动重发或生成新请求')
      }
    }).finally(() => {
      const same = channel === 'main' ? mainFlight === flight : interruptFlight === flight
      if (!current(flight.expected) || !same) return
      if (channel === 'main') mainFlight = null; else interruptFlight = null
      patch({ [channel === 'interrupt' ? 'interrupting' : 'submitting']: false }); schedule()
    })
    return flight.promise
  }
  function checkReceipt(channel = 'main'): Promise<any> {
    if (!['main', 'interrupt'].includes(channel) || !visible || !ready || !key || selectionKey() !== key || !usable()) return Promise.resolve()
    const existing = channel === 'main' ? mainCheck : interruptCheck
    if (existing) return existing.promise
    if (channel === 'main' ? mainFlight : interruptFlight) { failure(channel, '请求仍在处理中，请等待后只读核对'); return Promise.resolve() }
    if (!loadStore()) return Promise.resolve()
    const command = ownMarker(channel)
    if (!command) { failure(channel, '当前身份和设备没有可核对的未决编号；不会提交新请求'); return Promise.resolve() }
    const flight: any = { expected: epoch }
    if (channel === 'main') mainCheck = flight; else interruptCheck = flight
    patch({ [channel === 'interrupt' ? 'checkingInterrupt' : 'checkingMain']: true })
    flight.promise = session.readDialogReceipt(command.requestId).then((receipt: any) => {
      if (current(flight.expected)) acceptReceipt(channel, command, receipt)
    }).catch((error: any) => {
      if (current(flight.expected) && (!error || error.code !== 'CANCELLED')) failure(channel, '回执未能确认（包含查无记录），不证明请求未提交；保留原编号，请稍后只读核对')
    }).finally(() => {
      const same = channel === 'main' ? mainCheck === flight : interruptCheck === flight
      if (!current(flight.expected) || !same) return
      if (channel === 'main') mainCheck = null; else interruptCheck = null
      patch({ [channel === 'interrupt' ? 'checkingInterrupt' : 'checkingMain']: false })
    })
    return flight.promise
  }
  function changed() {
    const next = selectionKey(), nextScope = selectionScope(), nextReady = usable(), switched = next !== key, resumed = !ready && nextReady
    if (switched || ready && !nextReady) {
      epoch++; readSequence++; dispatchSequence++; cancelTimer(); prepared = null; readFlight = null; mainFlight = null; interruptFlight = null; mainCheck = null; interruptCheck = null; pollingPaused = false
      const preserveDraft = scope && scope === nextScope && !switched
      const draft = preserveDraft ? { draftText: state.draftText, draftVersion: state.draftVersion, draftError: state.draftError } : {}
      state = { ...empty(), ...draft }; if (!preserveDraft) pendingDraft = null
      key = next; scope = nextScope; loadStore(); emit()
    }
    ready = nextReady
    if (key && ready && (switched || resumed)) refresh()
  }
  function hide() {
    visible = false; listener = null; ready = false; epoch++; readSequence++; dispatchSequence++; prepared = null; cancelTimer()
    readFlight = null; mainFlight = null; interruptFlight = null; mainCheck = null; interruptCheck = null
    if (detach) detach(); detach = null
    state = { ...empty(), draftText: state.draftText, draftVersion: state.draftVersion, draftError: state.draftError }
  }
  function show(callback: any) {
    hide(); visible = true; listener = callback; loadStore()
    detach = session.attach(changed); changed(); if (key) refresh(); emit()
  }
  return { show, hide, getState, refresh, setDraft, prepare, confirm, cancel, checkReceipt }
}

module.exports = { verifyDialogControl, verifyDialogCommand, verifyDialogReceipt, dialogTextValid, dialogControlRequestIdValid: requestIdValid, createLiveDialogControl }
