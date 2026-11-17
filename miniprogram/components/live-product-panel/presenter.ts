// Shared page presentation helpers. No authentication, network or mutations.
const ACTION_LABELS: any = {
  head_shake: '轻轻摇头', head_nod: '认真点头', tail_wag: '开心摇尾', proud_pose: '骄傲转身',
  quiet_companion: '安静陪伴', greeting_combo: '见面问候', celebration_combo: '升级庆祝'
}
const timeLabel = (value: string) => value ? value.replace('T', ' ').replace(/Z$/, ' UTC') : '暂无记录'
const unlockLabels = (items: string[]) => items.map(item => ACTION_LABELS[item] || item)
function setChangedData(page: any, values: any) {
  const patch: any = {}
  for (const name of Object.keys(values)) {
    if (JSON.stringify(page.data[name]) !== JSON.stringify(values[name])) patch[name] = values[name]
  }
  if (Object.keys(patch).length) page.setData(patch)
}
function contextKey(state: any) {
  return JSON.stringify([state.mode, state.signedIn, state.selectedPetId, state.expiresAt, state.loading, state.listLoading])
}
function intimacyView(value: any) {
  return { ...value, badge: 'Lv.' + value.level, nextLevelPoints: value.nextPoints,
    currentUnlocks: unlockLabels(value.currentUnlocks), nextUnlocks: unlockLabels(value.nextUnlocks) }
}
function attachProductPage(page: any, view: string, factory: any, requestId: any) {
  if (!page.realProduct) page.realProduct = factory(page.liveSession, view, requestId)
  page.realProduct.show((value: any) => { if (page.pageVisible && page.data.liveMode) page.renderLiveProduct(value) })
  if (page.productDetach) page.productDetach()
  page.productDetach = page.liveSession.attach((state: any) => {
    if (!page.pageVisible) return
    const key = contextKey(state)
    if (page.productContext !== key) {
      page.productContext = key; page.confirmGeneration = (page.confirmGeneration || 0) + 1
      if (page.interactionTarget) { page.interactionTarget = null; setChangedData(page, { interactionConfirm: false }) }
    }
    if (page.data.liveMode) page.renderLiveProduct(page.realProduct.getState())
  })
}
function detachProductPage(page: any) {
  if (page.realProduct) page.realProduct.hide()
  if (page.productDetach) page.productDetach()
  page.productDetach = null
  page.confirmGeneration = (page.confirmGeneration || 0) + 1
}
module.exports = { timeLabel, setChangedData, intimacyView, contextKey, attachProductPage, detachProductPage }
