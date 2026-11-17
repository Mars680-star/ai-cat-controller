const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..')

function harness(connection = { baseUrl: 'http://127.0.0.1:8787', phoneLan: false, notice: '电脑开发者工具' }) {
  let definition, clears = 0, refreshStops = 0, timerId = 0
  const timers = new Map()
  const requests = [], writes = []
  const client = { clearSession() { clears++ } }
  for (const method of ['getHealth', 'login', 'listPets', 'logout', 'readPetStatus']) client[method] = (...args) => {
    let resolve, reject
    const promise = new Promise((yes, no) => { resolve = yes; reject = no })
    requests.push({ method, args, resolve, reject })
    return promise
  }
  vm.runInNewContext(ts.transpileModule(fs.readFileSync(path.join(root, 'pages/gateway-setup/index.ts'), 'utf8'), {
    compilerOptions: { target: ts.ScriptTarget.ES2019, module: ts.ModuleKind.CommonJS }
  }).outputText, {
    Page: value => { definition = value },
    setTimeout(fn, delay) { const id = ++timerId; timers.set(id, { fn, delay }); return id },
    clearTimeout(id) { timers.delete(id) },
    wx: { stopPullDownRefresh() { refreshStops++ }, setStorageSync() { assert.fail('no storage') }, login() { assert.fail('page cannot call wx.login') } },
    require(name) {
      if (name === '../../services/development-gateway') return {
        developmentGatewayInfo: () => connection
      }
      assert.equal(name, '../../services/cloud-auth-client')
      return { createCloudAuthClient: () => client, cloudAuthErrorMessage: () => '请求未完成，请重试' }
    }
  })
  const page = { ...definition, data: JSON.parse(JSON.stringify(definition.data)) }
  page.setData = patch => { Object.assign(page.data, patch); writes.push(patch) }
  return { page, requests, writes, timers, clears: () => clears, refreshStops: () => refreshStops,
    count: method => requests.filter(request => request.method === method).length,
    last: method => requests.filter(request => request.method === method).at(-1),
    async poll() { const [id, timer] = timers.entries().next().value; timers.delete(id); timer.fn(); await flush() }
  }
}
const flush = async () => { for (let i = 0; i < 20; i++) await Promise.resolve() }
const PETS = [{ petId: 'pet_a', name: '我的 AI 猫' }, { petId: 'pet_b', name: '另一只 AI 猫' }]
const select = petId => ({ currentTarget: { dataset: { petId } } })
const snapshot = () => ({ readOnly: true, sampledAt: '2026-10-20T00:00:00Z',
  health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' },
  device: { connected: true, deviceSerial: '7c2****a128', networkName: null, batteryPercent: 93,
    batteryState: 'charging', batteryVoltage: 4.175, chargerOnline: true, batteryPresent: true, lastSeenAt: null },
  dialog: { state: 'offline', elapsedSeconds: null, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false }
})
async function signed(pets = PETS) {
  const h = harness(), opened = h.page.onShow()
  h.last('getHealth').resolve({ wechatConfigured: true }); await opened
  const logged = h.page.login()
  h.last('login').resolve({ userId: 'user_a', expiresAt: '2099-01-01T00:00:00Z' }); await flush()
  h.last('listPets').resolve(pets); await logged
  return h
}

const stageTwo = {
  async 'gateway setup displays the selected nonsecret endpoint and transport notice'() {
    const connection = { baseUrl: 'http://192.168.1.110:8788', phoneLan: true, notice: '本地 HTTP 明文 · 电脑须开机' }
    const h = harness(connection), opened = h.page.onShow()
    assert.equal(h.page.data.gatewayAddress, connection.baseUrl)
    assert.equal(h.page.data.transportNotice, connection.notice)
    h.last('getHealth').resolve({ wechatConfigured: true }); await opened
    const view = fs.readFileSync(path.join(root, 'pages/gateway-setup/index.wxml'), 'utf8')
    assert.ok(view.includes('{{gatewayAddress}}')); assert.ok(view.includes('{{transportNotice}}'))
    assert.ok(!view.includes('http://127.0.0.1:8787'))
    h.page.onHide()
  },
  async 'authorized list is explicit and selecting only accepts listed IDs'() {
    const h = await signed()
    assert.equal(h.page.data.pets.length, 2)
    assert.equal(h.page.data.selectedPetId, '')
    assert.equal(h.count('readPetStatus'), 0)
    await h.page.selectPet(select('foreign_pet'))
    assert.equal(h.count('readPetStatus'), 0)
    const reading = h.page.selectPet(select('pet_a'))
    assert.equal(h.last('readPetStatus').args[0], 'pet_a')
    h.last('readPetStatus').resolve(snapshot()); await reading
    assert.equal(h.page.data.selectedPetId, 'pet_a')
    assert.equal(h.page.data.snapshot.device.batteryPercent, 93)
  },
  async 'zero pets never automatically claims or samples a device'() {
    const h = await signed([])
    assert.equal(h.page.data.petCount, 0)
    assert.equal(h.page.data.pets.length, 0)
    assert.equal(h.count('readPetStatus'), 0)
    await h.page.refreshStatus()
    assert.equal(h.count('readPetStatus'), 0)
    assert.equal(h.timers.size, 0)
  },
  async 'refreshing authorization does not force another login'() {
    const h = await signed([])
    const clears = h.clears()
    const refreshed = h.page.refreshPets(), joined = h.page.refreshPets()
    assert.equal(h.count('listPets'), 2)
    h.last('listPets').resolve(PETS); await Promise.all([refreshed, joined])
    assert.equal(h.page.data.petCount, 2)
    assert.equal(h.count('login'), 1)
    assert.equal(h.clears(), clears)
    assert.equal(h.page.data.signedIn, true)
  },
  async 'configuration check preserves signed-in status semantics'() {
    const h = await signed()
    const checking = h.page.checkGateway()
    h.last('getHealth').resolve({ wechatConfigured: true }); await checking
    assert.equal(h.page.data.signedIn, true)
    assert.equal(h.page.data.statusLabel, '微信身份验证成功')
  },
  async 'manual reads are single-flight and polling waits five seconds after settlement'() {
    const h = await signed()
    const reading = h.page.selectPet(select('pet_a'))
    const manual = h.page.refreshStatus()
    assert.equal(h.count('readPetStatus'), 1)
    assert.equal(h.timers.size, 0)
    h.last('readPetStatus').resolve(snapshot()); await Promise.all([reading, manual])
    assert.equal(h.timers.size, 1)
    assert.equal([...h.timers.values()][0].delay, 5000)
    await h.poll()
    assert.equal(h.count('readPetStatus'), 2)
    assert.equal(h.timers.size, 0)
    await h.page.selectPet(select('pet_a'))
    assert.equal(h.count('readPetStatus'), 2)
    h.last('readPetStatus').resolve(snapshot()); await flush()
    assert.equal(h.timers.size, 1)
  },
  async 'switching selection queues behind the old read and discards its result'() {
    const h = await signed(), clears = h.clears()
    const old = h.page.selectPet(select('pet_a')), oldRequest = h.last('readPetStatus')
    const newer = h.page.selectPet(select('pet_b'))
    const joined = h.page.refreshStatus()
    assert.equal(h.count('readPetStatus'), 1)
    assert.equal(h.page.data.snapshot, null)
    assert.equal(h.page.data.selectedPetId, 'pet_b')
    oldRequest.resolve(snapshot()); await old; await flush()
    assert.equal(h.count('readPetStatus'), 2)
    assert.equal(h.last('readPetStatus').args[0], 'pet_b')
    assert.equal(h.page.data.snapshot, null)
    const next = snapshot(); next.device.batteryPercent = 40
    h.last('readPetStatus').resolve(next); await Promise.all([newer, joined])
    assert.equal(h.page.data.snapshot.device.batteryPercent, 40)
    assert.equal(h.clears(), clears, 'switching pets must not clear authentication to cancel one request')
  },
  async 'lost selected ownership clears selection, old snapshot and polling but keeps login'() {
    const h = await signed(), reading = h.page.selectPet(select('pet_a'))
    h.last('readPetStatus').resolve(snapshot()); await reading
    const clears = h.clears(), refreshing = h.page.refreshPets()
    assert.equal(h.timers.size, 0)
    assert.equal(h.page.data.snapshot, null)
    h.last('listPets').resolve([PETS[1]]); await refreshing; await flush()
    assert.equal(h.page.data.selectedPetId, '')
    assert.equal(h.page.data.snapshot, null)
    assert.equal(h.page.data.signedIn, true)
    assert.equal(h.page.data.pets.length, 1)
    assert.equal(h.clears(), clears)
    assert.equal(h.timers.size, 0)
  },
  async 'list refresh invalidates an older read even when ownership is retained'() {
    const h = await signed(), old = h.page.selectPet(select('pet_a')), oldRequest = h.last('readPetStatus')
    const refreshed = h.page.refreshPets()
    h.last('listPets').resolve(PETS); await refreshed
    oldRequest.resolve(snapshot()); await old; await flush()
    assert.equal(h.page.data.snapshot, null)
    assert.equal(h.count('readPetStatus'), 2)
    h.last('readPetStatus').resolve(snapshot()); await flush()
    assert.equal(h.page.data.selectedPetId, 'pet_a')
    assert.equal(h.page.data.snapshot.device.batteryPercent, 93)
  },
  async 'failed reads clear previous snapshot and preserve retryable authentication'() {
    const h = await signed(), reading = h.page.selectPet(select('pet_a'))
    h.last('readPetStatus').resolve(snapshot()); await reading
    const retry = h.page.refreshStatus()
    h.last('readPetStatus').reject({ code: 'TIMEOUT', message: 'private-response-secret' }); await retry
    assert.equal(h.page.data.snapshot, null)
    assert.equal(h.page.data.signedIn, true)
    assert.equal(h.page.data.statusLoading, false)
    assert.ok(!JSON.stringify(h.writes).includes('private-response-secret'))
  },
  async 'unknown battery readings stay unknown and voice offline does not make the device offline'() {
    const h = await signed(), reading = h.page.selectPet(select('pet_a')), value = snapshot()
    for (const key of ['batteryPercent', 'batteryVoltage', 'chargerOnline', 'batteryPresent']) value.device[key] = null
    value.device.batteryState = 'unavailable'
    h.last('readPetStatus').resolve(value); await reading
    assert.equal(h.page.data.deviceOnline, true)
    assert.ok(h.page.data.deviceRows.some(row => row.label === '电量' && row.value === '未验证'))
    assert.ok(h.page.data.deviceRows.some(row => row.label === '电压' && row.value === '未验证'))
    assert.ok(h.page.data.dialogRows.some(row => row.value === '语音服务离线'))
  },
  async 'stale voice data is explicitly unverified, with gateway sample time labeled separately'() {
    const h = await signed(), reading = h.page.selectPet(select('pet_a')), value = snapshot()
    value.dialog.stale = true
    h.last('readPetStatus').resolve(value); await reading
    assert.ok(h.page.data.dialogRows.some(row => row.value.includes('已过期')))
    assert.equal(h.page.data.sampledLabel, value.sampledAt)
    assert.equal(h.page.data.deviceOnline, true)
  },
  async 'refresh failure cannot leave a previously live snapshot visible'() {
    const h = await signed(), reading = h.page.selectPet(select('pet_a'))
    h.last('readPetStatus').resolve(snapshot()); await reading
    const refreshing = h.page.refreshPets()
    h.last('listPets').reject({ code: 'UNAVAILABLE', message: 'private-error' }); await refreshing
    assert.equal(h.page.data.snapshot, null)
    assert.equal(h.timers.size, 0)
    assert.equal(h.page.data.signedIn, true)
    assert.equal(h.page.data.listLoading, false)
  },
  async 'logout clears selected device immediately, and late status cannot repopulate it'() {
    const h = await signed(), old = h.page.selectPet(select('pet_a')), oldRequest = h.last('readPetStatus')
    const exiting = h.page.logout()
    assert.equal(h.page.data.snapshot, null)
    assert.equal(h.page.data.selectedPetId, '')
    assert.equal(h.page.data.signedIn, false)
    oldRequest.resolve(snapshot()); await old
    h.last('logout').resolve({ loggedOut: true }); await exiting
    assert.equal(h.page.data.snapshot, null)
    assert.equal(h.timers.size, 0)
  },
  async 'an old identity read cannot unlock or overwrite the new identity selection'() {
    const h = await signed(), old = h.page.selectPet(select('pet_a')), oldRequest = h.last('readPetStatus')
    h.page.onHide()
    const opened = h.page.onShow(); h.last('getHealth').resolve({ wechatConfigured: true }); await opened
    const logged = h.page.login(); h.last('login').resolve({ userId: 'user_b', expiresAt: '2099-01-01T00:00:00Z' }); await flush()
    h.last('listPets').resolve(PETS); await logged
    const current = h.page.selectPet(select('pet_b'))
    const writes = h.writes.length
    oldRequest.resolve(snapshot()); await old
    assert.equal(h.writes.length, writes)
    assert.equal(h.page.data.statusLoading, true)
    const newer = snapshot(); newer.device.batteryPercent = 37
    h.last('readPetStatus').resolve(newer); await current
    assert.equal(h.page.data.userId, 'user_b')
    assert.equal(h.page.data.snapshot.device.batteryPercent, 37)
  },
  async 'old authorization 401 cannot log out a newer identity'() {
    const h = await signed(), old = h.page.refreshPets(), oldRequest = h.last('listPets')
    h.page.onHide()
    const opened = h.page.onShow(); h.last('getHealth').resolve({ wechatConfigured: true }); await opened
    const logged = h.page.login(); h.last('login').resolve({ userId: 'user_b', expiresAt: '2099-01-01T00:00:00Z' }); await flush()
    h.last('listPets').resolve(PETS); await logged
    const writes = h.writes.length
    oldRequest.reject({ code: 'UNAUTHORIZED' }); await old
    assert.equal(h.writes.length, writes)
    assert.equal(h.page.data.userId, 'user_b')
    assert.equal(h.page.data.signedIn, true)
  },
  async '401 from an earlier selection still clears the current identity'() {
    const h = await signed(), old = h.page.selectPet(select('pet_a')), oldRequest = h.last('readPetStatus')
    const queued = h.page.selectPet(select('pet_b'))
    oldRequest.reject({ code: 'UNAUTHORIZED' }); await Promise.all([old, queued])
    assert.equal(h.page.data.signedIn, false)
    assert.equal(h.page.data.selectedPetId, '')
    assert.equal(h.count('readPetStatus'), 1)
    assert.equal(h.timers.size, 0)
  },
  async 'rapid selection changes only sample the final queued selection'() {
    const h = await signed(), old = h.page.selectPet(select('pet_a')), oldRequest = h.last('readPetStatus')
    const middle = h.page.selectPet(select('pet_b')), latest = h.page.selectPet(select('pet_a'))
    oldRequest.resolve(snapshot()); await old; await flush()
    assert.equal(h.count('readPetStatus'), 2)
    assert.equal(h.last('readPetStatus').args[0], 'pet_a')
    assert.equal(h.page.data.snapshot, null)
    h.last('readPetStatus').resolve(snapshot()); await Promise.all([middle, latest])
    assert.equal(h.page.data.selectedPetId, 'pet_a')
  },
  async 'real zero percent and false charger flags remain real readings'() {
    const h = await signed(), reading = h.page.selectPet(select('pet_a')), value = snapshot()
    value.device.batteryPercent = 0; value.device.chargerOnline = false
    h.last('readPetStatus').resolve(value); await reading
    assert.ok(h.page.data.deviceRows.some(row => row.label === '电量' && row.value === '0%'))
    assert.ok(h.page.data.deviceRows.some(row => row.label === '充电器' && row.value === '未连接'))
  },
  async 'failed health update does not leave an old device snapshot labeled live'() {
    const h = await signed(), reading = h.page.selectPet(select('pet_a'))
    h.last('readPetStatus').resolve(snapshot()); await reading
    const checking = h.page.checkGateway()
    h.last('getHealth').reject({ code: 'UNAVAILABLE', message: 'private-upstream' }); await checking
    assert.equal(h.page.data.snapshot, null)
    assert.equal(h.page.data.signedIn, true)
    assert.ok(!JSON.stringify(h.writes).includes('private-upstream'))
  }
}
for (const method of ['listPets', 'readPetStatus']) {
  stageTwo[`${method} 401 clears identity, selection and all cached status`] = async () => {
    const h = await signed()
    const pending = method === 'listPets' ? h.page.refreshPets() : h.page.selectPet(select('pet_a'))
    h.last(method).reject({ code: 'UNAUTHORIZED', message: 'private-secret' }); await pending
    assert.equal(h.page.data.signedIn, false)
    assert.equal(h.page.data.userId, '')
    assert.equal(h.page.data.pets.length, 0)
    assert.equal(h.page.data.selectedPetId, '')
    assert.equal(h.page.data.snapshot, null)
    assert.equal(h.timers.size, 0)
  }
}
for (const lifecycle of ['onHide', 'onUnload']) {
  for (const method of ['listPets', 'readPetStatus']) {
    stageTwo[`${lifecycle} discards late ${method} completion and stops polling`] = async () => {
      const h = await signed()
      const pending = method === 'listPets' ? h.page.refreshPets() : h.page.selectPet(select('pet_a'))
      const request = h.last(method)
      h.page[lifecycle]()
      const writes = h.writes.length
      request.resolve(method === 'listPets' ? PETS : snapshot()); await pending; await flush()
      assert.equal(h.writes.length, writes)
      assert.equal(h.page.data.signedIn, false)
      assert.equal(h.page.data.snapshot, null)
      assert.equal(h.timers.size, 0)
      await h.page.refreshStatus()
      assert.equal(h.writes.length, writes)
    }
  }
}

async function main() {
  let count = 0
  const h = harness()
  const opened = h.page.onShow()
  assert.equal(h.requests[0].method, 'getHealth')
  assert.equal(h.requests.length, 1)
  count++
  h.requests[0].resolve({ mode: 'local-development', readOnly: true, wechatConfigured: false })
  await opened
  assert.equal(h.page.data.configured, false)
  assert.equal(h.page.data.statusLabel, '等待配置微信凭据')
  await h.page.login()
  assert.equal(h.requests.length, 1, 'missing configuration must not call wx.login')
  count++
  const ready = h.page.checkGateway()
  const duplicate = h.page.checkGateway()
  assert.equal(h.requests.length, 2, 'health refresh is single-flight')
  h.requests[1].resolve({ mode: 'local-development', readOnly: true, wechatConfigured: true })
  await Promise.all([ready, duplicate])
  assert.equal(h.page.data.configured, true)
  count++
  const login = h.page.login()
  h.page.login()
  assert.equal(h.requests.length, 3, 'explicit login is single-flight')
  h.requests[2].resolve({ userId: 'usr_example', expiresAt: '2026-10-19T12:00:00Z' })
  await flush()
  assert.equal(h.requests[3].method, 'listPets')
  h.requests[3].resolve([])
  await login
  assert.equal(h.page.data.signedIn, true)
  assert.equal(h.page.data.petCount, 0, 'do not auto-claim the physical device')
  assert.ok(!JSON.stringify(h.writes).includes('accessToken'))
  count++
  const logout = h.page.logout()
  h.requests[4].reject({ message: 'private-upstream-secret', code: 'UNAVAILABLE' })
  await logout
  assert.equal(h.page.data.signedIn, false)
  assert.ok(!JSON.stringify(h.writes).includes('private-upstream-secret'))
  count++
  const pending = h.page.checkGateway()
  h.page.onHide()
  const hiddenWrites = h.writes.length
  h.requests[5].resolve({ wechatConfigured: true })
  await pending
  assert.equal(h.writes.length, hiddenWrites)
  assert.ok(h.clears() > 0)
  count++
  const newer = h.page.onShow()
  const oldWriteCount = h.writes.length
  assert.equal(h.page.data.signedIn, false)
  assert.equal(h.page.data.userId, '')
  h.page.onUnload()
  h.requests[6].reject({ message: 'private-old-error' })
  await newer
  assert.equal(h.writes.length, oldWriteCount + 1, 'only unload clears the view')
  count++
  const disposedWrites = h.writes.length
  await h.page.onShow()
  await h.page.login()
  assert.equal(h.writes.length, disposedWrites)
  count++
  const expired = harness()
  const initial = expired.page.onShow()
  expired.requests[0].resolve({ wechatConfigured: true }); await initial
  Object.assign(expired.page.data, { signedIn: true, userId: 'old_user', expiresAt: '2099-01-01T00:00:00Z', petCount: 2 })
  const rejectedHealth = expired.page.checkGateway()
  expired.requests[1].reject({ code: 'UNAUTHORIZED', message: 'private-details' }); await rejectedHealth
  assert.equal(expired.page.data.signedIn, false)
  assert.equal(expired.page.data.userId, '')
  assert.equal(expired.page.data.petCount, 0)
  count++
  const refresh = expired.page.onPullDownRefresh()
  expired.page.onHide()
  const returned = expired.page.onShow()
  expired.requests[2].reject({ code: 'CANCELLED' }); await refresh
  assert.equal(expired.refreshStops(), 0, 'old visit must not stop the new native pull refresh')
  expired.requests[3].resolve({ wechatConfigured: false }); await returned
  const visibleRefresh = expired.page.onPullDownRefresh()
  expired.requests[4].resolve({ wechatConfigured: false }); await visibleRefresh
  assert.equal(expired.refreshStops(), 1)
  expired.page.onUnload()
  count++
  const app = JSON.parse(fs.readFileSync(path.join(root, 'app.json'), 'utf8'))
  assert.ok(app.pages.includes('pages/gateway-setup/index'))
  const view = fs.readFileSync(path.join(root, 'pages/device/index.wxml'), 'utf8')
  assert.ok(view.includes('bindtap="openGatewaySetup"'))
  assert.ok(fs.readFileSync(path.join(root, 'services/pet-service.ts'), 'utf8').includes('new MockPetService()'))
  count++
  let failures = 0
  for (const [name, run] of Object.entries(stageTwo)) {
    try { await run(); count++ }
    catch (_) { failures++; console.error('FAIL gateway stage2: ' + name) }
  }
  console.log(`gateway-setup-smoke: ${count}/${count + failures} passed (original 11 safety scenarios plus authorized devices, serial status polling and lifecycle)`)
  if (failures) process.exitCode = 1
}
main().catch(() => { console.error('gateway-setup-smoke: failed; no raw response logged'); process.exitCode = 1 })
