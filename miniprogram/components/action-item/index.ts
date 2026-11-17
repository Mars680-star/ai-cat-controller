Component({
  properties: {
    action: { type: Object, value: { actionId: '', actionNo: 0, name: '', description: '', minLevel: 1, state: 'unavailable', icon: '' } },
    offline: { type: Boolean, value: false }
  },
  methods: { select() { if (!this.data.offline && this.data.action && this.data.action.state === 'available') this.triggerEvent('select', this.data.action) } }
})
