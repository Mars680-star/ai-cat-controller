export interface GrowthAttribute {
  id: 'curiosity' | 'empathy' | 'knowledge' | 'energy' | 'mischief' | 'discipline'
  name: string
  value: number
  band: '普通' | '初显' | '成长中' | '明显' | '突出'
}
