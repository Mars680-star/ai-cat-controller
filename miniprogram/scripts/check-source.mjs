import fs from 'node:fs'
import path from 'node:path'

const root = path.resolve(import.meta.dirname, '..')
const read = (file) => fs.readFileSync(path.join(root, file), 'utf8')
const requireText = (file, values) => {
  const source = read(file)
  for (const value of values) {
    if (!source.includes(value)) throw new Error(`${file} 缺少 ${value}`)
  }
}

const app = JSON.parse(read('app.json'))
const primaryPages = [
  'pages/device/index',
  'pages/home/index',
  'pages/motion/index',
  'pages/growth/index',
  'pages/growth-personality/index',
  'pages/dialog/index',
  'pages/mijia/index',
  'pages/profile/index'
]
for (const page of primaryPages) {
  if (!app.pages.includes(page)) throw new Error(`app.json 未注册 ${page}`)
  for (const suffix of ['.ts', '.json', '.wxml', '.wxss']) {
    const target = path.join(root, `${page}${suffix}`)
    if (!fs.existsSync(target)) throw new Error(`缺少 ${page}${suffix}`)
  }
}

requireText('components/product-nav/index.ts', [
  "id: 'connection'", "id: 'home'", "id: 'motion'", "id: 'growth'",
  "id: 'personality'", "id: 'history'", "id: 'mijia'", "id: 'settings'"
])

const serviceMethods = [
  'login', 'bindPet', 'getDashboard', 'getDeviceDetail', 'updateDeviceStatus',
  'getActions', 'executeAction', 'stopAction', 'getActionHistory', 'getIntimacy',
  'recordInteraction', 'getGrowth', 'generateGrowth', 'getDialogStatus', 'startListening',
  'interruptDialog', 'sendText', 'listConversations', 'getConversation',
  'getSettings', 'updateSettings', 'submitFeedback', 'unbindPet', 'clearMemory',
  'resetProductData', 'getMijiaStatus', 'getMijiaSpec', 'prepareMijia', 'removeMijia'
]
for (const file of ['services/pet-service.ts', 'services/mock-pet-service.ts', 'services/api-pet-service.ts']) {
  requireText(file, serviceMethods.map((method) => `${method}(`))
}

requireText('pages/device/index.ts', ['bindPet(', 'updateStatus(', 'reconnect('])
requireText('pages/motion/index.ts', ['liveExecute(', 'liveStopMotion(', 'createLiveMotionHistory('])
// A card tap is now the explicit one-shot intent. Require the in-memory
// prepare/confirm boundary, not the removed second confirmation sheet.
const motionPage = read('pages/motion/index.ts')
const motionTap = motionPage.slice(motionPage.indexOf('  liveExecute(event: any) {'), motionPage.indexOf('  liveRetryMotion()'))
for (const value of ['!this.visible', '!this.data.liveMode', 'this.data.motionSubmitting', 'this.data.motionStopping', '!item.disabled']) {
  if (!motionTap.includes(value)) throw new Error(`运动单击入口缺少保护 ${value}`)
}
if (!/const token = this\.realMotion\.prepare\(actionId\)[\s\S]*return token \? this\.realMotion\.confirm\(token\) : Promise\.resolve\(\)/.test(motionTap)) {
  throw new Error('运动单击入口必须同步 prepare 并以原 token confirm 一次')
}
if (/\bawait\b|showModal|liveConfirmMotion/.test(motionTap) || /confirm-sheet|bindtap="liveConfirmMotion"/.test(read('pages/motion/index.wxml')) || 'confirm-sheet' in JSON.parse(read('pages/motion/index.json')).usingComponents) {
  throw new Error('运动单击入口不能异步排队或恢复二次确认')
}
requireText('services/live-motion.ts', [
  'function prepare(actionId: string)', 'function confirm(token: any)',
  'token !== preparedCommand', '!current(token.epoch)', 'token.key !== key',
  'token.revision !== revision', 'return execute(token.actionId)'
])
requireText('pages/motion/index.wxml', ['bindtap="liveExecute"', 'disabled="{{item.disabled}}"', 'bindtap="liveStopMotion"'])
requireText('pages/growth/index.ts', ['addInteraction('])
requireText('pages/dialog/index.ts', ['startListening(', 'interrupt(', 'sendText('])
requireText('pages/mijia/index.ts', ['prepare(', 'remove('])
requireText('pages/profile/index.ts', ['saveSettings(', 'submitFeedback(', 'confirmDanger(', 'logout('])

const unsupportedTags = /<\/?(?:strong|small|time|output)(?:\s|>)/
for (const page of fs.readdirSync(path.join(root, 'pages'))) {
  const file = path.join(root, 'pages', page, 'index.wxml')
  if (fs.existsSync(file) && unsupportedTags.test(fs.readFileSync(file, 'utf8'))) {
    throw new Error(`${path.relative(root, file)} 使用了非 WXML 标签`)
  }
}

for (const directory of ['pages', 'components']) {
  for (const entry of fs.readdirSync(path.join(root, directory))) {
    const jsonFile = path.join(root, directory, entry, 'index.json')
    if (fs.existsSync(jsonFile)) JSON.parse(fs.readFileSync(jsonFile, 'utf8'))
  }
}

console.log('web-parity-source-check: ok')
