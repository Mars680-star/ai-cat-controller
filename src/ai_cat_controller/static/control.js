(() => {
  "use strict";

  const $ = (selector) => document.querySelector(selector);
  const $$ = (selector) => [...document.querySelectorAll(selector)];

  const ui = {
    loginScreen: $("#login-screen"),
    loginForm: $("#login-form"),
    loginCode: $("#login-code"),
    nickname: $("#nickname"),
    loginApiKey: $("#login-api-key"),
    appShell: $("#app-shell"),
    connectionLabel: $("#connection-label"),
    connectionDot: $("#connection-dot"),
    topSubtitle: $("#top-subtitle"),
    navPetName: $("#nav-pet-name"),
    navPersonality: $("#nav-personality"),
    navButtons: $$(".nav-button[data-view]"),
    views: $$("[data-view-panel]"),
    logout: $("#logout-button"),
    bindSection: $("#bind-section"),
    connectedSection: $("#connected-section"),
    bindForm: $("#bind-form"),
    deviceSerial: $("#device-serial"),
    bindPetName: $("#bind-pet-name"),
    networkName: $("#network-name"),
    connectionMode: $("#connection-mode"),
    connectionDeviceId: $("#connection-device-id"),
    connectionSerial: $("#connection-serial"),
    connectionNetwork: $("#connection-network"),
    connectionNetworkName: $("#connection-network-name"),
    connectionLastSeen: $("#connection-last-seen"),
    connectionLastSeenSource: $("#connection-last-seen-source"),
    deviceStatusTitle: $("#device-status-title"),
    deviceStatusDescription: $("#device-status-description"),
    realDeviceStatus: $("#real-device-status"),
    realBatteryPercent: $("#real-battery-percent"),
    realBatteryAvailability: $("#real-battery-availability"),
    realBatteryStatus: $("#real-battery-status"),
    realBatteryVoltage: $("#real-battery-voltage"),
    realChargerOnline: $("#real-charger-online"),
    realBatteryPresent: $("#real-battery-present"),
    deviceStatusForm: $("#device-status-form"),
    mockOnline: $("#mock-online"),
    mockNetworkStatus: $("#mock-network-status"),
    mockBattery: $("#mock-battery"),
    mockBatteryValue: $("#mock-battery-value"),
    mockCharging: $("#mock-charging"),
    reconnect: $("#reconnect-button"),
    homePetName: $("#home-pet-name"),
    homeOnline: $("#home-online"),
    homePersonalityId: $("#home-personality-id"),
    homePersonality: $("#home-personality"),
    homePersonalityDescription: $("#home-personality-description"),
    homeLanguageStyle: $("#home-language-style"),
    homeIntimacy: $("#home-intimacy"),
    homeLevel: $("#home-level"),
    homeBattery: $("#home-battery"),
    homeCharging: $("#home-charging"),
    homeNetwork: $("#home-network"),
    homeNetworkName: $("#home-network-name"),
    homeAddress: $("#home-address"),
    actionGrid: $("#action-grid"),
    actionExecutions: $("#action-executions"),
    stopMotion: $("#stop-motion"),
    refreshActions: $("#refresh-actions"),
    growthLevel: $("#growth-level"),
    growthBadge: $("#growth-badge"),
    growthPoints: $("#growth-points"),
    growthProgress: $("#growth-progress"),
    growthProgressCopy: $("#growth-progress-copy"),
    growthDailyCap: $("#growth-daily-cap"),
    currentUnlocks: $("#current-unlocks"),
    nextUnlocks: $("#next-unlocks"),
    interactionButtons: $$(".interaction-button"),
    interactionHistory: $("#interaction-history"),
    growthTags: $("#growth-tags"),
    growthV1Status: $("#growth-v1-status"),
    growthTendencies: $("#growth-tendencies"),
    growthBehaviorSummary: $("#growth-behavior-summary"),
    growthEventHistory: $("#growth-event-history"),
    growthDebugForm: $("#growth-debug-form"),
    growthDebugEvent: $("#growth-debug-event"),
    growthDebugCount: $("#growth-debug-count"),
    dialogPetName: $("#dialog-pet-name"),
    dialogVoice: $("#dialog-voice"),
    voiceStateDot: $("#voice-state-dot"),
    voiceStateTitle: $("#voice-debug-title"),
    voiceStateMessage: $("#voice-state-message"),
    voiceStateElapsed: $("#voice-state-elapsed"),
    voiceFollowupRemaining: $("#voice-followup-remaining"),
    voiceStart: $("#voice-start"),
    voiceInterrupt: $("#voice-interrupt"),
    dialogSyncState: $("#dialog-sync-state"),
    conversationList: $("#conversation-list"),
    conversationDetailTitle: $("#conversation-detail-title"),
    conversationDetailMeta: $("#conversation-detail-meta"),
    conversationDetailStatus: $("#conversation-detail-status"),
    dialogList: $("#dialog-list"),
    dialogForm: $("#dialog-form"),
    dialogInput: $("#dialog-input"),
    settingsForm: $("#settings-form"),
    settingsName: $("#settings-name"),
    settingsVolume: $("#settings-volume"),
    settingsVolumeValue: $("#settings-volume-value"),
    settingsFollowUp: $("#settings-follow-up"),
    settingsFollowUpValue: $("#settings-follow-up-value"),
    settingsApiKey: $("#settings-api-key"),
    feedbackForm: $("#feedback-form"),
    feedbackCategory: $("#feedback-category"),
    feedbackContent: $("#feedback-content"),
    unbind: $("#unbind-button"),
    resetData: $("#reset-data-button"),
    personalityDialog: $("#personality-dialog"),
    revealName: $("#reveal-name"),
    revealDescription: $("#reveal-description"),
    revealStyle: $("#reveal-style"),
    revealConfirm: $("#reveal-confirm"),
    toast: $("#toast"),
  };

  const state = {
    apiKey: "",
    sessionToken: "",
    user: null,
    petId: null,
    dashboard: null,
    actions: [],
    activeView: "connection",
    toastTimer: null,
    refreshing: false,
    dialogsRefreshing: false,
    dialogDetailRequestId: 0,
    dialogConversations: [],
    selectedConversationId: null,
    selectedConversationSignature: null,
    voiceStatus: null,
    hardwareStatus: null,
    dialogConfig: null,
    intimacyPoints: null,
    growthTagIds: null,
  };

  const statusLabels = {
    online: "在线",
    weak: "弱网",
    offline: "离线",
    pending: "等待执行",
    running: "执行中",
    completed: "已完成",
    cancelled: "已取消",
    failed: "失败",
    timed_out: "超时",
  };

  const voiceStateLabels = {
    starting: "语音服务启动中",
    connecting: "正在连接云端",
    ready: "等待唤醒词",
    wake_detected: "已听到唤醒词",
    listening: "正在聆听",
    thinking: "正在思考",
    answering: "正在回答",
    followup_listening: "等待继续追问",
    interrupted: "回答已打断",
    echo_guard: "已停止回声循环",
    recovering: "正在恢复云端连接",
    offline: "语音服务离线",
    unavailable: "状态不可用",
    waking: "正在请求聆听",
    interrupting: "正在请求打断",
    submitting_text: "正在提交文字问题",
  };

  const batteryStateLabels = {
    charging: "正在充电",
    discharging: "使用电池",
    full: "已充满",
    not_charging: "已接电，未充电",
    unknown: "状态未知",
    unavailable: "电池状态不可用",
  };

  const conversationStatusLabels = {
    complete: "已完成",
    waiting_assistant: "等待回答",
    assistant_only: "仅有回答",
  };

  const growthAttributeLabels = {
    curiosity: "好奇",
    empathy: "共情",
    knowledge: "求知",
    energy: "活力",
    mischief: "淘气",
    discipline: "自律",
  };

  function showToast(message, level = "info") {
    window.clearTimeout(state.toastTimer);
    ui.toast.textContent = message;
    ui.toast.className = `toast visible${level === "error" ? " error" : ""}`;
    state.toastTimer = window.setTimeout(() => {
      ui.toast.className = "toast";
    }, 3200);
  }

  function requestHeaders(hasBody) {
    const headers = {};
    if (hasBody) {
      headers["Content-Type"] = "application/json";
    }
    if (state.apiKey) {
      headers["X-API-Key"] = state.apiKey;
    }
    if (state.sessionToken) {
      headers["X-Mock-Session"] = state.sessionToken;
    }
    return headers;
  }

  async function apiRequest(path, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 8000);
    const hasBody = Object.prototype.hasOwnProperty.call(options, "body");
    try {
      const response = await fetch(path, {
        ...options,
        cache: options.cache || "no-store",
        headers: {...requestHeaders(hasBody), ...(options.headers || {})},
        signal: controller.signal,
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const error = new Error(
          payload?.message || response.statusText || "请求失败",
        );
        error.status = response.status;
        throw error;
      }
      return payload;
    } catch (error) {
      if (error.name === "AbortError") {
        const timeoutError = new Error("请求超时，请检查服务连接");
        timeoutError.status = 0;
        throw timeoutError;
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function setTopStatus(text, online) {
    ui.connectionLabel.textContent = text;
    ui.connectionDot.classList.toggle("offline", !online);
  }

  function switchView(viewName) {
    if (!state.petId && viewName !== "connection") {
      showToast("请先绑定宠物设备", "error");
      viewName = "connection";
    }
    state.activeView = viewName;
    ui.views.forEach((view) => {
      view.classList.toggle("active", view.dataset.viewPanel === viewName);
    });
    ui.navButtons.forEach((button) => {
      button.classList.toggle("active", button.dataset.view === viewName);
    });
    if (state.petId) {
      if (viewName === "motion") {
        refreshActions().catch(reportError);
      } else if (viewName === "growth") {
        Promise.all([refreshIntimacy(), refreshGrowthPersonality()])
          .catch(reportError);
      } else if (viewName === "history") {
        Promise.all([refreshDialogs(), refreshVoiceStatus()]).catch(reportError);
      }
    }
  }

  function setBoundNavigation(bound) {
    ui.navButtons.forEach((button) => {
      button.disabled = !bound && button.dataset.view !== "connection";
    });
    ui.bindSection.classList.toggle("hidden", bound);
    ui.connectedSection.classList.toggle("hidden", !bound);
    ui.connectionMode.textContent = bound ? "已绑定" : "未绑定";
  }

  function formatDate(value) {
    if (!value) {
      return "--";
    }
    return new Date(value).toLocaleString("zh-CN", {
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
    });
  }

  function formatClock(value) {
    if (!value) {
      return "--";
    }
    return new Date(value).toLocaleTimeString("zh-CN", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
  }

  function reportError(error) {
    setTopStatus(error.status === 401 ? "会话失效" : "服务异常", false);
    showToast(error.message, "error");
  }

  async function performLogin({silent = false} = {}) {
    state.apiKey = ui.loginApiKey.value.trim();
    ui.settingsApiKey.value = state.apiKey;
    const payload = await apiRequest("/api/v1/auth/mock-login", {
      method: "POST",
      body: JSON.stringify({
        login_code: ui.loginCode.value.trim(),
        nickname: ui.nickname.value.trim(),
      }),
    });
    state.sessionToken = payload.data.session_token;
    state.user = payload.data.user;
    window.sessionStorage.setItem(
      "aiCatMockIdentity",
      JSON.stringify({
        loginCode: ui.loginCode.value.trim(),
        nickname: ui.nickname.value.trim(),
      }),
    );
    ui.loginScreen.classList.add("hidden");
    ui.appShell.classList.remove("hidden");
    setTopStatus("服务在线", true);
    ui.topSubtitle.textContent = state.user.nickname;

    const pets = payload.data.pets;
    state.intimacyPoints = null;
    state.growthTagIds = null;
    if (pets.length > 0) {
      state.petId = pets[0].pet_id;
      setBoundNavigation(true);
      await refreshAll();
      switchView(state.activeView);
    } else {
      state.petId = null;
      state.dashboard = null;
      setBoundNavigation(false);
      switchView("connection");
    }
    if (!silent) {
      showToast("登录成功");
    }
  }

  function renderDashboard(data) {
    state.dashboard = data;
    const pet = data.pet;
    const personality = data.personality;
    const intimacy = data.intimacy;
    const networkText = statusLabels[pet.network_status] || pet.network_status;

    ui.navPetName.textContent = pet.name;
    ui.navPersonality.textContent = personality.name;
    ui.topSubtitle.textContent = `${pet.name} · ${personality.name}`;
    ui.homePetName.textContent = pet.name;
    ui.dialogPetName.textContent = pet.name;
    ui.homeOnline.textContent = pet.online ? "在线" : "离线";
    ui.homeOnline.classList.toggle("offline", !pet.online);
    ui.homePersonalityId.textContent = personality.personality_id;
    ui.homePersonality.textContent = personality.name;
    ui.homePersonalityDescription.textContent = personality.description;
    ui.homeLanguageStyle.textContent = personality.language_style;
    ui.homeIntimacy.textContent = intimacy.points;
    ui.homeLevel.textContent = `Lv.${intimacy.level.level} ${intimacy.level.name}`;
    ui.homeBattery.textContent = `${pet.battery_percent}%`;
    ui.homeCharging.textContent = pet.charging ? "正在充电" : "未充电";
    ui.homeNetwork.textContent = networkText;
    ui.homeNetworkName.textContent = pet.network_name;
    ui.homeAddress.textContent = intimacy.address;
    ui.dialogVoice.textContent = personality.voice_name || personality.voice_id;
    ui.settingsName.value = pet.name;
    ui.settingsVolume.value = pet.volume;
    ui.settingsVolumeValue.textContent = `${pet.volume}%`;

    ui.connectionDeviceId.textContent = pet.device_id;
    ui.connectionSerial.textContent = pet.serial_number;
    ui.connectionNetwork.textContent = networkText;
    ui.connectionNetworkName.textContent = pet.network_name;
    ui.connectionLastSeen.textContent = formatDate(pet.last_seen_at);
    ui.mockOnline.checked = pet.online;
    ui.mockNetworkStatus.value = pet.network_status;
    ui.mockBattery.value = pet.battery_percent;
    ui.mockBatteryValue.textContent = `${pet.battery_percent}%`;
    ui.mockCharging.checked = pet.charging;
    setTopStatus(pet.online ? "设备在线" : "设备离线", pet.online);
  }

  function renderHardwareStatus(status) {
    state.hardwareStatus = status;
    const isLocalK1 = status.adapter_mode === "local_k1";
    ui.realDeviceStatus.classList.toggle("hidden", !isLocalK1);
    ui.deviceStatusForm.classList.toggle("hidden", isLocalK1);
    ui.reconnect.classList.toggle("hidden", isLocalK1);
    ui.deviceStatusTitle.textContent = isLocalK1
      ? "真机电源状态"
      : "设备状态模拟";
    ui.deviceStatusDescription.textContent = isLocalK1
      ? "直接读取 K1 电池与充电芯片，每 5 秒更新"
      : "用于验证断网、重连和电量显示";
    ui.connectionLastSeenSource.textContent = isLocalK1
      ? "真机实时状态"
      : "Mock 状态";

    if (!isLocalK1) {
      return;
    }

    if (
      status.output_volume_available &&
      status.output_volume_percent !== null
    ) {
      const outputVolume = Math.min(100, Math.max(0, status.output_volume_percent));
      ui.settingsVolume.value = outputVolume;
      ui.settingsVolumeValue.textContent = status.output_muted
        ? `${outputVolume}%（静音）`
        : `${outputVolume}%`;
    }

    ui.connectionLastSeen.textContent = formatDate(new Date().toISOString());
    ui.realChargerOnline.textContent = status.charger_online === null
      ? "未知"
      : status.charger_online ? "已连接" : "未连接";
    ui.realBatteryPresent.textContent = status.battery_present === null
      ? "电池检测状态未知"
      : status.battery_present ? "电池已安装" : "未检测到电池";

    if (!status.battery_available || status.battery_percent === null) {
      ui.homeBattery.textContent = "--";
      ui.homeCharging.textContent = status.battery_error || "电池不可用";
      ui.realBatteryPercent.textContent = "--";
      ui.realBatteryAvailability.textContent = status.battery_error || "电池不可用";
      ui.realBatteryStatus.textContent = "不可用";
      ui.realBatteryVoltage.textContent = "--";
      return;
    }

    const stateText = batteryStateLabels[status.battery_status] || "状态未知";
    const voltageText = status.battery_voltage_mv === null
      ? ""
      : ` · ${(status.battery_voltage_mv / 1000).toFixed(2)} V`;
    ui.homeBattery.textContent = `${status.battery_percent}%`;
    ui.homeCharging.textContent = `${stateText}${voltageText}`;
    ui.realBatteryPercent.textContent = `${status.battery_percent}%`;
    ui.realBatteryAvailability.textContent = "电池数据来自 K1";
    ui.realBatteryStatus.textContent = stateText;
    ui.realBatteryVoltage.textContent = status.battery_voltage_mv === null
      ? "电压数据不可用"
      : `${(status.battery_voltage_mv / 1000).toFixed(2)} V`;
  }

  async function refreshDashboard() {
    if (!state.petId || state.refreshing) {
      return;
    }
    state.refreshing = true;
    try {
      const payload = await apiRequest(
        `/api/v1/pets/${encodeURIComponent(state.petId)}/dashboard`,
      );
      renderDashboard(payload.data);
      try {
        const devicePayload = await apiRequest("/api/v1/device/status");
        renderHardwareStatus(devicePayload.data);
      } catch (error) {
        state.hardwareStatus = null;
      }
    } finally {
      state.refreshing = false;
    }
  }

  async function refreshAll() {
    await refreshDashboard();
    const results = await Promise.allSettled([
      refreshActions(),
      refreshIntimacy(),
      refreshGrowthPersonality(),
      refreshDialogs(),
      refreshDialogConfig(),
    ]);
    const failed = results.find((result) => result.status === "rejected");
    if (failed) {
      throw failed.reason;
    }
  }

  async function refreshDialogConfig() {
    const payload = await apiRequest("/api/v1/dialog/config");
    state.dialogConfig = payload.data;
    ui.settingsFollowUp.min = payload.data.minimum_seconds;
    ui.settingsFollowUp.max = payload.data.maximum_seconds;
    ui.settingsFollowUp.value = payload.data.follow_up_seconds;
    ui.settingsFollowUpValue.textContent = `${payload.data.follow_up_seconds} 秒`;
  }

  async function bindPet() {
    const payload = await apiRequest("/api/v1/pets/bind", {
      method: "POST",
      body: JSON.stringify({
        device_serial: ui.deviceSerial.value.trim(),
        pet_name: ui.bindPetName.value.trim(),
        network_name: ui.networkName.value.trim(),
      }),
    });
    state.intimacyPoints = null;
    state.petId = payload.data.pet.pet_id;
    setBoundNavigation(true);
    await refreshAll();
    if (payload.data.blind_box_revealed) {
      const personality = payload.data.personality;
      ui.revealName.textContent = personality.name;
      ui.revealDescription.textContent = personality.description;
      ui.revealStyle.textContent = personality.language_style;
      ui.personalityDialog.showModal();
    } else {
      showToast("设备已重新绑定，原性格与成长数据已恢复");
      switchView("home");
    }
  }

  function renderActionCatalog(actions) {
    state.actions = actions;
    ui.actionGrid.replaceChildren();
    actions.forEach((action) => {
      const card = document.createElement("article");
      card.className = [
        "action-card",
        action.unlocked ? "" : "locked",
        action.available ? "" : "unavailable",
      ].filter(Boolean).join(" ");
      const header = document.createElement("header");
      const title = document.createElement("h3");
      title.textContent = action.name;
      const number = document.createElement("span");
      number.className = "action-number";
      number.textContent = `#${String(action.action_no).padStart(3, "0")}`;
      const description = document.createElement("p");
      description.textContent = action.available
        ? action.description
        : action.unavailable_reason || "当前硬件暂不支持该动作";
      const button = document.createElement("button");
      button.type = "button";
      button.className = action.unlocked && action.available
        ? "button primary"
        : "button secondary";
      button.disabled = (
        !action.unlocked ||
        !action.available ||
        !state.dashboard?.pet.online
      );
      button.textContent = !action.available
        ? "硬件暂不可用"
        : action.unlocked
          ? (state.dashboard?.pet.online ? "执行动作" : "设备离线")
          : `Lv.${action.min_intimacy_level} 解锁`;
      button.addEventListener("click", () => executeAction(action.action_id));
      header.append(title, number);
      card.append(header, description, button);
      ui.actionGrid.append(card);
    });
  }

  function renderExecutions(executions) {
    ui.actionExecutions.replaceChildren();
    ui.actionExecutions.classList.toggle("empty-state", executions.length === 0);
    if (executions.length === 0) {
      ui.actionExecutions.textContent = "暂无动作记录";
      return;
    }
    executions.forEach((execution) => {
      const row = document.createElement("div");
      row.className = "timeline-row";
      const detail = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = execution.action_id;
      const time = document.createElement("small");
      time.textContent = `${formatDate(execution.created_at)} · ${execution.request_id}`;
      const result = document.createElement("span");
      result.className = ["completed", "running"].includes(execution.status)
        ? "result-positive"
        : "result-negative";
      result.textContent = statusLabels[execution.status] || execution.status;
      detail.append(title, time);
      row.append(detail, result);
      ui.actionExecutions.append(row);
    });
  }

  async function refreshActions() {
    if (!state.petId) {
      return;
    }
    const [catalog, executions] = await Promise.all([
      apiRequest(`/api/v1/pets/${encodeURIComponent(state.petId)}/actions`),
      apiRequest(
        `/api/v1/pets/${encodeURIComponent(state.petId)}/actions/executions`,
      ),
    ]);
    renderActionCatalog(catalog.data);
    renderExecutions(executions.data);
  }

  async function executeAction(actionId) {
    $$(".action-card button").forEach((button) => {
      button.disabled = true;
    });
    try {
      const payload = await apiRequest(
        `/api/v1/pets/${encodeURIComponent(state.petId)}/actions/${encodeURIComponent(actionId)}/execute`,
        {
          method: "POST",
          body: JSON.stringify({
            request_id: `ui-${actionId}-${Date.now()}`,
          }),
        },
      );
      showToast(
        payload.data.duplicate ? "重复请求已忽略" : "动作已进入执行队列",
      );
      await refreshActions();
      window.setTimeout(() => refreshActions().catch(reportError), 900);
      window.setTimeout(() => refreshActions().catch(reportError), 2200);
      window.setTimeout(() => refreshActions().catch(reportError), 4200);
    } catch (error) {
      reportError(error);
      await refreshActions().catch(() => {});
    }
  }

  function fillList(list, values, emptyText) {
    list.replaceChildren();
    const entries = values.length > 0 ? values : [emptyText];
    entries.forEach((value) => {
      const item = document.createElement("li");
      item.textContent = value;
      list.append(item);
    });
  }

  function renderInteractionHistory(history) {
    ui.interactionHistory.replaceChildren();
    ui.interactionHistory.classList.toggle("empty-state", history.length === 0);
    if (history.length === 0) {
      ui.interactionHistory.textContent = "暂无培养记录";
      return;
    }
    history.forEach((event) => {
      const row = document.createElement("div");
      row.className = "timeline-row";
      const detail = document.createElement("div");
      const title = document.createElement("strong");
      const ruleName = {
        daily_check_in: "每日见面",
        valid_dialog: "有效对话",
        touch: "触摸反馈",
        completed_task: "完成互动任务",
        ignored_greeting: "忽略主动问候",
      }[event.event_type] || event.event_type;
      title.textContent = ruleName;
      const time = document.createElement("small");
      time.textContent = event.reason || formatDate(event.created_at);
      const points = document.createElement("span");
      points.className = event.points_delta >= 0
        ? "result-positive"
        : "result-negative";
      points.textContent = `${event.points_delta > 0 ? "+" : ""}${event.points_delta}`;
      detail.append(title, time);
      row.append(detail, points);
      ui.interactionHistory.append(row);
    });
  }

  async function refreshIntimacy({notifyChange = false} = {}) {
    if (!state.petId) {
      return;
    }
    const payload = await apiRequest(
      `/api/v1/pets/${encodeURIComponent(state.petId)}/intimacy`,
    );
    const data = payload.data;
    if (
      notifyChange &&
      state.intimacyPoints !== null &&
      data.points > state.intimacyPoints
    ) {
      showToast(`检测到新互动，亲密度 +${data.points - state.intimacyPoints}`);
    }
    state.intimacyPoints = data.points;
    ui.growthLevel.textContent = `Lv.${data.level.level} ${data.level.name}`;
    ui.growthBadge.textContent = data.level.badge;
    ui.growthPoints.textContent = data.points;
    ui.growthProgress.style.width = `${data.progress.percent}%`;
    ui.growthProgressCopy.textContent = data.progress.next_level === null
      ? "已达到最高等级"
      : `${data.points} / ${data.progress.next_level}`;
    ui.growthDailyCap.textContent = data.debug_unlimited_touch_intimacy
      ? "触摸计分不限（调试）"
      : `每日增长上限 ${data.daily_growth_cap}`;
    fillList(ui.currentUnlocks, data.current_unlocks, "暂无");
    fillList(ui.nextUnlocks, data.next_unlocks, "已全部解锁");
    renderInteractionHistory(data.history);
  }

  async function addInteraction(eventType) {
    const payload = await apiRequest(
      `/api/v1/pets/${encodeURIComponent(state.petId)}/interactions`,
      {
        method: "POST",
        body: JSON.stringify({
          event_type: eventType,
          request_id: `ui-${eventType}-${Date.now()}`,
          metadata: {source: "browser_mock"},
        }),
      },
    );
    const event = payload.data;
    const message = event.points_delta === 0
      ? (event.reason || "本次亲密度未变化")
      : `亲密度 ${event.points_delta > 0 ? "+" : ""}${event.points_delta}`;
    showToast(message);
    await Promise.all([
      refreshIntimacy(),
      refreshGrowthPersonality({notifyTags: true}),
      refreshDashboard(),
      refreshActions(),
    ]);
  }

  function renderGrowthPersonality(data, {notifyTags = false} = {}) {
    ui.growthV1Status.textContent = data.enabled ? "已启用" : "未启用";
    ui.growthV1Status.title = data.enabled
      ? "成长事件会更新长期人格和运行时提示词"
      : "成长数据只读，不记录新事件，也不修改运行时提示词";
    const activeTagIds = data.active_tags.map((tag) => tag.tag_id);
    if (notifyTags && state.growthTagIds !== null) {
      const newTags = data.active_tags.filter(
        (tag) => !state.growthTagIds.includes(tag.tag_id),
      );
      if (newTags.length > 0) {
        showToast(`获得成长标签：${newTags.map((tag) => tag.display_name).join("、")}`);
      }
    }
    state.growthTagIds = activeTagIds;

    ui.growthTags.replaceChildren();
    if (data.active_tags.length === 0) {
      const empty = document.createElement("span");
      empty.className = "muted";
      empty.textContent = "尚未形成成长标签";
      ui.growthTags.append(empty);
    } else {
      data.active_tags.forEach((tag) => {
        const item = document.createElement("span");
        item.className = "growth-tag";
        item.textContent = tag.display_name;
        item.title = tag.description;
        ui.growthTags.append(item);
      });
    }

    ui.growthTendencies.replaceChildren();
    Object.values(data.attributes).forEach((attribute) => {
      const row = document.createElement("div");
      row.className = "tendency-row";
      const label = document.createElement("span");
      label.textContent = attribute.label;
      const tendency = document.createElement("strong");
      tendency.textContent = attribute.value === undefined
        ? attribute.tendency
        : `${attribute.tendency} · ${attribute.value.toFixed(2)}`;
      row.append(label, tendency);
      ui.growthTendencies.append(row);
    });

    fillList(
      ui.growthBehaviorSummary,
      data.behavior_profile.directives,
      "尚未形成明显的长期行为倾向",
    );
    renderGrowthEvents(data.recent_events);
    ui.growthDebugForm.classList.toggle("hidden", !data.debug_values_visible);
  }

  function renderGrowthEvents(events) {
    ui.growthEventHistory.replaceChildren();
    ui.growthEventHistory.classList.toggle("empty-state", events.length === 0);
    if (events.length === 0) {
      ui.growthEventHistory.textContent = "暂无成长记录";
      return;
    }
    events.forEach((event) => {
      const row = document.createElement("div");
      row.className = "timeline-row";
      const detail = document.createElement("div");
      const title = document.createElement("strong");
      title.textContent = event.event_label;
      const context = document.createElement("small");
      context.textContent = `${event.topic} · ${formatDate(event.created_at)}`;
      const delta = document.createElement("span");
      delta.className = "growth-event-delta";
      const changes = Object.entries(event.attribute_delta || {})
        .filter(([, value]) => value > 0)
        .map(([name, value]) => (
          `${growthAttributeLabels[name] || name} +${value.toFixed(2)}`
        ));
      if (changes.length > 0) {
        delta.textContent = changes.join(" · ");
      } else if (event.changed_attributes.length > 0) {
        delta.textContent = `${event.changed_attributes.join("、")}有所成长`;
      } else {
        delta.textContent = "标签形成";
      }
      detail.append(title, context);
      row.append(detail, delta);
      ui.growthEventHistory.append(row);
    });
  }

  async function refreshGrowthPersonality({notifyTags = false} = {}) {
    if (!state.petId) {
      return;
    }
    const payload = await apiRequest(
      `/api/v1/pets/${encodeURIComponent(state.petId)}/growth`,
    );
    renderGrowthPersonality(payload.data, {notifyTags});
  }

  async function submitDebugGrowth() {
    const submit = ui.growthDebugForm.querySelector("button[type='submit']");
    submit.disabled = true;
    try {
      const eventType = ui.growthDebugEvent.value;
      const payload = await apiRequest(
        `/api/v1/pets/${encodeURIComponent(state.petId)}/growth/debug`,
        {
          method: "POST",
          body: JSON.stringify({
            request_id: `growth-${eventType}-${Date.now()}`,
            event_type: eventType,
            count: Number(ui.growthDebugCount.value),
            topic: `debug-${eventType}`,
            emotion: "neutral",
            engagement: 1.0,
          }),
        },
      );
      renderGrowthPersonality(payload.data.growth);
      const awardedNames = payload.data.batch.awarded_tags.map((tagId) => {
        const tag = payload.data.growth.tag_history.find(
          (item) => item.tag_id === tagId,
        );
        return tag?.display_name || tagId;
      });
      showToast(awardedNames.length > 0
        ? `获得成长标签：${awardedNames.join("、")}`
        : `已生成 ${payload.data.batch.processed} 条成长事件`);
    } finally {
      submit.disabled = false;
    }
  }

  function renderDialogSync(sync) {
    if (!sync) {
      ui.dialogSyncState.textContent = "等待同步";
      return;
    }
    if (sync.imported_messages > 0) {
      ui.dialogSyncState.textContent = `新增 ${sync.imported_messages} 条`;
      return;
    }
    if (!sync.source_available && sync.message_count === 0) {
      ui.dialogSyncState.textContent = "等待真机字幕";
      return;
    }
    ui.dialogSyncState.textContent = `已同步 ${formatClock(sync.synced_at)}`;
  }

  function conversationSignature(conversation) {
    return [
      conversation.conversation_id,
      conversation.updated_at,
      conversation.message_count,
      conversation.status,
    ].join(":");
  }

  function renderConversationList(conversations) {
    ui.conversationList.replaceChildren();
    ui.conversationList.classList.toggle("empty-state", conversations.length === 0);
    if (conversations.length === 0) {
      ui.conversationList.textContent = "暂无真实语音会话";
      return;
    }
    conversations.forEach((conversation) => {
      const button = document.createElement("button");
      button.className = "conversation-item";
      button.type = "button";
      button.dataset.conversationId = conversation.conversation_id;
      button.setAttribute(
        "aria-selected",
        String(conversation.conversation_id === state.selectedConversationId),
      );

      const heading = document.createElement("span");
      heading.className = "conversation-item-heading";
      const preview = document.createElement("strong");
      preview.textContent = conversation.preview || "无用户字幕";
      const time = document.createElement("time");
      time.dateTime = conversation.updated_at;
      time.textContent = formatDate(conversation.updated_at);
      heading.append(preview, time);

      const meta = document.createElement("span");
      meta.className = "conversation-item-meta";
      const status = document.createElement("span");
      status.className = `conversation-status ${conversation.status}`;
      status.textContent = conversationStatusLabels[conversation.status]
        || conversation.status;
      const count = document.createElement("span");
      count.textContent = `${conversation.message_count} 条消息`;
      meta.append(status, count);
      button.append(heading, meta);
      button.addEventListener("click", () => {
        if (state.selectedConversationId === conversation.conversation_id) {
          return;
        }
        state.selectedConversationId = conversation.conversation_id;
        state.selectedConversationSignature = null;
        renderConversationList(state.dialogConversations);
        refreshDialogDetail(conversation.conversation_id).catch(reportError);
      });
      ui.conversationList.append(button);
    });
  }

  function clearDialogDetail() {
    state.selectedConversationId = null;
    state.selectedConversationSignature = null;
    ui.conversationDetailTitle.textContent = "选择一条会话";
    ui.conversationDetailMeta.textContent = "--";
    ui.conversationDetailStatus.textContent = "--";
    ui.conversationDetailStatus.className = "state-label";
    ui.dialogList.replaceChildren();
    ui.dialogList.classList.add("empty-state");
    ui.dialogList.textContent = "暂无对话记录";
  }

  function renderDialogDetail(data) {
    const {conversation, messages} = data;
    ui.conversationDetailTitle.textContent = conversation.preview || "会话详情";
    const source = conversation.source === "device" ? "真机语音" : "网页 Mock";
    const duration = conversation.duration_seconds > 0
      ? ` · ${conversation.duration_seconds} 秒`
      : "";
    ui.conversationDetailMeta.textContent =
      `${formatDate(conversation.started_at)} · ${source}${duration}`;
    ui.conversationDetailStatus.textContent =
      conversationStatusLabels[conversation.status] || conversation.status;
    ui.conversationDetailStatus.className =
      `state-label ${conversation.status}`;

    ui.dialogList.replaceChildren();
    ui.dialogList.classList.toggle("empty-state", messages.length === 0);
    if (messages.length === 0) {
      ui.dialogList.textContent = "暂无对话记录";
      return;
    }
    messages.forEach((message) => {
      const block = document.createElement("article");
      block.className = `dialog-message ${message.role}`;
      const content = document.createElement("p");
      content.textContent = message.content;
      const meta = document.createElement("small");
      meta.textContent = message.role === "assistant"
        ? `${formatDate(message.created_at)} · ${message.voice_id || "Mock 音色"}`
        : formatDate(message.created_at);
      block.append(content, meta);
      ui.dialogList.append(block);
    });
    ui.dialogList.scrollTop = ui.dialogList.scrollHeight;
  }

  async function refreshDialogDetail(conversationId) {
    if (!conversationId) {
      return;
    }
    const requestId = ++state.dialogDetailRequestId;
    const payload = await apiRequest(
      `/api/v1/pets/${encodeURIComponent(state.petId)}`
      + `/dialog-conversations/${encodeURIComponent(conversationId)}`,
    );
    if (
      requestId !== state.dialogDetailRequestId
      || state.selectedConversationId !== conversationId
    ) {
      return;
    }
    renderDialogDetail(payload.data);
    state.selectedConversationSignature = conversationSignature(
      payload.data.conversation,
    );
    renderDialogSync(payload.data.sync);
  }

  async function refreshDialogs(options = {}) {
    if (!state.petId || state.dialogsRefreshing) {
      return;
    }
    state.dialogsRefreshing = true;
    try {
      const payload = await apiRequest(
        `/api/v1/pets/${encodeURIComponent(state.petId)}/dialog-conversations`,
      );
      const conversations = payload.data.conversations;
      state.dialogConversations = conversations;
      renderDialogSync(payload.data.sync);

      const selectedExists = conversations.some(
        (item) => item.conversation_id === state.selectedConversationId,
      );
      if (options.selectLatest || !selectedExists) {
        state.selectedConversationId = conversations[0]?.conversation_id || null;
        state.selectedConversationSignature = null;
      }
      renderConversationList(conversations);
      if (!state.selectedConversationId) {
        clearDialogDetail();
        return;
      }

      const selected = conversations.find(
        (item) => item.conversation_id === state.selectedConversationId,
      );
      const nextSignature = conversationSignature(selected);
      if (options.forceDetail || nextSignature !== state.selectedConversationSignature) {
        await refreshDialogDetail(state.selectedConversationId);
      }
    } finally {
      state.dialogsRefreshing = false;
    }
  }

  function renderVoiceStatus(data) {
    state.voiceStatus = data;
    const now = Date.now();
    const elapsedMs = data.updated_at_ms > 0
      ? Math.max(now - data.updated_at_ms, 0)
      : 0;
    const remainingMs = data.follow_up_deadline_ms > 0
      ? Math.max(data.follow_up_deadline_ms - now, 0)
      : 0;
    ui.voiceStateTitle.textContent =
      voiceStateLabels[data.state] || data.state;
    ui.voiceStateMessage.textContent = data.stale
      ? `${data.message || "没有收到新状态"}（状态可能已过期）`
      : data.message;
    ui.voiceStateElapsed.textContent = data.updated_at_ms > 0
      ? `${Math.floor(elapsedMs / 1000)} 秒`
      : "--";
    ui.voiceFollowupRemaining.textContent = remainingMs > 0
      ? `追问窗口剩余 ${Math.ceil(remainingMs / 1000)} 秒`
      : (data.session_active ? "连续会话进行中" : "尚未进入连续会话");
    ui.voiceStateDot.className =
      `voice-state-dot ${data.stale ? "unavailable" : data.state}`;
    ui.voiceInterrupt.disabled =
      !data.can_interrupt && !data.session_active;
  }

  async function refreshVoiceStatus() {
    const payload = await apiRequest("/api/v1/dialog/status");
    renderVoiceStatus(payload.data);
  }

  async function controlVoice(endpoint) {
    ui.voiceStart.disabled = true;
    ui.voiceInterrupt.disabled = true;
    try {
      const payload = await apiRequest(endpoint, {
        method: "POST",
        body: JSON.stringify({request_id: `web-voice-${Date.now()}`}),
      });
      showToast(payload.message);
      window.setTimeout(() => refreshVoiceStatus().catch(reportError), 150);
      window.setTimeout(() => refreshVoiceStatus().catch(reportError), 700);
    } finally {
      window.setTimeout(() => {
        ui.voiceStart.disabled = false;
      }, 800);
    }
  }

  async function sendDialog() {
    const content = ui.dialogInput.value.trim();
    if (!content) {
      return;
    }
    const submit = ui.dialogForm.querySelector("button");
    submit.disabled = true;
    try {
      if (state.hardwareStatus?.adapter_mode === "local_k1") {
        const payload = await apiRequest("/api/v1/dialog/text", {
          method: "POST",
          body: JSON.stringify({
            content,
            request_id: `web-text-${Date.now()}`,
          }),
        });
        ui.dialogInput.value = "";
        showToast(payload.message);
        await refreshVoiceStatus();
        window.setTimeout(() => {
          refreshDialogs({selectLatest: true, forceDetail: true})
            .catch(reportError);
        }, 300);
        return;
      }
      const payload = await apiRequest(
        `/api/v1/pets/${encodeURIComponent(state.petId)}/dialogs`,
        {
          method: "POST",
          body: JSON.stringify({content, trigger_action: true}),
        },
      );
      ui.dialogInput.value = "";
      showToast(`已使用 ${payload.data.style.tone} 风格回应`);
      await Promise.all([
        refreshDialogs({selectLatest: true, forceDetail: true}),
        refreshIntimacy(),
        refreshGrowthPersonality({notifyTags: true}),
        refreshDashboard(),
      ]);
    } finally {
      submit.disabled = false;
    }
  }

  async function updateDeviceStatus(values = null) {
    const body = values || {
      online: ui.mockOnline.checked,
      battery_percent: Number(ui.mockBattery.value),
      charging: ui.mockCharging.checked,
      network_status: ui.mockNetworkStatus.value,
    };
    await apiRequest(
      `/api/v1/pets/${encodeURIComponent(state.petId)}/mock-device-status`,
      {method: "POST", body: JSON.stringify(body)},
    );
    await Promise.all([refreshDashboard(), refreshActions()]);
    showToast(body.online ? "设备状态已更新" : "已模拟设备断线");
  }

  async function saveSettings() {
    state.apiKey = ui.settingsApiKey.value.trim();
    ui.loginApiKey.value = state.apiKey;
    await Promise.all([
      apiRequest(
        `/api/v1/pets/${encodeURIComponent(state.petId)}/settings`,
        {
          method: "PATCH",
          body: JSON.stringify({
            name: ui.settingsName.value.trim(),
            volume: Number(ui.settingsVolume.value),
          }),
        },
      ),
      apiRequest("/api/v1/dialog/config", {
        method: "PATCH",
        body: JSON.stringify({
          follow_up_seconds: Number(ui.settingsFollowUp.value),
        }),
      }),
    ]);
    await Promise.all([refreshDashboard(), refreshDialogConfig()]);
    showToast("设置已保存");
  }

  async function submitFeedback() {
    await apiRequest(
      `/api/v1/pets/${encodeURIComponent(state.petId)}/feedback`,
      {
        method: "POST",
        body: JSON.stringify({
          category: ui.feedbackCategory.value,
          content: ui.feedbackContent.value.trim(),
        }),
      },
    );
    ui.feedbackContent.value = "";
    showToast("反馈已记录");
  }

  async function unbindPet() {
    const confirmed = window.confirm(
      "确认解绑当前设备？设备性格和成长数据会保留。",
    );
    if (!confirmed) {
      return;
    }
    await apiRequest(
      `/api/v1/pets/${encodeURIComponent(state.petId)}/unbind`,
      {method: "POST", body: JSON.stringify({})},
    );
    state.petId = null;
    state.intimacyPoints = null;
    state.growthTagIds = null;
    state.dashboard = null;
    state.actions = [];
    ui.navPetName.textContent = "等待绑定";
    ui.navPersonality.textContent = "暂无性格";
    setBoundNavigation(false);
    switchView("connection");
    setTopStatus("设备已解绑", false);
    showToast("设备已解绑");
  }

  async function resetProductData() {
    const confirmed = window.confirm(
      "确认格式化全部体验数据？系统会先自动备份，然后清除账号、绑定、性格、亲密度、动作、对话和反馈记录。此操作不能在网页中撤销。",
    );
    if (!confirmed) {
      return;
    }

    ui.resetData.disabled = true;
    try {
      const payload = await apiRequest("/api/v1/admin/reset-product-data", {
        method: "POST",
        body: JSON.stringify({confirmation: "RESET_PRODUCT_DATA"}),
      });
      const backupId = payload.data.backup_id;
      logout();
      setTopStatus("体验数据已格式化", false);
      window.alert(`格式化完成。备份编号：${backupId}\n请重新登录并从性格盲盒开始测试。`);
    } finally {
      ui.resetData.disabled = false;
    }
  }

  function logout() {
    state.sessionToken = "";
    state.user = null;
    state.petId = null;
    state.intimacyPoints = null;
    state.growthTagIds = null;
    state.dashboard = null;
    window.sessionStorage.removeItem("aiCatMockIdentity");
    ui.appShell.classList.add("hidden");
    ui.loginScreen.classList.remove("hidden");
    setTopStatus("未登录", false);
  }

  ui.loginForm.addEventListener("submit", (event) => {
    event.preventDefault();
    performLogin().catch(reportError);
  });
  ui.bindForm.addEventListener("submit", (event) => {
    event.preventDefault();
    bindPet().catch(reportError);
  });
  ui.navButtons.forEach((button) => {
    button.addEventListener("click", () => switchView(button.dataset.view));
  });
  ui.logout.addEventListener("click", logout);
  ui.revealConfirm.addEventListener("click", () => {
    ui.personalityDialog.close();
    switchView("home");
  });
  ui.mockBattery.addEventListener("input", () => {
    ui.mockBatteryValue.textContent = `${ui.mockBattery.value}%`;
  });
  ui.deviceStatusForm.addEventListener("submit", (event) => {
    event.preventDefault();
    updateDeviceStatus().catch(reportError);
  });
  ui.reconnect.addEventListener("click", () => {
    updateDeviceStatus({
      online: true,
      battery_percent: Number(ui.mockBattery.value),
      charging: ui.mockCharging.checked,
      network_status: "online",
    }).catch(reportError);
  });
  ui.stopMotion.addEventListener("click", () => {
    apiRequest("/api/v1/motion/stop", {
      method: "POST",
      body: JSON.stringify({}),
    })
      .then((payload) => {
        showToast(payload.message);
        return refreshActions();
      })
      .catch(reportError);
  });
  ui.refreshActions.addEventListener("click", () => {
    refreshActions().catch(reportError);
  });
  ui.interactionButtons.forEach((button) => {
    button.addEventListener("click", () => {
      addInteraction(button.dataset.event).catch(reportError);
    });
  });
  ui.growthDebugForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitDebugGrowth().catch(reportError);
  });
  ui.dialogForm.addEventListener("submit", (event) => {
    event.preventDefault();
    sendDialog().catch(reportError);
  });
  ui.voiceStart.addEventListener("click", () => {
    controlVoice("/api/v1/dialog/wake").catch(reportError);
  });
  ui.voiceInterrupt.addEventListener("click", () => {
    controlVoice("/api/v1/dialog/interrupt").catch(reportError);
  });
  ui.settingsVolume.addEventListener("input", () => {
    ui.settingsVolumeValue.textContent = `${ui.settingsVolume.value}%`;
  });
  ui.settingsFollowUp.addEventListener("input", () => {
    ui.settingsFollowUpValue.textContent = `${ui.settingsFollowUp.value} 秒`;
  });
  ui.settingsApiKey.addEventListener("input", () => {
    state.apiKey = ui.settingsApiKey.value.trim();
    ui.loginApiKey.value = state.apiKey;
  });
  ui.settingsForm.addEventListener("submit", (event) => {
    event.preventDefault();
    saveSettings().catch(reportError);
  });
  ui.feedbackForm.addEventListener("submit", (event) => {
    event.preventDefault();
    submitFeedback().catch(reportError);
  });
  ui.unbind.addEventListener("click", () => {
    unbindPet().catch(reportError);
  });
  ui.resetData.addEventListener("click", () => {
    resetProductData().catch(reportError);
  });

  async function boot() {
    setBoundNavigation(false);
    const saved = window.sessionStorage.getItem("aiCatMockIdentity");
    if (!saved) {
      return;
    }
    try {
      const identity = JSON.parse(saved);
      ui.loginCode.value = identity.loginCode;
      ui.nickname.value = identity.nickname;
      await performLogin({silent: true});
    } catch (error) {
      window.sessionStorage.removeItem("aiCatMockIdentity");
      reportError(error);
    }
  }

  boot();
  window.setInterval(async () => {
    if (!state.petId || document.hidden) {
      return;
    }
    try {
      await refreshDashboard();
      if (state.activeView === "growth") {
        await Promise.all([
          refreshIntimacy({notifyChange: true}),
          refreshGrowthPersonality({notifyTags: true}),
        ]);
      }
    } catch (error) {
      if (error.status === 401) {
        const saved = window.sessionStorage.getItem("aiCatMockIdentity");
        if (saved) {
          try {
            await performLogin({silent: true});
            return;
          } catch (loginError) {
            reportError(loginError);
            return;
          }
        }
      }
      reportError(error);
    }
  }, 5000);
  window.setInterval(() => {
    if (
      state.sessionToken &&
      !document.hidden &&
      state.activeView === "history"
    ) {
      refreshVoiceStatus().catch(reportError);
      refreshDialogs().catch(reportError);
    }
  }, 1000);
})();
