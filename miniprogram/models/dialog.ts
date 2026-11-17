export interface ConversationSummary {
  conversationId: string
  status: 'complete' | 'waiting_assistant' | 'assistant_only'
  startedAt: string
  updatedAt: string
  messageCount: number
  userPreview: string | null
  assistantPreview: string | null
}

export interface DialogMessage {
  messageId: string
  role: 'user' | 'assistant'
  content: string
  createdAt: string
  deliveryState?: 'sending' | 'sent' | 'failed'
}
