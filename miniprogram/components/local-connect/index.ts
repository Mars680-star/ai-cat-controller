const { getLiveDeviceSession } = require('../../services/live-device-session')
const { createLocalPairFlow } = require('../../utils/local-pair-flow')

const flow = createLocalPairFlow(wx, getLiveDeviceSession, {
  onPaired(host: any, detail: any) { host.triggerEvent('paired', detail) },
  onState(host: any, detail: any) { host.triggerEvent('statechange', detail) },
  onContinue(host: any) { host.triggerEvent('continue') },
  onNetwork(host: any) { host.triggerEvent('network') },
  onComputer(host: any) { host.triggerEvent('computer') }
})
const { data, onLoad, onShow, onHide, onUnload, ...methods } = flow

Component({
  properties: { addressHint: { type: String, value: '' }, compact: { type: Boolean, value: false } },
  data,
  observers: {
    addressHint(value: string) { if (typeof value === 'string') this.prefillAddress(value) }
  },
  lifetimes: {
    attached() { this.localAttached = true; onLoad.call(this, { address: this.data.addressHint }); onShow.call(this) },
    detached() { this.localAttached = false; onUnload.call(this) }
  },
  pageLifetimes: {
    show() { if (this.localAttached) { onLoad.call(this, { address: this.data.addressHint }); onShow.call(this) } },
    hide() { onHide.call(this) }
  },
  methods
})
