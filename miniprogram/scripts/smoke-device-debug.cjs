const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const filename = path.resolve(__dirname, '../pages/device-debug/index.ts')
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }
const sample = () => ({ health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' },
  device: { connected: true, batteryPercent: null, batteryVoltage: null, chargerOnline: null, batteryPresent: null },
  dialog: { state: 'ready', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false },
  readOnly: true, sampledAt: '2026-10-19T12:00:00Z' })

function harness() {
  let definition, nextTimer = 0, aborts = 0
  const timers = new Map(), requests = [], writes = []
  const client = { readStatus(token) {
    let resolve, reject
    const promise = new Promise((yes, no) => { resolve = yes; reject = no })
    requests.push({ token, resolve, reject })
    return { promise, abort() { aborts++ } }
  } }
  const module = { exports: {} }
  new vm.Script(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
  }).outputText, { filename }).runInNewContext({
    module, exports: module.exports, Page: value => { definition = value },
    wx: { stopPullDownRefresh() {}, getStorageSync() { throw Error('debug page must not use storage') }, setStorageSync() { throw Error('debug page must not store tokens') } },
    setTimeout(fn, delay) { const id = ++nextTimer; timers.set(id, { fn, delay }); return id },
    clearTimeout(id) { timers.delete(id) },
    require(name) {
      assert.equal(name, '../../services/dev-gateway-client', 'debug must not import Mock services')
      return { createDevGatewayClient: () => client, gatewayErrorMessage: () => '状态读取失败，请重试' }
    }
  })
  const page = { ...definition, data: JSON.parse(JSON.stringify(definition.data)) }
  page.setData = patch => { Object.assign(page.data, patch); writes.push(patch) }
  return { page, timers, requests, writes, aborts: () => aborts,
    enter(token = 'temporary-secret-for-local-debug-only') { page.onTokenInput({ detail: { value: token } }) },
    async poll() { const [id, timer] = timers.entries().next().value; timers.delete(id); timer.fn(); await flush() }
  }
}

async function main() {
  const cases = []
  const h = harness()
  h.page.onShow()
  assert.equal(h.requests.length, 0, 'no token means no automatic network read')
  assert.equal(h.page.data.statusLabel, '未验证')
  cases.push('initial state requires a token')
  h.enter()
  assert.ok(!JSON.stringify(h.writes).includes('temporary-secret'), 'token stays outside view data')
  cases.push('token never enters view data')
  const first = h.page.refresh()
  const joined = h.page.refresh()
  assert.equal(h.requests.length, 1, 'manual refresh shares the in-flight read')
  h.requests[0].resolve(sample())
  await Promise.all([first, joined])
  assert.equal(h.page.data.requesting, false)
  assert.equal(h.timers.size, 1)
  assert.equal([...h.timers.values()][0].delay, 5000)
  assert.ok(h.page.data.deviceRows.some(row => row.label === '电量' && row.value === '未验证'))
  cases.push('single-flight success preserves missing readings and schedules five-second polling')
  await h.poll()
  assert.equal(h.requests.length, 2)
  assert.equal(h.timers.size, 0, 'next poll starts only after the current read settles')
  cases.push('polling is serial')
  h.page.onHide()
  const hiddenWrites = h.writes.length
  assert.equal(h.aborts(), 1)
  assert.equal(h.timers.size, 0)
  h.requests[1].resolve({ ...sample(), device: { connected: false } })
  await flush()
  assert.equal(h.writes.length, hiddenWrites, 'late hidden result must not write')
  cases.push('hide aborts and late results do not write')
  h.page.onShow()
  await h.page.refresh()
  assert.equal(h.requests.length, 2, 'reentry must require a fresh temporary token')
  cases.push('reentry clears credentials')
  h.enter('new-secret-for-local-debug-only')
  const current = h.page.refresh()
  h.requests[2].resolve(sample())
  await current
  assert.equal(h.page.data.statusLabel, '真实状态')
  cases.push('new credentials can read after reentry')

  h.enter('replacement-secret-for-local-debug-only')
  const stale = h.page.refresh()
  h.enter('latest-secret-for-local-debug-only')
  const latest = h.page.refresh()
  h.requests[4].resolve(sample())
  await latest
  const latestWrites = h.writes.length
  h.requests[3].resolve({ ...sample(), device: { connected: false } })
  await stale
  assert.equal(h.writes.length, latestWrites, 'superseded result must not overwrite newer credentials/read')
  cases.push('latest credentials and request win')
  h.enter('retry-secret-for-local-debug-only')
  const failure = h.page.refresh()
  h.requests[5].reject({ code: 'TIMEOUT', message: 'raw secret debug response' })
  await failure
  assert.equal(h.page.data.statusLabel, '不可达')
  assert.ok(!JSON.stringify(h.writes).includes('raw secret'))
  assert.equal(h.page.data.snapshot, null, 'failed read cannot keep presenting old data as live')
  cases.push('timeout removes old readings and sanitizes details')
  const retry = h.page.refresh()
  h.requests[6].resolve(sample())
  await retry
  assert.equal(h.page.data.statusLabel, '真实状态')
  cases.push('timeout can be retried')
  h.page.onUnload()
  assert.equal(h.timers.size, 0)
  assert.ok(!JSON.stringify(h.page.data).includes('secret'))
  cases.push('unload clears data and polling')

  const oldVisit = harness()
  oldVisit.page.onShow()
  oldVisit.enter()
  const oldRead = oldVisit.page.refresh()
  oldVisit.page.onHide()
  oldVisit.page.onShow()
  oldVisit.enter('next-visit-secret-for-local-debug-only')
  const newRead = oldVisit.page.refresh()
  oldVisit.requests[0].reject({ code: 'TIMEOUT', message: 'secret old failure' })
  await oldRead
  assert.equal(oldVisit.page.data.requesting, true, 'old visit must not unlock the new request')
  oldVisit.requests[1].resolve(sample())
  await newRead
  cases.push('old visit cannot unlock a new request')
  const unauthorized = oldVisit.page.refresh()
  oldVisit.requests[2].reject({ code: 'UNAUTHORIZED', message: 'unsafe details' })
  await unauthorized
  assert.equal(oldVisit.page.data.statusLabel, '未验证')
  assert.equal(oldVisit.timers.size, 0, 'authorization failures require explicit retry, not endless polling')
  cases.push('unauthorized reads stop polling and remain unverified')
  const recovered = oldVisit.page.refresh()
  const staleSample = sample()
  staleSample.dialog.stale = true
  oldVisit.requests[3].resolve(staleSample)
  await recovered
  assert.equal(oldVisit.page.data.statusLabel, '未验证')
  assert.ok(oldVisit.page.data.dialogRows.some(row => row.value.includes('已过期')))
  cases.push('stale dialogue remains unverified')
  oldVisit.page.onUnload()
  const writesAfterUnload = oldVisit.writes.length
  oldVisit.page.onShow()
  await oldVisit.page.refresh()
  assert.equal(oldVisit.writes.length, writesAfterUnload, 'a disposed page cannot resume polling or writes')
  cases.push('disposed page cannot resume')

  const offline = harness()
  offline.page.onShow()
  offline.enter()
  const offlineRead = offline.page.refresh()
  const offlineSample = sample()
  offlineSample.dialog.state = 'offline'
  offline.requests[0].resolve(offlineSample)
  await offlineRead
  assert.equal(offline.page.data.statusLabel, '真实状态')
  assert.equal(offline.page.data.online, true)
  assert.equal(offline.page.data.error, '')
  assert.ok(offline.page.data.dialogRows.some(row => row.label === '对话状态' && row.value === '离线'))
  offline.page.onUnload()
  cases.push('voice offline is not gateway unreachable')
  for (const [phase, label] of Object.entries({
    processing_audio: '处理语音中', followup_preparing: '准备追问', connected: '语音已连接',
    proactive_queued: '主动问候等待中', proactive_speaking: '主动问候中',
    proactive_finishing: '主动问候收尾中', stopping: '语音停止中'
  })) {
    const phaseCase = harness()
    phaseCase.page.onShow(); phaseCase.enter()
    const phaseRead = phaseCase.page.refresh()
    const value = sample(); value.dialog.state = phase; value.dialog.elapsedSeconds = 1
    phaseCase.requests[0].resolve(value)
    await phaseRead
    assert.ok(phaseCase.page.data.dialogRows.some(row => row.label === '对话状态' && row.value === label))
    assert.equal(phaseCase.page.data.statusLabel, '真实状态')
    phaseCase.page.onUnload()
    cases.push('normal native phase label: ' + phase)
  }
  assert.equal(cases.length, 23)
  console.log(`device-debug-smoke: ${cases.length}/${cases.length} runtime scenarios passed (memory-only token, polling, retry, abort, lifecycle and offline semantics)`)
}
main().catch(error => { console.error(error); process.exitCode = 1 })
