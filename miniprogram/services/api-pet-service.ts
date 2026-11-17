// 第二阶段在这里把 HTTPS 云端响应映射为 PetService 的页面模型。
// 小程序页面不得解析后端响应包裹，也不得保存设备、火山或米家平台密钥。
class ApiPetService {
  private unavailable(): never { throw new Error('云端服务尚未接入') }
  login() { return this.unavailable() }
  getBoundPet() { return this.unavailable() }
  bindPet() { return this.unavailable() }
  getPersonalities() { return this.unavailable() }
  updatePersonality() { return this.unavailable() }
  getDashboard() { return this.unavailable() }
  getDeviceDetail() { return this.unavailable() }
  updateDeviceStatus() { return this.unavailable() }
  reconnectDevice() { return this.unavailable() }
  getActions() { return this.unavailable() }
  executeAction() { return this.unavailable() }
  finishAction() { return this.unavailable() }
  stopAction() { return this.unavailable() }
  getActionHistory() { return this.unavailable() }
  getIntimacy() { return this.unavailable() }
  recordInteraction() { return this.unavailable() }
  completeTask() { return this.unavailable() }
  getGrowth() { return this.unavailable() }
  generateGrowth() { return this.unavailable() }
  getDialogStatus() { return this.unavailable() }
  startListening() { return this.unavailable() }
  interruptDialog() { return this.unavailable() }
  sendText() { return this.unavailable() }
  listConversations() { return this.unavailable() }
  getConversation() { return this.unavailable() }
  getSettings() { return this.unavailable() }
  updatePet() { return this.unavailable() }
  updateVolume() { return this.unavailable() }
  updateFollowUpSeconds() { return this.unavailable() }
  updateSettings() { return this.unavailable() }
  submitFeedback() { return this.unavailable() }
  unbindPet() { return this.unavailable() }
  clearMemory() { return this.unavailable() }
  resetProductData() { return this.unavailable() }
  getMijiaStatus() { return this.unavailable() }
  getMijiaSpec() { return this.unavailable() }
  prepareMijia() { return this.unavailable() }
  removeMijia() { return this.unavailable() }
}

module.exports = { ApiPetService }
