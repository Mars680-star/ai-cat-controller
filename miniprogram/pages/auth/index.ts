const { getPetService } = require('../../services/pet-service')
const service = getPetService()

Page({
  data: {
    loginCode: 'aicat-user',
    nickname: '体验用户',
    apiKey: '',
    agreed: false,
    submitting: false
  },
  async onShow() {
    const session = wx.getStorageSync('aiCatSession')
    if (!session) return
    const pet = await service.getBoundPet()
    if (!pet) return wx.redirectTo({ url: '/pages/device/index' })
    if (!wx.getStorageSync('aiCatBlindBoxConfirmed')) return wx.redirectTo({ url: '/pages/blind-box/index' })
    wx.redirectTo({ url: '/pages/home/index' })
  },
  inputLoginCode(event: any) { this.setData({ loginCode: event.detail.value }) },
  inputNickname(event: any) { this.setData({ nickname: event.detail.value }) },
  inputApiKey(event: any) { this.setData({ apiKey: event.detail.value }) },
  onAgreementChange(event: any) { this.setData({ agreed: event.detail.value.includes('agree') }) },
  async login() {
    if (!this.data.agreed) return wx.showToast({ title: '请先阅读并同意隐私与用户协议', icon: 'none' })
    if (this.data.loginCode.trim().length < 3 || !this.data.nickname.trim()) return wx.showToast({ title: '请填写账号标识和用户昵称', icon: 'none' })
    if (this.data.submitting) return
    this.setData({ submitting: true })
    try {
      await service.login({ loginCode: this.data.loginCode.trim(), nickname: this.data.nickname.trim() })
      wx.redirectTo({ url: '/pages/device/index' })
    } catch (error) {
      wx.showToast({ title: '暂时无法登录，请稍后重试', icon: 'none' })
      this.setData({ submitting: false })
    }
  },
  onHide() { this.setData({ apiKey: '' }) },
  onUnload() { this.setData({ apiKey: '' }) },
  showAgreement() { wx.showModal({ title: '界面预览说明', content: '这是本地UI原型，不调用微信登录、不上传表单、不连接设备。所有数值为演示数据。正式接入时再提供完整用户协议。', showCancel: false }) }
})
