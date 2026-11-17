const ACTIONS = [
  { actionId: 'shake-head', actionNo: 1, name: '轻轻摇头', description: '左右摇摇头，回应你的呼唤', minLevel: 1, state: 'available', icon: '/assets/icons/move.png' },
  { actionId: 'nod', actionNo: 2, name: '认真点头', description: '认真地点点头，表示它在听', minLevel: 1, state: 'available', icon: '/assets/icons/check.png' },
  { actionId: 'wag-tail', actionNo: 3, name: '开心摇尾', description: '开心时晃动小尾巴', minLevel: 1, state: 'available', icon: '/assets/icons/heart.png' },
  { actionId: 'proud-turn', actionNo: 4, name: '骄傲转身', description: '得意地转过身，等你夸它', minLevel: 2, state: 'available', icon: '/assets/icons/sprout.png' },
  { actionId: 'quiet-company', actionNo: 5, name: '安静陪伴', description: '放松下来，安静待在你身边', minLevel: 2, state: 'available', icon: '/assets/icons/heart.png' },
  { actionId: 'greeting', actionNo: 6, name: '见面问候', description: '用热情的动作欢迎你回来', minLevel: 3, state: 'locked', icon: '/assets/icons/house.png' },
  { actionId: 'level-up', actionNo: 7, name: '升级庆祝', description: '在亲密度升级时一起庆祝', minLevel: 4, state: 'locked', icon: '/assets/icons/zap.png' }
]

const ACTION_HISTORY = [
  { id: 'ar-1', name: '认真点头', status: '已完成', time: '今天 14:22' },
  { id: 'ar-2', name: '开心摇尾', status: '已完成', time: '今天 09:18' },
  { id: 'ar-3', name: '轻轻摇头', status: '已完成', time: '昨天 21:06' }
]

module.exports = { ACTIONS, ACTION_HISTORY }
