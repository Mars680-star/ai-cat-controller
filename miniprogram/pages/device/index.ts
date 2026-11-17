const { getPetService } = require('../../services/pet-service')
const { createRequestId } = require('../../utils/request-id')
const { formatDateTime, batteryText } = require('../../utils/format')
const { lengthBetween } = require('../../utils/validators')
const { livePageData, livePageMethods, attachLivePage, detachLivePage } = require('../../utils/live-page')
const service = getPetService()

const BATTERY_STATES: any = {
  charging: '正在充电', discharging: '使用电池', full: '已充满',
  not_charging: '已接电，未充电', unknown: '状态未知', unavailable: '电池不可用'
}

Page({
  ...livePageMethods,
  data: {
    ...livePageData,
    loading: true, error: '', bound: false, pet: null, mockReady: true,
    deviceSerial: 'FOX-DEMO-0001', petName: '小安', networkName: '家庭 Wi-Fi', binding: false,
    batteryLabel: '--', batteryStateLabel: '等待数据', voltageLabel: '电压数据不可用',
    chargerLabel: '未知', batteryPresentLabel: '电池检测状态未知', networkLabel: '未知', lastSeenLabel: '--',
    mockOnline: true, networkOptions: ['在线', '弱网', '离线'],
    networkValues: ['online', 'weak', 'offline'], networkIndex: 0, batteryPercent: 86,
    charging: false, updating: false, reconnecting: false, openingDebug: false, openingGateway: false,
    advancedOpen: false, previewToolsOpen: false, connectionSwitching: false, connectionNotice: ''
  },
  onShow() {
    this.debugNavigation = null
    this.pageVisible = true
    this.viewVersion = (this.viewVersion || 0) + 1
    this.setData({ binding: false, updating: false, reconnecting: false, openingDebug: false, openingGateway: false, connectionSwitching: false })
    attachLivePage(this)
    if (this.data.liveMode) return
    return this.load()
  },
  onHide() { this.leavePage() },
  onUnload() { this.leavePage() },
  leavePage() { this.pageVisible = false; detachLivePage(this); this.viewVersion = (this.viewVersion || 0) + 1; this.debugNavigation = null },
  onLiveModeChanged(enabled: boolean) {
    this.viewVersion = (this.viewVersion || 0) + 1
    this.setData({ pet: null, bound: false, loading: false, error: '', binding: false, updating: false, reconnecting: false })
    if (!enabled && this.pageVisible) this.load()
  },
  isCurrentVisit(visit: number) { return this.pageVisible && this.viewVersion === visit },
  toggleAdvanced() { if (this.pageVisible) this.setData({ advancedOpen: !this.data.advancedOpen }) },
  localPaired() { if (this.pageVisible) this.setData({ connectionNotice: '已连接，可直接进入控制或设置 Wi-Fi。' }) },
  localContinue() {
    if (!this.pageVisible) return
    const state = this.liveSession.getState()
    if (state.authKind === 'local' && state.signedIn && state.selectedPetId) {
      this.openConnectionTool('/pages/home/index', '设备控制页')
    }
  },
  localNetwork() {
    if (!this.pageVisible) return
    const state = this.liveSession.getState()
    if (state.authKind === 'local' && state.signedIn) this.openConnectionTool('/pages/local-network/index', '设置 Wi-Fi')
  },
  localComputer() { if (this.pageVisible) this.setData({ advancedOpen: true, connectionNotice: '已切换电脑连接，请在下方确认微信登录。' }) },
  async useComputerConnection() {
    if (!this.pageVisible || this.data.connectionSwitching) return
    this.setData({ connectionSwitching: true, connectionNotice: '' })
    try {
      await this.liveSession.useComputerGateway()
      if (this.pageVisible && this.liveSession.getState().authKind === 'wechat') await this.liveSession.enable()
    } catch (_) { if (this.pageVisible) this.setData({ connectionNotice: '原连接还有未决请求，暂不能切换；请保留记录并核对原请求。' }) }
    finally { if (this.pageVisible) this.setData({ connectionSwitching: false }) }
  },
  async openPreviewTools() {
    if (!this.pageVisible || this.data.connectionSwitching) return
    if (this.data.previewToolsOpen) { this.setData({ previewToolsOpen: false }); return }
    if (!this.data.liveMode) { this.setData({ previewToolsOpen: true }); return }
    try {
      await this.showPreview()
      if (this.pageVisible && !this.data.liveMode) this.setData({ previewToolsOpen: !this.data.previewToolsOpen })
    } catch (_) { if (this.pageVisible) this.setData({ connectionNotice: '原连接还有未决请求，不能切到预览；请保留记录。' }) }
  },
  openDeviceDebug() {
    this.openConnectionTool('/pages/device-debug/index', '只读联调页')
  },
  openGatewaySetup() {
    this.openConnectionTool('/pages/gateway-setup/index', '网关接入检查页')
  },
  openProvision() {
    this.openConnectionTool('/pages/provision/index', '安卓扫码配网页')
  },
  openLocalPair() { this.openConnectionTool('/pages/local-pair/index', '猫端本地配对页') },
  openConnectionTool(url: string, label: string) {
    if (!this.pageVisible || this.debugNavigation) return
    const visit = this.viewVersion
    const attempt = {}
    this.debugNavigation = attempt
    this.setData({ openingDebug: url === '/pages/device-debug/index', openingGateway: url === '/pages/gateway-setup/index' })
    const failed = () => {
      if (!this.isCurrentVisit(visit) || this.debugNavigation !== attempt) return
      this.debugNavigation = null
      this.setData({ openingDebug: false, openingGateway: false })
      wx.showToast({ title: label + '未打开，请重试', icon: 'none' })
    }
    try { wx.navigateTo({ url, fail: failed }) }
    catch (error) { failed() }
  },
  async onPullDownRefresh() { try { await (this.data.liveMode ? this.liveRefresh() : this.load()) } finally { wx.stopPullDownRefresh() } },
  async load() {
    if (!this.pageVisible || this.data.liveMode) return
    const visit = this.viewVersion
    const request = this.loadVersion = (this.loadVersion || 0) + 1
    const current = () => this.isCurrentVisit(visit) && this.loadVersion === request
    if (!wx.getStorageSync('aiCatSession')) return this.setData({ mockReady: false, loading: false, bound: false, pet: null, error: '' })
    this.setData({ mockReady: true })
    try {
      const boundPet = await service.getBoundPet()
      if (!current()) return
      if (!boundPet) return this.setData({ loading: false, bound: false, pet: null, error: '' })
      const pet = await service.getDeviceDetail(boundPet.petId)
      if (!current()) return
      const networkIndex = Math.max(0, this.data.networkValues.indexOf(pet.connectionState))
      const batteryAvailable = pet.batteryPercent !== null && pet.batteryPercent !== undefined
      const voltageAvailable = pet.batteryVoltage !== null && pet.batteryVoltage !== undefined
      this.setData({
        pet, bound: true, networkIndex, networkLabel: this.data.networkOptions[networkIndex],
        batteryLabel: batteryAvailable ? batteryText(pet.batteryPercent) : '--',
        batteryStateLabel: batteryAvailable ? BATTERY_STATES[pet.batteryState] || '状态未知' : '电池不可用',
        voltageLabel: voltageAvailable ? Number(pet.batteryVoltage).toFixed(2) + ' V' : '电压数据不可用',
        chargerLabel: pet.chargerOnline === null || pet.chargerOnline === undefined ? '未知' : pet.chargerOnline ? '已连接' : '未连接',
        batteryPresentLabel: pet.batteryPresent === null || pet.batteryPresent === undefined ? '电池检测状态未知' : pet.batteryPresent ? '电池已安装' : '未检测到电池',
        lastSeenLabel: formatDateTime(pet.lastSeenAt), mockOnline: pet.connectionState !== 'offline',
        batteryPercent: batteryAvailable ? pet.batteryPercent : 0, charging: pet.batteryState === 'charging',
        loading: false, error: ''
      })
    } catch (error) { if (current()) this.setData({ loading: false, error: '暂时无法获取本地预览状态，请稍后重试' }) }
  },
  openPreviewAuth() { if (this.pageVisible && !this.data.liveMode) wx.reLaunch({ url: '/pages/auth/index' }) },
  inputSerial(event: any) { if (!this.data.binding) this.setData({ deviceSerial: event.detail.value }) },
  inputPetName(event: any) { if (!this.data.binding) this.setData({ petName: event.detail.value }) },
  inputNetworkName(event: any) { if (!this.data.binding) this.setData({ networkName: event.detail.value }) },
  async bindPet() {
    if (!this.pageVisible || this.data.liveMode || this.data.binding) return
    const visit = this.viewVersion
    if (!lengthBetween(this.data.deviceSerial, 4, 64)) return wx.showToast({ title: '设备序列号需要 4–64 个字符', icon: 'none' })
    if (!lengthBetween(this.data.petName, 1, 20)) return wx.showToast({ title: '名字需要 1–20 个字符', icon: 'none' })
    if (!lengthBetween(this.data.networkName, 1, 64)) return wx.showToast({ title: '请输入 Wi-Fi 名称', icon: 'none' })
    this.setData({ binding: true })
    try {
      await service.bindPet({ deviceSerial: this.data.deviceSerial.trim(), petName: this.data.petName.trim(), networkName: this.data.networkName.trim() })
      if (!this.isCurrentVisit(visit)) return
      wx.redirectTo({ url: '/pages/blind-box/index' })
    } catch (error: any) {
      if (this.isCurrentVisit(visit)) wx.showToast({ title: error.message || '绑定预览未完成，请重试', icon: 'none' })
    } finally { if (this.isCurrentVisit(visit)) this.setData({ binding: false }) }
  },
  onOnlineChange(event: any) {
    if (this.data.updating || this.data.reconnecting) return
    const mockOnline = Boolean(event.detail.value)
    this.setData({ mockOnline, networkIndex: mockOnline ? (this.data.networkIndex === 2 ? 0 : this.data.networkIndex) : 2 })
  },
  onNetworkChange(event: any) {
    if (this.data.updating || this.data.reconnecting) return
    const networkIndex = Number(event.detail.value)
    if (!this.data.networkValues[networkIndex]) return
    this.setData({ networkIndex, mockOnline: this.data.networkValues[networkIndex] !== 'offline' })
  },
  onBatteryChange(event: any) { if (!this.data.updating && !this.data.reconnecting) this.setData({ batteryPercent: Number(event.detail.value) }) },
  onChargingChange(event: any) { if (!this.data.updating && !this.data.reconnecting) this.setData({ charging: event.detail.value }) },
  async updateStatus() {
    if (!this.pageVisible || this.data.liveMode || this.data.updating || this.data.reconnecting || !this.data.pet) return
    const visit = this.viewVersion
    this.setData({ updating: true })
    try {
      await service.updateDeviceStatus(this.data.pet.petId, {
        online: this.data.mockOnline, connectionState: this.data.networkValues[this.data.networkIndex],
        batteryPercent: this.data.batteryPercent, charging: this.data.charging
      }, createRequestId('device-status'))
      if (!this.isCurrentVisit(visit)) return
      await this.load()
      if (!this.isCurrentVisit(visit)) return
      wx.showToast({ title: '本地预览已更新', icon: 'success' })
    } catch (error) { if (this.isCurrentVisit(visit)) wx.showToast({ title: '预览状态未更新，请重试', icon: 'none' }) }
    finally { if (this.isCurrentVisit(visit)) this.setData({ updating: false }) }
  },
  async reconnect() {
    if (!this.pageVisible || this.data.liveMode || this.data.reconnecting || this.data.updating || !this.data.pet) return
    const visit = this.viewVersion
    this.setData({ reconnecting: true })
    try {
      await service.reconnectDevice(this.data.pet.petId)
      if (!this.isCurrentVisit(visit)) return
      await this.load()
      if (!this.isCurrentVisit(visit)) return
      wx.showToast({ title: '已演示重新连接', icon: 'success' })
    } catch (error) { if (this.isCurrentVisit(visit)) wx.showToast({ title: '重连预览暂未完成', icon: 'none' }) }
    finally { if (this.isCurrentVisit(visit)) this.setData({ reconnecting: false }) }
  }
})
