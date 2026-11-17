// Product data is a separate, view-scoped lane. No credentials, demo fallback,
// background mutation, generated interaction or automatic POST retry belongs here.
const PRODUCT_EVENTS = ['daily_check_in', 'touch', 'completed_task', 'ignored_greeting']
const HISTORY_EVENTS = PRODUCT_EVENTS.concat(['valid_dialog'])
const PERSONALITIES = ['sunny_explorer', 'gentle_companion', 'proud_star', 'curious_scholar', 'calm_guardian']
const ATTRIBUTES = ['curiosity', 'empathy', 'knowledge', 'energy', 'mischief', 'discipline']
const ACTIONS = ['head_nod', 'head_shake', 'tail_wag', 'proud_pose', 'quiet_companion', 'greeting_combo', 'celebration_combo']
const GROWTH_EVENTS = ['knowledge_discussion', 'question', 'emotional_sharing', 'joke', 'casual_chat', 'planning', 'encouragement', 'physical_touch', 'completed_task', 'daily_meeting', 'tag_awarded']
const unsafe = /[\x00-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/
const object = (value: any) => value && typeof value === 'object' && !Array.isArray(value)
const own = (value: any, key: string) => Object.prototype.hasOwnProperty.call(value, key)
function invalid(): never { throw new Error('UNVERIFIED') }
function text(value: any, max = 80, allowEmpty = false): string {
  if (typeof value !== 'string' || value.length > max || unsafe.test(value) || !allowEmpty && !value.trim()) return invalid()
  return value
}
function number(value: any, min = 0, max = 1000000000, integer = false): number {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < min || value > max || integer && !Number.isSafeInteger(value)) return invalid()
  return value
}
function bool(value: any): boolean { if (typeof value !== 'boolean') return invalid(); return value }
function choice(value: any, values: string[]): string { if (!values.includes(value)) return invalid(); return value }
function list(value: any, max: number, verify: any): any[] {
  if (!Array.isArray(value) || value.length > max) return invalid()
  return value.map(verify)
}
function unique(value: any[], key: any) {
  const keys = value.map(key)
  if (new Set(keys).size !== keys.length) return invalid()
  return value
}
function utc(value: any): string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(value) || !Number.isFinite(Date.parse(value)) || new Date(Date.parse(value)).toISOString().slice(0, 19) !== value.slice(0, 19)) return invalid()
  return value
}
function optionalTime(value: any) { return value === null ? null : utc(value) }
function token(value: any, max = 128): string {
  if (typeof value !== 'string' || !new RegExp('^[A-Za-z0-9_-]{1,' + max + '}$').test(value)) return invalid()
  return value
}
function productEventValid(value: any): boolean { return typeof value === 'string' && PRODUCT_EVENTS.includes(value) }
function productRequestIdValid(value: any): boolean { return typeof value === 'string' && /^[A-Za-z0-9_.:-]{1,64}$/.test(value) }
function verifyIntimacyData(value: any) {
  if (!object(value)) return invalid()
  const result = {
    points: number(value.points, 0, 1000000000, true), level: number(value.level, 0, 4, true),
    levelName: text(value.levelName), minPoints: number(value.minPoints, 0, 1000000000, true),
    nextPoints: value.nextPoints === null ? null : number(value.nextPoints, 0, 1000000000, true),
    progressPercent: number(value.progressPercent, 0, 100), address: text(value.address),
    currentUnlocks: list(value.currentUnlocks, 32, (item: any) => text(item, 160)),
    nextUnlocks: list(value.nextUnlocks, 32, (item: any) => text(item, 160))
  }
  if (result.points < result.minPoints || result.nextPoints !== null && (result.nextPoints <= result.minPoints || result.points >= result.nextPoints)) return invalid()
  return result
}
function interaction(value: any) {
  if (!object(value)) return invalid()
  return { eventType: choice(value.eventType, HISTORY_EVENTS), label: text(value.label),
    pointsDelta: number(value.pointsDelta, -1000000000, 1000000000, true), pointsAfter: number(value.pointsAfter, 0, 1000000000, true), createdAt: utc(value.createdAt) }
}
function verifyDashboard(value: any) {
  if (!object(value) || !object(value.personality) || value.historyScope !== 'latest_30' || !object(value.today) || value.today.interactionCount !== null || value.today.pointsGained !== null) return invalid()
  const personality = value.personality
  return { interactionsEnabled: bool(value.interactionsEnabled), petName: text(value.petName), personality: {
    personalityId: choice(personality.personalityId, PERSONALITIES), name: text(personality.name), description: text(personality.description, 500, true),
    languageStyle: text(personality.languageStyle, 500, true), voiceName: text(personality.voiceName, 80, true)
  }, intimacy: verifyIntimacyData(value.intimacy),
    unlockedActionIds: unique(list(value.unlockedActionIds, 7, (item: any) => choice(item, ACTIONS)), (item: any) => item),
    recentInteractions: list(value.recentInteractions, 30, interaction), historyScope: 'latest_30',
    today: { interactionCount: null, pointsGained: null }, sampledAt: utc(value.sampledAt) }
}
function verifyIntimacy(value: any) {
  if (!object(value) || value.historyScope !== 'latest_30') return invalid()
  return { interactionsEnabled: bool(value.interactionsEnabled), intimacy: verifyIntimacyData(value.intimacy), dailyGrowthCap: number(value.dailyGrowthCap, 0, 1000000000, true),
    dailyCheckInCompleted: bool(value.dailyCheckInCompleted), rules: unique(list(value.rules, 5, (rule: any) => {
      if (!object(rule)) return invalid()
      return { eventType: choice(rule.eventType, HISTORY_EVENTS), label: text(rule.label), points: number(rule.points, -1000000000, 1000000000, true), maxPerDay: number(rule.maxPerDay, 0, 1000000000, true) }
    }), (rule: any) => rule.eventType), history: list(value.history, 30, interaction), historyScope: 'latest_30', sampledAt: utc(value.sampledAt) }
}
function tag(value: any) {
  if (!object(value)) return invalid()
  return { tagId: token(value.tagId), name: text(value.name), description: text(value.description, 500, true), earnedAt: utc(value.earnedAt) }
}
function verifyGrowth(value: any) {
  if (!object(value) || !object(value.behavior)) return invalid()
  const attributes = unique(list(value.attributes, 6, (item: any) => {
    if (!object(item)) return invalid()
    return { attributeId: choice(item.attributeId, ATTRIBUTES), label: text(item.label), tendency: choice(item.tendency, ['普通', '初显', '成长中', '明显', '突出']), value: number(item.value, 0, 100) }
  }), (item: any) => item.attributeId)
  if (attributes.length !== 6) return invalid()
  const behavior = value.behavior
  return { enabled: bool(value.enabled), attributes,
    activeTags: unique(list(value.activeTags, 64, tag), (item: any) => item.tagId),
    tagHistory: list(value.tagHistory, 64, (item: any) => ({ ...tag(item), active: bool(item.active), replacedAt: optionalTime(item.replacedAt) })),
    recentEvents: list(value.recentEvents, 20, (item: any) => {
      if (!object(item)) return invalid()
      return { eventType: choice(item.eventType, GROWTH_EVENTS), label: text(item.label), topic: text(item.topic, 160, true),
        emotion: choice(item.emotion, ['positive', 'neutral', 'negative', 'excited', 'sad', 'angry']),
        changedAttributes: unique(list(item.changedAttributes, 6, (attribute: any) => choice(attribute, ATTRIBUTES)), (attribute: any) => attribute), createdAt: utc(item.createdAt) }
    }), behavior: { basePersonalityId: choice(behavior.basePersonalityId, PERSONALITIES), basePersonalityName: text(behavior.basePersonalityName),
      intimacyLevel: number(behavior.intimacyLevel, 0, 4, true), directives: list(behavior.directives, 32, (item: any) => text(item, 500)),
      activeTagNames: list(behavior.activeTagNames, 64, (item: any) => text(item)) }, sampledAt: utc(value.sampledAt) }
}
function verifyInteraction(value: any) {
  if (!object(value) || !productEventValid(value.eventType) || !productRequestIdValid(value.requestId)) return invalid()
  const status = choice(value.status, ['sending', 'recorded', 'unknown', 'failed'])
  const recorded = status === 'recorded'
  if (!recorded && ['pointsDelta', 'pointsAfter', 'duplicate', 'celebrationScheduled'].some(key => !own(value, key) || value[key] !== null)) return invalid()
  return { receiptId: token(value.receiptId), eventType: value.eventType, requestId: value.requestId, status,
    pointsDelta: recorded ? number(value.pointsDelta, -1000000000, 1000000000, true) : null,
    pointsAfter: recorded ? number(value.pointsAfter, 0, 1000000000, true) : null,
    duplicate: recorded ? bool(value.duplicate) : null, celebrationScheduled: recorded ? bool(value.celebrationScheduled) : null, updatedAt: utc(value.updatedAt) }
}

function createLiveProduct(session: any, view: string, makeRequestId: any) {
  const readers: any = { home: 'readDashboard', growth: 'readIntimacy', personality: 'readGrowth' }
  if (!own(readers, view)) throw new Error('INVALID_VIEW')
  let visible = false, listener: any = null, detach: any = null, timer: any = null
  let key = '', ready = false, epoch = 0, revision = 0, readDirty = false, readFlight: any = null, writeFlight: any = null, pending: any = null
  const empty = () => ({ snapshot: null, loading: false, error: '', submitting: false, uncertain: false, result: null, interactionError: '' })
  let state: any = empty()
  function getState() { return JSON.parse(JSON.stringify({ ...state, retryAvailable: Boolean(pending && state.uncertain) })) }
  function emit() { if (visible && listener) listener(getState()) }
  function patch(value: any) { state = { ...state, ...value }; emit() }
  function selection() {
    const value = session.getState()
    return value.mode === 'live' && value.signedIn && typeof value.expiresAt === 'string' && value.expiresAt && value.selectedPetId && value.pets.some((pet: any) => pet.petId === value.selectedPetId) ? value.selectedPetId + '|' + value.expiresAt : ''
  }
  function usable() { const value = session.getState(); return Boolean(selection() && !value.loading && !value.listLoading) }
  function current(expected: number) { return visible && ready && epoch === expected && key && key === selection() && usable() }
  function cancelTimer() { clearTimeout(timer); timer = null }
  function schedule() {
    cancelTimer()
    if (!visible || !ready || !key || readFlight || writeFlight) return
    const expected = epoch
    timer = setTimeout(() => { timer = null; if (current(expected)) read() }, 10000)
  }
  function read(): Promise<any> {
    if (!visible || !ready || !key || key !== selection() || !usable()) return Promise.resolve()
    if (writeFlight) return Promise.resolve()
    if (readFlight) return readFlight.promise
    cancelTimer()
    const flight: any = { expected: epoch, revision }
    readFlight = flight; patch({ loading: true })
    flight.promise = session[readers[view]]().then((snapshot: any) => {
      if (current(flight.expected) && flight.revision === revision) patch({ snapshot, error: '' })
    }).catch((error: any) => {
      if (current(flight.expected) && flight.revision === revision && (!error || error.code !== 'CANCELLED')) patch({ snapshot: null, error: '真实数据暂不可用，请刷新重试；若网页授权失效，请更新电脑私有授权配置' })
    }).finally(() => {
      if (!current(flight.expected) || readFlight !== flight) return
      readFlight = null; patch({ loading: false })
      if (readDirty && !writeFlight) { readDirty = false; read() } else schedule()
    })
    return flight.promise
  }
  function changed() {
    const next = selection(), nextReady = usable(), switched = next !== key, resumed = !ready && nextReady
    if (switched || ready && !nextReady) {
      epoch++; revision++; cancelTimer(); readFlight = null; writeFlight = null; readDirty = false
      if (switched) { pending = null; state = empty() }
      else state = { ...state, snapshot: null, loading: false, submitting: false, uncertain: Boolean(pending || state.uncertain) }
      key = next; emit()
    }
    ready = nextReady
    if (key && ready && (switched || resumed)) read()
  }
  function rejected(message: string) { patch({ interactionError: '互动未发送：' + message }); return Promise.resolve() }
  function send(command: any): Promise<any> {
    const flight: any = { expected: epoch, revision: ++revision }
    writeFlight = flight; cancelTimer(); patch({ submitting: true, interactionError: '' })
    let recorded = false
    flight.promise = session.recordInteraction(command.eventType, command.requestId).then((receipt: any) => {
      if (!current(flight.expected) || flight.revision !== revision) return
      if (receipt.eventType !== command.eventType || receipt.requestId !== command.requestId) throw new Error('UNVERIFIED')
      recorded = receipt.status === 'recorded'
      const unresolved = ['sending', 'unknown'].includes(receipt.status)
      if (!unresolved) pending = null
      patch({ result: receipt, snapshot: recorded ? null : state.snapshot, uncertain: unresolved,
        interactionError: unresolved ? '互动结果不确定，请检查记录；仅可显式查询同一编号，切勿再次提交新互动' : receipt.status === 'failed' ? '本次互动未成功记录，请刷新状态后重新确认' : '' })
    }).catch((error: any) => {
      if (current(flight.expected) && flight.revision === revision && (!error || error.code !== 'CANCELLED')) patch({ uncertain: true, interactionError: '互动结果尚未确认，请检查记录或显式查询同一编号；不会自动重发' })
    }).finally(() => {
      if (!current(flight.expected) || writeFlight !== flight) return
      writeFlight = null; patch({ submitting: false })
      if (recorded) {
        // An older GET may still be pending when a write is acknowledged. Discard
        // its data and queue exactly one new read after it settles, not overlap.
        if (readFlight) readDirty = true
        else { readDirty = false; read() }
      } else schedule()
    })
    return flight.promise
  }
  function interact(eventType: string): Promise<any> {
    if (!visible) return Promise.resolve()
    if (!ready || !key || key !== selection() || !usable()) return rejected('登录或设备授权正在更新，请刷新后重新确认')
    if (!productEventValid(eventType)) return rejected('不支持该互动类型')
    if (writeFlight) return writeFlight.promise
    if (pending || state.uncertain) return rejected('上一编号结果尚未确认，请先检查记录或查询同一编号')
    if (!state.snapshot) return rejected('真实数据尚未验证，请刷新后重新确认')
    if (state.snapshot.interactionsEnabled !== true) return rejected('培养记录接口暂未开放，请等待服务端接入')
    const requestId = makeRequestId('interaction')
    if (!productRequestIdValid(requestId)) return rejected('请求编号未通过校验，请重试')
    pending = { eventType, requestId }
    return send(pending)
  }
  function retryInteraction(): Promise<any> {
    if (!visible) return Promise.resolve()
    if (!ready || !key || key !== selection() || !usable()) return rejected('登录或设备授权正在更新，请刷新后重试同一编号')
    if (writeFlight) return writeFlight.promise
    if (!pending || !state.uncertain) return rejected('没有待核对的同编号请求，请刷新后重新确认')
    if (!state.snapshot || state.snapshot.interactionsEnabled !== true) return rejected('真实数据或培养权限尚未验证，请刷新后重试同一编号，不要创建新互动')
    return send(pending)
  }
  function hide() {
    visible = false; listener = null; ready = false; epoch++; revision++; cancelTimer()
    readFlight = null; writeFlight = null; readDirty = false
    if (detach) detach(); detach = null
    state = { ...state, snapshot: null, loading: false, submitting: false, uncertain: Boolean(pending || state.uncertain) }
  }
  function show(callback: any) {
    hide(); visible = true; listener = callback
    detach = session.attach(changed); changed(); if (key) read(); emit()
  }
  return { show, hide, read, interact, retryInteraction, getState }
}

module.exports = { verifyDashboard, verifyIntimacy, verifyGrowth, verifyInteraction, productEventValid, productRequestIdValid, createLiveProduct }
