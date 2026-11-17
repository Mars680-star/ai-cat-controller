const assert = require('node:assert/strict')
const fs = require('node:fs')
const path = require('node:path')
const vm = require('node:vm')
const ts = require('typescript')
const filename = path.resolve(__dirname, '../services/dev-gateway-client.ts')
const TOKEN = 'temporary-token-for-local-debug-only'
const sample = () => ({ success: true, data: {
  health: { status: 'ok', version: '0.1.0', adapter: 'local_k1' },
  device: { connected: true, deviceSerial: null, networkName: null, batteryPercent: null, batteryState: 'unavailable',
    batteryVoltage: null, chargerOnline: null, batteryPresent: null, lastSeenAt: null },
  dialog: { state: 'listening', elapsedSeconds: 0, followUpRemaining: 0, canInterrupt: false, sessionActive: false, stale: false },
  readOnly: true, sampledAt: '2026-10-19T12:00:00Z'
} })

async function main() {
  let cases = 0
  const module = { exports: {} }
  new vm.Script(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2019 }
  }).outputText, { filename }).runInNewContext({ module, exports: module.exports, console })
  const { createDevGatewayClient, gatewayErrorMessage } = module.exports
  const requests = []
  let aborted = 0
  const client = createDevGatewayClient({ request(options) { requests.push(options); return { abort() { aborted++ } } } })
  const read = client.readStatus(TOKEN)
  assert.equal(requests.length, 1)
  assert.equal(requests[0].url, 'http://127.0.0.1:8765/v1/status')
  assert.equal(requests[0].method, 'GET')
  assert.equal(requests[0].timeout, 5000)
  assert.equal(requests[0].header.Authorization, 'Bearer ' + TOKEN)
  assert.equal(requests[0].data, undefined)
  requests[0].success({ statusCode: 200, data: sample() })
  const result = await read.promise
  assert.equal(result.readOnly, true)
  assert.equal(result.device.batteryPercent, null, 'unknown readings must not become zero or demo data')
  assert.equal(result.dialog.elapsedSeconds, 0, 'real zero remains a valid reading')
  cases++
  const idleStates = ['ready', 'waiting_wake', 'interrupted', 'echo_guard', 'offline', 'unavailable']
  for (const state of [...idleStates, 'starting', 'connecting', 'connected', 'wake_detected', 'listening', 'processing_audio', 'thinking', 'answering', 'followup_preparing', 'followup_listening', 'waiting_follow_up', 'proactive_queued', 'proactive_speaking', 'proactive_finishing', 'stopping', 'recovering', 'waking', 'interrupting', 'submitting_text']) {
    const value = sample()
    value.data.device.deviceSerial = '7c2****a128'
    value.data.dialog.state = state
    value.data.dialog.elapsedSeconds = idleStates.includes(state) ? null : 5
    const request = client.readStatus(TOKEN)
    requests.at(-1).success({ statusCode: 200, data: value })
    assert.equal((await request.promise).dialog.state, state)
    cases++
  }

  for (const [response, expected] of [
    [{ statusCode: 401, data: { message: 'secret-token from server' } }, 'UNAUTHORIZED'],
    [{ statusCode: 500, data: { message: 'ssh private data' } }, 'UNREACHABLE'],
    [{ statusCode: 200, data: { success: false, message: 'token=secret' } }, 'UNVERIFIED'],
    [{ statusCode: 200, data: { ...sample(), data: { ...sample().data, readOnly: false } } }, 'UNVERIFIED'],
    [{ statusCode: 200, data: { ...sample(), data: { ...sample().data, health: { ...sample().data.health, adapter: 'mock' } } } }, 'UNVERIFIED'],
    [{ statusCode: 200, data: { success: true, data: {} } }, 'UNVERIFIED']
  ]) {
    const request = client.readStatus(TOKEN)
    requests.at(-1).success(response)
    await assert.rejects(request.promise, error => error.code === expected && !/secret|ssh|token=/.test(error.message))
    cases++
  }
  for (const [failure, code] of [[{ errMsg: 'request:fail timeout secret' }, 'TIMEOUT'], [{ errMsg: 'request:fail secret' }, 'UNREACHABLE']]) {
    const request = client.readStatus(TOKEN)
    requests.at(-1).fail(failure)
    await assert.rejects(request.promise, error => error.code === code && !error.message.includes('secret'))
    cases++
  }
  for (const edit of [
    value => { value.data.device.batteryPercent = 101 },
    value => { value.data.device.batteryPercent = 50 },
    value => { value.data.device.connected = 'true' },
    value => { value.data.device.networkName = 'private network' },
    value => { value.data.device.lastSeenAt = value.data.sampledAt },
    value => { value.data.dialog.stale = undefined },
    value => { value.data.dialog.followUpRemaining = 121 },
    value => { value.data.dialog.state = 'ready'; value.data.dialog.elapsedSeconds = 10 },
    value => { value.data.dialog.elapsedSeconds = 86401 },
    value => { value.data.device.deviceSerial = 'unmasked-serial' },
    value => { value.data.health.version = 'unsafe\nversion' },
    value => { value.data.sampledAt = 'unknown' }
  ]) {
    const value = sample()
    edit(value)
    const request = client.readStatus(TOKEN)
    requests.at(-1).success({ statusCode: 200, data: value })
    await assert.rejects(request.promise, error => error.code === 'UNVERIFIED')
    cases++
  }
  const safe = client.readStatus(TOKEN)
  const envelope = sample()
  envelope.debugSecret = 'do not forward'
  envelope.data.debugSecret = 'do not forward'
  envelope.data.device.debugSecret = 'do not forward'
  requests.at(-1).success({ statusCode: 200, data: envelope })
  assert.ok(!JSON.stringify(await safe.promise).includes('do not forward'))
  cases++
  const cancel = client.readStatus(TOKEN)
  cancel.abort()
  requests.at(-1).success({ statusCode: 200, data: sample() })
  await assert.rejects(cancel.promise, error => error.code === 'CANCELLED')
  assert.equal(aborted, 1)
  cases++
  const before = requests.length
  for (const token of ['', 'too-short', 'bad\r\nAuthorization: injected', 'x'.repeat(257), 'x'.repeat(23) + '/', 'x'.repeat(23) + '中']) {
    await assert.rejects(client.readStatus(token).promise, error => error.code === 'TOKEN_REQUIRED')
    cases++
  }
  assert.equal(requests.length, before, 'invalid tokens must never create a request')
  assert.ok(!gatewayErrorMessage({ message: 'secret', code: 'unknown' }).includes('secret'))
  cases++
  assert.equal(typeof gatewayErrorMessage({ code: 'toString' }), 'string')
  cases++
  assert.equal(cases, 56)
  console.log(`dev-gateway-client-smoke: ${cases}/${cases} cases passed (fixed GET/auth/timeout, strict schema, 25 states, sanitized errors, token validation, abort)`)
}
main().catch(error => { console.error(error); process.exitCode = 1 })
