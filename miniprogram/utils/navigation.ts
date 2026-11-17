// One transition at a time, shared by the bottom navigation and page shortcuts.
// Release through native callbacks rather than an arbitrary debounce delay.
let transition: object | null = null

function transitionOnce(url: string, push: boolean) {
  if (transition) return false
  const current = transition = {}
  const release = () => { if (transition === current) transition = null }
  const failed = () => {
    release()
    wx.showToast({ title: '切换未完成，请重试', icon: 'none' })
  }
  try {
    if (push) wx.navigateTo({ url, fail: failed, complete: release })
    else wx.redirectTo({ url, fail: failed, complete: release })
  } catch (_) {
    failed()
  }
  return true
}

function redirectOnce(url: string) { return transitionOnce(url, false) }
function navigateOnce(url: string) { return transitionOnce(url, true) }

module.exports = { redirectOnce, navigateOnce }
