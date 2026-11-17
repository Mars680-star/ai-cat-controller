// Inline presentation of the SAME private controller used by the operator page.
const { createProvisionFlowDefinition } = require('../../utils/provision-flow')
const flow = createProvisionFlowDefinition({
  requireHandoff: true,
  onState(this: any, detail: any) { if (this.attachedFlow) this.triggerEvent('state', detail) },
  onTerminal(this: any, detail: any) { if (this.attachedFlow) this.triggerEvent('terminal', detail) },
  onError(this: any, detail: any) { if (this.attachedFlow) this.triggerEvent('error', detail) },
  onBack(this: any) {
    this.stopFlow(true)
    if (this.attachedFlow) this.triggerEvent('back', {})
  }
})
const { data, onShow, onHide, onUnload, ...methods } = flow
Component({
  properties: {
    revision: { type: Number, value: 0, observer(value: number, before: number) {
      if (this.attachedFlow && this.flowPageVisible && value !== before) this.startFlow()
    } }
  },
  data: { ...data, diagnosticsOpen: false },
  lifetimes: {
    attached() { this.attachedFlow = true; this.flowPageVisible = true; this.startFlow() },
    detached() { this.attachedFlow = false; this.flowPageVisible = false; this.stopFlow(true) }
  },
  pageLifetimes: {
    hide() { this.flowPageVisible = false; this.returningFromBackground = true; this.stopFlow(false) },
    show() {
      this.flowPageVisible = true
      // Backgrounding deliberately clears private keys. Never auto-consume a
      // second invitation or restart the old form on return from system Wi-Fi.
      if (this.attachedFlow && this.returningFromBackground) {
        this.triggerEvent('error', { message: '已离开微信，请在当前步骤重新配对设备后继续。' })
      }
    }
  },
  methods: {
    ...methods,
    startFlow() {
      if (!this.attachedFlow || !this.flowPageVisible) return
      if (this.flowActive && this.flowStartedRevision === this.data.revision) return
      if (this.flowActive && (this.data.busy || this.data.confirming)) {
        this.triggerEvent('error', { message: '当前请求尚未结束，请保留原编号核对。' }); return
      }
      if (this.flowActive) this.stopFlow(true)
      this.flowActive = true; this.returningFromBackground = false
      this.flowStartedRevision = this.data.revision
      const version = this.flowStartVersion = (this.flowStartVersion || 0) + 1
      Promise.resolve().then(() => {
        if (this.attachedFlow && this.flowPageVisible && this.flowActive && version === this.flowStartVersion) return onShow.call(this)
      }).catch(() => {
        if (this.attachedFlow && this.flowActive && version === this.flowStartVersion) {
          this.triggerEvent('error', { message: '安全配网页未能准备好，请保留原编号并返回核对。' })
        }
      })
    },
    stopFlow(disposed: boolean) {
      this.flowActive = false
      this.flowStartVersion = (this.flowStartVersion || 0) + 1
      if (disposed) onUnload.call(this)
      else onHide.call(this)
    },
    toggleDiagnostics() { if (!this.data.busy && !this.data.confirming) this.setData({ diagnosticsOpen: !this.data.diagnosticsOpen }) }
  }
})
