Component({
  properties: {
    title: { type: String, value: '' },
    description: { type: String, value: '' },
    value: { type: Number, value: 0 },
    suffix: { type: String, value: '' },
    min: { type: Number, value: 0 },
    max: { type: Number, value: 100 },
    step: { type: Number, value: 1 }
  },
  data: { percent: 0, draggingValue: 0 },
  observers: {
    'value,min,max': function(value: number, min: number, max: number) {
      const range = Math.max(1, max - min)
      this.setData({ percent: Math.max(0, Math.min(100, ((value - min) / range) * 100)), draggingValue: value })
    }
  },
  methods: {
    updateFromTouch(event: any, final: boolean) {
      const touch = (event.touches && event.touches[0]) || (event.changedTouches && event.changedTouches[0])
      if (!touch) return
      this.createSelectorQuery().select('.slider-control').boundingClientRect((rect: any) => {
        if (!rect || !rect.width) return
        const ratio = Math.max(0, Math.min(1, (touch.clientX - rect.left) / rect.width))
        const raw = this.data.min + ratio * (this.data.max - this.data.min)
        const value = Math.max(this.data.min, Math.min(this.data.max, Math.round(raw / this.data.step) * this.data.step))
        const percent = ((value - this.data.min) / Math.max(1, this.data.max - this.data.min)) * 100
        this.setData({ percent, draggingValue: value })
        this.triggerEvent(final ? 'change' : 'changing', { value })
      }).exec()
    },
    startDrag(event: any) { this.updateFromTouch(event, false) },
    drag(event: any) { this.updateFromTouch(event, false) },
    endDrag(event: any) { this.updateFromTouch(event, true) }
  }
})
