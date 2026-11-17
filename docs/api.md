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
| POST | `/api/v1/dialog/text` | Submit text to the live Volcengine session; K1 speaks the answer. |
| GET | `/api/v1/personality/runtime` | Read the current personality revision, console-managed voice marker and native apply state. |
| POST | `/api/v1/auth/mock-login` | 创建 Mock 用户会话。 |
| GET | `/api/v1/personalities` | 查询 5 种逻辑性格配置。 |
| GET | `/api/v1/pets` | 查询当前用户的宠物。 |
| POST | `/api/v1/pets/bind` | 绑定设备并在首次创建时抽取性格。 |
| GET | `/api/v1/pets/{pet_id}/dashboard` | 查询宠物主页聚合数据。 |
| GET | `/api/v1/pets/{pet_id}/intimacy` | 查询等级、规则、解锁和事件。 |
| POST | `/api/v1/pets/{pet_id}/interactions` | 幂等记录亲密度事件。 |
| GET | `/api/v1/pets/{pet_id}/growth` | 查询成长属性倾向、标签、最近事件和行为画像。 |
| POST | `/api/v1/pets/{pet_id}/growth/debug` | 批量模拟成长事件；仅 Mock 或显式开发开关。 |
| GET | `/api/v1/pets/{pet_id}/actions` | 查询只读安全动作预设。 |
| POST | `/api/v1/pets/{pet_id}/actions/{action_id}/execute` | 执行预设动作。 |
| GET | `/api/v1/pets/{pet_id}/actions/executions` | 查询动作结果。 |
| GET/POST | `/api/v1/pets/{pet_id}/dialogs` | 查询或生成 Mock 对话。 |
| GET | `/api/v1/pets/{pet_id}/dialog-conversations` | 同步真机字幕并查询会话摘要。 |
| GET | `/api/v1/pets/{pet_id}/dialog-conversations/{conversation_id}` | 查询单个会话及时间顺序消息。 |
| POST | `/api/v1/pets/{pet_id}/memory/clear` | 清除当前用户的近期记忆，保留网页对话记录。 |
| PATCH | `/api/v1/pets/{pet_id}/settings` | 修改名称与音量。 |
| POST | `/api/v1/pets/{pet_id}/mock-device-status` | 模拟电量和网络状态。 |
| POST | `/api/v1/pets/{pet_id}/feedback` | 记录异常反馈。 |
| POST | `/api/v1/pets/{pet_id}/unbind` | 解除用户与设备绑定。 |
| POST | `/api/v1/admin/reset-product-data` | 备份并清除全部产品体验数据；默认禁用。 |
| GET | `/api/v1/integrations/mijia/pets/{pet_id}/status` | 查询当前用户的米家接入准备状态。 |
| GET | `/api/v1/integrations/mijia/pets/{pet_id}/spec` | 查询未映射平台实例编号的固定能力草案。 |
| POST | `/api/v1/integrations/mijia/pets/{pet_id}/prepare` | 幂等准备米家接入资料和隔离网关标识。 |
| POST | `/api/v1/integrations/mijia/pets/{pet_id}/remove` | 清除准备状态并撤销网关标识。 |
| GET | `/api/v1/gateway/mijia/v1/devices/{gateway_device_id}/snapshot` | 查询用户态设备快照。 |
| POST | `/api/v1/gateway/mijia/v1/devices/{gateway_device_id}/properties/read` | 读取固定属性白名单。 |
| POST | `/api/v1/gateway/mijia/v1/devices/{gateway_device_id}/properties/write` | 幂等写入固定音量属性。 |
| POST | `/api/v1/gateway/mijia/v1/devices/{gateway_device_id}/actions/{action_id}` | 幂等执行固定米家动作。 |

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
| `/motion/head/shake` | Fixed `head_lr` in legacy_safe; `conversation_tilt` preset in k1_vendor_smooth | Enabled after board/profile validation. |
| `/motion/head/nod` | `head_ud` with the selected fixed profile speed | Enabled after board/profile validation. |
| `/motion/tail/wag` | `tail_lr` with the selected fixed profile speed | Disabled by default; requires `AI_CAT_ENABLE_TAIL_MOTION=true`. |
| `/motion/stop` | `/usr/bin/ai-toy_app motor stop` | Idempotent; also stops a voice-started motor process. |

The profile selects reviewed fixed commands/presets, not caller-provided motor
parameters. See `k1-motion-profiles.md` for the device-specific evidence and
ranges. This software safety update does not change the underlying trajectories.

The new source version also accepts operator-only `AI_CAT_ENABLE_MOTION` and
`AI_CAT_HAS_TAIL` (both default `true`, preserving existing device behavior).
Movement disabled returns `501` before creating a task or product execution
record; stop remains usable with the original authentication. A tailless device
rejects tail movements and whole tail-dependent combos with `501`, even with
debug unlock or tail permission enabled; the catalog omits those four presets.
Device status exposes read-only `motion_enabled`/`has_tail`, and
`tail_state=not_present` when no tail exists. There is no HTTP policy toggle.

Environment values are case-insensitive exact `1/true/yes/on` or `0/false/no/off`;
whitespace, an explicit empty string, or other values fail closed. Python refuses
invalid startup settings; native motor actions reject invalid policy but native
stop still works. Deployment must include the corresponding Python/native
versions; the current K1 has not received this update. See
[`k1-no-tail-device.md`](k1-no-tail-device.md).

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

米家接入状态和能力草案接口也需要 `X-Mock-Session`。网关接口只使用全局
`X-API-Key`，不接受浏览器声明用户或宠物归属；正式连接器还必须部署在 HTTPS 和
独立连接器认证之后。当前不保存米家凭据，`miot_model` 及 MIoT 实例编号均未填写。
固定属性、动作、幂等和撤销语义见 `mijia-integration-gateway.md`。

成长查询在 Mock 或显式开启 Debug 时额外返回 6 个属性的精确 `value`，正式
Local K1 默认只返回“普通/初显/成长中/明显/突出”倾向。快速养成请求示例：

```json
{
  "request_id": "demo-knowledge-1",
  "event_type": "knowledge_discussion",
  "count": 20,
  "topic": "robotics",
  "emotion": "neutral",
  "engagement": 1.0
}
```

Mock 模式始终允许该调试接口；Local K1 只有显式设置
`AI_CAT_ENABLE_DEBUG_GROWTH=true` 才允许，默认返回 `403`。单批最多 500 条，
同一 `request_id + 序号` 重放时返回重复统计，不会再次增长。

格式化接口还要求 `AI_CAT_ENABLE_PRODUCT_DATA_RESET=true`，请求体必须为：

```json
{
  "confirmation": "RESET_PRODUCT_DATA"
}
```

接口会先备份 SQLite 和固定运行时文件，再清除账号、绑定、性格、亲密度、记忆、
动作、对话和反馈数据。火山鉴权、License、SDK 和系统配置不在清理范围内。响应只
返回备份编号；正在语音对话时返回 `409`，完成后原 `X-Mock-Session` 立即失效。

独立清除近期记忆请求为：

```json
{
  "request_id": "web-memory-clear-1723456789000"
}
```

该接口按当前用户和宠物记录记忆截止时间，立即重建人格运行时，但不删除网页可见的
对话记录。截止时间之后完成的新问答可以重新形成记忆；重复 `request_id` 不会再次
推进截止时间。正在语音对话时同样返回 `409`。

性格运行时接口返回当前 `personality_id`、亲密度称呼、语音动作白名单和自主行为
配置。`voice_type=volcengine_console` 与 `voice_source=volcengine_console` 只
表示音色由火山引擎控制台统一管理，不是实际发音人 ID。`native_applied=true`
表示原生进程上报的 revision 与 FastAPI 当前 revision 一致。短期记忆只公开
`memory_revision`、`memory_message_count` 和 `memory_latest_at`；长期结构化记忆只
公开 `long_term_memory_revision`、`long_term_memory_count` 和
`long_term_memory_latest_at`。接口不返回历史正文或事实内容，详细边界见
`docs/persistent-dialog-memory.md`。

文字提问请求：

```json
{
  "content": "你现在电量是多少？",
  "request_id": "web-text-1720000000000"
}
```

`content` 为 `1..500` 个非控制字符。真机只在对话状态为 `ready`、
`followup_listening` 或 `interrupted` 时接收；正在聆听、思考或回答时返回 `409`。
接口成功表示原生进程已收到请求，不表示模型已经回答。最终用户文本和 AI 回答
通过会话历史接口同步。

会话摘要接口默认返回最近 `30` 个会话，可用 `limit=1..50` 调整。响应中的
`sync.revision`、`message_count` 和 `latest_message_at` 可用于客户端增量刷新；
`status` 为 `waiting_assistant`、`complete` 或 `assistant_only`。单会话详情的
`messages` 按时间升序返回。旧 `/dialogs` 查询接口保留，供已有调用方兼容。

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
- `output_volume_available`：是否成功连接系统 PulseAudio。
- `output_volume_percent`：当前默认输出音量；网页只允许设置 `0..100`。
- `output_muted`：默认输出是否静音。
- `output_volume_error`：PulseAudio 状态不可用时的诊断信息。

为了避免电量计状态滞后，`charger_online=false` 时接口会把有效电池状态统一为
`discharging`；充电器在线但电量计未报告充电时返回 `not_charging`。浏览器端
状态请求使用 `no-store`，每 5 秒重新读取真机。

`PATCH /api/v1/pets/{pet_id}/settings` 的音量在 `local_k1` 模式下会先写入
PulseAudio 固定的 `@DEFAULT_SINK@`，成功后才持久化到 SQLite。`0` 同时静音，
非零值自动取消静音；命令不接受任意 sink 名称。

Validation errors use `422`, conflicts `409`, unavailable devices `503`,
timeouts `504`, and invalid authentication `401`.
