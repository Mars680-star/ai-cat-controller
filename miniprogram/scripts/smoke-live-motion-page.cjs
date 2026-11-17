// Real motion page + real TS cloud/shared/controller modules; wx is injected offline.
const assert = require('node:assert/strict')
const { harness, motionSnapshot, noTailSnapshot, execution, deviceSnapshot, flush, plain, ACTION_IDS } = require('./smoke-live-motion.cjs')
const choose = actionId => ({ currentTarget: { dataset: { actionId } } })
function bridgeWrites(page) {
  const writes = [], original = page.setData
  page.setData = function(value) { writes.push(plain(value)); return original.call(this, value) }
  return writes
}
const cases = {
  async 'server cooldown is visible and cards unlock only after an authenticated available snapshot'() {
    const h = harness(), page = await h.page(true)
    const sample = motionSnapshot(true, execution('ended_unverified'))
    sample.actions.forEach(item => { item.available = false; item.reason = '动作冷却中' })
    const read = page.liveMotionRefresh(); h.success(sample); await read
    assert.equal(page.data.motionStatus, '短暂冷却中，请稍候再点')
    assert.ok(page.data.actions.every(item => item.disabled)); assert.equal(page.data.availableCount, 0)
    await page.liveExecute(choose('head_nod')); assert.equal(h.motionPosts().length, 0)
    await h.advance(400)
    assert.ok(page.data.actions.every(item => item.disabled))
    assert.equal(h.requests.at(-1).method, 'GET'); assert.ok(h.requests.at(-1).url.endsWith('/motion'))
    h.success(motionSnapshot(true)); await flush()
    assert.ok(page.data.actions.every(item => !item.disabled)); assert.equal(page.data.availableCount, 7)
    assert.equal(h.motionPosts().length, 0); assert.ok(!page.data.motionStatus.includes('已完成'))
  },
  async 'unknown receipt is displayed as pending verification and never as an endless running card'() {
    const h = harness(), page = await h.page(true)
    const read = page.liveMotionRefresh()
    h.success(motionSnapshot(true, { ...execution('unknown'), actionId: 'tail_wag' })); await read
    assert.equal(page.data.motionStatus, '结果待核实，已暂停新动作')
    const card = page.data.actions.find(item => item.actionId === 'tail_wag')
    assert.equal(card.running, false); assert.equal(card.badge, '待核对')
    assert.ok(page.data.actions.every(item => item.disabled)); assert.equal(page.data.motionCanStop, true)
    await page.liveExecute(choose('head_nod')); assert.equal(h.motionPosts().length, 0)
  },
  async 'stale stop tap after state loss gives feedback without a command'() {
    const h = harness(), page = await h.page(true)
    const read = page.liveMotionRefresh(); h.failure(); await read
    assert.equal(page.data.motionCanStop, false)
    await page.liveStopMotion()
    assert.match(page.data.motionError, /停止请求未发送.*刷新.*现场检查/)
    assert.equal(h.motionPosts().length, 0)
  },
  async 'duplicate pending page stop stays in progress without a rejection notice'() {
    const h = harness(), page = await h.page(true)
    const status = page.liveMotionRefresh(); h.success(motionSnapshot(true, execution())); await status
    const stop = page.liveStopMotion()
    assert.equal(page.data.motionStopping, true)
    await page.liveStopMotion()
    assert.equal(page.data.motionError, '')
    assert.equal(h.motionPosts().length, 1)
    h.success(motionSnapshot(true, { ...execution('stopped'), actionId: 'stop' })); await stop
    assert.equal(page.data.motionStatus, '停止请求已确认')
  },
  async 'default product entry performs only health GET and never loads Mock data or logs in'() {
    const h = harness(), page = await h.page(false, false)
    assert.equal(page.data.liveMode, true)
    assert.equal(h.logins.length, 0); assert.equal(h.motionPosts().length, 0)
    assert.ok(h.requests.every(item => item.method === 'GET'))
    assert.equal(h.storageCalls(), 0); assert.equal(page.data.history, null)
    assert.equal(typeof page.useRealDevice, 'undefined'); assert.equal(typeof page.showPreview, 'undefined')
    assert.equal(page.data.actions.length, 7); assert.ok(page.data.actions.every(item => item.disabled))
  },
  async 'product cards keep the original layout and use all seven real canonical actions'() {
    const h = harness(), page = await h.page(true)
    assert.equal(page.data.actions.length, 7); assert.equal(page.data.availableCount, 7)
    assert.equal(page.data.connectedCount, 7); assert.equal(page.data.unavailableCount, 0)
    assert.equal(page.data.selectedName, '小安'); assert.equal(h.logins.length, 1)
    assert.ok(page.data.actions.every(item => item.connected && !item.disabled && item.badge === '可执行'))
    assert.deepEqual(plain(page.data.actions.map(item => item.actionId)).sort(), [...ACTION_IDS].sort())
    assert.equal(h.motionPosts().length, 0)
    assert.ok(!JSON.stringify(page.data).includes('minLevel'))
    assert.equal('progress' in page.data, false); assert.equal('remainingSeconds' in page.data, false)
  },
  async 'each canonical card tap sends exactly one parameter-free command without another confirmation'() {
    for (const actionId of ACTION_IDS) {
      const h = harness(), page = await h.page(true)
      const send = page.liveExecute(choose(actionId))
      await page.liveExecute(choose(actionId)); await page.liveExecute(choose('head_shake'))
      assert.equal(h.motionPosts().length, 1)
      const payload = plain(h.motionPosts()[0].data)
      assert.equal(payload.actionId, actionId)
      assert.deepEqual(Object.keys(payload).sort(), ['actionId', 'requestId'])
      assert.equal(typeof page.liveConfirmMotion, 'undefined')
      h.success(motionSnapshot(true, { ...execution(), actionId })); await send
      assert.equal(page.data.actions.find(item => item.actionId === actionId).running, true)
      assert.equal(page.data.motionCanStop, true)
    }
  },
  async 'unlinked webpage authorization keeps five actions visible but unavailable without demo unlocks'() {
    const h = harness(), page = await h.page(true), sample = motionSnapshot(true)
    sample.actions = sample.actions.map(item => ['head_nod', 'head_shake'].includes(item.actionId) ? item : { ...item, available: false, reason: '需关联网页授权' })
    const read = page.liveMotionRefresh(); h.success(sample); await read
    assert.equal(page.data.connectedCount, 7); assert.equal(page.data.availableCount, 2); assert.equal(page.data.unavailableCount, 5)
    for (const card of page.data.actions.filter(item => !['head_nod', 'head_shake'].includes(item.actionId))) {
      assert.equal(card.reason, '需关联网页授权'); assert.equal(card.disabled, true)
      await page.liveExecute(choose(card.actionId))
    }
    assert.equal(h.motionPosts().length, 0); assert.equal(page.data.motionConfirm, undefined)
  },
  async 'locked personality level and missing tail reasons render without enabling an action'() {
    const h = harness(), page = await h.page(true), sample = motionSnapshot(true)
    const reasons = { proud_pose: '当前性格未解锁', celebration_combo: '亲密度等级不足', tail_wag: '设备未启用尾部' }
    sample.actions = sample.actions.map(item => reasons[item.actionId] ? { ...item, available: false, reason: reasons[item.actionId] } : item)
    const read = page.liveMotionRefresh(); h.success(sample); await read
    assert.equal(page.data.availableCount, 4); assert.equal(page.data.unavailableCount, 3)
    for (const [actionId, reason] of Object.entries(reasons)) {
      const card = page.data.actions.find(item => item.actionId === actionId)
      assert.equal(card.reason, reason); assert.equal(card.disabled, true)
      await page.liveExecute(choose(actionId))
    }
    assert.equal(h.motionPosts().length, 0)
  },
  async 'changed unlock or capability disables the card before dispatch and never queues a tap'() {
    for (const actionId of ACTION_IDS.filter(item => !['head_nod', 'head_shake'].includes(item))) {
      const h = harness(), page = await h.page(true), sample = motionSnapshot(true)
      sample.actions = sample.actions.map(item => item.actionId === actionId ? { ...item, available: false, reason: '动作已不可用，请重新检查授权和设备能力' } : item)
      const read = page.liveMotionRefresh(); h.success(sample); await read
      await page.liveExecute(choose(actionId)); assert.equal(h.motionPosts().length, 0)
      const recover = page.liveMotionRefresh(); h.success(motionSnapshot(true)); await recover
      assert.equal(h.motionPosts().length, 0)
    }
  },
  async 'history reads are fixed authenticated GETs and contain no request body or stored credentials'() {
    const h = harness(), page = await h.page(true)
    const request = h.requests.find(item => item.url.endsWith('/motion/history'))
    assert.equal(request.url, 'http://127.0.0.1:8787/v1/pets/pet_1/motion/history')
    assert.equal(request.method, 'GET'); assert.match(request.header.Authorization, /^Bearer /)
    assert.equal('data' in request, false); assert.equal(h.storageCalls(), 0)
    assert.deepEqual(plain(page.data.history), [])
  },
  async 'real history shows stop and unverified end without claiming physical completion'() {
    const h = harness(), page = await h.page(true); h.autoHistory(false)
    const read = page.refreshHistory()
    h.success({ items: [{ ...execution('stopped', 'stop_1'), actionId: 'stop' }, execution('ended_unverified')], limit: 20 }); await read
    assert.equal(page.data.history.length, 2)
    assert.equal(page.data.history[0].name, '停止动作')
    assert.equal(page.data.history[0].statusText, '停止请求已确认')
    assert.equal(page.data.history[1].statusText, '动作已结束，结果待验证')
    assert.ok(!JSON.stringify(page.data.history).includes('已完成'))
  },
  async 'unchanged status polls do not repeatedly fetch the history list'() {
    const h = harness(), page = await h.page(true)
    const count = h.requests.filter(item => item.url.endsWith('/motion/history')).length
    for (let i = 0; i < 3; i++) {
      const read = page.liveMotionRefresh(); h.success(motionSnapshot(true)); await read
    }
    assert.equal(h.requests.filter(item => item.url.endsWith('/motion/history')).length, count)
  },
  async 'unchanged full motion and history callbacks make no view bridge writes'() {
    const h = harness(), page = await h.page(true), writes = bridgeWrites(page)
    for (let i = 0; i < 3; i++) {
      page.renderLiveMotion(plain(page.realMotion.getState()))
      page.renderHistory({ items: [], loading: false, error: '' })
    }
    assert.deepEqual(writes, [])
    assert.equal(h.motionPosts().length, 0)
  },
  async 'unchanged motion polls send only loading flips without cards or history arrays'() {
    const h = harness(), page = await h.page(true), writes = bridgeWrites(page)
    const historyReads = h.requests.filter(item => item.url.endsWith('/motion/history')).length
    for (let i = 0; i < 3; i++) {
      const read = page.liveMotionRefresh(); h.success(motionSnapshot(true)); await read
    }
    assert.equal(writes.length, 6)
    assert.ok(writes.every(value => Object.keys(value).length === 1 && typeof value.motionLoading === 'boolean'))
    assert.equal(h.requests.filter(item => item.url.endsWith('/motion/history')).length, historyReads)
    assert.equal(h.motionPosts().length, 0)
  },
  async 'history loading flips preserve unchanged rows but a changed receipt updates them'() {
    const h = harness(), page = await h.page(true)
    const row = execution('ended_unverified')
    page.renderHistory({ items: [row], loading: false, error: '' })
    const writes = bridgeWrites(page)
    page.renderHistory({ items: [plain(row)], loading: true, error: '' })
    page.renderHistory({ items: [plain(row)], loading: false, error: '' })
    assert.deepEqual(writes, [{ historyLoading: true }, { historyLoading: false }])
    page.renderHistory({ items: [{ ...row, status: 'failed' }], loading: false, error: '' })
    assert.deepEqual(Object.keys(writes.at(-1)), ['history'])
    assert.equal(page.data.history[0].requestId, row.requestId)
    assert.equal(page.data.history[0].statusText, '动作执行失败')
  },
  async 'a changed sample time is rendered without resending unchanged action cards'() {
    const h = harness(), page = await h.page(true), writes = bridgeWrites(page)
    const value = plain(page.realMotion.getState())
    value.snapshot.sampledAt = '2026-11-01T00:00:03Z'
    page.renderLiveMotion(value)
    assert.equal(writes.length, 1)
    assert.deepEqual(Object.keys(writes[0]).sort(), ['motionSnapshot', 'sampledTime'])
    assert.equal(page.data.motionSnapshot.sampledAt, value.snapshot.sampledAt)
    assert.match(page.data.sampledTime, /:03$/)
  },
  async 'changed execution updates card state and immediate stop availability together'() {
    const h = harness(), page = await h.page(true), writes = bridgeWrites(page)
    const read = page.liveMotionRefresh(); h.success(motionSnapshot(true, execution())); await read
    const running = writes.find(value => value.motionStatus === '动作执行中')
    assert.ok(running && running.motionCanStop === true)
    assert.ok(running.actions.find(item => item.actionId === 'head_nod').running)
    assert.ok(page.data.actions.every(item => item.disabled))
    const stopped = page.liveMotionRefresh(); h.success(motionSnapshot(true, { ...execution('stopped'), actionId: 'stop' })); await stopped
    const receipt = writes.find(value => value.motionStatus === '停止请求已确认')
    assert.ok(receipt && receipt.motionCanStop === false)
    assert.ok(receipt.actions.every(item => !item.running))
    assert.equal(h.motionPosts().length, 0)
  },
  async 'hidden direct render callbacks and late reads never write the view bridge'() {
    const h = harness(), page = await h.page(true)
    const value = plain(page.realMotion.getState()), read = page.liveMotionRefresh(), pending = h.requests.length - 1
    h.hidePage()
    const writes = bridgeWrites(page)
    page.renderLiveMotion({ ...value, snapshot: motionSnapshot(true, execution()) })
    page.renderHistory({ items: [execution()], loading: false, error: '' })
    h.success(motionSnapshot(true, execution()), pending); await read
    assert.deepEqual(writes, [])
    assert.equal(h.hiddenWrites(), 0); assert.equal(h.motionPosts().length, 0)
  },
  async 'history failure clears its rows visibly without hiding an active stop context'() {
    const h = harness(), page = await h.page(true)
    const status = page.liveMotionRefresh(); h.success(motionSnapshot(true, execution())); await status; await flush()
    h.autoHistory(false)
    const read = page.refreshHistory(); h.failure(); await read
    assert.equal(page.data.history, null); assert.match(page.data.historyError, /记录暂不可用.*不影响停止/)
    assert.equal(page.data.motionStatus, '动作执行中'); assert.equal(page.data.motionCanStop, true)
    const stop = page.liveStopMotion(); h.success(motionSnapshot(true, execution('stopped'))); await stop
    assert.equal(page.data.motionStatus, '停止请求已确认')
  },
  async 'pending history never blocks stop and a changed execution schedules one fresh history read'() {
    const h = harness(), page = await h.page(true)
    const status = page.liveMotionRefresh(); h.success(motionSnapshot(true, execution())); await status; await flush()
    h.autoHistory(false)
    const read = page.refreshHistory(), old = h.requests.length - 1
    const stop = page.liveStopMotion(); assert.ok(h.requests.at(-1).url.endsWith('/stop'))
    h.success(motionSnapshot(true, { ...execution('stopped', 'stop_new'), actionId: 'stop' })); await stop
    h.success({ items: [execution()], limit: 20 }, old); await read; await flush()
    assert.ok(h.requests.at(-1).url.endsWith('/motion/history'))
    assert.ok(!page.data.history || !page.data.history.some(item => item.status === 'running'))
    h.success({ items: [{ ...execution('stopped', 'stop_new'), actionId: 'stop' }], limit: 20 }); await flush()
    assert.equal(page.data.history[0].executionId, 'stop_new')
  },
  async 'late history from old device is discarded before the new selection can render'() {
    const h = harness(), page = await h.page(true); h.autoHistory(false)
    const read = page.refreshHistory(), old = h.requests.length - 1
    const select = page.liveSelect({ currentTarget: { dataset: { petId: 'pet_2' } } })
    assert.equal(page.data.history, null)
    h.success({ items: [execution()], limit: 20 }, old); await read
    assert.equal(page.data.history, null)
    h.success(deviceSnapshot(), h.requests.findLastIndex(item => item.url.endsWith('/status'))); await select
    h.success(motionSnapshot(true), h.requests.findLastIndex(item => item.url.endsWith('/motion')))
    h.success({ items: [], limit: 20 }, h.requests.findLastIndex(item => item.url.endsWith('/motion/history'))); await flush()
    assert.equal(page.data.selectedName, '另一只猫'); assert.deepEqual(plain(page.data.history), [])
  },
  async 'hidden history cannot write page state or send automatic stop'() {
    const h = harness(), page = await h.page(true); h.autoHistory(false)
    const read = page.refreshHistory(), old = h.requests.length - 1
    h.hidePage(); h.success({ items: [execution()], limit: 20 }, old); await read
    assert.equal(h.hiddenWrites(), 0); assert.equal(h.motionPosts().length, 0)
  },
  async '401 from history clears identity and all live action context'() {
    const h = harness(), page = await h.page(true); h.autoHistory(false)
    const read = page.refreshHistory(); h.success({}, h.requests.length - 1, 401); await read
    assert.equal(page.data.liveState.signedIn, false); assert.equal(page.data.history, null)
    assert.equal(page.data.motionSnapshot, null); assert.equal(page.data.motionCanStop, false)
  },
  async 'leaving during execute and returning never creates progress timers or blindly replays'() {
    const h = harness(), page = await h.page(true)
    const send = page.liveExecute(choose('head_nod')), old = h.requests.length - 1
    h.hidePage(); h.success(motionSnapshot(true, execution()), old); await send
    await h.advance(6000)
    assert.equal(h.hiddenWrites(), 0); assert.equal(h.motionPosts().length, 1)
    await h.showPage()
    h.success(motionSnapshot(true), h.requests.findLastIndex(item => item.url.endsWith('/motion'))); await flush()
    assert.equal(page.data.motionUncertain, true); assert.equal(page.data.motionCanStop, true)
    assert.ok(page.data.actions.every(item => item.disabled)); assert.equal('progress' in page.data, false)
    assert.equal(h.motionPosts().length, 1); assert.equal(h.hiddenWrites(), 0)
  },
  async 'late response after unload never updates page and never triggers an automatic stop'() {
    const h = harness(), page = await h.page(true)
    const send = page.liveExecute(choose('head_nod')), old = h.requests.length - 1
    page.onUnload(); h.hidePage()
    h.success(motionSnapshot(true, execution()), old); await send; await h.advance(6000)
    assert.equal(h.hiddenWrites(), 0); assert.equal(h.motionPosts().length, 1)
  },
  async 'prepared page shows all seven locked actions without writes'() {
    const h = harness(), page = await h.page()
    assert.equal(page.data.liveMode, true)
    assert.equal(page.data.motionStatus, '动作接口已准备，等待现场试动确认')
    assert.equal(page.data.motionActions.length, 7)
    assert.ok(page.data.motionActions.every(item => item.disabled))
    assert.equal(page.data.motionCanStop, false)
    await page.liveExecute(choose('head_nod')); await page.liveStopMotion()
    assert.equal(h.motionPosts().length, 0)
  },
  async 'a card tap submits immediately and rapid taps are discarded rather than queued'() {
    const h = harness(), page = await h.page(true)
    const send = page.liveExecute(choose('head_nod'))
    assert.equal(page.data.motionSubmitting, true); assert.equal(page.data.motionStatus, '正在发送动作')
    await page.liveExecute(choose('head_nod')); await page.liveExecute(choose('tail_wag'))
    assert.equal(h.motionPosts().length, 1)
    h.success(motionSnapshot(true, execution())); await send
    assert.equal(page.data.motionStatus, '动作执行中'); assert.equal(page.data.motionCanStop, true)
  },
  async 'leaving between in-memory prepare and dispatch cannot send a stale tap'() {
    const h = harness(), page = await h.page(true)
    const prepare = page.realMotion.prepare
    page.realMotion.prepare = actionId => { const token = prepare(actionId); h.hidePage(); return token }
    await page.liveExecute(choose('head_shake'))
    assert.equal(h.motionPosts().length, 0); assert.equal(h.hiddenWrites(), 0)
  },
  async 'failed status read disables taps and automatic GET recovery never replays them'() {
    const h = harness(), page = await h.page(true)
    const read = page.liveMotionRefresh(); h.failure(); await read
    await page.liveExecute(choose('head_nod')); assert.equal(h.motionPosts().length, 0)
    await h.advance(3000); h.success(motionSnapshot(true)); await flush()
    assert.equal(h.motionPosts().length, 0)
    assert.ok(page.data.actions.every(item => !item.disabled))
  },
  async 'new busy state prevents a card tap without sending or stopping'() {
    const h = harness(), page = await h.page(true)
    const read = page.liveMotionRefresh(); h.success(motionSnapshot(true, execution())); await read
    await page.liveExecute(choose('head_nod')); assert.equal(h.motionPosts().length, 0)
    assert.equal(page.data.motionCanStop, true)
  },
  async 'authorization refresh blocks taps and never replays them after resume'() {
    const h = harness(), page = await h.page(true)
    const list = h.session.refreshPets()
    await page.liveExecute(choose('head_nod')); assert.equal(h.motionPosts().length, 0)
    h.success([{ petId: 'pet_1', name: '小安' }]); await flush()
    const statusIndex = h.requests.findLastIndex(item => item.url.endsWith('/status'))
    h.success(deviceSnapshot(), statusIndex); await list
    const motionIndex = h.requests.findLastIndex(item => item.url.endsWith('/motion'))
    h.success(motionSnapshot(true), motionIndex); await flush()
    assert.equal(h.motionPosts().length, 0)
  },
  async 'shared status GET in flight does not delay a valid direct action tap'() {
    const h = harness(), page = await h.page(true)
    await h.advance(5000)
    const statusIndex = h.requests.findLastIndex(item => item.url.endsWith('/status'))
    const motionIndex = h.requests.findLastIndex(item => item.url.endsWith('/motion'))
    assert.equal(h.session.getState().statusLoading, true)
    const send = page.liveExecute(choose('head_nod')); assert.equal(h.motionPosts().length, 1)
    h.success(motionSnapshot(true, execution())); await send
    h.success(deviceSnapshot(), statusIndex)
    h.success(motionSnapshot(true), motionIndex); await flush()
    assert.equal(h.motionPosts().length, 1); assert.equal(page.data.motionStatus, '动作执行中')
  },
  async 'programmatic invalid action and legacy Mock events cannot bypass live page'() {
    const h = harness(), page = await h.page(true)
    for (const actionId of ['tail', 'wag-tail', 'proud-turn', 'quiet-company', 'greeting', 'level-up', 'stop', 'head_nod?angle=100', '__proto__']) page.liveExecute(choose(actionId))
    assert.equal(typeof page.execute, 'undefined'); assert.equal(typeof page.confirmStop, 'undefined')
    assert.equal(h.motionPosts().length, 0)
  },
  async 'stop remains immediately usable during unresolved execute'() {
    const h = harness(), page = await h.page(true)
    const send = page.liveExecute(choose('head_nod')); const first = h.requests.length - 1
    assert.equal(page.data.motionSubmitting, true); assert.equal(page.data.motionCanStop, true)
    const stop = page.liveStopMotion(); assert.equal(h.motionPosts().length, 2)
    assert.ok(h.requests.at(-1).url.endsWith('/stop'))
    h.success(motionSnapshot(true, { ...execution('stopped'), actionId: 'stop' })); await stop
    h.success(motionSnapshot(true, execution()), first); await send
    assert.equal(page.data.motionStatus, '停止请求已确认')
  },
  async 'failed execution stays unresolved and explicit one-tap retry preserves request id'() {
    const h = harness(), page = await h.page(true)
    const send = page.liveExecute(choose('head_nod')), payload = plain(h.requests.at(-1).data)
    h.failure(); await send
    assert.equal(page.data.motionStatus, '结果不确定，请停止并检查')
    assert.equal(page.data.motionCanRetry, true)
    const retry = page.liveRetryMotion(); assert.deepEqual(plain(h.requests.at(-1).data), payload)
    h.success(motionSnapshot(true, execution('ended_unverified'))); await retry
    assert.equal(page.data.motionStatus, '动作已结束，结果待验证')
    assert.ok(!page.data.motionStatus.includes('已完成'))
  },
  async 'selection change between prepare and dispatch cannot target either device'() {
    const h = harness(), page = await h.page(true)
    const prepare = page.realMotion.prepare
    let selected
    page.realMotion.prepare = actionId => { const token = prepare(actionId); selected = h.session.selectPet('pet_2'); return token }
    await page.liveExecute(choose('head_nod'))
    assert.equal(h.motionPosts().length, 0)
    h.success(deviceSnapshot(), h.requests.findLastIndex(item => item.url.endsWith('/status'))); await selected
    assert.equal(h.motionPosts().length, 0)
  },
  async '401 clears page snapshot and actionable buttons'() {
    const h = harness(), page = await h.page(true), read = page.liveMotionRefresh()
    h.success({}, h.requests.length - 1, 401); await read
    assert.equal(page.data.liveState.signedIn, false); assert.equal(page.data.motionSnapshot, null)
    assert.equal(page.data.motionCanStop, false); assert.equal(page.data.motionActions.length, 0)
  },
  async 'hidden page ignores in-flight response and has no hidden writes or automatic stop'() {
    const h = harness(), page = await h.page(true), read = page.liveMotionRefresh(), old = h.requests.length - 1
    h.hidePage(); h.success(motionSnapshot(true, execution()), old); await read
    await h.advance(3100)
    assert.equal(h.hiddenWrites(), 0); assert.equal(h.motionPosts().length, 0)
  },
  async 'identity loss between prepare and dispatch removes action context without commands'() {
    const h = harness(), page = await h.page(true)
    const prepare = page.realMotion.prepare
    page.realMotion.prepare = actionId => { const token = prepare(actionId); h.session.clear(); return token }
    await page.liveExecute(choose('head_nod'))
    assert.equal(page.data.motionSnapshot, null); assert.equal(h.motionPosts().length, 0)
  },
  async 'explicit refresh while execute pending cannot race over stop result'() {
    const h = harness(), page = await h.page(true)
    const send = page.liveExecute(choose('head_nod')), index = h.requests.length - 1
    const count = h.requests.length; await page.liveMotionRefresh(); assert.equal(h.requests.length, count)
    const stop = page.liveStopMotion(); h.success(motionSnapshot(true, execution('stopped'))); await stop
    h.success(motionSnapshot(true, execution()), index); await send
    assert.equal(page.data.motionStatus, '停止请求已确认')
  },
  async 'read failure while a real execution may move preserves stop-only uncertainty'() {
    const h = harness(), page = await h.page(true)
    const read = page.liveMotionRefresh(); h.success(motionSnapshot(true, execution())); await read
    const failed = page.liveMotionRefresh(); h.failure(); await failed
    assert.equal(page.data.motionStatus, '结果不确定，请停止并检查')
    assert.equal(page.data.motionCanStop, true); assert.ok(page.data.motionActions.every(item => item.disabled))
    const stop = page.liveStopMotion(); assert.equal(h.requests.at(-1).data.executionId, 'exec_1')
    h.success(motionSnapshot(true, execution('stopped'))); await stop
  },
  async 'software stop acknowledgement with busy latch never claims motor physically stopped'() {
    const h = harness(), page = await h.page(true), read = page.liveMotionRefresh()
    const value = motionSnapshot(true, execution('stopped'))
    value.deviceBusy = true; value.actions.forEach(item => { item.available = false })
    h.success(value); await read
    assert.equal(page.data.motionStatus, '停止请求已确认，仍需现场核查')
    assert.ok(page.data.motionActions.every(item => item.disabled))
    assert.equal(h.motionPosts().length, 0)
  },
  async 'verified no-tail device keeps all seven cards and disables the four tail presets truthfully'() {
    const h = harness(), page = await h.page(true), read = page.liveMotionRefresh()
    h.success(noTailSnapshot()); await read
    assert.equal(page.data.connectedCount, 7); assert.equal(page.data.availableCount, 3); assert.equal(page.data.unavailableCount, 4)
    for (const actionId of ['tail_wag', 'proud_pose', 'greeting_combo', 'celebration_combo']) {
      const card = page.data.actions.find(item => item.actionId === actionId)
      assert.equal(card.disabled, true); assert.equal(card.reason, '该设备没有尾部机构')
      await page.liveExecute(choose(actionId))
    }
    assert.equal(h.motionPosts().length, 0)
  },
  async 'no-tail raw-only snapshot shows two available heads and does not invent quiet companion authorization'() {
    const h = harness(), page = await h.page(true), sample = noTailSnapshot(), read = page.liveMotionRefresh()
    sample.actions = sample.actions.map(item => item.actionId === 'quiet_companion' ? { ...item, available: false, reason: '需关联网页授权' } : item)
    h.success(sample); await read
    assert.equal(page.data.connectedCount, 7); assert.equal(page.data.availableCount, 2); assert.equal(page.data.unavailableCount, 5)
    const quiet = page.data.actions.find(item => item.actionId === 'quiet_companion')
    assert.equal(quiet.disabled, true); assert.equal(quiet.reason, '需关联网页授权')
  },
  async 'paused device prevents action taps but preserves its independent stop channel'() {
    const h = harness(), page = await h.page(true)
    const read = page.liveMotionRefresh(); h.success(noTailSnapshot(execution(), true)); await read
    assert.equal(page.data.availableCount, 0); assert.equal(page.data.motionCanStop, true)
    assert.match(page.data.motionStatus, /设备动作已暂停/)
    await page.liveExecute(choose('head_nod')); assert.equal(h.motionPosts().length, 0)
    const stop = page.liveStopMotion(); h.success(noTailSnapshot({ ...execution('stopped'), actionId: 'stop' }, true)); await stop
    assert.equal(h.motionPosts().length, 1); assert.ok(h.motionPosts()[0].url.endsWith('/stop'))
  },
  async 'tail capability change between prepare and dispatch cannot send a tail action'() {
    const h = harness(), page = await h.page(true)
    const token = page.realMotion.prepare('tail_wag')
    const read = page.liveMotionRefresh(); h.success(noTailSnapshot()); await read
    await page.realMotion.confirm(token)
    assert.equal(h.motionPosts().length, 0); assert.equal(page.data.actions.length, 7)
  },
  async 'malformed short catalog never hides tails by inference and cannot enable a new action'() {
    const h = harness(), page = await h.page(true), sample = noTailSnapshot()
    delete sample.hasTail
    const read = page.liveMotionRefresh(); h.success(sample); await read
    assert.equal(page.data.actions.length, 7); assert.equal(page.data.availableCount, 0)
    assert.ok(page.data.actions.every(item => item.disabled)); assert.equal(h.motionPosts().length, 0)
  }
}
;(async () => {
  for (const [name, run] of Object.entries(cases)) {
    try { await run() } catch (error) { console.error('FAIL:', name); throw error }
  }
  console.log('live-motion-page-smoke: ' + Object.keys(cases).length + '/' + Object.keys(cases).length + ' passed (real page, injected wx; no real device writes)')
})().catch(error => { console.error(error); process.exitCode = 1 })
