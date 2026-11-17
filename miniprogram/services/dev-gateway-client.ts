// The only network boundary for the isolated, read-only desktop debug page.
const GATEWAY_URL = 'http://127.0.0.1:8765/v1/status'
const SAFE_MESSAGES: any = {
  TOKEN_REQUIRED: '请输入 24–256 位临时联调令牌',
  UNAUTHORIZED: '临时令牌未通过验证，请重新输入后重试',
  TIMEOUT: '本机代理读取超时，请检查代理后重试',
  UNREACHABLE: '暂时无法读取本机代理，请检查代理后重试',
  UNVERIFIED: '返回状态未通过只读实机校验，请检查代理后重试',
  CANCELLED: '读取已取消'
}
const DIALOG_STATES = [
  'starting', 'connecting', 'connected', 'ready', 'waiting_wake', 'wake_detected', 'listening',
  'processing_audio', 'thinking', 'answering', 'followup_preparing', 'followup_listening', 'waiting_follow_up', 'interrupted',
  'proactive_queued', 'proactive_speaking', 'proactive_finishing', 'stopping',
  'echo_guard', 'recovering', 'waking', 'interrupting', 'submitting_text', 'offline', 'unavailable'
]
const IDLE_STATES = ['ready', 'waiting_wake', 'interrupted', 'echo_guard', 'offline', 'unavailable']
const BATTERY_STATES = ['charging', 'discharging', 'full', 'not_charging', 'unknown', 'unavailable']

function gatewayErrorMessage(error: any): string {
  const code = error && error.code
  return typeof code === 'string' && Object.prototype.hasOwnProperty.call(SAFE_MESSAGES, code) ? SAFE_MESSAGES[code] : SAFE_MESSAGES.UNREACHABLE
}
function failure(code: string): any { const error: any = new Error(SAFE_MESSAGES[code]); error.code = code; return error }
function object(value: any) { return value && typeof value === 'object' && !Array.isArray(value) }
function nullableNumber(value: any) { return value === null || (typeof value === 'number' && Number.isFinite(value) && value >= 0) }
function nullableBoolean(value: any) { return value === null || typeof value === 'boolean' }

function verifyStatus(envelope: any): any {
  if (!object(envelope) || envelope.success !== true || !object(envelope.data)) throw failure('UNVERIFIED')
  const { health, device, dialog, sampledAt, readOnly } = envelope.data
  if (!object(health) || !object(device) || !object(dialog) || readOnly !== true || health.status !== 'ok' || health.adapter !== 'local_k1') throw failure('UNVERIFIED')
  if (typeof health.version !== 'string' || health.version.length > 64 || !/^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$/.test(health.version)) throw failure('UNVERIFIED')
  if (typeof sampledAt !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$/.test(sampledAt) || !Number.isFinite(Date.parse(sampledAt))) throw failure('UNVERIFIED')
  if (typeof device.connected !== 'boolean' || device.networkName !== null || device.lastSeenAt !== null) throw failure('UNVERIFIED')
  if (device.deviceSerial !== null && (typeof device.deviceSerial !== 'string' || !/^[0-9A-Za-z._:-]{3}\*{4}[0-9A-Za-z._:-]{4}$/.test(device.deviceSerial))) throw failure('UNVERIFIED')
  if (device.batteryPercent !== null && (!Number.isInteger(device.batteryPercent) || device.batteryPercent < 0 || device.batteryPercent > 100)) throw failure('UNVERIFIED')
  if (!BATTERY_STATES.includes(device.batteryState) || !nullableNumber(device.batteryVoltage) || !nullableBoolean(device.chargerOnline) || !nullableBoolean(device.batteryPresent)) throw failure('UNVERIFIED')
  if (device.batteryState === 'unavailable' && [device.batteryPercent, device.batteryVoltage, device.chargerOnline, device.batteryPresent].some(value => value !== null)) throw failure('UNVERIFIED')
  if (!DIALOG_STATES.includes(dialog.state)) throw failure('UNVERIFIED')
  if (IDLE_STATES.includes(dialog.state) ? dialog.elapsedSeconds !== null : !Number.isInteger(dialog.elapsedSeconds) || dialog.elapsedSeconds < 0 || dialog.elapsedSeconds > 86400) throw failure('UNVERIFIED')
  if (!Number.isInteger(dialog.followUpRemaining) || dialog.followUpRemaining < 0 || dialog.followUpRemaining > 120 || typeof dialog.canInterrupt !== 'boolean' || typeof dialog.sessionActive !== 'boolean' || typeof dialog.stale !== 'boolean') throw failure('UNVERIFIED')
  // Copy only contracted fields. Never forward debug details or arbitrary server text.
  return {
    readOnly: true, sampledAt,
    health: { status: health.status, version: health.version, adapter: health.adapter },
    device: { connected: device.connected, deviceSerial: device.deviceSerial, networkName: null,
      batteryPercent: device.batteryPercent, batteryState: device.batteryState, batteryVoltage: device.batteryVoltage,
      chargerOnline: device.chargerOnline, batteryPresent: device.batteryPresent, lastSeenAt: null },
    dialog: { state: dialog.state, elapsedSeconds: dialog.elapsedSeconds, followUpRemaining: dialog.followUpRemaining,
      canInterrupt: dialog.canInterrupt, sessionActive: dialog.sessionActive, stale: dialog.stale }
  }
}

function createDevGatewayClient(wxApi: any) {
  return {
    readStatus(token: string) {
      let task: any = null
      let settled = false
      let cancel: any = null
      const promise = new Promise((resolve, reject) => {
        const fail = (code: string) => { if (!settled) { settled = true; reject(failure(code)) } }
        cancel = () => fail('CANCELLED')
        if (typeof token !== 'string' || !/^[A-Za-z0-9._~-]{24,256}$/.test(token)) { fail('TOKEN_REQUIRED'); return }
        try {
          task = wxApi.request({
            url: GATEWAY_URL, method: 'GET', timeout: 5000,
            header: { Authorization: 'Bearer ' + token },
            success(response: any) {
              if (settled) return
              if (response.statusCode === 401 || response.statusCode === 403) { fail('UNAUTHORIZED'); return }
              if (response.statusCode !== 200) { fail('UNREACHABLE'); return }
              try { const status = verifyStatus(response.data); settled = true; resolve(status) }
              catch (_) { fail('UNVERIFIED') }
            },
            fail(error: any) { fail(error && typeof error.errMsg === 'string' && error.errMsg.includes('timeout') ? 'TIMEOUT' : 'UNREACHABLE') }
          })
        } catch (_) { fail('UNREACHABLE') }
      })
      return {
        promise,
        abort() {
          if (settled) return
          cancel()
          try { if (task && typeof task.abort === 'function') task.abort() } catch (_) {}
          task = null
        }
      }
    }
  }
}

module.exports = { createDevGatewayClient, gatewayErrorMessage, verifyReadOnlyStatus: verifyStatus }
