// Explicit, dormant cloud-auth preparation. Never selected by the Mock PetService.
const { verifyReadOnlyStatus } = require('./dev-gateway-client')
const { verifyMotion, verifyMotionHistory, actionValid, requestIdValid } = require('./live-motion')
const { verifyDashboard, verifyIntimacy, verifyGrowth, verifyInteraction, productEventValid, productRequestIdValid } = require('./live-product')
const { verifyDialogStatus, verifyDialogConversations, verifyDialogConversation, conversationIdValid, dialogRevisionValid } = require('./live-dialog')
const { verifyDialogControl, verifyDialogCommand, verifyDialogReceipt, dialogControlRequestIdValid } = require('./live-dialog-control')
const { verifyPreferences, verifyPreferencesCommand, verifyPreferencesReceipt, preferencesReceiptMatchesCommand, preferencesRequestIdValid } = require('./live-preferences')
const { LOOPBACK_GATEWAY_ORIGIN, PHONE_LAN_GATEWAY_ORIGIN, phoneLanEnabled, developmentGatewayInfo } = require('./development-gateway')
const DEFAULT_BASE_URL = LOOPBACK_GATEWAY_ORIGIN
const MESSAGES: any = {
  CONFIG_INVALID: '接入地址未通过安全校验',
  AUTH_REQUIRED: '请先登录后再读取设备',
  UNAUTHORIZED: '登录已失效，请重新登录',
  LOGIN_FAILED: '微信登录未完成，请重新尝试',
  TIMEOUT: '请求超时，请重新尝试',
  UNAVAILABLE: '服务暂不可用，请稍后重试',
  UNVERIFIED: '服务响应未通过校验，请稍后重试',
  CANCELLED: '操作已取消',
  INVALID_ID: '设备标识未通过校验',
  LOCAL_NATIVE_DOMAIN: '微信拒绝了本地地址请求，请检查开发版调试的地址校验设置。[LOCAL_NATIVE_DOMAIN]',
  LOCAL_NATIVE_TIMEOUT: '微信访问设备超时，请核对当前 Wi-Fi 与设备地址。[LOCAL_NATIVE_TIMEOUT]',
  LOCAL_NATIVE_PERMISSION: '微信本地网络请求被权限或隐私检查拒绝。[LOCAL_NATIVE_PERMISSION]',
  LOCAL_NATIVE_NETWORK: '微信未能访问设备；若手机浏览器可达，请检查微信网络权限与开发版调试设置。[LOCAL_NATIVE_NETWORK]',
  LOCAL_CRYPTO: '微信当前运行环境无法使用所需的加密组件，未发送配对资料。[LOCAL_CRYPTO]',
  LOCAL_RANDOM: '微信安全随机数不可用，未发送配对资料。[LOCAL_RANDOM]',
  LOCAL_CHALLENGE_HTTP: '设备挑战接口未返回可用响应，请核对设备服务。[LOCAL_CHALLENGE_HTTP]',
  LOCAL_CARD_MISMATCH: '当前地址的设备身份与机身卡不一致，请核对原设备和原卡。[LOCAL_CARD_MISMATCH]',
  LOCAL_CHALLENGE_AUTH: '设备挑战未通过加密校验；不要更换密钥或清缓存，请保留原卡核查。[LOCAL_CHALLENGE_AUTH]',
  LOCAL_RESPONSE_HTTP: '加密请求未收到设备的正常响应，请核对服务状态。[LOCAL_RESPONSE_HTTP]',
  LOCAL_RESPONSE_AUTH: '设备加密响应未通过校验，请保留原记录核查。[LOCAL_RESPONSE_AUTH]',
  LOCAL_REQUEST_INVALID: '本地请求未通过安全检查，未自动重试。[LOCAL_REQUEST_INVALID]'
}

function cloudAuthErrorMessage(error: any): string {
  const code = error && error.code
  return typeof code === 'string' && Object.prototype.hasOwnProperty.call(MESSAGES, code) ? MESSAGES[code] : MESSAGES.UNAVAILABLE
}
function fail(code: string): any { const error: any = new Error(MESSAGES[code]); error.code = code; return error }
function object(value: any) { return value && typeof value === 'object' && !Array.isArray(value) }
function validId(value: any) { return typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value) }

function baseOrigin(wxApi: any, options: any): string {
  if (options === undefined) return developmentGatewayInfo(wxApi).baseUrl
  if (!object(options) || Object.keys(options).some(key => key !== 'baseUrl')) throw fail('CONFIG_INVALID')
  const value = options.baseUrl === undefined ? developmentGatewayInfo(wxApi).baseUrl : options.baseUrl
  if (value === DEFAULT_BASE_URL) return value
  // A single compiled LAN pin, proven Android develop runtime only. This never
  // broadens the HTTPS validator or enables arbitrary HTTP/private IP origins.
  if (value === PHONE_LAN_GATEWAY_ORIGIN && phoneLanEnabled(wxApi)) return value
  if (typeof value !== 'string') throw fail('CONFIG_INVALID')
  // Pure HTTPS DNS origins only: no credentials, path, query, fragment, IPv4/IPv6 or numeric IP aliases.
  const match = /^https:\/\/([A-Za-z0-9.-]+)(?::([0-9]{1,5}))?$/.exec(value)
  if (!match) throw fail('CONFIG_INVALID')
  const host = match[1].toLowerCase()
  const labels = host.split('.')
  if (host.length > 253 || labels.length < 2 || !/^[A-Za-z]{2,63}$/.test(labels[labels.length - 1])) throw fail('CONFIG_INVALID')
  if (labels.some(label => !/^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$/.test(label))) throw fail('CONFIG_INVALID')
  if (host === 'metadata.google.internal' || host === 'instance-data.ec2.internal' || /(?:^|\.)(?:localhost|local|internal|metadata)$/.test(host)) throw fail('CONFIG_INVALID')
  if (match[2] && (Number(match[2]) < 1 || Number(match[2]) > 65535 || String(Number(match[2])) !== match[2])) throw fail('CONFIG_INVALID')
  return value
}

function futureUtc(value: any): string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(value)) throw fail('UNVERIFIED')
  const timestamp = Date.parse(value)
  if (!Number.isFinite(timestamp) || timestamp <= Date.now() || new Date(timestamp).toISOString().slice(0, 19) !== value.slice(0, 19)) throw fail('UNVERIFIED')
  return value
}
function sessionMetadata(value: any): any {
  if (!object(value) || !validId(value.userId)) throw fail('UNVERIFIED')
  return { userId: value.userId, expiresAt: futureUtc(value.expiresAt) }
}
function validateHealth(value: any): any {
  if (!object(value) || value.mode !== 'local-development' || typeof value.wechatConfigured !== 'boolean' || typeof value.readOnly !== 'boolean') throw fail('UNVERIFIED')
  return { mode: value.mode, wechatConfigured: value.wechatConfigured, readOnly: value.readOnly }
}
function validatePets(value: any): any[] {
  if (!Array.isArray(value) || value.length > 1000) throw fail('UNVERIFIED')
  const seen = new Set()
  return value.map(pet => {
    if (!object(pet) || !validId(pet.petId) || seen.has(pet.petId) || typeof pet.name !== 'string' || !pet.name.trim() || pet.name.length > 80 || /[\x00-\x1f\x7f]/.test(pet.name)) throw fail('UNVERIFIED')
    seen.add(pet.petId)
    return { petId: pet.petId, name: pet.name }
  })
}

// Shared DTO/receipt boundary. The optional transport is module-internal plumbing
// for the separately authenticated encrypted local client, never a public URL option.
function createGatewayClientCore(wxApi: any, options?: any, localTransport?: any) {
  const baseUrl = localTransport ? '' : baseOrigin(wxApi, options)
  let version = 0
  let session: any = null // The bearer is never returned, logged or written outside this closure.
  let loginFlight: any = null
  const pending = new Set<() => void>()

  function cancelPending() {
    version++
    loginFlight = null
    for (const cancel of Array.from(pending)) cancel()
  }
  function clearSession() {
    session = null
    cancelPending()
  }
  function current(expected: number) { if (expected !== version) throw fail('CANCELLED') }
  function activeSession(): any {
    if (!session) throw fail('AUTH_REQUIRED')
    if (Date.parse(session.expiresAt) <= Date.now()) { clearSession(); throw fail('AUTH_REQUIRED') }
    return session
  }

  function loginCode(expected: number): Promise<string> {
    return new Promise((resolve, reject) => {
      let settled = false
      let timer: any
      const finish = (code: string | null, value?: string) => {
        if (settled) return
        settled = true; clearTimeout(timer); pending.delete(cancel)
        if (code) reject(fail(code)); else resolve(value as string)
      }
      const cancel = () => finish('CANCELLED')
      pending.add(cancel)
      timer = setTimeout(() => finish('TIMEOUT'), 5000)
      try {
        current(expected)
        wxApi.login({
          timeout: 5000,
          success(result: any) {
            if (settled) return
            if (expected !== version) { cancel(); return }
            if (!object(result) || typeof result.code !== 'string' || !/^[A-Za-z0-9._~-]{1,512}$/.test(result.code)) { finish('LOGIN_FAILED'); return }
            finish(null, result.code)
          },
          fail() { finish(expected === version ? 'LOGIN_FAILED' : 'CANCELLED') }
        })
      } catch (_) { finish(expected === version ? 'LOGIN_FAILED' : 'CANCELLED') }
    })
  }

  function request(method: string, route: string, body: any, bearer: string | null, expected: number, validate: (value: any) => any, timeoutMS = 5000): Promise<any> {
    const promise = new Promise((resolve, reject) => {
      let settled = false
      let task: any = null
      let timer: any
      const finish = (code: string | null, value?: any, abort = false) => {
        if (settled) return
        settled = true; clearTimeout(timer); pending.delete(cancel)
        if (code) reject(fail(code)); else resolve(value)
        if (abort) { try { if (task && typeof task.abort === 'function') task.abort() } catch (_) {} }
        task = null
      }
      const cancel = () => finish('CANCELLED', undefined, true)
      if (expected !== version) { cancel(); return }
      pending.add(cancel)
      timer = setTimeout(() => finish('TIMEOUT', undefined, true), timeoutMS)
      try {
        const header: any = { 'content-type': 'application/json' }
        if (bearer) header.Authorization = 'Bearer ' + bearer
        const nativeOptions: any = {
          url: baseUrl + route, method, header, timeout: timeoutMS,
          ...(body === undefined ? {} : { data: body }),
          success(response: any) {
            if (settled) return
            if (expected !== version) { cancel(); return }
            if (response && response.statusCode === 401) {
              finish('UNAUTHORIZED')
              clearSession()
              return
            }
            if (!response || response.statusCode !== 200) { finish('UNAVAILABLE'); return }
            try {
              if (!object(response.data) || response.data.success !== true || !Object.prototype.hasOwnProperty.call(response.data, 'data')) throw fail('UNVERIFIED')
              finish(null, validate(response.data.data))
            } catch (_) { finish('UNVERIFIED') }
          },
          fail(error: any) {
            if (settled) return
            if (expected !== version) { cancel(); return }
            // Only our internal encrypted transport can brand a diagnostic.
            // Never trust native/server error fields or remote message text.
            if (localTransport && typeof localTransport.diagnosticCode === 'function') {
              let code: any
              try { code = localTransport.diagnosticCode(error) } catch (_) {}
              if (typeof code === 'string' && code.startsWith('LOCAL_') && Object.prototype.hasOwnProperty.call(MESSAGES, code)) { finish(code); return }
            }
            finish(error && typeof error.errMsg === 'string' && error.errMsg.includes('timeout') ? 'TIMEOUT' : 'UNAVAILABLE')
          }
        }
        task = localTransport ? localTransport.request(nativeOptions) : wxApi.request(nativeOptions)
      } catch (_) { finish('UNAVAILABLE') }
    })
    return promise.then(value => { current(expected); return value })
  }

  function login(): Promise<any> {
    if (loginFlight) return loginFlight.promise
    clearSession()
    const expected = version
    const flight: any = { expected }
    loginFlight = flight
    flight.promise = (async () => {
      const code = localTransport ? null : await loginCode(expected)
      current(expected)
      const result = await request('POST', localTransport ? '/local/pair' : '/v1/auth/wechat',
        localTransport ? { confirmation: 'pair-local-device' } : { code }, null, expected, value => {
        const metadata = sessionMetadata(value)
        if (localTransport && !/^loc_[A-Za-z0-9_-]{1,100}$/.test(metadata.userId)) throw fail('UNVERIFIED')
        if (typeof value.accessToken !== 'string' || !/^[A-Za-z0-9._~-]{24,2048}$/.test(value.accessToken)) throw fail('UNVERIFIED')
        return { ...metadata, accessToken: value.accessToken }
      })
      current(expected)
      session = { userId: result.userId, expiresAt: result.expiresAt, accessToken: result.accessToken }
      return { userId: session.userId, expiresAt: session.expiresAt }
    })().finally(() => { if (loginFlight === flight) loginFlight = null })
    return flight.promise
  }

  function protectedRequest(route: string, validate: (value: any) => any): Promise<any> {
    try {
      const active = activeSession()
      return request('GET', route, undefined, active.accessToken, version, validate)
    } catch (error) { return Promise.reject(error) }
  }
  function motionRequest(method: string, petId: string, suffix = '', body?: any): Promise<any> {
    if (!validId(petId)) return Promise.reject(fail('INVALID_ID'))
    try {
      const active = activeSession()
      // Fixed motion writes include bounded identity/policy checks before and
      // after dispatch. Their deadline is not a retry or proof of physical stop.
      const timeoutMS = method === 'POST' && (suffix === '/execute' || suffix === '/stop') ? 10000 : 5000
      return request(method, '/v1/pets/' + encodeURIComponent(petId) + '/motion' + suffix, body, active.accessToken, version, verifyMotion, timeoutMS)
    } catch (error) { return Promise.reject(error) }
  }
  function localProvisionRequest(route: string, body: any): Promise<any> {
    if (!localTransport) return Promise.reject(fail('CONFIG_INVALID'))
    try {
      const active = activeSession()
      return request('POST', route, body, active.accessToken, version,
        value => localTransport.validateProvision(value, route, body), route.includes('/archive-') ? 20000 : 10000)
    } catch (error) { return Promise.reject(error) }
  }

  return {
    ...(localTransport ? {
      prepareLocalProvision: (requestId: string) => localProvisionRequest('/local/provision/prepare', { requestId, confirmation: 'open-local-provisioning' }),
      currentLocalProvision: () => localProvisionRequest('/local/provision/current', {}),
      resumeLocalProvision: (requestId: string) => localProvisionRequest('/local/provision/resume', { requestId }),
      statusLocalProvision: (requestId: string) => localProvisionRequest('/local/provision/status', { requestId }),
      receiptLocalProvision: (requestId: string) => localProvisionRequest('/local/provision/receipt', { requestId }),
      readLocalProvisionArchive: (requestId: string) => localProvisionRequest('/local/provision/archive-status', { requestId }),
      prepareLocalProvisionArchive: (body: any) => localProvisionRequest('/local/provision/archive-prepare', body),
      confirmLocalProvisionArchive: (body: any) => localProvisionRequest('/local/provision/archive-confirm', body)
    } : {}),
    getHealth() { return request('GET', '/health', undefined, null, version, localTransport ? localTransport.validateHealth : validateHealth) },
    login,
    getSession(): Promise<any> {
      let active: any
      try { active = activeSession() } catch (error) { return Promise.reject(error) }
      const expected = version
      return request('GET', '/v1/session', undefined, active.accessToken, expected, value => {
        const metadata = sessionMetadata(value)
        if (metadata.userId !== active.userId) throw fail('UNVERIFIED')
        return metadata
      }).then(metadata => {
        current(expected)
        session = { ...session, expiresAt: metadata.expiresAt }
        return metadata
      }).catch(error => {
        if (expected === version && error.code === 'UNVERIFIED') clearSession()
        throw error
      })
    },
    // Lifecycle suspension must cancel old callbacks without discarding a valid
    // identity when navigating between product pages. Never expose the bearer.
    cancelPending,
    peekSession() {
      try { return { expiresAt: activeSession().expiresAt } } catch (_) { return null }
    },
    peekIdentity() {
      try { const active = activeSession(); return { userId: active.userId, expiresAt: active.expiresAt } } catch (_) { return null }
    },
    clearSession,
    logout(): Promise<any> {
      const previous = session
      clearSession()
      if (!previous) return Promise.resolve({ loggedOut: true })
      return request('POST', '/v1/auth/logout', undefined, previous.accessToken, version, value => {
        if (!object(value) || value.loggedOut !== true) throw fail('UNVERIFIED')
        return { loggedOut: true }
      })
    },
    listPets() { return protectedRequest('/v1/pets', validatePets) },
    readPetStatus(petId: string) {
      if (!validId(petId)) return Promise.reject(fail('INVALID_ID'))
      return protectedRequest('/v1/pets/' + encodeURIComponent(petId) + '/status', value => verifyReadOnlyStatus({ success: true, data: value }))
    },
    readDashboard(petId: string) {
      if (!validId(petId)) return Promise.reject(fail('INVALID_ID'))
      return protectedRequest('/v1/pets/' + encodeURIComponent(petId) + '/dashboard', verifyDashboard)
    },
    readIntimacy(petId: string) {
      if (!validId(petId)) return Promise.reject(fail('INVALID_ID'))
      return protectedRequest('/v1/pets/' + encodeURIComponent(petId) + '/intimacy', verifyIntimacy)
    },
    readGrowth(petId: string) {
      if (!validId(petId)) return Promise.reject(fail('INVALID_ID'))
      return protectedRequest('/v1/pets/' + encodeURIComponent(petId) + '/growth', verifyGrowth)
    },
    readDialogStatus(petId: string) {
      if (!validId(petId)) return Promise.reject(fail('INVALID_ID'))
      return protectedRequest('/v1/pets/' + encodeURIComponent(petId) + '/dialog', verifyDialogStatus)
    },
    readDialogControl(petId: string) {
      if (!validId(petId)) return Promise.reject(fail('INVALID_ID'))
      return protectedRequest('/v1/pets/' + encodeURIComponent(petId) + '/dialog/control', verifyDialogControl)
    },
    readPreferences(petId: string) {
      if (!validId(petId)) return Promise.reject(fail('INVALID_ID'))
      return protectedRequest('/v1/pets/' + encodeURIComponent(petId) + '/preferences', verifyPreferences)
    },
    readPreferencesReceipt(petId: string, requestId: string) {
      if (!validId(petId) || !preferencesRequestIdValid(requestId)) return Promise.reject(fail('INVALID_ID'))
      return protectedRequest('/v1/pets/' + encodeURIComponent(petId) + '/preferences/requests/' + encodeURIComponent(requestId), value => {
        const receipt = verifyPreferencesReceipt(value)
        if (receipt.requestId !== requestId) throw fail('UNVERIFIED')
        return receipt
      })
    },
    sendPreferencesCommand(petId: string, value: any) {
      if (!validId(petId)) return Promise.reject(fail('INVALID_ID'))
      let command: any
      try { command = verifyPreferencesCommand(value) } catch (_) { return Promise.reject(fail('INVALID_ID')) }
      try {
        const active = activeSession()
        return request('POST', '/v1/pets/' + encodeURIComponent(petId) + '/preferences/commands', command, active.accessToken, version, value => {
          const receipt = verifyPreferencesReceipt(value)
          if (!preferencesReceiptMatchesCommand(receipt, command)) throw fail('UNVERIFIED')
          return receipt
        }, 10000)
      } catch (error) { return Promise.reject(error) }
    },
    readDialogReceipt(petId: string, requestId: string) {
      if (!validId(petId) || !dialogControlRequestIdValid(requestId)) return Promise.reject(fail('INVALID_ID'))
      return protectedRequest('/v1/pets/' + encodeURIComponent(petId) + '/dialog/requests/' + encodeURIComponent(requestId), value => {
        const receipt = verifyDialogReceipt(value)
        if (receipt.requestId !== requestId) throw fail('UNVERIFIED')
        return receipt
      })
    },
    sendDialogCommand(petId: string, value: any) {
      if (!validId(petId)) return Promise.reject(fail('INVALID_ID'))
      let command: any
      try { command = verifyDialogCommand(value) } catch (_) { return Promise.reject(fail('INVALID_ID')) }
      try {
        const active = activeSession()
        return request('POST', '/v1/pets/' + encodeURIComponent(petId) + '/dialog/commands', command, active.accessToken, version, value => {
          const receipt = verifyDialogReceipt(value)
          if (receipt.requestId !== command.requestId || receipt.kind !== command.kind) throw fail('UNVERIFIED')
          return receipt
        }, 10000)
      } catch (error) { return Promise.reject(error) }
    },
    readDialogConversations(petId: string, revision?: string) {
      if (!validId(petId) || revision !== undefined && !dialogRevisionValid(revision)) return Promise.reject(fail('INVALID_ID'))
      return protectedRequest('/v1/pets/' + encodeURIComponent(petId) + '/dialog/conversations' + (revision === undefined ? '' : '/since/' + revision), value => {
        const page = verifyDialogConversations(value)
        if (page.unchanged && page.revision !== revision) throw fail('UNVERIFIED')
        return page
      })
    },
    readDialogConversation(petId: string, conversationId: string, revision?: string) {
      if (!validId(petId) || !conversationIdValid(conversationId) || revision !== undefined && !dialogRevisionValid(revision)) return Promise.reject(fail('INVALID_ID'))
      return protectedRequest('/v1/pets/' + encodeURIComponent(petId) + '/dialog/conversations/' + conversationId + (revision === undefined ? '' : '/since/' + revision), value => {
        const page = verifyDialogConversation(value)
        if (page.conversation.conversationId !== conversationId || page.unchanged && page.revision !== revision) throw fail('UNVERIFIED')
        return page
      })
    },
    recordInteraction(petId: string, eventType: string, requestId: string) {
      if (!validId(petId) || !productEventValid(eventType) || !productRequestIdValid(requestId)) return Promise.reject(fail('INVALID_ID'))
      try {
        const active = activeSession()
        return request('POST', '/v1/pets/' + encodeURIComponent(petId) + '/interactions', { eventType, requestId }, active.accessToken, version, value => {
          const receipt = verifyInteraction(value)
          if (receipt.eventType !== eventType || receipt.requestId !== requestId) throw fail('UNVERIFIED')
          return receipt
        }, 10000)
      } catch (error) { return Promise.reject(error) }
    },
    readMotion(petId: string) { return motionRequest('GET', petId) },
    readMotionHistory(petId: string) {
      if (!validId(petId)) return Promise.reject(fail('INVALID_ID'))
      return protectedRequest('/v1/pets/' + encodeURIComponent(petId) + '/motion/history', verifyMotionHistory)
    },
    executeMotion(petId: string, actionId: string, requestId: string) {
      if (!actionValid(actionId) || !requestIdValid(requestId)) return Promise.reject(fail('INVALID_ID'))
      return motionRequest('POST', petId, '/execute', { actionId, requestId })
    },
    stopMotion(petId: string, requestId: string, executionId?: string) {
      if (!requestIdValid(requestId) || (executionId !== undefined && !validId(executionId))) return Promise.reject(fail('INVALID_ID'))
      return motionRequest('POST', petId, '/stop', { requestId, ...(executionId === undefined ? {} : { executionId }) })
    }
  }
}

function createCloudAuthClient(wxApi: any, options?: any) { return createGatewayClientCore(wxApi, options) }

module.exports = { createCloudAuthClient, cloudAuthErrorMessage, createGatewayClientCore }
