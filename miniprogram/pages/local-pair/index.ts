const { getLiveDeviceSession } = require('../../services/live-device-session')
const { createLocalPairFlow } = require('../../utils/local-pair-flow')

Page(createLocalPairFlow(wx, getLiveDeviceSession, {
  onContinue() { wx.navigateTo({ url: '/pages/home/index' }) },
  onNetwork() { wx.navigateTo({ url: '/pages/local-network/index' }) }
}))
