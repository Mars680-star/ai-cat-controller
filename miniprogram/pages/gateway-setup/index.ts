const { createCloudAuthClient, cloudAuthErrorMessage } = require('../../services/cloud-auth-client')
const { developmentGatewayInfo } = require('../../services/development-gateway')

const BATTERY: any = { charging: '正在充电', discharging: '使用电池', full: '已充满', not_charging: '已接电，未充电', unknown: '未验证', unavailable: '未验证' }
const VOICE: any = {
  starting: '语音启动中', connecting: '语音连接中', ready: '等待唤醒', waiting_wake: '等待唤醒',
  wake_detected: '已唤醒', listening: '聆听中', thinking: '思考中', answering: '回答中',
  followup_listening: '等待追问', waiting_follow_up: '等待追问', interrupted: '已打断', echo_guard: '回声保护',
  recovering: '语音恢复中', waking: '正在请求聆听', interrupting: '正在请求打断', submitting_text: '正在提交文字',
  offline: '语音服务离线', unavailable: '语音状态未验证'
}
const valueText = (value: any) => value === null || value === undefined || value === '' ? '未验证' : String(value)
const yesNo = (value: any, yes: string, no: string) => value === true ? yes : value === false ? no : '未验证'
const emptyStatus = (label = '请选择已授权设备') => ({
  snapshot: null, statusLoading: false, statusError: '', deviceOnline: false,
  deviceStatusLabel: label, deviceRows: [], dialogRows: [], sampledLabel: '尚未采样'
})
const authFailure = (error: any) => error && ['UNAUTHORIZED', 'AUTH_REQUIRED'].includes(error.code)

Page({
  data: {
    busy: false, error: '', configured: false, signedIn: false,
    gatewayAddress: '', transportNotice: '',
    statusLabel: '尚未检查', userId: '', expiresAt: '', petCount: 0,
    pets: [], listLoading: false, listError: '', selectedPetId: '', selectedPetName: '', ...emptyStatus()
  },
  onShow() {
    if (this.disposed) return
    this.visible = true
    this.visit = (this.visit || 0) + 1
    this.client = this.client || createCloudAuthClient(wx)
    const connection = developmentGatewayInfo(wx)
    this.setData({ gatewayAddress: connection.baseUrl, transportNotice: connection.notice })
    this.invalidateIdentity()
    this.client.clearSession()
    this.setData({ busy: false, error: '', configured: false, signedIn: false,
      statusLabel: '尚未检查', userId: '', expiresAt: '', petCount: 0, pets: [],
      listLoading: false, listError: '', selectedPetId: '', selectedPetName: '', ...emptyStatus() })
    return this.checkGateway()
  },
  onHide() { this.leavePage() },
  onUnload() { this.leavePage(); this.disposed = true },
  stopPolling() { clearTimeout(this.statusTimer); this.statusTimer = null },
  invalidateIdentity() {
    this.identityVersion = (this.identityVersion || 0) + 1
    this.selectionVersion = (this.selectionVersion || 0) + 1
    this.authorizationReady = false
    this.listFlight = null
    this.statusFlight = null
    this.statusWaiter = null
    this.stopPolling()
  },
  leavePage() {
    this.visible = false
    this.visit = (this.visit || 0) + 1
    this.invalidateIdentity()
    if (this.client) this.client.clearSession()
    this.setData({ busy: false, signedIn: false, configured: false,
      statusLabel: '尚未检查', userId: '', expiresAt: '', petCount: 0, error: '', pets: [],
      listLoading: false, listError: '', selectedPetId: '', selectedPetName: '', ...emptyStatus() })
  },
  current(visit: number) { return this.visible && !this.disposed && this.visit === visit },
  identityCurrent(visit: number, identity: number) { return this.current(visit) && this.identityVersion === identity },
  selectionCurrent(context: any) {
    return this.identityCurrent(context.visit, context.identity) && this.data.signedIn && this.authorizationReady &&
      !this.data.listLoading && this.selectionVersion === context.selection && this.data.selectedPetId === context.petId &&
      this.data.pets.some((pet: any) => pet.petId === context.petId)
  },
  clearIdentity(statusLabel: string, error = '', configured = this.data.configured) {
    this.invalidateIdentity()
    this.client.clearSession()
    this.setData({ busy: false, signedIn: false, configured, statusLabel, error,
      userId: '', expiresAt: '', petCount: 0, pets: [], listLoading: false, listError: '',
      selectedPetId: '', selectedPetName: '', ...emptyStatus() })
  },
  async onPullDownRefresh() {
    const visit = this.visit, identity = this.identityVersion
    try {
      if (this.data.signedIn) {
        await this.refreshPets()
        if (this.identityCurrent(visit, identity) && this.data.signedIn && this.data.selectedPetId) await this.refreshStatus()
      } else await this.checkGateway()
    } finally { if (this.current(visit)) wx.stopPullDownRefresh() }
  },
  async checkGateway() {
    if (!this.visible || this.disposed || this.data.busy) return
    const visit = this.visit, identity = this.identityVersion
    this.stopPolling()
    this.setData({ busy: true, error: '' })
    try {
      const health = await this.client.getHealth()
      if (!this.identityCurrent(visit, identity)) return
      this.setData({ configured: health.wechatConfigured, statusLabel: this.data.signedIn ? '微信身份验证成功' :
        health.wechatConfigured ? '网关已就绪，可验证微信登录' : '等待配置微信凭据' })
    } catch (error) {
      if (!this.identityCurrent(visit, identity)) return
      if (authFailure(error)) this.clearIdentity('登录已失效，请重新验证', cloudAuthErrorMessage(error), false)
      else this.setData({ configured: false, statusLabel: this.data.signedIn ? '已登录 · 网关检查未更新' : '网关暂不可达',
        error: cloudAuthErrorMessage(error), ...emptyStatus('网关检查未更新，请重试'), statusLoading: Boolean(this.statusFlight) })
    } finally {
      if (this.identityCurrent(visit, identity)) { this.setData({ busy: false }); this.startPolling() }
    }
  },
  async login() {
    if (!this.visible || this.disposed || this.data.busy || !this.data.configured || this.data.signedIn) return
    this.invalidateIdentity()
    const visit = this.visit, identity = this.identityVersion
    this.setData({ busy: true, error: '', pets: [], petCount: 0, listError: '',
      selectedPetId: '', selectedPetName: '', ...emptyStatus() })
    try {
      const session = await this.client.login()
      if (!this.identityCurrent(visit, identity)) return
      const pets = await this.client.listPets()
      if (!this.identityCurrent(visit, identity)) return
      this.authorizationReady = true
      this.setData({ signedIn: true, userId: session.userId, expiresAt: session.expiresAt,
        pets, petCount: pets.length, statusLabel: '微信身份验证成功' })
    } catch (error) {
      if (this.identityCurrent(visit, identity)) this.clearIdentity('登录未完成', cloudAuthErrorMessage(error))
    } finally { if (this.identityCurrent(visit, identity)) this.setData({ busy: false }) }
  },
  refreshPets() {
    if (!this.visible || this.disposed || !this.data.signedIn || this.data.busy) return Promise.resolve()
    const visit = this.visit, identity = this.identityVersion
    if (this.listFlight && this.listFlight.visit === visit && this.listFlight.identity === identity) return this.listFlight.promise
    this.stopPolling()
    this.selectionVersion = (this.selectionVersion || 0) + 1
    this.statusWaiter = null
    this.authorizationReady = false
    this.setData({ listLoading: true, listError: '', ...emptyStatus('正在核对设备授权') })
    const flight: any = { visit, identity }
    this.listFlight = flight
    flight.promise = this.client.listPets().then((pets: any[]) => {
      if (!this.identityCurrent(visit, identity) || this.listFlight !== flight) return
      const selected = pets.find(pet => pet.petId === this.data.selectedPetId)
      const lostSelection = Boolean(this.data.selectedPetId && !selected)
      this.authorizationReady = true
      this.setData({ pets, petCount: pets.length,
        selectedPetId: selected ? selected.petId : '', selectedPetName: selected ? selected.name : '',
        ...emptyStatus(lostSelection ? '设备授权已移除，请重新选择' : selected ? '正在读取真实状态' : '请选择已授权设备') })
    }).catch((error: any) => {
      if (!this.identityCurrent(visit, identity) || this.listFlight !== flight) return
      if (authFailure(error)) { this.clearIdentity('登录已失效，请重新验证', cloudAuthErrorMessage(error)); return }
      this.authorizationReady = false
      this.setData({ pets: [], petCount: 0, selectedPetId: '', selectedPetName: '',
        listError: cloudAuthErrorMessage(error), ...emptyStatus('授权列表未更新，请重试') })
    }).finally(() => {
      if (!this.identityCurrent(visit, identity) || this.listFlight !== flight) return
      this.listFlight = null
      this.setData({ listLoading: false })
      if (this.authorizationReady && this.data.selectedPetId) this.refreshStatus()
    })
    return flight.promise
  },
  selectPet(event: any) {
    if (!this.visible || this.disposed || !this.data.signedIn || !this.authorizationReady || this.data.busy || this.data.listLoading) return Promise.resolve()
    const petId = event.currentTarget.dataset.petId
    const pet = this.data.pets.find((item: any) => item.petId === petId)
    if (!pet || pet.petId === this.data.selectedPetId) return Promise.resolve()
    this.stopPolling()
    this.selectionVersion = (this.selectionVersion || 0) + 1
    this.statusWaiter = null
    this.setData({ selectedPetId: pet.petId, selectedPetName: pet.name, ...emptyStatus('正在读取真实状态') })
    return this.refreshStatus()
  },
  refreshStatus() {
    if (!this.visible || this.disposed || !this.data.signedIn || !this.authorizationReady || !this.data.selectedPetId || this.data.listLoading || this.data.busy) return Promise.resolve()
    const context = { visit: this.visit, identity: this.identityVersion, selection: this.selectionVersion, petId: this.data.selectedPetId }
    if (!this.selectionCurrent(context)) return Promise.resolve()
    this.stopPolling()
    if (this.statusFlight) {
      if (this.statusFlight.visit === context.visit && this.statusFlight.identity === context.identity && this.statusFlight.selection === context.selection) return this.statusFlight.promise
      if (this.statusWaiter && this.statusWaiter.selection === context.selection && this.statusWaiter.identity === context.identity) return this.statusWaiter.promise
      this.setData({ statusLoading: true })
      const waiter: any = { ...context }
      this.statusWaiter = waiter
      // No per-request abort: preserve the session and wait for the old selection to settle.
      waiter.promise = this.statusFlight.promise.then(() => {
        if (this.selectionCurrent(context)) return this.refreshStatus()
      }).finally(() => { if (this.statusWaiter === waiter) this.statusWaiter = null })
      return waiter.promise
    }
    const flight: any = { ...context }
    this.statusFlight = flight
    this.setData({ statusLoading: true, statusError: '' })
    flight.promise = this.client.readPetStatus(context.petId).then((snapshot: any) => {
      if (this.selectionCurrent(context) && this.statusFlight === flight) this.applySnapshot(snapshot)
    }).catch((error: any) => {
      if (!this.identityCurrent(context.visit, context.identity)) return
      if (authFailure(error)) { this.clearIdentity('登录已失效，请重新验证', cloudAuthErrorMessage(error)); return }
      if (!this.selectionCurrent(context) || this.statusFlight !== flight) return
      this.setData({ ...emptyStatus('状态暂不可达，请重试'), statusError: cloudAuthErrorMessage(error) })
    }).finally(() => {
      if (this.statusFlight === flight) this.statusFlight = null
      if (!this.selectionCurrent(context)) return
      this.setData({ statusLoading: false })
      this.startPolling()
    })
    return flight.promise
  },
  startPolling() {
    this.stopPolling()
    const context = { visit: this.visit, identity: this.identityVersion, selection: this.selectionVersion, petId: this.data.selectedPetId }
    if (!this.selectionCurrent(context) || this.data.busy || this.statusFlight) return
    this.statusTimer = setTimeout(() => {
      this.statusTimer = null
      if (this.selectionCurrent(context)) this.refreshStatus()
    }, 5000)
  },
  applySnapshot(snapshot: any) {
    const { device, dialog } = snapshot
    const stale = dialog.stale === true
    this.setData({ snapshot, statusError: '', deviceOnline: device.connected === true,
      deviceStatusLabel: device.connected ? '真实状态 · 设备在线' : '真实状态 · 设备未连接', sampledLabel: snapshot.sampledAt,
      deviceRows: [
        { label: '设备连接', value: yesNo(device.connected, '在线', '未连接') },
        { label: '脱敏编号', value: valueText(device.deviceSerial) },
        { label: '电量', value: device.batteryPercent === null || device.batteryPercent === undefined ? '未验证' : device.batteryPercent + '%' },
        { label: '电压', value: device.batteryVoltage === null || device.batteryVoltage === undefined ? '未验证' : device.batteryVoltage + ' V' },
        { label: '电池状态', value: BATTERY[device.batteryState] || '未验证' },
        { label: '充电器', value: yesNo(device.chargerOnline, '已连接', '未连接') },
        { label: '电池检测', value: yesNo(device.batteryPresent, '已安装', '未检测到') }
      ],
      dialogRows: [
        { label: '语音状态', value: stale ? '未验证（语音状态已过期）' : VOICE[dialog.state] || '语音状态未验证' },
        { label: '语音会话', value: stale ? '未验证（语音状态已过期）' : yesNo(dialog.sessionActive, '活跃', '未活跃') },
        { label: '语音数据新鲜度', value: stale ? '已过期，不作为当前语音状态' : '本次已验证' }
      ]
    })
  },
  async logout() {
    if (!this.visible || this.disposed || this.data.busy || !this.data.signedIn) return
    this.invalidateIdentity()
    const visit = this.visit, identity = this.identityVersion
    this.setData({ busy: true, signedIn: false, error: '', pets: [], petCount: 0, listLoading: false, listError: '',
      userId: '', expiresAt: '', selectedPetId: '', selectedPetName: '', ...emptyStatus() })
    try {
      await this.client.logout()
      if (this.identityCurrent(visit, identity)) this.setData({ statusLabel: '业务会话已注销' })
    } catch (error) {
      if (this.identityCurrent(visit, identity)) this.setData({ statusLabel: '本页已退出',
        error: '服务端注销未确认，旧会话将按有效期失效；请恢复连接后重新验证。' })
    } finally {
      if (this.identityCurrent(visit, identity)) {
        this.client.clearSession()
        this.setData({ busy: false })
      }
    }
  }
})
