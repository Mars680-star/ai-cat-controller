const { createDevGatewayClient, gatewayErrorMessage } = require('../../services/dev-gateway-client')
const BATTERY: any = { charging: '正在充电', discharging: '使用电池', full: '已充满', not_charging: '已接电，未充电', unknown: '未验证', unavailable: '未验证' }
const DIALOG: any = {
  starting: '启动中', connecting: '连接中', ready: '等待唤醒', waiting_wake: '等待唤醒',
  wake_detected: '已唤醒', listening: '聆听中', processing_audio: '处理语音中', thinking: '思考中', answering: '回答中',
  followup_preparing: '准备追问', connected: '语音已连接', proactive_queued: '主动问候等待中',
  proactive_speaking: '主动问候中', proactive_finishing: '主动问候收尾中', stopping: '语音停止中',
  followup_listening: '等待追问', waiting_follow_up: '等待追问', interrupted: '已打断',
  echo_guard: '回声保护', recovering: '恢复中', waking: '正在请求聆听', interrupting: '正在请求打断', submitting_text: '文字问题提交中', offline: '离线', unavailable: '未验证'
}
const text = (value: any) => value === null || value === undefined || value === '' ? '未验证' : String(value)
const yesNo = (value: any, yes: string, no: string) => value === true ? yes : value === false ? no : '未验证'

Page({
  data: {
    statusLabel: '未验证', statusTone: 'neutral', requesting: false, error: '',
    hasToken: false, tokenInputVisible: true, snapshot: null, online: false,
    healthRows: [], deviceRows: [], dialogRows: [], sampledLabel: '尚未采样'
  },
  onShow() {
    if (this.disposed) return
    this.visible = true
    this.visit = (this.visit || 0) + 1
    this.cancelRead()
    this.debugToken = ''
    this.pollingEnabled = false
    this.setData({ hasToken: false, tokenInputVisible: true, requesting: false, error: '', snapshot: null, online: false,
      statusLabel: '未验证', statusTone: 'neutral', healthRows: [], deviceRows: [], dialogRows: [], sampledLabel: '尚未采样' })
  },
  onHide() { this.leave(false) },
  onUnload() { this.leave(true) },
  leave(disposed: boolean) {
    this.visible = false
    this.disposed = disposed || this.disposed
    this.visit = (this.visit || 0) + 1
    this.debugToken = ''
    this.pollingEnabled = false
    this.cancelRead()
    // Removing the native input clears its value without copying the token to view data.
    this.setData({ hasToken: false, tokenInputVisible: false, requesting: false, snapshot: null, online: false,
      statusLabel: '未验证', statusTone: 'neutral', healthRows: [], deviceRows: [], dialogRows: [], error: '' })
  },
  current(visit: number, version: number) { return this.visible && !this.disposed && this.visit === visit && this.readVersion === version },
  cancelRead() {
    clearTimeout(this.pollTimer)
    this.pollTimer = null
    const flight = this.readFlight
    this.readFlight = null
    if (flight) flight.request.abort()
  },
  onTokenInput(event: any) {
    if (!this.visible || this.disposed) return
    this.readVersion = (this.readVersion || 0) + 1
    this.cancelRead()
    this.pollingEnabled = false
    this.debugToken = String(event.detail.value || '')
    this.setData({ hasToken: /^[A-Za-z0-9._~-]{24,256}$/.test(this.debugToken), requesting: false, error: '',
      snapshot: null, online: false, statusLabel: '未验证', statusTone: 'neutral', healthRows: [], deviceRows: [], dialogRows: [], sampledLabel: '尚未采样' })
  },
  async onPullDownRefresh() {
    const visit = this.visit
    await this.refresh()
    if (this.visible && this.visit === visit) wx.stopPullDownRefresh()
  },
  refresh() {
    if (!this.visible || this.disposed) return Promise.resolve()
    if (this.readFlight) return this.readFlight.promise
    if (!this.data.hasToken) {
      this.setData({ error: gatewayErrorMessage({ code: 'TOKEN_REQUIRED' }) })
      return Promise.resolve()
    }
    clearTimeout(this.pollTimer)
    this.pollTimer = null
    this.pollingEnabled = true
    const visit = this.visit
    const version = this.readVersion = (this.readVersion || 0) + 1
    if (!this.client) this.client = createDevGatewayClient(wx)
    const request = this.client.readStatus(this.debugToken)
    const flight: any = { visit, version, request }
    this.readFlight = flight
    this.setData({ requesting: true, error: '' })
    flight.promise = request.promise.then((snapshot: any) => {
      if (!this.current(visit, version) || this.readFlight !== flight) return
      this.applySnapshot(snapshot)
    }).catch((error: any) => {
      if (!this.current(visit, version) || this.readFlight !== flight) return
      if (['UNAUTHORIZED', 'TOKEN_REQUIRED', 'UNVERIFIED'].includes(error && error.code)) this.pollingEnabled = false
      const unverified = ['UNAUTHORIZED', 'TOKEN_REQUIRED', 'UNVERIFIED'].includes(error && error.code)
      this.setData({ requesting: false, snapshot: null, online: false, healthRows: [], deviceRows: [], dialogRows: [], sampledLabel: '尚未采样',
        statusLabel: unverified ? '未验证' : '不可达', statusTone: 'danger', error: gatewayErrorMessage(error) })
    }).finally(() => {
      if (!this.current(visit, version) || this.readFlight !== flight) return
      this.readFlight = null
      this.setData({ requesting: false })
      if (this.pollingEnabled && this.debugToken) {
        this.pollTimer = setTimeout(() => { this.pollTimer = null; if (this.current(visit, version)) this.refresh() }, 5000)
      }
    })
    return flight.promise
  },
  applySnapshot(snapshot: any) {
    const { health, device, dialog } = snapshot
    const stale = dialog.stale === true
    const dialogValue = (value: any) => stale ? '未验证（状态已过期）' : text(value)
    this.setData({ snapshot, statusLabel: stale ? '未验证' : '真实状态', statusTone: stale ? 'neutral' : 'success', online: device.connected === true,
      sampledLabel: snapshot.sampledAt,
      healthRows: [{ label: '健康检查', value: health.status }, { label: '软件版本', value: health.version }, { label: '设备适配器', value: health.adapter }],
      deviceRows: [
        { label: '连接读数', value: yesNo(device.connected, '已连接', '未连接') },
        { label: '设备序列号（已掩码）', value: text(device.deviceSerial) },
        { label: '电量', value: device.batteryPercent === null || device.batteryPercent === undefined ? '未验证' : device.batteryPercent + '%' },
        { label: '电池状态', value: BATTERY[device.batteryState] || '未验证' },
        { label: '电压', value: device.batteryVoltage === null || device.batteryVoltage === undefined ? '未验证' : device.batteryVoltage + ' V' },
        { label: '充电器', value: yesNo(device.chargerOnline, '已连接', '未连接') },
        { label: '电池检测', value: yesNo(device.batteryPresent, '已安装', '未检测到') }
      ],
      dialogRows: [
        { label: '对话状态', value: dialogValue(DIALOG[dialog.state] || '未验证') },
        { label: '距状态更新时间', value: dialogValue(dialog.elapsedSeconds === null ? null : dialog.elapsedSeconds + ' 秒') },
        { label: '追问剩余时间', value: dialogValue(dialog.followUpRemaining === null ? null : dialog.followUpRemaining + ' 秒') },
        { label: '可打断标记（只读）', value: dialogValue(yesNo(dialog.canInterrupt, '是', '否')) },
        { label: '会话活跃标记', value: dialogValue(yesNo(dialog.sessionActive, '是', '否')) },
        { label: '数据新鲜度', value: stale ? '已过期，等待重新验证' : '本次采样' }
      ]
    })
  }
})
