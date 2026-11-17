// Public presentation only. No QR/key, network credential or request mutation.
const LABELS: any = { prepared: '热点准备中', opening: '热点正在开启', open: '等待填写网络',
  accepted: '设备已接收', connecting: '设备连接中', connected: '设备已报告连接', failed: '设备报告失败',
  not_sent: '尚未发送网络资料', unknown: '原结果待核验' }

function localNetworkView(network: any, options: any = {}) {
  const value = network || {}, record = value.record, saved = value.marker
  const closeout = value.closeout || {}
  const archived = closeout.archived === true
  const paired = value.paired === true, busy = value.busy === true || options.confirming === true
  const activeInner = Boolean(record && record.networkRequestId !== null)
  const complete = Boolean(record && record.status === 'connected' && saved && saved.settled === true)
  // Inner acceptance outranks stale outer 'open' metadata. This only changes
  // presentation; no child event can settle either authenticated ledger.
  const flowReview = options.flowPending === true || ['accepted', 'connecting', 'unknown', 'failed', 'connected'].includes(options.flowStatus)
  const needsReview = !archived && (activeInner || value.legacyBlocked === true || flowReview)
  const pendingOuter = Boolean(saved && saved.settled !== true)
  const opening = Boolean(!archived && record && ['prepared', 'opening', 'open'].includes(record.status) && !needsReview)
  const expired = Boolean(opening && (record.expiresAt * 1000 <= Date.now() || options.hotspotWindowExpired === true))
  const pairLocation = !needsReview && options.pairLocation === 'hotspot' ? 'hotspot' : 'station'
  // A same-visit, just-confirmed public hint can restore the next address after
  // hide erased authorization. It never claims an AP is reachable or grants I/O.
  const resumeHotspot = options.resumeHotspot === true && pairLocation === 'hotspot' && pendingOuter && !record
  const showPair = !complete && (!paired || options.pairPrompt === true)
  const atHotspot = value.deviceAddress === '10.47.0.1'
  let step = complete || archived || needsReview || options.flowMode === 'receipt' ? 3 : opening || resumeHotspot || options.flowMode === 'configure' ? 2 : 1
  let title = '为狐狸设置 Wi-Fi', copy = '先连接设备，再确认更换网络。不会自动切换手机 Wi-Fi。'
  let action = '', actionLabel = ''
  if (complete) {
    title = '本次网络已核验'; copy = '原加密回执已核对，可以返回控制界面。'
  } else if (archived) {
    title = '本次测试已归档，结果仍未知'
    copy = '手机与设备两端的旧记录均已保留，不算联网成功或失败。旧窗口和二维码不能再用；下次配网需你另外开启新窗口。'
    action = value.canPrepare === true ? 'openWindow' : ''; actionLabel = '开始新配网'
  } else if (showPair) {
    title = resumeHotspot ? '连接本次设备热点' : pendingOuter || needsReview ? '继续上次配网' : '先连接你的狐狸'
    copy = needsReview && pairLocation !== 'hotspot'
      ? '请先连接目标 Wi-Fi；若未成功，连接狐狸已恢复的原普通网络，再重扫同一机身卡。原请求保留，不重新发送。'
      : pairLocation === 'hotspot'
      ? '请在系统 Wi-Fi 连接本次设备热点，返回后在这里重扫同一机身卡。'
      : '手机连接猫当前所在的普通 Wi-Fi，在这里扫描机身卡；原请求编号会保留。'
  } else if (busy) {
    title = needsReview ? '正在核验原结果' : '正在检查设备状态'
    copy = '请稍候；正在读取状态，不会自动发送网络资料。'
    action = 'refreshWindow'; actionLabel = '请稍候…'
  } else if (needsReview && atHotspot) {
    title = '手机切回目标 Wi-Fi'
    copy = '资料提交后，设备会关闭临时热点尝试连接目标网络。请把手机也连接目标 Wi-Fi，再重扫同一机身卡核验；热点消失不代表成功或失败。'
    action = 'pairStation'; actionLabel = '已连目标 Wi-Fi，扫码核验'
  } else if (value.error && value.canRead === true && needsReview) {
    title = '重新连接，核验原结果'
    copy = '原请求已保留。请确认手机与狐狸在同一普通 Wi-Fi，重扫同一机身卡连接当前地址；不再寻找已关闭的旧热点。'
    action = 'pairStation'; actionLabel = '重新扫码连接当前网络'
  } else if (value.error && value.canRead === true) {
    title = '暂时没能读取设备'; copy = '原记录已保留。先检查当前连接，不会重新发送或开启热点。'
    action = 'refreshWindow'; actionLabel = '重新检查连接'
  } else if (closeout.phase === 'unknown') {
    title = '本次收尾结果待核验'; copy = '保留两端原记录。请先只读核对，不重复提交归档，也不重新配网。'
    action = 'refreshCloseout'; actionLabel = '只读核对收尾结果'
  } else if (closeout.remoteArchived === true) {
    title = '设备端已留档，手机端待完成'; copy = '仍不代表联网成功或失败。请在下方核对并明确确认完成手机端归档，暂不开始新配网。'
    action = 'focusCloseout'; actionLabel = '继续手机端归档'
  } else if (closeout.eligible === true) {
    title = '原未知结果已核对'; copy = '不用重复扫码。请在下方“结束本次测试并归档”中确认事实；原结果仍未知。'
    action = 'focusCloseout'; actionLabel = '查看收尾确认'
  } else if (value.canReceipt === true) {
    title = '核验本次联网结果'; copy = '读取原加密回执，不重新发送 Wi-Fi 信息。'
    action = 'verifyReceipt'; actionLabel = '核验联网结果'
  } else if (needsReview) {
    title = '原结果还需核对'; copy = '如果手机刚换网，请重扫同一机身卡连接猫的新地址；不要重新发送。'
    action = value.canRead ? 'refreshWindow' : ''; actionLabel = '核对原结果'
  } else if (value.canConfigure === true && !expired) {
    title = '填写目标 Wi-Fi'; copy = '已连接设备热点，下一步直接在本页填写猫之后使用的网络。'
    action = 'continueConfiguration'; actionLabel = '填写 Wi-Fi'
  } else if (expired) {
    title = '核对窗口是否已结束'; copy = '窗口时间已到，只核对原编号，不猜测成功或再次发送。'
    action = value.canRead ? 'refreshWindow' : ''; actionLabel = '核对原窗口'
  } else if (opening) {
    title = '连接本次设备热点'; copy = '到手机系统 Wi-Fi 连接下方热点，再返回本页扫码继续。'
    action = 'pairHotspot'; actionLabel = '已连接热点，扫码继续'
  } else if (pendingOuter) {
    title = '核对上次开启请求'; copy = '开启请求已经记录，只核对原编号，不创建新窗口。'
    action = value.canRead ? 'refreshWindow' : ''; actionLabel = '核对原窗口'
  } else if (value.canPrepare === true) {
    title = record && record.status === 'not_sent' ? '可以重新开始' : '准备更换狐狸的网络'
    copy = '确认后猫会暂时断开原网络，并开启约 180 秒设备热点。'
    action = 'openWindow'; actionLabel = record && record.status === 'not_sent' ? '重新开启配网' : '开始配网'
  } else if (value.canRead === true) {
    action = 'refreshWindow'; actionLabel = '检查配网状态'
  }
  return { step, title, copy, action, actionLabel, complete, showPair,
    actionEnabled: Boolean(options.available === true && !busy && action),
    showHotspot: opening && !expired && !complete, showReconnect: Boolean(!showPair && !complete && action !== 'pairStation' && (needsReview || pendingOuter)),
    showControlExit: (needsReview || archived) && !complete,
    reviewPending: needsReview,
    showCloseout: Boolean(archived || closeout.remoteArchived === true || record && record.status === 'unknown'),
    closeoutArchived: archived, closeoutRecovering: !archived && (closeout.remoteArchived === true || closeout.phase === 'recovering'),
    closeoutReviewEnabled: Boolean(options.available === true && paired && !busy && !options.flowMode && !closeout.loading && !closeout.submitting),
    closeoutChecksEnabled: Boolean(options.available === true && paired && !busy && !options.flowMode && !closeout.loading && !closeout.submitting && !archived && ['ready', 'recovering'].includes(closeout.phase) && (closeout.eligible === true || closeout.remoteArchived === true) && options.closeoutReviewRequired !== true),
    closeoutActionLabel: closeout.remoteArchived === true || closeout.phase === 'recovering' ? '完成手机端归档' : '结束本次测试并归档',
    controlExitEnabled: Boolean(options.available === true && paired && !atHotspot && !value.error && !busy),
    pairAddressHint: pairLocation === 'hotspot' ? '10.47.0.1' : '', pairLocation,
    statusLabel: record ? LABELS[record.status] || '等待核对' : '',
    connectionVerified: Boolean(paired && !value.error && !(needsReview && atHotspot)),
    connectionLabel: value.error ? '需要重新连接' : needsReview && atHotspot ? '等待切网核验' : paired ? '设备已连接' : '待扫码连接',
    stepOneDone: step > 1, stepTwoDone: step > 2 }
}

module.exports = { localNetworkView }
