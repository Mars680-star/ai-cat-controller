const { getPetService } = require('../../services/pet-service')
const { createRequestId } = require('../../utils/request-id')
const { livePageData, livePageMethods, attachLivePage, detachLivePage } = require('../../utils/live-page')
const { createLiveProduct } = require('../../services/live-product')
const { attachProductPage, detachProductPage, intimacyView, timeLabel, contextKey, setChangedData } = require('../../components/live-product-panel/presenter')
const service = getPetService()
const INTERACTIONS: any = { daily_check_in: '每日见面', touch: '摸摸它', completed_task: '一起完成任务', ignored_greeting: '错过问候' }

Page({
  ...livePageMethods,
  data: { ...livePageData, loading: true, error: '', pet: null, intimacy: null, submittingEvent: '', celebration: false,
    productLoading: false, productError: '', sampledTime: '', interactionsEnabled: false, interactionBlocked: true,
    interactionConfirm: false, interactionNotice: '', interactionError: '', interactionUncertain: false, interactionRetry: false, liveRules: {} },
  async onShow() {
    this.pageVisible = true
    this.interactionTarget = null
    setChangedData(this, { interactionConfirm: false })
    this.viewVersion = (this.viewVersion || 0) + 1
    attachLivePage(this)
    attachProductPage(this, 'growth', createLiveProduct, createRequestId)
    if (!this.data.liveMode) await this.load(false)
  },
  async onPullDownRefresh() {
    try { await (this.data.liveMode ? this.liveProductRefresh() : this.load(true)) }
    finally { if (this.pageVisible) wx.stopPullDownRefresh() }
  },
  onHide() {
    this.pageVisible = false
    this.viewVersion = (this.viewVersion || 0) + 1
    clearTimeout(this.celebrationTimer)
    detachProductPage(this)
    detachLivePage(this)
    this.interactionTarget = null
  },
  onUnload() { this.onHide() },
  onLiveModeChanged(enabled: boolean) {
    this.viewVersion = (this.viewVersion || 0) + 1
    this.interactionTarget = null
    clearTimeout(this.celebrationTimer)
    this.setData({ loading: false, error: '', pet: null, intimacy: null, submittingEvent: '', celebration: false,
      productError: '', productLoading: false, interactionConfirm: false, interactionNotice: '', interactionError: '', interactionUncertain: false, interactionRetry: false, interactionBlocked: true })
    if (!enabled && this.pageVisible) this.load(false)
  },
  async load(silent = false) {
    if (!this.pageVisible) return
    if (this.data.liveMode) return this.liveProductRefresh()
    const visit = this.viewVersion
    if (this.previewFlight && this.previewFlight.visit === visit) return this.previewFlight.promise
    if (!silent && !this.data.intimacy) this.setData({ loading: true })
    const flight: any = { visit }
    this.previewFlight = flight
    flight.promise = (async () => {
      try {
        const pet = await service.getBoundPet()
        if (!this.pageVisible || this.viewVersion !== visit || this.data.liveMode) return
        if (!pet) return wx.redirectTo({ url: '/pages/device/index' })
        const intimacy = await service.getIntimacy(pet.petId)
        if (this.pageVisible && this.viewVersion === visit && !this.data.liveMode) setChangedData(this, { pet, intimacy, loading: false, error: '' })
      } catch (_) {
        if (this.pageVisible && this.viewVersion === visit && !this.data.liveMode) this.setData({ loading: false, error: '培养记录暂时没有更新，已保留上次内容' })
      }
    })().finally(() => { if (this.previewFlight === flight) this.previewFlight = null })
    return flight.promise
  },
  renderLiveProduct(value: any) {
    if (!this.pageVisible || !this.data.liveMode) return
    const state = this.liveSession.getState(), snapshot = value.snapshot
    const selected = state.pets.find((item: any) => item.petId === state.selectedPetId)
    const liveRules: any = {}
    if (snapshot) for (const rule of snapshot.rules) liveRules[rule.eventType] = { ...rule, pointsText: (rule.points > 0 ? '+' : '') + rule.points + ' 亲密度', limitText: rule.maxPerDay === null ? '次数与每日上限由服务端校验' : '每日最多 ' + rule.maxPerDay + ' 次，服务端校验' }
    const intimacy = snapshot ? { ...intimacyView(snapshot.intimacy), dailyGrowth: null, dailyCap: snapshot.dailyGrowthCap,
      today: { met: snapshot.dailyCheckInCompleted, taskCompleted: null }, recent: snapshot.history.map((item: any, index: number) => ({ id: String(index), title: item.label, time: timeLabel(item.createdAt), value: item.pointsDelta })) } : null
    const result = value.result
    const notice = result ? result.status === 'recorded' ? (result.duplicate ? '已找到这条互动的原记录，未重复计分。' : '互动已记录，亲密度变化 ' + (result.pointsDelta > 0 ? '+' : '') + result.pointsDelta + '。') + (result.celebrationScheduled ? ' 原有升级庆祝可能触发，请留意设备。' : '') : result.status === 'unknown' ? '提交结果暂不确定，请先刷新检查；若重试，仅查询或重放同一请求，不会创建新互动。' : result.status === 'sending' ? '正在记录真实互动，请勿重复点击。' : '这条互动没有记录成功。' : ''
    const modalNotice = this.interactionModalNotice
    const modalError = modalNotice && modalNotice.generation === this.confirmGeneration && modalNotice.key === contextKey(state) ? modalNotice.text : ''
    setChangedData(this, { pet: selected ? { name: selected.name, personalityName: '真实培养记录' } : null, intimacy,
      loading: false, error: '', productLoading: value.loading, productError: value.error, sampledTime: snapshot ? timeLabel(snapshot.sampledAt) : '',
      liveRules, interactionsEnabled: Boolean(snapshot && snapshot.interactionsEnabled),
      interactionBlocked: Boolean(!snapshot || !snapshot.interactionsEnabled || value.submitting || value.uncertain || value.loading || state.loading || state.listLoading),
      submittingEvent: value.submitting ? this.activeInteraction || 'pending' : '', interactionNotice: notice,
      interactionError: [modalError, value.interactionError].filter(Boolean).join(' '), interactionUncertain: value.uncertain,
      interactionRetry: Boolean(value.retryAvailable && !value.submitting && snapshot && snapshot.interactionsEnabled && !value.loading && !state.loading && !state.listLoading) })
  },
  liveProductRefresh() {
    if (!this.pageVisible) return Promise.resolve()
    return Promise.all([this.liveRefresh(), this.realProduct ? this.realProduct.read() : Promise.resolve()])
  },
  async confirmRealInteraction(eventType: string, retry = false) {
    if (!this.pageVisible || !this.data.liveMode || this.data.interactionConfirm || !this.realProduct) return
    const value = this.realProduct.getState(), state = this.liveSession.getState()
    if (!state.signedIn || !state.selectedPetId || state.loading || state.listLoading || !value.snapshot || !value.snapshot.interactionsEnabled || value.submitting || (!retry && (value.uncertain || this.data.interactionBlocked)) || (retry && !value.retryAvailable)) return
    if (!retry && eventType === 'daily_check_in' && value.snapshot.dailyCheckInCompleted) return
    const target = { key: contextKey(state), generation: this.confirmGeneration, eventType, retry }
    this.interactionModalNotice = null
    this.interactionTarget = target
    this.setData({ interactionConfirm: true, interactionError: value.interactionError })
    return new Promise<void>(resolve => {
      const currentTarget = () => this.pageVisible && this.data.liveMode && this.interactionTarget === target && this.confirmGeneration === target.generation && contextKey(this.liveSession.getState()) === target.key
      const modalFailed = () => {
        if (currentTarget()) {
          this.interactionTarget = null
          this.interactionModalNotice = { key: target.key, generation: target.generation,
            text: retry ? '本次核对请求未发送：确认弹窗打开失败；原互动仍需核对，不会创建新互动。' : '互动未发送：确认弹窗打开失败，请重新点击操作。' }
          this.setData({ interactionConfirm: false })
          this.renderLiveProduct(this.realProduct.getState())
        }
        resolve()
      }
      const modal = {
        title: retry ? '检查或重试同一条互动' : '记录真实互动：' + INTERACTIONS[eventType],
        content: retry ? '仅使用原请求编号检查或重试，不会创建另一条互动。若结果仍不确定，请勿再次新建。' : '这会写入真实亲密度与成长记录，每日限制由服务端决定。亲密度升级时，原有系统可能触发庆祝动作；请保持设备周围安全。',
        confirmText: retry ? '同号核对' : '确认记录',
        success: async (answer: any) => {
          // Native callbacks can outlive a visit or a later confirmation.
          if (!currentTarget()) { resolve(); return }
          try {
            if (!answer || answer.confirm !== true) return
            const current = this.realProduct.getState()
            if (!current.snapshot || !current.snapshot.interactionsEnabled || current.submitting || (!retry && current.uncertain)) return
            this.activeInteraction = eventType
            this.interactionTarget = null
            this.setData({ interactionConfirm: false })
            await (retry ? this.realProduct.retryInteraction() : this.realProduct.interact(eventType))
          } finally {
            if (currentTarget()) { this.interactionTarget = null; this.setData({ interactionConfirm: false }) }
            resolve()
          }
        },
        fail: modalFailed
      }
      try { wx.showModal(modal) } catch (_) { modalFailed() }
    })
  },
  retryInteraction() { if (this.pageVisible && this.data.liveMode && this.data.interactionRetry) return this.confirmRealInteraction(this.activeInteraction || '', true) },
  async addInteraction(event: any) {
    const eventType = event && event.currentTarget && event.currentTarget.dataset.event
    if (!Object.prototype.hasOwnProperty.call(INTERACTIONS, eventType)) return
    if (this.data.liveMode) return this.confirmRealInteraction(eventType)
    if (!this.pageVisible || this.data.submittingEvent || !this.data.pet || !this.data.intimacy) return
    if (eventType === 'daily_check_in' && this.data.intimacy.today.met) return
    if (eventType === 'completed_task' && this.data.intimacy.today.taskCompleted) return
    const visit = this.viewVersion
    this.setData({ submittingEvent: eventType })
    try {
      const result = await service.recordInteraction(this.data.pet.petId, eventType, createRequestId(`interaction-${eventType}`))
      if (!this.pageVisible || this.data.liveMode || this.viewVersion !== visit) return
      await this.load(true)
      if (!this.pageVisible || this.data.liveMode || this.viewVersion !== visit) return
      wx.showToast({ title: result.duplicate ? result.reason : result.pointsAdded === 0 ? '本次亲密度未变化' : `亲密度 ${result.pointsAdded > 0 ? '+' : ''}${result.pointsAdded}`, icon: 'none' })
      if (result.upgraded) { this.setData({ celebration: true }); this.celebrationTimer = setTimeout(() => { if (this.pageVisible && !this.data.liveMode) this.setData({ celebration: false }) }, 1800) }
    } catch (error: any) { if (this.pageVisible && !this.data.liveMode && this.viewVersion === visit) wx.showToast({ title: error.message || '这次互动还没有记录成功', icon: 'none' }) }
    finally { if (this.pageVisible && !this.data.liveMode && this.viewVersion === visit) this.setData({ submittingEvent: '' }) }
  }
})
