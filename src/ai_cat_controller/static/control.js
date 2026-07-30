(() => {
  "use strict";

  const ui = {
    connection: document.querySelector("#connection-label"),
    adapter: document.querySelector("#adapter-badge"),
    currentAction: document.querySelector("#current-action"),
    dialogState: document.querySelector("#dialog-state"),
    uptime: document.querySelector("#uptime"),
    hostname: document.querySelector("#hostname"),
    serviceList: document.querySelector("#service-list"),
    intensity: document.querySelector("#intensity"),
    intensityValue: document.querySelector("#intensity-value"),
    duration: document.querySelector("#duration"),
    durationValue: document.querySelector("#duration-value"),
    apiKey: document.querySelector("#api-key"),
    stop: document.querySelector("#stop-motion"),
    wake: document.querySelector("#wake-dialog"),
    interrupt: document.querySelector("#interrupt-dialog"),
    actionButtons: [...document.querySelectorAll(".action-button")],
    log: document.querySelector("#operation-log"),
    clearLog: document.querySelector("#clear-log"),
  };

  const state = {
    apiKey: "",
    actionActive: false,
    consecutiveFailures: 0,
  };

  function appendLog(message, level = "info") {
    const item = document.createElement("li");
    const timestamp = new Date().toLocaleTimeString();
    item.textContent = `[${timestamp}] ${level.toUpperCase()} ${message}`;
    ui.log.prepend(item);
    while (ui.log.children.length > 30) {
      ui.log.lastElementChild.remove();
    }
  }

  function requestHeaders(hasBody) {
    const headers = {};
    if (hasBody) {
      headers["Content-Type"] = "application/json";
    }
    if (state.apiKey) {
      headers["X-API-Key"] = state.apiKey;
    }
    return headers;
  }

  async function apiRequest(path, options = {}) {
    const controller = new AbortController();
    const timeout = window.setTimeout(() => controller.abort(), 5000);
    const hasBody = Object.prototype.hasOwnProperty.call(options, "body");
    try {
      const response = await fetch(path, {
        ...options,
        headers: {...requestHeaders(hasBody), ...(options.headers || {})},
        signal: controller.signal,
      });
      const payload = await response.json().catch(() => null);
      if (!response.ok) {
        const message = payload?.message || response.statusText || "请求失败";
        throw new Error(`HTTP ${response.status}: ${message}`);
      }
      return payload;
    } catch (error) {
      if (error.name === "AbortError") {
        throw new Error("请求超时");
      }
      throw error;
    } finally {
      window.clearTimeout(timeout);
    }
  }

  function setActionAvailability(active) {
    state.actionActive = active;
    ui.actionButtons.forEach((button) => {
      button.disabled = active;
    });
  }

  function renderDevice(data) {
    const active = !["idle", "unavailable"].includes(data.current_action);
    setActionAvailability(active);
    ui.currentAction.textContent = data.current_action;
    ui.dialogState.textContent = data.dialog_state;
    ui.hostname.textContent = data.hostname;
    ui.uptime.textContent = `${Math.floor(data.uptime_seconds)} s`;
    ui.adapter.textContent = data.adapter_mode.toUpperCase();
    ui.adapter.className = `mode-badge ${data.adapter_mode === "mock" ? "mock" : "local"}`;
  }

  function renderServices(services) {
    ui.serviceList.replaceChildren();
    services.forEach((service) => {
      const row = document.createElement("div");
      row.className = "service-row";

      const name = document.createElement("span");
      name.className = "service-name";
      name.textContent = service.service_name;

      const status = document.createElement("span");
      status.className = `service-state ${service.active ? "ok" : ""}`;
      status.textContent = service.error
        ? "ERROR"
        : `${service.active ? "ACTIVE" : "INACTIVE"} / ${service.enabled ? "ENABLED" : "DISABLED"}`;

      row.append(name, status);
      ui.serviceList.append(row);
    });
  }

  async function refreshStatus() {
    const results = await Promise.allSettled([
      apiRequest("/api/v1/device/status"),
      apiRequest("/api/v1/services/status"),
    ]);
    const failed = results.filter((result) => result.status === "rejected");
    if (failed.length > 0) {
      state.consecutiveFailures += 1;
      if (state.consecutiveFailures >= 2) {
        ui.connection.textContent = "服务离线";
      }
      return;
    }
    state.consecutiveFailures = 0;
    ui.connection.textContent = "服务在线";
    renderDevice(results[0].value.data);
    renderServices(results[1].value.data.services);
  }

  async function runMotion(endpoint) {
    setActionAvailability(true);
    try {
      const payload = await apiRequest(endpoint, {
        method: "POST",
        body: JSON.stringify({
          intensity: Number(ui.intensity.value),
          duration_ms: Number(ui.duration.value),
        }),
      });
      appendLog(`${payload.message}: ${payload.data.action}`);
      await refreshStatus();
    } catch (error) {
      setActionAvailability(false);
      appendLog(error.message, "error");
    }
  }

  async function stopMotion({quiet = false, keepalive = false} = {}) {
    try {
      const response = await fetch("/api/v1/motion/stop", {
        method: "POST",
        headers: requestHeaders(false),
        keepalive,
      });
      if (!quiet) {
        const payload = await response.json().catch(() => null);
        if (!response.ok) {
          throw new Error(`HTTP ${response.status}: ${payload?.message || "停止失败"}`);
        }
        appendLog(payload.message);
        await refreshStatus();
      }
    } catch (error) {
      if (!quiet) {
        appendLog(error.message, "error");
      }
    }
  }

  async function runDialog(endpoint) {
    try {
      const payload = await apiRequest(endpoint, {
        method: "POST",
        body: JSON.stringify({}),
      });
      appendLog(payload.message);
      await refreshStatus();
    } catch (error) {
      appendLog(error.message, "error");
    }
  }

  ui.intensity.addEventListener("input", () => {
    ui.intensityValue.textContent = ui.intensity.value;
  });
  ui.duration.addEventListener("input", () => {
    ui.durationValue.textContent = `${ui.duration.value} ms`;
  });
  ui.apiKey.addEventListener("input", () => {
    state.apiKey = ui.apiKey.value;
    refreshStatus();
  });
  ui.actionButtons.forEach((button) => {
    button.addEventListener("click", () => runMotion(button.dataset.endpoint));
  });
  ui.stop.addEventListener("click", () => stopMotion());
  ui.wake.addEventListener("click", () => runDialog("/api/v1/dialog/wake"));
  ui.interrupt.addEventListener("click", () => runDialog("/api/v1/dialog/interrupt"));
  ui.clearLog.addEventListener("click", () => ui.log.replaceChildren());

  document.addEventListener("visibilitychange", () => {
    if (document.hidden && state.actionActive) {
      stopMotion({quiet: true, keepalive: true});
    }
  });
  window.addEventListener("beforeunload", () => {
    if (state.actionActive) {
      stopMotion({quiet: true, keepalive: true});
    }
  });

  refreshStatus().catch((error) => appendLog(error.message, "error"));
  window.setInterval(refreshStatus, 3000);
})();
