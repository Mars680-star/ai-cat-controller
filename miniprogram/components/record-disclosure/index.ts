Component({ properties: { title: String, count: Number, countText: { type: String, value: '' } }, data: { open: false }, methods: { toggle() { this.setData({ open: !this.data.open }) } } })
