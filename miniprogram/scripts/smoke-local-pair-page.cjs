// Real TS page lifecycle with an injected session boundary; no real camera or network.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path'), vm = require('node:vm'), ts = require('typescript')
const root = path.resolve(__dirname, '..'), DEVICE = 'dev_' + '1'.repeat(32), CARD = '3'.repeat(32), KEY = '22'.repeat(32), QR = 'AICAT-LOCAL:1:' + DEVICE + ':' + CARD + ':' + KEY
const clone = value => JSON.parse(JSON.stringify(value)), flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }
require.extensions['.ts'] = (module, filename) => module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 } }).outputText, filename)
const validators = require('../services/local-device-client.ts')
const discovery = require('../services/local-device-discovery.ts')
function harness(options = {}) {
  const modals = [], preparations = [], confirmations = [], cancellations = [], nav = [], views = [], events = []
  let definition, listener, state = { mode: 'preview', authKind: 'wechat', signedIn: false, selectedPetId: '', pets: [] }, cleared = 0, switched = 0
  const session = {
    attach(fn) { listener = fn; fn(clone(state)); return () => { listener = null } },
    prepareLocal(card, address) { validators.localCardIdentity(card); validators.verifyLocalAddress(address); if (options.prepareFail) throw Error('PRIVATE_SECRET'); const token = {}; preparations.push({ card, address, token }); return { token, context: { deviceId: DEVICE, cardId: CARD, address, port: 8789 } } },
    cancelLocal(token) { cancellations.push(token) },
    getState() { return clone(state) },
    async confirmLocal(token) { confirmations.push(token); if (options.confirmFail) throw Error('PRIVATE_SECRET'); if (options.deferConfirm) await options.deferConfirm; state = { mode: 'live', authKind: 'local', signedIn: true, localContext: { deviceId: DEVICE, cardId: CARD, address: preparations[preparations.length - 1].address }, selectedPetId: '', pets: options.pets || [{ petId: 'lp_' + DEVICE.slice(4), name: '小安' }], message: '本地配对成功' }; if (options.onConfirmState) state = options.onConfirmState(state); if (listener) listener(clone(state)) },
    useComputerGateway() { switched++; return Promise.resolve() },
    async selectPet(value) { nav.push(['select', value]); if (options.deferSelect) await options.deferSelect; state.selectedPetId = value; if (listener) listener(clone(state)) }, refresh() {}, refreshPets() {}, logout() { cleared++ }
  }
  const wx = { getAccountInfoSync: () => ({ miniProgram: { envVersion: options.env || 'develop' } }), getDeviceInfo: () => ({ platform: options.platform || 'android' }),
    showModal(value) { modals.push(value); if (options.modalThrow) throw Error('PRIVATE_SECRET') }, navigateTo(value) { nav.push(value) } }
  const context = vm.createContext({ wx, Page(value) { definition = value }, Component(value) { definition = value } })
  const source = ts.transpileModule(fs.readFileSync(path.join(root, options.component ? 'components/local-connect/index.ts' : 'pages/local-pair/index.ts'), 'utf8'), { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 } }).outputText
  new vm.Script('(function(require){' + source + '\n})').runInContext(context)(name => name.includes('live-device-session') ? { getLiveDeviceSession: () => session } : name.includes('local-pair-flow') ? require('../utils/local-pair-flow.ts') : name.includes('local-device-discovery') ? discovery : validators)
  const page = { ...(options.component ? definition.methods : definition), data: { ...clone(definition.data), ...(options.component ? { addressHint: options.addressHint || '', compact: true } : {}) },
    setData(patch) { Object.assign(this.data, clone(patch)); views.push(clone(patch)) }, triggerEvent(name, detail) { events.push([name, clone(detail || {})]); if (options.onEvent) options.onEvent(name, detail, h) } }
  const h = { page, modals, preparations, confirmations, cancellations, nav, views, events,
    show() { if (options.component) { if (!page.localAttached) definition.lifetimes.attached.call(page); else definition.pageLifetimes.show.call(page) } else page.onShow() },
    hide() { if (options.component) definition.pageLifetimes.hide.call(page); else page.onHide() },
    detach() { if (options.component) definition.lifetimes.detached.call(page); else page.onUnload() },
    hint(value) { page.data.addressHint = value; definition.observers.addressHint.call(page, value) },
    address(value = '192.168.1.152') { page.inputAddress({ detail: { value } }) },
    scan(result = QR, key = page.data.cameraKeys[0]) { page.cameraDecoded({ currentTarget: { dataset: { scanId: key } }, detail: { type: 'QR_CODE', result } }) },
    async confirm(index = modals.length - 1) { await modals[index].success({ confirm: true }); await flush() },
    state(value) { state = { ...state, ...value }; if (listener) listener(clone(state)) }, switched: () => switched, cleared: () => cleared }
  return h
}
const cases = []
const check = (name, run) => cases.push([name, run])
check('onShow only subscribes and renders without pairing, login, camera or commands', () => {
  const h = harness(); h.show(); assert.equal(h.page.data.available, true); assert.equal(h.modals.length, 0); assert.equal(h.preparations.length, 0); assert.equal(h.page.data.scanning, false)
})
check('address deep-link only pre-fills a canonical private IP and never starts pairing', () => {
  const h = harness(); h.page.onLoad({ address: '10.47.0.1' }); h.show(); assert.equal(h.page.data.address, '10.47.0.1'); assert.equal(h.preparations.length, 0); assert.equal(h.page.data.scanning, false)
  for (const address of ['http://10.47.0.1', '127.0.0.1', ['10.47.0.1']]) { const h = harness(); h.page.onLoad({ address }); assert.equal(h.page.data.address, ''); assert.equal(h.preparations.length, 0) }
})
check('unsupported platform/environment and invalid addresses cannot start scanning', () => {
  for (const options of [{ env: 'trial' }, { env: 'release' }, { platform: 'ios' }, { platform: 'devtools' }]) { const h = harness(options); h.show(); h.address(); h.page.scanCard(); assert.equal(h.page.data.scanning, false) }
  const h = harness(); h.show(); for (const address of ['', 'http://192.168.1.152', '127.0.0.1', '192.168.1.152:8789']) { h.address(address); h.page.scanCard(); assert.equal(h.page.data.scanning, false) }
})
check('card is never in Page state and pairing waits for one explicit confirmation', async () => {
  const h = harness(); h.show(); h.address(); h.page.scanCard(); h.scan()
  assert.equal(h.preparations.length, 1); assert.equal(h.confirmations.length, 0); assert.equal(h.modals.length, 1); assert.equal(h.page.data.confirming, true)
  assert.doesNotMatch(JSON.stringify(h.page), new RegExp(KEY + '|AICAT-LOCAL:'))
  assert.doesNotMatch(JSON.stringify(h.views), new RegExp(KEY + '|AICAT-LOCAL:'))
  assert.ok(h.modals[0].content.includes(DEVICE)); assert.ok(h.modals[0].content.includes('192.168.1.152:8789')); assert.ok(h.modals[0].content.includes('不是微信登录'))
  await h.confirm(); assert.equal(h.confirmations.length, 1); assert.equal(h.page.data.paired, true); assert.equal(h.page.data.state.selectedPetId, 'lp_' + DEVICE.slice(4))
  await h.confirm(); assert.equal(h.confirmations.length, 1)
})
check('cancel and native modal failure clear only their own prepared intent without sending', async () => {
  for (const fail of [false, true]) {
    const h = harness(); h.show(); h.address(); h.page.scanCard(); h.scan()
    if (fail) h.modals[0].fail({ errMsg: 'PRIVATE_SECRET' }); else await h.modals[0].success({ confirm: false })
    assert.equal(h.confirmations.length, 0); assert.equal(h.cancellations.length, 1); assert.equal(h.page.data.confirming, false); assert.doesNotMatch(JSON.stringify(h.page.data), /PRIVATE_SECRET/)
  }
})
check('only boolean confirmation dispatches; late failure after confirmation cannot claim it was unsent', async () => {
  for (const answer of [null, {}, { confirm: 'yes' }, { confirm: 1 }]) { const h = harness(); h.show(); h.address(); h.page.scanCard(); h.scan(); await h.modals[0].success(answer); assert.equal(h.confirmations.length, 0) }
  const h = harness(); h.show(); h.address(); h.page.scanCard(); h.scan(); await h.confirm(); const state = clone(h.page.data)
  h.modals[0].fail({ errMsg: 'PRIVATE' }); assert.deepEqual(clone(h.page.data), state); assert.equal(h.confirmations.length, 1)
})
check('camera generations reject a late decoded frame after cancel and another scan', () => {
  const h = harness(); h.show(); h.address(); h.page.scanCard(); const old = h.page.data.cameraKeys[0]
  h.page.cancelScan(); h.page.scanCard(); h.scan(QR, old); assert.equal(h.preparations.length, 0)
  h.scan(); assert.equal(h.preparations.length, 1)
})
check('hide rejects late scanner and modal callbacks without authorizing a new visit', async () => {
  const h = harness(); h.show(); h.address(); h.page.scanCard(); const old = h.page.data.cameraKeys[0]
  h.hide(); h.scan(QR, old); assert.equal(h.preparations.length, 0)
  h.show(); h.address(); h.page.scanCard(); h.scan(); const intent = h.preparations[0].token
  h.hide(); h.show(); await h.confirm(0); assert.equal(h.confirmations.length, 0); assert.ok(h.cancellations.includes(intent)); assert.equal(h.page.data.paired, false)
})
check('address cannot change while camera or modal owns the prepared target', () => {
  const h = harness(); h.show(); h.address(); h.page.scanCard(); h.address('192.168.1.153'); assert.equal(h.page.data.address, '192.168.1.152')
  h.scan(); h.address('192.168.1.153'); assert.equal(h.page.data.address, '192.168.1.152'); assert.equal(h.preparations[0].address, '192.168.1.152')
})
check('wrong QR and all failures produce fixed safe feedback with no QR/native details', async () => {
  const h = harness(); h.show(); h.address(); h.page.scanCard(); h.scan('AICAT-PROVISION:PRIVATE_SECRET'); assert.equal(h.modals.length, 0); assert.doesNotMatch(JSON.stringify(h.page.data), /PRIVATE_SECRET/)
  const bad = harness({ confirmFail: true }); bad.show(); bad.address(); bad.page.scanCard(); bad.scan(); await bad.confirm(); assert.equal(bad.page.data.busy, false); assert.doesNotMatch(JSON.stringify(bad.page.data), /PRIVATE_SECRET/)
})
check('a confirmed unique card alias selects by readonly path without automatic navigation', async () => {
  const h = harness(); h.show(); h.page.openControls(); assert.equal(h.nav.length, 0)
  h.address(); h.page.scanCard(); h.scan(); await h.confirm(); assert.deepEqual(h.nav, [['select', 'lp_' + DEVICE.slice(4)]])
  h.page.openControls(); assert.equal(h.nav[1].url, '/pages/home/index')
  h.page.selectPet({ detail: { petId: 'lp_' + DEVICE.slice(4) } }); assert.deepEqual(h.nav[2], ['select', 'lp_' + DEVICE.slice(4)])
})
check('computer fallback is manual, guarded and never runs during scan or confirmation', async () => {
  const h = harness(); h.show(); h.address(); h.page.scanCard(); await h.page.useComputer(); assert.equal(h.switched(), 0)
  h.page.cancelScan(); await h.page.useComputer(); assert.equal(h.switched(), 1)
})
check('templates register a standalone camera and no credential input or QR rendering', () => {
  const view = fs.readFileSync(path.join(root, 'pages/local-pair/index.wxml'), 'utf8')
  assert.ok(view.includes('data-scan-id="{{item}}"')); assert.ok(view.includes('不经电脑中转'))
  assert.doesNotMatch(view, /\{\{[^}]*\b(?:key|cardQR|accessToken|password)\b/)
  const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8')); assert.ok(app.pages.includes('pages/local-pair/index'))
})

module.exports = { harness, DEVICE, CARD, KEY, QR, flush }
if (require.main === module) (async () => { for (const [name, run] of cases) { await run(); console.log('PASS ' + name) } console.log('local-pair-page-smoke: ' + cases.length + '/' + cases.length + ' (offline Page lifecycle)') })().catch(error => { console.error(error); process.exitCode = 1 })
