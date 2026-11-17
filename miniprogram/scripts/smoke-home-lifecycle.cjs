const assert = require('node:assert/strict')
const ts = require('typescript')

require.extensions['.ts'] = (module, filename) => {
  const source = require('node:fs').readFileSync(filename, 'utf8')
  module._compile(ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
  }).outputText, filename)
}

let definition
let activeTimers = new Map()
let timerId = 0
let stoppedRefresh = 0
const originalSetInterval = global.setInterval
const originalClearInterval = global.clearInterval
global.setInterval = (callback) => { const id = ++timerId; activeTimers.set(id, callback); return id }
global.clearInterval = (id) => activeTimers.delete(id)
global.Page = (value) => { definition = value }
global.wx = {
  getStorageSync: () => true,
  stopPullDownRefresh: () => { stoppedRefresh++ },
  redirectTo() {}, reLaunch() {}, showToast() {}
}

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const pet = { petId: 'preview', batteryPercent: 80, batteryState: 'discharging', connectionState: 'online' }
const dashboard = { pet, intimacy: {}, today: {}, recent: [] }
let pendingPet
let pendingDashboard
let petCalls = 0
let dashboardCalls = 0
const service = {
  getBoundPet() { petCalls++; return pendingPet ? pendingPet.promise : Promise.resolve(pet) },
  getDashboard() { dashboardCalls++; return pendingDashboard ? pendingDashboard.promise : Promise.resolve(dashboard) }
}
const servicePath = require.resolve('../services/pet-service.ts')
require.cache[servicePath] = { id: servicePath, filename: servicePath, loaded: true, exports: { getPetService: () => service } }
require('../pages/home/index.ts')

function page() {
  const result = { ...definition, data: { ...definition.data }, writes: [] }
  result.setData = (patch) => { Object.assign(result.data, patch); result.writes.push(patch) }
  return result
}

async function main() {
  // A page left during initial loading must not write or start a hidden timer.
  const hidden = page()
  pendingPet = deferred()
  const loading = hidden.onShow()
  hidden.onHide()
  const writesBeforeHide = hidden.writes.length
  pendingPet.resolve(pet)
  await loading
  assert.equal(hidden.writes.length, writesBeforeHide, 'late initial response must not write into a hidden page')
  assert.equal(activeTimers.size, 0, 'late initial loading must not restart a hidden timer')
  pendingPet = null

  const visible = page()
  petCalls = 0
  dashboardCalls = 0
  await visible.onShow()
  assert.equal(petCalls, 1, 'first paint should reuse the bound pet lookup')
  assert.equal(dashboardCalls, 1)
  assert.equal(activeTimers.size, 1)
  const rendered = visible.writes.length
  await visible.loadDashboard(true)
  assert.equal(visible.writes.length, rendered, 'unchanged dashboard should not trigger a full render')

  pendingDashboard = deferred()
  dashboardCalls = 0
  const first = visible.loadDashboard(true)
  const second = visible.loadDashboard(true)
  const pull = visible.onPullDownRefresh()
  await Promise.resolve()
  assert.equal(dashboardCalls, 1, 'poll and pull refresh should share one in-flight request')
  pendingDashboard.resolve(dashboard)
  await Promise.all([first, second, pull])
  assert.equal(stoppedRefresh, 1)
  pendingDashboard = null

  const oldResponse = pendingDashboard = deferred()
  const stale = visible.loadDashboard(true)
  await Promise.resolve()
  visible.onHide()
  const currentResponse = pendingDashboard = deferred()
  const newVisit = visible.onShow()
  await Promise.resolve()
  // A previous visit may finish late; it cannot clobber the new visit or
  // clear that visit's in-flight guard.
  const staleWrites = visible.writes.length
  oldResponse.resolve({ ...dashboard, pet: { ...pet, batteryPercent: 30 } })
  await stale
  assert.equal(visible.writes.length, staleWrites)
  const requestsBeforeRetry = dashboardCalls
  const joined = visible.loadDashboard(true)
  await Promise.resolve()
  assert.equal(dashboardCalls, requestsBeforeRetry)
  currentResponse.resolve({ ...dashboard, pet: { ...pet, batteryPercent: 81 } })
  await Promise.all([newVisit, joined])
  assert.equal(visible.data.dashboard.pet.batteryPercent, 81)
  assert.equal(activeTimers.size, 1)
  pendingDashboard = null

  pendingPet = deferred()
  const failed = visible.onPullDownRefresh()
  pendingPet.reject(new Error('temporary preview error'))
  await failed
  assert.equal(stoppedRefresh, 2, 'failed refresh must release the spinner')
  assert.ok(visible.data.dashboard, 'a refresh failure must retain previous content')
  visible.onUnload()
  assert.equal(activeTimers.size, 0)

  const recovered = page()
  pendingPet = deferred()
  const initialFailure = recovered.onShow()
  pendingPet.reject(new Error('initial preview error'))
  await initialFailure
  assert.equal(activeTimers.size, 0)
  assert.equal(recovered.data.loading, false)
  pendingPet = null
  await recovered.retry()
  assert.equal(recovered.data.error, '')
  assert.equal(activeTimers.size, 1, 'a successful initial retry should restart visible polling')
  recovered.onUnload()
  assert.equal(activeTimers.size, 0)
  console.log('home-lifecycle-smoke: stale results, hidden timers, single-flight, unchanged render and refresh recovery ok')
}

main().catch((error) => { console.error(error); process.exitCode = 1 }).finally(() => {
  global.setInterval = originalSetInterval
  global.clearInterval = originalClearInterval
})
