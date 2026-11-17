// Cross-device recovery through real local-network, original core and AES.
// All session RPCs and wx I/O are synthetic; no device or network is accessed.
const assert = require('node:assert/strict')
const { harness, KEY, DEVICE, SESSION, QR, flush, plain } = require('./smoke-provision-page.cjs')
const OUTER_STORE = 'aiCatLocalNetworkRequestV1', INNER_STORE = 'aiCatProvisionPendingV1'
const CARD = '4'.repeat(32), REVIEW = '5'.repeat(32), TOKEN = 'SyntheticReviewToken_'.repeat(3)
const OUTER = 'local_unknown_original', INNER = 'wifi_unknown_original'
const ACKS = ['window-ended', 'network-stable', 'device-idle', 'accept-unknown']
const now = Date.parse('2026-11-02T08:00:00Z')
function fixture(options = {}, storage = new Map(), sharedServer = null) {
  if (!storage.has(OUTER_STORE)) storage.set(OUTER_STORE, { v: 1, deviceId: DEVICE, cardId: CARD, requestId: OUTER, windowId: SESSION, settled: false })
  if (!options.noInner && !storage.has(INNER_STORE)) storage.set(INNER_STORE, { v: 1, deviceId: DEVICE, sessionId: SESSION, requestId: INNER })
  const h = harness({ now }, storage), api = h.module('services/local-network'), coreApi = h.module('services/live-provisioning')
  const views = [], calls = [], events = [], listeners = new Set()
  const original = { deviceId: DEVICE, requestId: OUTER, windowId: SESSION, networkRequestId: INNER }
  const server = sharedServer || { archived: false, stages: 0, commits: 0, receipt: options.receipt || null }
  if (!server.receipt && storage.get(INNER_STORE) && storage.get(INNER_STORE).v === 1) server.receipt = h.receiptQR('unknown')
  let auth = { mode: 'live', authKind: 'local', signedIn: true, localCardRequired: false,
    localContext: { deviceId: DEVICE, cardId: CARD, address: '192.168.1.152', port: 8789 } }
  let raw = { requestId: OUTER, deviceId: DEVICE, windowId: SESSION, status: 'unknown', apName: 'AI-CAT-ABC123',
    expiresAt: now / 1000 - 10, networkRequestId: INNER, receiptQr: server.receipt, invitation: QR }
  const review = () => ({ eligible: !server.archived, cardId: CARD, original: plain(original), status: 'unknown',
    resolution: server.archived ? 'operator_archived' : 'pending', reviewId: server.archived ? REVIEW : null,
    archivedAt: server.archived ? now : null, acknowledgements: server.archived ? ACKS.slice() : [] })
  let deferredCommit
  const session = {
    getState() { return plain(auth) }, attach(fn) { listeners.add(fn); fn(); return () => listeners.delete(fn) },
    async resumeLocalProvision(requestId) { calls.push(['GET-resume', requestId]); assert.equal(requestId, OUTER); return plain(raw) },
    async readLocalProvisionArchive(requestId) { calls.push(['GET-review', requestId]); assert.equal(requestId, OUTER); const value = review(); return options.reviewPatch ? options.reviewPatch(value) : value },
    async prepareLocalProvisionArchive(value) {
      calls.push(['POST-prepare', plain(value)]); server.stages++; events.push('server-prepare')
      assert.deepEqual(plain(value), { requestId: OUTER, windowId: SESSION, networkRequestId: INNER, acknowledgements: ACKS })
      const preparation = { reviewToken: TOKEN, reviewId: REVIEW, expiresAt: now / 1000 + 30, cardId: CARD, original: plain(original) }
      return options.preparationPatch ? options.preparationPatch(preparation) : preparation
    },
    async confirmLocalProvisionArchive(value) {
      calls.push(['POST-confirm', plain(value)]); server.commits++; events.push('server-commit')
      assert.deepEqual(plain(value), { requestId: OUTER, windowId: SESSION, networkRequestId: INNER,
        reviewToken: TOKEN, acknowledgements: ACKS, confirmation: 'archive-unknown-provisioning' })
      if (options.deferCommit) await new Promise(resolve => { deferredCommit = resolve })
      server.archived = true
      if (options.loseCommitResponse) { options.loseCommitResponse = false; throw Error('PRIVATE_SERVER_RESPONSE_LOST') }
      return review()
    },
    async prepareLocalProvision() { throw Error('Closeout must never open a new AP') },
    async currentLocalProvision() { return { window: plain(raw) } },
    async statusLocalProvision() { return plain(raw) }
  }
  const originalSet = h.wx.setStorageSync
  let failedOuterWrite = false
  h.wx.setStorageSync = (key, value) => {
    if (key === INNER_STORE && value && value.pending === null && value.archives) {
      events.push('inner-archive')
      if (value.archives.some(entry => entry.source === 'local_gateway')) assert.equal(server.archived, true, 'server audit must commit before phone archive')
    }
    if (key === OUTER_STORE && value.settled) {
      events.push('outer-settle')
      assert.equal(storage.get(INNER_STORE).pending, null)
      if (options.failOuterWrite && !failedOuterWrite) { failedOuterWrite = true; throw Error('PRIVATE_OUTER_WRITE_FAIL') }
    }
    originalSet(key, value)
    if (options.replaceOuterAfterInner && key === INNER_STORE && value && value.pending === null) storage.set(OUTER_STORE, { ...storage.get(OUTER_STORE), requestId: 'foreign_new_request', settled: false })
  }
  const apiCalls = { random: 0 }
  const originalCrypto = h.wx.getUserCryptoManager
  h.wx.getUserCryptoManager = () => {
    const manager = originalCrypto()
    return { getRandomValues(value) { apiCalls.random++; return manager.getRandomValues(value) } }
  }
  function controller() { const value = api.createLocalNetwork(h.wx, session); value.show(state => views.push(plain(state))); return value }
  return { h, api, coreApi, options, storage, views, calls, events, server, session, apiCalls, original,
    controller, review,
    raw(patch) { raw = { ...raw, ...patch } },
    auth(patch) { auth = { ...auth, ...patch }; for (const fn of [...listeners]) fn() },
    finishCommit() { deferredCommit() },
    writes() { return calls.filter(call => call[0].startsWith('POST-')) },
    safe() {
      const rendered = JSON.stringify({ views, logs: h.logs, storage: [...storage], writes: h.storageWrites })
      for (const secret of [KEY, QR, server.receipt, TOKEN, 'PRIVATE_SERVER', 'PRIVATE_OUTER']) if (secret) assert.equal(rendered.includes(secret), false, 'private material escaped')
      assert.equal(h.sockets.length, 0); assert.equal(h.wifiStarts.length, 0); assert.equal(h.wifiReads.length, 0); assert.equal(apiCalls.random, 0)
    }, clean() { api.clearLocalProvisionHandoff() }
  }
}
let passed = 0
async function check(name, run) { await run(); passed++; console.log('PASS ' + name) }
;(async () => {
  await check('real AEAD review is read-only and never archives or opens a network window', async () => {
    const t = fixture(), c = t.controller(), before = plain([...t.storage])
    assert.equal(c.getState().closeout.phase, 'unchecked'); assert.equal(t.calls.length, 0)
    assert.equal(await c.refreshCloseout(), true)
    assert.equal(c.getState().closeout.phase, 'ready'); assert.equal(c.getState().closeout.eligible, true)
    assert.deepEqual(t.calls.map(call => call[0]), ['GET-resume', 'GET-review'])
    assert.deepEqual([...t.storage], before); assert.equal(t.h.storageWrites.length, 0)
    c.hide(); t.safe(); t.clean()
  })
  await check('server-first audit precedes original core archive and exact outer settlement', async () => {
    const t = fixture(), c = t.controller(); await c.refreshCloseout()
    const token = await c.prepareCloseout(ACKS)
    assert.equal(c.getState().closeout.phase, 'prepared'); assert.equal(t.h.storageWrites.length, 0)
    assert.equal(await c.confirmCloseout(token), true)
    assert.deepEqual(t.events, ['server-prepare', 'server-commit', 'inner-archive', 'outer-settle'])
    const inner = t.storage.get(INNER_STORE)
    assert.equal(inner.v, 2); assert.equal(inner.pending, null); assert.equal(inner.archives.length, 1)
    assert.equal(inner.archives[0].status, 'unknown'); assert.equal(inner.archives[0].source, 'local_gateway')
    assert.equal(inner.archives[0].reviewId, REVIEW); assert.equal(inner.archives[0].outerRequestId, OUTER)
    assert.equal(t.storage.get(OUTER_STORE).settled, true)
    assert.equal(c.getState().closeout.archived, true); assert.equal(c.getState().canPrepare, true)
    const calls = t.calls.length; await c.confirmCloseout(token); assert.equal(t.calls.length, calls)
    assert.equal(t.server.commits, 1); c.hide(); t.safe(); t.clean()
  })
  await check('lost server commit response recovers by GET and phone confirmation without another server POST', async () => {
    const t = fixture({ loseCommitResponse: true }), c = t.controller(); await c.refreshCloseout()
    const token = await c.prepareCloseout(ACKS)
    assert.equal(await c.confirmCloseout(token), false); assert.equal(c.getState().closeout.phase, 'unknown')
    assert.equal(t.server.archived, true); assert.equal(t.storage.get(INNER_STORE).v, 1); assert.equal(t.storage.get(OUTER_STORE).settled, false)
    const writes = t.writes().length, storageWrites = t.h.storageWrites.length
    assert.equal(await c.refreshCloseout(), true); assert.equal(c.getState().closeout.phase, 'recovering')
    assert.equal(c.getState().canPrepare, false); assert.equal(t.writes().length, writes); assert.equal(t.h.storageWrites.length, storageWrites)
    const recovered = await c.prepareCloseout(ACKS); assert.equal(t.writes().length, writes)
    assert.equal(await c.confirmCloseout(recovered), true)
    assert.equal(t.server.stages, 1); assert.equal(t.server.commits, 1); assert.equal(c.getState().closeout.archived, true)
    c.hide(); t.safe(); t.clean()
  })
  await check('phone inner success plus outer write fault blocks same runtime and recovers on app reopen with the same storage', async () => {
    const t = fixture({ failOuterWrite: true }), c = t.controller(); await c.refreshCloseout()
    const token = await c.prepareCloseout(ACKS); assert.equal(await c.confirmCloseout(token), false)
    assert.equal(t.storage.get(INNER_STORE).pending, null); assert.equal(t.storage.get(INNER_STORE).archives.length, 1)
    assert.equal(t.storage.get(OUTER_STORE).settled, false); c.hide()
    const sameRuntime = t.controller(); assert.equal(await sameRuntime.refreshCloseout(), false)
    assert.equal(sameRuntime.getState().canPrepare, false); sameRuntime.hide()
    const fresh = fixture({}, t.storage, t.server), reopened = fresh.controller(), before = plain([...t.storage])
    assert.equal(await reopened.refreshCloseout(), true); assert.equal(reopened.getState().closeout.phase, 'recovering')
    assert.deepEqual([...fresh.storage], before); assert.equal(fresh.h.storageWrites.length, 0)
    const resumed = await reopened.prepareCloseout(ACKS); assert.equal(fresh.writes().length, 0)
    assert.equal(await reopened.confirmCloseout(resumed), true)
    assert.deepEqual(fresh.events, ['outer-settle']); assert.equal(fresh.storage.get(INNER_STORE).archives.length, 1)
    assert.equal(t.server.commits, 1); assert.equal(reopened.getState().closeout.archived, true)
    reopened.hide(); t.safe(); fresh.safe(); t.clean(); fresh.clean()
  })
  await check('a concurrent foreign outer request is never overwritten or recovered as the old archived request', async () => {
    const t = fixture({ replaceOuterAfterInner: true }), c = t.controller(); await c.refreshCloseout()
    const token = await c.prepareCloseout(ACKS); assert.equal(await c.confirmCloseout(token), false)
    assert.equal(t.storage.get(OUTER_STORE).requestId, 'foreign_new_request'); assert.equal(t.storage.get(OUTER_STORE).settled, false)
    c.hide(); const fresh = fixture({}, t.storage, t.server), reopened = fresh.controller()
    assert.equal(await reopened.refreshCloseout(), false); assert.equal(reopened.getState().closeout.archived, false)
    assert.equal(fresh.storage.get(OUTER_STORE).requestId, 'foreign_new_request'); assert.equal(fresh.writes().length, 0)
    reopened.hide(); t.safe(); fresh.safe(); t.clean(); fresh.clean()
  })
  await check('wrong card tuple receipt or missing inner evidence cannot obtain closeout eligibility', async () => {
    for (const mutation of ['identity-card', 'review-card', 'review-tuple', 'receipt-tag', 'receipt-not-unknown', 'empty-inner', 'foreign-inner', 'archived-without-inner']) {
      const t = fixture(mutation === 'review-card' ? { reviewPatch: value => ({ ...value, cardId: '9'.repeat(32) }) }
        : mutation === 'review-tuple' ? { reviewPatch: value => ({ ...value, original: { ...value.original, networkRequestId: 'another_request' } }) } : {})
      if (mutation === 'identity-card') t.auth({ localContext: { deviceId: DEVICE, cardId: '9'.repeat(32), address: '192.168.1.152' } })
      if (mutation === 'receipt-tag') t.raw({ receiptQr: t.h.receiptQR('unknown', { ciphertext: '00'.repeat(32) }) })
      if (mutation === 'receipt-not-unknown') t.raw({ receiptQr: t.h.receiptQR('connected') })
      if (mutation === 'empty-inner' || mutation === 'archived-without-inner') t.storage.delete(INNER_STORE)
      if (mutation === 'archived-without-inner') t.server.archived = true
      if (mutation === 'foreign-inner') t.storage.set(INNER_STORE, { v: 1, deviceId: DEVICE, sessionId: SESSION, requestId: 'another_request' })
      const before = plain([...t.storage]), c = t.controller()
      assert.equal(await c.refreshCloseout(), false, mutation)
      assert.equal(c.getState().closeout.eligible, false); assert.equal(c.getState().closeout.archived, false)
      assert.deepEqual([...t.storage], before); assert.equal(t.h.storageWrites.length, 0); assert.equal(t.writes().length, 0)
      c.hide(); t.safe(); t.clean()
    }
  })
  await check('an authenticated unknown receipt without live server eligibility still cannot stage an archive', async () => {
    const t = fixture({ reviewPatch: value => ({ ...value, eligible: false }) }), c = t.controller(), before = plain([...t.storage])
    assert.equal(await c.refreshCloseout(), true); assert.equal(c.getState().closeout.phase, 'blocked')
    assert.equal(c.getState().closeout.eligible, false); await assert.rejects(c.prepareCloseout(ACKS))
    assert.equal(t.writes().length, 0); assert.deepEqual([...t.storage], before); assert.equal(t.h.storageWrites.length, 0)
    c.hide(); t.safe(); t.clean()
  })
  await check('server review and preparation contracts reject changed tuple or extra public fields before append', async () => {
    for (const patch of [value => ({ ...value, extra: true }), value => ({ ...value, cardId: '8'.repeat(32) }),
      value => ({ ...value, original: { ...value.original, requestId: 'other_outer' } }), value => ({ ...value, reviewToken: 'short' })]) {
      const t = fixture({ preparationPatch: patch }), c = t.controller(); await c.refreshCloseout()
      await assert.rejects(c.prepareCloseout(ACKS)); assert.equal(t.server.commits, 0)
      assert.equal(t.h.storageWrites.length, 0); assert.equal(c.getState().canPrepare, false)
      c.hide(); t.safe(); t.clean()
    }
    const t = fixture({ reviewPatch: value => ({ ...value, extra: true }) }), c = t.controller()
    assert.equal(await c.refreshCloseout(), false); assert.equal(t.writes().length, 0); c.hide(); t.safe(); t.clean()
  })
  await check('hide and identity changes invalidate prepared closeout without double commit or new window', async () => {
    for (const change of ['hide', 'card', 'expired']) {
      const t = fixture(), c = t.controller(); await c.refreshCloseout(); const token = await c.prepareCloseout(ACKS)
      if (change === 'hide') c.hide()
      if (change === 'card') t.auth({ localContext: { deviceId: DEVICE, cardId: '8'.repeat(32), address: '192.168.1.152' } })
      if (change === 'expired') t.h.options.now += 20001
      assert.equal(await c.confirmCloseout(token), false); assert.equal(t.server.commits, 0)
      assert.equal(t.storage.get(INNER_STORE).v, 1); assert.equal(t.storage.get(OUTER_STORE).settled, false)
      c.hide(); t.safe(); t.clean()
    }
  })
  await check('duplicate confirmation shares no second server append and a hidden in-flight ACK cannot archive locally', async () => {
    const t = fixture({ deferCommit: true }), c = t.controller(); await c.refreshCloseout(); const token = await c.prepareCloseout(ACKS)
    const commit = c.confirmCloseout(token); await flush(); assert.equal(t.server.commits, 1)
    assert.equal(await c.confirmCloseout(token), false); c.hide(); const views = t.views.length
    t.finishCommit(); assert.equal(await commit, false); assert.equal(t.views.length, views)
    assert.equal(t.storage.get(INNER_STORE).v, 1); assert.equal(t.storage.get(OUTER_STORE).settled, false)
    const fresh = fixture({}, t.storage, t.server), recovered = fresh.controller()
    await recovered.refreshCloseout(); assert.equal(recovered.getState().closeout.phase, 'recovering')
    assert.equal(t.server.commits, 1); assert.equal(fresh.writes().length, 0)
    recovered.hide(); t.safe(); fresh.safe(); t.clean(); fresh.clean()
  })
  await check('legacy USB archive remains readable and cannot impersonate a matching local-gateway audit', async () => {
    const t = fixture(), queue = [QR, t.server.receipt]
    const core = t.coreApi.createLiveProvisioning(t.h.wx, value => value.success({ scanType: 'QR_CODE', result: queue.shift() }))
    core.show(() => {}); await core.scan(); await core.scanReceipt()
    assert.equal(core.getState().archiveReady, true)
    const token = core.prepareArchive(['window-ended', 'usb-checked', 'accept-unknown'])
    assert.equal(core.confirmArchive(token), true)
    const entry = t.storage.get(INNER_STORE).archives[0]
    assert.equal(entry.source, undefined); assert.equal(entry.status, 'unknown'); core.hide()
    const c = t.controller(); t.server.archived = true
    assert.equal(await c.refreshCloseout(), false); assert.equal(c.getState().closeout.archived, false)
    assert.equal(t.storage.get(INNER_STORE).archives.length, 1); assert.equal(t.storage.get(OUTER_STORE).settled, false)
    c.hide(); t.safe(); t.clean()
  })
  console.log(`local-closeout-smoke: ${passed}/${passed} passed (real controller/core/AES, synthetic RPC only)`)
})().catch(error => { console.error(error); process.exitCode = 1 })
