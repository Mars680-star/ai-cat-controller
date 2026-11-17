// Read-only transcripts: bounded snapshots and conditional replacement, never
// voice control, text submission, login, device selection or persistent storage.
const DIALOG_STATES = ['starting', 'connecting', 'connected', 'ready', 'waiting_wake', 'wake_detected', 'listening', 'processing_audio', 'thinking', 'answering', 'followup_preparing', 'followup_listening', 'waiting_follow_up', 'interrupted', 'echo_guard', 'recovering', 'waking', 'interrupting', 'submitting_text', 'proactive_queued', 'proactive_speaking', 'proactive_finishing', 'stopping', 'offline', 'unavailable']
const unsafe = /[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/
const object = (value: any) => value && typeof value === 'object' && !Array.isArray(value)
const revisionValid = (value: any) => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value)
const conversationIdValid = (value: any) => typeof value === 'string' && /^conv_[a-f0-9]{48}$/.test(value)
function invalid(): never { throw new Error('UNVERIFIED') }
function bool(value: any): boolean { if (typeof value !== 'boolean') return invalid(); return value }
function count(value: any, minimum = 0, maximum = 1000000000): number {
  if (!Number.isSafeInteger(value) || value < minimum || value > maximum) return invalid()
  return value
}
// WeChat does not guarantee Node Buffer or TextEncoder. Count exact UTF-8 bytes
// without a polyfill and reject lone surrogates rather than silently replacing.
function utf8Bytes(value: string, maximumCodePoints?: number): number {
  let bytes = 0, codePoints = 0
  for (let index = 0; index < value.length; index++) {
    codePoints++
    if (maximumCodePoints !== undefined && codePoints > maximumCodePoints) return invalid()
    const code = value.charCodeAt(index)
    if (code <= 0x7f) bytes++
    else if (code <= 0x7ff) bytes += 2
    else if (code >= 0xd800 && code <= 0xdbff) {
      const next = value.charCodeAt(++index)
      if (!(next >= 0xdc00 && next <= 0xdfff)) return invalid()
      bytes += 4
    } else if (code >= 0xdc00 && code <= 0xdfff) return invalid()
    else bytes += 3
  }
  return bytes
}
function text(value: any, maximum: number): string {
  if (typeof value !== 'string' || !value.length || value.length > maximum * 2 || unsafe.test(value)) return invalid()
  utf8Bytes(value, maximum)
  return value
}
function utc(value: any): string {
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$/.test(value) || !Number.isFinite(Date.parse(value)) || new Date(Date.parse(value)).toISOString().slice(0, 19) !== value.slice(0, 19)) return invalid()
  return value
}
const nullableUtc = (value: any) => value === null ? null : utc(value)
function revision(value: any): string { if (!revisionValid(value)) return invalid(); return value }
function verifyDialogStatus(value: any) {
  if (!object(value) || value.readOnly !== true || !DIALOG_STATES.includes(value.state)) return invalid()
  return { state: value.state, sessionActive: bool(value.sessionActive), audioPlaying: value.audioPlaying === null ? null : bool(value.audioPlaying), stale: bool(value.stale),
    updatedAt: nullableUtc(value.updatedAt), sequence: count(value.sequence, 0, Number.MAX_SAFE_INTEGER), followUpDeadlineAt: nullableUtc(value.followUpDeadlineAt),
    revision: revision(value.revision), sampledAt: utc(value.sampledAt), readOnly: true }
}
function verifySummary(value: any) {
  if (!object(value) || !conversationIdValid(value.conversationId) || !['complete', 'waiting_assistant', 'assistant_only'].includes(value.status) || !['device', 'mock'].includes(value.source)) return invalid()
  const result = { conversationId: value.conversationId, status: value.status, source: value.source,
    startedAt: utc(value.startedAt), updatedAt: utc(value.updatedAt), durationSeconds: count(value.durationSeconds),
    messageCount: count(value.messageCount, 1), userMessageCount: count(value.userMessageCount), assistantMessageCount: count(value.assistantMessageCount),
    preview: text(value.preview, 160), assistantPreview: value.assistantPreview === null ? null : text(value.assistantPreview, 160) }
  if (result.userMessageCount + result.assistantMessageCount !== result.messageCount || Date.parse(result.updatedAt) < Date.parse(result.startedAt)) return invalid()
  return result
}
function verifyDialogConversations(value: any) {
  if (!object(value) || value.limit !== 30 || value.scope !== 'persisted_only' || !Array.isArray(value.items) || value.items.length > 30) return invalid()
  const items = value.items.map(verifySummary), unchanged = bool(value.unchanged)
  if (new Set(items.map((item: any) => item.conversationId)).size !== items.length || unchanged && items.length) return invalid()
  return { items, revision: revision(value.revision), limit: 30, scope: 'persisted_only', unchanged, sampledAt: utc(value.sampledAt) }
}
function verifyDialogConversation(value: any) {
  if (!object(value) || value.limit !== 100 || value.scope !== 'persisted_only' || !Array.isArray(value.messages) || value.messages.length > 100) return invalid()
  const conversation = verifySummary(value.conversation), unchanged = bool(value.unchanged), truncated = bool(value.truncated)
  const windowMessageCount = count(value.windowMessageCount, 1, 100)
  const messages = value.messages.map((item: any) => {
    if (!object(item) || typeof item.messageId !== 'string' || !/^msg_[a-f0-9]{48}$/.test(item.messageId) || !['user', 'assistant'].includes(item.role)) return invalid()
    return { messageId: item.messageId, role: item.role, content: text(item.content, 8192), createdAt: utc(item.createdAt) }
  })
  if (new Set(messages.map((item: any) => item.messageId)).size !== messages.length || windowMessageCount > conversation.messageCount || truncated !== (conversation.messageCount > windowMessageCount) || unchanged && messages.length || !unchanged && messages.length !== windowMessageCount) return invalid()
  if (messages.reduce((total: number, item: any) => total + utf8Bytes(item.content), 0) > 192 * 1024) return invalid()
  const result = { conversation, messages, revision: revision(value.revision), limit: 100, windowMessageCount, truncated, scope: 'persisted_only', unchanged, sampledAt: utc(value.sampledAt) }
  if (utf8Bytes(JSON.stringify(result)) > 256 * 1024) return invalid()
  return result
}

function createLiveDialog(session: any, options?: any) {
  const detail = options && options.view === 'conversation'
  if (options !== undefined && (!object(options) || Object.keys(options).some(key => !['view', 'conversationId', 'petId'].includes(key)) || !['dialog', 'conversation'].includes(options.view))) throw new Error('INVALID_VIEW')
  if (detail && (!conversationIdValid(options.conversationId) || typeof options.petId !== 'string' || !/^[A-Za-z0-9_-]{1,128}$/.test(options.petId))) throw new Error('INVALID_ID')
  let visible = false, listener: any = null, detach: any = null, timer: any = null, epoch = 0, key = '', ready = false
  let readFlight: any = null, detailFlight: any = null, nextListAt = 0
  let statusPaused = false, listPaused = false, detailPaused = false
  let selected = detail ? options.conversationId : ''
  const empty = () => ({ snapshot: null, conversations: null, conversation: null, selectedConversationId: selected,
    loading: false, detailsLoading: false, error: '', conversationsError: '', detailsError: '' })
  let state: any = empty()
  function getState() { return JSON.parse(JSON.stringify(state)) }
  function emit() { if (visible && listener) listener(getState()) }
  function patch(value: any) { state = { ...state, ...value }; emit() }
  function selection() {
    const value = session.getState()
    return value.mode === 'live' && value.signedIn && value.expiresAt && value.selectedPetId && value.pets.some((pet: any) => pet.petId === value.selectedPetId) ? value.selectedPetId + '|' + value.expiresAt : ''
  }
  function usable() {
    const value = session.getState()
    return Boolean(selection() && !value.loading && !value.listLoading && (!detail || value.selectedPetId === options.petId))
  }
  function current(expected: number) { return visible && ready && epoch === expected && key && key === selection() && usable() }
  function cancelTimer() { clearTimeout(timer); timer = null }
  function schedule() {
    cancelTimer()
    if (!visible || !ready || !key || readFlight || detailFlight || (detail ? detailPaused : statusPaused && listPaused)) return
    const expected = epoch
    timer = setTimeout(() => { timer = null; if (current(expected)) refresh(true) }, detail ? 10000 : 5000)
  }
  function refresh(automatic = false): Promise<any> {
    if (!visible || !ready || !key || key !== selection() || !usable()) return Promise.resolve()
    if (detail) return loadConversation(selected, automatic === true)
    if (readFlight) return readFlight.promise
    if (automatic !== true) { statusPaused = false; listPaused = false }
    const readStatus = !statusPaused, readList = !listPaused && (automatic !== true || Date.now() >= nextListAt)
    if (!readStatus && !readList) { schedule(); return Promise.resolve() }
    cancelTimer()
    const flight: any = { expected: epoch, revision: state.conversations && state.conversations.revision }
    readFlight = flight; patch({ loading: true })
    const status = readStatus ? session.readDialogStatus().then((snapshot: any) => {
      if (current(flight.expected)) patch({ snapshot, error: '' })
    }).catch((error: any) => {
      if (current(flight.expected) && (!error || error.code !== 'CANCELLED')) { statusPaused = true; patch({ snapshot: null, error: '语音状态暂不可用，自动读取已暂停，请手动刷新；此页尚未开放对话控制' }) }
    }) : Promise.resolve()
    const conversations = readList ? session.readDialogConversations(flight.revision || undefined).then((page: any) => {
      if (!current(flight.expected)) return
      if (page.unchanged) {
        if (!state.conversations || page.revision !== flight.revision || page.revision !== state.conversations.revision) throw new Error('UNVERIFIED')
        patch({ conversations: { ...state.conversations, sampledAt: page.sampledAt }, conversationsError: '' })
      } else patch({ conversations: page, conversationsError: '' })
    }).catch((error: any) => {
      if (current(flight.expected) && (!error || error.code !== 'CANCELLED')) { listPaused = true; patch({ conversations: null, conversationsError: '已保存的会话摘要暂不可用，自动读取已暂停，请手动刷新；旧设备可能尚未支持纯只读记录接口' }) }
    }).finally(() => { if (current(flight.expected)) nextListAt = Date.now() + 10000 }) : Promise.resolve()
    flight.promise = Promise.all([status, conversations]).finally(() => {
      if (!current(flight.expected) || readFlight !== flight) return
      readFlight = null; patch({ loading: false }); schedule()
    })
    return flight.promise
  }
  function loadConversation(conversationId: string, automatic = false): Promise<any> {
    if (!visible || !ready || !key || key !== selection() || !usable()) return Promise.resolve()
    if (!conversationIdValid(conversationId) || detail && conversationId !== options.conversationId || !detail && (!state.conversations || !state.conversations.items.some((item: any) => item.conversationId === conversationId))) {
      patch({ conversation: null, detailsError: '请从当前已授权设备的会话列表重新打开详情' }); return Promise.resolve()
    }
    if (detailFlight && detailFlight.conversationId === conversationId) return detailFlight.promise
    if (detailFlight) return Promise.resolve()
    if (automatic && detailPaused) return Promise.resolve()
    detailPaused = false
    if (selected !== conversationId) { selected = conversationId; patch({ conversation: null, selectedConversationId: selected, detailsError: '' }) }
    cancelTimer()
    const flight: any = { expected: epoch, conversationId, revision: state.conversation && state.conversation.revision }
    detailFlight = flight; patch({ detailsLoading: true })
    flight.promise = session.readDialogConversation(conversationId, flight.revision || undefined).then((page: any) => {
      if (!current(flight.expected) || selected !== flight.conversationId) return
      if (page.conversation.conversationId !== flight.conversationId) throw new Error('UNVERIFIED')
      if (page.unchanged) {
        if (!state.conversation || page.revision !== flight.revision || page.revision !== state.conversation.revision) throw new Error('UNVERIFIED')
        if (page.windowMessageCount !== state.conversation.windowMessageCount || page.truncated !== state.conversation.truncated || JSON.stringify(page.conversation) !== JSON.stringify(state.conversation.conversation)) throw new Error('UNVERIFIED')
        patch({ conversation: { ...page, unchanged: false, messages: state.conversation.messages }, detailsError: '' })
      } else patch({ conversation: page, detailsError: '' })
    }).catch((error: any) => {
      if (current(flight.expected) && selected === flight.conversationId && (!error || error.code !== 'CANCELLED')) { detailPaused = true; patch({ conversation: null, detailsError: '会话详情暂不可用，自动读取已暂停，请手动刷新或返回会话列表重新打开；不会显示演示或旧设备内容' }) }
    }).finally(() => {
      if (!current(flight.expected) || detailFlight !== flight) return
      detailFlight = null; patch({ detailsLoading: false }); schedule()
    })
    return flight.promise
  }
  function changed() {
    const next = selection(), nextReady = usable(), switched = next !== key, resumed = !ready && nextReady
    if (switched || ready && !nextReady) {
      epoch++; cancelTimer(); readFlight = null; detailFlight = null; nextListAt = 0
      statusPaused = false; listPaused = false; detailPaused = false
      if (!detail) selected = ''
      state = empty(); key = next; emit()
    }
    ready = nextReady
    const identity = session.getState()
    if (detail && identity.selectedPetId && identity.selectedPetId !== options.petId) patch({ detailsError: '设备选择已变化，请返回会话列表重新打开' })
    if (key && ready && (switched || resumed)) refresh()
  }
  function hide() {
    visible = false; listener = null; ready = false; epoch++; cancelTimer(); readFlight = null; detailFlight = null
    nextListAt = 0; statusPaused = false; listPaused = false; detailPaused = false
    if (detach) detach(); detach = null
    if (!detail) selected = ''
    state = empty()
  }
  function show(callback: any) {
    hide(); visible = true; listener = callback
    detach = session.attach(changed); changed(); if (key) refresh(); emit()
  }
  return { show, hide, refresh, loadConversation, getState }
}

module.exports = { verifyDialogStatus, verifyDialogConversations, verifyDialogConversation, conversationIdValid, dialogRevisionValid: revisionValid, createLiveDialog }
