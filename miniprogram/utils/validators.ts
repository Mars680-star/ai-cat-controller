function lengthBetween(value: string, min: number, max: number) {
  const length = String(value || '').trim().length
  return length >= min && length <= max
}

module.exports = { lengthBetween }
