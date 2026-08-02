# API

## Response envelope

Success:

```json
{
  "success": true,
  "message": "操作成功",
  "data": {}
}
```

Error:

```json
{
  "success": false,
  "message": "错误说明",
  "data": {
    "code": "machine_readable_code",
    "details": {}
  }
}
```

## Endpoints

| Method | Path | Result |
|---|---|---|
| GET | `/health` | Process health; no API Key required. |
| GET | `/api/v1/device/status` | Adapter, motion, dialog, process and battery state. |
| GET | `/api/v1/services/status` | Fixed local service states. |
| POST | `/api/v1/motion/head/shake` | Accept a head-shake task. |
| POST | `/api/v1/motion/head/nod` | Accept a head-nod task. |
| POST | `/api/v1/motion/tail/wag` | Accept a tail-wag task. |
| POST | `/api/v1/motion/stop` | Cancel current motion. |
| GET | `/api/v1/dialog/config` | Read the persisted follow-up window. |
| PATCH | `/api/v1/dialog/config` | Set the follow-up window to `5..120` seconds. |
| POST | `/api/v1/dialog/wake` | Enter dialog state. |
| POST | `/api/v1/dialog/interrupt` | Interrupt dialog state. |
| POST | `/api/v1/auth/mock-login` | 创建 Mock 用户会话。 |
| GET | `/api/v1/personalities` | 查询 5 种逻辑性格配置。 |
| GET | `/api/v1/pets` | 查询当前用户的宠物。 |
| POST | `/api/v1/pets/bind` | 绑定设备并在首次创建时抽取性格。 |
| GET | `/api/v1/pets/{pet_id}/dashboard` | 查询宠物主页聚合数据。 |
| GET | `/api/v1/pets/{pet_id}/intimacy` | 查询等级、规则、解锁和事件。 |
| POST | `/api/v1/pets/{pet_id}/interactions` | 幂等记录亲密度事件。 |
| GET | `/api/v1/pets/{pet_id}/actions` | 查询只读安全动作预设。 |
| POST | `/api/v1/pets/{pet_id}/actions/{action_id}/execute` | 执行预设动作。 |
| GET | `/api/v1/pets/{pet_id}/actions/executions` | 查询动作结果。 |
| GET/POST | `/api/v1/pets/{pet_id}/dialogs` | 查询或生成 Mock 对话。 |
| PATCH | `/api/v1/pets/{pet_id}/settings` | 修改名称与音量。 |
| POST | `/api/v1/pets/{pet_id}/mock-device-status` | 模拟电量和网络状态。 |
| POST | `/api/v1/pets/{pet_id}/feedback` | 记录异常反馈。 |
| POST | `/api/v1/pets/{pet_id}/unbind` | 解除用户与设备绑定。 |

Motion body:

```json
{
  "intensity": 0.5,
  "duration_ms": 600,
  "request_id": "optional-identifier"
}
```

`intensity` is `0.1..1.0`; `duration_ms` is `100..3000`. Motion acceptance
returns `202`. Local K1 uses these fields only for request validation; callers
cannot override native motor parameters. The fixed mappings are:

| API | Local K1 command | Availability |
|---|---|---|
| `/motion/head/shake` | `/usr/bin/ai-toy_app motor head_lr 1` | Enabled after board validation. |
| `/motion/head/nod` | `/usr/bin/ai-toy_app motor head_ud 2` | Enabled after board validation. |
| `/motion/tail/wag` | `/usr/bin/ai-toy_app motor tail_lr 1` | Disabled by default; requires `AI_CAT_ENABLE_TAIL_MOTION=true`. |
| `/motion/stop` | `/usr/bin/ai-toy_app motor stop` | Idempotent; also stops a voice-started motor process. |

Overlapping motion returns `409`. An unavailable tail action returns `501`.
Timeout and cancellation first send `SIGTERM`; the native routine idles the
motor before exit, with `SIGKILL` used only after the grace period expires.

When authentication is enabled, send:

```text
X-API-Key: configured-secret
```

除登录和性格列表外，产品 Mock 路由还需要登录响应中的：

```text
X-Mock-Session: temporary-session-token
```

`X-Mock-Session` 只用于浏览器产品 Mock，不是正式微信鉴权方案。动作接口只接受
预设 `action_id`，不接受角度、速度或持续时间覆盖。

在 `local_k1` 模式下，设备状态从 Linux `power_supply` 接口返回：

- `battery_available`：是否获得有效电量并检测到电池。
- `battery_percent`：`0..100`，不可用时为 `null`。
- `battery_status`：`charging`、`discharging`、`full`、
  `not_charging`、`unknown` 或 `unavailable`。
- `battery_present`：电池计量芯片报告的电池存在状态。
- `battery_voltage_mv`：电池电压，单位毫伏。
- `charging`：是否正在充电；无法判断时为 `null`。
- `charger_online`：充电器输入是否在线，不等同于一定正在充电。
- `battery_error`：读取失败的属性，不可用时用于诊断。

为了避免电量计状态滞后，`charger_online=false` 时接口会把有效电池状态统一为
`discharging`；充电器在线但电量计未报告充电时返回 `not_charging`。浏览器端
状态请求使用 `no-store`，每 5 秒重新读取真机。

Validation errors use `422`, conflicts `409`, unavailable devices `503`,
timeouts `504`, and invalid authentication `401`.
