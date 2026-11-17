const { buildLiveDeviceView } = require('../../utils/live-device-view')

// Presentation only: identity and requests remain in the one shared session.
Component({
  properties: {
    state: { type: Object, value: {} },
    busy: { type: Boolean, value: false },
    error: { type: String, value: '' },
    caption: { type: String, value: '真实数据 · 互动和保存仍需确认。' },
    previewLabel: { type: String, value: '切回界面预览' },
    selectionLocked: { type: Boolean, value: false }
  },
  data: { view: buildLiveDeviceView(), optionsOpen: false },
  observers: {
    state(state: any) {
      const view = buildLiveDeviceView(state, true)
      const previous = this.data.view
      const changed = !previous || previous.local !== view.local || previous.signedIn !== view.signedIn || previous.selectedPetId !== view.selectedPetId
      this.setData({ view, ...(changed ? { optionsOpen: false } : {}) })
    }
  },
  methods: {
    toggleOptions() { this.setData({ optionsOpen: !this.data.optionsOpen }) },
    enable() { this.triggerEvent('enable') },
    login() { this.triggerEvent('login') },
    localPair() { this.triggerEvent('login') },
    refresh() { this.triggerEvent('refresh') },
    list() { this.triggerEvent('list') },
    logout() { this.triggerEvent('logout') },
    preview() { this.triggerEvent('preview') },
    select(event: any) {
      const petId = event && event.currentTarget && event.currentTarget.dataset && event.currentTarget.dataset.petId
      if (typeof petId === 'string' && /^[A-Za-z0-9_-]{1,128}$/.test(petId)) this.triggerEvent('select', { petId })
    }
  }
})
