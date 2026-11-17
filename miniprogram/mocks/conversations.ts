const CONVERSATIONS = [
  {
    conversationId: 'conv-001', status: 'complete', startedAt: '2026-08-12T14:18:00+08:00', updatedAt: '2026-08-12T14:21:00+08:00', messageCount: 4,
    userPreview: '下午要做项目汇报，有点紧张。', assistantPreview: '先慢慢呼吸，你已经准备得很认真了。',
    messages: [
      { messageId: 'm1', role: 'user', content: '下午要做项目汇报，有点紧张。', createdAt: '14:18', deliveryState: 'sent' },
      { messageId: 'm2', role: 'assistant', content: '先慢慢呼吸，你已经准备得很认真了。', createdAt: '14:19' },
      { messageId: 'm3', role: 'user', content: '你可以给我打打气吗？', createdAt: '14:20', deliveryState: 'sent' },
      { messageId: 'm4', role: 'assistant', content: '当然可以。你对内容很熟悉，只要照着自己的节奏讲，我会一直在这里陪你。', createdAt: '14:21' }
    ]
  },
  {
    conversationId: 'conv-002', status: 'complete', startedAt: '2026-08-11T21:03:00+08:00', updatedAt: '2026-08-11T21:06:00+08:00', messageCount: 2,
    userPreview: '今天想早点休息。', assistantPreview: '好的，那我们今晚就慢一点。',
    messages: [
      { messageId: 'm5', role: 'user', content: '今天想早点休息。', createdAt: '21:03', deliveryState: 'sent' },
      { messageId: 'm6', role: 'assistant', content: '好的，那我们今晚就慢一点。', createdAt: '21:06' }
    ]
  }
]

module.exports = { CONVERSATIONS }
