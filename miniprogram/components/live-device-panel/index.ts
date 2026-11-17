const { buildLiveDeviceView } = require('../../utils/live-device-view')

Component({
  properties: {
    state: { type: Object, value: {} },
    summary: { type: Boolean, value: false }
  },
  data: { view: buildLiveDeviceView(), optionsOpen: false, detailsOpen: false },
  observers: {
    'state, summary'(state: any, summary: boolean) {
      const view = buildLiveDeviceView(state, summary)
      const previous = this.data.view
      const changed = !previous || previous.local !== view.local || previous.signedIn !== view.signedIn || previous.selectedPetId !== view.selectedPetId
      this.setData({ view, ...(changed ? { optionsOpen: false, detailsOpen: false } : {}) })
    }
  },
  methods: {
    toggleOptions() { this.setData({ optionsOpen: !this.data.optionsOpen }) },
    toggleDetails() { this.setData({ detailsOpen: !this.data.detailsOpen }) },
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
