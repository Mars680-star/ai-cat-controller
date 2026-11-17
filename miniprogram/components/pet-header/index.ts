Component({
  properties: { pet: { type: Object, value: null } },
  methods: {
    openPersonality() { this.triggerEvent('personality') }
  }
})
