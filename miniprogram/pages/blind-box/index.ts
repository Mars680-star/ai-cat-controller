const { getPetService } = require('../../services/pet-service')
const service = getPetService()

Page({
  data: { pet: null, loading: true, entering: false },
  async onLoad() {
    const pet = await service.getBoundPet()
    if (!pet) return wx.redirectTo({ url: '/pages/device/index' })
    this.setData({ pet, loading: false })
  },
  onUnload() { clearTimeout(this.enterTimer) },
  onHide() { clearTimeout(this.enterTimer) },
  enterHome() {
    if (this.data.entering) return
    this.setData({ entering: true })
    wx.setStorageSync('aiCatBlindBoxConfirmed', true)
    this.enterTimer = setTimeout(() => wx.redirectTo({ url: '/pages/home/index' }), 350)
  }
})
