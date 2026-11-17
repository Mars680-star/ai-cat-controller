// Reuse the established provisioning core's read-only ledger validation.
// Never reinterpret its schema, repair/erase storage, or start provisioning.
const { createLiveProvisioning } = require('./live-provisioning')
const faultedApis = new WeakSet<object>()
const FAILURE = '未决配网记录不可验证，禁止切换或新建窗口；请保留原记录，不能清数据绕过。'

function inspectLegacyProvisionPending(wxApi: any) {
  if (!wxApi || typeof wxApi !== 'object' && typeof wxApi !== 'function' || faultedApis.has(wxApi)) throw new Error(FAILURE)
  let controller: any = null, failed = false, result: any = null
  try {
    controller = createLiveProvisioning(wxApi)
    controller.show(() => {})
    const state = controller.getState()
    if (state.error) throw new Error(FAILURE)
    result = { pending: state.pending, reviewIdentity: state.reviewIdentity }
  } catch (_) { failed = true }
  finally {
    try { if (controller) controller.hide() } catch (_) { failed = true }
  }
  if (failed) { faultedApis.add(wxApi); throw new Error(FAILURE) }
  return result
}

module.exports = { inspectLegacyProvisionPending }
