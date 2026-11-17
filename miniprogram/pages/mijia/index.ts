const { getPetService } = require('../../services/pet-service')
const { createRequestId } = require('../../utils/request-id')
const service = getPetService()

Page({
  data: { loading: true, error: '', pet: null, status: null, spec: null, changing: false, removeSheet: false },
  async onShow() { await this.load(false) },
  async onPullDownRefresh() { await this.load(true); wx.stopPullDownRefresh() },
  async load(silent = false) {
    if (!wx.getStorageSync('aiCatSession')) return wx.redirectTo({ url: '/pages/auth/index' })
    if (!silent && !this.data.status) this.setData({ loading: true })
    try {
      const pet = await service.getBoundPet()
      if (!pet) return wx.redirectTo({ url: '/pages/device/index' })
      const [status, spec] = await Promise.all([service.getMijiaStatus(pet.petId), service.getMijiaSpec(pet.petId)])
      this.setData({ pet, status, spec, loading: false, error: '' })
    } catch (error) { this.setData({ loading: false, error: '接入资料预览暂时没有更新，请重试' }) }
  },
  async prepare() {
    if (this.data.changing || !this.data.pet || !this.data.status || this.data.status.status === 'prepared') return
    this.setData({ changing: true })
    try {
      await service.prepareMijia(this.data.pet.petId, createRequestId('mijia-prepare'))
      await this.load(true)
      wx.showToast({ title: '已生成本地预览资料', icon: 'success' })
    } catch (error: any) { wx.showToast({ title: error.message || '预览资料准备失败', icon: 'none' }) }
    finally { this.setData({ changing: false }) }
  },
  showRemove() {
    if (!this.data.changing && this.data.status && this.data.status.status === 'prepared') this.setData({ removeSheet: true })
  },
  hideRemove() { if (!this.data.changing) this.setData({ removeSheet: false }) },
  async remove() {
    if (this.data.changing || !this.data.pet || !this.data.removeSheet) return
    this.setData({ changing: true })
    try {
      await service.removeMijia(this.data.pet.petId, createRequestId('mijia-remove'))
      this.setData({ removeSheet: false })
      await this.load(true)
      wx.showToast({ title: '本地预览资料已清除', icon: 'success' })
    } catch (error: any) { wx.showToast({ title: error.message || '暂时无法清除预览', icon: 'none' }) }
    finally { this.setData({ changing: false }) }
  }
})
