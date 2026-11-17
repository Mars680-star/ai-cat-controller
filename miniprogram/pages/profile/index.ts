const { getPetService } = require('../../services/pet-service')
const { createRequestId } = require('../../utils/request-id')
const { lengthBetween } = require('../../utils/validators')
const { formatDateTime } = require('../../utils/format')
const { clearDraft, draftFor } = require('../dialog/view-state')
const { livePageData, livePageMethods, attachLivePage, detachLivePage } = require('../../utils/live-page')
const { createLivePreferences } = require('../../services/live-preferences')
const service = getPetService()

const PREVIEW_FIELDS = ['pet', 'session', 'name', 'volume', 'followUpSeconds', 'settingsDirty', 'memoryNotice', 'feedbackIndex', 'feedbackContent', 'feedbackRemaining']
const STEP_NAMES: any = { pet_settings: '名字与音量', follow_up: '连续聊天时间', feedback: '设备本地反馈' }
const STEP_STATUS: any = { pending: '尚待处理', sending: '正在派发，结果未确认', applied: '已应用', not_sent: '已确认未派发', unknown: '结果未知，勿重复提交' }
function receiptView(receipt: any, kind: string) {
  if (!receipt) return { message: '', steps: [] }
  const labels: any = { sending: '设备正在处理，只能核对原回执，请勿重复提交。',
    recorded: kind === 'feedback' ? '反馈已保存在设备本地，不代表已上传云端或客服已收到。' : '本次设置步骤已确认应用，请以刷新后的实际读数为准。',
    partially_applied: '仅部分设置已应用，未完成步骤不会自动补发。请先刷新核对，再针对剩余差异重新确认；不能重放整份旧设置。',
    not_sent: '已确认本次未派发；如需修改，请核对最新资料后重新确认。', unknown: '提交结果未知，请保留原编号并核对回执，不能换编号重发。' }
  return { message: labels[receipt.status] || '回执尚未验证', steps: receipt.steps.map((item: any) => ({ name: STEP_NAMES[item.step], status: STEP_STATUS[item.status] })) }
}
function permissionKey(value: any) {
  return value ? JSON.stringify([value.verified, value.settingsEnabled, value.feedbackEnabled, value.canChangeName, value.canChangeVolume, value.canChangeFollowUp, value.canSubmitFeedback, value.settingsReviewRequired, value.feedbackReviewRequired, value.outputVolumeAvailable,
    value.name, value.storedVolume, value.outputVolume, value.outputMuted, value.followUpSeconds, value.followUpSource]) : ''
}
function draftKey(value: any, kind: string) { return kind === 'settings' ? value.settingsVersion : value.feedbackVersion }

const DANGER: any = {
  unbind: {
    title: '解除本地演示绑定？',
    description: '仅操作本地预览，解绑后回到连接页。设备性格与成长保留，原用户历史不会交给新用户；不会解绑或更改真实设备。',
    confirmText: '解除演示绑定'
  },
  memory: {
    title: '演示清除全部记忆？',
    description: '仅演示清除短期原文和长期结构化记忆，完整对话记录仍保留。之后的新对话会重新形成记忆；真实设备记忆不受影响。',
    confirmText: '清除演示记忆'
  },
  reset: {
    title: '重置全部本地预览？',
    description: '演示重置账号、绑定、性格、亲密度、记忆、动作、对话与反馈。仅本地预览，不影响真实设备、云端授权或设备设置。恢复编号也仅作流程展示。',
    confirmText: '重置本地预览'
  }
}

Page({
  ...livePageMethods,
  data: {
    ...livePageData, loading: true, error: '', pet: null, session: null, name: '', volume: 62, followUpSeconds: 30,
    apiKey: '', saving: false, settingsDirty: false, memoryNotice: '',
    feedbackTypes: ['设备', '对话', '动作', '账号', '其他'],
    feedbackValues: ['device', 'dialog', 'motion', 'account', 'other'],
    feedbackIndex: 0, feedbackContent: '', feedbackRemaining: 500, feedbackSubmitting: false,
    dangerKind: '', danger: null, processingDanger: false,
    preferencesLoading: false, preferencesError: '', preferencesStatus: '真实设置尚未验证', preferencesReason: '', preferencesKnown: false,
    realOutputLabel: '未知', realMutedLabel: '未提供', realStoredLabel: '未知', realFollowSource: '未知',
    canChangeName: false, canChangeVolume: false, canChangeFollowUp: false, canSaveReal: false, canFeedbackReal: false,
    settingsDraftError: '', feedbackDraftError: '', preferencesConfirm: false, settingsMessage: '', feedbackMessage: '',
    settingsSteps: [], feedbackSteps: [], settingsReceiptStatus: '', feedbackReceiptStatus: '',
    canCheckSettings: false, canCheckFeedback: false, settingsChecking: false, feedbackChecking: false,
    realSettingsBlocked: false, realFeedbackBlocked: false
  },
  onShow() {
    this.pageVisible = true
    this.viewVersion = (this.viewVersion || 0) + 1
    this.setData({ saving: false, feedbackSubmitting: false, processingDanger: false, dangerKind: '', danger: null })
    attachLivePage(this)
    if (!this.realPreferences) this.realPreferences = createLivePreferences(this.liveSession, createRequestId, wx)
    this.realPreferences.show((value: any) => { if (this.pageVisible && this.data.liveMode) this.renderPreferences(value) })
    if (this.data.liveMode) return
    return this.load()
  },
  onHide() { this.leavePage() },
  onUnload() { this.leavePage() },
  leavePage() {
    if (this.preferenceConfirmation || this.preparingPreferences) this.preferenceGuardNotice = '请求未发送：离开页面已撤销确认，返回后需重新保存并确认。'
    this.preferenceConfirmation = null
    if (this.realPreferences) this.realPreferences.hide()
    detachLivePage(this)
    this.pageVisible = false
    this.viewVersion = (this.viewVersion || 0) + 1
    if (this.data.apiKey) this.setData({ apiKey: '' })
  },
  isCurrentVisit(visit: number) { return this.pageVisible && this.viewVersion === visit },
  async onPullDownRefresh() { try { await (this.data.liveMode ? this.refreshPreferences() : this.load()) } finally { if (this.pageVisible) wx.stopPullDownRefresh() } },
  onLiveModeChanged(liveMode: boolean) {
    this.viewVersion = (this.viewVersion || 0) + 1
    this.loadVersion = (this.loadVersion || 0) + 1
    if (this.preferenceConfirmation || this.preparingPreferences) this.preferenceGuardNotice = '请求未发送：登录或模式已变化，请重新选择设备并确认。'
    const confirm = this.preferenceConfirmation
    this.preferenceConfirmation = null
    if (confirm && this.realPreferences) this.realPreferences.cancel(confirm.token)
    if (liveMode) {
      this.previewForm = Object.fromEntries(PREVIEW_FIELDS.map(key => [key, this.data[key]]))
      this.setData({ pet: null, session: null, name: null, volume: null, followUpSeconds: null, settingsDirty: false,
        feedbackIndex: 0, feedbackContent: '', feedbackRemaining: 500, memoryNotice: '' })
    } else if (this.previewForm) this.setData(this.previewForm)
    this.preferencesRenderSignature = ''
    this.setData({ loading: false, error: '', apiKey: '', saving: false, feedbackSubmitting: false,
      dangerKind: '', danger: null, processingDanger: false, preferencesConfirm: false })
  },
  async showPreview() {
    if (!this.pageVisible) return
    await this.liveSession.preview()
    if (this.pageVisible && !this.data.liveMode) return this.load()
  },
  refreshPreferences() {
    if (!this.pageVisible || !this.data.liveMode) return Promise.resolve()
    if (!this.preferenceConfirmation && !this.preparingPreferences) this.preferenceGuardNotice = ''
    return Promise.all([this.liveRefresh(), this.realPreferences ? this.realPreferences.refresh() : Promise.resolve()])
  },
  renderPreferences(value: any) {
    if (!this.pageVisible || !this.data.liveMode) return
    const snapshot = value.snapshot, known = Boolean(snapshot && snapshot.verified)
    const settingsDraft = value.settingsDraft, feedbackDraft = value.feedbackDraft
    const confirm = this.preferenceConfirmation
    if (confirm && (!value.confirmPending || confirm.permissions !== permissionKey(snapshot) || confirm.version !== draftKey(value, confirm.kind))) {
      this.preferenceConfirmation = null
      this.preferenceGuardNotice = '请求未发送：确认期间草稿、登录、设备或保存权限已变化，请重新检查并确认。'
      if (value.confirmPending) this.realPreferences.cancel(confirm.token)
    }
    const state = this.liveSession.getState(), selected = state.pets.find((item: any) => item.petId === state.selectedPetId)
    const settingsReceipt = receiptView(value.settingsReceipt, 'settings'), feedbackReceipt = receiptView(value.feedbackReceipt, 'feedback')
    const settingsFailure = value.settingsError || '', feedbackFailure = value.feedbackError || ''
    const isConfirm = Boolean(this.preferenceConfirmation)
    const unverifiedReason = !state.signedIn ? state.authKind === 'local' ? '请先扫描机身卡完成本地配对并选择设备。' : '请先完成微信登录并选择已授权设备。'
      : !state.selectedPetId ? '请选择已授权设备，再刷新真实设置与实际读数。'
      : value.loading ? '正在核对设置与反馈权限，请稍候；核验不会更改设备。'
      : value.error ? '真实设置读取失败，请刷新后重试；不会使用演示值或自动提交。'
      : '尚未取得当前设备的设置权限，请刷新真实设置与实际读数。'
    const patch: any = { loading: false, error: '', pet: selected ? { petId: selected.petId, name: selected.name, personalityName: '真实设备设置' } : null, session: null,
      name: settingsDraft.name, volume: settingsDraft.volume, followUpSeconds: settingsDraft.followUpSeconds, settingsDirty: value.settingsDirty,
      feedbackIndex: Math.max(0, this.data.feedbackValues.indexOf(feedbackDraft.category)), feedbackContent: feedbackDraft.content,
      feedbackRemaining: 500 - Array.from(feedbackDraft.content).length, apiKey: '', memoryNotice: '',
      preferencesKnown: known, preferencesLoading: value.loading, preferencesError: this.preferenceGuardNotice || value.error || value.storageError,
      preferencesStatus: value.loading ? '正在核对设置与反馈权限' : !snapshot ? '真实设置尚未验证' : !snapshot.verified ? '身份或接口未验证，禁止保存' : !snapshot.settingsEnabled && !snapshot.feedbackEnabled ? '真实设置与反馈尚未开放' : value.settingsBlocked || value.feedbackBlocked || snapshot.manualReviewRequired ? '存在待核查请求，请按原编号核对' : '真实偏好已开放 · 每次提交须确认',
      preferencesReason: snapshot ? snapshot.reason : unverifiedReason,
      realStoredLabel: known && snapshot.storedVolume !== null ? snapshot.storedVolume + '%' : '未知',
      realOutputLabel: known && snapshot.outputVolume !== null ? snapshot.outputVolume + '%' : '未知',
      realMutedLabel: !known || snapshot.outputMuted === null ? '未提供静音状态' : snapshot.outputMuted ? '设备实际已静音' : '设备实际未静音',
      realFollowSource: !known ? '未知' : snapshot.followUpSource === 'default' ? '设备默认值' : '设备已保存值',
      canChangeName: Boolean(known && snapshot.canChangeName), canChangeVolume: Boolean(known && snapshot.canChangeVolume),
      canChangeFollowUp: Boolean(known && snapshot.canChangeFollowUp),
      canSaveReal: Boolean(known && !value.loading && value.settingsDirty && !value.settingsNeedsRefresh && !value.settingsDraftError && !value.settingsBlocked && !value.settingsSubmitting && !value.settingsChecking && !isConfirm && value.settingsDirtyFields.every((field: string) => field === 'name' ? snapshot.canChangeName : field === 'volume' ? snapshot.canChangeVolume : snapshot.canChangeFollowUp)),
      canFeedbackReal: Boolean(known && !value.loading && snapshot.canSubmitFeedback && !value.feedbackDraftError && feedbackDraft.content.trim() && Array.from(feedbackDraft.content).length >= 2 && Array.from(feedbackDraft.content).length <= 500 && !value.feedbackBlocked && !value.feedbackSubmitting && !value.feedbackChecking && !isConfirm),
      settingsDraftError: value.settingsDraftError, feedbackDraftError: value.feedbackDraftError, preferencesConfirm: isConfirm,
      saving: value.settingsSubmitting, feedbackSubmitting: value.feedbackSubmitting,
      settingsMessage: value.settingsSubmitting ? '当前设置正在提交，尚未确认结果，请勿重复保存。' : settingsFailure || settingsReceipt.message,
      feedbackMessage: value.feedbackSubmitting ? '当前反馈正在提交，尚未确认已保存。' : feedbackFailure || feedbackReceipt.message,
      settingsSteps: value.settingsSubmitting || settingsFailure && value.settingsReceipt && value.settingsReceipt.status === 'recorded' ? [] : settingsReceipt.steps,
      feedbackSteps: value.feedbackSubmitting || feedbackFailure && value.feedbackReceipt && value.feedbackReceipt.status === 'recorded' ? [] : feedbackReceipt.steps,
      settingsReceiptStatus: !value.settingsSubmitting && !settingsFailure && value.settingsReceipt ? value.settingsReceipt.status : '',
      feedbackReceiptStatus: !value.feedbackSubmitting && !feedbackFailure && value.feedbackReceipt ? value.feedbackReceipt.status : '',
      canCheckSettings: value.canCheckSettings, canCheckFeedback: value.canCheckFeedback, settingsChecking: value.settingsChecking, feedbackChecking: value.feedbackChecking,
      realSettingsBlocked: value.settingsBlocked, realFeedbackBlocked: value.feedbackBlocked }
    const signature = JSON.stringify(patch)
    if (signature !== this.preferencesRenderSignature) {
      this.preferencesRenderSignature = signature
      const changed: any = {}
      Object.keys(patch).forEach(key => { if (JSON.stringify(this.data[key]) !== JSON.stringify(patch[key])) changed[key] = patch[key] })
      if (Object.keys(changed).length) this.setData(changed)
    }
  },
  async requestRealPreferences(kind: string) {
    const visit = this.viewVersion
    if (!this.isCurrentVisit(visit) || !this.data.liveMode || !this.realPreferences || this.preferenceConfirmation || this.preparingPreferences || !['settings', 'feedback'].includes(kind)) return
    if (!(kind === 'settings' ? this.data.canSaveReal : this.data.canFeedbackReal)) return
    this.preferenceGuardNotice = ''
    this.preparingPreferences = true
    let token: any = null
    try { token = await this.realPreferences.prepare(kind) } finally { this.preparingPreferences = false }
    if (!token || !this.isCurrentVisit(visit) || !this.data.liveMode) { if (token) this.realPreferences.cancel(token); return }
    const value = this.realPreferences.getState(), snapshot = value.snapshot
    if (!snapshot) { this.realPreferences.cancel(token); return }
    const target = { token, kind, visit, version: draftKey(value, kind), permissions: permissionKey(snapshot) }
    this.preferenceConfirmation = target
    this.renderPreferences(value)
    return new Promise<void>(resolve => {
      const currentConfirmation = () => this.isCurrentVisit(visit) && this.data.liveMode && this.preferenceConfirmation === target
      const modalFailed = () => {
        if (currentConfirmation()) {
          this.preferenceConfirmation = null
          this.preferenceGuardNotice = '请求未发送：确认弹窗打开失败，请重新点击保存；本次未提交设备变更。'
          this.realPreferences.cancel(token)
          this.renderPreferences(this.realPreferences.getState())
        }
        resolve()
      }
      const modal = { title: kind === 'settings' ? '保存真实设备设置？' : '保存反馈到真实设备？',
        content: kind === 'settings' ? '仅提交本次实际修改的字段。音量会影响真实扬声器，连续聊天时间会改变设备参数。名字/音量与追问时间分两步保存，可能部分成功；结果未知时只能核对原回执，不会自动补发。确认保存一次？' : '问题描述将保存到所选设备本地 SQLite，不代表客服已收到，也不承诺上传云端。请勿填写密钥或敏感信息。确认保存一次？',
        confirmText: '确认保存',
        success: async (answer: any) => {
          if (!currentConfirmation()) { resolve(); return }
          try {
            this.preferenceConfirmation = null
            if (!answer || answer.confirm !== true) { this.preferenceGuardNotice = '请求未发送：已取消本次保存确认。'; this.realPreferences.cancel(token); return }
            await this.realPreferences.confirm(token)
          } finally {
            if (this.preferenceConfirmation === target) { this.preferenceConfirmation = null; this.realPreferences.cancel(token) }
            if (this.isCurrentVisit(visit) && this.data.liveMode) this.renderPreferences(this.realPreferences.getState())
            resolve()
          }
        },
        fail: modalFailed
      }
      try { wx.showModal(modal) } catch (_) { modalFailed() }
    })
  },
  checkPreferenceReceipt(event: any) {
    const kind = event && event.currentTarget && event.currentTarget.dataset.kind
    if (!this.pageVisible || !this.data.liveMode || !this.realPreferences || !['settings', 'feedback'].includes(kind)) return Promise.resolve()
    return this.realPreferences.checkReceipt(kind)
  },
  async load() {
    if (!this.pageVisible) return
    if (this.data.liveMode) return this.refreshPreferences()
    const visit = this.viewVersion
    const request = this.loadVersion = (this.loadVersion || 0) + 1
    const current = () => this.isCurrentVisit(visit) && !this.data.liveMode && this.loadVersion === request
    if (!wx.getStorageSync('aiCatSession')) return wx.redirectTo({ url: '/pages/auth/index' })
    try {
      const pet = await service.getBoundPet()
      if (!current()) return
      if (!pet) return wx.redirectTo({ url: '/pages/device/index' })
      const settings = await service.getSettings()
      if (!current()) return
      const clearedAt = wx.getStorageSync('aiCatMemoryClearedAt')
      this.setData({
        pet, session: wx.getStorageSync('aiCatSession'),
        ...(!this.data.settingsDirty ? { name: pet.name, volume: settings.volume, followUpSeconds: settings.followUpSeconds } : {}),
        memoryNotice: clearedAt ? '上次演示清除：' + formatDateTime(clearedAt) : '',
        loading: false, error: ''
      })
    } catch (error) { if (current()) this.setData({ loading: false, error: '设置预览暂未更新，已保留上次内容' }) }
  },
  editRealSetting(field: string, value: any) {
    if (!this.pageVisible || !this.data.liveMode || !this.realPreferences || !(field === 'name' ? this.data.canChangeName : field === 'volume' ? this.data.canChangeVolume : this.data.canChangeFollowUp)) return
    this.realPreferences.setDraft('settings', { [field]: value })
  },
  inputName(event: any) { if (this.data.liveMode) return this.editRealSetting('name', event.detail.value); this.setData({ name: event.detail.value, settingsDirty: true }) },
  onVolumeChange(event: any) { if (this.data.liveMode) return this.editRealSetting('volume', Number(event.detail.value)); this.setData({ volume: Number(event.detail.value), settingsDirty: true }) },
  onFollowChange(event: any) { if (this.data.liveMode) return this.editRealSetting('followUpSeconds', Number(event.detail.value)); this.setData({ followUpSeconds: Number(event.detail.value), settingsDirty: true }) },
  inputApiKey(event: any) { if (!this.data.liveMode) this.setData({ apiKey: event.detail.value }) },
  async saveSettings() {
    if (this.data.liveMode) return this.requestRealPreferences('settings')
    if (!this.pageVisible || this.data.saving || this.data.processingDanger || !this.data.pet) return
    const visit = this.viewVersion
    const snapshot = { name: this.data.name, volume: this.data.volume, followUpSeconds: this.data.followUpSeconds, apiKey: this.data.apiKey }
    const name = snapshot.name.trim()
    if (!lengthBetween(name, 1, 20)) return wx.showToast({ title: '名字需要 1–20 个字符', icon: 'none' })
    this.setData({ saving: true })
    try {
      const result = await service.updateSettings(this.data.pet.petId, {
        name, volume: snapshot.volume, followUpSeconds: snapshot.followUpSeconds
      }, createRequestId('settings'))
      if (!this.isCurrentVisit(visit)) return
      const unchanged = this.data.name === snapshot.name && this.data.volume === snapshot.volume && this.data.followUpSeconds === snapshot.followUpSeconds
      this.setData({ pet: result.pet, settingsDirty: !unchanged, ...(unchanged ? { name } : {}), ...(this.data.apiKey === snapshot.apiKey ? { apiKey: '' } : {}) })
      wx.showToast({ title: unchanged ? '本地偏好已保存' : '已保存，仍有新修改', icon: unchanged ? 'success' : 'none' })
    } catch (error) { if (this.isCurrentVisit(visit)) wx.showToast({ title: '预览设置未保存，请重试', icon: 'none' }) }
    finally { if (this.isCurrentVisit(visit)) this.setData({ saving: false }) }
  },
  onFeedbackType(event: any) {
    if (this.data.liveMode) {
      if (!this.pageVisible || !this.realPreferences || !this.data.preferencesKnown) return
      const category = this.data.feedbackValues[Number(event.detail.value)]
      if (category) this.realPreferences.setDraft('feedback', { ...this.realPreferences.getState().feedbackDraft, category })
      return
    }
    this.setData({ feedbackIndex: Number(event.detail.value) })
  },
  inputFeedback(event: any) {
    const feedbackContent = event.detail.value
    if (this.data.liveMode) {
      if (this.pageVisible && this.realPreferences && this.data.preferencesKnown) this.realPreferences.setDraft('feedback', { ...this.realPreferences.getState().feedbackDraft, content: feedbackContent })
      return
    }
    this.setData({ feedbackContent, feedbackRemaining: 500 - feedbackContent.length })
  },
  async submitFeedback() {
    if (this.data.liveMode) return this.requestRealPreferences('feedback')
    if (!this.pageVisible || this.data.feedbackSubmitting || this.data.processingDanger || !this.data.pet) return
    const visit = this.viewVersion
    const snapshot = { content: this.data.feedbackContent, index: this.data.feedbackIndex }
    const content = snapshot.content.trim()
    if (!lengthBetween(content, 2, 500)) return wx.showToast({ title: '请用 2–500 个字描述问题', icon: 'none' })
    this.setData({ feedbackSubmitting: true })
    try {
      await service.submitFeedback(this.data.pet.petId, {
        category: this.data.feedbackValues[snapshot.index], content, requestId: createRequestId('feedback')
      })
      if (!this.isCurrentVisit(visit)) return
      if (this.data.feedbackContent === snapshot.content && this.data.feedbackIndex === snapshot.index) this.setData({ feedbackContent: '', feedbackRemaining: 500 })
      wx.showToast({ title: '已演示提交，未发送网络', icon: 'none' })
    } catch (error) { if (this.isCurrentVisit(visit)) wx.showToast({ title: '反馈预览暂未完成', icon: 'none' }) }
    finally { if (this.isCurrentVisit(visit)) this.setData({ feedbackSubmitting: false }) }
  },
  showDanger(event: any) {
    if (this.data.liveMode) return
    const dangerKind = event.currentTarget.dataset.kind
    if (!this.data.processingDanger && DANGER[dangerKind]) this.setData({ dangerKind, danger: DANGER[dangerKind] })
  },
  hideDanger() { if (!this.data.processingDanger) this.setData({ dangerKind: '', danger: null }) },
  async confirmDanger() {
    if (this.data.liveMode) return
    if (!this.pageVisible || !this.data.dangerKind || this.data.processingDanger || !this.data.pet) return
    const visit = this.viewVersion
    const kind = this.data.dangerKind
    const draft = kind === 'memory' ? null : draftFor(this.data.pet)
    this.setData({ processingDanger: true })
    try {
      if (kind === 'unbind') {
        await service.unbindPet(this.data.pet.petId)
        clearDraft(draft)
        if (!this.isCurrentVisit(visit)) return
        this.setData({ dangerKind: '', danger: null, apiKey: '' })
        return wx.redirectTo({ url: '/pages/device/index' })
      }
      if (kind === 'memory') {
        await service.clearMemory(this.data.pet.petId, createRequestId('memory-clear'))
        if (!this.isCurrentVisit(visit)) return
        this.setData({ dangerKind: '', danger: null, memoryNotice: '已演示清除短期与长期记忆，完整对话记录仍保留。' })
        wx.showToast({ title: '演示记忆已清除', icon: 'success' })
        return
      }
      const result = await service.resetProductData('RESET_PRODUCT_DATA')
      clearDraft(draft)
      if (!this.isCurrentVisit(visit)) return
      this.setData({ dangerKind: '', danger: null, apiKey: '' })
      wx.showModal({
        title: '本地重置演示完成',
        content: '预览恢复编号：' + result.backupId + '\n此编号仅展示流程，不是设备备份。真实设备未改变，请重新体验登录与性格盲盒。',
        showCancel: false,
        success: () => { if (this.isCurrentVisit(visit)) wx.reLaunch({ url: '/pages/auth/index' }) }
      })
    } catch (error: any) {
      if (this.isCurrentVisit(visit)) wx.showToast({ title: error.message || '本地操作暂未完成', icon: 'none' })
    } finally { if (this.isCurrentVisit(visit)) this.setData({ processingDanger: false }) }
  },
  logout() {
    if (this.data.liveMode) return this.liveLogout()
    clearDraft()
    this.leavePage()
    wx.removeStorageSync('aiCatSession')
    wx.reLaunch({ url: '/pages/auth/index' })
  }
})
