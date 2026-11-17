const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const ts = require('typescript')

require.extensions['.ts'] = (module, filename) => {
  const source = fs.readFileSync(filename, 'utf8')
  const result = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
  })
  module._compile(result.outputText, filename)
}

let definition
let bound = true
const redirects = []
const notices = []
let pendingNavigation
global.Component = (value) => { definition = value }
global.wx = {
  getStorageSync() { return bound ? { petId: 'preview' } : null },
  redirectTo(value) { redirects.push(value.url); pendingNavigation = value },
  showToast(value) { notices.push(value.title) }
}
require('../components/product-nav/index.ts')
const app = JSON.parse(fs.readFileSync(path.join(__dirname, '../app.json'), 'utf8'))
const items = definition.data.items
assert.equal(items.length, 8)
assert.equal(new Set(items.map((item) => item.id)).size, 8)
const context = { data: { current: '' } }
for (const item of items) {
  assert.ok(app.pages.includes(item.path.replace(/^\//, '')))
  definition.methods.navigate.call(context, { currentTarget: { dataset: item } })
  pendingNavigation.complete()
}
assert.deepEqual(redirects, items.map((item) => item.path))

redirects.length = 0
context.data.current = 'settings'
definition.methods.navigate.call(context, { currentTarget: { dataset: items[7] } })
assert.equal(redirects.length, 0, 'selected item must not redirect itself')

bound = false
context.data.current = ''
const liveEntryIds = new Set(['connection', 'home', 'motion', 'growth', 'personality', 'history', 'settings'])
for (const item of items) {
  definition.methods.navigate.call(context, { currentTarget: { dataset: item } })
  if (liveEntryIds.has(item.id)) pendingNavigation.complete()
}
assert.deepEqual(redirects, items.filter(item => liveEntryIds.has(item.id)).map(item => item.path), 'real-device entries cannot depend on a demo binding')
assert.equal(notices.length, 1, 'only preview-only entries still require a preview binding')

bound = true
redirects.length = 0
definition.methods.navigate.call(context, { currentTarget: { dataset: items[1] } })
definition.methods.navigate.call(context, { currentTarget: { dataset: items[2] } })
assert.deepEqual(redirects, [items[1].path], 'rapid taps must share one navigation transition')
const failedNavigation = pendingNavigation
failedNavigation.fail()
definition.methods.navigate.call(context, { currentTarget: { dataset: items[2] } })
assert.deepEqual(redirects, [items[1].path, items[2].path], 'failed navigation must allow retry')
failedNavigation.complete()
definition.methods.navigate.call(context, { currentTarget: { dataset: items[3] } })
assert.equal(redirects.length, 2, 'late completion from the failed transition must not unlock its replacement')
pendingNavigation.complete()
definition.methods.navigate.call(context, { currentTarget: { dataset: { id: 'home', path: '/pages/not-registered/index' } } })
assert.equal(redirects.length, 2, 'unknown route data must not navigate')
console.log('navigation-smoke: 8 routes; selected/unbound, rapid-tap and failure recovery guards ok')
