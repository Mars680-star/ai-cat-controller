// One in-memory identity shared by product pages. No automatic login/claim,
// persistent storage or fallback to demo readings. Motion requests are explicit,
// selected-device guarded calls; no background command or automatic retry.
const { createCloudAuthClient, cloudAuthErrorMessage } = require('./cloud-auth-client')
const { createLocalDeviceClient } = require('./local-device-client')
const { readLocalNetworkMarker } = require('./local-network')
const { inspectLegacyProvisionPending } = require('./provision-ledger-guard')

const empty = (mode = 'preview'): any => ({
  mode, phase: mode === 'preview' ? 'preview' : 'auth-required',
  error: '', message: mode === 'preview' ? '界面预览 · 未连接设备' : '请验证微信登录',
  health: null, signedIn: false, expiresAt: '', pets: [], selectedPetId: '', snapshot: null,
  loading: false, listLoading: false, statusLoading: false
})
const clone = (value: any): any => JSON.parse(JSON.stringify(value))
const authFailure = (error: any) => error && ['UNAUTHORIZED', 'AUTH_REQUIRED'].includes(error.code)

function createLiveDeviceSession(wxApi: any, options?: any) {
  let client: any = createCloudAuthClient(wxApi, options)
  let authKind = 'wechat', localCardMissing = false, localIntent: any = null
  let commandFlights = 0, motionUnresolved = false, transportStorageFault = false
  let state: any = empty()
  let version = 0
  let authorized = false
  let healthFlight: any = null, loginFlight: any = null, listFlight: any = null, statusFlight: any = null
  let pollTimer: any = null, expiryTimer: any = null
  const listeners = new Set<(value: any) => void>()
  function initial(mode = 'preview') {
    return { ...empty(mode), authKind, localCardRequired: localCardMissing,
      localContext: authKind === 'local' ? client.getLocalContext() : null }
  }
  function configured() { return state.health && (authKind === 'local' ? state.health.mode === 'local-device' && state.health.localConfigured === true && !localCardMissing : state.health.wechatConfigured === true) }

  function getState() { return clone(state) }
  function emit() {
    for (const listener of Array.from(listeners)) {
      try { listener(getState()) } catch (_) { /* A view must not break the session. */ }
    }
  }
  function patch(value: any) { state = { ...state, ...value }; emit() }
  function active() { return state.mode === 'live' && listeners.size > 0 }
  function current(expected: number) { return active() && expected === version }
  function stopTimers() {
    clearTimeout(pollTimer); clearTimeout(expiryTimer)
    pollTimer = null; expiryTimer = null
  }
  function invalidate() {
    version++
    stopTimers()
    healthFlight = null; loginFlight = null; listFlight = null; statusFlight = null
    client.cancelPending()
  }
  function resetIdentity(message: string, error = '', preserveHealth = true) {
    invalidate()
    client.clearSession()
    authorized = false
    const health = preserveHealth ? state.health : null
    state = { ...initial(state.mode), health, message, error }
    emit()
  }
  function hasIdentity() {
    if (!state.signedIn) return false
    if (client.peekSession()) return true
    resetIdentity('登录已失效，请重新验证', cloudAuthErrorMessage({ code: 'AUTH_REQUIRED' }))
    return false
  }
  function armExpiry() {
    clearTimeout(expiryTimer); expiryTimer = null
    if (!active() || !state.signedIn) return
    const metadata = client.peekSession()
    if (!metadata) { hasIdentity(); return }
    const expected = version
    expiryTimer = setTimeout(() => {
      expiryTimer = null
      if (!current(expected)) return
      if (hasIdentity()) armExpiry()
    }, Math.min(2147483647, Math.max(1, Date.parse(metadata.expiresAt) - Date.now())))
  }
  function selectionReady() {
    return active() && state.signedIn && authorized && !state.loading && !state.listLoading &&
      Boolean(state.selectedPetId) && state.pets.some((pet: any) => pet.petId === state.selectedPetId)
  }
  function startPolling() {
    clearTimeout(pollTimer); pollTimer = null
    if (!selectionReady() || statusFlight) return
    const expected = version
    pollTimer = setTimeout(() => {
      pollTimer = null
      if (current(expected)) readStatus()
    }, 5000)
  }

  function readStatus(): Promise<any> {
    if (!selectionReady() || !hasIdentity()) return Promise.resolve()
    if (statusFlight) return statusFlight.promise
    clearTimeout(pollTimer); pollTimer = null
    const flight: any = { expected: version, petId: state.selectedPetId }
    statusFlight = flight
    patch({ statusLoading: true, error: '', phase: 'reading', message: '正在读取真实状态' })
    flight.promise = client.readPetStatus(flight.petId).then((snapshot: any) => {
      if (!current(flight.expected) || statusFlight !== flight || !hasIdentity()) return
      patch({ snapshot, phase: 'live', message: snapshot.device.connected ? '真实状态 · 设备在线' : '真实状态 · 设备未连接', error: '' })
    }).catch((error: any) => {
      if (!current(flight.expected) || statusFlight !== flight) return
      if (authFailure(error)) { resetIdentity('登录已失效，请重新验证', cloudAuthErrorMessage(error)); return }
      patch({ snapshot: null, phase: 'error', message: '状态暂不可达，请重试', error: cloudAuthErrorMessage(error) })
    }).finally(() => {
      if (!current(flight.expected) || statusFlight !== flight) return
      statusFlight = null
      patch({ statusLoading: false })
      armExpiry(); startPolling()
    })
    return flight.promise
  }

  function loadPets(): Promise<any> {
    if (!active() || !hasIdentity()) return Promise.resolve()
    if (listFlight) return listFlight.promise
    const flight: any = { expected: version }
    listFlight = flight
    authorized = false
    patch({ listLoading: true, snapshot: null, statusLoading: false, error: '', phase: 'loading-pets', message: '正在核对设备授权' })
    flight.promise = client.listPets().then((pets: any[]) => {
      if (!current(flight.expected) || listFlight !== flight || !hasIdentity()) return
      const selected = pets.find(pet => pet.petId === state.selectedPetId)
      const lost = Boolean(state.selectedPetId && !selected)
      authorized = true
      patch({ pets, selectedPetId: selected ? selected.petId : '', snapshot: null,
        phase: 'selection', message: lost ? '设备授权已移除，请重新选择' : pets.length ? '请选择已授权设备' : '暂无已授权设备' })
    }).catch((error: any) => {
      if (!current(flight.expected) || listFlight !== flight) return
      if (authFailure(error)) { resetIdentity('登录已失效，请重新验证', cloudAuthErrorMessage(error)); return }
      authorized = false
      patch({ pets: [], selectedPetId: '', snapshot: null, phase: 'error', message: '授权列表未更新，请重试', error: cloudAuthErrorMessage(error) })
    }).finally(() => {
      if (!current(flight.expected) || listFlight !== flight) return
      listFlight = null
      patch({ listLoading: false })
      armExpiry()
    }).then(() => {
      if (current(flight.expected) && authorized && state.selectedPetId) return readStatus()
    })
    return flight.promise
  }

  function refreshPets(): Promise<any> {
    if (!active() || !state.signedIn || state.loading || !hasIdentity()) return Promise.resolve()
    if (listFlight) return listFlight.promise
    invalidate()
    return loadPets()
  }

  function checkHealth(): Promise<any> {
    if (!active()) return Promise.resolve()
    if (healthFlight) return healthFlight.promise
    if (state.loading) return Promise.resolve()
    invalidate()
    authorized = false
    const flight: any = { expected: version }
    healthFlight = flight
    patch({ health: null, snapshot: null, loading: true, listLoading: false, statusLoading: false,
      phase: 'checking', message: '正在检查网关', error: '' })
    flight.promise = client.getHealth().then((health: any) => {
      if (!current(flight.expected) || healthFlight !== flight) return
      patch({ health, phase: state.signedIn ? 'selection' : 'ready',
        message: state.signedIn ? authKind === 'local' ? '机身卡本地配对已验证' : '微信身份已验证' :
          authKind === 'local' ? '猫端网关已就绪，请确认机身卡配对' : health.wechatConfigured ? '网关已就绪，可验证微信登录' : '等待配置微信凭据' })
    }).catch((error: any) => {
      if (!current(flight.expected) || healthFlight !== flight) return
      if (authFailure(error)) { resetIdentity('登录已失效，请重新验证', cloudAuthErrorMessage(error)); return }
      patch({ health: null, snapshot: null, phase: 'error', message: '网关暂不可达', error: cloudAuthErrorMessage(error) })
    }).finally(() => {
      if (!current(flight.expected) || healthFlight !== flight) return
      healthFlight = null
      patch({ loading: false })
      armExpiry()
    }).then(() => {
      if (current(flight.expected) && state.health && state.signedIn) return loadPets()
    })
    return flight.promise
  }

  function login(): Promise<any> {
    if (!active()) return Promise.resolve()
    if (loginFlight) return loginFlight.promise
    if (state.signedIn || state.loading || !configured()) return Promise.resolve()
    invalidate()
    client.clearSession()
    authorized = false
    const flight: any = { expected: version }
    loginFlight = flight
    patch({ loading: true, signedIn: false, expiresAt: '', pets: [], selectedPetId: '', snapshot: null,
      listLoading: false, statusLoading: false, phase: 'login', message: authKind === 'local' ? '正在验证机身卡本地配对' : '正在验证微信身份', error: '' })
    flight.promise = client.login().then(() => {
      if (!current(flight.expected) || loginFlight !== flight) return
      const metadata = client.peekSession()
      if (!metadata) { resetIdentity('登录已失效，请重新验证', cloudAuthErrorMessage({ code: 'AUTH_REQUIRED' })); return }
      patch({ signedIn: true, expiresAt: metadata.expiresAt, loading: false, phase: 'selection', message: authKind === 'local' ? '本地配对成功 · 非微信账号登录' : '微信身份验证成功' })
      armExpiry()
      return loadPets()
    }).catch((error: any) => {
      if (current(flight.expected) && loginFlight === flight) resetIdentity('登录未完成，请重试', cloudAuthErrorMessage(error))
    }).finally(() => {
      if (!current(flight.expected) || loginFlight !== flight) return
      loginFlight = null
      patch({ loading: false })
    })
    return flight.promise
  }

  function selectPet(petId: string): Promise<any> {
    if (!active() || !authorized || state.loading || state.listLoading || !hasIdentity()) return Promise.resolve()
    if (!state.pets.some((pet: any) => pet.petId === petId)) return Promise.resolve()
    if (state.selectedPetId === petId) return readStatus()
    invalidate()
    patch({ selectedPetId: petId, snapshot: null, statusLoading: false, phase: 'reading', message: '正在读取真实状态', error: '' })
    armExpiry()
    return readStatus()
  }

  function refresh(): Promise<any> {
    if (!active() || state.loading) return Promise.resolve()
    if (!state.signedIn) return checkHealth()
    if (state.listLoading) return listFlight ? listFlight.promise : Promise.resolve()
    return authorized && state.selectedPetId ? readStatus() : refreshPets()
  }

  function enable(): Promise<any> {
    if (state.mode === 'preview') {
      invalidate(); client.clearSession(); authorized = false
      state = initial('live'); emit()
    }
    if (!active()) return Promise.resolve()
    return state.signedIn ? refresh() : checkHealth()
  }
  function cancelLocal(token?: any) {
    if (localIntent && (token === undefined || localIntent.token === token)) { localIntent.client.destroy(); localIntent = null }
  }
  function clear() {
    cancelLocal()
    if (authKind === 'local') { client.destroy(); localCardMissing = true }
    resetIdentity(state.mode === 'preview' ? '界面预览 · 未连接设备' : authKind === 'local' ? '本地密钥已清除，请重新扫描机身卡' : '请重新验证微信登录', '', false)
  }
  function preview() {
    invalidate(); client.clearSession(); authorized = false
    state = initial(); emit()
  }
  function logout(): Promise<any> {
    if (!active() || state.loading || !state.signedIn) return Promise.resolve()
    invalidate(); authorized = false
    const expected = version, health = state.health
    state = { ...initial('live'), health, loading: true, message: '正在注销业务会话' }; emit()
    return client.logout().then(() => {
      if (current(expected)) patch({ message: '业务会话已注销' })
    }).catch(() => {
      if (current(expected)) patch({ message: '本机已退出', error: '服务端注销未确认，旧会话将按有效期失效。' })
    }).finally(() => {
      if (current(expected)) { client.clearSession(); patch({ loading: false }) }
    })
  }

  function attach(listener: (value: any) => void) {
    // Each subscription owns its detach even if two callers reuse a callback.
    const subscription = (value: any) => listener(value)
    const first = listeners.size === 0
    listeners.add(subscription)
    try { listener(getState()) } catch (_) {}
    if (first && active()) {
      const metadata = client.peekSession()
      if (metadata && state.signedIn) refreshPets()
      else {
        if (state.signedIn) resetIdentity('登录已失效，请重新验证', cloudAuthErrorMessage({ code: 'AUTH_REQUIRED' }))
        checkHealth()
      }
    }
    return () => {
      if (!listeners.delete(subscription) || listeners.size > 0) return
      invalidate()
      authorized = false
      const metadata = client.peekSession()
      state = { ...state, snapshot: null, loading: false, listLoading: false, statusLoading: false,
        signedIn: Boolean(metadata && state.signedIn), expiresAt: metadata ? metadata.expiresAt : '',
        phase: state.mode === 'preview' ? 'preview' : 'auth-required',
        message: state.mode === 'preview' ? '界面预览 · 未连接设备' : '页面已暂停，重新打开后核对设备授权', error: '' }
      if (!metadata) state = { ...state, pets: [], selectedPetId: '' }
    }
  }

  function motionRequest(method: string, args: any[]): Promise<any> {
    if (!selectionReady() || !hasIdentity()) {
      const error: any = new Error('AUTH_REQUIRED'); error.code = 'AUTH_REQUIRED'; return Promise.reject(error)
    }
    const expected = version, petId = state.selectedPetId
    const command = ['executeMotion', 'stopMotion', 'sendDialogCommand', 'sendPreferencesCommand', 'recordInteraction'].includes(method)
    if (command) commandFlights++
    return client[method](petId, ...args).then((value: any) => {
      if (!current(expected) || !selectionReady() || state.selectedPetId !== petId || !hasIdentity()) {
        const error: any = new Error('CANCELLED'); error.code = 'CANCELLED'; throw error
      }
      if (['readMotion', 'executeMotion', 'stopMotion'].includes(method)) motionUnresolved = Boolean(value.deviceBusy || value.execution && ['sending', 'running', 'unknown'].includes(value.execution.status))
      return value
    }).catch((error: any) => {
      // A late response from an old view/selection must not clear a new identity.
      if (current(expected) && authFailure(error)) resetIdentity('登录已失效，请重新验证', cloudAuthErrorMessage(error))
      if (['executeMotion', 'stopMotion'].includes(method) && !['AUTH_REQUIRED', 'UNAUTHORIZED', 'INVALID_ID'].includes(error && error.code)) motionUnresolved = true
      throw error
    }).finally(() => { if (command) commandFlights-- })
  }

  function assertSwitch(target?: any) {
    const context = authKind === 'local' ? client.getLocalContext() : null
    const sameLocal = context && target && context.deviceId === target.deviceId && context.cardId === target.cardId
    if (commandFlights || motionUnresolved && !sameLocal) throw new Error('仍有动作或控制请求待核对，禁止切换连接；请回原设备核对，不要清除记录')
    function badStorage(): never { transportStorageFault = true; throw new Error('未决记录不可验证，禁止切换连接；不能清记录绕过') }
    function read(key: string) { if (transportStorageFault) badStorage(); try { return wxApi.getStorageSync(key) } catch (_) { badStorage() } }
    let legacy: any
    try { legacy = inspectLegacyProvisionPending(wxApi) } catch (_) { badStorage() }
    if (legacy.pending && (!target || !legacy.reviewIdentity || target.deviceId !== legacy.reviewIdentity.deviceId)) throw new Error('原配网请求仍待核对，只允许重新配对原设备恢复回执；不能切换电脑或其它设备')
    let network: any
    try { network = readLocalNetworkMarker(wxApi) } catch (_) { badStorage() }
    if (network) {
      if (!network.settled && (!target || target.deviceId !== network.deviceId || target.cardId !== network.cardId)) throw new Error('有待核对的本地配网窗口，只允许重新配对原机身卡；不能切换电脑或其它设备')
    }
    for (const [storage, fields] of [['aiCatDialogPendingV1', ['main', 'interrupt']], ['aiCatPreferencesPendingV1', ['settings', 'feedback']]] as any[]) {
      const raw = read(storage)
      if (raw === undefined || raw === '') continue
      if (!raw || Object.keys(raw).length !== 3 || raw.version !== 1 || fields.some((field: string) => !Object.prototype.hasOwnProperty.call(raw, field))) badStorage()
      for (const field of fields) {
        const item = raw[field]
        if (item === null) continue
        const user = target ? 'loc_' + target.deviceId.slice(4) + '_' + target.cardId : ''
        const pet = target ? 'lp_' + target.deviceId.slice(4) : ''
        if (!item || Object.keys(item).length !== 4 || typeof item.userId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(item.userId) ||
            typeof item.petId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(item.petId) || typeof item.requestId !== 'string' || !/^[A-Za-z0-9_.:-]{1,64}$/.test(item.requestId) || typeof item.kind !== 'string') badStorage()
        if (target ? item.userId !== user || item.petId !== pet : item.userId.startsWith('loc_')) throw new Error('另一个连接或身份有未决请求，请回原上下文核对；不能清记录绕过')
      }
    }
  }
  function prepareLocal(card: string, address: string) {
    cancelLocal()
    const candidate = createLocalDeviceClient(wxApi, card, address), context = candidate.getLocalContext()
    try { assertSwitch(context) } catch (error) { candidate.destroy(); throw error }
    const token = {}
    localIntent = { token, client: candidate, expiresAt: Date.now() + 30000 }
    return { token, context }
  }
  async function confirmLocal(token: any) {
    if (!localIntent || localIntent.token !== token || Date.now() >= localIntent.expiresAt || !listeners.size) { cancelLocal(token); throw new Error('配对确认已过期，请重新扫码') }
    const intent = localIntent
    try { assertSwitch(intent.client.getLocalContext()) } catch (error) { cancelLocal(token); throw error }
    localIntent = null
    invalidate(); client.clearSession(); if (typeof client.destroy === 'function') client.destroy()
    client = intent.client; authKind = 'local'; localCardMissing = false; authorized = false
    state = initial('live'); emit()
    const pairingClient = intent.client, checking = checkHealth(), expected = version
    await checking
    // A cancelled old card must never authorise login on a replacement client.
    if (client === pairingClient && current(expected) && configured() && !state.error) await login()
  }
  async function useComputerGateway() {
    assertSwitch()
    cancelLocal(); invalidate(); client.clearSession(); if (typeof client.destroy === 'function') client.destroy()
    client = createCloudAuthClient(wxApi, options); authKind = 'wechat'; localCardMissing = false; authorized = false
    state = initial('live'); emit()
    if (active()) await checkHealth()
  }
  function localProvisionRequest(method: string, argument?: any) {
    if (authKind !== 'local' || !active() || !hasIdentity() || !state.signedIn || localCardMissing || typeof client[method] !== 'function') {
      return Promise.reject(Object.assign(new Error('本地配对已失效，请重新扫码'), { code: 'AUTH_REQUIRED' }))
    }
    const expected = version
    commandFlights++
    return client[method](argument).then((value: any) => {
      if (!current(expected) || authKind !== 'local' || !hasIdentity()) throw Object.assign(new Error('本地确认已变化'), { code: 'CANCELLED' })
      return value
    }).catch((error: any) => {
      if (current(expected) && authFailure(error)) resetIdentity('本地配对已失效，请重新扫码', cloudAuthErrorMessage(error))
      throw error
    }).finally(() => commandFlights--)
  }

  return { attach, getState, enable, preview, login, checkHealth, refreshPets, selectPet, refresh, logout, clear,
    prepareLocal, confirmLocal, cancelLocal, useComputerGateway,
    prepareLocalProvision: (requestId: string) => localProvisionRequest('prepareLocalProvision', requestId),
    currentLocalProvision: () => localProvisionRequest('currentLocalProvision'),
    resumeLocalProvision: (requestId: string) => localProvisionRequest('resumeLocalProvision', requestId),
    statusLocalProvision: (requestId: string) => localProvisionRequest('statusLocalProvision', requestId),
    receiptLocalProvision: (requestId: string) => localProvisionRequest('receiptLocalProvision', requestId),
    readLocalProvisionArchive: (requestId: string) => localProvisionRequest('readLocalProvisionArchive', requestId),
    prepareLocalProvisionArchive: (body: any) => localProvisionRequest('prepareLocalProvisionArchive', body),
    confirmLocalProvisionArchive: (body: any) => localProvisionRequest('confirmLocalProvisionArchive', body),
    getIdentity: () => state.signedIn ? client.peekIdentity() : null,
    readDashboard: () => motionRequest('readDashboard', []),
    readIntimacy: () => motionRequest('readIntimacy', []),
    readGrowth: () => motionRequest('readGrowth', []),
    readDialogStatus: () => motionRequest('readDialogStatus', []),
    readDialogControl: () => motionRequest('readDialogControl', []),
    readPreferences: () => motionRequest('readPreferences', []),
    readPreferencesReceipt: (requestId: string) => motionRequest('readPreferencesReceipt', [requestId]),
    sendPreferencesCommand: (command: any) => motionRequest('sendPreferencesCommand', [command]),
    readDialogReceipt: (requestId: string) => motionRequest('readDialogReceipt', [requestId]),
    sendDialogCommand: (command: any) => motionRequest('sendDialogCommand', [command]),
    readDialogConversations: (revision?: string) => motionRequest('readDialogConversations', [revision]),
    readDialogConversation: (conversationId: string, revision?: string) => motionRequest('readDialogConversation', [conversationId, revision]),
    recordInteraction: (eventType: string, requestId: string) => motionRequest('recordInteraction', [eventType, requestId]),
    readMotion: () => motionRequest('readMotion', []),
    readMotionHistory: () => motionRequest('readMotionHistory', []),
    executeMotion: (actionId: string, requestId: string) => motionRequest('executeMotion', [actionId, requestId]),
    stopMotion: (requestId: string, executionId?: string) => motionRequest('stopMotion', [requestId, executionId]) }
}

let sharedSession: any = null
function getLiveDeviceSession() {
  if (!sharedSession) sharedSession = createLiveDeviceSession(wx)
  return sharedSession
}
module.exports = { createLiveDeviceSession, getLiveDeviceSession }
