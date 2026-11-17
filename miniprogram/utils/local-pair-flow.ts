// Shared card scanner/confirmation lifecycle for an inline component and legacy
// page. Card text, keys and bearer stay in callback/session closures, not data.
const { localRuntimeAvailable, verifyLocalAddress } = require('../services/local-device-client')
const { createLocalDeviceDiscovery } = require('../services/local-device-discovery')
const { buildLiveDeviceView } = require('./live-device-view')
const HOTSPOT_ADDRESS = '10.47.0.1'

function createLocalPairFlow(wxApi: any, getSession: any, options: any = {}) {
  function current(host: any, visit: number) { return host.visible && host.visit === visit }
  function contextMatches(state: any, context: any) {
    const value = state && state.localContext
    return state && state.authKind === 'local' && state.signedIn === true && value &&
      value.deviceId === context.deviceId && value.cardId === context.cardId && value.address === context.address
  }
  function notify(host: any, name: string, detail: any) {
    if (typeof options[name] === 'function') options[name](host, detail)
  }
  function present(host: any, state: any) {
    if (!host.visible) return
    let address = host.data.address
    let contextAddress = ''
    if (state.authKind === 'local' && state.localContext) {
      try { contextAddress = verifyLocalAddress(state.localContext.address) } catch (_) {}
      if (contextAddress && contextAddress !== HOTSPOT_ADDRESS) host.recentStationAddress = contextAddress
    }
    if (!address && host.allowContextPrefill !== false) {
      // A remembered AP is not a candidate for an ordinary-network step.
      if (contextAddress && (contextAddress !== HOTSPOT_ADDRESS || host.pairAddressHint === HOTSPOT_ADDRESS)) address = contextAddress
      else if (host.recentStationAddress) address = host.recentStationAddress
    }
    const paired = Boolean(state.mode === 'live' && state.authKind === 'local' && state.signedIn === true && state.localContext && state.localContext.address === address)
    const view = buildLiveDeviceView(state, true)
    const snapshot = paired && view.hasSnapshot ? state.snapshot : null, device = snapshot && snapshot.device
    const battery = device && device.batteryPercent
    const patch: any = { state: { ...state, snapshot }, paired, selectedPetId: paired ? view.selectedPetId : '',
      pairedName: paired && view.selectedName ? view.selectedName : '我的狐狸',
      statusText: paired ? device ? device.connected ? '设备在线' : '设备未连接' : state.statusLoading ? '正在读取状态' : state.error ? '状态暂不可用' : state.loading || state.listLoading ? '正在核对授权' : '已配对 · 状态未验证' : '尚未连接',
      batteryText: typeof battery === 'number' && Number.isFinite(battery) ? battery + '%' : '--',
      message: state.authKind === 'local' ? state.message : '手机与设备连接同一 Wi-Fi，再扫描私有机身卡。' }
    if (!host.data.address && address) { patch.address = address; patch.addressExpanded = host.stationAddressReview === true }
    host.setData(patch)
    notify(host, 'onState', { paired, busy: Boolean(host.data.busy), selectedPetId: patch.selectedPetId, address: host.data.address })
  }
  return {
    data: { available: false, address: '', addressExpanded: true, detailsExpanded: false,
      scanning: false, cameraKeys: [], confirming: false, busy: false, error: '',
      message: '手机与设备连接同一 Wi-Fi，再扫描私有机身卡。', state: {}, paired: false,
      pairedName: '我的狐狸', selectedPetId: '', statusText: '尚未连接', batteryText: '--',
      discoveryAvailable: false, discovering: false, candidates: [], discoveryHint: '' },
    onLoad(input: any) {
      if (input && input.address !== undefined) this.prefillAddress(input.address)
    },
    prefillAddress(value: any) {
      try {
        const hint = value === '' ? '' : verifyLocalAddress(value)
        // Parent renders can repeat the same property value; never erase a
        // manually selected address or cancel its scanner on such an update.
        if (this.pairAddressHint === hint) return
        const stationTransition = hint === '' && (this.pairAddressHint === HOTSPOT_ADDRESS || this.data.address === HOTSPOT_ADDRESS)
        this.pairAddressHint = hint
        this.pairAddressHintVersion = (this.pairAddressHintVersion || 0) + 1
        this.allowContextPrefill = true
        this.stationAddressReview = stationTransition
        if (this.session && this.intent) this.session.cancelLocal(this.intent)
        this.intent = null
        if (this.discovery) this.discovery.stop()
        this.setData({ address: hint || (stationTransition ? '' : this.data.address), addressExpanded: !hint,
          scanning: false, cameraKeys: [], confirming: false, paired: false, selectedPetId: '',
          batteryText: '--', statusText: '尚未连接', state: { ...this.data.state, snapshot: null }, error: '' })
        if (this.visible && this.session) present(this, this.session.getState())
      }
      catch (_) { this.setData({ addressExpanded: true, error: '设备地址未通过校验，请填写当前局域网 IPv4。' }) }
    },
    onShow() {
      this.pause()
      this.visible = true; this.visit = (this.visit || 0) + 1; this.intent = null
      this.session = getSession()
      if (!this.discovery) this.discovery = createLocalDeviceDiscovery(wxApi)
      this.discovery.show((value: any) => { if (this.visible) this.setData(value) })
      this.setData({ available: localRuntimeAvailable(wxApi), scanning: false, cameraKeys: [], confirming: false, busy: false, error: '' })
      this.detach = this.session.attach((state: any) => present(this, state))
    },
    onHide() { this.pause() }, onUnload() { this.pause() },
    pause() {
      this.visible = false; this.visit = (this.visit || 0) + 1
      if (this.session && this.intent) this.session.cancelLocal(this.intent)
      this.intent = null
      if (this.discovery) this.discovery.hide()
      if (this.detach) this.detach()
      this.detach = null
    },
    inputAddress(event: any) {
      if (this.visible && !this.data.busy && !this.data.scanning && !this.data.confirming) {
        this.allowContextPrefill = false
        this.setData({ address: event.detail.value, error: '' })
        if (this.session) present(this, this.session.getState())
      }
    },
    toggleAddress() {
      if (!this.visible || this.data.busy || this.data.scanning || this.data.confirming) return
      if (this.data.addressExpanded && !this.data.address) return
      this.setData({ addressExpanded: !this.data.addressExpanded })
    },
    toggleDetails() { if (this.visible) this.setData({ detailsExpanded: !this.data.detailsExpanded }) },
    discover() { if (this.visible && !this.data.busy && !this.data.confirming && !this.data.scanning) this.discovery.start() },
    stopDiscovery() { if (this.visible) this.discovery.stop('查找已停止；选择地址后仍需扫描机身卡确认。') },
    chooseCandidate(event: any) {
      if (!this.visible || this.data.busy || this.data.confirming || this.data.scanning) return
      const address = event && event.currentTarget && event.currentTarget.dataset && event.currentTarget.dataset.address
      if (this.data.candidates.some((item: any) => item.address === address)) {
        this.allowContextPrefill = false
        this.setData({ address, addressExpanded: false, error: '' })
        if (this.session) present(this, this.session.getState())
      }
    },
    scanCard() {
      if (!this.visible || !this.data.available || this.data.busy || this.data.confirming || this.data.scanning) return
      try { verifyLocalAddress(this.data.address) }
      catch (_) { this.setData({ addressExpanded: true, error: '先查找设备或填写设备 IP，再扫描机身卡；不填写网址、端口或命令。' }); return }
      this.discovery.stop()
      this.setData({ scanning: true, cameraKeys: [this.cameraGeneration = (this.cameraGeneration || 0) + 1], error: '' })
    },
    cancelScan() { if (this.visible) this.setData({ scanning: false, cameraKeys: [] }) },
    cameraError(event: any) {
      if (this.visible && this.data.scanning && this.data.cameraKeys[0] === (event && event.currentTarget && event.currentTarget.dataset && event.currentTarget.dataset.scanId))
        this.setData({ scanning: false, cameraKeys: [], error: '相机未能启动，请检查微信相机权限后再扫机身卡。' })
    },
    cameraDecoded(event: any) {
      if (!this.visible || !this.data.scanning || this.data.cameraKeys[0] !== (event && event.currentTarget && event.currentTarget.dataset && event.currentTarget.dataset.scanId)) return
      const visit = this.visit, hintVersion = this.pairAddressHintVersion || 0, detail = event && event.detail
      this.setData({ scanning: false, cameraKeys: [] })
      let prepared: any
      try {
        if (!detail || detail.type !== undefined && !['QR_CODE', 'qrCode', 'qrcode', 'qr_code'].includes(detail.type)) throw new Error('card')
        prepared = this.session.prepareLocal(detail.result, this.data.address)
      } catch (_) { this.setData({ error: '机身卡或地址未通过校验，或原连接仍有未决请求；请保留原记录，不清数据绕过。' }); return }
      this.intent = prepared.token; this.setData({ confirming: true, error: '' })
      let answered = false
      const cancel = () => { this.session.cancelLocal(prepared.token); if (this.intent === prepared.token) this.intent = null }
      try {
        wxApi.showModal({ title: '连接这台狐狸？',
          content: '连接 ' + prepared.context.address + ':8789\n设备 ' + prepared.context.deviceId + '\n使用私有机身卡本地授权，不是微信登录。请确认是你的设备且同处受信 Wi-Fi。',
          confirmText: '确认连接', cancelText: '取消',
          success: async (answer: any) => {
            if (answered) return
            answered = true
            if (!current(this, visit) || this.intent !== prepared.token || (this.pairAddressHintVersion || 0) !== hintVersion) { cancel(); return }
            if (!answer || answer.confirm !== true) { cancel(); this.setData({ confirming: false }); return }
            this.intent = null; this.setData({ confirming: false, busy: true })
            try {
              await this.session.confirmLocal(prepared.token)
              if (!current(this, visit) || (this.pairAddressHintVersion || 0) !== hintVersion) return
              let state = this.session.getState()
              if (!contextMatches(state, prepared.context)) return
              // The card-authorized gateway may expose exactly one expected
              // local alias. Selecting it performs only the existing read path.
              const expectedPet = 'lp_' + prepared.context.deviceId.slice(4)
              if (!state.selectedPetId && Array.isArray(state.pets) && state.pets.length === 1 && state.pets[0].petId === expectedPet)
                await this.session.selectPet(expectedPet)
              if (!current(this, visit) || (this.pairAddressHintVersion || 0) !== hintVersion) return
              state = this.session.getState()
              if (!contextMatches(state, prepared.context)) return
              notify(this, 'onPaired', { deviceId: prepared.context.deviceId, cardId: prepared.context.cardId,
                address: prepared.context.address, selectedPetId: state.selectedPetId || '' })
            } catch (_) { if (current(this, visit)) this.setData({ error: '连接未完成，请核对同网地址和机身卡；原控制记录会保留。' }) }
            finally { if (current(this, visit)) { this.setData({ busy: false }); present(this, this.session.getState()) } }
          },
          fail: () => { if (answered) return; answered = true; cancel(); if (current(this, visit)) this.setData({ confirming: false, error: '确认窗口未完成，本次未提交配对。' }) }
        })
      } catch (_) { if (!answered) { answered = true; cancel(); if (current(this, visit)) this.setData({ confirming: false, error: '确认窗口不可用，本次未提交配对。' }) } }
    },
    openControls() { if (this.visible && this.data.paired && !this.data.busy) notify(this, 'onContinue', {}) },
    openLocalNetwork() { if (this.visible && this.data.paired && !this.data.busy) notify(this, 'onNetwork', {}) },
    selectPet(event: any) {
      if (!this.visible || this.data.busy) return
      const petId = event && event.detail && event.detail.petId || event && event.currentTarget && event.currentTarget.dataset && event.currentTarget.dataset.petId
      if (typeof petId === 'string') return this.session.selectPet(petId)
    },
    refreshState() { if (this.visible && !this.data.busy) return this.session.refresh() },
    refreshList() { if (this.visible && !this.data.busy) return this.session.refreshPets() },
    logoutLocal() { if (this.visible && !this.data.busy) return this.session.logout() },
    async useComputer() {
      if (!this.visible || this.data.busy || this.data.confirming || this.data.scanning) return
      const visit = this.visit; this.setData({ busy: true, error: '' })
      try { await this.session.useComputerGateway(); if (current(this, visit)) notify(this, 'onComputer', {}) }
      catch (_) { if (current(this, visit)) this.setData({ error: '原连接有未决请求或存储不可验证，不能切换；回原上下文核对，不清记录。' }) }
      finally { if (current(this, visit)) { this.setData({ busy: false }); present(this, this.session.getState()) } }
    }
  }
}

module.exports = { createLocalPairFlow }
