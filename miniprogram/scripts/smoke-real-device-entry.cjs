const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const root = path.resolve(__dirname, '..')
const route = '/pages/device/index'

const transpile = file => ts.transpileModule(fs.readFileSync(file, 'utf8'), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
}).outputText

function harness() {
  const calls = [], toasts = []
  const wx = { navigateTo: value => calls.push({ kind: 'navigate', ...value }), redirectTo: value => calls.push({ kind: 'redirect', ...value }), showToast: value => toasts.push(value) }
  const module = { exports: {} }
  vm.runInNewContext(transpile(path.join(root, 'utils/navigation.ts')), { module, exports: module.exports, wx })
  return { nav: module.exports, wx, calls, toasts }
}

function main() {
  const h = harness()
  assert.equal(h.nav.navigateOnce(route), true)
  assert.equal(h.nav.navigateOnce(route), false)
  assert.equal(h.nav.redirectOnce('/pages/device/index'), false)
  assert.equal(h.calls.length, 1)
  assert.equal(h.calls[0].kind, 'navigate', 'real entry preserves native back navigation')
  h.calls[0].complete()
  assert.equal(h.nav.navigateOnce(route), true)
  const failed = h.calls[1]
  failed.fail()
  assert.equal(h.toasts.length, 1)
  assert.equal(h.nav.navigateOnce(route), true)
  failed.complete()
  assert.equal(h.nav.navigateOnce(route), false, 'late callback cannot unlock the replacement')
  h.calls[2].complete()
  h.wx.navigateTo = () => { throw Error('native unavailable') }
  assert.equal(h.nav.navigateOnce(route), true)
  h.wx.navigateTo = value => h.calls.push(value)
  assert.equal(h.nav.navigateOnce(route), true)

  const homeFile = path.join(root, 'pages/home/index.ts')
  let page
  const source = transpile(homeFile)
  vm.runInNewContext(source, {
    Page: value => { page = value },
    require(id) {
      if (id.endsWith('/pet-service')) return { getPetService: () => ({}) }
      if (id.endsWith('/format')) return { batteryText: () => '' }
      if (id.endsWith('/navigation')) return { redirectOnce() { assert.fail('must use native back navigation') }, navigateOnce: url => assert.equal(url, route) }
      if (id.endsWith('/live-page')) return { livePageData: {}, livePageMethods: {} }
      // This test exercises only the standalone navigation entry, not onShow.
      // Keep new real-data dependencies explicit and fail if navigation starts
      // a reader, request ID or presentation lifecycle as a side effect.
      const forbidden = () => assert.fail('navigation must not start real product data')
      if (id.endsWith('/live-product')) return { createLiveProduct: forbidden }
      if (id.endsWith('/request-id')) return { createRequestId: forbidden }
      if (id.endsWith('/live-device-view')) return { buildLiveDeviceView: forbidden }
      if (id.endsWith('/live-product-panel/presenter')) return {
        attachProductPage: forbidden, detachProductPage: forbidden,
        intimacyView: forbidden, timeLabel: forbidden, setChangedData: forbidden
      }
      assert.fail('unexpected dependency')
    }
  })
  page.pageVisible = true
  page.openRealDevice()
  const view = fs.readFileSync(path.join(root, 'pages/home/index.wxml'), 'utf8')
  assert.ok(view.includes('bindtap="openRealDevice"'))
  assert.ok(view.indexOf('bindtap="openRealDevice"') < view.indexOf('home-overview'), 'entry must be visible above the demo pet card')
  assert.ok(view.includes('演示数据'))
  console.log('real-device-entry-smoke: 6/6 passed (visible shortcut, native back, single transition, retry, stale callbacks, Mock boundary)')
}
try { main() } catch (error) { console.error('real-device-entry-smoke failed:', error.message); process.exitCode = 1 }
