// Non-secret, explicit temporary development transport. This is NOT a production
// server setting: never accept a URL from input, storage, QR codes or responses.
const LOOPBACK_GATEWAY_ORIGIN = 'http://127.0.0.1:8787'
const PHONE_LAN_GATEWAY_ORIGIN = 'http://192.168.1.110:8788'
const ENABLE_TRUSTED_ANDROID_DEVELOP_LAN = true

function phoneLanEnabled(wxApi: any): boolean {
  if (!ENABLE_TRUSTED_ANDROID_DEVELOP_LAN) return false
  try {
    if (!wxApi || typeof wxApi.getAccountInfoSync !== 'function') return false
    const account = wxApi.getAccountInfoSync()
    if (!account || !account.miniProgram || account.miniProgram.envVersion !== 'develop') return false
    const device = typeof wxApi.getDeviceInfo === 'function' ? wxApi.getDeviceInfo() :
      typeof wxApi.getSystemInfoSync === 'function' ? wxApi.getSystemInfoSync() : null
    return Boolean(device && device.platform === 'android')
  } catch (_) { return false }
}

function developmentGatewayInfo(wxApi: any): any {
  const phoneLan = phoneLanEnabled(wxApi)
  return {
    baseUrl: phoneLan ? PHONE_LAN_GATEWAY_ORIGIN : LOOPBACK_GATEWAY_ORIGIN,
    phoneLan,
    notice: phoneLan ? '手机同网联调 · 本地 HTTP 明文通道，仅用于受信任 Wi-Fi；电脑须开机。不是正式远程控制。' :
      '电脑开发者工具使用本机网关。正式手机接入需服务器与 HTTPS 域名。'
  }
}

module.exports = { LOOPBACK_GATEWAY_ORIGIN, PHONE_LAN_GATEWAY_ORIGIN, phoneLanEnabled, developmentGatewayInfo }
