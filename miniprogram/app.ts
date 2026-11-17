const { getPetService } = require('./services/pet-service')
const { getLiveDeviceSession } = require('./services/live-device-session')
const { clearLocalProvisionHandoff } = require('./services/local-network')

App({
  globalData: {
    appName: 'AI 狐狸伴侣',
    uiPreviewOnly: true
  },
  onHide() { try { clearLocalProvisionHandoff() } finally { getLiveDeviceSession().clear() } },
  onLaunch() {
    const service: any = getPetService()
    if (service.prepareDevelopmentScenario) service.prepareDevelopmentScenario()
    const pet = wx.getStorageSync('aiCatPet')
    if (pet && pet.name === '团子') {
      wx.setStorageSync('aiCatPet', { ...pet, name: '小安' })
    }
  }
})
