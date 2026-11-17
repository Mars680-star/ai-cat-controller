const { createRequestId } = require('../../utils/request-id')
const { livePageData, livePageMethods, attachLivePage, detachLivePage } = require('../../utils/live-page')
const { createLiveMotion, createLiveMotionHistory, MOTION_NAMES, MOTION_STATES } = require('../../services/live-motion')

// Canonical product catalog only. Availability always comes from the verified
// server policy, never local demo levels, personality or capability assumptions.
const ACTION_CARDS = [
  { actionId: 'head_shake', name: '轻轻摇头', description: '左右摇摇头，回应你的呼唤', icon: '/assets/icons/move.png', connected: true },
  { actionId: 'head_nod', name: '认真点头', description: '认真地点点头，表示它在听', icon: '/assets/icons/check.png', connected: true },
  { actionId: 'tail_wag', name: '开心摇尾', description: '开心时晃动小尾巴', icon: '/assets/icons/heart.png', connected: true },
  { actionId: 'proud_pose', name: '骄傲转身', description: '侧头轻摇尾巴，短暂停留后回正', icon: '/assets/icons/sprout.png', connected: true },
  { actionId: 'quiet_companion', name: '安静陪伴', description: '放松下来，安静待在你身边', icon: '/assets/icons/heart.png', connected: true },
  { actionId: 'greeting_combo', name: '见面问候', description: '用热情的动作欢迎你回来', icon: '/assets/icons/house.png', connected: true },
  { actionId: 'celebration_combo', name: '升级庆祝', description: '在亲密度升级时一起庆祝', icon: '/assets/icons/zap.png', connected: true }
]
const initialCards = () => ACTION_CARDS.map(item => ({ ...item, disabled: true, badge: '等待连接', reason: '' }))
function displayTime(value: string) {
  const date = new Date(value)
  const pad = (part: number) => String(part).padStart(2, '0')
  return date.getFullYear() + '-' + pad(date.getMonth() + 1) + '-' + pad(date.getDate()) + ' ' + pad(date.getHours()) + ':' + pad(date.getMinutes()) + ':' + pad(date.getSeconds())
}
function sameRenderedValue(left: any, right: any): boolean {
  if (left === right) return true
  if (!left || !right || typeof left !== 'object' || typeof right !== 'object') return false
  if (Array.isArray(left) !== Array.isArray(right)) return false
  const keys = Object.keys(left)
  return keys.length === Object.keys(right).length && keys.every(key =>
    Object.prototype.hasOwnProperty.call(right, key) && sameRenderedValue(left[key], right[key]))
}
function setChangedData(page: any, next: any) {
  // Compare current data, not a cached callback: user events can change it too.
  // Poll/loading updates must not resend unchanged cards and history over the bridge.
  const patch: any = {}
  for (const key of Object.keys(next)) {
    if (!sameRenderedValue(page.data[key], next[key])) patch[key] = next[key]
  }
  if (Object.keys(patch).length) page.setData(patch)
}

Page({
  openLocalPair() { if (this.pageVisible) wx.navigateTo({ url: '/pages/local-pair/index' }) },
  liveLogin: livePageMethods.liveLogin,
  liveList: livePageMethods.liveList,
  liveLogout: livePageMethods.liveLogout,
  data: {
    ...livePageData,
    motionSnapshot: null, motionLoading: false, motionSubmitting: false, motionStopping: false,
    motionError: '', motionUncertain: false, motionStatus: '连接设备后即可控制', motionActions: [],
    motionCanStop: false, motionCanRetry: false,
    actions: initialCards(), connectedCount: 7, availableCount: 0, unavailableCount: 7,
    history: null, historyLoading: false, historyError: '', selectedName: '', sampledTime: '', executionName: ''
  },
  onShow() {
    this.visible = true; this.pageVisible = true
    this.motionGuardNotice = ''; this.historySignature = ''
    attachLivePage(this)
    if (!this.realHistory) this.realHistory = createLiveMotionHistory(this.liveSession)
    this.realHistory.show((value: any) => this.renderHistory(value))
    if (!this.realMotion) this.realMotion = createLiveMotion(this.liveSession, createRequestId)
    this.realMotion.show((value: any) => this.renderLiveMotion(value))
    // Enter the real read-only lane, never perform wx.login or a command on show.
    if (this.liveSession.getState().mode !== 'live') this.liveSession.enable()
    return Promise.resolve()
  },
  onHide() {
    this.visible = false; this.pageVisible = false
    if (this.realMotion) this.realMotion.hide()
    if (this.realHistory) this.realHistory.hide()
    detachLivePage(this)
  },
  onUnload() { this.onHide() },
  async onPullDownRefresh() {
    try { await this.refreshAll() }
    finally { if (this.visible) wx.stopPullDownRefresh() }
  },
  liveSelect(event: any) {
    if (!this.visible) return Promise.resolve()
    const petId = event && event.currentTarget && event.currentTarget.dataset.petId
    return this.liveSession.selectPet(petId)
  },
  refreshConnection() {
    if (!this.visible) return Promise.resolve()
    const state = this.liveSession.getState()
    return !state.signedIn ? this.liveSession.checkHealth() : this.liveSession.refreshPets()
  },
  refreshAll() {
    if (!this.visible) return Promise.resolve()
    const state = this.liveSession.getState()
    return !state.signedIn || !state.selectedPetId ? this.refreshConnection() :
      Promise.all([this.liveMotionRefresh(), this.refreshHistory()])
  },
  onLiveModeChanged() {
    this.motionGuardNotice = ''; this.historySignature = ''
    this.setData({ actions: initialCards(), connectedCount: 7, availableCount: 0, unavailableCount: 7, history: null, historyError: '', selectedName: '' })
  },
  renderHistory(value: any) {
    if (!this.visible) return
    const history = value.items === null ? null : value.items.map((item: any) => ({
      ...item, name: item.actionId === 'stop' ? '停止动作' : MOTION_NAMES[item.actionId],
      statusText: MOTION_STATES[item.status], time: displayTime(item.updatedAt),
      badgeState: ['sending', 'running'].includes(item.status) ? 'running' : 'unavailable'
    }))
    setChangedData(this, { history, historyLoading: value.loading, historyError: value.error })
  },
  refreshHistory() { if (this.visible && this.realHistory) return this.realHistory.read(); return Promise.resolve() },
  renderLiveMotion(value: any) {
    if (!this.visible) return
    const snapshot = value.snapshot, execution = snapshot && snapshot.execution
    const stopLocked = !snapshot || !snapshot.enabled || !snapshot.verified
    const locked = stopLocked || snapshot.deviceMotionEnabled === false
    const busy = Boolean(snapshot && snapshot.deviceBusy)
    const active = Boolean(execution && ['sending', 'running', 'unknown'].includes(execution.status))
    const cooling = Boolean(snapshot && snapshot.verified && snapshot.actions.some((item: any) => !item.available && item.reason === '动作冷却中'))
    const state = this.liveSession.getState()
    const petId = state.selectedPetId
    if (this.motionHistoryPet !== petId) { this.motionHistoryPet = petId; this.historySignature = '' }
    const selected = state.pets.find((pet: any) => pet.petId === petId)
    const status = !state.signedIn ? '登录后连接你的设备' : !petId ? '请选择已授权设备' :
      !snapshot ? value.error ? '动作状态未验证' : '等待真实动作状态' :
      !snapshot.enabled ? '动作接口已准备，等待现场试动确认' : !snapshot.verified ? '设备状态未验证，禁止执行动作' : snapshot.deviceMotionEnabled === false ? '设备动作已暂停，停止通道独立保留' :
      value.stopping ? '正在发送停止请求' : value.submitting ? '正在发送动作' :
      execution && execution.status === 'unknown' ? '结果待核实，已暂停新动作' : value.uncertain ? '结果不确定，请停止并检查' : execution && execution.status === 'stopped' && busy ? '停止请求已确认，仍需现场核查' : cooling && !busy && !active ? '短暂冷却中，请稍候再点' : execution ? MOTION_STATES[execution.status] : busy ? '设备忙，请稍后手动重试' : '准备好动一动了'
    const motionActions = (snapshot ? snapshot.actions : []).map((item: any) => ({
      actionId: item.actionId, name: MOTION_NAMES[item.actionId], reason: item.reason,
      disabled: Boolean(locked || busy || active || value.uncertain || value.submitting || value.stopping || !item.available)
    }))
    const catalogCards = ACTION_CARDS
    const actions = catalogCards.map(item => {
      const live = motionActions.find((action: any) => action.actionId === item.actionId)
      const disabled = !live || live.disabled
      const badge = !state.signedIn || !petId ? '等待连接' : locked ? '暂不可执行' :
        execution && execution.actionId === item.actionId && active ? execution.status === 'unknown' ? '待核对' : '执行中' : value.uncertain ? '待现场核查' : disabled ? '暂不可执行' : '可执行'
      const reason = !state.signedIn || !petId ? '' : disabled && live ? live.reason ||
        (locked ? '设备状态尚未验证' : value.uncertain ? '上一请求结果不确定，请停止并检查' : busy || active ? '当前动作结束后再点，不会排队' : value.submitting || value.stopping ? '已有请求正在处理' : '请刷新动作状态后重试') : !live && snapshot && snapshot.verified && snapshot.hasTail === false ? '该设备没有尾部机构' : ''
      return { ...item, disabled, badge, reason, running: Boolean(execution && execution.actionId === item.actionId && ['sending', 'running'].includes(execution.status)) }
    })
    setChangedData(this, {
      motionSnapshot: snapshot, motionLoading: value.loading, motionSubmitting: value.submitting,
      motionStopping: value.stopping, motionError: this.motionGuardNotice || value.error, motionUncertain: value.uncertain,
      motionStatus: status, motionActions, actions, availableCount: motionActions.filter((item: any) => !item.disabled).length,
      connectedCount: catalogCards.length, unavailableCount: catalogCards.length - motionActions.filter((item: any) => !item.disabled).length,
      selectedName: selected ? selected.name : '', sampledTime: snapshot ? displayTime(snapshot.sampledAt) : '',
      executionName: execution ? execution.actionId === 'stop' ? '停止动作' : MOTION_NAMES[execution.actionId] : '',
      motionCanStop: Boolean(!stopLocked && !value.stopping && (busy || active || value.submitting || value.uncertain)),
      motionCanRetry: Boolean(!locked && !value.submitting && !value.stopping && value.uncertain && value.error && value.retryAvailable) })
    const signature = petId + '|' + (execution ? [execution.executionId, execution.status, execution.updatedAt].join('|') : '')
    if (snapshot && signature !== this.historySignature) {
      const changed = Boolean(this.historySignature)
      this.historySignature = signature
      this.realHistory.read(changed)
    }
  },
  liveMotionRefresh() {
    if (this.visible && this.realMotion) { this.motionGuardNotice = ''; return this.realMotion.read() }
    return Promise.resolve()
  },
  liveExecute(event: any) {
    if (!this.visible || !this.data.liveMode || this.data.motionSubmitting || this.data.motionStopping) return Promise.resolve()
    const actionId = event && event.currentTarget && event.currentTarget.dataset.actionId
    const action = this.data.motionActions.find((item: any) => item.actionId === actionId && !item.disabled)
    if (!action) return Promise.resolve()
    this.motionGuardNotice = ''
    // A card tap is the user's explicit one-shot intent. Keep the controller's
    // single-use identity/selection guard, with no second modal or queued tap.
    const token = this.realMotion.prepare(actionId)
    return token ? this.realMotion.confirm(token) : Promise.resolve()
  },
  liveRetryMotion() {
    if (!this.visible || !this.data.liveMode || !this.data.motionCanRetry) return Promise.resolve()
    this.motionGuardNotice = ''
    return this.realMotion.retryExecute()
  },
  liveStopMotion() {
    if (!this.visible || !this.data.liveMode) return
    if (!this.data.motionCanStop) {
      // A native tap can arrive just after the rendered permission was revoked.
      // Keep the page guard, but do not make that rejected stop look successful.
      if (!this.data.motionStopping) setChangedData(this, {
        motionError: '停止请求未发送：当前状态不允许提交，请刷新动作状态并现场检查设备'
      })
      return
    }
    return this.realMotion.stop()
  }
})
