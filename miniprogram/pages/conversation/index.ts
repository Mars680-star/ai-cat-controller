const { getPetService } = require('../../services/pet-service')
const { formatDateTime } = require('../../utils/format')
const { beginView, endView, viewActive, patchView, clearDraft } = require('../dialog/view-state')
const { livePageData, livePageMethods, attachLivePage, detachLivePage } = require('../../utils/live-page')
const { createLiveDialog } = require('../../services/live-dialog')
const { detailView } = require('../dialog/live-view')
const service = getPetService()

function timeLabel(value: any) {
  if (!value) return '时间未记录'
  const text = String(value)
  return text.includes('T') && Number.isFinite(Date.parse(text)) ? formatDateTime(text) : text
}

Page({
  ...livePageMethods,
  data: { ...livePageData, loading: true, error: '', pet: null, conversation: null, startedLabel: '', leaving: false,
    detailLive: false, liveDetailLoading: false, liveDetailError: '', liveDetailKnown: false },
  onLoad(options: any) {
    this.conversationId = options.id || 'conv-001'
    this.requestedLive = options.mode === 'live'
    this.requestedPetId = options.petId || ''
    this.realRouteValid = /^conv_[0-9a-f]{48}$/.test(this.conversationId) && /^[A-Za-z0-9_-]{1,128}$/.test(this.requestedPetId)
    this.setData({ detailLive: this.requestedLive })
  },
  async onShow() {
    const epoch = beginView(this)
    if (epoch === null) return
    this.leaving = false
    patchView(this, { leaving: false }, epoch)
    attachLivePage(this)
    if (this.requestedLive && this.liveSession.getState().mode !== 'live') this.liveSession.enable()
    if (this.data.detailLive || this.data.liveMode) { this.attachRealDetail(); return }
    await this.load()
  },
  onHide() { this.pauseRealDetail(false) },
  onUnload() { this.pauseRealDetail(true) },
  pauseRealDetail(disposed: boolean) {
    endView(this, disposed)
    if (this.realDetail) this.realDetail.hide()
    this.detailAttached = false
    detachLivePage(this)
  },
  onLiveModeChanged(enabled: boolean) {
    this.viewEpoch = (this.viewEpoch || 0) + 1
    this.loadSequence = (this.loadSequence || 0) + 1
    patchView(this, { detailLive: Boolean(this.requestedLive || enabled), loading: false, error: '', pet: null, conversation: null,
      startedLabel: '', liveDetailLoading: false, liveDetailError: '', liveDetailKnown: false })
    if (enabled && this.pageVisible) this.attachRealDetail()
  },
  attachRealDetail() {
    if (!this.pageVisible || this.detailAttached) return
    if (!this.realRouteValid) {
      patchView(this, { detailLive: true, loading: false, conversation: null, liveDetailError: '真实记录链接无效，请返回会话列表重新打开。' })
      return
    }
    if (!this.realDetail) this.realDetail = createLiveDialog(this.liveSession, { view: 'conversation', conversationId: this.conversationId, petId: this.requestedPetId })
    this.detailAttached = true
    this.realDetail.show((value: any) => this.renderRealDetail(value))
  },
  renderRealDetail(value: any) {
    const epoch = this.viewEpoch
    if (!viewActive(this, epoch) || !this.data.detailLive) return
    const state = this.liveSession.getState()
    const correctPet = state.selectedPetId === this.requestedPetId
    const selected = state.pets.find((item: any) => item.petId === this.requestedPetId)
    const verified = Boolean(correctPet && state.signedIn && !state.loading && !state.listLoading && selected)
    const raw = verified ? value.conversation : null
    const conversation = raw ? detailView(raw) : null
    patchView(this, { loading: false, error: '', pet: verified ? { name: selected.name, avatarUrl: '/assets/ai-cat-avatar.png' } : null,
      conversation, startedLabel: conversation ? conversation.startedLabel : '', liveDetailKnown: Boolean(conversation),
      liveDetailLoading: value.detailsLoading,
      liveDetailError: !correctPet && state.selectedPetId ? '所选设备已改变，不能把另一台设备的记录显示在此链接中，请返回会话列表。' : value.detailsError || value.error }, epoch)
  },
  liveDetailRefresh() {
    if (!this.pageVisible) return Promise.resolve()
    if (!this.realRouteValid) return this.attachRealDetail()
    this.attachRealDetail()
    return Promise.all([this.liveRefresh(), this.realDetail ? this.realDetail.refresh() : Promise.resolve()])
  },
  showPreview() { return this.goBack() },
  async onPullDownRefresh() {
    const epoch = this.viewEpoch
    try { await this.load() }
    finally { if (viewActive(this, epoch)) wx.stopPullDownRefresh() }
  },
  async load() {
    const epoch = this.viewEpoch
    if (!viewActive(this, epoch)) return
    if (this.data.detailLive || this.data.liveMode) return this.liveDetailRefresh()
    const sequence = this.loadSequence = (this.loadSequence || 0) + 1
    const current = () => viewActive(this, epoch) && this.loadSequence === sequence
    try {
      const pet = await service.getBoundPet()
      if (!current()) return
      if (!pet || !wx.getStorageSync('aiCatSession')) {
        clearDraft()
        wx.redirectTo({ url: pet ? '/pages/auth/index' : '/pages/device/index' })
        return
      }
      const conversation = await service.getConversation(pet.petId, this.conversationId)
      if (!current()) return
      const messages = (conversation.messages || []).map((message: any) => ({ ...message, createdLabel: timeLabel(message.createdAt) }))
      patchView(this, { pet, conversation: { ...conversation, messages }, startedLabel: timeLabel(conversation.startedAt), loading: false, error: '' }, epoch)
    } catch (error) {
      if (current()) patchView(this, { loading: false, error: '暂时无法打开这次会话' }, epoch)
    }
  },
  goBack() {
    const epoch = this.viewEpoch
    if (!viewActive(this, epoch) || this.leaving) return
    this.leaving = true
    patchView(this, { leaving: true }, epoch)
    wx.navigateBack({
      delta: 1,
      fail: () => {
        if (!viewActive(this, epoch)) return
        wx.redirectTo({
          url: '/pages/dialog/index',
          fail: () => {
            if (!viewActive(this, epoch)) return
            this.leaving = false
            patchView(this, { leaving: false }, epoch)
          }
        })
      }
    })
  }
})
