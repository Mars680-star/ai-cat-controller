const { MockPetService } = require('./mock-pet-service')

export interface PetService {
  login(input?: any): Promise<any>
  getBoundPet(): Promise<any | null>
  bindPet(input: any): Promise<any>
  getPersonalities(): Promise<any[]>
  updatePersonality(petId: string, personalityId: string): Promise<any>
  getDashboard(petId: string): Promise<any>
  getDeviceDetail(petId: string): Promise<any>
  updateDeviceStatus(petId: string, input: any, requestId: string): Promise<any>
  reconnectDevice(petId: string): Promise<void>
  getActions(petId: string): Promise<any[]>
  executeAction(petId: string, actionId: string, requestId: string): Promise<any>
  finishAction(executionId?: string): Promise<void>
  stopAction(petId: string, requestId: string, executionId?: string): Promise<any>
  getActionHistory(petId: string): Promise<any[]>
  getIntimacy(petId: string): Promise<any>
  recordInteraction(petId: string, eventType: string, requestId: string): Promise<any>
  completeTask(petId: string, requestId: string): Promise<any>
  getGrowth(petId: string): Promise<any>
  generateGrowth(petId: string, eventType: string, count: number, requestId: string): Promise<any>
  getDialogStatus(petId: string): Promise<any>
  startListening(petId: string, requestId: string): Promise<void>
  interruptDialog(petId: string, requestId: string): Promise<void>
  sendText(petId: string, content: string, requestId: string): Promise<void>
  listConversations(petId: string, cursor?: string): Promise<any>
  getConversation(petId: string, conversationId: string): Promise<any>
  getSettings(): Promise<any>
  updatePet(petId: string, input: any): Promise<any>
  updateVolume(value: number): Promise<void>
  updateFollowUpSeconds(seconds: number): Promise<void>
  updateSettings(petId: string, input: any, requestId: string): Promise<any>
  submitFeedback(petId: string, input: any): Promise<void>
  unbindPet(petId: string): Promise<void>
  clearMemory(petId: string, requestId: string): Promise<any>
  resetProductData(confirmation: string): Promise<any>
  getMijiaStatus(petId: string): Promise<any>
  getMijiaSpec(petId: string): Promise<any>
  prepareMijia(petId: string, requestId: string): Promise<any>
  removeMijia(petId: string, requestId: string): Promise<any>
}

const service: PetService = new MockPetService()
function getPetService(): PetService { return service }

module.exports = { getPetService }
