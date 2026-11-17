// Packaging-boundary regression, not proof of the proprietary WeChat bundler.
// Build the static graph first; runtime require may only use registered modules.
const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')

const args = process.argv.slice(2)
assert.ok(args.length === 0 || (args.length === 2 && args[0] === '--project-dir'), 'usage: smoke-module-registration.cjs [--project-dir DIRECTORY]')
const root = args.length ? path.resolve(args[1]) : path.resolve(__dirname, '..')
const readJson = relative => JSON.parse(fs.readFileSync(path.join(root, relative), 'utf8'))
const publicConfig = readJson('project.config.json')
const privateFile = path.join(root, 'project.private.config.json')
const privateConfig = fs.existsSync(privateFile) ? readJson('project.private.config.json') : {}
const settings = { ...publicConfig.setting, ...privateConfig.setting }
const modules = new Map()
const entries = new Set(['app.js'])
const jsonVisited = new Set()

function runtimeId(parent, dependency) {
  assert.equal(typeof dependency, 'string')
  assert.ok(dependency.startsWith('.'), 'only explicit relative script dependencies are supported')
  const id = path.posix.normalize(path.posix.join(path.posix.dirname(parent), dependency)).replace(/\.(?:ts|js)$/, '') + '.js'
  assert.ok(!id.startsWith('../') && !path.posix.isAbsolute(id), 'dependency escaped the project')
  return id
}
function excluded(relative) {
  return (publicConfig.packOptions?.ignore || []).some(rule => {
    const value = String(rule.value).replace(/^\/+|\/+$/g, '')
    return rule.type === 'folder' ? relative === value || relative.startsWith(value + '/') :
      rule.type === 'file' ? relative === value :
      rule.type === 'suffix' ? relative.endsWith(value) :
      rule.type === 'prefix' ? relative.startsWith(value) :
      rule.type === 'regexp' ? new RegExp(rule.value).test(relative) : false
  })
}
function collect(id) {
  if (modules.has(id)) return
  const relative = id.replace(/\.js$/, '.ts')
  assert.ok(!excluded(relative) && !excluded(id), `required module is excluded: ${id}`)
  assert.ok(fs.existsSync(path.join(root, relative)), `missing source: ${relative}`)
  const source = fs.readFileSync(path.join(root, relative), 'utf8')
  const tree = ts.createSourceFile(relative, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TS)
  const dependencies = new Set()
  function visit(node) {
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) {
      assert.ok(ts.isStringLiteral(node.moduleSpecifier), `nonliteral import: ${relative}`)
      if (!node.isTypeOnly && !node.importClause?.isTypeOnly) dependencies.add(node.moduleSpecifier.text)
    }
    if (ts.isCallExpression(node) && ts.isIdentifier(node.expression) && node.expression.text === 'require') {
      assert.ok(node.arguments.length === 1 && ts.isStringLiteral(node.arguments[0]), `dynamic require: ${relative}`)
      dependencies.add(node.arguments[0].text)
    }
    ts.forEachChild(node, visit)
  }
  visit(tree)
  const result = ts.transpileModule(source, {
    fileName: relative, reportDiagnostics: true,
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
  })
  assert.ok(!(result.diagnostics || []).some(value => value.category === ts.DiagnosticCategory.Error), `transpile failed: ${relative}`)
  modules.set(id, { code: result.outputText, dependencies })
  for (const dependency of dependencies) collect(runtimeId(id, dependency))
}
function componentEntries(base) {
  if (jsonVisited.has(base)) return
  jsonVisited.add(base)
  entries.add(base + '.js')
  const filename = path.join(root, base + '.json')
  if (!fs.existsSync(filename)) return
  for (const value of Object.values(readJson(base + '.json').usingComponents || {})) {
    assert.equal(typeof value, 'string')
    const component = value.startsWith('/') ? value.slice(1) : path.posix.normalize(path.posix.join(path.posix.dirname(base), value))
    assert.ok(!component.startsWith('../') && !component.startsWith('plugin:'), 'unsupported component entry')
    componentEntries(component)
  }
}
for (const page of readJson('app.json').pages) componentEntries(page)
for (const entry of entries) collect(entry)

function registry(omitted, runtimeWx = {}) {
  const registered = new Map(modules)
  if (omitted) registered.delete(omitted)
  const loaded = new Map(), definitions = { App: [], Page: [], Component: [] }
  const denied = name => () => { throw Error(`unexpected startup side effect: ${name}`) }
  const wx = new Proxy({ getStorageSync: () => undefined, ...runtimeWx }, { get: (target, name) => target[name] || denied('wx.' + String(name)) })
  const context = vm.createContext({ wx,
    setTimeout: denied('setTimeout'), setInterval: denied('setInterval'),
    clearTimeout: denied('clearTimeout'), clearInterval: denied('clearInterval')
  })
  function load(id, dependency = id) {
    if (!registered.has(id)) throw Error(`module '${id}' is not defined, require args is '${dependency}'`)
    if (loaded.has(id)) return loaded.get(id).exports
    const module = { exports: {} }
    loaded.set(id, module)
    const wrapped = new vm.Script('(function(require,module,exports,App,Page,Component){' + registered.get(id).code + '\n})', { filename: id }).runInContext(context)
    wrapped(value => load(runtimeId(id, value), value), module, module.exports,
      value => definitions.App.push(value), value => definitions.Page.push(value), value => definitions.Component.push(value))
    return module.exports
  }
  return { load, definitions, loaded }
}

const cases = [
  ['inline connection and Wi-Fi form share registered factories without lifecycle side effects', () => {
    const runtime = registry()
    runtime.load('components/local-connect/index.js')
    runtime.load('components/provision-flow/index.js')
    assert.equal(runtime.definitions.Component.length, 2)
    assert.equal(runtime.definitions.Page.length, 0)
    for (const id of ['utils/local-pair-flow.js','utils/provision-flow.js','utils/local-network-view.js']) {
      assert.ok(modules.has(id), `inline helper must be registered: ${id}`)
    }
    for (const name of ['scanCard','cameraDecoded','openControls','openLocalNetwork']) {
      assert.equal(typeof runtime.definitions.Component[0].methods[name], 'function')
    }
    for (const name of ['startFlow','stopFlow','inputPassword','configure','returnToLocalNetwork']) {
      assert.equal(typeof runtime.definitions.Component[1].methods[name], 'function')
    }
    const device = fs.readFileSync(path.join(root,'pages/device/index.wxml'),'utf8')
    const network = fs.readFileSync(path.join(root,'pages/local-network/index.wxml'),'utf8')
    assert.ok(device.includes('<local-connect '))
    assert.ok(network.includes('<local-connect ') && network.includes('<provision-flow '))
    const page = fs.readFileSync(path.join(root,'pages/local-network/index.ts'),'utf8')
    assert.ok(!page.includes("url: '/pages/local-pair/index") && !page.includes("url: '/pages/provision/index"))
  }],
  ['local pairing and network entry register without starting discovery or encryption requests', () => {
    const runtime = registry()
    runtime.load('pages/local-pair/index.js')
    const page = runtime.definitions.Page[0]
    for (const name of ['onLoad', 'onShow', 'onHide', 'scanCard', 'cameraDecoded', 'discover', 'openLocalNetwork']) assert.equal(typeof page[name], 'function')
    for (const id of ['services/local-device-client.js', 'services/local-device-discovery.js', 'services/local-network.js']) assert.ok(modules.has(id), id)
    assert.ok(readJson('app.json').pages.includes('pages/local-network/index'))
    runtime.load('pages/local-network/index.js')
    assert.equal(runtime.definitions.Page.length, 2)
    assert.equal(typeof runtime.definitions.Page[1].onShow, 'function')
  }],
  ['missing registration reproduces the reported white-screen exception', () => {
    assert.throws(() => registry('utils/live-page.js').load('pages/home/index.js'), {
      message: "module 'utils/live-page.js' is not defined, require args is '../../utils/live-page'"
    })
  }],
  ['new live helpers and component are included in the static dependency closure', () => {
    for (const id of ['utils/live-page.js', 'utils/live-device-view.js', 'services/live-device-session.js',
      'services/cloud-auth-client.js', 'services/development-gateway.js', 'services/dev-gateway-client.js', 'components/live-device-panel/index.js']) {
      assert.ok(modules.has(id), `unregistered dependency: ${id}`)
    }
  }],
  ['all registered relative dependencies resolve before any entry runs', () => {
    for (const [id, module] of modules) for (const dependency of module.dependencies) assert.ok(modules.has(runtimeId(id, dependency)))
  }],
  ['app registers without invoking launch or starting network, timers or writes', () => {
    const runtime = registry(); runtime.load('app.js'); assert.equal(runtime.definitions.App.length, 1)
  }],
  ['app hide still clears the card/session if private handoff cleanup fails', () => {
    const runtime = registry(); let cleared = 0
    runtime.load('services/local-network.js').clearLocalProvisionHandoff = () => { throw Error('synthetic handoff failure') }
    runtime.load('services/live-device-session.js').getLiveDeviceSession = () => ({ clear() { cleared++ } })
    runtime.load('app.js')
    assert.throws(() => runtime.definitions.App[0].onHide(), /synthetic handoff failure/)
    assert.equal(cleared, 1)
  }],
  ['home and connection register through the same strict module registry', () => {
    const runtime = registry(); runtime.load('pages/home/index.js'); runtime.load('pages/device/index.js')
    assert.equal(runtime.definitions.Page.length, 2)
    assert.ok(runtime.loaded.has('utils/live-page.js')); assert.ok(runtime.loaded.has('services/live-device-session.js'))
  }],
  ['motion registers its guarded shared session and controller without startup side effects', () => {
    const runtime = registry(); runtime.load('pages/motion/index.js')
    assert.equal(runtime.definitions.Page.length, 1)
    assert.ok(runtime.loaded.has('services/live-device-session.js'))
    assert.ok(runtime.loaded.has('services/live-motion.js'))
  }],
  ['home cultivation and personality register their real-data lane without startup requests', () => {
    const runtime = registry()
    for (const page of ['home', 'growth', 'growth-personality']) runtime.load(`pages/${page}/index.js`)
    assert.equal(runtime.definitions.Page.length, 3)
    for (const id of ['services/live-product.js', 'services/live-device-session.js',
      'components/live-product-panel/presenter.js']) assert.ok(runtime.loaded.has(id), id)
    assert.ok(modules.has('components/live-product-panel/index.js'))
  }],
  ['real product panel registers as presentation only and cannot log in or write on load', () => {
    const runtime = registry(); runtime.load('components/live-product-panel/index.js')
    assert.equal(runtime.definitions.Component.length, 1)
    assert.ok(runtime.loaded.has('utils/live-device-view.js'))
    for (const name of ['enable', 'login', 'refresh', 'list', 'select', 'logout', 'preview']) {
      assert.equal(typeof runtime.definitions.Component[0].methods[name], 'function', name)
    }
  }],
  ['dialog and detail register their GET-only readers without starting controls', () => {
    const runtime = registry()
    runtime.load('pages/dialog/index.js'); runtime.load('pages/conversation/index.js')
    assert.equal(runtime.definitions.Page.length, 2)
    for (const id of ['services/live-dialog.js', 'services/live-device-session.js',
      'pages/dialog/live-view.js']) assert.ok(runtime.loaded.has(id), id)
    for (const page of runtime.definitions.Page) {
      assert.equal(typeof page.onShow, 'function')
      assert.equal(typeof page.onHide, 'function')
    }
  }],
  ['profile registers shared preferences and the existing panel without startup effects', () => {
    const runtime = registry()
    runtime.load('pages/profile/index.js')
    assert.equal(runtime.definitions.Page.length, 1)
    for (const id of ['services/live-preferences.js', 'services/live-device-session.js']) assert.ok(runtime.loaded.has(id), id)
    assert.ok(modules.has('components/live-product-panel/index.js'))
    const page = runtime.definitions.Page[0]
    for (const name of ['onShow', 'onHide', 'saveSettings', 'submitFeedback', 'showDanger', 'confirmDanger']) assert.equal(typeof page[name], 'function', name)
    const config = readJson('pages/profile/index.json')
    assert.equal(config.usingComponents['live-product-panel'], '/components/live-product-panel/index')
  }],
  ['one compact mode banner preserves preview, confirmation and expandable connection boundaries', () => {
    const runtime = registry(); runtime.load('components/app-page/index.js')
    assert.equal(runtime.definitions.Component[0].properties.hideModeBanner.value, false)
    const view = fs.readFileSync(path.join(root, 'components/app-page/index.wxml'), 'utf8')
    assert.ok(view.includes('!localProvisioning && (!hideModeBanner ||'))
    assert.equal((view.match(/class="preview-banner"/g) || []).length, 1)
    assert.ok(view.includes('aria-expanded="{{showConnectionDetails}}"'))
    assert.ok(view.includes('wx:if="{{showConnectionDetails}}"'))
    assert.ok(view.includes('界面预览 · 未连接设备'))
    assert.ok(view.includes('真实数据 · 培养互动需确认，动作另有开关'))
    assert.ok(view.includes("current === 'motion' ? '真实运动"))
    assert.ok(view.includes("current === 'history' ? '真实对话"))
    assert.ok(view.includes("current === 'settings' ? '真实设置"))
    assert.ok(view.includes('状态与记录只读，控制需授权确认'))
    assert.ok(view.includes('保存与反馈需授权确认，危险操作未接入'))
    const definition = runtime.definitions.Component[0]
    const events = []
    const component = { data: { ...definition.data }, setData(patch) { Object.assign(this.data, patch) }, triggerEvent(...args) { events.push(args) } }
    assert.equal(component.data.showConnectionDetails, false)
    definition.methods.toggleConnectionDetails.call(component)
    assert.equal(component.data.showConnectionDetails, true)
    definition.methods.toggleConnectionDetails.call(component)
    assert.equal(component.data.showConnectionDetails, false); assert.deepEqual(events, [])
  }],
  ['phone LAN warning is runtime gated without network or credential side effects', () => {
    for (const [envVersion, platform, expected] of [
      ['develop', 'android', true], ['trial', 'android', false], ['release', 'android', false], ['develop', 'devtools', false]
    ]) {
      const runtime = registry(undefined, {
        getAccountInfoSync: () => ({ miniProgram: { envVersion } }), getDeviceInfo: () => ({ platform })
      })
      runtime.load('components/app-page/index.js')
      const definition = runtime.definitions.Component[0]
      const component = { data: { ...definition.data }, setData(patch) { Object.assign(this.data, patch) } }
      definition.lifetimes.attached.call(component)
      assert.equal(component.data.phoneLan, expected)
      if (expected) for (const word of ['HTTP 明文', '受信任 Wi-Fi', '电脑须开机', '不是正式远程控制']) assert.ok(component.data.transportNotice.includes(word))
    }
    const runtime = registry(); runtime.load('components/app-page/index.js')
    const definition = runtime.definitions.Component[0]
    const component = { data: { ...definition.data }, setData(patch) { Object.assign(this.data, patch) } }
    assert.doesNotThrow(() => definition.lifetimes.attached.call(component))
    assert.equal(component.data.phoneLan, false)
    const view = fs.readFileSync(path.join(root, 'components/app-page/index.wxml'), 'utf8')
    assert.ok(view.includes('!localProvisioning && (!hideModeBanner || ((liveDevice || gatewaySetup) && phoneLan'))
    assert.ok(!view.includes('!hideModeBanner && (liveDevice || gatewaySetup) && phoneLan'))
    assert.ok(view.includes('{{transportNotice}}'))
    for (const message of ['HTTP 明文', '受信 Wi-Fi', '电脑须开机', '本地加密', '电脑可关闭', '不是微信身份或异地控制']) assert.ok(view.includes(message), message)
    assert.equal(definition.properties.localProvisioning.value, false)
    const motion = fs.readFileSync(path.join(root, 'pages/motion/index.wxml'), 'utf8')
    assert.ok(motion.includes('hideModeBanner="{{true}}"'))
    assert.ok(motion.includes('liveDevice="{{true}}"'))
    assert.ok(!motion.includes('localProvisioning="{{true}}"'))
    const provision = fs.readFileSync(path.join(root, 'pages/provision/index.wxml'), 'utf8')
    assert.ok(provision.includes('localProvisioning="{{true}}"'))
  }],
  ['record disclosure distinguishes unknown counts without changing its default or toggle', () => {
    const runtime = registry(); runtime.load('components/record-disclosure/index.js')
    const definition = runtime.definitions.Component[0]
    assert.equal(definition.properties.countText.value, '')
    const component = { data: { ...definition.data, count: 0, countText: '读取失败' },
      setData(patch) { Object.assign(this.data, patch) } }
    definition.methods.toggle.call(component)
    assert.equal(component.data.open, true); assert.equal(component.data.countText, '读取失败')
    definition.methods.toggle.call(component)
    assert.equal(component.data.open, false); assert.equal(component.data.count, 0)
    const view = fs.readFileSync(path.join(root, 'components/record-disclosure/index.wxml'), 'utf8')
    assert.ok(view.includes("countText || count + ' 条'"))
    assert.ok(view.includes('aria-expanded="{{open}}"'))
  }],
  ['live panel registers with its view helper without calling observers or lifecycles', () => {
    const runtime = registry(); runtime.load('components/live-device-panel/index.js')
    assert.equal(runtime.definitions.Component.length, 1); assert.ok(runtime.loaded.has('utils/live-device-view.js'))
  }],
  ['public project settings disable development file pruning and hot reload', () => {
    assert.equal(publicConfig.setting.ignoreDevUnusedFiles, false)
    assert.equal(publicConfig.setting.compileHotReLoad, false)
  }],
  ['effective public/private project settings keep those compatibility settings', () => {
    assert.equal(settings.ignoreDevUnusedFiles, false)
    assert.equal(settings.compileHotReLoad, false)
  }]
]
let failed = 0
for (const [name, run] of cases) {
  try { run(); console.log('PASS ' + name) }
  catch (error) { failed++; console.error('FAIL ' + name + ': ' + error.message) }
}
console.log(`module-registration-smoke: ${cases.length - failed}/${cases.length} passed (${modules.size} registered modules)`)
if (failed) process.exitCode = 1
