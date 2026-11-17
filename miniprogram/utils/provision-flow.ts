const { createLiveProvisioning } = require('../services/live-provisioning')
const { takeLocalProvisionHandoff } = require('../services/local-network')
const localTransfers = new WeakMap<any, any>()
const STATES = ['accepted', 'connecting', 'connected', 'failed', 'unknown', 'not_sent']
const PROBE_LABEL: any = { idle: '尚未自检', testing: '正在检查本地端口', reachable: '端口可达 · 短时有效', failed: '自检未通过', expired: '自检已过期，请重新检查' }
const ARCHIVE_CHECKS = ['window-ended', 'usb-checked', 'accept-unknown']
const ARCHIVE_LIMIT = 20
const STATUS: any = {
  accepted: ['设备已接收配网请求', '已接收不代表联网完成。请核对原请求，或扫描操作员显示的收尾回执。'],
  connecting: ['设备正在连接 Wi-Fi', '请稍候，不要再次发送网络信息；网络切换可能断开原热点。'],
  connected: ['设备确认已连接', '已验证本次联网回执；不等于云账号绑定或小程序设备归属已建立。'],
  failed: ['设备报告连接失败', '请核对目标网络及原连接状态；不会自动再次配置网络。'],
  unknown: ['配网结果未知', '网络切换可能断开热点。保留原请求，可扫描收尾回执；不要清数据或重新发送。'],
  not_sent: ['网络信息未发送', '资料可能已填写，但发送前的读取热点、绑定或连接尚未成功。请查看具体原因；密码框已为安全清空，不代表此前未填写。']
}
const LOCAL_STATUS: any = {
  accepted: ['手机切回目标 Wi-Fi', '设备已接纳资料，正在尝试切网，尚未确认成功。热点可能因此消失；请把手机连接刚填写的目标 Wi-Fi，返回同一向导重扫机身卡并核验原请求。'],
  connecting: ['设备切网中，请连接目标 Wi-Fi', '临时热点可能关闭。手机连接目标 Wi-Fi 后，返回同一向导重扫同一机身卡核验；不要重复发送。'],
  unknown: ['连接结果待核验', '热点消失不代表成功或失败。手机连接目标 Wi-Fi 后返回向导，发现当前设备地址、重扫同一卡并核验原回执；若目标网找不到设备，也检查原普通 Wi-Fi，不重发资料。']
}
function active(page: any, epoch: number) { return page.pageVisible && page.viewEpoch === epoch }
function recoverableBinding(value: any) {
  // Only a proven pre-dispatch failure may offer a NEW explicit transport
  // choice. A pending/unknown request must keep its original review path.
  return value.scanned === true && value.status === 'not_sent' && value.pending !== true &&
    value.networkValid === false && value.transportMode === 'bound' && typeof value.error === 'string' &&
    /\[WIFI_BIND_(?:CALL|EVENT|CLOSED|TIMEOUT)_(?:NA|-[1-4]|[0-6])\]/.test(value.error)
}
function android() {
  try { return (typeof wx.getDeviceInfo === 'function' ? wx.getDeviceInfo() : wx.getSystemInfoSync()).platform === 'android' } catch (_) { return false }
}
function networkInputGate(page: any, value: any) {
  const reason = !page.pageVisible ? '页面已离开，请返回后重新校验本次二维码。'
    : !page.data.androidAvailable ? '电脑和 iOS 仅预览；请在安卓手机微信中打开小程序预览后填写。'
    : value.pending === true ? '有待核对的未决配网请求，输入暂时锁定。请先核对原请求或扫描收尾回执，不要清缓存或重复发送。'
    : value.busy === true ? '正在处理扫码或配网请求，输入暂时锁定，请等待当前操作结束。'
    : value.scanned !== true ? '请先完成第 1 步扫码，看到“二维码已校验”后，这两个输入框才会解锁。'
    : ''
  return { canEditNetwork: !reason, networkLockReason: reason }
}
function publicRequestIdentity(value: any) {
  try {
    if (!value || typeof value.deviceId !== 'string' || !/^dev_[0-9a-f]{32}$/.test(value.deviceId) ||
        typeof value.sessionId !== 'string' || !/^[0-9a-f]{32}$/.test(value.sessionId) ||
        typeof value.requestId !== 'string' || !/^[A-Za-z0-9_.:-]{1,64}$/.test(value.requestId)) return null
    return { deviceId: value.deviceId, sessionId: value.sessionId, requestId: value.requestId }
  } catch (_) { return null }
}
function archiveCheckData(value: any) {
  const valid = Array.isArray(value) && value.every(item => ARCHIVE_CHECKS.includes(item)) && new Set(value).size === value.length
  const acks = valid ? ARCHIVE_CHECKS.filter(item => value.includes(item)) : []
  return { archiveAcks: acks, archiveWindowEnded: acks.includes('window-ended'),
    archiveUSBChecked: acks.includes('usb-checked'), archiveAcceptUnknown: acks.includes('accept-unknown') }
}

function createProvisionFlowDefinition(callbacks: any = {}) {
  return {
  data: { androidAvailable: false, phaseLabel: '等待本次配网二维码', statusHint: '',
    status: '', error: '', notice: '', busy: false, scanned: false, pending: false,
    hotspotReady: false, confirming: false, canConfigure: false, canQuery: false,
    canScanReceipt: false, canEditNetwork: false, networkLockReason: '', ssid: '', formKeys: [], cameraKeys: [],
    transportAvailable: false, transportMode: 'bound', probeStatus: 'idle', probeLabel: PROBE_LABEL.idle,
    probeHint: '', compatibilityReady: false, canProbe: false, bindingRecoveryAvailable: false, canRecoverBinding: false,
    archiveAvailable: false, archiveReady: false, canArchive: false, archiveSubmitting: false, archiveLimitReached: false,
    reviewIdentity: null, archiveCount: 0, lastArchiveIdentity: null,
    localNetworkSource: false, localNetworkReceipt: false, ...archiveCheckData([]) },
  async onShow() {
    const previousTransfer = localTransfers.get(this)
    if (previousTransfer) previousTransfer.dispose()
    localTransfers.delete(this)
    if (this.provision) this.provision.hide()
    this.dropCamera()
    this.pageVisible = true
    const epoch = this.viewEpoch = (this.viewEpoch || 0) + 1
    this.confirmation = null
    this.flowTerminalAnnounced = false
    this.flowSettlementError = false
    this.privatePassword = ''
    this.ssidDraft = ''
    this.setData({ androidAvailable: android(), phaseLabel: '准备本次配网', statusHint: '', status: '',
      error: '', notice: '', busy: false, scanned: false, pending: false,
      canConfigure: false, canQuery: false, canScanReceipt: false, canEditNetwork: false,
      networkLockReason: '', ssid: '', hotspotReady: false,
      confirming: false, cameraKeys: [], formKeys: [this.inputGeneration = (this.inputGeneration || 0) + 1],
      transportMode: 'bound', probeStatus: 'idle', probeLabel: PROBE_LABEL.idle, probeHint: '', compatibilityReady: false, canProbe: false,
      bindingRecoveryAvailable: false, canRecoverBinding: false,
      archiveReady: false, canArchive: false, archiveSubmitting: false,
      localNetworkSource: false, localNetworkReceipt: false, ...archiveCheckData([]) })
    const transfer = takeLocalProvisionHandoff()
    if (transfer) localTransfers.set(this, transfer)
    if (!transfer && callbacks.requireHandoff === true) {
      this.setData({ phaseLabel: '请重新连接设备', error: '安全配网资料已清除，请返回当前步骤重新取回。',
        formKeys: [], canConfigure: false, canQuery: false, canEditNetwork: false })
      this.notifyFlowError('安全配网资料已清除，请返回当前步骤重新取回。')
      return
    }
    if (!this.provision) this.provision = createLiveProvisioning(wx, (options: any) => {
      const privateTransfer = localTransfers.get(this)
      if (privateTransfer && privateTransfer.deliver(options)) return
      if (callbacks.requireHandoff === true) { options.fail({ errMsg: 'private handoff unavailable' }); return }
      this.openEmbeddedCamera(options)
    })
    this.provision.show((value: any) => { if (active(this, epoch)) this.render(value) })
    if (!transfer) return
    this.setData({ localNetworkSource: true, localNetworkReceipt: transfer.hasReceipt })
    try {
      await this.provision.scan()
      if (!active(this, epoch)) return
      if (callbacks.requireHandoff === true && this.provision.getState().scanned !== true) {
        transfer.dispose(); localTransfers.delete(this)
        this.notifyFlowError('安全配网资料未通过校验，请保留原编号并返回当前步骤重新取回。')
        return
      }
      if (transfer.hasReceipt) {
        if (!transfer.canApplyReceipt(this.provision.getState())) {
          this.setData({ notice: '猫端原回执已取回，但手机原未决编号不匹配或缺失；保留全部记录，不据此清除未决或重新配网。' })
          transfer.dispose(); localTransfers.delete(this)
          this.notifyFlowError('原未决编号不匹配或缺失，请保留记录并返回核对。')
          return
        }
        await this.provision.scanReceipt()
      }
      if (active(this, epoch)) this.render(this.provision.getState())
    } catch (_) {
      transfer.dispose(); localTransfers.delete(this)
      if (active(this, epoch)) this.setData({ notice: '猫端私有交接未完成，未发送网络资料；请回猫端配网页核对原窗口，不重开或清记录。' })
      if (active(this, epoch)) this.notifyFlowError('安全配网资料未能取回，请保留原编号并返回核对。')
    }
  },
  onHide() { this.pause(false) },
  onUnload() { this.pause(true) },
  pause(disposed: boolean) {
    this.pageVisible = false
    this.viewEpoch = (this.viewEpoch || 0) + 1
    const confirmation = this.confirmation
    this.confirmation = null
    if (confirmation && confirmation.kind === 'archive' && this.provision && typeof this.provision.cancelArchive === 'function') {
      try { this.provision.cancelArchive(confirmation.token) } catch (_) { /* Hide still clears the visit below. */ }
    }
    this.privatePassword = ''
    this.ssidDraft = ''
    const transfer = localTransfers.get(this)
    if (transfer) transfer.dispose()
    localTransfers.delete(this)
    this.dropCamera()
    if (this.provision) this.provision.hide()
    // Destroy uncontrolled native inputs on hide; unload destroys the view itself.
    if (!disposed) this.setData({ formKeys: [], cameraKeys: [], ssid: '', hotspotReady: false, confirming: false,
      canConfigure: false, canQuery: false, canScanReceipt: false, canEditNetwork: false,
      transportMode: 'bound', probeStatus: 'idle', probeLabel: PROBE_LABEL.idle, probeHint: '', compatibilityReady: false, canProbe: false,
      bindingRecoveryAvailable: false, canRecoverBinding: false,
      archiveReady: false, canArchive: false, archiveSubmitting: false, ...archiveCheckData([]),
      networkLockReason: '页面已离开，请返回后重新校验本次二维码。' })
  },
  dropCamera() {
    const target = this.cameraAttempt
    this.cameraAttempt = null
    if (target) clearTimeout(target.timer)
  },
  openEmbeddedCamera(options: any) {
    if (!this.pageVisible || !this.data.androidAvailable || this.cameraAttempt) {
      options.fail({ errMsg: 'scanCode:fail unavailable' })
      return
    }
    const key = String(this.cameraSequence = (this.cameraSequence || 0) + 1)
    const target = { key, epoch: this.viewEpoch, options, timer: null as any }
    this.cameraAttempt = target
    target.timer = setTimeout(() => this.finishEmbeddedCamera(target, null, 'timeout'), 20000)
    // Only a non-secret component generation enters Page.data. QR contents stay
    // in the event callback and controller closure, never in the render bridge.
    this.setData({ cameraKeys: [key] })
  },
  cameraForEvent(event: any) {
    const target = this.cameraAttempt
    const key = event && event.currentTarget && event.currentTarget.dataset && event.currentTarget.dataset.scanId
    return target && active(this, target.epoch) && key === target.key ? target : null
  },
  finishEmbeddedCamera(target: any, value: any, reason = '') {
    if (!target || this.cameraAttempt !== target || !active(this, target.epoch)) return
    this.dropCamera()
    this.setData({ cameraKeys: [] })
    if (value) target.options.success(value)
    else target.options.fail({ errMsg: 'scanCode:fail interrupted' })
    if (reason && active(this, target.epoch)) {
      const notices: any = {
        cancel: '已取消扫码，本次扫码未发起新的配网请求。',
        error: '相机未能启动，请检查微信的相机授权后重新扫码；本次扫码未发起新的配网请求。',
        stop: '相机扫码已中断，请回到本页重新扫码；本次扫码未发起新的配网请求。',
        timeout: '相机暂未读到二维码，请调整距离、确认终端二维码完整后重新扫码。'
      }
      const pendingHint = this.provision.getState().pending ? '原配网请求结果仍待核对，请保留原编号。' : ''
      this.setData({ notice: (notices[reason] || '扫码未完成，请重试。') + pendingHint })
    }
  },
  cameraDecoded(event: any) {
    const target = this.cameraForEvent(event), detail = event && event.detail
    if (!target) return
    const result = detail && detail.result
    const invalidResult = typeof result !== 'string' || result.length < 1 || result.length > 2048
    // Camera metadata is not an authentication boundary. Normalize only known
    // QR aliases (or absent metadata); the controller still validates the full
    // invitation or same-request authenticated receipt before using its body.
    const supportedType = detail && (detail.type === undefined || ['QR_CODE', 'qrCode', 'qrcode', 'qr_code'].includes(detail.type))
    if (invalidResult || !supportedType) {
      this.finishEmbeddedCamera(target, null)
      if (active(this, target.epoch)) {
        const message = invalidResult
          ? '扫码结果为空或超出支持范围，请重新扫描本次配网或收尾二维码。'
          : '扫码结果类型暂不支持，请重新扫描本次配网或收尾二维码。'
        const pendingHint = this.provision.getState().pending
          ? '原配网请求结果仍待核对，请保留原编号。' : '本次扫码未发起新的配网请求。'
        this.setData({ notice: message + pendingHint })
      }
      return
    }
    this.finishEmbeddedCamera(target, { scanType: 'QR_CODE', result })
  },
  cameraError(event: any) { this.finishEmbeddedCamera(this.cameraForEvent(event), null, 'error') },
  cameraStopped(event: any) { this.finishEmbeddedCamera(this.cameraForEvent(event), null, 'stop') },
  cancelEmbeddedScan(event: any) { this.finishEmbeddedCamera(this.cameraForEvent(event), null, 'cancel') },
  render(value: any) {
    if (!active(this, this.viewEpoch)) return
    const transfer = localTransfers.get(this)
    if (transfer) {
      try {
        if (transfer.observe(value)) { transfer.dispose(); localTransfers.delete(this) }
      } catch (_) {
        this.flowSettlementError = true
        transfer.dispose(); localTransfers.delete(this)
        this.setData({ notice: '原回执状态已显示，但猫端配网编号的本机收尾保存未通过；保留记录，不换号重开。' })
      }
    }
    const status = STATES.includes(value.status) ? value.status : ''
    const confirming = Boolean(this.confirmation)
    const transportAvailable = Boolean(this.provision && typeof this.provision.setTransportMode === 'function' && typeof this.provision.probe === 'function')
    const transportMode = transportAvailable && value.transportMode === 'direct' ? 'direct' : 'bound'
    const compatibilityReady = transportMode === 'direct' && value.compatibilityReady === true
    const bindingRecoveryAvailable = transportAvailable && recoverableBinding(value)
    const transportReady = transportMode === 'bound' || compatibilityReady
    const probeStatus = Object.prototype.hasOwnProperty.call(PROBE_LABEL, value.probeStatus) ? value.probeStatus : 'idle'
    const archiveAvailable = Boolean(this.provision && typeof this.provision.prepareArchive === 'function' && typeof this.provision.cancelArchive === 'function' && typeof this.provision.confirmArchive === 'function')
    const reviewIdentity = value.pending === true ? publicRequestIdentity(value.reviewIdentity) : null
    const archiveReady = Boolean(archiveAvailable && reviewIdentity && value.archiveReady === true)
    const archiveAcks = archiveCheckData(this.data.archiveAcks).archiveAcks
    const archiveCount = Number.isSafeInteger(value.archiveCount) && value.archiveCount >= 0 ? value.archiveCount : 0
    const archiveLimitReached = value.pending === true && archiveCount >= ARCHIVE_LIMIT
    const last = value.lastArchive
    const lastArchiveIdentity = last && last.status === 'unknown' && last.resolution === 'operator_archived' ? publicRequestIdentity(last.original) : null
    const label = value.phase === 'archived' ? ['原请求已归档，结果仍未知', '仅记录受信操作员的人工声明，不代表配网成功。原窗口不可复用；再次配置需要新的窗口、二维码和发送确认。']
      : probeStatus === 'testing' ? ['正在检查本地端口', '仅测试固定配网端口，不发送网络资料；端口可达不代表身份已验证或配网成功。']
      : value.phase === 'scanning-receipt' ? ['正在扫描收尾回执', '只验证原请求的加密回执，不重新发送网络资料。']
      : value.phase === 'scanning' ? ['正在扫描二维码', '扫描本次操作员生成的二维码，不是设备编号或网页链接。']
      : value.phase === 'checking' ? ['正在核对原配网结果', '本次只核对原请求，不发送 Wi-Fi 信息。']
      : value.phase === 'preparing' ? ['正在准备安全配网', '正在加密本次资料，请勿重复点击。']
      : status ? this.data.localNetworkSource && LOCAL_STATUS[status] || STATUS[status]
      : value.phase === 'error' && !value.scanned ? ['扫码未完成', '请查看扫码按钮附近的提示；本次扫码未发起新配置，原请求结果请单独核对。']
      : value.pending && !value.scanned ? ['请扫描原配网码核对', '发现未决配网请求；请保留原编号，不要生成新请求绕过。']
      : value.pending ? ['请核对原配网请求', '二维码已校验，但有未决配网请求；请核对原编号或扫描收尾回执，暂不填写或发送新的网络资料。']
      : value.scanned ? ['二维码已校验', '请手动连接本次设备热点，再填写目标 Wi-Fi。']
      : ['等待本次配网二维码', '先由操作员开启配网窗口；本页不会自动开启设备热点。']
    this.setData({ ...networkInputGate(this, value), phaseLabel: label[0], statusHint: label[1], status,
      error: typeof value.error === 'string' ? value.error : '', busy: value.busy === true,
      scanned: value.scanned === true, pending: value.pending === true, confirming,
      transportAvailable, transportMode, probeStatus, probeLabel: PROBE_LABEL[probeStatus],
      probeHint: typeof value.probeHint === 'string' ? value.probeHint : '', compatibilityReady,
      bindingRecoveryAvailable,
      canRecoverBinding: Boolean(bindingRecoveryAvailable && this.data.androidAvailable && this.data.hotspotReady && !value.busy && !confirming),
      archiveAvailable, archiveReady, reviewIdentity, lastArchiveIdentity,
      archiveCount, archiveLimitReached,
      archiveSubmitting: Boolean(this.confirmation && this.confirmation.kind === 'archive' && this.confirmation.submitted),
      canArchive: Boolean(this.data.androidAvailable && archiveAvailable && archiveReady && archiveCount < ARCHIVE_LIMIT && value.canArchive === true && archiveAcks.length === ARCHIVE_CHECKS.length && !value.busy && !confirming),
      canProbe: Boolean(this.data.androidAvailable && this.data.hotspotReady && transportAvailable && transportMode === 'direct' && value.canProbe && !value.busy && !confirming),
      canConfigure: Boolean(this.data.androidAvailable && this.data.hotspotReady && transportReady && value.canConfigure && !confirming),
      canQuery: Boolean(this.data.androidAvailable && this.data.hotspotReady && transportReady && value.canQuery && !confirming),
      canScanReceipt: Boolean(this.data.androidAvailable && value.canScanReceipt && !confirming && typeof this.provision.scanReceipt === 'function') })
    this.notifyFlowState(value)
  },
  notifyFlowError(message: string) {
    if (!this.pageVisible || typeof callbacks.onError !== 'function') return
    try { callbacks.onError.call(this, { message }) } catch (_) { /* Presentation cannot interrupt ledger cleanup. */ }
  },
  notifyFlowState(value: any) {
    if (!this.pageVisible) return
    const detail = { phase: typeof value.phase === 'string' ? value.phase : '',
      status: STATES.includes(value.status) ? value.status : '', pending: value.pending === true,
      busy: value.busy === true, error: typeof value.error === 'string' ? value.error : '' }
    if (typeof callbacks.onState === 'function') {
      try { callbacks.onState.call(this, detail) } catch (_) { /* No secret material crosses this event. */ }
    }
    if (!this.pageVisible || this.flowTerminalAnnounced || this.flowSettlementError || detail.pending || detail.busy || detail.error ||
        !['connected','pending'].includes(detail.phase) || !['connected','failed','not_sent'].includes(detail.status)) return
    this.flowTerminalAnnounced = true
    if (typeof callbacks.onTerminal === 'function') {
      try { callbacks.onTerminal.call(this, detail) } catch (_) { /* User still explicitly leaves the form. */ }
    }
  },
  invalidateConfirmation() {
    const target = this.confirmation
    if (!target) return
    this.confirmation = null
    this.setData({ confirming: false, notice: target.kind === 'archive'
      ? '本次人工归档确认已撤销；原配网请求结果仍待核对。'
      : '网络信息未发送：确认期间资料或热点选择已更改，请重新确认。' })
    if (target.kind === 'archive') {
      if (typeof this.provision.cancelArchive === 'function') this.provision.cancelArchive(target.token)
    } else this.provision.cancel(target.token)
  },
  editable() { return networkInputGate(this, this.provision ? this.provision.getState() : this.data).canEditNetwork },
  inputSSID(event: any) {
    if (!this.editable()) return
    const raw = event && event.detail && event.detail.value
    const value = typeof raw === 'string' && raw.length <= 64 ? raw : ''
    if (value !== this.ssidDraft) this.invalidateConfirmation()
    this.ssidDraft = value
    this.setData({ ssid: value })
    this.provision.setNetwork(this.ssidDraft, this.privatePassword)
  },
  inputPassword(event: any) {
    if (!this.editable()) return
    const raw = event && event.detail && event.detail.value
    const value = typeof raw === 'string' && raw.length <= 128 ? raw : ''
    if (value !== this.privatePassword) this.invalidateConfirmation()
    // Never place this value in Page.data, setData, persistent storage or logs.
    this.privatePassword = value
    this.provision.setNetwork(this.ssidDraft, this.privatePassword)
  },
  hotspotChanged(event: any) {
    if (!this.pageVisible || !this.data.androidAvailable || this.data.busy || this.confirmation && this.confirmation.kind === 'archive') return
    const values = event && event.detail && event.detail.value
    const ready = Array.isArray(values) && values.includes('hotspot')
    if (ready !== this.data.hotspotReady) this.invalidateConfirmation()
    this.setData({ hotspotReady: ready })
    this.render(this.provision.getState())
  },
  transportChanged(event: any) {
    if (!this.pageVisible || !this.data.androidAvailable || this.data.busy || this.confirmation && this.confirmation.kind === 'archive' || !this.provision || typeof this.provision.setTransportMode !== 'function' || typeof this.provision.probe !== 'function') return
    const mode = event && event.detail && event.detail.value
    if (!['bound', 'direct'].includes(mode) || mode === this.provision.getState().transportMode) return
    this.invalidateConfirmation()
    this.setData(archiveCheckData([]))
    let changed = false
    try { changed = this.provision.setTransportMode(mode) === true } catch (_) { /* Only fixed local feedback, never native error text. */ }
    this.setData({ notice: changed ? mode === 'direct'
      ? '已选择本地直连兼容模式；旧确认已撤销。请主动完成端口自检，再重新确认发送。'
      : '已恢复标准 Wi-Fi 绑定；旧确认已撤销，不会自动发送。'
      : '当前不能更改通道模式，请等待当前操作结束后重新确认。' })
    this.render(this.provision.getState())
  },
  async probeCompatibility() {
    const epoch = this.viewEpoch
    if (!active(this, epoch) || !this.data.androidAvailable || !this.data.hotspotReady || this.data.busy || this.confirmation ||
        !this.provision || typeof this.provision.probe !== 'function' || typeof this.provision.setTransportMode !== 'function') return
    const before = this.provision.getState()
    if (before.transportMode !== 'direct' || before.canProbe !== true) return
    this.setData({ notice: '' })
    try { await this.provision.probe() }
    catch (_) { if (active(this, epoch)) this.setData({ notice: '本次端口自检未完成；自检不会提交配网资料，原配网请求状态不变，请保留原编号并查看提示。' }) }
    finally { if (active(this, epoch)) this.render(this.provision.getState()) }
  },
  async recoverBinding() {
    const epoch = this.viewEpoch
    if (!active(this, epoch) || !this.data.androidAvailable || !this.data.hotspotReady || this.data.busy || this.confirmation ||
        !this.provision || typeof this.provision.setTransportMode !== 'function' || typeof this.provision.probe !== 'function' ||
        !recoverableBinding(this.provision.getState())) return
    // This tap authorizes only selecting compatibility and a zero-data probe.
    // The original controller has already discarded this failed draft. Destroy
    // the native password input too; a later send still needs fresh input and
    // its own confirmation. Never recreate a request or clear a ledger here.
    this.privatePassword = ''
    this.setData({ formKeys: [this.inputGeneration = (this.inputGeneration || 0) + 1] })
    this.transportChanged({ detail: { value: 'direct' } })
    if (!active(this, epoch) || this.provision.getState().transportMode !== 'direct') return
    await this.probeCompatibility()
    if (!active(this, epoch)) return
    const value = this.provision.getState()
    if (value.transportMode === 'direct' && !value.pending) this.setData({ notice: value.compatibilityReady === true
      ? '兼容通道端口可达，尚未发送网络信息。请重新填写密码，再点“保存并连接”确认一次。'
      : '兼容通道检查未通过，未发送网络信息。请保持设备热点连接，查看下方诊断后再手动检查。' })
  },
  archiveAcksChanged(event: any) {
    if (!this.pageVisible || !this.data.androidAvailable || !this.data.archiveAvailable || this.data.archiveLimitReached || this.data.busy || this.confirmation) return
    const values = event && event.detail && event.detail.value
    this.setData(archiveCheckData(values))
    this.render(this.provision.getState())
  },
  async archiveRequest() {
    const epoch = this.viewEpoch
    if (!active(this, epoch) || !this.data.androidAvailable || this.data.busy || this.confirmation || !this.data.canArchive ||
        !this.provision || typeof this.provision.prepareArchive !== 'function' || typeof this.provision.cancelArchive !== 'function' || typeof this.provision.confirmArchive !== 'function') return
    const value = this.provision.getState(), acks = archiveCheckData(this.data.archiveAcks).archiveAcks
    if (!value.canArchive || !value.archiveReady || !publicRequestIdentity(value.reviewIdentity) || acks.length !== ARCHIVE_CHECKS.length) return
    let token: any = null
    try { token = this.provision.prepareArchive(acks) } catch (_) { /* No raw storage or native error enters the page. */ }
    if (typeof token !== 'string' || !token || token.length > 128 || !active(this, epoch)) {
      if (active(this, epoch)) { this.setData({ notice: '人工归档未完成；请保留原编号并重新核对，不据此判断联网或重复配网。' }); this.render(this.provision.getState()) }
      return
    }
    const target = { token, epoch, kind: 'archive', submitted: false }
    this.confirmation = target
    this.setData({ notice: '' })
    this.render(this.provision.getState())
    return new Promise<void>(resolve => {
      const current = () => active(this, epoch) && this.confirmation === target
      const cancel = () => { try { this.provision.cancelArchive(token) } catch (_) { /* Fixed feedback only. */ } }
      const failure = () => {
        if (!current()) { resolve(); return }
        if (target.submitted) return
        target.submitted = true; this.confirmation = null; cancel()
        this.setData({ notice: '人工归档未完成：确认弹窗未能完成，请保留原编号并重新核对。' })
        this.render(this.provision.getState()); resolve()
      }
      const modal = { title: '人工归档未知请求？', confirmText: '确认归档', cancelText: '暂不归档',
        content: '仅限受信本地试点。你提交的是人工声明，不是程序自动 USB 核验：原配网程序已结束，操作员已通过 USB 确认无配网任务和检查点且当前网络稳定；你接受结果仍未知、目标凭据可能已保存。此确认仅 30 秒内有效，只归档原编号，不代表联网成功，不删除历史，也不会配网。确认归档？',
        success: async (answer: any) => {
          if (!current()) { resolve(); return }
          if (target.submitted) return
          target.submitted = true
          let archived = false
          try {
            if (!answer || answer.confirm !== true) {
              cancel()
              this.setData({ notice: '已取消本次人工归档；原配网请求结果仍待核对。' })
              return
            }
            this.setData({ archiveSubmitting: true })
            archived = await this.provision.confirmArchive(token) === true
            if (!current()) return
            if (archived) {
              this.privatePassword = ''; this.ssidDraft = ''
              this.setData({ ssid: '', hotspotReady: false, ...archiveCheckData([]),
                formKeys: [this.inputGeneration = (this.inputGeneration || 0) + 1],
                notice: '原请求已人工归档，结果仍未知。原窗口不能复用；新配网需要操作员开启新窗口、扫描新二维码并重新确认。' })
            } else this.setData({ notice: '人工归档未完成；请保留原编号并重新核对，不据此判断联网或重复配网。' })
          } catch (_) {
            if (current()) this.setData({ notice: '人工归档未完成；请保留原编号并重新核对，不据此判断联网或重复配网。' })
          } finally {
            if (current()) {
              this.confirmation = null
              if (!archived) cancel()
              this.render(this.provision.getState())
            }
            resolve()
          }
        }, fail: failure }
      try { wx.showModal(modal) } catch (_) { failure() }
    })
  },
  async scanInvitation() {
    const epoch = this.viewEpoch
    if (!active(this, epoch) || !this.data.androidAvailable || this.data.busy || this.confirmation) return
    if (this.data.localNetworkSource) { this.setData({ notice: '本次使用猫端私有交接。切换网络后请返回猫端配网页，重扫同一机身卡并核对原窗口，不扫描其它窗口覆盖。' }); return }
    this.privatePassword = ''
    this.ssidDraft = ''
    this.setData({ ssid: '', formKeys: [], notice: '', ...archiveCheckData([]) })
    await this.provision.scan()
    if (active(this, epoch)) {
      this.setData({ formKeys: [this.inputGeneration = (this.inputGeneration || 0) + 1] })
      this.render(this.provision.getState())
    }
  },
  async configure() {
    const epoch = this.viewEpoch
    if (!active(this, epoch) || !this.data.androidAvailable || !this.data.canConfigure || this.confirmation) return
    const token = this.provision.prepare()
    if (!token || !active(this, epoch)) return
    const target = { token, epoch }
    this.confirmation = target
    this.setData({ notice: '' })
    this.render(this.provision.getState())
    return new Promise<void>(resolve => {
      const current = () => active(this, epoch) && this.confirmation === target
      const failure = () => {
        if (current()) {
          this.confirmation = null
          this.setData({ notice: '网络信息未发送：确认弹窗打开失败，请重新操作。' })
          this.provision.cancel(token)
          this.render(this.provision.getState())
        }
        resolve()
      }
      const route = this.data.transportMode === 'direct'
        ? '当前模式：本地直连兼容。由系统选路，不强制绑定 Wi-Fi；自检不是身份认证，也不代表配网成功。'
        : '当前模式：标准 Wi-Fi 绑定。使用绑定到当前 Wi-Fi 的本地通道。'
      const modal = { title: '更改 AI 猫的 Wi-Fi？', confirmText: '确认配网', cancelText: '暂不配网',
        content: route + '将读取手机当前 Wi-Fi 名称和热点标识（BSSID），仅在内存核对设备热点，不保存或上传这些信息。随后加密发送目标 Wi-Fi，改变猫的网络；SSH 与网页可能暂时中断。须已手动连接设备热点，不等于云账号绑定。结果未知时只核对原请求，不自动重配。确认只发送一次？',
        success: async (answer: any) => {
          if (!current()) { resolve(); return }
          this.confirmation = null
          try {
            if (!answer || answer.confirm !== true) {
              this.setData({ notice: '网络信息未发送：已取消本次确认。' })
              this.provision.cancel(token)
              return
            }
            await this.provision.confirm(token)
          } finally {
            if (active(this, epoch)) {
              const value = this.provision.getState()
              if (!value.networkValid) {
                this.privatePassword = ''
                this.setData({ formKeys: [this.inputGeneration = (this.inputGeneration || 0) + 1] })
              }
              this.render(value)
            }
            resolve()
          }
        }, fail: failure }
      try { wx.showModal(modal) } catch (_) { failure() }
    })
  },
  async queryOriginal() {
    const epoch = this.viewEpoch
    if (!active(this, epoch) || !this.data.androidAvailable || !this.data.canQuery || this.confirmation) return
    this.setData({ notice: '', ...archiveCheckData([]) })
    await this.provision.query()
    if (active(this, epoch)) this.render(this.provision.getState())
  },
  async scanFinalReceipt() {
    const epoch = this.viewEpoch
    if (!active(this, epoch) || !this.data.androidAvailable || this.data.busy || this.confirmation) return
    if (this.data.localNetworkSource) { this.returnToLocalNetwork(); return }
    if (typeof this.provision.scanReceipt !== 'function') {
      this.setData({ notice: '当前版本尚不能扫描收尾回执；不据此判断配网成功，请由操作员核对原请求。' })
      return
    }
    if (!this.data.canScanReceipt) return
    this.setData({ notice: '', ...archiveCheckData([]) })
    await this.provision.scanReceipt()
    if (active(this, epoch)) this.render(this.provision.getState())
  },
  returnToLocalNetwork() {
    if (!this.pageVisible || this.data.busy || this.confirmation) return
    if (typeof callbacks.onBack === 'function') { callbacks.onBack.call(this); return }
    wx.navigateTo({ url: '/pages/local-network/index' })
  }
  }
}

module.exports = { createProvisionFlowDefinition }
