Component({
  properties: {
    title: { type: String, value: '' },
    description: { type: String, value: '' },
    value: { type: String, value: '' },
    arrow: { type: Boolean, value: true }
  },
  methods: {
    activate() { this.triggerEvent('tap', {}, { bubbles: true, composed: true }) }
  }
})
