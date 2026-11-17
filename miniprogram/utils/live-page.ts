// Product-page integration only; authentication and polling stay in the shared closure.
const { getLiveDeviceSession } = require('../services/live-device-session')
const { buildLiveDeviceView } = require('./live-device-view')

const livePageData = {
  liveMode: false, liveOnline: false, liveStatusLabel: '尚未验证',
  liveState: { mode: 'preview', signedIn: false, pets: [], selectedPetId: '', snapshot: null }
}

function attachLivePage(page: any) {
  detachLivePage(page)
  const session = getLiveDeviceSession()
  page.liveSession = session
  page.liveDetach = session.attach((state: any) => {
    if (!page.pageVisible) return
    const liveMode = state.mode === 'live'
    const view = buildLiveDeviceView(state, true)
    const liveOnline = view.online
    const liveStatusLabel = !view.signedIn ? '尚未验证' : !view.hasSnapshot ? '等待真实状态' : liveOnline ? '设备在线' : '设备离线'
    const patch = { liveMode, liveOnline, liveStatusLabel, liveState: state }
    const signature = JSON.stringify(patch)
    const changedMode = page.data.liveMode !== liveMode
    if (signature !== page.liveRenderSignature) {
      page.setData(patch)
      page.liveRenderSignature = signature
    }
    if (changedMode) page.onLiveModeChanged(liveMode)
  })
}

function detachLivePage(page: any) {
  if (page.liveDetach) page.liveDetach()
  page.liveDetach = null
  page.liveRenderSignature = ''
}

const livePageMethods = {
  useRealDevice() { if (this.pageVisible) return this.liveSession.enable() },
  liveLogin() {
    if (!this.pageVisible) return
    if (this.liveSession.getState().authKind === 'local') { wx.redirectTo({ url: '/pages/device/index' }); return }
    return this.liveSession.login()
  },
  liveList() { if (this.pageVisible) return this.liveSession.refreshPets() },
  liveSelect(event: any) { if (this.pageVisible) return this.liveSession.selectPet(event.detail.petId) },
  liveLogout() { if (this.pageVisible) return this.liveSession.logout() },
  showPreview() { if (this.pageVisible) return this.liveSession.preview() },
  liveRefresh() {
    if (!this.pageVisible) return Promise.resolve()
    const state = this.liveSession.getState()
    return !state.signedIn ? this.liveSession.checkHealth() :
      state.selectedPetId ? this.liveSession.refresh() : this.liveSession.refreshPets()
  }
}

module.exports = { livePageData, livePageMethods, attachLivePage, detachLivePage }
