const { getLiveDeviceSession } = require('../../services/live-device-session')
const { createLocalNetwork, clearLocalProvisionHandoff } = require('../../services/local-network')
const { localRuntimeAvailable } = require('../../services/local-device-client')
const { localNetworkView } = require('../../utils/local-network-view')
function current(page: any, visit: number) { return page.visible && page.visit === visit }
const FLOW_STATUSES = ['accepted', 'connecting', 'connected', 'failed', 'unknown', 'not_sent']
const FLOW_PHASES = ['idle', 'pending', 'scanning', 'scanning-receipt', 'checking', 'preparing', 'connected', 'error', 'archived']
const CLOSEOUT_ACKS = ['window-ended', 'network-stable', 'device-idle', 'accept-unknown']
function closeoutAckData(value: any) {
  const valid = Array.isArray(value) && value.every(item => CLOSEOUT_ACKS.includes(item)) && new Set(value).size === value.length
  const acks = valid ? CLOSEOUT_ACKS.filter(item => value.includes(item)) : []
  return { closeoutAcks: acks, closeoutWindowEnded: acks.includes('window-ended'), closeoutNetworkStable: acks.includes('network-stable'),
    closeoutDeviceIdle: acks.includes('device-idle'), closeoutAcceptUnknown: acks.includes('accept-unknown') }
}
function closeoutSignature(value: any) {
  const marker = value && value.marker, record = value && value.record
  return JSON.stringify([value && value.paired, value && value.deviceAddress, marker && [marker.deviceId, marker.cardId, marker.requestId, marker.windowId],
    record && [record.deviceId, record.requestId, record.windowId, record.networkRequestId], value && value.closeout && value.closeout.original || null])
}

Page({
  data: { available: false, network: {}, wizard: {}, error: '', confirming: false,
    showDetails: false, showHelp: false, pairLocation: 'station', pairPrompt: false, pairingBusy: false,
    flowMode: '', flowRevision: 0, flowState: {}, flowComplete: false,
    closeoutPreparing: false, closeoutPosting: false, closeoutReviewRequired: false, closeoutMessage: '', ...closeoutAckData([]) },
  async onShow() {
    clearLocalProvisionHandoff()
    this.visible = true; this.visit = (this.visit || 0) + 1
    this.intent = null
    this.closeoutAttempt = null; this.closeoutRevision = (this.closeoutRevision || 0) + 1; this.closeoutLastSignature = ''
    if (!this.network) this.network = createLocalNetwork(wx, getLiveDeviceSession())
    this.setData({ available: localRuntimeAvailable(wx), error: '', confirming: false,
      flowMode: '', flowState: {}, flowComplete: false, pairingBusy: false,
      closeoutPreparing: false, closeoutPosting: false, closeoutReviewRequired: false, closeoutMessage: '', ...closeoutAckData([]) })
    this.network.show((value: any) => { if (this.visible) this.renderWizard(value) })
    this.focusCurrentStep()
    await this.refreshWindow()
  },
  onHide() { this.pause() }, onUnload() { this.pause(); this.clearHotspotResume() },
  clearHotspotResume(resetLocation = false) {
    clearTimeout(this.hotspotResumeTimer); this.hotspotResumeTimer = null; this.hotspotResumeHint = null; this.expiredHotspotWindow = null
    if (resetLocation) this.setData({ pairLocation: 'station', pairPrompt: false })
  },
  rememberOpenedHotspot(value: any) {
    const record = value && value.record, saved = value && value.marker
    // Only the just-confirmed response can create this RAM-only address hint.
    // A persisted marker or a cold visit is never proof that an AP is active.
    this.clearHotspotResume()
    if (!value || !value.paired || value.error || value.legacyBlocked || !saved || saved.settled || !record ||
        !['prepared', 'opening', 'open'].includes(record.status) || record.networkRequestId !== null ||
        record.deviceId !== saved.deviceId || record.requestId !== saved.requestId || record.windowId !== saved.windowId ||
        !Number.isFinite(record.expiresAt) || record.expiresAt * 1000 <= Date.now()) return
    this.hotspotResumeHint = { deviceId: saved.deviceId, cardId: saved.cardId, requestId: saved.requestId,
      windowId: saved.windowId, expiresAt: record.expiresAt }
    this.setData({ pairLocation: 'hotspot', pairPrompt: true })
  },
  hotspotResumeCurrent(value: any) {
    const hint = this.hotspotResumeHint || this.expiredHotspotWindow
    if (!hint) return false
    const saved = value && value.marker, record = value && value.record
    let session: any = null
    try { session = getLiveDeviceSession().getState() } catch (_) {}
    const context = session && session.localContext
    if (record && Number.isFinite(record.expiresAt)) hint.expiresAt = Math.min(hint.expiresAt, record.expiresAt)
    const expired = Boolean(this.expiredHotspotWindow) || hint.expiresAt * 1000 <= Date.now()
    if (!saved || saved.settled || value.legacyBlocked ||
        saved.deviceId !== hint.deviceId || saved.cardId !== hint.cardId || saved.requestId !== hint.requestId || saved.windowId !== hint.windowId ||
        !session || session.mode !== 'live' || session.authKind !== 'local' || !context || context.deviceId !== hint.deviceId || context.cardId !== hint.cardId ||
        record && (record.deviceId !== hint.deviceId || record.requestId !== hint.requestId || record.windowId !== hint.windowId ||
          record.networkRequestId !== null || !['prepared', 'opening', 'open'].includes(record.status)) ||
        this.data.flowState.pending === true || ['accepted', 'connecting', 'connected', 'failed', 'unknown'].includes(this.data.flowState.status)) {
      this.clearHotspotResume(true)
      return false
    }
    if (expired) {
      this.clearHotspotResume(true)
      // Keep only a same-window RAM cutoff, never an address/authorization hint.
      // Even a later deadline in refreshed metadata cannot revive this window.
      this.expiredHotspotWindow = hint
      this.setData({ error: '本次热点窗口已到期；请连接机器恢复的普通 Wi-Fi，只读核对原窗口，不重复开启或发送资料。' })
      return false
    }
    // Refreshing and returning never renew the original deadline.
    clearTimeout(this.hotspotResumeTimer)
    this.hotspotResumeTimer = setTimeout(() => { this.hotspotResumeTimer = null; if (this.visible) this.renderWizard() }, Math.max(1, hint.expiresAt * 1000 - Date.now()))
    return true
  },
  pause() {
    clearTimeout(this.hotspotResumeTimer); this.hotspotResumeTimer = null
    this.revokeCloseout(false)
    let innerPending = this.data.flowState.pending === true
    try { innerPending = innerPending || this.network && this.network.getState().legacyBlocked === true } catch (_) {}
    const pairLocation = this.data.flowMode
      ? innerPending || ['accepted', 'connecting', 'connected', 'failed', 'unknown'].includes(this.data.flowState.status) ? 'station' : 'hotspot'
      : this.data.pairLocation
    this.visible = false; this.visit = (this.visit || 0) + 1
    if (this.network && this.intent) this.network.cancelOpening(this.intent)
    this.intent = null
    clearLocalProvisionHandoff()
    if (this.network) this.network.hide()
    this.setData({ flowMode: '', flowState: {}, flowComplete: false, confirming: false, pairingBusy: false, pairLocation,
      closeoutPreparing: false, closeoutPosting: false, ...closeoutAckData([]) })
  },
  renderWizard(nextNetwork?: any) {
    if (!this.visible) return
    const value = nextNetwork || (this.network ? this.network.getState() : this.data.network)
    const resumeHotspot = this.hotspotResumeCurrent(value)
    const signature = closeoutSignature(value), closeout = value.closeout || {}
    const changed = this.closeoutLastSignature && this.closeoutLastSignature !== signature
    this.closeoutLastSignature = signature
    const revoked = !value.paired || !closeout.loading && !closeout.submitting && !closeout.archived && !closeout.eligible && !closeout.remoteArchived && closeout.phase !== 'prepared'
    if ((changed || revoked) && (!this.closeoutAttempt || !this.closeoutAttempt.submitted) &&
        (this.closeoutAttempt || this.data.closeoutAcks.length)) this.revokeCloseout(true)
    this.setData({ network: value, wizard: localNetworkView(value, {
      available: this.data.available, confirming: this.data.confirming || this.data.pairingBusy,
      pairLocation: this.data.pairLocation, pairPrompt: this.data.pairPrompt,
      resumeHotspot, hotspotWindowExpired: Boolean(this.expiredHotspotWindow),
      flowMode: this.data.flowMode, flowPending: this.data.flowState.pending,
      flowStatus: this.data.flowState.status, closeoutReviewRequired: this.data.closeoutReviewRequired }) })
  },
  revokeCloseout(updateView = true) {
    const attempt = this.closeoutAttempt
    this.closeoutAttempt = null; this.closeoutRevision = (this.closeoutRevision || 0) + 1
    if (attempt && attempt.token && this.network && typeof this.network.cancelCloseout === 'function') {
      try { this.network.cancelCloseout(attempt.token) } catch (_) { /* No raw error or token enters the view. */ }
    }
    if (updateView && this.visible) this.setData({ confirming: Boolean(this.intent), closeoutPreparing: false, closeoutPosting: false, ...closeoutAckData([]) })
  },
  closeoutFactsChanged(event: any) {
    if (!this.visible || !this.data.available || this.data.flowMode || !this.data.wizard.showCloseout || this.data.closeoutPosting || this.data.network.closeout && this.data.network.closeout.submitting ||
        !this.closeoutAttempt && !this.data.wizard.closeoutChecksEnabled) return
    const facts = closeoutAckData(event && event.detail && event.detail.value)
    if (JSON.stringify(facts.closeoutAcks) !== JSON.stringify(this.data.closeoutAcks)) this.revokeCloseout(true)
    this.setData(facts); this.renderWizard()
  },
  async refreshCloseout() {
    if (!this.visible || !this.data.available || this.data.flowMode || this.data.confirming || this.data.pairingBusy ||
        !this.data.wizard.showCloseout || !this.data.wizard.closeoutReviewEnabled || typeof this.network.refreshCloseout !== 'function') return
    const visit = this.visit
    this.revokeCloseout(true); this.setData({ closeoutMessage: '' })
    try {
      await this.network.refreshCloseout()
      if (!current(this, visit)) return
      const value = this.network.getState(), closeout = value.closeout || {}
      this.setData({ closeoutReviewRequired: !['ready', 'recovering', 'archived'].includes(closeout.phase) })
      this.renderWizard(value)
    } catch (_) { if (current(this, visit)) { this.setData({ closeoutReviewRequired: true, closeoutMessage: '收尾条件尚未核实；保留原记录，只读核对后再继续。' }); this.renderWizard() } }
  },
  async requestCloseout() {
    if (!this.visible || !this.data.available || this.data.flowMode || this.data.confirming || this.data.pairingBusy ||
        !this.data.wizard.showCloseout || !this.data.wizard.closeoutChecksEnabled || this.data.closeoutAcks.length !== CLOSEOUT_ACKS.length ||
        typeof this.network.prepareCloseout !== 'function' || typeof this.network.confirmCloseout !== 'function') return
    const visit = this.visit, acks = this.data.closeoutAcks.slice(), signature = closeoutSignature(this.network.getState())
    const attempt: any = { visit, revision: (this.closeoutRevision || 0) + 1, signature, acks, token: null, submitted: false }
    this.closeoutRevision = attempt.revision; this.closeoutAttempt = attempt
    const valid = () => current(this, visit) && this.closeoutAttempt === attempt && this.closeoutRevision === attempt.revision &&
      JSON.stringify(this.data.closeoutAcks) === JSON.stringify(acks) && closeoutSignature(this.network.getState()) === signature
    const cancel = () => {
      if (attempt.token) { try { this.network.cancelCloseout(attempt.token) } catch (_) {} }
      if (this.closeoutAttempt === attempt) { this.closeoutAttempt = null; if (current(this, visit)) { this.setData({ confirming: false, closeoutPreparing: false, closeoutPosting: false, ...closeoutAckData([]) }); this.renderWizard() } }
    }
    this.setData({ confirming: true, closeoutPreparing: true, closeoutMessage: '' }); this.renderWizard()
    try {
      attempt.token = await this.network.prepareCloseout(acks)
      if (!attempt.token || !valid()) { cancel(); return }
      const prepared = this.network.getState().closeout || {}
      if (prepared.phase !== 'prepared' || prepared.archived || prepared.submitting) { cancel(); return }
      this.setData({ closeoutPreparing: false })
      let answered = false
      wx.showModal({ title: prepared.remoteArchived ? '完成手机端归档？' : '结束本次测试并归档？', confirmText: '确认归档', cancelText: '暂不归档',
        content: '这是对原设备、原窗口、原请求的人工收尾，不代表联网成功或失败。你确认旧测试和热点已结束，当前普通网络稳定，机器没有动作或语音会话；接受结果仍未知、网络资料可能已保存。旧记录会保留，旧窗口和二维码不能再用；本次不会开启热点或发送 Wi-Fi。确认归档？',
        success: async (answer: any) => {
          if (answered) return
          answered = true
          if (!valid() || !answer || answer.confirm !== true) { cancel(); return }
          attempt.submitted = true
          this.setData({ closeoutPosting: true })
          try {
            const archived = await this.network.confirmCloseout(attempt.token)
            if (!current(this, visit) || this.closeoutAttempt !== attempt) return
            const verified = archived === true && this.network.getState().closeout && this.network.getState().closeout.archived === true
            this.setData({ closeoutReviewRequired: !verified,
              closeoutMessage: verified ? '两端旧记录已归档，结果仍未知。没有开启新窗口或发送网络资料。' : '收尾尚未完全核实；只读核对原记录，不重复提交归档或重新配网。' })
          } catch (_) {
            if (current(this, visit) && this.closeoutAttempt === attempt) this.setData({ closeoutReviewRequired: true, closeoutMessage: '归档结果待核验；原编号保留，请先只读核对，不重复发送。' })
          } finally {
            if (current(this, visit) && this.closeoutAttempt === attempt) {
              this.closeoutAttempt = null
              this.setData({ confirming: false, closeoutPosting: false, ...closeoutAckData([]) }); this.renderWizard()
            }
          }
        },
        fail: () => { if (answered) return; answered = true; cancel() }
      })
    } catch (_) {
      if (valid()) this.setData({ closeoutReviewRequired: true, closeoutMessage: '本次收尾确认未准备好；保留原编号，请只读核对后再继续。' })
      cancel()
    }
  },
  focusCurrentStep() { if (this.visible && typeof wx.pageScrollTo === 'function') { try { wx.pageScrollTo({ scrollTop: 0, duration: 150 }) } catch (_) {} } },
  focusCloseout() { if (this.visible && !this.data.flowMode && this.data.wizard.showCloseout && typeof wx.pageScrollTo === 'function') { try { wx.pageScrollTo({ selector: '.closeout-card', duration: 150 }) } catch (_) {} } },
  toggleDetails() { if (this.visible) this.setData({ showDetails: !this.data.showDetails }) },
  toggleHelp() { if (this.visible) this.setData({ showHelp: !this.data.showHelp }) },
  async primaryAction() {
    if (!this.visible || this.data.flowMode || !this.data.wizard.actionEnabled) return
    const action = this.data.wizard.action
    if (['openWindow', 'refreshWindow', 'continueConfiguration', 'verifyReceipt', 'pairHotspot', 'pairStation', 'refreshCloseout', 'focusCloseout'].includes(action)) return this[action]()
  },
  async paired() {
    if (!this.visible || this.data.confirming || this.data.flowMode) return
    this.setData({ pairPrompt: false, pairingBusy: false, error: '' }); this.renderWizard()
    await this.refreshWindow()
  },
  pairStateChanged(event: any) {
    if (!this.visible || this.data.flowMode) return
    this.setData({ pairingBusy: Boolean(event && event.detail && event.detail.busy === true) })
    this.renderWizard()
  },
  async openWindow() {
    if (!this.visible || !this.data.available || !this.data.network.canPrepare || this.data.confirming || this.data.pairingBusy || this.data.flowMode) return
    const visit = this.visit
    let token: any
    try { token = await this.network.prepareOpening() }
    catch (_) { if (current(this, visit)) this.setData({ error: '本次开启确认未准备好；请核对本地配对及原未决记录，不会换号重试。' }); return }
    if (!current(this, visit)) { this.network.cancelOpening(token); return }
    this.intent = token
    this.setData({ confirming: true, error: '' })
    this.renderWizard()
    let answered = false
    const cancel = () => { this.network.cancelOpening(token); if (this.intent === token) this.intent = null }
    try {
      wx.showModal({ title: '开启猫端配网窗口？', confirmText: '开启窗口', cancelText: '取消',
        content: '将暂时断开猫当前 Wi-Fi，开启约180秒的本次热点；云端语音和网页可能中断。超时由原网络检查点恢复，不会修改大模型密钥、固件或电机参数。之后仍须你手动连接热点、重扫同一机身卡并单独确认目标 Wi-Fi。只开启一次？',
        success: async (answer: any) => {
          if (answered) return
          answered = true
          if (!current(this, visit) || this.intent !== token) { cancel(); return }
          this.intent = null; this.setData({ confirming: false }); this.renderWizard()
          if (!answer || answer.confirm !== true) { cancel(); this.renderWizard(); return }
          try {
            await this.network.confirmOpening(token)
            if (current(this, visit)) { const value = this.network.getState(); this.rememberOpenedHotspot(value); this.renderWizard(value) }
          }
          catch (_) { if (current(this, visit)) this.setData({ error: '本次确认未能提交或原编号保存未核实；保留记录，不自动再开窗口。' }) }
        },
        fail: () => { if (answered) return; answered = true; cancel(); if (current(this, visit)) { this.setData({ confirming: false, error: '确认窗口未完成，本次未提交开启请求。' }); this.renderWizard() } }
      })
    } catch (_) { if (!answered) { answered = true; cancel(); if (current(this, visit)) { this.setData({ confirming: false, error: '确认窗口不可用，本次未提交开启请求。' }); this.renderWizard() } } }
  },
  async refreshWindow() {
    if (!this.visible || !this.data.available || !this.data.network.canRead || this.data.confirming || this.data.pairingBusy || this.data.flowMode) return
    this.revokeCloseout(true)
    const visit = this.visit
    this.setData({ error: '' })
    try { await this.network.refresh() }
    catch (_) { if (current(this, visit)) this.setData({ error: '原窗口暂不可读；请重扫同一机身卡并核对猫当前地址，不清原记录。' }) }
  },
  async transfer(mode: string) {
    if (!this.visible || !this.data.available || this.data.confirming || this.data.pairingBusy || this.data.flowMode ||
        mode === 'configure' && this.expiredHotspotWindow ||
        !(mode === 'configure' ? this.data.network.canConfigure : mode === 'receipt' && this.data.network.canReceipt)) return
    this.revokeCloseout(true)
    const visit = this.visit
    this.setData({ error: '' })
    try {
      if (!await this.network.openProvision(mode) || !current(this, visit)) return
      this.setData({ flowMode: mode, flowRevision: this.data.flowRevision + 1,
        flowState: { busy: true, pending: false, status: '', phase: '' }, flowComplete: false, pairPrompt: false })
      this.focusCurrentStep()
    } catch (_) {
      clearLocalProvisionHandoff()
      if (current(this, visit)) this.setData({ error: '私有配网交接未完成；保留原编号，不重开窗口或重发网络资料。' })
    }
  },
  continueConfiguration() { return this.transfer('configure') },
  verifyReceipt() { return this.transfer('receipt') },
  pairHotspot() {
    if (this.visible && this.data.available && !this.data.confirming && !this.data.pairingBusy && !this.data.network.busy && !this.data.flowMode) {
      this.revokeCloseout(true)
      this.setData({ pairLocation: 'hotspot', pairPrompt: true, error: '' }); this.renderWizard()
    }
  },
  pairStation() {
    if (this.visible && this.data.available && !this.data.confirming && !this.data.pairingBusy && !this.data.network.busy && !this.data.flowMode) {
      this.revokeCloseout(true)
      this.clearHotspotResume()
      this.setData({ pairLocation: 'station', pairPrompt: true, error: '' }); this.renderWizard()
    }
  },
  flowStateChanged(event: any) {
    if (!this.visible || !this.data.flowMode) return
    const value = event && event.detail || {}
    const status = FLOW_STATUSES.includes(value.status) ? value.status : ''
    const phase = FLOW_PHASES.includes(value.phase) ? value.phase : ''
    this.setData({ flowState: { busy: value.busy === true, pending: value.pending === true, status, phase } })
    this.renderWizard()
  },
  flowTerminal(event: any) {
    this.flowStateChanged(event)
    const value = this.data.flowState
    if (this.visible && this.data.flowMode) this.setData({ flowComplete: value.busy === false && value.pending === false && ['connected', 'failed', 'not_sent'].includes(value.status) })
  },
  async flowBack() {
    if (!this.visible || !this.data.flowMode || this.data.flowState.busy === true) return
    const sent = this.data.flowState.pending === true || ['accepted', 'connecting', 'connected', 'failed', 'unknown'].includes(this.data.flowState.status)
    clearLocalProvisionHandoff()
    this.setData({ flowMode: '', flowState: {}, flowComplete: false,
      pairLocation: sent ? 'station' : 'hotspot', pairPrompt: false, error: '' })
    this.renderWizard()
    this.focusCurrentStep()
    await this.refreshWindow()
  },
  flowUnavailable() {
    if (!this.visible || !this.data.flowMode) return
    clearLocalProvisionHandoff()
    this.setData({ flowMode: '', flowState: {}, flowComplete: false,
      error: '本次填写界面未能准备好，私有材料已清空。请核对原窗口后继续，不重新发送。' })
    this.renderWizard()
  },
  returnToControl() {
    if (!this.visible || this.data.flowMode || !this.data.wizard.controlExitEnabled) return
    // Navigation only; being reachable does not settle the Wi-Fi request.
    wx.redirectTo({ url: '/pages/motion/index' })
  },
  finish() {
    if (!this.visible || !this.data.wizard.complete || this.data.network.busy || this.data.flowMode) return
    wx.navigateBack({ delta: 1, fail: () => { if (this.visible) wx.redirectTo({ url: '/pages/device/index' }) } })
  }
})
