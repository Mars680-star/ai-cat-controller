// Pure presentation checks only: no network, login, storage, timers or device actions.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
}).outputText, filename)
const { buildLiveDeviceView } = require('../utils/live-device-view.ts')

const snapshot = () => ({
  readOnly: true, sampledAt: '2026-10-20T08:00:00Z',
  health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' },
  device: { connected: true, deviceSerial: '7c2****a128', networkName: null, lastSeenAt: null,
    batteryPercent: 0, batteryState: 'discharging', batteryVoltage: 3.7, chargerOnline: false, batteryPresent: true },
  dialog: { state: 'offline', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false }
})
const state = (patch = {}) => ({ mode: 'live', signedIn: true, loading: false, listLoading: false, statusLoading: false,
  health: { mode: 'local-development', readOnly: true, wechatConfigured: true },
  pets: [{ petId: 'pet_live', name: '真实小安' }], selectedPetId: 'pet_live', snapshot: snapshot(), ...patch })
let count = 0
function check(name, run) { run(); count++; console.log('PASS ' + name) }

check('preview never displays real readings', () => {
  const view = buildLiveDeviceView(state({ mode: 'preview' }))
  assert.equal(view.live, false); assert.equal(view.hasSnapshot, false); assert.equal(view.online, false)
  assert.equal(view.batteryLabel, '未验证'); assert.equal(view.canLogin, false)
})
check('empty state is safe and unauthenticated', () => {
  const view = buildLiveDeviceView()
  assert.equal(view.live, false); assert.equal(view.hasSnapshot, false); assert.equal(view.pets.length, 0)
})
check('zero percent and false charger are preserved', () => {
  const view = buildLiveDeviceView(state())
  assert.equal(view.batteryLabel, '0%'); assert.equal(view.online, true)
  assert.equal(view.deviceRows.find(row => row.label === '充电器').value, '未连接')
})
check('voice offline does not make a connected device offline', () => {
  const view = buildLiveDeviceView(state())
  assert.equal(view.voiceLabel, '语音服务离线'); assert.equal(view.online, true)
})
for (const [phase, label] of Object.entries({
  processing_audio: '处理语音中', followup_preparing: '准备追问', connected: '语音已连接',
  proactive_queued: '主动问候等待中', proactive_speaking: '主动问候中',
  proactive_finishing: '主动问候收尾中', stopping: '语音停止中'
})) check('native voice phase has a verified label: ' + phase, () => {
  const sample = snapshot(); sample.dialog.state = phase; sample.dialog.elapsedSeconds = 1
  const view = buildLiveDeviceView(state({ snapshot: sample }))
  assert.equal(view.hasSnapshot, true); assert.equal(view.voiceLabel, label)
  assert.equal(view.online, true)
  sample.dialog.stale = true
  assert.match(buildLiveDeviceView(state({ snapshot: sample })).voiceLabel, /已过期/)
})
check('sampling UTC is explicitly not the last online timestamp', () => {
  const view = buildLiveDeviceView(state())
  assert.equal(view.sampledLabel, '2026-10-20T08:00:00Z')
  assert.match(view.sampledCaption, /UTC/); assert.match(view.sampledCaption, /不是.*最后在线/)
})
check('null measurements stay unverified, never become zero or example values', () => {
  const sample = snapshot()
  Object.assign(sample.device, { batteryPercent: null, batteryVoltage: null, chargerOnline: null, batteryPresent: null, batteryState: 'unknown', deviceSerial: null })
  const view = buildLiveDeviceView(state({ snapshot: sample }))
  assert.equal(view.batteryLabel, '未验证'); assert.equal(view.chargeLabel, '未验证')
  assert.equal(view.deviceRows.find(row => row.label === '电压').value, '未验证')
  assert.equal(view.deviceRows.find(row => row.label === '脱敏编号').value, '未验证')
})
check('stale voice suppresses all old voice readings', () => {
  const sample = snapshot()
  Object.assign(sample.dialog, { state: 'thinking', elapsedSeconds: 15, sessionActive: true, stale: true, followUpRemaining: 20 })
  const view = buildLiveDeviceView(state({ snapshot: sample }))
  assert.match(view.voiceLabel, /未验证/)
  assert.equal(view.dialogRows.some(row => /15|20|思考中|活跃/.test(row.value)), false)
})
check('unavailable voice is not described as verified', () => {
  const sample = snapshot(); sample.dialog.state = 'unavailable'
  const view = buildLiveDeviceView(state({ snapshot: sample }))
  assert.match(view.voiceLabel, /未验证/)
  assert.equal(view.dialogRows.find(row => row.label === '语音数据新鲜度').value, '未验证')
})
check('offline device remains explicitly offline', () => {
  const sample = snapshot(); sample.device.connected = false
  const view = buildLiveDeviceView(state({ snapshot: sample }))
  assert.equal(view.online, false); assert.match(view.statusLabel, /未连接/)
})
check('sign-out clears view even if stale snapshot remains in input', () => {
  const view = buildLiveDeviceView(state({ signedIn: false }))
  assert.equal(view.hasSnapshot, false); assert.equal(view.pets.length, 0); assert.equal(view.selectedName, '')
  assert.equal(view.canLogin, true)
})
check('authorization removed suppresses a leftover selected device', () => {
  const view = buildLiveDeviceView(state({ pets: [] }))
  assert.equal(view.hasSnapshot, false); assert.equal(view.selectedName, ''); assert.equal(view.canRefresh, false)
})
check('unknown selected ID suppresses readings', () => {
  const view = buildLiveDeviceView(state({ selectedPetId: 'another' }))
  assert.equal(view.hasSnapshot, false); assert.equal(view.canRefresh, false)
})
check('list refresh hides old authorized values', () => {
  const view = buildLiveDeviceView(state({ listLoading: true }))
  assert.equal(view.hasSnapshot, false); assert.equal(view.canRefresh, false); assert.equal(view.canSelect, false)
})
check('request error hides old snapshot and supplies retry', () => {
  const view = buildLiveDeviceView(state({ error: '服务暂不可用，请稍后重试' }))
  assert.equal(view.hasSnapshot, false); assert.equal(view.online, false); assert.equal(view.canRetry, true)
  assert.equal(view.batteryLabel, '未验证')
})
check('invalid snapshot never leaks arbitrary fields or claims online', () => {
  const sample = snapshot(); sample.health.adapter = 'mock'; sample.device.networkName = 'DEMO WIFI'
  const view = buildLiveDeviceView(state({ snapshot: sample }))
  assert.equal(view.hasSnapshot, false); assert.equal(view.online, false)
  assert.equal(JSON.stringify(view).includes('DEMO WIFI'), false)
})
check('busy state disables login, repeated sampling and list selection', () => {
  const view = buildLiveDeviceView(state({ statusLoading: true }))
  assert.equal(view.canRefresh, false); assert.equal(view.canList, false); assert.equal(view.canSelect, false)
  assert.equal(view.statusLoading, true)
})
check('health not configured blocks explicit login', () => {
  const view = buildLiveDeviceView(state({ signedIn: false, health: { mode: 'local-development', readOnly: true, wechatConfigured: false } }))
  assert.equal(view.canLogin, false); assert.equal(view.canRetry, true)
})
check('missing or malformed health does not enable login', () => {
  for (const health of [null, {}, { wechatConfigured: true }, { mode: 'local-development', readOnly: 'false', wechatConfigured: true }]) {
    assert.equal(buildLiveDeviceView(state({ signedIn: false, health })).canLogin, false)
  }
})
check('explicit mixed-mode gateway health permits login without granting motion', () => {
  const view = buildLiveDeviceView(state({ signedIn: false, health: { mode: 'local-development', readOnly: false, wechatConfigured: true } }))
  assert.equal(view.canLogin, true)
  assert.equal(view.hasSnapshot, false)
  assert.equal(view.online, false)
  assert.equal(view.actions, undefined)
})
check('local card authentication is explicit and never presented as configured WeChat', () => {
  const local = state({ signedIn: false, authKind: 'local', health: { mode: 'local-device', wechatConfigured: false, localConfigured: true, readOnly: false } })
  const view = buildLiveDeviceView(local)
  assert.equal(view.local, true); assert.equal(view.configured, true); assert.match(view.statusLabel, /机身卡/)
  assert.equal(buildLiveDeviceView({ ...local, localCardRequired: true }).canLogin, false)
  assert.equal(buildLiveDeviceView({ ...local, authKind: 'wechat' }).configured, false)
  assert.equal(buildLiveDeviceView({ ...local, health: { ...local.health, wechatConfigured: true } }).configured, false)
})
check('pet IDs and names are validated without hidden identity fields', () => {
  const view = buildLiveDeviceView(state({ pets: [
    { petId: 'pet_live', name: '真实小安', secret: 'DO_NOT_COPY' },
    { petId: '../invalid', name: 'bad' }, { petId: 'pet_live', name: 'dup' },
    { petId: 'pet_empty', name: ' ' }, { petId: 'pet_control', name: 'x\n' }
  ] }))
  assert.equal(view.pets.length, 1); assert.equal(JSON.stringify(view).includes('DO_NOT_COPY'), false)
  assert.equal(view.selectedName, '真实小安')
})
check('formatting does not mutate controller state', () => {
  const input = state(), before = JSON.stringify(input)
  buildLiveDeviceView(input, true); buildLiveDeviceView(input, false)
  assert.equal(JSON.stringify(input), before)
})
check('component exposes only presentation events', () => {
  let definition
  const previous = global.Component
  global.Component = value => { definition = value }
  require('../components/live-device-panel/index.ts')
  global.Component = previous
  const events = []
  const component = { ...definition.methods, properties: { state: state(), summary: false }, setData() {}, triggerEvent: (name, detail) => events.push({ name, detail }) }
  for (const event of ['enable', 'login', 'refresh', 'list', 'logout', 'preview']) component[event]()
  component.select({ currentTarget: { dataset: { petId: 'pet_live' } } })
  assert.deepEqual(events.map(value => value.name), ['enable', 'login', 'refresh', 'list', 'logout', 'preview', 'select'])
  assert.deepEqual(events[6].detail, { petId: 'pet_live' })
  component.select({ currentTarget: { dataset: { petId: '../invalid' } } })
  assert.equal(events.length, 7)
})
check('component is network, credentials, timer and device-write free', () => {
  const file = fs.readFileSync(path.resolve(__dirname, '../components/live-device-panel/index.ts'), 'utf8')
  assert.doesNotMatch(file, /\b(?:wx|wxApi)\.|setTimeout\(|setInterval\(|createCloudAuthClient|setStorage|accessToken|productSecret/)
  const view = fs.readFileSync(path.resolve(__dirname, '../components/live-device-panel/index.wxml'), 'utf8')
  for (const label of ['只读', '微信登录', '切回界面预览', '设备与连接选项']) assert.ok(view.includes(label))
  assert.ok(view.includes('wx:if="{{!summary}}"'))
})
check('compact device panel folds only presentation and resets details across identities', () => {
  let definition
  const previous = global.Component
  global.Component = value => { definition = value }
  const filename = require.resolve('../components/live-device-panel/index.ts')
  delete require.cache[filename]; require(filename); global.Component = previous
  const calls = []
  const component = { data: { ...definition.data }, setData(value) { Object.assign(this.data, value) }, triggerEvent(...args) { calls.push(args) } }
  assert.equal(component.data.optionsOpen, false); assert.equal(component.data.detailsOpen, false)
  definition.observers['state, summary'].call(component, state(), false)
  definition.methods.toggleOptions.call(component); definition.methods.toggleDetails.call(component)
  assert.equal(component.data.optionsOpen, true); assert.equal(component.data.detailsOpen, true)
  definition.observers['state, summary'].call(component, state({ statusLoading: true }), false)
  assert.equal(component.data.optionsOpen, true)
  definition.observers['state, summary'].call(component, state({ signedIn: false }), false)
  assert.equal(component.data.optionsOpen, false); assert.equal(component.data.detailsOpen, false)
  assert.deepEqual(calls, [])
  const view = fs.readFileSync(path.resolve(__dirname, '../components/live-device-panel/index.wxml'), 'utf8')
  assert.ok(view.includes('optionsOpen || !view.selectedPetId'))
  assert.ok(view.includes('aria-expanded="{{optionsOpen}}"'))
  assert.ok(view.includes('aria-expanded="{{detailsOpen}}"'))
  assert.ok(view.includes("view.live ? view.local ? '本地加密' : '微信授权' : '界面预览'"))
  assert.ok(view.indexOf('wx:if="{{view.error}}"') < view.indexOf('optionsOpen || !view.selectedPetId'))
  for (const handler of ['select', 'list', 'logout', 'preview', 'refresh']) assert.ok(view.includes(`bindtap="${handler}"`))
})
check('compact product panel never folds failures, locks, or empty authorization behind options', () => {
  let definition
  const previous = global.Component
  global.Component = value => { definition = value }
  const filename = require.resolve('../components/live-product-panel/index.ts')
  delete require.cache[filename]; require(filename); global.Component = previous
  const calls = []
  const component = { data: { ...definition.data }, setData(value) { Object.assign(this.data, value) }, triggerEvent(...args) { calls.push(args) } }
  assert.equal(component.data.optionsOpen, false)
  definition.observers.state.call(component, state())
  definition.methods.toggleOptions.call(component)
  assert.equal(component.data.optionsOpen, true)
  definition.observers.state.call(component, state({ pets: [], error: '请核对原请求' }))
  assert.equal(component.data.optionsOpen, false); assert.equal(component.data.view.hasSnapshot, false)
  assert.equal(component.data.view.error, '请核对原请求'); assert.deepEqual(calls, [])
  const view = fs.readFileSync(path.resolve(__dirname, '../components/live-product-panel/index.wxml'), 'utf8')
  assert.ok(view.includes('optionsOpen || !view.selectedPetId'))
  assert.ok(view.includes('aria-expanded="{{optionsOpen}}"'))
  assert.ok(view.includes('selectionLocked || !view.canSelect || busy'))
  assert.ok(view.indexOf('wx:if="{{view.error}}"') < view.indexOf('optionsOpen || !view.selectedPetId'))
  assert.ok(view.includes('wx:if="{{error}}"'))
  for (const handler of ['select', 'list', 'logout', 'preview', 'refresh']) assert.ok(view.includes(`bindtap="${handler}"`))
})
console.log(`live-device-view-smoke: ${count}/${count} passed`)
