const assert = require('node:assert/strict')
const ts = require('typescript')

require.extensions['.ts'] = (module, filename) => {
  const source = require('node:fs').readFileSync(filename, 'utf8')
  const output = ts.transpileModule(source, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
  }).outputText
  module._compile(output, filename)
}

const storage = new Map()
global.wx = {
  getStorageSync(key) { return storage.get(key) },
  setStorageSync(key, value) { storage.set(key, value) },
  removeStorageSync(key) { storage.delete(key) }
}

async function main() {
  const { getPetService } = require('../services/pet-service.ts')
  const service = getPetService()

  await service.login({ loginCode: 'smoke-user', nickname: '测试用户' })
  assert.equal((await service.getBoundPet()), null)

  const bound = await service.bindPet({ deviceSerial: 'K1-SMOKE-0001', petName: '小安', networkName: '测试 Wi-Fi' })
  assert.equal(bound.pet.name, '小安')
  const petId = bound.pet.petId

  const dashboard = await service.getDashboard(petId)
  assert.equal(dashboard.pet.deviceId, 'K1-SMOKE-0001')
  await service.updateDeviceStatus(petId, { online: true, connectionState: 'weak', batteryPercent: 55, charging: true }, 'device-smoke-1')
  assert.equal((await service.getDeviceDetail(petId)).batteryPercent, 55)

  await service.executeAction(petId, 'nod', 'action-smoke-1')
  await service.finishAction()
  assert.equal((await service.getActionHistory(petId))[0].status, '已完成')

  const interaction = await service.recordInteraction(petId, 'touch', 'interaction-smoke-1')
  assert.equal(interaction.pointsAdded, 1)
  const generated = await service.generateGrowth(petId, 'question', 3, 'growth-smoke-1')
  assert.equal(generated.processed, 3)

  await service.sendText(petId, '今天怎么样？', 'text-smoke-1')
  assert.equal((await service.listConversations(petId)).items[0].userPreview, '今天怎么样？')

  const prepared = await service.prepareMijia(petId, 'mijia-smoke-1')
  assert.equal(prepared.status.status, 'prepared')
  assert.equal((await service.getMijiaSpec(petId)).properties.length, 8)
  await service.removeMijia(petId, 'mijia-smoke-2')

  const settings = await service.updateSettings(petId, { name: '安安', volume: 48, followUpSeconds: 45 }, 'settings-smoke-1')
  assert.equal(settings.pet.name, '安安')
  assert.equal((await service.getSettings()).followUpSeconds, 45)

  const memory = await service.clearMemory(petId, 'memory-smoke-1')
  assert.equal(memory.conversationsPreserved > 0, true)
  await service.unbindPet(petId)
  assert.equal((await service.getBoundPet()), null)

  const reset = await service.resetProductData('RESET_PRODUCT_DATA')
  assert.match(reset.backupId, /^mini-backup-/)
  console.log('mock-service-smoke: ok')
}

main().catch((error) => {
  console.error(error)
  process.exitCode = 1
})
