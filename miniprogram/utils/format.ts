function formatDateTime(value: string | null) {
  if (!value) return '暂未获取'
  const date = new Date(value)
  const pad = (number: number) => String(number).padStart(2, '0')
  return `${date.getMonth() + 1}月${date.getDate()}日 ${pad(date.getHours())}:${pad(date.getMinutes())}`
}

function batteryText(percent: number | null) {
  return percent === null || percent === undefined ? '暂未获取' : `${percent}%`
}

module.exports = { formatDateTime, batteryText }
