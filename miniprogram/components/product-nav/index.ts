const ITEMS = [
  { id: 'connection', label: '连接', path: '/pages/device/index', icon: '/assets/icons/wifi.png' },
  { id: 'home', label: '主页', path: '/pages/home/index', icon: '/assets/icons/house.png' },
  { id: 'motion', label: '运动', path: '/pages/motion/index', icon: '/assets/icons/move.png' },
  { id: 'growth', label: '培养', path: '/pages/growth/index', icon: '/assets/icons/sprout.png' },
  { id: 'personality', label: '性格', path: '/pages/growth-personality/index', icon: '/assets/icons/heart.png' },
  { id: 'history', label: '对话', path: '/pages/dialog/index', icon: '/assets/icons/message-circle.png' },
  { id: 'mijia', label: '米家', path: '/pages/mijia/index', icon: '/assets/icons/zap.png' },
  { id: 'settings', label: '设置', path: '/pages/profile/index', icon: '/assets/icons/user-round.png' }
]

// These pages have their own explicit real-device authentication/selection UI.
// A preview binding is not proof of ownership and must not gate real entry.
const LIVE_ENTRY_IDS = new Set(['connection', 'home', 'motion', 'growth', 'personality', 'history', 'settings'])

Component({
  properties: { current: { type: String, value: '' } },
  data: { items: ITEMS },
  methods: {
    navigate(event: any) {
      const id = event.currentTarget.dataset.id
      const path = event.currentTarget.dataset.path
      if (!ITEMS.some((item) => item.id === id && item.path === path) || id === this.data.current) return
      if (!wx.getStorageSync('aiCatPet') && !LIVE_ENTRY_IDS.has(id)) {
        wx.showToast({ title: '请先绑定宠物设备', icon: 'none' })
        return
      }
      redirectOnce(path)
    }
  }
})
const { redirectOnce } = require('../../utils/navigation')

