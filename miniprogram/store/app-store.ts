const state = {
  scenario: 'online_lv2',
  lastError: '',
  pendingRequestIds: {} as Record<string, string>
}

function getState() { return state }
function setScenario(scenario: string) { state.scenario = scenario }
function setPending(key: string, requestId: string) { state.pendingRequestIds[key] = requestId }
function getPending(key: string) { return state.pendingRequestIds[key] }
function clearPending(key: string) { delete state.pendingRequestIds[key] }

module.exports = { getState, setScenario, setPending, getPending, clearPending }
