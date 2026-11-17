const { developmentGatewayInfo } = require('../../services/development-gateway')

Component({
  data: { phoneLan: false, transportNotice: '', showConnectionDetails: false },
  properties: {
    loading: Boolean,
    error: String,
    noTab: Boolean,
    readOnlyDebug: { type: Boolean, value: false },
    gatewaySetup: { type: Boolean, value: false },
    liveDevice: { type: Boolean, value: false },
    localProvisioning: { type: Boolean, value: false },
    authKind: { type: String, value: 'wechat' },
    hideModeBanner: { type: Boolean, value: false },
    current: { type: String, value: '' },
    subtitle: { type: String, value: '陪伴你的智能宠物' },
    statusText: { type: String, value: '设备在线' },
    online: { type: Boolean, value: true }
  },
  lifetimes: {
    attached() {
      const connection = developmentGatewayInfo(wx)
      this.setData({ phoneLan: connection.phoneLan, transportNotice: connection.notice })
    }
  },
  methods: {
    toggleConnectionDetails() { this.setData({ showConnectionDetails: !this.data.showConnectionDetails }) },
    retry() { this.triggerEvent('retry') }
  }
})
