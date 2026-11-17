function createRequestId(prefix = 'req') {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`
}

module.exports = { createRequestId }
