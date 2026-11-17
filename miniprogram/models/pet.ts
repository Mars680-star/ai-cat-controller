export type ConnectionState = 'online' | 'weak' | 'offline'
export type BatteryState = 'charging' | 'discharging' | 'full' | 'not_charging' | 'unknown' | 'unavailable'

export interface PersonalityOption {
  id: string
  name: string
  description: string
  languageStyle: string
  preferredActions: string[]
}

export interface PetSummary {
  petId: string
  deviceId: string
  serialMasked: string
  name: string
  avatarUrl: string
  personalityId: string
  personalityName: string
  personalityDescription: string
  languageStyle: string
  address: string
  connectionState: ConnectionState
  networkName: string
  lastSeenAt: string | null
  batteryPercent: number | null
  batteryState: BatteryState
  chargerOnline: boolean | null
}
