# 性格与培养配置

## 五种性格

| ID | 性格 | 对话风格 | 火山音色 | `voice_type` | 语音动作白名单 |
|---|---|---|---|---|---|
| `sunny_explorer` | 元气探险家 | 明快、有活力、邀请探索 | 撒娇学妹 | `zh_female_yuanqinvyou_moon_bigtts` | 摇头、点头、摇尾 |
| `gentle_companion` | 温柔陪伴者 | 柔和、慢节奏、先倾听 | 温柔小雅 | `zh_female_wenrouxiaoya_moon_bigtts` | 点头 |
| `proud_star` | 傲娇小明星 | 俏皮、嘴硬心软、不贬低用户 | 傲娇霸总 | `zh_male_aojiaobazong_moon_bigtts` | 摇头、点头 |
| `curious_scholar` | 好奇小博士 | 清晰解释、鼓励验证 | 少年梓辛 | `zh_male_shaonianzixin_moon_bigtts` | 点头、摇头、摇尾 |
| `calm_guardian` | 沉稳守护者 | 稳重直接、优先安全 | 渊博小叔 | `zh_male_yuanboxiaoshu_moon_bigtts` | 点头 |

完整提示词、等级称呼、动作触发规则和禁止内容位于
`src/ai_cat_controller/domain/personalities.py`。首次创建宠物实例时随机选择性格；
重新绑定只恢复原性格，不重新抽取。

Local K1 复用一个火山智能体，在当前 WebSocket 会话中动态覆盖性格配置：

1. FastAPI 根据设备序列号找到已绑定宠物，并组合性格、人名、亲密度称呼、禁止
   内容和可用动作。
2. 配置原子写入
   `/var/lib/ai-cat-controller/personality-runtime.json`，权限为 `0600`。
3. 原生对话进程只在没有唤醒、收音、思考、播放或工具执行时读取新 revision，
   通过火山 `session.update` 更新 `LLMConfig.SystemMessages` 和
   `TTSConfig.ProviderParams.audio.voice_type`。
4. 原生 Function Calling 在执行电机前再次检查性格动作白名单；不允许的动作只
   返回拒绝结果，不启动电机。
5. `toy_motor.service` 从 `/api/v1/personality/runtime` 读取同一性格的安全头部
   动作权重与固定主动短语。K1 使用预生成 WAV 本地播放，每 3 分钟最多 1 句，
   不启动火山服务；播放期间通过 `/run/ai-cat/local-speech-active` 暂停唤醒收音。
   WAV 由每种性格对应的火山音色一次性数字缓存，头部动作结束 2.2 秒后再播放。

绑定、宠物改名、亲密度跨级和 FastAPI 重启都会刷新配置。设备重启后 SQLite 与
运行时文件会恢复当前性格。`GET /api/v1/personality/runtime` 中
`native_applied=true` 表示原生状态上报的 revision 与 FastAPI 配置一致。

摇尾还受两层限制：亲密度至少 1 级，并且维修验收后显式设置
`AI_CAT_ENABLE_TAIL_MOTION=true`；否则不会出现在语音动作白名单中。

## 亲密度

| 等级 | 分值 | 名称 | 主要解锁 |
|---|---:|---|---|
| 0 | 0-19 | 初次相遇 | 基础对话、基础头部动作 |
| 1 | 20-49 | 逐渐熟悉 | 亲近称呼、基础尾部动作、主动问候 |
| 2 | 50-99 | 亲密伙伴 | 组合动作、情绪陪伴 |
| 3 | 100-179 | 深度羁绊 | 庆祝动作、主动陪伴、专属语气 |
| 4 | 180+ | 灵魂伙伴 | 全部动作、最高亲密称呼、纪念日问候 |

默认每日正向增长上限为 20，可通过
`AI_CAT_INTIMACY_DAILY_CAP` 调整。

| 事件 | 分值 | 每日次数上限 |
|---|---:|---:|
| 每日见面 | +3 | 1 |
| 有效对话 | +2 | 5 |
| 触摸反馈 | +1 | 8 |
| 完成互动任务 | +5 | 2 |
| 忽略主动问候 | -1 | 2 |

每个事件必须携带 `request_id`。重复 ID 返回原结果，不重复计分。Local K1 会从
`toy_main` 日志增量导入头部、背部、左右脚和鼻部的实体触摸，并复用相同的幂等、
每日次数与总增长上限。监控从文件末尾开始，不会在服务重启后重放历史触摸。

浏览器仍保留触摸调试按钮；“完成任务”目前由网页人工确认。正式小程序阶段应改为
服务端任务状态签名上报，不能信任客户端直接声明任务完成。

## 动作安全

动作库位于 `src/ai_cat_controller/domain/actions.py`。当前包含 7 个动作，
每个动作具有固定编号、性格范围、最低等级、执行器方向、名义角度、速度和持续时间。
Pydantic 在进程启动时校验以下范围：

- 角度 `0..45` 度；
- 速度 `0.1..1.0`；
- 单步持续时间 `100..3000 ms`。

网页和小程序只能提交 `action_id + request_id`，不能覆盖这些参数。上述范围目前只
用于 Mock 合同验证；映射到 K1 真实电机前，仍需逐动作完成机械限位、堵转、温升和
紧急停止验证。
