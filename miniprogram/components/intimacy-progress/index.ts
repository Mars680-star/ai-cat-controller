Component({ properties: { intimacy: { type: Object, value: null }, compact: { type: Boolean, value: false } }, methods: { open() { this.triggerEvent('open') } } })
