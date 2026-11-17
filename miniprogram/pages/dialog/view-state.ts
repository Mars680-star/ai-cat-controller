// Private, in-memory UI state: no new storage keys or device/API calls.
let cachedDraft: any = null

function beginView(page: any) {
  if (page.viewDisposed) return null
  page.pageVisible = true
  page.viewEpoch = (page.viewEpoch || 0) + 1
  return page.viewEpoch
}

function endView(page: any, disposed = false) {
  page.pageVisible = false
  page.viewEpoch = (page.viewEpoch || 0) + 1
  if (disposed) page.viewDisposed = true
}

function viewActive(page: any, epoch: number) {
  return page.pageVisible === true && !page.viewDisposed && page.viewEpoch === epoch
}

function patchView(page: any, patch: any, epoch = page.viewEpoch) {
  if (!viewActive(page, epoch)) return
  const changed: any = {}
  for (const key of Object.keys(patch)) {
    const previous = page.data[key], next = patch[key]
    if (previous === next) continue
    if (previous && next && typeof previous === 'object' && typeof next === 'object' && JSON.stringify(previous) === JSON.stringify(next)) continue
    changed[key] = next
  }
  if (Object.keys(changed).length) page.setData(changed)
}

function clearDraft(expected: any = cachedDraft) {
  // An old account operation must not clear a newer account's draft.
  if (expected === cachedDraft) cachedDraft = null
}

function draftFor(pet: any) {
  const session = wx.getStorageSync('aiCatSession')
  if (!session || !session.userId || !pet) { clearDraft(); return null }
  const scope = JSON.stringify([session.userId, pet.petId, pet.deviceId || ''])
  if (!cachedDraft || cachedDraft.scope !== scope) cachedDraft = { scope, text: '', revision: 0, pending: null, flight: null }
  return cachedDraft
}

function draftCurrent(draft: any) { return Boolean(draft) && draft === cachedDraft }

function editDraft(draft: any, text: string) {
  if (!draftCurrent(draft) || draft.text === text) return
  draft.text = text
  draft.revision++
}

function submitDraft(draft: any, content: string, requestId: string, send: () => Promise<any>) {
  const revision = draft.revision
  const text = draft.text
  const pending = { content, requestId, state: 'sending' }
  draft.pending = pending
  const flight = Promise.resolve().then(send).then(() => {
    if (draftCurrent(draft)) {
      // Clear only the submitted snapshot, never text entered during the send.
      if (draft.revision === revision && draft.text === text && text.trim() === content) editDraft(draft, '')
      draft.pending = { ...pending, state: 'sent' }
    }
    return true
  }, () => {
    if (draftCurrent(draft)) draft.pending = { ...pending, state: 'failed' }
    return false
  }).finally(() => { if (draft.flight === flight) draft.flight = null })
  draft.flight = flight
  return flight
}

module.exports = { beginView, endView, viewActive, patchView, clearDraft, draftFor, draftCurrent, editDraft, submitDraft }
