// Actual inline Component and shared factory; fake native/session boundaries.
const assert = require('node:assert/strict'), fs = require('node:fs'), path = require('node:path')
const { harness, DEVICE, CARD, KEY, QR, flush } = require('./smoke-local-pair-page.cjs')
const root = path.resolve(__dirname, '..'), pet = 'lp_' + DEVICE.slice(4)
const cases = [], check = (name, run) => cases.push([name, run])
function pair(h) { h.show(); h.address(); h.page.scanCard(); h.scan(); return h.confirm() }
const snapshot = () => ({ readOnly: true, sampledAt: '2026-11-02T00:00:00Z',
  health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' },
  device: { connected: true, deviceSerial: '7c2****a128', networkName: null, lastSeenAt: null,
    batteryPercent: 61, batteryState: 'discharging', batteryVoltage: 3.7, chargerOnline: false, batteryPresent: true },
  dialog: { state: 'offline', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false } })

check('inline attach/show are inert and a canonical public address hint only prefills', () => {
  const h = harness({ component: true, addressHint: '10.47.0.1' }); h.show()
  assert.equal(h.page.data.address, '10.47.0.1'); assert.equal(h.page.data.addressExpanded, false)
  assert.equal(h.preparations.length, 0); assert.equal(h.confirmations.length, 0); assert.equal(h.page.data.scanning, false)
  assert.equal(h.events.filter(([name]) => name === 'paired').length, 0)
  h.show(); assert.equal(h.preparations.length, 0)
})
check('inline card confirmation auto-selects only the verified singleton and emits no navigation', async () => {
  const h = harness({ component: true }); await pair(h)
  assert.deepEqual(h.nav, [['select', pet]]); assert.equal(h.page.data.selectedPetId, pet)
  const paired = h.events.filter(([name]) => name === 'paired')
  assert.equal(paired.length, 1)
  assert.deepEqual(paired[0][1], { deviceId: DEVICE, cardId: CARD, address: '192.168.1.152', selectedPetId: pet })
  assert.doesNotMatch(JSON.stringify({ data: h.page.data, views: h.views, events: h.events }), new RegExp(KEY + '|AICAT-LOCAL:|accessToken'))
  h.page.openControls(); h.page.openLocalNetwork()
  assert.ok(h.events.some(([name]) => name === 'continue')); assert.ok(h.events.some(([name]) => name === 'network'))
  assert.equal(h.nav.filter(value => !Array.isArray(value)).length, 0)
})
check('multiple or unexpected aliases never auto-select or manufacture authorization', async () => {
  for (const pets of [[{ petId: pet, name: 'A' }, { petId: 'other', name: 'B' }], [{ petId: 'other', name: 'A' }], []]) {
    const h = harness({ component: true, pets }); await pair(h)
    assert.deepEqual(h.nav, []); assert.equal(h.page.data.selectedPetId, '')
  }
})
check('changed card, address or auth realm after async pair cannot auto-select or emit paired', async () => {
  for (const replace of [state => ({ ...state, authKind: 'wechat' }),
    state => ({ ...state, localContext: { ...state.localContext, cardId: 'a'.repeat(32) } }),
    state => ({ ...state, localContext: { ...state.localContext, deviceId: 'dev_' + 'a'.repeat(32) } }),
    state => ({ ...state, localContext: { ...state.localContext, address: '192.168.1.153' } })]) {
    const h = harness({ component: true, onConfirmState: replace }); await pair(h)
    assert.deepEqual(h.nav, []); assert.equal(h.events.filter(([name]) => name === 'paired').length, 0)
  }
})
check('hint changes cannot show a previous address snapshot as the new target', async () => {
  const h = harness({ component: true }); await pair(h)
  h.state({ snapshot: snapshot() })
  assert.equal(h.page.data.paired, true); assert.equal(h.page.data.batteryText, '61%')
  h.hint('10.47.0.1')
  assert.equal(h.page.data.address, '10.47.0.1'); assert.equal(h.page.data.paired, false); assert.equal(h.page.data.batteryText, '--')
  assert.equal(h.confirmations.length, 1)
  h.page.scanCard(); assert.equal(h.page.data.scanning, true)
})
check('existing valid public context prefills an empty address but never overrides a target hint', () => {
  for (const hinted of [false, true]) {
    const h = harness({ component: true, ...(hinted ? { addressHint: '10.47.0.1' } : {}) })
    h.state({ authKind: 'local', localContext: { deviceId: DEVICE, cardId: CARD, address: '192.168.1.152' }, signedIn: false })
    h.show(); assert.equal(h.page.data.address, hinted ? '10.47.0.1' : '192.168.1.152')
    assert.equal(h.preparations.length, 0); assert.equal(h.page.data.scanning, false)
  }
})
check('switching the explicit AP hint to station clears AP address and readings without session or request changes', async () => {
  const h = harness({ component: true, addressHint: '10.47.0.1' }); h.show()
  h.page.scanCard(); h.scan(); await h.confirm(); h.state({ snapshot: snapshot() })
  assert.equal(h.page.data.paired, true); assert.equal(h.page.data.batteryText, '61%')
  h.hint('')
  assert.equal(h.page.data.address, ''); assert.equal(h.page.data.addressExpanded, true)
  assert.equal(h.page.data.paired, false); assert.equal(h.page.data.batteryText, '--'); assert.equal(h.page.data.selectedPetId, '')
  assert.equal(h.page.data.state.snapshot, null)
  h.state({ snapshot: snapshot() })
  assert.equal(h.page.data.address, ''); assert.equal(h.page.data.paired, false); assert.equal(h.page.data.batteryText, '--')
  assert.equal(h.preparations.length, 1); assert.equal(h.confirmations.length, 1); assert.equal(h.cleared(), 0)
})
check('without an explicit AP hint global AP metadata never automatically fills a new component', () => {
  for (const signedIn of [false, true]) {
    const h = harness({ component: true })
    h.state({ mode: 'live', authKind: 'local', signedIn, localContext: { deviceId: DEVICE, cardId: CARD, address: '10.47.0.1' }, pets: [{ petId: pet, name: 'A' }], selectedPetId: pet, snapshot: snapshot() })
    h.show(); assert.equal(h.page.data.address, ''); assert.equal(h.page.data.paired, false); assert.equal(h.page.data.batteryText, '--')
    assert.equal(h.preparations.length, 0); assert.equal(h.confirmations.length, 0); assert.equal(h.cleared(), 0)
  }
})
check('station target may recover its recent ordinary address but repeated same hints never overwrite manual input', () => {
  const h = harness({ component: true })
  h.state({ authKind: 'local', localContext: { deviceId: DEVICE, cardId: CARD, address: '192.168.1.152' } }); h.show()
  h.hint('10.47.0.1')
  h.state({ localContext: { deviceId: DEVICE, cardId: CARD, address: '10.47.0.1' } })
  h.hint(''); assert.equal(h.page.data.address, '192.168.1.152'); assert.equal(h.page.data.addressExpanded, true)
  h.address('192.168.1.153'); h.hint(''); h.hint(''); assert.equal(h.page.data.address, '192.168.1.153')
  h.hint('10.47.0.1'); h.address('192.168.1.154'); h.hint('10.47.0.1'); assert.equal(h.page.data.address, '192.168.1.154')
  assert.equal(h.preparations.length, 0); assert.equal(h.confirmations.length, 0); assert.equal(h.cleared(), 0)
})
check('errors, loading or removed device authorization never present an old snapshot as current readings', async () => {
  for (const patch of [{ error: '状态暂不可用' }, { loading: true }, { listLoading: true }, { signedIn: false }, { pets: [] }, { selectedPetId: 'other' }]) {
    const h = harness({ component: true }); await pair(h)
    h.state({ snapshot: snapshot() }); assert.equal(h.page.data.batteryText, '61%')
    h.state(patch)
    assert.equal(h.page.data.batteryText, '--'); assert.notEqual(h.page.data.statusText, '设备在线'); assert.equal(h.page.data.state.snapshot, null)
    assert.equal(h.confirmations.length, 1); assert.equal(h.cleared(), 0)
  }
})
check('a changed target hint cancels an unconfirmed old modal and rejects its late confirmation', async () => {
  const h = harness({ component: true, addressHint: '10.47.0.1' }); h.show(); h.page.scanCard(); h.scan()
  h.hint(''); await h.confirm(0)
  assert.equal(h.page.data.address, ''); assert.equal(h.page.data.confirming, false)
  assert.equal(h.confirmations.length, 0); assert.equal(h.events.filter(([name]) => name === 'paired').length, 0)
  assert.equal(h.cleared(), 0)
})
check('a changed target during confirmed pairing cannot auto-select or emit completion for the old AP', async () => {
  let finish
  const deferred = new Promise(resolve => { finish = resolve })
  const h = harness({ component: true, addressHint: '10.47.0.1', deferConfirm: deferred })
  h.show(); h.page.scanCard(); h.scan()
  const completing = h.modals[0].success({ confirm: true }); await flush()
  h.hint(''); finish(); await completing
  assert.equal(h.page.data.address, ''); assert.equal(h.page.data.busy, false); assert.equal(h.page.data.paired, false)
  assert.deepEqual(h.nav, []); assert.equal(h.events.filter(([name]) => name === 'paired').length, 0)
  assert.equal(h.confirmations.length, 1); assert.equal(h.cleared(), 0)
})
check('inline hiding rejects stale scanner/modal and suppresses completion events after detached', async () => {
  const h = harness({ component: true }); h.show(); h.address(); h.page.scanCard(); h.scan()
  h.hide(); h.show(); await h.confirm(0)
  assert.equal(h.confirmations.length, 0); assert.equal(h.events.filter(([name]) => name === 'paired').length, 0)
  h.page.scanCard(); const generation = h.page.data.cameraKeys[0]; h.detach(); h.scan(QR, generation)
  assert.equal(h.preparations.length, 1)
})
check('hide while pair or singleton selection awaits cannot authorize the next visit', async () => {
  for (const phase of ['pair', 'select']) {
    let finish
    const deferred = new Promise(resolve => { finish = resolve })
    const h = harness({ component: true, ...(phase === 'pair' ? { deferConfirm: deferred } : { deferSelect: deferred }) })
    h.show(); h.address(); h.page.scanCard(); h.scan()
    const completing = h.modals[0].success({ confirm: true }); await flush()
    h.hide(); h.show(); finish(); await completing
    assert.equal(h.events.filter(([name]) => name === 'paired').length, 0)
    if (phase === 'pair') assert.deepEqual(h.nav, [])
  }
})
check('ordinary passive session updates never emit a completed pairing event or select', () => {
  const h = harness({ component: true }); h.show(); h.address()
  h.state({ mode: 'live', authKind: 'local', signedIn: true, localContext: { deviceId: DEVICE, cardId: CARD, address: '192.168.1.152' }, pets: [{ petId: pet, name: 'A' }] })
  assert.equal(h.page.data.paired, true); assert.deepEqual(h.nav, []); assert.equal(h.events.filter(([name]) => name === 'paired').length, 0)
})
check('compact template keeps address editing and explicit re-scan even for a paired target', () => {
  const view = fs.readFileSync(path.join(root, 'components/local-connect/index.wxml'), 'utf8')
  assert.ok(view.includes('!paired || compact || addressExpanded')); assert.ok(view.includes('重新扫描机身卡连接'))
  assert.ok(view.includes('wx:if="{{scanning}}"')); assert.ok(view.includes('data-scan-id="{{item}}"'))
  assert.ok(view.includes('wx:elif="{{discoveryHint}}"'), 'completed/failed discovery must retain its bounded hint')
  assert.doesNotMatch(view, /discoveryHint\s*&&\s*!discoveryAvailable/)
  assert.doesNotMatch(view, /\{\{[^}]*\b(?:key|cardQR|accessToken|password)\b/)
  const source = fs.readFileSync(path.join(root, 'utils/local-pair-flow.ts'), 'utf8')
  assert.doesNotMatch(source, /192\.168\.1\.152|setStorage|console\.|wxApi\.login|wxApi\.navigate/)
  const legacy = fs.readFileSync(path.join(root, 'pages/local-pair/index.ts'), 'utf8')
  const component = fs.readFileSync(path.join(root, 'components/local-connect/index.ts'), 'utf8')
  assert.ok(legacy.includes('createLocalPairFlow')); assert.ok(component.includes('createLocalPairFlow'))
})

;(async () => { for (const [name, run] of cases) { await run(); console.log('PASS ' + name) }
  console.log('local-connect-smoke: ' + cases.length + '/' + cases.length + ' (synthetic inline Component/shared lifecycle)')
})().catch(error => { console.error(error); process.exitCode = 1 })
