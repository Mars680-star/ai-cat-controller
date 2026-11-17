const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const root = path.resolve(__dirname, '..')
const route = '/pages/device-debug/index'

function harness() {
  let page
  const calls = [], toasts = [], mutations = []
  const service = {
    getBoundPet: async () => null,
    bindPet: () => mutations.push('bind'), updateDeviceStatus: () => mutations.push('update'), reconnectDevice: () => mutations.push('reconnect')
  }
  const wx = {
    getStorageSync: () => ({ userId: 'preview-user' }), redirectTo() {}, stopPullDownRefresh() {},
    showToast: (options) => toasts.push(options), navigateTo: (options) => calls.push(options)
  }
  const source = fs.readFileSync(path.join(root, 'pages/device/index.ts'), 'utf8')
  vm.runInNewContext(ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2019, module: ts.ModuleKind.CommonJS } }).outputText, {
    Page: (definition) => { page = definition }, wx,
    require: (id) => {
      if (id.endsWith('/pet-service')) return { getPetService: () => service }
      if (id.endsWith('/request-id')) return { createRequestId: () => 'preview-request' }
      if (id.endsWith('/format')) return { formatDateTime: (value) => value, batteryText: (value) => `${value}%` }
      if (id.endsWith('/validators')) return { lengthBetween: () => true }
      if (id.endsWith('/live-page')) return { livePageData: { liveMode: false }, livePageMethods: {}, attachLivePage() {}, detachLivePage() {} }
      throw Error(`Unexpected dependency: ${id}`)
    }
  }, { filename: 'pages/device/index.ts' })
  page.data = JSON.parse(JSON.stringify(page.data))
  page.writes = []
  page.setData = function (patch) { this.writes.push(patch); Object.assign(this.data, patch) }
  return { page, wx, calls, toasts, mutations }
}

const cases = []
const test = (name, run) => cases.push({ name, run })

test('read-only debug page is registered without replacing Mock product routes', () => {
  const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))
  assert.ok(app.pages.includes(route.slice(1)))
  for (const name of ['device', 'home', 'motion', 'growth', 'growth-personality', 'dialog', 'mijia', 'profile']) assert.ok(app.pages.includes(`pages/${name}/index`))
  const view = fs.readFileSync(path.join(root, 'pages/device/index.wxml'), 'utf8')
  assert.ok(view.includes('AI猫只读联调'))
  assert.ok(view.includes('bindtap="openDeviceDebug"'))
  const mockBinding = view.indexOf('wx:if="{{!bound && mockReady}}"')
  assert.ok(mockBinding >= 0 && view.indexOf('AI猫只读联调') < mockBinding, 'entry must not depend on a fake binding')
  assert.ok(fs.readFileSync(path.join(root, 'services/pet-service.ts'), 'utf8').includes('new MockPetService()'))
})

test('inline connection is primary while legacy tools and preview keep independent guards', () => {
  const view = fs.readFileSync(path.join(root, 'pages/device/index.wxml'), 'utf8')
  const real = view.indexOf('<live-device-panel wx:if="{{liveMode && liveState.authKind !== \'local\'}}"')
  const shared = view.indexOf('bindtap="openProvision"')
  const preview = /<block\s+wx:if="\{\{previewToolsOpen && !liveMode\}\}">/.exec(view)
  assert.ok(preview, 'preview needs its own !liveMode guard: the shared card separates it from the real panel')
  assert.ok(real >= 0 && real < shared && shared < preview.index, 'preserve the real panel followed by a shared entry outside the preview-only branch')
  assert.ok(view.indexOf('<local-connect ') < view.indexOf('wx:if="{{advancedOpen}}"'))
  assert.ok(view.includes('bindtap="useComputerConnection"'))
  const h = harness()
  assert.equal(h.page.data.advancedOpen, false)
  assert.equal(h.page.data.previewToolsOpen, false)
})

test('repeated taps navigate once without mutating the Mock or actual device', async () => {
  const h = harness(); await h.page.onShow()
  h.page.openDeviceDebug(); h.page.openDeviceDebug()
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].url, route)
  assert.equal(h.page.data.openingDebug, true)
  assert.deepEqual(h.mutations, [])
})

test('failed navigation releases its guard and allows a retry', async () => {
  const h = harness(); await h.page.onShow(); h.page.openDeviceDebug()
  h.calls[0].fail({ errMsg: 'preview failure' })
  assert.equal(h.page.data.openingDebug, false)
  h.page.openDeviceDebug(); assert.equal(h.calls.length, 2)
  const writes = h.page.writes.length
  h.calls[0].fail({ errMsg: 'late failure from first attempt' })
  assert.equal(h.page.writes.length, writes)
  assert.equal(h.page.data.openingDebug, true)
  h.page.openDeviceDebug(); assert.equal(h.calls.length, 2)
})

test('hide discards late callbacks and returning to the page unlocks navigation', async () => {
  const h = harness(); await h.page.onShow(); h.page.openDeviceDebug(); h.page.onHide()
  const writes = h.page.writes.length, toasts = h.toasts.length
  h.calls[0].fail({ errMsg: 'hidden failure' }); h.page.openDeviceDebug()
  assert.equal(h.page.writes.length, writes); assert.equal(h.toasts.length, toasts); assert.equal(h.calls.length, 1)
  await h.page.onShow(); h.page.openDeviceDebug(); assert.equal(h.calls.length, 2)
})

test('unloaded page never starts navigation or updates from a late callback', async () => {
  const h = harness(); await h.page.onShow(); h.page.openDeviceDebug(); h.page.onUnload()
  const writes = h.page.writes.length
  h.calls[0].fail({ errMsg: 'unloaded failure' }); h.page.openDeviceDebug()
  assert.equal(h.page.writes.length, writes); assert.equal(h.calls.length, 1)
})

test('synchronous native navigation failure is retryable', async () => {
  const h = harness(); await h.page.onShow()
  h.wx.navigateTo = () => { throw Error('native navigation unavailable') }
  h.page.openDeviceDebug(); assert.equal(h.page.data.openingDebug, false)
  h.wx.navigateTo = (options) => h.calls.push(options)
  h.page.openDeviceDebug(); assert.equal(h.calls.length, 1)
})

test('expanding tools and preview is local presentation only, never login or mutation', async () => {
  const h = harness(); await h.page.onShow(); let switches = 0
  h.page.showPreview = () => { switches++; throw Error('no mode switch needed') }
  h.page.toggleAdvanced(); assert.equal(h.page.data.advancedOpen, true)
  h.page.toggleAdvanced(); assert.equal(h.page.data.advancedOpen, false)
  await h.page.openPreviewTools(); assert.equal(h.page.data.previewToolsOpen, true)
  await h.page.openPreviewTools(); assert.equal(h.page.data.previewToolsOpen, false)
  assert.equal(switches, 0); assert.deepEqual(h.calls, []); assert.deepEqual(h.mutations, [])
})

test('explicit continue requires a selected local identity and performs just one navigation', async () => {
  const h = harness(); await h.page.onShow()
  let state = { authKind: 'local', signedIn: false, selectedPetId: 'lp_synthetic' }
  h.page.liveSession = { getState: () => state }
  h.page.localContinue(); assert.equal(h.calls.length, 0)
  state = { ...state, signedIn: true, authKind: 'wechat' }; h.page.localContinue(); assert.equal(h.calls.length, 0)
  state = { ...state, authKind: 'local', selectedPetId: '' }; h.page.localContinue(); assert.equal(h.calls.length, 0)
  state.selectedPetId = 'lp_synthetic'; h.page.localContinue(); h.page.localContinue()
  assert.equal(h.calls.length, 1); assert.equal(h.calls[0].url, '/pages/home/index')
  assert.deepEqual(h.mutations, [])
})

test('paired notification alone never navigates, opens an AP or executes an action', async () => {
  const h = harness(); await h.page.onShow()
  h.page.localPaired(); assert.match(h.page.data.connectionNotice, /已连接/)
  assert.equal(h.calls.length, 0); assert.deepEqual(h.mutations, [])
})

test('network settings is an explicit guarded destination, not a second pairing page', async () => {
  const h = harness(); await h.page.onShow(); let signedIn = false
  h.page.liveSession = { getState: () => ({ authKind:'local', signedIn }) }
  h.page.localNetwork(); assert.equal(h.calls.length, 0)
  signedIn = true; h.page.localNetwork(); assert.equal(h.calls[0].url, '/pages/local-network/index')
  assert.deepEqual(h.mutations, [])
})

test('unresolved computer-mode switch keeps fixed feedback and never auto logs in', async () => {
  const h = harness(); await h.page.onShow(); let enabled = 0
  h.page.liveSession = { useComputerGateway: async () => { throw Error('PRIVATE_TOKEN') }, enable: () => enabled++ }
  await h.page.useComputerConnection()
  assert.equal(enabled, 0); assert.equal(h.page.data.connectionSwitching, false)
  assert.doesNotMatch(h.page.data.connectionNotice, /PRIVATE_TOKEN/)
  assert.equal(h.calls.length, 0); assert.deepEqual(h.mutations, [])
})

test('a hidden switch cannot enable its old connection or update a departed page', async () => {
  const h = harness(); await h.page.onShow(); let release, enabled = 0
  h.page.liveSession = { useComputerGateway: () => new Promise(resolve => { release = resolve }),
    getState: () => ({ authKind:'wechat' }), enable: () => enabled++ }
  const task = h.page.useComputerConnection(); h.page.onHide(); const count = h.page.writes.length
  release(); await task
  assert.equal(enabled, 0); assert.equal(h.page.writes.length, count)
  await h.page.onShow(); assert.equal(h.page.data.connectionSwitching, false)
})

test('live preview opt-in still respects unresolved mode-switch protection', async () => {
  const h = harness(); await h.page.onShow(); h.page.data.liveMode = true
  h.page.showPreview = async () => { throw Error('unresolved') }
  await h.page.openPreviewTools()
  assert.equal(h.page.data.previewToolsOpen, false); assert.match(h.page.data.connectionNotice, /未决/)
  assert.equal(h.calls.length, 0); assert.deepEqual(h.mutations, [])
})

;(async () => {
  let failed = 0
  for (const { name, run } of cases) {
    try { await run(); console.log(`ok - ${name}`) }
    catch (error) { failed++; console.error(`not ok - ${name}\n  ${error.message}`) }
  }
  console.log(`debug-entry-smoke: ${cases.length - failed}/${cases.length} passed`)
  if (failed) process.exitCode = 1
})()
