// No target Wi-Fi input here. Card keys, invitations and encrypted receipt QR
// strings never cross the Page bridge or storage. Only original public IDs do.
const { parseProvisioningQR } = require('./live-provisioning')
const { inspectLegacyProvisionPending } = require('./provision-ledger-guard')
const { createLiveProvisioning, lookupLocalArchive, verifyUnknownReceipt } = require('./live-provisioning')
const LOCAL_NETWORK_STORAGE = 'aiCatLocalNetworkRequestV1'
const DEVICE = /^dev_[0-9a-f]{32}$/, HEX32 = /^[0-9a-f]{32}$/, ID = /^[A-Za-z0-9_.:-]{1,64}$/
const TERMINAL = ['connected', 'failed', 'not_sent']
const PHASES = ['prepared', 'opening', 'open', 'accepted', 'connecting', ...TERMINAL, 'unknown']
const REVIEW_ACKS = ['window-ended', 'network-stable', 'device-idle', 'accept-unknown']
const faults = new WeakSet<any>()
let handoff: any = null
const transfers = new Set<any>()

function invalid(): never { throw new Error('本地配网原编号或私有资料未通过校验；请保留记录') }
function clone(value: any) { return JSON.parse(JSON.stringify(value)) }
function exact(value: any, fields: string[]) {
  if (!value || typeof value !== 'object' || Array.isArray(value) || Object.keys(value).length !== fields.length || fields.some(field => !Object.prototype.hasOwnProperty.call(value, field))) invalid()
}
function marker(value: any): any {
  if (value === undefined || value === '') return null
  exact(value, ['v', 'deviceId', 'cardId', 'requestId', 'windowId', 'settled'])
  if (value.v !== 1 || typeof value.deviceId !== 'string' || !DEVICE.test(value.deviceId) ||
      typeof value.cardId !== 'string' || !HEX32.test(value.cardId) || typeof value.requestId !== 'string' || !ID.test(value.requestId) ||
      value.windowId !== null && (typeof value.windowId !== 'string' || !HEX32.test(value.windowId)) || typeof value.settled !== 'boolean') invalid()
  return clone(value)
}
function readLocalNetworkMarker(wxApi: any): any {
  try {
    if (faults.has(wxApi)) invalid()
    return marker(wxApi.getStorageSync(LOCAL_NETWORK_STORAGE))
  } catch (_) { faults.add(wxApi); invalid() }
}
function writeMarker(wxApi: any, value: any, expected: any) {
  try {
    if (faults.has(wxApi) || JSON.stringify(readLocalNetworkMarker(wxApi)) !== JSON.stringify(expected)) invalid()
    const checked = marker(value)
    if (!checked) invalid()
    wxApi.setStorageSync(LOCAL_NETWORK_STORAGE, checked)
    if (JSON.stringify(readLocalNetworkMarker(wxApi)) !== JSON.stringify(checked)) invalid()
    return checked
  } catch (_) { faults.add(wxApi); invalid() }
}
function identity(session: any): any {
  const state = session.getState(), context = state && state.localContext
  if (!state || state.mode !== 'live' || state.authKind !== 'local' || state.signedIn !== true || state.localCardRequired === true ||
      !context || typeof context.deviceId !== 'string' || !DEVICE.test(context.deviceId) ||
      typeof context.cardId !== 'string' || !HEX32.test(context.cardId) || typeof context.address !== 'string') invalid()
  return { deviceId: context.deviceId, cardId: context.cardId, address: context.address }
}
function sameIdentity(left: any, right: any) { return left && right && left.deviceId === right.deviceId && left.cardId === right.cardId }
function safeRecord(record: any): any {
  if (!record || typeof record.requestId !== 'string' || !ID.test(record.requestId) || typeof record.deviceId !== 'string' || !DEVICE.test(record.deviceId) ||
      typeof record.windowId !== 'string' || !HEX32.test(record.windowId) || !PHASES.includes(record.status) ||
      typeof record.apName !== 'string' || !/^AI-CAT-[A-F0-9]{6}$/.test(record.apName) || typeof record.expiresAt !== 'number' || !Number.isFinite(record.expiresAt) || record.expiresAt < 0 ||
      record.networkRequestId !== null && (typeof record.networkRequestId !== 'string' || !ID.test(record.networkRequestId))) invalid()
  return { requestId: record.requestId, deviceId: record.deviceId, windowId: record.windowId, status: record.status,
    apName: record.apName, expiresAt: record.expiresAt, networkRequestId: record.networkRequestId,
    hasReceipt: typeof record.receiptQr === 'string' && /^AICAT-PROVISION-RECEIPT:1:[A-Za-z0-9_-]{1,2000}$/.test(record.receiptQr) }
}
function matches(markerValue: any, record: any, context: any) {
  return markerValue && sameIdentity(markerValue, context) && markerValue.deviceId === record.deviceId &&
    markerValue.requestId === record.requestId && (markerValue.windowId === null || markerValue.windowId === record.windowId)
}

function clearLocalProvisionHandoff() {
  handoff = null
  for (const old of Array.from(transfers) as any[]) old.dispose()
}
function offerHandoff(wxApi: any, session: any, record: any, mode: string) {
  clearLocalProvisionHandoff()
  const context = identity(session), checked = safeRecord(record), original = readLocalNetworkMarker(wxApi)
  const invitation = parseProvisioningQR(record.invitation)
  if (!matches(original, checked, context) || invitation.deviceId !== checked.deviceId || invitation.sessionId !== checked.windowId) invalid()
  if (mode === 'configure') {
    if (context.address !== '10.47.0.1' || !['prepared', 'opening', 'open'].includes(checked.status) || checked.networkRequestId !== null || checked.expiresAt * 1000 <= Date.now()) invalid()
  } else if (mode !== 'receipt' || !checked.hasReceipt || checked.networkRequestId === null) invalid()
  // These are the ONLY QR copies. Both are erased on use, expiry, app hide,
  // session invalidation or any navigation failure.
  let invitationQR = record.invitation, receiptQR = mode === 'receipt' ? record.receiptQr : ''
  let disposed = false, deliveredInvitation = false, deliveredReceipt = false
  let detach: any = null, timer: any = null
  const expires = Date.now() + 15000
  const transfer: any = {
    metadata: clone(checked), hasReceipt: mode === 'receipt',
    current() {
      if (disposed || (invitationQR || receiptQR) && Date.now() >= expires) return false
      try { const value = identity(session); return sameIdentity(value, context) && value.address === context.address } catch (_) { return false }
    },
    dispose() {
      if (disposed) return
      disposed = true; invitationQR = ''; receiptQR = ''
      transfers.delete(transfer)
      clearTimeout(timer)
      if (detach) detach()
      detach = null
    },
    deliver(options: any) {
      if (!invitationQR && !receiptQR) return false
      if (!transfer.current()) { transfer.dispose(); options.fail({ errMsg: 'private handoff expired' }); return true }
      let value = ''
      if (!deliveredInvitation && invitationQR) { value = invitationQR; invitationQR = ''; deliveredInvitation = true }
      else if (mode === 'receipt' && !deliveredReceipt && receiptQR) { value = receiptQR; receiptQR = ''; deliveredReceipt = true }
      else return false
      options.success({ scanType: 'QR_CODE', result: value })
      value = ''
      if (!invitationQR && !receiptQR) clearTimeout(timer)
      return true
    },
    canApplyReceipt(state: any) {
      const originalRequest = state && state.reviewIdentity
      return transfer.current() && deliveredInvitation && mode === 'receipt' && state.canScanReceipt === true &&
        originalRequest && originalRequest.deviceId === checked.deviceId && originalRequest.sessionId === checked.windowId &&
        originalRequest.requestId === checked.networkRequestId
    },
    observe(state: any) {
      // This is called only by the original accepted controller after injecting
      // this same invitation; its authenticated terminal path clears only its
      // original pending ID. Unknown/error never changes this outer marker.
      if (disposed || !deliveredInvitation || !state || state.pending || state.busy || state.error ||
          !['connected', 'pending'].includes(state.phase) || !TERMINAL.includes(state.status) ||
          mode === 'receipt' && !deliveredReceipt) return false
      const saved = readLocalNetworkMarker(wxApi)
      if (!matches(saved, checked, context)) invalid()
      writeMarker(wxApi, { ...saved, windowId: checked.windowId, settled: true }, saved)
      return true
    }
  }
  handoff = transfer
  transfers.add(transfer)
  detach = session.attach(() => { if (!transfer.current()) { if (handoff === transfer) handoff = null; transfer.dispose() } })
  if (disposed && detach) { detach(); detach = null }
  if (!disposed) timer = setTimeout(() => { if (handoff === transfer) handoff = null; transfer.dispose() }, 15000)
  return transfer
}
function takeLocalProvisionHandoff() {
  const value = handoff; handoff = null
  if (!value || !value.current()) { if (value) value.dispose(); return null }
  return value
}

function createLocalNetwork(wxApi: any, session: any) {
  let visible = false, epoch = 0, listener: any = null, detach: any = null, intent: any = null, sequence = 0
  let state: any = { paired: false, busy: false, confirming: false, error: '', notice: '', record: null, marker: null }
  let closeout: any = { phase: 'unchecked', loading: false, submitting: false, eligible: false, archived: false, remoteArchived: false, error: '', original: null }
  let closeoutFlight: any = null, closeoutIntent: any = null
  function disposeReview() { if (closeoutIntent && closeoutIntent.core) closeoutIntent.core.hide(); closeoutIntent = null }
  function resetReview() {
    if (closeoutFlight && closeoutFlight.core) closeoutFlight.core.hide()
    disposeReview(); closeoutFlight = null
    closeout = { phase: 'unchecked', loading: false, submitting: false, eligible: false, archived: false, remoteArchived: false, error: '', original: null }
  }
  function getState() {
    const record = state.record, saved = state.marker
    let context: any = null
    try { context = identity(session) } catch (_) {}
    const ready = visible && context && !state.busy && !intent && !closeoutFlight && !closeoutIntent && !faults.has(wxApi)
    let legacyBlocked = false, legacyHint = ''
    try {
      legacyBlocked = inspectLegacyProvisionPending(wxApi).pending === true
      if (legacyBlocked) legacyHint = '原配网页仍有未决 Wi-Fi 请求，只能核对原编号；本次禁止开启新热点窗口。'
    } catch (_) { legacyBlocked = true; legacyHint = '原配网记录不可验证，禁止开启新窗口；保留现有记录，不清数据绕过。' }
    let reviewed = false
    if (closeout.archived && record && saved && saved.settled && closeout.original && closeout.reviewId &&
        matches(saved, record, context) && closeout.original.requestId === record.requestId && closeout.original.windowId === record.windowId) {
      try { reviewed = Boolean(lookupLocalArchive(wxApi, { deviceId: record.deviceId, cardId: context.cardId,
        outerRequestId: record.requestId, windowId: record.windowId, networkRequestId: record.networkRequestId, reviewId: closeout.reviewId })) } catch (_) {}
    }
    const knownSettled = saved && saved.settled && record && (TERMINAL.includes(record.status) || reviewed)
    return clone({ ...state, closeout: { ...closeout, archived: reviewed }, paired: Boolean(context), confirming: Boolean(intent),
      deviceAddress: context ? context.address : '', legacyBlocked, legacyHint,
      canRead: Boolean(ready), canPrepare: Boolean(ready && !legacyBlocked && (!saved || knownSettled)),
      canConfigure: Boolean(ready && !legacyBlocked && context.address === '10.47.0.1' && record &&
        ['prepared', 'opening', 'open'].includes(record.status) && record.networkRequestId === null && record.expiresAt * 1000 > Date.now()),
      canReceipt: Boolean(ready && record && record.hasReceipt && record.networkRequestId !== null) })
  }
  function emit() { if (visible && listener) listener(getState()) }
  function patch(value: any) { state = { ...state, ...value }; emit() }
  function current(expected: number, context?: any) {
    if (!visible || expected !== epoch) return false
    if (!context) return true
    try { const value = identity(session); return sameIdentity(value, context) && value.address === context.address } catch (_) { return false }
  }
  function load() {
    const saved = readLocalNetworkMarker(wxApi)
    state = { ...state, marker: saved }
    return saved
  }
  function ready() {
    if (!visible || state.busy || intent || closeoutFlight || closeoutIntent || faults.has(wxApi)) invalid()
    const context = identity(session), saved = load()
    if (saved && !saved.settled && !sameIdentity(saved, context)) invalid()
    return { context, saved }
  }
  function saveRecord(record: any, context: any, previous: any, replaceSettled = false) {
    const checked = safeRecord(record)
    if (previous && checked.requestId !== previous.requestId) resetReview()
    if (checked.deviceId !== context.deviceId || previous && !matches(previous, checked, context) && !(replaceSettled && previous.settled)) invalid()
    let verifiedUnsubmitted = false
    if (checked.status === 'not_sent' && checked.networkRequestId === null) {
      // This signed outer record proves that THIS opening never accepted target
      // credentials. It says nothing about an older inner Wi-Fi request. Never
      // use it to settle around a real/unknown or unreadable legacy ledger.
      try { verifiedUnsubmitted = inspectLegacyProvisionPending(wxApi).pending === false } catch (_) {}
    }
    const settled = previous && matches(previous, checked, context) && previous.settled || verifiedUnsubmitted
    const next = { v: 1, deviceId: context.deviceId, cardId: context.cardId, requestId: checked.requestId,
      windowId: checked.windowId, settled: Boolean(settled) }
    const saved = writeMarker(wxApi, next, previous)
    patch({ marker: saved, record: checked })
    return checked
  }
  async function randomId(): Promise<string> {
    return new Promise((resolve, reject) => {
      let done = false
      const timer = setTimeout(() => finish(null), 3000)
      const finish = (value: any) => {
        if (done) return
        done = true; clearTimeout(timer)
        if (!value || Object.prototype.toString.call(value.randomValues) !== '[object ArrayBuffer]' || value.randomValues.byteLength !== 16) { reject(new Error('安全随机数不可用')); return }
        resolve('local_wifi_' + Array.from(new Uint8Array(value.randomValues), byte => byte.toString(16).padStart(2, '0')).join(''))
      }
      try { wxApi.getUserCryptoManager().getRandomValues({ length: 16, success: finish, fail: () => finish(null) }) } catch (_) { finish(null) }
    })
  }
  function show(callback: any) {
    hide(); visible = true; listener = callback
    state = { paired: false, busy: false, confirming: false, error: '', notice: '', record: null, marker: null }
    try { load() } catch (_) { state.error = '本地配网原编号存储不可用，禁止新请求；不要清数据绕过。' }
    detach = session.attach(() => {
      if (!visible) return
      if (intent && !current(intent.epoch, intent.context)) intent = null
      const review = closeoutIntent || closeoutFlight
      if (review && !current(review.epoch, review.context)) resetReview()
      emit()
    })
    emit()
  }
  function hide() {
    resetReview()
    visible = false; epoch++; listener = null; intent = null
    if (detach) detach()
    detach = null
    state = { ...state, busy: false, confirming: false, record: null }
  }
  async function prepareOpening() {
    if (!getState().canPrepare) invalid()
    const { context, saved } = ready(), expected = epoch
    if (saved && !saved.settled) invalid()
    if (inspectLegacyProvisionPending(wxApi).pending) invalid()
    patch({ busy: true, error: '', notice: '' })
    try {
      const requestId = await randomId()
      if (!current(expected, context)) invalid()
      const token = 'local-network-confirm-' + (++sequence)
      intent = { token, requestId, epoch: expected, context, previous: saved, expires: Date.now() + 30000 }
      return token
    } finally { if (current(expected)) patch({ busy: false }) }
  }
  function cancelOpening(token: any) { if (intent && intent.token === token) { intent = null; emit() } }
  async function confirmOpening(token: any) {
    const attempt = intent
    if (!attempt || attempt.token !== token) invalid()
    intent = null
    if (!current(attempt.epoch, attempt.context) || Date.now() >= attempt.expires || state.busy) invalid()
    if (!getState().canPrepare) invalid()
    if (inspectLegacyProvisionPending(wxApi).pending) invalid()
    const next = { v: 1, deviceId: attempt.context.deviceId, cardId: attempt.context.cardId,
      requestId: attempt.requestId, windowId: null, settled: false }
    const saved = writeMarker(wxApi, next, attempt.previous)
    patch({ marker: saved, busy: true, error: '', notice: '已保存原窗口请求编号，正在提交一次开启请求。' })
    let dispatched = false
    try {
      if (inspectLegacyProvisionPending(wxApi).pending) invalid()
      dispatched = true
      const result = await session.prepareLocalProvision(attempt.requestId)
      if (!current(attempt.epoch, attempt.context)) return
      saveRecord(result, attempt.context, saved)
      patch({ notice: '猫端已记录本次窗口。请手动连接所示热点；切换系统 Wi-Fi 后必须重新扫同一机身卡，在热点地址配对，再继续填写。' })
    } catch (_) {
      if (current(attempt.epoch)) patch({ error: dispatched
        ? '开启窗口是否被接收尚未核实；保留原编号，只核对原请求，不换号重试。'
        : '检测到原配网未决或存储不可验证，开启请求未发送；保留原编号，不重开或清数据绕过。' })
    } finally { if (current(attempt.epoch)) patch({ busy: false }) }
  }
  async function refresh() {
    const { context, saved } = ready(), expected = epoch
    patch({ busy: true, error: '', notice: '' })
    try {
      if (saved && !saved.settled) {
        const result = await session.statusLocalProvision(saved.requestId)
        if (current(expected, context)) saveRecord(result, context, saved)
      } else {
        const result = await session.currentLocalProvision()
        if (!current(expected, context)) return
        if (result.window === null) patch({ record: null, notice: '猫端没有原配网窗口。只有确认后才会提交新开启请求。' })
        else saveRecord(result.window, context, saved, true)
      }
    } catch (_) { if (current(expected)) patch({ error: '原窗口读取未完成；请确认已重扫同一机身卡并配对猫当前地址，保留原编号。' }) }
    finally { if (current(expected)) patch({ busy: false }) }
  }
  async function openProvision(mode: string) {
    const { context, saved } = ready(), expected = epoch
    if (!saved || !sameIdentity(saved, context)) invalid()
    if (mode === 'configure' && inspectLegacyProvisionPending(wxApi).pending) invalid()
    patch({ busy: true, error: '', notice: '' })
    try {
      const result = await session.resumeLocalProvision(saved.requestId)
      if (!current(expected, context)) return false
      saveRecord(result, context, saved)
      offerHandoff(wxApi, session, result, mode)
      return true
    } catch (_) {
      clearLocalProvisionHandoff()
      if (current(expected)) patch({ error: mode === 'receipt'
        ? '原邀请或收尾回执尚不可用；保留原编号，只读核对，不重新发送网络信息。'
        : '本次窗口尚不可用于填写。请连接本次设备热点、重扫同一机身卡配对，再核对原窗口；不换号重开。' })
      return false
    } finally { if (current(expected)) patch({ busy: false }) }
  }
  function reviewContext() {
    if (!visible || state.busy || intent || closeoutFlight || closeoutIntent || faults.has(wxApi)) invalid()
    const context = identity(session), saved = load()
    if (context.address === '10.47.0.1' || !saved || !sameIdentity(saved, context) || !saved.windowId ||
        typeof session.readLocalProvisionArchive !== 'function') invalid()
    return { context, saved, epoch }
  }
  function sameOriginal(review: any, record: any, context: any) {
    exact(review, ['eligible', 'cardId', 'original', 'status', 'resolution', 'reviewId', 'archivedAt', 'acknowledgements'])
    exact(review.original, ['deviceId', 'requestId', 'windowId', 'networkRequestId'])
    if (typeof review.eligible !== 'boolean' || review.cardId !== context.cardId || review.status !== 'unknown' ||
        review.original.deviceId !== record.deviceId || review.original.requestId !== record.requestId ||
        review.original.windowId !== record.windowId || review.original.networkRequestId !== record.networkRequestId) invalid()
    if (review.resolution === 'pending') {
      if (review.reviewId !== null || review.archivedAt !== null || !Array.isArray(review.acknowledgements) || review.acknowledgements.length) invalid()
    } else if (review.resolution !== 'operator_archived' || review.eligible || typeof review.reviewId !== 'string' ||
        !HEX32.test(review.reviewId) || !Number.isSafeInteger(review.archivedAt) || review.archivedAt < 0 ||
        JSON.stringify(review.acknowledgements) !== JSON.stringify(REVIEW_ACKS)) invalid()
  }
  function archiveContext(review: any) {
    return { deviceId: review.original.deviceId, cardId: review.cardId, outerRequestId: review.original.requestId,
      windowId: review.original.windowId, networkRequestId: review.original.networkRequestId, reviewId: review.reviewId }
  }
  async function readReview(flight: any) {
    const raw = await session.resumeLocalProvision(flight.saved.requestId)
    if (!current(flight.epoch, flight.context)) invalid()
    const record = safeRecord(raw)
    if (!matches(flight.saved, record, flight.context) || record.status !== 'unknown' || !record.networkRequestId || !record.hasReceipt) invalid()
    verifyUnknownReceipt(wxApi, raw.invitation, raw.receiptQr, { deviceId: record.deviceId,
      windowId: record.windowId, networkRequestId: record.networkRequestId })
    const review = await session.readLocalProvisionArchive(record.requestId)
    if (!current(flight.epoch, flight.context)) invalid()
    sameOriginal(review, record, flight.context)
    const legacy = inspectLegacyProvisionPending(wxApi)
    let entry: any = null
    if (legacy.pending) {
      if (!legacy.reviewIdentity || legacy.reviewIdentity.deviceId !== record.deviceId ||
          legacy.reviewIdentity.sessionId !== record.windowId || legacy.reviewIdentity.requestId !== record.networkRequestId) invalid()
    } else {
      if (review.resolution !== 'operator_archived') invalid()
      entry = lookupLocalArchive(wxApi, archiveContext(review))
      if (!entry) invalid()  // No original local evidence: never manufacture it.
    }
    return { raw, record, review, entry }
  }
  async function makeReviewCore(raw: any) {
    const queue = [raw.invitation, raw.receiptQr]
    const core = createLiveProvisioning(wxApi, (options: any) => {
      const value = queue.shift()
      if (!value) { options.fail({ errMsg: 'original receipt unavailable' }); return }
      options.success({ scanType: 'QR_CODE', result: value })
    })
    try {
      core.show(() => {}); await core.scan(); await core.scanReceipt()
      if (core.getState().status !== 'unknown' || core.getState().archiveReady !== true) invalid()
      return core
    } catch (_) { core.hide(); invalid() }
  }
  async function refreshCloseout() {
    let flight: any
    try { flight = reviewContext() } catch (_) { return false }
    closeoutFlight = flight
    closeout = { ...closeout, phase: 'checking', loading: true, eligible: false, error: '' }; emit()
    try {
      const value = await readReview(flight)
      if (!current(flight.epoch, flight.context) || closeoutFlight !== flight) return false
      const remoteArchived = value.review.resolution === 'operator_archived'
      const archived = Boolean(remoteArchived && value.entry && flight.saved.settled)
      state = { ...state, record: value.record }
      closeout = { phase: archived ? 'archived' : remoteArchived ? 'recovering' : value.review.eligible ? 'ready' : 'blocked',
        loading: false, submitting: false, eligible: !remoteArchived && value.review.eligible, archived, remoteArchived,
        original: clone(value.review.original), reviewId: value.review.reviewId,
        error: !remoteArchived && !value.review.eligible ? '当前还不满足收尾条件；请确认旧窗口已结束、网络稳定且没有动作或语音会话，再只读核对。' : '' }
      return true
    } catch (_) {
      if (current(flight.epoch, flight.context)) closeout = { ...closeout, phase: 'blocked', loading: false, submitting: false,
        eligible: false, archived: false, error: '原未知回执、同机身份或本机未决证据尚不可核验；保留原记录，不重新发送。' }
      return false
    } finally { if (closeoutFlight === flight) { closeoutFlight = null; emit() } }
  }
  async function prepareCloseout(acks: any) {
    if (JSON.stringify(acks) !== JSON.stringify(REVIEW_ACKS) || !['ready', 'recovering'].includes(closeout.phase)) invalid()
    const flight = reviewContext()
    closeoutFlight = flight; closeout = { ...closeout, phase: 'preparing', loading: true, eligible: false, error: '' }; emit()
    let core: any = null
    try {
      const value = await readReview(flight)
      let preparation: any = null
      if (value.review.resolution !== 'operator_archived') {
        if (!value.review.eligible) invalid()
        preparation = await session.prepareLocalProvisionArchive({ requestId: value.record.requestId,
          windowId: value.record.windowId, networkRequestId: value.record.networkRequestId, acknowledgements: REVIEW_ACKS.slice() })
        exact(preparation, ['reviewToken', 'reviewId', 'expiresAt', 'cardId', 'original'])
        exact(preparation.original, ['deviceId', 'requestId', 'windowId', 'networkRequestId'])
        if (preparation.cardId !== flight.context.cardId || Object.keys(value.review.original).some(name => preparation.original[name] !== value.review.original[name]) ||
            typeof preparation.reviewToken !== 'string' || !/^[A-Za-z0-9_-]{32,128}$/.test(preparation.reviewToken) ||
            typeof preparation.reviewId !== 'string' || !HEX32.test(preparation.reviewId) ||
            !Number.isFinite(preparation.expiresAt) || preparation.expiresAt * 1000 <= Date.now()) invalid()
      }
      if (!current(flight.epoch, flight.context) || closeoutFlight !== flight) invalid()
      if (!value.entry) core = await makeReviewCore(value.raw)
      const innerToken = core ? core.prepareLocalArchive(REVIEW_ACKS, { cardId: flight.context.cardId, outerRequestId: value.record.requestId }) : null
      if (core && !innerToken || !current(flight.epoch, flight.context) || closeoutFlight !== flight) invalid()
      const token = {}
      closeoutIntent = { ...flight, token, core, innerToken, review: value.review, preparation,
        fingerprint: JSON.stringify(readLocalNetworkMarker(wxApi)),
        expires: Math.min(Date.now() + 20000, preparation ? preparation.expiresAt * 1000 : Date.now() + 20000) }
      state = { ...state, record: value.record }
      closeout = { ...closeout, phase: 'prepared', loading: false, eligible: false, original: clone(value.review.original) }
      return token
    } catch (_) {
      if (core) core.hide()
      if (current(flight.epoch, flight.context)) closeout = { ...closeout, phase: 'blocked', loading: false, eligible: false,
        error: '本次收尾确认未准备好；可能已有未过期确认，请稍后只读核对原记录，不自动重试。' }
      throw new Error('收尾确认未准备好')
    } finally { if (closeoutFlight === flight) { closeoutFlight = null; emit() } }
  }
  function cancelCloseout(token: any) {
    if (!closeoutIntent || closeoutIntent.token !== token) return
    disposeReview(); closeout = { ...closeout, phase: 'blocked', loading: false, eligible: false,
      error: '已取消本次确认，原记录保留；再次操作前请只读核对收尾条件。' }; emit()
  }
  async function confirmCloseout(token: any) {
    const command = closeoutIntent
    if (!command || command.token !== token || !current(command.epoch, command.context) || Date.now() >= command.expires) {
      cancelCloseout(token); return false
    }
    closeoutIntent = null; closeoutFlight = command
    closeout = { ...closeout, phase: 'committing', loading: false, submitting: true, eligible: false, error: '' }; emit()
    try {
      if (JSON.stringify(readLocalNetworkMarker(wxApi)) !== command.fingerprint) invalid()
      let review = command.review
      if (review.resolution !== 'operator_archived') {
        review = await session.confirmLocalProvisionArchive({ requestId: review.original.requestId,
          windowId: review.original.windowId, networkRequestId: review.original.networkRequestId,
          reviewToken: command.preparation.reviewToken, acknowledgements: REVIEW_ACKS.slice(), confirmation: 'archive-unknown-provisioning' })
        if (review.reviewId !== command.preparation.reviewId) invalid()
      } else review = await session.readLocalProvisionArchive(review.original.requestId)
      if (!current(command.epoch, command.context) || closeoutFlight !== command) return false
      sameOriginal(review, state.record, command.context)
      if (review.resolution !== 'operator_archived') invalid()
      if (JSON.stringify(readLocalNetworkMarker(wxApi)) !== command.fingerprint) invalid()
      if (command.core && !command.core.confirmLocalArchive(command.innerToken, review)) invalid()
      const entry = lookupLocalArchive(wxApi, archiveContext(review))
      if (!entry || inspectLegacyProvisionPending(wxApi).pending) invalid()
      const saved = readLocalNetworkMarker(wxApi)
      if (!matches(saved, state.record, command.context)) invalid()
      if (!saved.settled) state.marker = writeMarker(wxApi, { ...saved, settled: true }, saved)
      else state.marker = saved
      closeout = { phase: 'archived', loading: false, submitting: false, eligible: false, archived: true,
        remoteArchived: true, original: clone(review.original), reviewId: review.reviewId, error: '' }
      return true
    } catch (_) {
      if (current(command.epoch, command.context)) closeout = { ...closeout, phase: 'unknown', loading: false,
        submitting: false, eligible: false, archived: false,
        error: faults.has(wxApi)
          ? '手机本地写入尚未核实。请退出并重新打开小程序，保留全部数据，再核对原编号；不要清缓存或重复提交。'
          : '两端收尾尚未完整核实。保留原编号，请只读核对；不重复提交、不清数据。' }
      return false
    } finally {
      if (command.core) command.core.hide()
      if (closeoutFlight === command) { closeoutFlight = null; emit() }
    }
  }
  return { show, hide, getState, prepareOpening, cancelOpening, confirmOpening, refresh, openProvision,
    refreshCloseout, prepareCloseout, confirmCloseout, cancelCloseout }
}

module.exports = { createLocalNetwork, readLocalNetworkMarker, clearLocalProvisionHandoff, takeLocalProvisionHandoff, LOCAL_NETWORK_STORAGE }
