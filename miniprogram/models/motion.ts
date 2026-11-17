export type ActionState = 'available' | 'locked' | 'unavailable' | 'running'
export interface PetAction {
  actionId: string
  actionNo: number
  name: string
  description: string
  minLevel: number
  state: ActionState
  unavailableReason?: string
}
