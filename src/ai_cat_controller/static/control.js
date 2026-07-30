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
    dialogPetName: $("#dialog-pet-name"),
    dialogVoice: $("#dialog-voice"),
    voiceStateDot: $("#voice-state-dot"),
    voiceStateTitle: $("#voice-debug-title"),
    voiceStateMessage: $("#voice-state-message"),
    voiceStateElapsed: $("#voice-state-elapsed"),
    voiceFollowupRemaining: $("#voice-followup-remaining"),
    voiceStart: $("#voice-start"),
    voiceInterrupt: $("#voice-interrupt"),
    dialogList: $("#dialog-list"),
    dialogForm: $("#dialog-form"),
    dialogInput: $("#dialog-input"),
    settingsForm: $("#settings-form"),
    settingsName: $("#settings-name"),
    settingsVolume: $("#settings-volume"),
    settingsVolumeValue: $("#settings-volume-value"),
    settingsApiKey: $("#settings-api-key"),
    feedbackForm: $("#feedback-form"),
    feedbackCategory: $("#feedback-category"),
    feedbackContent: $("#feedback-content"),
    unbind: $("#unbind-button"),
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
    voiceStatus: null,
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
    offline: "语音服务离线",
    unavailable: "状态不可用",
    waking: "正在请求聆听",
    interrupting: "正在请求打断",
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
        refreshIntimacy().catch(reportError);
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
    ui.dialogVoice.textContent = personality.voice_id;
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
    } finally {
      state.refreshing = false;
    }
  }

  async function refreshAll() {
    await refreshDashboard();
    const results = await Promise.allSettled([
      refreshActions(),
      refreshIntimacy(),
      refreshDialogs(),
    ]);
    const failed = results.find((result) => result.status === "rejected");
    if (failed) {
      throw failed.reason;
    }
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
      card.className = `action-card${action.unlocked ? "" : " locked"}`;
      const header = document.createElement("header");
      const title = document.createElement("h3");
      title.textContent = action.name;
      const number = document.createElement("span");
      number.className = "action-number";
      number.textContent = `#${String(action.action_no).padStart(3, "0")}`;
      const description = document.createElement("p");
      description.textContent = action.description;
      const button = document.createElement("button");
      button.type = "button";
      button.className = action.unlocked ? "button primary" : "button secondary";
      button.disabled = !action.unlocked || !state.dashboard?.pet.online;
      button.textContent = action.unlocked
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

  async function refreshIntimacy() {
    if (!state.petId) {
      return;
    }
    const payload = await apiRequest(
      `/api/v1/pets/${encodeURIComponent(state.petId)}/intimacy`,
    );
    const data = payload.data;
    ui.growthLevel.textContent = `Lv.${data.level.level} ${data.level.name}`;
    ui.growthBadge.textContent = data.level.badge;
    ui.growthPoints.textContent = data.points;
    ui.growthProgress.style.width = `${data.progress.percent}%`;
    ui.growthProgressCopy.textContent = data.progress.next_level === null
      ? "已达到最高等级"
      : `${data.points} / ${data.progress.next_level}`;
    ui.growthDailyCap.textContent = `每日增长上限 ${data.daily_growth_cap}`;
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
    await Promise.all([refreshIntimacy(), refreshDashboard(), refreshActions()]);
  }

  function renderDialogs(messages) {
    ui.dialogList.replaceChildren();
    ui.dialogList.classList.toggle("empty-state", messages.length === 0);
    if (messages.length === 0) {
      ui.dialogList.textContent = "暂无对话记录";
      return;
    }
    [...messages].reverse().forEach((message) => {
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

  async function refreshDialogs() {
    if (!state.petId) {
      return;
    }
    const payload = await apiRequest(
      `/api/v1/pets/${encodeURIComponent(state.petId)}/dialogs`,
    );
    renderDialogs(payload.data);
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
        refreshDialogs(),
        refreshIntimacy(),
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
    await apiRequest(
      `/api/v1/pets/${encodeURIComponent(state.petId)}/settings`,
      {
        method: "PATCH",
        body: JSON.stringify({
          name: ui.settingsName.value.trim(),
          volume: Number(ui.settingsVolume.value),
        }),
      },
    );
    await refreshDashboard();
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
    state.dashboard = null;
    state.actions = [];
    ui.navPetName.textContent = "等待绑定";
    ui.navPersonality.textContent = "暂无性格";
    setBoundNavigation(false);
    switchView("connection");
    setTopStatus("设备已解绑", false);
    showToast("设备已解绑");
  }

  function logout() {
    state.sessionToken = "";
    state.user = null;
    state.petId = null;
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
    }
  }, 1000);
})();
