// Optional, explicit, bounded discovery. An mDNS answer is NEVER authorization.
const { verifyLocalAddress, localRuntimeAvailable } = require('./local-device-client')
const SERVICE = '_aicat-local._tcp.'
function createLocalDeviceDiscovery(wxApi: any) {
  let visible = false, epoch = 0, listener: any = null, timer: any = null, found: any = null, ownsScan = false
  const available = () => localRuntimeAvailable(wxApi) && ['startLocalServiceDiscovery', 'stopLocalServiceDiscovery', 'onLocalServiceFound', 'offLocalServiceFound'].every(name => typeof wxApi[name] === 'function')
  let state: any = { discoveryAvailable: false, discovering: false, candidates: [], discoveryHint: '可手填猫当前 IP；发现结果仅是候选，不会自动配对。' }
  function emit() { if (visible && listener) listener(JSON.parse(JSON.stringify(state))) }
  function stop(message = '') {
    epoch++; clearTimeout(timer); timer = null
    if (found) { try { wxApi.offLocalServiceFound(found) } catch (_) {}; found = null }
    if (ownsScan) { ownsScan = false; try { wxApi.stopLocalServiceDiscovery({ success() {}, fail() {} }) } catch (_) {} }
    state.discovering = false
    if (message) state.discoveryHint = message
    emit()
  }
  function start() {
    if (!visible || state.discovering) return false
    if (!available()) { state.discoveryAvailable = false; state.discoveryHint = '此环境不支持可安全清理的本地发现，请手填猫当前 IP。'; emit(); return false }
    const expected = ++epoch
    state = { ...state, discoveryAvailable: true, discovering: true, candidates: [], discoveryHint: '正在查找，最多30秒；结果只供选择地址，不授予控制权限。' }
    found = (event: any) => {
      if (!visible || expected !== epoch || !state.discovering || !event || event.serviceType !== SERVICE || event.port !== 8789) return
      let address: string
      try { address = verifyLocalAddress(event.ip) } catch (_) { return }
      if (state.candidates.length >= 16 || state.candidates.some((item: any) => item.address === address)) return
      // Deliberately ignore advertised names/TXT: untrusted discovery metadata
      // must never replace the scanned device/card identity or a user decision.
      state.candidates.push({ address, port: 8789 }); emit()
    }
    try {
      wxApi.onLocalServiceFound(found); ownsScan = true
      timer = setTimeout(() => { if (visible && expected === epoch) stop('本次30秒发现已结束。可选择候选地址，或继续手填；仍需机身卡确认。') }, 30000)
      emit()
      wxApi.startLocalServiceDiscovery({ serviceType: SERVICE,
        success() { if (visible && expected === epoch) emit() },
        fail() { if (visible && expected === epoch) stop('本地发现未完成，请手填猫当前 IP；不会自动尝试其它地址。') } })
      return true
    } catch (_) { stop('本地发现不可用，请手填猫当前 IP。'); return false }
  }
  return {
    show(callback: any) { stop(); visible = true; listener = callback; state.discoveryAvailable = available(); emit() },
    start, stop,
    hide() { stop(); visible = false; listener = null; state.candidates = [] },
    getState() { return JSON.parse(JSON.stringify(state)) }
  }
}
module.exports = { createLocalDeviceDiscovery }
