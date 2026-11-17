const INTIMACY = {
  points: 286,
  level: 2,
  levelName: '熟悉伙伴',
  badge: 'Lv.2',
  levelStart: 200,
  nextLevelPoints: 400,
  progressPercent: 43,
  dailyGrowth: 18,
  dailyCap: 30,
  currentUnlocks: ['安静陪伴', '骄傲转身', '记住重要日子'],
  nextUnlocks: ['见面问候', '更丰富的称呼']
}

const GROWTH = {
  intro: '陪伴性格可以自主选择，长期互动还会丰富表达与行为倾向。',
  tags: ['喜欢倾听', '对新事物好奇', '情绪稳定', '记得约定'],
  attributes: [
    { id: 'curiosity', name: '好奇', value: 72, band: '明显' },
    { id: 'empathy', name: '共情', value: 84, band: '突出' },
    { id: 'knowledge', name: '求知', value: 63, band: '成长中' },
    { id: 'energy', name: '活力', value: 58, band: '成长中' },
    { id: 'mischief', name: '淘气', value: 36, band: '初显' },
    { id: 'discipline', name: '自律', value: 77, band: '明显' }
  ],
  portraits: ['聊天时会先确认你的感受', '更喜欢温和的问候动作', '会在重要日子前主动提醒'],
  recent: [
    { id: 'g1', reason: '完成了一次有效对话', time: '今天 14:20', change: '共情 +2' },
    { id: 'g2', reason: '记住了新的小约定', time: '昨天 21:05', change: '自律 +1' }
  ]
}

module.exports = { INTIMACY, GROWTH }
