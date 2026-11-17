// Local TS page runtime checks: controlled promises, no network, GUI or device.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

require.extensions['.ts'] = (module, filename) => {
  module._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
  }).outputText, filename)
}

function deferred() {
  let resolve, reject
  const promise = new Promise((yes, no) => { resolve = yes; reject = no })
  return { promise, resolve, reject }
}
const input = value => ({ detail: { value } })
const flush = async () => { for (let i = 0; i < 16; i++) await Promise.resolve() }
const pet = { petId: 'preview', name: '小安', personalityName: '安静', connectionState: 'online', batteryPercent: 86, batteryState: 'discharging' }

function harness(name, overrides = {}) {
  const calls = [], writes = [], toasts = [], redirects = [], modals = []
  const storage = new Map([['aiCatSession', { userId: 'preview-user', displayName: '体验用户' }]])
  const service = {
    async getBoundPet() { return { ...pet } },
    async getSettings() { return { volume: 62, followUpSeconds: 30 } },
    async getDeviceDetail() { return { ...pet } },
    async updateSettings(id, value) { return { pet: { ...pet, name: value.name } } },
    async submitFeedback() {}, async updateDeviceStatus() {}, async reconnectDevice() {}, async bindPet() {},
    async unbindPet() {}, async clearMemory() {}, async resetProductData() { return { backupId: 'preview-only' } },
    ...overrides
  }
  for (const [key, method] of Object.entries(service)) {
    service[key] = (...args) => { calls.push({ method: key, args }); return method(...args) }
  }
  const wx = {
    getStorageSync: key => storage.get(key), removeStorageSync: key => storage.delete(key),
    showToast: value => toasts.push(value), showModal: value => modals.push(value),
    redirectTo: value => redirects.push(value), reLaunch: value => redirects.push(value), stopPullDownRefresh() {}
  }
  global.wx = wx
  let definition
  const filename = path.resolve(__dirname, `../pages/${name}/index.ts`)
  const context = vm.createContext({
    console, wx, Page: value => { definition = value },
    require: value => value === '../../services/pet-service'
      ? { getPetService: () => service } : require(path.resolve(path.dirname(filename), value + '.ts'))
  })
  new vm.Script(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
  }).outputText, { filename }).runInContext(context)
  const page = { ...definition, data: JSON.parse(JSON.stringify(definition.data)) }
  page.setData = patch => { Object.assign(page.data, patch); writes.push(patch) }
  return {
    page, calls, writes, toasts, redirects, modals, wx, storage,
    async show() { await page.onShow(); await flush() },
    hide() { if (page.onHide) page.onHide() },
    unload() { if (page.onUnload) page.onUnload() },
    count(method) { return calls.filter(call => call.method === method).length }
  }
}

const cases = {
  async 'profile refresh and reentry preserve unsaved preferences'() {
    const h = harness('profile')
    await h.show()
    h.page.inputName(input('未保存的名字'))
    h.page.onVolumeChange(input(25))
    h.page.onFollowChange(input(60))
    await h.page.onPullDownRefresh()
    h.hide()
    await h.show()
    assert.equal(h.page.data.name, '未保存的名字')
    assert.equal(h.page.data.volume, 25)
    assert.equal(h.page.data.followUpSeconds, 60)
    assert.equal(h.page.data.settingsDirty, true)
  },
  async 'settings response only clears its submitted snapshot'() {
    const pending = deferred()
    const h = harness('profile', { updateSettings: () => pending.promise })
    await h.show()
    h.page.inputName(input('第一次修改'))
    const saving = h.page.saveSettings()
    await h.page.saveSettings()
    assert.equal(h.count('updateSettings'), 1)
    h.page.inputName(input('保存期间的新修改'))
    h.page.onVolumeChange(input(40))
    pending.resolve({ pet: { ...pet, name: '第一次修改' } })
    await saving
    assert.equal(h.page.data.name, '保存期间的新修改')
    assert.equal(h.page.data.volume, 40)
    assert.equal(h.page.data.settingsDirty, true)
    assert.equal(h.page.data.saving, false)
  },
  async 'matched settings success clears dirty state and loading'() {
    const h = harness('profile')
    await h.show()
    h.page.inputName(input(' 已保存 '))
    await h.page.saveSettings()
    assert.equal(h.page.data.settingsDirty, false)
    assert.equal(h.page.data.saving, false)
    assert.equal(h.page.data.pet.name, '已保存')
  },
  async 'feedback response preserves newer text and blocks duplicate submit'() {
    const pending = deferred()
    const h = harness('profile', { submitFeedback: () => pending.promise })
    await h.show()
    h.page.inputFeedback(input('第一次反馈'))
    const submitting = h.page.submitFeedback()
    await h.page.submitFeedback()
    h.page.inputFeedback(input('提交期间的新反馈'))
    h.page.onFeedbackType(input(2))
    pending.resolve()
    await submitting
    assert.equal(h.count('submitFeedback'), 1)
    assert.equal(h.page.data.feedbackContent, '提交期间的新反馈')
    assert.equal(h.page.data.feedbackRemaining, 500 - '提交期间的新反馈'.length)
    assert.equal(h.page.data.feedbackSubmitting, false)
  },
  async 'unchanged feedback clears only after success'() {
    const h = harness('profile')
    await h.show()
    h.page.inputFeedback(input('完整反馈'))
    await h.page.submitFeedback()
    assert.equal(h.page.data.feedbackContent, '')
    assert.equal(h.page.data.feedbackRemaining, 500)
    assert.equal(h.page.data.feedbackSubmitting, false)
  },
  async 'visible request errors retain drafts and release loading'() {
    const h = harness('profile', {
      updateSettings: async () => { throw Error('local error') },
      submitFeedback: async () => { throw Error('local error') }
    })
    await h.show()
    h.page.inputName(input('需要重试'))
    h.page.inputFeedback(input('仍在这里'))
    await h.page.saveSettings()
    await h.page.submitFeedback()
    assert.equal(h.page.data.settingsDirty, true)
    assert.equal(h.page.data.feedbackContent, '仍在这里')
    assert.equal(h.page.data.saving, false)
    assert.equal(h.page.data.feedbackSubmitting, false)
  },
  async 'device online switch and network picker stay consistent'() {
    const h = harness('device')
    await h.show()
    h.page.onNetworkChange(input(2))
    assert.equal(h.page.data.mockOnline, false)
    h.page.onOnlineChange(input(true))
    assert.equal(h.page.data.networkIndex, 0)
    h.page.onNetworkChange(input(1))
    assert.equal(h.page.data.mockOnline, true)
    h.page.onOnlineChange(input(false))
    assert.equal(h.page.data.networkIndex, 2)
  },
  async 'device status controls cannot change a pending submission'() {
    const pending = deferred()
    const h = harness('device', { updateDeviceStatus: () => pending.promise })
    await h.show()
    const updating = h.page.updateStatus()
    await h.page.updateStatus()
    await h.page.reconnect()
    h.page.onNetworkChange(input(2))
    h.page.onBatteryChange(input(5))
    assert.equal(h.page.data.networkIndex, 0)
    assert.equal(h.page.data.batteryPercent, 86)
    assert.equal(h.count('updateDeviceStatus'), 1)
    assert.equal(h.count('reconnectDevice'), 0)
    pending.resolve()
    await updating
    assert.equal(h.page.data.updating, false)
  },
  async 'stale danger modal callback cannot navigate a hidden page'() {
    const h = harness('profile')
    await h.show()
    h.page.showDanger({ currentTarget: { dataset: { kind: 'reset' } } })
    await h.page.confirmDanger()
    assert.equal(h.modals.length, 1)
    h.hide()
    h.modals[0].success()
    assert.equal(h.redirects.length, 0)
  },
  async 'old save completion cannot unlock a new visit request'() {
    const first = deferred(), second = deferred()
    let requests = 0
    const h = harness('profile', { updateSettings: () => ++requests === 1 ? first.promise : second.promise })
    await h.show()
    h.page.inputName(input('旧访问'))
    const stale = h.page.saveSettings()
    h.hide()
    await h.show()
    h.page.inputName(input('新访问'))
    const current = h.page.saveSettings()
    assert.equal(requests, 2)
    first.resolve({ pet: { ...pet, name: '旧访问' } })
    await stale
    assert.equal(h.page.data.saving, true)
    second.resolve({ pet: { ...pet, name: '新访问' } })
    await current
    assert.equal(h.page.data.saving, false)
    assert.equal(h.page.data.pet.name, '新访问')
  }
}

for (const kind of ['logout', 'unbind', 'reset']) {
  cases[`${kind} clears transient dialog drafts even when the same account and device return`] = async () => {
    const h = harness('profile')
    await h.show()
    global.wx = h.wx
    const state = require('../pages/dialog/view-state.ts')
    const draft = state.draftFor(pet)
    state.editDraft(draft, '不应在退出或重置后恢复的草稿')
    const pending = deferred()
    const flight = state.submitDraft(draft, draft.text, 'cleanup-test', () => pending.promise)
    if (kind === 'logout') h.page.logout()
    else {
      h.page.showDanger({ currentTarget: { dataset: { kind } } })
      await h.page.confirmDanger()
    }
    h.storage.set('aiCatSession', { userId: 'preview-user' })
    const next = state.draftFor(pet)
    assert.notEqual(next, draft)
    assert.equal(next.text, '')
    pending.resolve()
    await flight
    assert.equal(next.pending, null, 'an old send must not revive the cleared draft')
    state.clearDraft()
  }
}

for (const kind of ['unbind', 'reset']) {
  cases[`late ${kind} completion cannot clear a newer account's draft`] = async () => {
    const pending = deferred()
    const method = kind === 'unbind' ? 'unbindPet' : 'resetProductData'
    const h = harness('profile', { [method]: () => pending.promise })
    await h.show()
    global.wx = h.wx
    const state = require('../pages/dialog/view-state.ts')
    state.editDraft(state.draftFor(pet), '旧账号草稿')
    h.page.showDanger({ currentTarget: { dataset: { kind } } })
    const request = h.page.confirmDanger()
    h.hide()
    h.storage.set('aiCatSession', { userId: 'different-user' })
    const next = state.draftFor(pet)
    state.editDraft(next, '新账号草稿')
    pending.resolve({ backupId: 'preview' })
    await request
    assert.equal(next.text, '新账号草稿')
    assert.equal(state.draftCurrent(next), true)
    state.clearDraft()
  }
}

for (const name of ['profile', 'device']) {
  cases[`${name} old initial response cannot overwrite a newer visit`] = async () => {
    const old = deferred()
    let requests = 0
    const h = harness(name, {
      getBoundPet: () => ++requests === 1 ? old.promise : Promise.resolve({ ...pet, name: '新访问' }),
      getDeviceDetail: async id => ({ ...pet, name: '新访问' })
    })
    const stale = h.page.onShow()
    h.hide()
    await h.show()
    const rendered = h.writes.length
    old.resolve({ ...pet, name: '旧访问' })
    await stale
    await flush()
    assert.equal(h.writes.length, rendered)
    assert.equal(h.page.data.pet.name, '新访问')
  }
  cases[`${name} stale detail response cannot overwrite a newer refresh`] = async () => {
    const old = deferred()
    let requests = 0
    const method = name === 'profile' ? 'getSettings' : 'getDeviceDetail'
    const latest = name === 'profile' ? { volume: 90, followUpSeconds: 60 } : { ...pet, batteryPercent: 90 }
    const h = harness(name, { [method]: () => ++requests === 1 ? old.promise : Promise.resolve(latest) })
    const stale = h.page.onShow()
    await flush()
    await h.page.load()
    const rendered = h.writes.length
    old.resolve(name === 'profile' ? { volume: 10, followUpSeconds: 5 } : { ...pet, batteryPercent: 10 })
    await stale
    assert.equal(h.writes.length, rendered)
    assert.equal(name === 'profile' ? h.page.data.volume : h.page.data.batteryPercent, 90)
  }
  cases[`${name} hidden load failure cannot render an error`] = async () => {
    const pending = deferred()
    const method = name === 'profile' ? 'getSettings' : 'getDeviceDetail'
    const h = harness(name, { [method]: () => pending.promise })
    const loading = h.page.onShow()
    await flush()
    h.hide()
    const rendered = h.writes.length
    pending.reject(Error('late load failure'))
    await loading
    assert.equal(h.writes.length, rendered)
    assert.equal(h.page.data.error, '')
  }
}

for (const [name, method, action] of [
  ['profile', 'updateSettings', page => { page.inputName(input('待保存')); return page.saveSettings() }],
  ['profile', 'submitFeedback', page => { page.inputFeedback(input('待反馈')); return page.submitFeedback() }],
  ['profile', 'clearMemory', page => { page.showDanger({ currentTarget: { dataset: { kind: 'memory' } } }); return page.confirmDanger() }],
  ['device', 'updateDeviceStatus', page => page.updateStatus()],
  ['device', 'reconnectDevice', page => page.reconnect()],
  ['device', 'bindPet', page => page.bindPet()]
]) {
  for (const failure of [false, true]) {
    cases[`${name}.${method} late ${failure ? 'failure' : 'success'} has no hidden writes or notices`] = async () => {
      const pending = deferred()
      const h = harness(name, { [method]: () => pending.promise })
      await h.show()
      const request = action(h.page)
      h.unload()
      const writes = h.writes.length, notices = h.toasts.length, redirects = h.redirects.length
      if (failure) pending.reject(Error('late operation failure'))
      else pending.resolve({ pet })
      await request
      await flush()
      assert.equal(h.writes.length, writes)
      assert.equal(h.toasts.length, notices)
      assert.equal(h.redirects.length, redirects)
    }
  }
}

async function main() {
  let failures = 0
  for (const [name, run] of Object.entries(cases)) {
    try { await run(); console.log(`PASS ${name}`) }
    catch (error) { failures++; console.error(`FAIL ${name}: ${error.message}`) }
  }
  console.log(`settings-device-smoke: ${Object.keys(cases).length - failures}/${Object.keys(cases).length} passed`)
  if (failures) process.exitCode = 1
}
main().catch(error => { console.error(error); process.exitCode = 1 })
