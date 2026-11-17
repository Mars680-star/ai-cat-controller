// Pure, defensive presentation of an authorized read-only snapshot. No session or device writes.
const { verifyReadOnlyStatus } = require('../services/dev-gateway-client')

const BATTERY: any = {
  charging: '正在充电', discharging: '使用电池', full: '已充满',
  not_charging: '已接电，未充电', unknown: '未验证', unavailable: '未验证'
}
const VOICE: any = {
  starting: '语音启动中', connecting: '语音连接中', ready: '等待唤醒', waiting_wake: '等待唤醒',
  wake_detected: '已唤醒', listening: '聆听中', processing_audio: '处理语音中', thinking: '思考中', answering: '回答中',
  followup_preparing: '准备追问', connected: '语音已连接', proactive_queued: '主动问候等待中',
  proactive_speaking: '主动问候中', proactive_finishing: '主动问候收尾中', stopping: '语音停止中',
  followup_listening: '等待追问', waiting_follow_up: '等待追问', interrupted: '已打断', echo_guard: '回声保护',
  recovering: '语音恢复中', waking: '正在请求聆听', interrupting: '正在请求打断', submitting_text: '正在提交文字',
  offline: '语音服务离线', unavailable: '语音状态未验证'
}
const validId = (value: any) => typeof value === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(value)
const message = (value: any) => typeof value === 'string' ? value.replace(/[\x00-\x1f\x7f]/g, '').slice(0, 300) : ''
const yesNo = (value: any, yes: string, no: string) => value === true ? yes : value === false ? no : '未验证'

function buildLiveDeviceView(input: any = {}, summary = false): any {
  const state = input && typeof input === 'object' && !Array.isArray(input) ? input : {}
  const live = state.mode === 'live'
  const signedIn = live && state.signedIn === true
  const loading = state.loading === true
  const listLoading = state.listLoading === true
  const statusLoading = state.statusLoading === true
  const busy = loading || listLoading || statusLoading
  const local = state.authKind === 'local'
  const configured = Boolean(state.health && typeof state.health.readOnly === 'boolean' && (local ?
    state.health.mode === 'local-device' && state.health.wechatConfigured === false && state.health.localConfigured === true && state.localCardRequired !== true :
    state.health.mode === 'local-development' && state.health.wechatConfigured === true))
  const error = live ? message(state.error) : ''
  const seen = new Set()
  const pets = signedIn && Array.isArray(state.pets) ? state.pets.slice(0, 1000).filter((pet: any) => {
    if (!pet || !validId(pet.petId) || seen.has(pet.petId) || typeof pet.name !== 'string' ||
      !pet.name.trim() || pet.name.length > 80 || /[\x00-\x1f\x7f]/.test(pet.name)) return false
    seen.add(pet.petId)
    return true
  }).map((pet: any) => ({ petId: pet.petId, name: pet.name, selected: pet.petId === state.selectedPetId })) : []
  const selected = pets.find((pet: any) => pet.selected)
  let snapshot: any = null
  if (live && signedIn && selected && !loading && !listLoading && !error && state.snapshot) {
    try { snapshot = verifyReadOnlyStatus({ success: true, data: state.snapshot }) } catch (_) {}
  }
  let statusLabel = !live ? '尚未连接真实设备' : !signedIn ? local ? '请扫描机身卡完成本地配对' : '请先验证微信登录' :
    !selected ? '请选择已授权设备' : '真实状态未验证'
  if (live && loading) statusLabel = '正在准备真实连接'
  else if (live && listLoading) statusLabel = '正在核对设备授权'
  else if (live && error) statusLabel = '状态暂不可用，请重试'
  else if (snapshot) statusLabel = snapshot.device.connected ? '真实状态 · 设备在线' : '真实状态 · 设备未连接'
  else if (live && statusLoading) statusLabel = '正在读取真实状态'

  const view: any = {
    live, local, summary: summary === true, signedIn, configured, loading, listLoading, statusLoading,
    pets, selectedPetId: selected ? selected.petId : '', selectedName: selected ? selected.name : '',
    hasSnapshot: Boolean(snapshot), online: false, statusLabel,
    batteryLabel: '未验证', chargeLabel: '未验证', voiceLabel: '语音状态未验证',
    sampledLabel: '尚未采样', sampledCaption: '代理采样时间（UTC，不是设备最后在线时间）',
    deviceRows: [], dialogRows: [], error, message: live ? message(state.message) : '',
    canEnable: !busy, canLogin: live && !signedIn && configured && !busy,
    canRefresh: Boolean(live && signedIn && selected && !busy),
    canRetry: live && !busy, canList: live && signedIn && !busy,
    canSelect: live && signedIn && !busy, canLogout: live && signedIn && !loading,
    canPreview: live && !loading
  }
  if (!snapshot) return view
  const { device, dialog } = snapshot
  const voiceUnverified = dialog.stale === true || dialog.state === 'unavailable'
  const voiceLabel = dialog.stale ? '未验证（语音状态已过期）' : VOICE[dialog.state] || '语音状态未验证'
  const batteryLabel = device.batteryPercent === null ? '未验证' : device.batteryPercent + '%'
  return {
    ...view, online: device.connected === true, batteryLabel, chargeLabel: BATTERY[device.batteryState] || '未验证',
    voiceLabel, sampledLabel: snapshot.sampledAt,
    deviceRows: [
      { label: '设备连接', value: yesNo(device.connected, '在线', '未连接') },
      { label: '脱敏编号', value: device.deviceSerial === null ? '未验证' : device.deviceSerial },
      { label: '电量', value: batteryLabel },
      { label: '电压', value: device.batteryVoltage === null ? '未验证' : device.batteryVoltage + ' V' },
      { label: '电池状态', value: BATTERY[device.batteryState] || '未验证' },
      { label: '充电器', value: yesNo(device.chargerOnline, '已连接', '未连接') },
      { label: '电池检测', value: yesNo(device.batteryPresent, '已安装', '未检测到') },
      { label: 'Wi-Fi 名称', value: '未验证（接口暂未提供）' },
      { label: '设备最后在线', value: '未验证（不是代理采样时间）' }
    ],
    dialogRows: [
      { label: '语音状态', value: voiceLabel },
      { label: '语音会话', value: voiceUnverified ? '未验证' : yesNo(dialog.sessionActive, '活跃', '未活跃') },
      { label: '语音数据新鲜度', value: dialog.stale ? '已过期，不作为当前语音状态' : voiceUnverified ? '未验证' : '本次已验证' }
    ]
  }
}

module.exports = { buildLiveDeviceView }
