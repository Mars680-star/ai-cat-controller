const { getPetService } = require('../../services/pet-service')
const { createRequestId } = require('../../utils/request-id')
const { formatDateTime } = require('../../utils/format')
const { beginView, endView, viewActive, patchView, clearDraft, draftFor, draftCurrent, editDraft, submitDraft } = require('./view-state')
const { livePageData, livePageMethods, attachLivePage, detachLivePage } = require('../../utils/live-page')
const { createLiveDialog } = require('../../services/live-dialog')
const { createLiveDialogControl } = require('../../services/live-dialog-control')
const { utcLabel, summaryView } = require('./live-view')
const service = getPetService()

const STATUS: any = {
  starting: { text: '语音服务启动中', hint: '正在准备语音互动。', badge: 'info' },
  connecting: { text: '正在连接云端', hint: '请稍候，连接完成后即可说话。', badge: 'info' },
  connected: { text: '语音云端已连接', hint: '正在等待语音会话配置完成。', badge: 'info' },
  ready: { text: '等待唤醒词', hint: '点击按钮，预览开始聆听后的状态。', badge: 'neutral' },
  waiting_wake: { text: '等待唤醒词', hint: '点击按钮，预览开始聆听后的状态。', badge: 'neutral' },
  wake_detected: { text: '已听到唤醒词', hint: '正在打开聆听通道。', badge: 'success' },
  listening: { text: '正在聆听', hint: '慢慢说，我在听。', badge: 'success' },
  thinking: { text: '正在思考', hint: '正在组织回应，请稍候。', badge: 'info' },
  processing_audio: { text: '正在处理语音问题', hint: '问题已提交，等待服务端处理确认。', badge: 'info' },
  answering: { text: '正在回答', hint: '需要时可以打断并结束。', badge: 'info' },
  proactive_queued: { text: '设备自主短语已排队', hint: '设备已有自主短语等待播报，不代表本页新发起了请求。', badge: 'info' },
  proactive_speaking: { text: '设备正在自主播报', hint: '正在展示设备原有播报阶段，本页未开启新的自主功能。', badge: 'info' },
  proactive_finishing: { text: '设备自主播报收尾中', hint: '正在等待原有播报流程收尾。', badge: 'info' },
  followup_listening: { text: '等待继续追问', hint: '还想聊什么？可以继续说。', badge: 'success' },
  followup_preparing: { text: '正在准备继续追问', hint: '播放阶段已结束，正在准备后续聆听。', badge: 'info' },
  waiting_follow_up: { text: '等待继续追问', hint: '还想聊什么？可以继续说。', badge: 'success' },
  interrupted: { text: '回答已打断', hint: '本轮回答已经结束。', badge: 'neutral' },
  echo_guard: { text: '已停止回声循环', hint: '可以重新开始聆听。', badge: 'neutral' },
  recovering: { text: '正在恢复云端连接', hint: '连接恢复后会自动更新。', badge: 'info' },
  waking: { text: '正在请求聆听', hint: '请稍候。', badge: 'info' },
  interrupting: { text: '正在请求打断', hint: '正在结束当前回答。', badge: 'info' },
  submitting_text: { text: '正在提交文字问题', hint: '消息已进入发送流程。', badge: 'info' },
  stopping: { text: '语音服务正在停止', hint: '尚未确认语音服务已经停止。', badge: 'info' },
  offline: { text: '语音服务离线', hint: '请检查设备网络后再试。', badge: 'danger' },
  unavailable: { text: '状态不可用', hint: '暂时没有取得语音状态。', badge: 'danger' }
}
const ACTIVE = ['starting', 'connecting', 'connected', 'wake_detected', 'listening', 'thinking', 'processing_audio', 'answering', 'proactive_queued', 'proactive_speaking', 'proactive_finishing', 'followup_listening', 'followup_preparing', 'waiting_follow_up', 'recovering', 'waking', 'interrupting', 'submitting_text', 'stopping']
const CONTROL_NAMES: any = { listen: '开始或继续聆听', interrupt: '打断并结束', text: '发送文字问题' }
function realDraftValid(text: string) {
  const points = Array.from(text)
  return Boolean(text.trim()) && points.length <= 500 && !/[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/.test(text) && !points.some(point => { const code = point.codePointAt(0) || 0; return code >= 0xd800 && code <= 0xdfff })
}
function receiptLabel(value: any) {
  return !value ? '' : ({ sending: '请求正在派发，请勿重复发送', accepted: '请求已接收，等待设备状态更新；不代表回答或声音播放已经完成', not_sent: '已确认未发送；可在核对权限后重新确认', unknown: '发送结果未知，只能核对原回执，不能换编号重发' } as any)[value.status] || '回执状态未验证'
}

Page({
  ...livePageMethods,
  data: {
    ...livePageData, loading: true, error: '', pet: null, dialogState: 'ready', statusView: STATUS.ready,
    status: null, statusElapsed: '—', conversations: [], syncText: '正在同步', inputText: '', canSend: false,
    sending: false, controlling: false, pendingMessage: null, openingConversationId: '',
    liveDialogLoading: false, liveDialogError: '', liveDialogSampled: '', liveListKnown: false, liveListLoading: false,
    liveStatusKnown: false, liveStatusStale: false, liveListError: '', liveListHasMore: false,
    liveControlLoading: false, liveControlStatus: '控制接口尚未验证', liveControlReason: '', liveControlError: '',
    liveCanListen: false, liveCanInterrupt: false, liveCanSendText: false, liveDraftEditable: false,
    liveControlConfirm: false, liveControlSubmitting: false, liveControlInterrupting: false, realDraftCount: 0, realDraftError: '',
    liveMainReceipt: null, liveInterruptReceipt: null, liveMainMessage: '', liveInterruptMessage: '',
    liveCanCheckMain: false, liveCanCheckInterrupt: false, liveCheckingMain: false, liveCheckingInterrupt: false
  },
  async onShow() {
    const epoch = beginView(this)
    if (epoch === null) return
    patchView(this, { controlling: false, openingConversationId: '' }, epoch)
    attachLivePage(this)
    if (!this.realDialog) this.realDialog = createLiveDialog(this.liveSession)
    this.realDialog.show((value: any) => { if (this.pageVisible && this.data.liveMode) this.renderLiveDialog(value) })
    if (!this.realControl) this.realControl = createLiveDialogControl(this.liveSession, createRequestId, wx)
    this.realControl.show((value: any) => { if (this.pageVisible && this.data.liveMode) this.renderLiveControl(value) })
    if (this.data.liveMode) return
    await this.load(false)
    if (viewActive(this, epoch)) this.startPolling()
  },
  onHide() { this.pauseView(false) },
  onUnload() { this.pauseView(true) },
  pauseView(disposed: boolean) {
    if (this.controlConfirmation || this.preparingControl) this.controlGuardNotice = '请求未发送：离开页面已取消上次确认，返回后需重新选择并确认。'
    endView(this, disposed)
    this.controlConfirmation = null
    if (this.realControl) this.realControl.hide()
    if (this.realDialog) this.realDialog.hide()
    detachLivePage(this)
    clearTimeout(this.dialogTimer)
    this.dialogTimer = null
    this.openingConversation = false
  },
  async onPullDownRefresh() {
    const epoch = this.viewEpoch
    await this.load(true)
    if (viewActive(this, epoch)) wx.stopPullDownRefresh()
  },
  onLiveModeChanged() {
    this.viewEpoch = (this.viewEpoch || 0) + 1
    this.loadSequence = (this.loadSequence || 0) + 1
    this.statusRevision = (this.statusRevision || 0) + 1
    clearTimeout(this.dialogTimer); this.dialogTimer = null
    this.openingConversation = false
    this.controlConfirmation = null
    this.controlGuardNotice = ''
    patchView(this, { loading: false, error: '', pet: null, status: null, dialogState: 'unavailable',
      statusView: STATUS.unavailable, statusElapsed: '—', conversations: [], syncText: '', inputText: '',
      canSend: false, sending: false, controlling: false, pendingMessage: null, openingConversationId: '',
      liveDialogLoading: false, liveDialogError: '', liveDialogSampled: '', liveListKnown: false, liveListLoading: false,
      liveStatusKnown: false, liveStatusStale: false, liveListError: '', liveListHasMore: false,
      liveControlLoading: false, liveControlStatus: '控制接口尚未验证', liveControlReason: '', liveControlError: '',
      liveCanListen: false, liveCanInterrupt: false, liveCanSendText: false, liveDraftEditable: false,
      liveControlConfirm: false, liveControlSubmitting: false, liveControlInterrupting: false, realDraftCount: 0, realDraftError: '',
      liveMainReceipt: null, liveInterruptReceipt: null, liveMainMessage: '', liveInterruptMessage: '',
      liveCanCheckMain: false, liveCanCheckInterrupt: false, liveCheckingMain: false, liveCheckingInterrupt: false })
  },
  async showPreview() {
    if (!this.pageVisible) return
    await this.liveSession.preview()
    if (!this.pageVisible || this.data.liveMode) return
    await this.load(false)
    if (this.pageVisible && !this.data.liveMode) this.startPolling()
  },
  liveDialogRefresh() {
    if (!this.pageVisible) return Promise.resolve()
    return Promise.all([this.liveRefresh(), this.realDialog ? this.realDialog.refresh() : Promise.resolve(), this.realControl ? this.realControl.refresh() : Promise.resolve()])
  },
  refreshRealControl() {
    if (this.pageVisible && this.data.liveMode && this.realControl) {
      // Only retire an old, explicitly unsent modal notice. The controller's
      // durable pending markers and receipt warnings remain untouched.
      if (!this.controlConfirmation) this.controlGuardNotice = ''
      return this.realControl.refresh()
    }
    return Promise.resolve()
  },
  renderLiveDialog(value: any) {
    const epoch = this.viewEpoch
    if (!viewActive(this, epoch) || !this.data.liveMode) return
    const state = this.liveSession.getState(), snapshot = value.snapshot, list = value.conversations
    const selected = state.pets.find((item: any) => item.petId === state.selectedPetId)
    const known = Boolean(snapshot), stale = Boolean(snapshot && snapshot.stale)
    const dialogState = snapshot && !stale ? snapshot.state : 'unavailable'
    const text = stale ? '语音状态已过期' : !snapshot ? '语音状态未验证' : (STATUS[dialogState] || STATUS.unavailable).text
    const hint = stale ? '已停止展示旧的聆听或回答状态，请刷新后查看。' : !snapshot ? state.authKind === 'local' ? '扫描机身卡本地配对并选择设备后读取真实状态。' : '验证微信登录并选择设备后读取真实状态。' : dialogState === 'offline' ? '设备返回语音服务离线；这不代表设备网络断开，已保存记录仍可独立读取。' : dialogState === 'unavailable' ? '本次暂未取得可用语音状态，已保存记录仍可独立读取。' : '这里只读展示语音阶段；下方真实控制需服务端授权和逐次确认，不会随刷新自动发送。'
    patchView(this, { pet: selected ? { name: selected.name, personalityName: '真实对话只读' } : null,
      loading: false, error: '', dialogState, statusView: { text, hint, badge: stale || !known || ['offline', 'unavailable'].includes(dialogState) ? 'danger' : 'neutral' },
      status: snapshot && !stale ? { sessionActive: snapshot.sessionActive, audioPlaying: snapshot.audioPlaying, followUpDeadlineAt: snapshot.followUpDeadlineAt } : null,
      statusElapsed: '未提供', conversations: list ? list.items.map(summaryView) : [],
      syncText: list ? '设备库已保存记录 · 最近 ' + list.limit + ' 条' : value.conversationsError ? '真实记录读取失败' : value.loading ? '读取真实记录中…' : '尚未读取真实记录',
      liveDialogLoading: value.loading, liveDialogError: value.error, liveListLoading: value.loading,
      liveListError: value.conversationsError, liveListKnown: Boolean(list), liveStatusKnown: known, liveStatusStale: stale,
      liveDialogSampled: snapshot ? utcLabel(snapshot.sampledAt) : '', liveListHasMore: false }, epoch)
  },
  renderLiveControl(value: any) {
    const epoch = this.viewEpoch
    if (!viewActive(this, epoch) || !this.data.liveMode) return
    const snapshot = value.snapshot, permitted = Boolean(snapshot && snapshot.enabled && snapshot.verified)
    const content = typeof value.draftText === 'string' ? value.draftText : ''
    const count = Array.from(content).length
    const draftError = value.draftError || (count > 500 ? '真实问题最多 500 字，正文已保留，请修改后再确认。' : content && !realDraftValid(content) ? '正文含不支持的字符或空白，请修改后再确认。' : '')
    const confirm = this.controlConfirmation
    const fingerprint = snapshot ? JSON.stringify([snapshot.enabled, snapshot.verified, snapshot.canListen, snapshot.canInterrupt, snapshot.canSendText, snapshot.manualReviewRequired, snapshot.state, snapshot.stale]) : ''
    if (confirm && (!value.confirmPending || confirm.fingerprint !== fingerprint || confirm.draftVersion !== value.draftVersion)) {
      this.controlConfirmation = null
      this.controlGuardNotice = value.error ? '请求未发送：确认期间控制权限读取失败，请核对控制权限后重新确认。'
        : confirm.draftVersion !== value.draftVersion ? '请求未发送：确认期间问题正文已修改，请重新确认。'
        : confirm.fingerprint !== fingerprint ? '请求未发送：确认期间语音状态或控制权限已变化，请核对后重新确认。'
        : '请求未发送：登录、设备或本次确认状态已更新，请重新选择并确认。'
      if (value.confirmPending) this.realControl.cancel(confirm.token)
    }
    const status = value.loading ? '正在核对控制权限' : !snapshot ? '控制接口尚未验证' : !snapshot.enabled ? '真实控制尚未开放' : !snapshot.verified ? '控制身份未验证，禁止发送' : snapshot.stale ? '语音阶段已过期，仅按权限保留独立打断' : snapshot.manualReviewRequired || value.mainBlocked || value.interruptBlocked ? '存在待核查请求，各通道按各自权限保留' : '真实控制可用 · 每次操作须确认'
    const identity = this.liveSession.getState()
    const unverifiedReason = !identity.signedIn ? identity.authKind === 'local' ? '请先扫描机身卡完成本地配对并选择设备。' : '请先完成微信登录并选择已授权设备。'
      : !identity.selectedPetId ? '请选择已授权设备，再核对控制权限。'
      : value.loading ? '正在核对控制权限，请稍候；核验不会发送语音请求。'
      : value.error ? '控制权限读取失败，请点击“核对控制权限”重试；已保存记录仍可独立读取。'
      : '尚未取得本页控制权限，请点击“核对控制权限”。'
    patchView(this, { liveControlLoading: value.loading, liveControlStatus: status, liveControlReason: snapshot ? snapshot.reason : unverifiedReason,
      liveControlError: this.controlGuardNotice || value.error || value.storageError,
      liveCanListen: Boolean(permitted && !value.loading && snapshot.canListen && !value.mainBlocked && !value.submitting && !value.checkingMain && !this.controlConfirmation),
      liveCanSendText: Boolean(permitted && !value.loading && snapshot.canSendText && !value.mainBlocked && !value.submitting && !value.checkingMain && !this.controlConfirmation && !draftError && realDraftValid(content)),
      liveCanInterrupt: Boolean(permitted && !value.loading && snapshot.canInterrupt && !value.interruptBlocked && !value.interrupting && !value.checkingInterrupt && !this.controlConfirmation),
      liveDraftEditable: permitted, inputText: content, realDraftCount: count, realDraftError: draftError, canSend: realDraftValid(content),
      liveControlConfirm: Boolean(this.controlConfirmation), liveControlSubmitting: value.submitting, liveControlInterrupting: value.interrupting,
      liveMainReceipt: value.submitting || value.mainError && value.mainReceipt && value.mainReceipt.status === 'accepted' ? null : value.mainReceipt,
      liveInterruptReceipt: value.interrupting || value.interruptError && value.interruptReceipt && value.interruptReceipt.status === 'accepted' ? null : value.interruptReceipt,
      liveMainMessage: value.submitting ? '当前请求正在发送，尚未确认接受，请勿重复发送。' : value.mainError || receiptLabel(value.mainReceipt),
      liveInterruptMessage: value.interrupting ? '当前打断请求正在发送，尚未确认接受，请勿重复发送。' : value.interruptError || receiptLabel(value.interruptReceipt),
      liveCanCheckMain: value.canCheckMain, liveCanCheckInterrupt: value.canCheckInterrupt, liveCheckingMain: value.checkingMain, liveCheckingInterrupt: value.checkingInterrupt,
      sending: false, controlling: false, pendingMessage: null }, epoch)
  },
  async requestRealControl(kind: string) {
    const epoch = this.viewEpoch
    if (!viewActive(this, epoch) || !this.data.liveMode || !this.realControl || this.controlConfirmation || this.preparingControl || !Object.prototype.hasOwnProperty.call(CONTROL_NAMES, kind)) return
    if (!(kind === 'listen' ? this.data.liveCanListen : kind === 'interrupt' ? this.data.liveCanInterrupt : this.data.liveCanSendText)) return
    this.preparingControl = true
    this.controlGuardNotice = ''
    let token: any = null
    try { token = await this.realControl.prepare(kind) }
    finally { this.preparingControl = false }
    if (!token || !viewActive(this, epoch) || !this.data.liveMode) { if (token) this.realControl.cancel(token); return }
    const value = this.realControl.getState(), snapshot = value.snapshot
    if (!snapshot) { this.realControl.cancel(token); return }
    const target = { token, epoch, draftVersion: value.draftVersion,
      fingerprint: JSON.stringify([snapshot.enabled, snapshot.verified, snapshot.canListen, snapshot.canInterrupt, snapshot.canSendText, snapshot.manualReviewRequired, snapshot.state, snapshot.stale]) }
    this.controlConfirmation = target
    this.renderLiveControl(value)
    return new Promise<void>(resolve => {
      const currentConfirmation = () => viewActive(this, epoch) && this.data.liveMode && this.controlConfirmation === target
      const modalFailed = () => {
        if (currentConfirmation()) {
          this.controlConfirmation = null
          this.controlGuardNotice = '请求未发送：确认弹窗打开失败，请重新点击操作；未打开麦克风或发送指令。'
          this.realControl.cancel(token)
          this.renderLiveControl(this.realControl.getState())
        }
        resolve()
      }
      const modal = { title: '真实设备：' + CONTROL_NAMES[kind],
        content: kind === 'interrupt' ? '确认向所选设备发送一次打断请求？这是真机语音链路，涉及麦克风、云端请求、扬声器及原有自动动作，可能结束正在进行的聆听或回答。未知文字请求仍保留原编号，不会因此认定未发送。' : '确认发送一次到所选真实设备？这可能开启麦克风、调用云端模型并通过扬声器播放，也可能遵循设备原有自动动作。请确认周围环境安全；接口接受不代表回答或播放完成。',
        confirmText: '确认发送',
        success: async (answer: any) => {
          // Native callbacks may arrive after hide/show or a newer modal.
          // Check ownership before cancellation as well as before dispatch.
          if (!currentConfirmation()) { resolve(); return }
          try {
            this.controlConfirmation = null
            if (!answer || answer.confirm !== true) { this.controlGuardNotice = '请求未发送：已取消本次确认。'; this.realControl.cancel(token); return }
            await this.realControl.confirm(token)
          } finally {
            if (this.controlConfirmation === target) { this.controlConfirmation = null; this.realControl.cancel(token) }
            if (viewActive(this, epoch) && this.data.liveMode) this.renderLiveControl(this.realControl.getState())
            resolve()
          }
        },
        fail: modalFailed
      }
      // Parameter validation may fail synchronously on native runtimes.
      try { wx.showModal(modal) } catch (_) { modalFailed() }
    })
  },
  checkOriginalReceipt(event: any) {
    if (!viewActive(this, this.viewEpoch) || !this.data.liveMode || !this.realControl) return Promise.resolve()
    const channel = event && event.currentTarget && event.currentTarget.dataset.channel
    if (channel !== 'main' && channel !== 'interrupt') return Promise.resolve()
    return this.realControl.checkReceipt(channel)
  },
  startPolling() {
    clearTimeout(this.dialogTimer)
    this.dialogTimer = null
    const epoch = this.viewEpoch
    if (!viewActive(this, epoch) || this.data.liveMode || !this.data.pet) return
    // No interval: a slow request cannot overlap the next poll.
    this.dialogTimer = setTimeout(async () => {
      this.dialogTimer = null
      if (!viewActive(this, epoch)) return
      await this.refreshStatus()
      if (viewActive(this, epoch)) this.startPolling()
    }, ACTIVE.includes(this.data.dialogState) ? 1000 : 5000)
  },
  async readStatus(petId: string, epoch: number, revision: number): Promise<any> {
    if (!viewActive(this, epoch) || this.data.liveMode) return null
    const existing = this.statusFlight
    if (existing) {
      if (existing.epoch === epoch && existing.revision === revision && existing.petId === petId) return existing.promise
      try { await existing.promise } catch (error) {}
      return viewActive(this, epoch) ? this.readStatus(petId, epoch, revision) : null
    }
    const flight = { epoch, revision, petId, promise: Promise.resolve().then(() => service.getDialogStatus(petId)) }
    this.statusFlight = flight
    try { return await flight.promise }
    finally { if (this.statusFlight === flight) this.statusFlight = null }
  },
  applyStatus(raw: any, epoch = this.viewEpoch) {
    if (!raw || !viewActive(this, epoch) || this.data.liveMode) return
    const state = raw.state || 'unavailable'
    const elapsed = Number(raw.elapsedSeconds)
    const active = ACTIVE.includes(state)
    const status = {
      state, elapsedSeconds: active && Number.isFinite(elapsed) ? Math.max(0, Math.floor(elapsed)) : null,
      followUpRemaining: Math.max(0, Math.floor(Number(raw.followUpRemaining) || 0)),
      canInterrupt: Boolean(raw.canInterrupt), sessionActive: Boolean(raw.sessionActive)
    }
    const statusView = STATUS[state] || { text: state, hint: raw.message || '请稍后重试。', badge: 'neutral' }
    patchView(this, { dialogState: state, statusView, status, statusElapsed: status.elapsedSeconds === null ? '—' : status.elapsedSeconds + ' 秒' }, epoch)
  },
  renderDraft(epoch = this.viewEpoch) {
    if (this.data.liveMode || !draftCurrent(this.draft)) return
    patchView(this, { inputText: this.draft.text, canSend: Boolean(this.draft.text.trim()), sending: Boolean(this.draft.flight), pendingMessage: this.draft.pending }, epoch)
  },
  async load(silent: any = false) {
    const epoch = this.viewEpoch
    if (!viewActive(this, epoch)) return
    if (this.data.liveMode) return this.liveDialogRefresh()
    const sequence = this.loadSequence = (this.loadSequence || 0) + 1
    const current = () => viewActive(this, epoch) && this.loadSequence === sequence
    if (silent !== true && !this.data.pet) patchView(this, { loading: true }, epoch)
    try {
      const pet = await service.getBoundPet()
      if (!current()) return
      if (!pet || !wx.getStorageSync('aiCatSession')) {
        clearDraft()
        wx.redirectTo({ url: pet ? '/pages/auth/index' : '/pages/device/index' })
        return
      }
      this.draft = draftFor(pet)
      patchView(this, { pet }, epoch)
      this.renderDraft(epoch)
      if (this.draft && this.draft.flight) this.watchSend(this.draft, this.draft.flight, epoch)
      const revision = this.statusRevision || 0
      const [status, page] = await Promise.all([this.readStatus(pet.petId, epoch, revision), service.listConversations(pet.petId)])
      if (!current()) return
      // The list needs previews, not every message body in each conversation.
      const conversations = page.items.map((item: any) => ({
        conversationId: item.conversationId, status: item.status, userPreview: item.userPreview,
        assistantPreview: item.assistantPreview, messageCount: item.messageCount,
        startedLabel: formatDateTime(item.startedAt),
        statusText: item.status === 'complete' ? '已完成' : item.status === 'waiting_assistant' ? '等待回答' : '仅有回答'
      }))
      patchView(this, { conversations, syncText: page.syncText || '已同步', loading: false, error: '' }, epoch)
      if (revision === (this.statusRevision || 0)) this.applyStatus(status, epoch)
    } catch (error) {
      if (current()) patchView(this, { loading: false, error: '对话状态暂时没有更新，已保留上次内容' }, epoch)
    }
  },
  async refreshStatus() {
    const epoch = this.viewEpoch
    if (!viewActive(this, epoch) || this.data.liveMode || !this.data.pet) return
    const revision = this.statusRevision || 0
    try {
      const status = await this.readStatus(this.data.pet.petId, epoch, revision)
      if (revision === (this.statusRevision || 0)) this.applyStatus(status, epoch)
    } catch (error) {}
  },
  async controlVoice(kind: string) {
    const epoch = this.viewEpoch
    if (this.data.liveMode) return this.requestRealControl(kind === 'listen' ? 'listen' : kind === 'interrupt' ? 'interrupt' : '')
    if (!viewActive(this, epoch) || this.data.liveMode || !this.data.pet || this.controlFlight) return
    if (kind === 'listen' && this.data.dialogState === 'offline') return
    if (kind === 'interrupt' && (!this.data.status || (!this.data.status.canInterrupt && !this.data.status.sessionActive))) return
    this.statusRevision = (this.statusRevision || 0) + 1
    this.controlFlight = true
    patchView(this, { controlling: true }, epoch)
    try {
      if (kind === 'listen') await service.startListening(this.data.pet.petId, createRequestId('listen'))
      else await service.interruptDialog(this.data.pet.petId, createRequestId('interrupt'))
      if (!viewActive(this, epoch)) return
      await this.refreshStatus()
      if (viewActive(this, epoch)) this.startPolling()
    } catch (error) {
      if (viewActive(this, epoch)) wx.showToast({ title: kind === 'listen' ? '对话暂时没有开始，请再试一次' : '暂时无法结束，请再试一次', icon: 'none' })
    } finally {
      this.controlFlight = false
      patchView(this, { controlling: false }, epoch)
    }
  },
  startListening() { return this.controlVoice('listen') },
  interrupt() { return this.controlVoice('interrupt') },
  mainAction() { return this.startListening() },
  onInput(event: any) {
    if (viewActive(this, this.viewEpoch) && this.data.liveMode) {
      if (this.realControl && this.data.liveDraftEditable) this.realControl.setDraft(String(event.detail.value || ''))
      return
    }
    if (!viewActive(this, this.viewEpoch) || this.data.liveMode || !draftCurrent(this.draft)) return
    editDraft(this.draft, String(event.detail.value || '').slice(0, 500))
    this.renderDraft()
  },
  watchSend(draft: any, flight: Promise<boolean>, epoch: number) {
    if (this.sendWatch && this.sendWatch.flight === flight && this.sendWatch.epoch === epoch) return this.sendWatch.promise
    const promise = flight.then(async (success) => {
      if (!viewActive(this, epoch) || this.data.liveMode || this.draft !== draft || !draftCurrent(draft)) return
      this.renderDraft(epoch)
      if (success) {
        this.statusRevision = (this.statusRevision || 0) + 1
        await this.load(true)
        if (viewActive(this, epoch)) wx.showToast({ title: '演示问题已发送', icon: 'success' })
      }
    })
    this.sendWatch = { flight, epoch, promise }
    return promise
  },
  sendText() {
    if (this.data.liveMode) return this.requestRealControl('text')
    const epoch = this.viewEpoch
    const draft = this.draft
    if (!viewActive(this, epoch) || this.data.liveMode || !this.data.pet || !draftCurrent(draft) || draft.flight) return
    const content = draft.text.trim()
    if (!content || content.length > 500) return
    const requestId = draft.pending && draft.pending.state === 'failed' && draft.pending.content === content ? draft.pending.requestId : createRequestId('text')
    return this.sendSnapshot(content, requestId)
  },
  sendSnapshot(content: string, requestId: string) {
    const epoch = this.viewEpoch
    const draft = this.draft
    if (!viewActive(this, epoch) || this.data.liveMode || !this.data.pet || !draftCurrent(draft) || draft.flight) return
    const petId = this.data.pet.petId
    this.statusRevision = (this.statusRevision || 0) + 1
    const flight = submitDraft(draft, content, requestId, () => service.sendText(petId, content, requestId))
    this.renderDraft(epoch)
    return this.watchSend(draft, flight, epoch)
  },
  retrySend() {
    if (!viewActive(this, this.viewEpoch) || this.data.liveMode || !draftCurrent(this.draft) || this.draft.flight || !this.draft.pending || this.draft.pending.state !== 'failed') return
    return this.sendSnapshot(this.draft.pending.content, this.draft.pending.requestId)
  },
  openConversation(event: any) {
    const epoch = this.viewEpoch
    const id = event.currentTarget.dataset.id
    if (!viewActive(this, epoch) || this.openingConversation || !id) return
    if (this.data.liveMode && (!this.data.liveListKnown || !this.data.conversations.some((item: any) => item.conversationId === id))) return
    this.openingConversation = true
    patchView(this, { openingConversationId: String(id) }, epoch)
    wx.navigateTo({
      url: '/pages/conversation/index?id=' + encodeURIComponent(String(id)) + (this.data.liveMode ? '&mode=live&petId=' + encodeURIComponent(this.liveSession.getState().selectedPetId) : ''),
      fail: () => {
        if (!viewActive(this, epoch)) return
        this.openingConversation = false
        patchView(this, { openingConversationId: '' }, epoch)
      }
    })
  }
})
