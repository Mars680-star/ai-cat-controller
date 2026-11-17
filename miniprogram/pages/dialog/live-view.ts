// Read-only presentation. Times are source UTC; no inferred stage timer or totals.
const utcLabel = (value: any) => typeof value === 'string' && value ? value.replace('T', ' ').replace(/Z$/, ' UTC') : '未提供'
const statusText = (status: string) => status === 'complete' ? '已保存问答' : status === 'waiting_assistant' ? '待回答记录' : '仅回答记录'
const sourceText = (source: string) => source === 'mock' ? '设备库中的 Mock 来源记录' : '设备来源记录'
function summaryView(value: any) {
  return { ...value, userPreview: value.preview, startedLabel: utcLabel(value.startedAt),
    updatedLabel: utcLabel(value.updatedAt), statusText: statusText(value.status), sourceText: sourceText(value.source) }
}
function detailView(value: any) {
  return { ...summaryView(value.conversation), durationText: value.conversation.durationSeconds + ' 秒',
    messages: value.messages.map((item: any) => ({ ...item, createdLabel: utcLabel(item.createdAt) })),
    truncated: value.truncated, limit: value.limit, windowMessageCount: value.windowMessageCount, sampledLabel: utcLabel(value.sampledAt), scope: value.scope }
}
module.exports = { utcLabel, summaryView, detailView }
