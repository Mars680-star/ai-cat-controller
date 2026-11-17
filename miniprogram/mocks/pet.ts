const PET = {
  petId: 'pet-tuanz-001',
  deviceId: 'DEMO-FOX-2026',
  serialMasked: 'DEMO-****-2026',
  name: '小安',
  avatarUrl: '/assets/ai-cat-avatar.png',
  personalityId: 'gentle-companion',
  personalityName: '温柔陪伴者',
  personalityDescription: '它会先听你说完，再用轻松、温和的方式回应。',
  languageStyle: '句子简短，少打断，重视感受与回应。',
  address: '亲爱的朋友',
  connectionState: 'online',
  networkName: 'Home-WiFi',
  lastSeenAt: '2026-10-19T14:30:00+08:00',
  batteryPercent: 82,
  batteryState: 'discharging',
  chargerOnline: false
}

const PERSONALITIES = [
  { id: 'energy', name: '元气探险家', description: '对新鲜事充满期待，会主动邀请你一起发现小乐趣。', languageStyle: '活泼、明快，偶尔带一点小幽默。', preferredActions: ['开心摇尾', '见面问候', '升级庆祝'] },
  { id: 'gentle-companion', name: '温柔陪伴者', description: '它会先听你说完，再用轻松、温和的方式回应。', languageStyle: '句子简短，少打断，重视感受与回应。', preferredActions: ['安静陪伴', '认真点头', '轻轻摇头'] },
  { id: 'proud-star', name: '傲娇小明星', description: '看起来有一点骄傲，其实很在意你的每一次呼唤。', languageStyle: '俏皮、克制，关心藏在小吐槽里。', preferredActions: ['骄傲转身', '轻轻摇头', '开心摇尾'] },
  { id: 'curious-scholar', name: '好奇小博士', description: '喜欢问为什么，也愿意和你一起把事情弄明白。', languageStyle: '清晰、有条理，会用小例子解释。', preferredActions: ['认真点头', '见面问候', '轻轻摇头'] },
  { id: 'steady-guardian', name: '沉稳守护者', description: '情绪稳定可靠，喜欢在你需要时安静地守在身边。', languageStyle: '沉稳、简洁，给人安心的力量。', preferredActions: ['安静陪伴', '认真点头', '骄傲转身'] }
]

module.exports = { PET, PERSONALITIES }
