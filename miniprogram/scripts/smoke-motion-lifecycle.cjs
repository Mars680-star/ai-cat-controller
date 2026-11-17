// Legacy Mock service lifecycle only. Product-page lifecycle now lives in smoke-live-motion-page.cjs.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..')

function harness() {
  let now = 2000000000000
  let sequence = 0
  let pageDefinition
  let hidden = false
  let hiddenWrites = 0
  let hiddenToasts = 0
  const timers = new Map()
  const storage = new Map()
  const toasts = []
  const timerErrors = []
  const cache = new Map()
  const schedule = (fn, delay, interval = false) => {
    const id = ++sequence
    timers.set(id, { fn, at: now + Math.max(1, delay || 0), interval: interval ? Math.max(1, delay || 0) : 0 })
    return id
  }
  class ClockDate extends Date {
    constructor(...args) { if (args.length) super(...args); else super(now) }
    static now() { return now }
  }
  const context = vm.createContext({
    console, Date: ClockDate,
    setTimeout: (fn, delay) => schedule(fn, delay), clearTimeout: id => timers.delete(id),
    setInterval: (fn, delay) => schedule(fn, delay, true), clearInterval: id => timers.delete(id),
    Page(definition) { pageDefinition = definition },
    wx: {
      getStorageSync: key => storage.get(key), setStorageSync: (key, value) => storage.set(key, value),
      removeStorageSync: key => storage.delete(key), showToast: value => { if (hidden) hiddenToasts++; toasts.push(value) },
      redirectTo() {}, stopPullDownRefresh() {}
    }
  })
  function load(relative, parent = root) {
    let filename = path.resolve(parent, relative)
    if (!filename.endsWith('.ts')) filename += '.ts'
    if (cache.has(filename)) return cache.get(filename).exports
    const module = { exports: {} }
    cache.set(filename, module)
    const output = ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
      compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
    }).outputText
    const wrapper = new vm.Script('(function(require,module,exports){' + output + '\n})', { filename }).runInContext(context)
    wrapper(name => load(name, path.dirname(filename)), module, module.exports)
    return module.exports
  }
  const { PET } = load('./mocks/pet')
  storage.set('aiCatSession', { userId: 'motion-smoke' })
  storage.set('aiCatPet', { ...PET })
  const service = load('./services/pet-service').getPetService()
  const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }
  async function tick(ms) {
    const end = now + ms
    await flush()
    for (let guard = 0; guard < 10000; guard++) {
      const next = [...timers.entries()].filter(([, timer]) => timer.at <= end).sort((a, b) => a[1].at - b[1].at || a[0] - b[0])[0]
      if (!next) { now = end; await flush(); if (timerErrors.length) throw timerErrors.shift(); return }
      const [id, timer] = next
      now = timer.at
      if (timer.interval) timer.at += timer.interval
      else timers.delete(id)
      const result = timer.fn()
      if (result && typeof result.catch === 'function') result.catch(error => timerErrors.push(error))
      await flush()
    }
    throw Error('fake timer loop exceeded bound')
  }
  async function settle(promise) {
    let completed = false, value, failure
    Promise.resolve(promise).then(result => { value = result; completed = true }, error => { failure = error; completed = true })
    for (let guard = 0; guard < 200; guard++) {
      await flush()
      if (completed) { if (failure) throw failure; return value }
      const next = [...timers.values()].sort((a, b) => a.at - b.at)[0]
      if (!next) throw Error('unresolved promise without timer')
      await tick(Math.max(0, next.at - now))
    }
    throw Error('promise did not settle')
  }
  return {
    service, petId: PET.petId, tick, settle, toasts, storage,
    scenarioConfig: load('./mocks/scenario-config').MOCK_SCENARIO_CONFIG,
    rebind() { storage.set('aiCatPet', { ...PET }) },
    hiddenWrites: () => hiddenWrites,
    hiddenToasts: () => hiddenToasts,
    intervals: () => [...timers.values()].filter(timer => timer.interval).length
  }
}

const cases = {
  async 'background action reaches its deadline without page timers'() {
    const h = harness()
    await h.settle(h.service.executeAction(h.petId, 'nod', 'background'))
    await h.tick(5000)
    const actions = await h.settle(h.service.getActions(h.petId))
    assert.equal(actions.some(action => action.state === 'running'), false)
    assert.equal((await h.settle(h.service.getActionHistory(h.petId)))[0].status, '已完成')
  },
  async 'same execution request is idempotent'() {
    const h = harness()
    await h.settle(Promise.all([
      h.service.executeAction(h.petId, 'nod', 'duplicate'), h.service.executeAction(h.petId, 'nod', 'duplicate')
    ]))
    const history = await h.settle(h.service.getActionHistory(h.petId))
    assert.equal(history.filter(item => item.requestId === 'duplicate').length, 1)
  },
  async 'a second action cannot overwrite an active execution'() {
    const h = harness()
    const first = h.service.executeAction(h.petId, 'nod', 'busy-first')
    const second = h.service.executeAction(h.petId, 'shake-head', 'busy-second').then(() => null, error => error)
    const [, error] = await h.settle(Promise.all([first, second]))
    assert.equal(error && error.code, 'ACTION_BUSY')
  },
  async 'late finish cannot overwrite a cancellation'() {
    const h = harness()
    await h.settle(h.service.executeAction(h.petId, 'nod', 'cancelled'))
    await h.settle(h.service.stopAction(h.petId, 'stop-cancelled'))
    await h.settle(h.service.finishAction('cancelled'))
    assert.equal((await h.settle(h.service.getActionHistory(h.petId)))[0].status, '已取消')
  },
  async 'an earlier stop never cancels a newer action'() {
    const h = harness()
    await h.settle(h.service.executeAction(h.petId, 'nod', 'old-execution'))
    const stopping = h.service.stopAction(h.petId, 'stop-old', 'old-execution')
    await h.tick(10)
    await h.settle(h.service.executeAction(h.petId, 'shake-head', 'new-execution'))
    await h.settle(stopping)
    const history = await h.settle(h.service.getActionHistory(h.petId))
    assert.equal(history.find(item => item.requestId === 'old-execution').status, '已取消')
    assert.equal(history.find(item => item.requestId === 'new-execution').status, '执行中')
  },
  async 'stopping after expiry preserves completion'() {
    const h = harness()
    await h.settle(h.service.executeAction(h.petId, 'nod', 'already-done'))
    await h.tick(5000)
    await h.settle(h.service.stopAction(h.petId, 'late-stop', 'already-done'))
    assert.equal((await h.settle(h.service.getActionHistory(h.petId)))[0].status, '已完成')
  },
  async 'an old finish await cannot complete a newer execution'() {
    const h = harness()
    await h.settle(h.service.executeAction(h.petId, 'nod', 'finish-old'))
    const finishing = h.service.finishAction('finish-old')
    const stopping = h.service.stopAction(h.petId, 'stop-finish-old', 'finish-old')
    await h.settle(h.service.executeAction(h.petId, 'shake-head', 'finish-new'))
    await h.settle(Promise.all([finishing, stopping]))
    const history = await h.settle(h.service.getActionHistory(h.petId))
    assert.equal(history.find(item => item.requestId === 'finish-old').status, '已取消')
    assert.equal(history.find(item => item.requestId === 'finish-new').status, '执行中')
  },
  async 'local product reset clears execution and request caches'() {
    const h = harness()
    await h.settle(h.service.executeAction(h.petId, 'nod', 'reuse-after-reset'))
    await h.settle(h.service.resetProductData('RESET_PRODUCT_DATA'))
    assert.equal(h.service.actionExecution, null)
    assert.equal(h.service.actionRequests.size, 0)
    h.rebind()
    const result = await h.settle(h.service.executeAction(h.petId, 'shake-head', 'reuse-after-reset'))
    assert.equal(result.duplicate, false)
    assert.equal(result.status, 'running')
  },
  async 'unbinding clears action state before a new local binding'() {
    const h = harness()
    await h.settle(h.service.executeAction(h.petId, 'nod', 'reuse-after-unbind'))
    await h.settle(h.service.unbindPet(h.petId))
    assert.equal(h.service.actionExecution, null)
    assert.equal(h.service.actionRequests.size, 0)
    h.rebind()
    const result = await h.settle(h.service.executeAction(h.petId, 'shake-head', 'reuse-after-unbind'))
    assert.equal(result.duplicate, false)
    assert.equal(result.status, 'running')
  },
  async 'restarting a development scenario drops old execution caches'() {
    const h = harness()
    await h.settle(h.service.executeAction(h.petId, 'nod', 'reuse-after-scenario'))
    h.scenarioConfig.enabled = true
    h.scenarioConfig.name = 'online_lv2'
    h.service.prepareDevelopmentScenario()
    assert.equal(h.service.actionExecution, null)
    assert.equal(h.service.actionRequests.size, 0)
    const result = await h.settle(h.service.executeAction(h.petId, 'shake-head', 'reuse-after-scenario'))
    assert.equal(result.duplicate, false)
  },
  async 'a fresh running scenario seeds a new execution instead of reusing the old one'() {
    const h = harness()
    await h.settle(h.service.executeAction(h.petId, 'nod', 'before-running-scenario'))
    h.scenarioConfig.enabled = true
    h.scenarioConfig.name = 'action_running'
    h.service.prepareDevelopmentScenario()
    assert.equal(h.service.actionExecution, null)
    assert.equal(h.service.actionRequests.size, 0)
    const actions = await h.settle(h.service.getActions(h.petId))
    const running = actions.find(action => action.state === 'running')
    assert.ok(running)
    assert.notEqual(running.executionId, 'before-running-scenario')
    assert.equal(h.service.actionRequests.size, 1)
  }
}

async function main() {
  let failures = 0
  for (const [name, run] of Object.entries(cases)) {
    try { await run(); console.log('ok - ' + name) }
    catch (error) { failures++; console.error('not ok - ' + name + '\n' + error.message) }
  }
  console.log('motion-lifecycle-smoke: ' + (Object.keys(cases).length - failures) + '/' + Object.keys(cases).length + ' passed')
  if (failures) process.exitCode = 1
}
main().catch(error => { console.error(error); process.exitCode = 1 })
