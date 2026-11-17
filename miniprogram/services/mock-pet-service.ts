const { PET, PERSONALITIES } = require('../mocks/pet')
const { ACTIONS, ACTION_HISTORY } = require('../mocks/actions')
const { INTIMACY, GROWTH } = require('../mocks/growth')
const { CONVERSATIONS } = require('../mocks/conversations')
const { SCENARIOS } = require('../mocks/scenarios')
const { MOCK_SCENARIO_CONFIG } = require('../mocks/scenario-config')
const { getState, setScenario } = require('../store/app-store')

function copy(value: any) { return JSON.parse(JSON.stringify(value)) }
function wait(ms = 260) { return new Promise((resolve) => setTimeout(resolve, ms)) }

class UserFacingError extends Error {
  code: string
  constructor(code: string, message: string) {
    super(message)
    this.name = 'UserFacingError'
    this.code = code
  }
}

class MockPetService {
  private runningActionId = ''
  private actionExecution: any = null
  private actionRequests = new Map<string, any>()
  private dialogState = 'ready'
  private dialogUpdatedAt = Date.now()
  private volume = 62
  private followUpSeconds = 25
  private intimacy = copy(INTIMACY)
  private growth = { ...copy(GROWTH), debugValuesVisible: true }
  private conversations = copy(CONVERSATIONS)
  private actionHistory = copy(ACTION_HISTORY)
  private interactionHistory = [
    { id: 'i1', eventType: 'valid_dialog', title: '有效对话', value: '+5', time: '今天 14:21', reason: '完成了一次有效对话' },
    { id: 'i2', eventType: 'daily_check_in', title: '每日见面', value: '+3', time: '今天 08:05', reason: '今日首次见面' }
  ]
  private today = { met: true, conversations: 2, touchCount: 4, taskCompleted: false }

  prepareDevelopmentScenario() {
    if (!MOCK_SCENARIO_CONFIG.enabled) return
    this.clearActionExecution()
    const name = SCENARIOS[MOCK_SCENARIO_CONFIG.name] ? MOCK_SCENARIO_CONFIG.name : 'online_lv2'
    setScenario(name)
    const scenario = SCENARIOS[name]
    if (scenario.entry === 'logged_out') {
      wx.removeStorageSync('aiCatSession')
      wx.removeStorageSync('aiCatPet')
      wx.removeStorageSync('aiCatBlindBoxConfirmed')
      return
    }
    wx.setStorageSync('aiCatSession', { userId: 'aicat-user', displayName: '体验用户', avatarUrl: '' })
    if (scenario.entry === 'unbound') {
      wx.removeStorageSync('aiCatPet')
      wx.removeStorageSync('aiCatBlindBoxConfirmed')
      return
    }
    wx.setStorageSync('aiCatPet', { ...PET, ...(scenario.pet || {}) })
    if (scenario.entry === 'first_bind') wx.removeStorageSync('aiCatBlindBoxConfirmed')
    else wx.setStorageSync('aiCatBlindBoxConfirmed', true)
    this.runningActionId = scenario.runningActionId || ''
    this.dialogState = scenario.dialogState || 'ready'
  }

  private scenario() { return SCENARIOS[getState().scenario] || SCENARIOS.online_lv2 }

  async login(input: any = {}) {
    await wait(420)
    const session = {
      userId: String(input.loginCode || 'aicat-user'),
      displayName: String(input.nickname || '体验用户'),
      avatarUrl: ''
    }
    wx.setStorageSync('aiCatSession', session)
    return copy(session)
  }

  async getBoundPet() { await wait(100); return copy(wx.getStorageSync('aiCatPet') || null) }

  async bindPet(input: any) {
    await wait(850)
    const personality = PERSONALITIES[Math.floor(Math.random() * PERSONALITIES.length)]
    const deviceId = String(input.deviceSerial || input.deviceId || '').trim()
    const pet = {
      ...PET,
      deviceId,
      serialMasked: `${deviceId.slice(0, 3)}****${deviceId.slice(-4)}`,
      name: String(input.petName || input.name || '小安').trim(),
      networkName: String(input.networkName || '').trim(),
      personalityId: personality.id,
      personalityName: personality.name,
      personalityDescription: personality.description,
      languageStyle: personality.languageStyle
    }
    wx.setStorageSync('aiCatPet', pet)
    wx.removeStorageSync('aiCatBlindBoxConfirmed')
    return { pet: copy(pet), personality: copy(personality), blindBoxRevealed: true }
  }

  async getPersonalities() { await wait(120); return copy(PERSONALITIES) }

  async updatePersonality(petId: string, personalityId: string) {
    const pet = this.petOrThrow(petId)
    const personality = PERSONALITIES.find((item: any) => item.id === personalityId)
    if (!personality) throw new UserFacingError('PERSONALITY_NOT_FOUND', '没有找到这个陪伴性格')
    const updated = { ...pet, personalityId, personalityName: personality.name, personalityDescription: personality.description, languageStyle: personality.languageStyle }
    wx.setStorageSync('aiCatPet', updated)
    return copy(updated)
  }

  async getDashboard(petId: string) {
    await wait()
    const pet = this.petOrThrow(petId)
    return {
      pet,
      intimacy: copy(this.intimacy),
      today: { interactionCount: this.today.conversations + this.today.touchCount + (this.today.met ? 1 : 0), lastInteraction: '今天 14:22 认真点头' },
      recent: [
        { id: 'r1', type: '对话', title: '陪你聊了聊下午的项目汇报', time: '14:21' },
        { id: 'r2', type: '动作', title: '完成了“认真点头”', time: '14:22' },
        { id: 'r3', type: '成长', title: '共情倾向增加了 2 点', time: '昨天' }
      ]
    }
  }

  async getDeviceDetail(petId: string) {
    await wait()
    const pet = this.petOrThrow(petId)
    return { ...pet, adapterMode: 'mock', batteryVoltage: 4.18, batteryPresent: true, outputVolume: this.volume }
  }

  async updateDeviceStatus(petId: string, input: any, requestId: string) {
    const pet = this.petOrThrow(petId)
    const updated = {
      ...pet,
      connectionState: input.online === false ? 'offline' : input.connectionState,
      batteryPercent: Number(input.batteryPercent),
      batteryState: input.charging ? 'charging' : 'discharging',
      chargerOnline: Boolean(input.charging),
      lastSeenAt: new Date().toISOString()
    }
    wx.setStorageSync('aiCatPet', updated)
    await wait(360)
    return { requestId, pet: copy(updated) }
  }

  async reconnectDevice(petId: string) {
    const pet = this.petOrThrow(petId)
    await wait(620)
    wx.setStorageSync('aiCatPet', { ...pet, connectionState: 'online', lastSeenAt: new Date().toISOString() })
  }

  async getActions(petId: string) {
    this.petOrThrow(petId)
    await wait()
    this.refreshActionExecution()
    return copy(ACTIONS.map((item: any) => {
      const execution = this.actionExecution
      return execution && item.actionId === execution.actionId
        ? { ...item, state: 'running', ...this.actionSnapshot(execution) }
        : { ...item }
    }))
  }

  // Deadlines, not page-owned timers, are the authority for local action previews.
  // Reconcile on access so hidden pages do no work and overdue actions still finish.
  private refreshActionExecution() {
    if (this.actionExecution && this.runningActionId !== this.actionExecution.actionId) {
      this.settleActionExecution(this.actionExecution, '已取消')
    }
    if (!this.actionExecution && this.runningActionId) {
      const action = ACTIONS.find((item: any) => item.actionId === this.runningActionId)
      if (action) this.createActionExecution(action, 'scenario-action-' + Date.now())
      else this.runningActionId = ''
    }
    if (this.actionExecution && Date.now() >= this.actionExecution.deadlineAt) {
      this.settleActionExecution(this.actionExecution, '已完成', this.actionExecution.deadlineAt)
    }
  }

  private createActionExecution(action: any, requestId: string) {
    const startedAt = Date.now()
    const execution = {
      id: requestId, executionId: requestId, requestId, actionId: action.actionId,
      name: action.name, status: '执行中', time: '刚刚', startedAt,
      durationMs: 2400, deadlineAt: startedAt + 2400
    }
    this.runningActionId = action.actionId
    this.actionExecution = execution
    this.actionHistory.unshift(execution)
    this.actionHistory = this.actionHistory.slice(0, 40)
    this.actionRequests.set(requestId, execution)
    while (this.actionRequests.size > 80) this.actionRequests.delete(this.actionRequests.keys().next().value)
    return execution
  }

  private settleActionExecution(execution: any, status: string, finishedAt = Date.now()) {
    if (!execution || execution.status !== '执行中') return
    execution.status = status
    execution.finishedAt = finishedAt
    if (this.actionExecution === execution) {
      this.actionExecution = null
      this.runningActionId = ''
    }
  }

  private clearActionExecution() {
    this.settleActionExecution(this.actionExecution, '已取消')
    this.runningActionId = ''
    this.actionExecution = null
    this.actionRequests.clear()
  }

  private actionSnapshot(execution: any) {
    return {
      executionId: execution.executionId, actionId: execution.actionId,
      startedAt: execution.startedAt, deadlineAt: execution.deadlineAt, durationMs: execution.durationMs,
      status: execution.status === '执行中' ? 'running' : execution.status === '已完成' ? 'completed' : 'cancelled',
      progress: execution.status === '已完成' ? 100 : Math.min(99, Math.max(0, Math.floor((Date.now() - execution.startedAt) / execution.durationMs * 100)))
    }
  }

  async executeAction(petId: string, actionId: string, requestId: string) {
    const pet = this.petOrThrow(petId)
    this.refreshActionExecution()
    const duplicate = this.actionRequests.get(requestId)
    if (duplicate) {
      if (duplicate.actionId !== actionId) throw new UserFacingError('REQUEST_CONFLICT', '这个请求编号已用于其他动作')
      await wait(350)
      this.refreshActionExecution()
      return { ...this.actionSnapshot(duplicate), duplicate: true }
    }
    if (pet.connectionState === 'offline') throw new UserFacingError('OFFLINE', '设备暂时离线，请检查网络后重试')
    const action = ACTIONS.find((item: any) => item.actionId === actionId)
    if (!action || action.state !== 'available') throw new UserFacingError('ACTION_UNAVAILABLE', '这个动作现在还不可用')
    if (this.actionExecution) throw new UserFacingError('ACTION_BUSY', '请等待当前动作预览完成')
    // Claim synchronously before the simulated await to guard rapid duplicate taps.
    const execution = this.createActionExecution(action, requestId)
    await wait(350)
    this.refreshActionExecution()
    return { ...this.actionSnapshot(execution), duplicate: false }
  }

  async finishAction(executionId?: string) {
    this.refreshActionExecution()
    const execution = executionId ? this.actionRequests.get(executionId) : this.actionExecution
    await wait(180)
    this.refreshActionExecution()
    this.settleActionExecution(execution, '已完成')
  }

  async stopAction(petId: string, requestId: string, executionId?: string) {
    this.petOrThrow(petId)
    this.refreshActionExecution()
    const execution = executionId ? this.actionRequests.get(executionId) : this.actionExecution
    // Cancel the captured execution now; a later response must not touch a newer one.
    this.settleActionExecution(execution, '已取消')
    await wait(260)
    return { requestId, executionId: execution?.executionId, status: execution?.status }
  }

  async getActionHistory(petId: string) {
    this.petOrThrow(petId)
    await wait(100)
    this.refreshActionExecution()
    return copy(this.actionHistory.slice(0, 20))
  }

  async getIntimacy(petId: string) {
    this.petOrThrow(petId)
    await wait()
    return { ...copy(this.intimacy), ...(this.scenario().intimacy || {}), today: copy(this.today), recent: copy(this.interactionHistory) }
  }

  async recordInteraction(petId: string, eventType: string, requestId: string) {
    this.petOrThrow(petId)
    await wait(420)
    if (eventType === 'daily_check_in' && this.today.met) return { requestId, duplicate: true, pointsAdded: 0, reason: '今天已经见过面啦' }
    const points: any = { daily_check_in: 3, touch: 1, completed_task: 5, ignored_greeting: -1 }
    const names: any = { daily_check_in: '每日见面', touch: '摸摸它', completed_task: '一起完成任务', ignored_greeting: '错过问候' }
    const pointsAdded = points[eventType] || 0
    if (eventType === 'daily_check_in') this.today.met = true
    if (eventType === 'touch') this.today.touchCount += 1
    if (eventType === 'completed_task') this.today.taskCompleted = true
    this.intimacy.points = Math.max(0, this.intimacy.points + pointsAdded)
    this.intimacy.progressPercent = Math.min(100, Math.max(0, this.intimacy.progressPercent + pointsAdded))
    this.interactionHistory.unshift({ id: requestId, eventType, title: names[eventType] || eventType, value: `${pointsAdded > 0 ? '+' : ''}${pointsAdded}`, time: '刚刚', reason: '由小程序体验操作记录' })
    return { requestId, duplicate: false, pointsAdded, upgraded: Boolean(this.scenario().taskUpgraded) }
  }

  async completeTask(petId: string, requestId: string) { return this.recordInteraction(petId, 'completed_task', requestId) }

  async getGrowth(petId: string) {
    this.petOrThrow(petId)
    await wait()
    return { enabled: true, ...copy(this.growth), ...(this.scenario().growth || {}) }
  }

  async generateGrowth(petId: string, eventType: string, count: number, requestId: string) {
    this.petOrThrow(petId)
    await wait(520)
    const labels: any = { knowledge_discussion: '知识讨论', question: '提出问题', emotional_sharing: '情绪分享', encouragement: '鼓励互动', joke: '轻松玩笑', planning: '计划讨论', casual_chat: '日常闲聊' }
    const safeCount = Math.max(1, Math.min(500, Number(count)))
    this.growth.attributes = this.growth.attributes.map((attribute: any, index: number) => ({ ...attribute, value: Math.min(100, Number((attribute.value + safeCount * (index % 2 === 0 ? 0.12 : 0.08)).toFixed(2))) }))
    this.growth.recent.unshift({ id: requestId, reason: labels[eventType] || eventType, time: '刚刚', change: `生成 ${safeCount} 条成长事件` })
    if (this.growth.tags.length < 5) this.growth.tags.push('持续成长')
    return { requestId, processed: safeCount, growth: { enabled: true, ...copy(this.growth) } }
  }

  async getDialogStatus(petId: string) {
    const pet = this.petOrThrow(petId)
    await wait(80)
    const state = pet.connectionState === 'offline' ? 'offline' : (this.scenario().dialogState || this.dialogState)
    return {
      state,
      elapsedSeconds: Math.floor((Date.now() - this.dialogUpdatedAt) / 1000),
      followUpRemaining: state === 'waiting_follow_up' ? this.followUpSeconds : 0,
      sessionActive: ['listening', 'thinking', 'answering', 'waiting_follow_up'].includes(state),
      canInterrupt: ['answering', 'listening', 'thinking', 'waiting_follow_up'].includes(state),
      message: state === 'offline' ? '请检查设备网络' : '连续会话状态会自动更新'
    }
  }

  async startListening(petId: string, requestId: string) { this.petOrThrow(petId); this.dialogState = 'listening'; this.dialogUpdatedAt = Date.now(); await wait(260); return { requestId } }
  async interruptDialog(petId: string, requestId: string) { this.petOrThrow(petId); this.dialogState = 'ready'; this.dialogUpdatedAt = Date.now(); await wait(260); return { requestId } }

  async sendText(petId: string, content: string, requestId: string) {
    this.petOrThrow(petId)
    this.dialogState = 'thinking'
    this.dialogUpdatedAt = Date.now()
    await wait(620)
    if (this.scenario().failSendText) throw new UserFacingError('SEND_FAILED', '消息暂时没有发出，请重新发送')
    const now = new Date().toISOString()
    const response = '我听见了。我们可以慢慢把这件事说清楚。'
    this.conversations.unshift({
      conversationId: `conv-${Date.now()}`,
      status: 'complete', startedAt: now, updatedAt: now, messageCount: 2,
      userPreview: content, assistantPreview: response,
      messages: [
        { messageId: `${requestId}-u`, role: 'user', content, createdAt: now, deliveryState: 'sent' },
        { messageId: `${requestId}-a`, role: 'assistant', content: response, createdAt: now }
      ]
    })
    this.dialogState = 'waiting_follow_up'
    this.dialogUpdatedAt = Date.now()
    return { requestId, content }
  }

  async listConversations(petId: string, cursor?: string) {
    this.petOrThrow(petId)
    await wait()
    const items = this.scenario().conversations === undefined ? this.conversations : this.scenario().conversations
    return { items: copy(items), nextCursor: null, syncText: '已同步' }
  }

  async getConversation(petId: string, conversationId: string) {
    this.petOrThrow(petId)
    await wait()
    const conversation = this.conversations.find((item: any) => item.conversationId === conversationId) || this.conversations[0]
    return copy({ ...conversation, durationText: '3 分钟', sourceText: '文字互动' })
  }

  async getSettings() { await wait(100); return { volume: this.volume, followUpSeconds: this.followUpSeconds } }

  async updatePet(petId: string, input: any) {
    const pet = this.petOrThrow(petId)
    await wait(280)
    const updated = { ...pet, ...input }
    wx.setStorageSync('aiCatPet', updated)
    return copy(updated)
  }

  async updateVolume(value: number) { await wait(220); this.volume = value }
  async updateFollowUpSeconds(seconds: number) { await wait(220); this.followUpSeconds = seconds }

  async updateSettings(petId: string, input: any, requestId: string) {
    const pet = await this.updatePet(petId, { name: input.name })
    this.volume = input.volume
    this.followUpSeconds = input.followUpSeconds
    return { requestId, pet, volume: this.volume, followUpSeconds: this.followUpSeconds }
  }

  async submitFeedback(petId: string, input: any) { this.petOrThrow(petId); await wait(420); return { accepted: true, input } }

  async unbindPet(petId: string) {
    this.petOrThrow(petId)
    await wait(420)
    this.clearActionExecution()
    wx.removeStorageSync('aiCatPet')
    wx.removeStorageSync('aiCatBlindBoxConfirmed')
    wx.removeStorageSync('aiCatMijia')
  }

  async clearMemory(petId: string, requestId: string) {
    this.petOrThrow(petId)
    await wait(420)
    wx.setStorageSync('aiCatMemoryClearedAt', new Date().toISOString())
    return { requestId, shortTermMessagesRemoved: 16, longTermFactsRemoved: 4, conversationsPreserved: this.conversations.length }
  }

  async resetProductData(confirmation: string) {
    if (confirmation !== 'RESET_PRODUCT_DATA') throw new UserFacingError('CONFIRMATION_REQUIRED', '确认文字不正确')
    await wait(620)
    ;['aiCatSession', 'aiCatPet', 'aiCatBlindBoxConfirmed', 'aiCatMijia', 'aiCatMemoryClearedAt'].forEach((key) => wx.removeStorageSync(key))
    this.clearActionExecution()
    this.dialogState = 'ready'
    this.conversations = copy(CONVERSATIONS)
    return { backupId: `mini-backup-${Date.now()}` }
  }

  async getMijiaStatus(petId: string) {
    this.petOrThrow(petId)
    await wait(160)
    return copy(wx.getStorageSync('aiCatMijia') || { status: 'not_prepared', gatewayDeviceId: '', platformLinked: false, miotModel: null, specRevision: 'mijia-preparation-v1' })
  }

  async getMijiaSpec(petId: string) {
    this.petOrThrow(petId)
    await wait(160)
    return {
      properties: ['设备在线', '电量', '充电状态', '音量', '亲密等级', '亲密度', '对话状态', '动作状态'],
      actions: ['认真点头', '轻轻摇头', '开心摇尾', '停止动作', '开始聆听', '打断回答'],
      events: ['动作完成', '亲密等级变化', '设备异常']
    }
  }

  async prepareMijia(petId: string, requestId: string) {
    this.petOrThrow(petId)
    await wait(420)
    const current = wx.getStorageSync('aiCatMijia')
    const status = current && current.status === 'prepared' ? current : { status: 'prepared', gatewayDeviceId: `aicat_${Date.now().toString(36)}`, platformLinked: false, miotModel: null, specRevision: 'mijia-preparation-v1' }
    wx.setStorageSync('aiCatMijia', status)
    return { requestId, duplicate: Boolean(current && current.status === 'prepared'), status: copy(status) }
  }

  async removeMijia(petId: string, requestId: string) {
    this.petOrThrow(petId)
    await wait(420)
    wx.removeStorageSync('aiCatMijia')
    return { requestId }
  }

  private petOrThrow(petId?: string) {
    const stored = wx.getStorageSync('aiCatPet')
    if (!stored) throw new UserFacingError('PET_NOT_FOUND', '请先绑定宠物设备')
    const pet = { ...stored, ...(this.scenario().pet || {}) }
    if (petId && pet.petId !== petId) throw new UserFacingError('PET_NOT_FOUND', '暂时找不到这只宠物，请重新绑定')
    return copy(pet)
  }
}

module.exports = { MockPetService, UserFacingError }
