const SCENARIOS: Record<string, any> = {
  logged_out: { entry: 'logged_out' },
  logged_in_unbound: { entry: 'unbound' },
  first_bind: { entry: 'first_bind' },
  online_lv2: { entry: 'home' },
  weak_charging: {
    entry: 'home',
    pet: { connectionState: 'weak', batteryPercent: 48, batteryState: 'charging', chargerOnline: true }
  },
  offline_no_battery: {
    entry: 'home',
    pet: { connectionState: 'offline', batteryPercent: null, batteryState: 'unavailable', chargerOnline: null, lastSeenAt: '2026-08-12T11:20:00+08:00' }
  },
  action_running: { entry: 'home', runningActionId: 'nod' },
  near_upgrade: {
    entry: 'home',
    intimacy: { points: 395, progressPercent: 98, dailyGrowth: 25 }
  },
  upgraded: {
    entry: 'home',
    intimacy: { points: 405, level: 3, levelName: '默契好友', badge: 'Lv.3', levelStart: 400, nextLevelPoints: 700, progressPercent: 2 },
    taskUpgraded: true
  },
  no_growth_tags: { entry: 'home', growth: { tags: [] } },
  rich_growth_tags: { entry: 'home' },
  empty_conversations: { entry: 'home', conversations: [] },
  waiting_answer: { entry: 'home', dialogState: 'thinking' },
  send_failed: { entry: 'home', failSendText: true }
}

module.exports = { SCENARIOS }
