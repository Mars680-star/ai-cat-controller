const { getPetService } = require('../../services/pet-service')
const { batteryText } = require('../../utils/format')
const { redirectOnce, navigateOnce } = require('../../utils/navigation')
const { livePageData, livePageMethods, attachLivePage, detachLivePage } = require('../../utils/live-page')
const { createLiveProduct } = require('../../services/live-product')
const { createRequestId } = require('../../utils/request-id')
const { buildLiveDeviceView } = require('../../utils/live-device-view')
const { attachProductPage, detachProductPage, intimacyView, timeLabel, setChangedData } = require('../../components/live-product-panel/presenter')
const service = getPetService()

Page({
  ...livePageMethods,
  data: { ...livePageData, loading: true, error: '', dashboard: null, batteryLabel: '', chargeLabel: '', networkLabel: '', productLoading: false, productError: '', sampledTime: '' },
  async onShow() {
    this.pageVisible = true
    const visit = this.viewVersion = (this.viewVersion || 0) + 1
    clearInterval(this.statusTimer)
    this.statusTimer = null
    attachLivePage(this)
    attachProductPage(this, 'home', createLiveProduct, createRequestId)
    if (this.data.liveMode) return
    await this.loadDashboard(false)
    this.startPolling(visit)
  },
  startPolling(visit = this.viewVersion) {
    if (this.isCurrentVisit(visit) && !this.data.liveMode && this.data.dashboard && !this.statusTimer) {
      this.statusTimer = setInterval(() => this.loadDashboard(true), 12000)
    }
  },
  onHide() { this.leavePage() },
  onUnload() { this.leavePage() },
  leavePage() {
    this.pageVisible = false
    detachProductPage(this)
    detachLivePage(this)
    this.viewVersion = (this.viewVersion || 0) + 1
    clearInterval(this.statusTimer)
    this.statusTimer = null
  },
  isCurrentVisit(visit: number) { return this.pageVisible && this.viewVersion === visit },
  async onPullDownRefresh() {
    try { await (this.data.liveMode ? this.liveProductRefresh() : this.loadDashboard(true)) } finally { if (this.pageVisible) wx.stopPullDownRefresh() }
  },
  async retry() {
    if (this.data.liveMode) return this.liveProductRefresh()
    const visit = this.viewVersion
    await this.loadDashboard(false)
    this.startPolling(visit)
  },
  loadDashboard(silent = false) {
    if (!this.pageVisible || this.data.liveMode) return Promise.resolve()
    const visit = this.viewVersion
    if (this.dashboardRequest && this.dashboardRequest.visit === visit) return this.dashboardRequest.promise
    if (!silent && !this.data.dashboard) this.setData({ loading: true, error: '' })
    const request: any = { visit }
    this.dashboardRequest = request
    request.promise = (async () => {
      try {
        if (!wx.getStorageSync('aiCatSession')) { this.setData({ loading: false, dashboard: null, error: '' }); return }
        const pet = await service.getBoundPet()
        if (!this.isCurrentVisit(visit)) return
        if (!pet) { this.setData({ loading: false, dashboard: null, error: '' }); return }
        if (!wx.getStorageSync('aiCatBlindBoxConfirmed')) { wx.reLaunch({ url: '/pages/blind-box/index' }); return }
        const dashboard = await service.getDashboard(pet.petId)
        if (!this.isCurrentVisit(visit)) return
        const chargeMap: any = { charging: '正在充电', full: '已充满', discharging: '未充电', not_charging: '未充电', unknown: '暂未获取', unavailable: '暂未获取' }
        const patch = {
          dashboard,
          batteryLabel: batteryText(dashboard.pet.batteryPercent),
          chargeLabel: chargeMap[dashboard.pet.batteryState] || '暂未获取',
          networkLabel: dashboard.pet.connectionState === 'online' ? '良好' : dashboard.pet.connectionState === 'weak' ? '不稳定' : '已断开',
          loading: false,
          error: ''
        }
        const snapshot = JSON.stringify(patch)
        if (snapshot !== this.dashboardSnapshot || this.data.loading || this.data.error) {
          this.setData(patch)
          this.dashboardSnapshot = snapshot
        }
      } catch (_) {
        if (this.isCurrentVisit(visit)) this.setData({ loading: false, error: this.data.dashboard ? '暂时没有更新成功，已保留上次的内容' : '主页暂时加载失败，请重试' })
      }
    })().finally(() => { if (this.dashboardRequest === request) this.dashboardRequest = null })
    return request.promise
  },
  onLiveModeChanged(enabled: boolean) {
    this.viewVersion = (this.viewVersion || 0) + 1
    clearInterval(this.statusTimer)
    this.statusTimer = null
    this.dashboardRequest = null
    this.dashboardSnapshot = ''
    this.setData({ dashboard: null, loading: false, error: '', batteryLabel: '', chargeLabel: '', networkLabel: '', productError: '', productLoading: false, sampledTime: '' })
    if (!enabled && this.pageVisible) {
      const visit = this.viewVersion
      this.loadDashboard(false).then(() => this.startPolling(visit))
    }
  },
  renderLiveProduct(value: any) {
    if (!this.pageVisible || !this.data.liveMode) return
    const snapshot = value.snapshot
    const view = buildLiveDeviceView(this.liveSession.getState(), true)
    const dashboard = snapshot ? {
      pet: { name: snapshot.petName, avatarUrl: '/assets/ai-cat-avatar.png', personalityId: snapshot.personality.personalityId,
        personalityName: snapshot.personality.name, personalityDescription: snapshot.personality.description,
        languageStyle: snapshot.personality.languageStyle, address: snapshot.intimacy.address,
        connectionState: !view.hasSnapshot ? 'unverified' : view.online ? 'online' : 'offline', networkName: '真实连接状态 · 非信号强度' },
      intimacy: intimacyView(snapshot.intimacy),
      today: { interactionCount: snapshot.today.interactionCount, lastInteraction: snapshot.recentInteractions.length ? timeLabel(snapshot.recentInteractions[0].createdAt) : '暂无互动记录' },
      recent: snapshot.recentInteractions.map((item: any, index: number) => ({ id: String(index), title: item.label, type: '亲密度 ' + (item.pointsDelta > 0 ? '+' : '') + item.pointsDelta, time: timeLabel(item.createdAt) }))
    } : null
    setChangedData(this, { dashboard, productLoading: value.loading, productError: value.error, loading: false, error: '',
      batteryLabel: view.batteryLabel, chargeLabel: view.chargeLabel,
      networkLabel: view.hasSnapshot ? view.online ? '在线' : '未连接' : '未验证', sampledTime: snapshot ? timeLabel(snapshot.sampledAt) : '' })
  },
  liveProductRefresh() {
    if (!this.pageVisible) return Promise.resolve()
    return Promise.all([this.liveRefresh(), this.realProduct ? this.realProduct.read() : Promise.resolve()])
  },
  openRealDevice() { if (this.pageVisible) navigateOnce('/pages/device/index') },
  openDevice() { redirectOnce('/pages/device/index') },
  openGrowth() { redirectOnce('/pages/growth/index') }
})
