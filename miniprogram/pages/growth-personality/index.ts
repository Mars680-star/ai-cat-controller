const { getPetService } = require('../../services/pet-service')
const { createRequestId } = require('../../utils/request-id')
const { livePageData, livePageMethods, attachLivePage, detachLivePage } = require('../../utils/live-page')
const { createLiveProduct } = require('../../services/live-product')
const { attachProductPage, detachProductPage, timeLabel, setChangedData } = require('../../components/live-product-panel/presenter')
const service = getPetService()

Page({
  ...livePageMethods,
  data: { ...livePageData, loading: true, error: '', pet: null, growth: null, productLoading: false, productError: '', sampledTime: '', tagHistory: [], debugEvents: ['知识讨论', '提出问题', '情绪分享', '鼓励互动', '轻松玩笑', '计划讨论', '日常闲聊'], debugValues: ['knowledge_discussion', 'question', 'emotional_sharing', 'encouragement', 'joke', 'planning', 'casual_chat'], debugIndex: 0, debugCount: '20', debugSubmitting: false, debugError: '', debugResult: '' },
  async onShow() {
    this.pageVisible = true
    this.viewVersion = (this.viewVersion || 0) + 1
    attachLivePage(this)
    attachProductPage(this, 'personality', createLiveProduct, createRequestId)
    if (!this.data.liveMode) await this.load()
  },
  onHide() {
    this.pageVisible = false
    this.viewVersion = (this.viewVersion || 0) + 1
    detachProductPage(this)
    detachLivePage(this)
  },
  onUnload() { this.onHide() },
  async onPullDownRefresh() {
    try { await (this.data.liveMode ? this.liveProductRefresh() : this.load()) }
    finally { if (this.pageVisible) wx.stopPullDownRefresh() }
  },
  onLiveModeChanged(enabled: boolean) {
    this.viewVersion = (this.viewVersion || 0) + 1
    this.setData({ loading: false, error: '', pet: null, growth: null, productLoading: false, productError: '', sampledTime: '', tagHistory: [], debugSubmitting: false, debugError: '', debugResult: '' })
    if (!enabled && this.pageVisible) this.load()
  },
  async load() {
    if (!this.pageVisible) return
    if (this.data.liveMode) return this.liveProductRefresh()
    const visit = this.viewVersion
    if (this.previewFlight && this.previewFlight.visit === visit) return this.previewFlight.promise
    const flight: any = { visit }
    this.previewFlight = flight
    flight.promise = (async () => {
      try {
        const pet = await service.getBoundPet()
        if (!this.pageVisible || this.data.liveMode || this.viewVersion !== visit) return
        if (!pet) return wx.redirectTo({ url: '/pages/device/index' })
        const growth = await service.getGrowth(pet.petId)
        if (this.pageVisible && !this.data.liveMode && this.viewVersion === visit) setChangedData(this, { pet, growth, loading: false, error: '' })
      } catch (_) { if (this.pageVisible && !this.data.liveMode && this.viewVersion === visit) this.setData({ loading: false, error: '成长人格暂时无法更新' }) }
    })().finally(() => { if (this.previewFlight === flight) this.previewFlight = null })
    return flight.promise
  },
  renderLiveProduct(value: any) {
    if (!this.pageVisible || !this.data.liveMode) return
    const snapshot = value.snapshot, state = this.liveSession.getState()
    const selected = state.pets.find((item: any) => item.petId === state.selectedPetId)
    const labels: any = {}
    if (snapshot) for (const attribute of snapshot.attributes) labels[attribute.attributeId] = attribute.label
    const growth = snapshot ? { enabled: snapshot.enabled, tags: snapshot.activeTags.map((tag: any) => tag.name),
      attributes: snapshot.attributes.map((item: any) => ({ id: item.attributeId, name: item.label, value: item.value, band: item.tendency })),
      portraits: snapshot.behavior.directives,
      recent: snapshot.recentEvents.map((item: any, index: number) => ({ id: String(index), reason: item.label, time: timeLabel(item.createdAt),
        change: item.changedAttributes.length ? '关联维度：' + item.changedAttributes.map((id: string) => labels[id] || id).join('、') : '本条无维度变化',
        topic: item.topic, emotion: item.emotion })), debugValuesVisible: false } : null
    const tagHistory = snapshot ? snapshot.tagHistory.map((item: any, index: number) => ({ ...item, id: String(index), time: timeLabel(item.earnedAt), replacedTime: item.replacedAt ? timeLabel(item.replacedAt) : '' })) : []
    setChangedData(this, { pet: selected && snapshot ? { name: selected.name, personalityName: snapshot.behavior.basePersonalityName } : null,
      growth, tagHistory, loading: false, error: '', productLoading: value.loading, productError: value.error, sampledTime: snapshot ? timeLabel(snapshot.sampledAt) : '',
      debugSubmitting: false, debugError: '', debugResult: '' })
  },
  liveProductRefresh() {
    if (!this.pageVisible) return Promise.resolve()
    return Promise.all([this.liveRefresh(), this.realProduct ? this.realProduct.read() : Promise.resolve()])
  },
  onDebugEvent(event: any) {
    if (this.data.liveMode || !this.pageVisible) return
    const debugIndex = Number(event.detail.value)
    if (Number.isInteger(debugIndex) && debugIndex >= 0 && debugIndex < this.data.debugValues.length) this.setData({ debugIndex, debugError: '', debugResult: '' })
  },
  inputDebugCount(event: any) { if (this.pageVisible && !this.data.liveMode) this.setData({ debugCount: event.detail.value, debugError: '', debugResult: '' }) },
  async generateGrowth() {
    if (!this.pageVisible || this.data.liveMode || this.data.debugSubmitting || !this.data.pet) return
    const visit = this.viewVersion
    const count = Number(this.data.debugCount)
    if (!Number.isInteger(count) || count < 1 || count > 500) {
      this.setData({ debugError: '请输入 1–500 之间的整数', debugResult: '' })
      return
    }
    this.setData({ debugSubmitting: true, debugError: '', debugResult: '' })
    try {
      const result = await service.generateGrowth(this.data.pet.petId, this.data.debugValues[this.data.debugIndex], count, createRequestId('growth-preview'))
      if (!this.pageVisible || this.data.liveMode || this.viewVersion !== visit) return
      this.setData({ growth: { ...result.growth, debugValuesVisible: this.data.growth.debugValuesVisible }, debugResult: `已生成 ${result.processed} 条演示成长事件，分值与标签已更新。` })
      wx.showToast({ title: '成长预览已更新', icon: 'success' })
    } catch (error) { if (this.pageVisible && !this.data.liveMode && this.viewVersion === visit) wx.showToast({ title: '成长事件暂时没有生成成功', icon: 'none' }) }
    finally { if (this.pageVisible && !this.data.liveMode && this.viewVersion === visit) this.setData({ debugSubmitting: false }) }
  }
})
