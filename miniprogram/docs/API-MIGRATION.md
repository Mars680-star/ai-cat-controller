# 第二阶段 API 对接字段差异清单

## 接入边界

小程序只连接 HTTPS 云端服务，不直连设备局域网，不保存设备密钥、火山引擎密钥或产品凭据。正式鉴权需要新增“微信 `code` → 业务 Session”接口；现有演示登录和浏览器鉴权头不能复用。

2026-10-19开发调试例外：独立device-debug页仅在电脑开发者工具连接
`http://127.0.0.1:8765/v1/status` 本机只读代理，临时令牌仅留内存；K1 IP、
设备密钥只在电脑代理私有配置中。默认业务PetService仍为Mock，ApiPetService
仍未实现。此例外不等于完成本节的正式云端/微信业务接入，手机不能沿用回环地址。

## 能力映射

| 小程序 Service | 云端参考能力 | 第二阶段需确认 |
|---|---|---|
| `login` | 新增微信登录换 Session | Session 续期、注销、隐私授权 |
| `bindPet` | 新增绑定/配网编排 | 二维码格式、设备归属、配网协议、超时 |
| `getPersonalities` / `updatePersonality` | 新增陪伴性格配置 | 返回 5 种可用性格；保存后同步对话风格与宠物摘要 |
| `getDashboard` | `GET /api/v1/pets/{pet_id}/dashboard` | 电量空值、充电器在线与正在充电分离 |
| `getDeviceDetail` | `GET /api/v1/device/status` | 设备编号脱敏、最近在线时间、网络名称 |
| `updateDeviceStatus` | `POST /api/v1/pets/{pet_id}/mock-device-status` | 仅开发环境启用；正式小程序不得模拟设备状态 |
| `reconnectDevice` | 待新增 | 只发起重连，不在客户端伪造在线结果 |
| `getActions` / `executeAction` | `GET/POST /api/v1/pets/{pet_id}/actions...` | 仅使用预设动作 ID；服务端接受 `request_id`。UI预览用executionId/startedAt/deadlineAt/durationMs恢复进度；正式接口需提供权威执行状态，不能以客户端估算宣布硬件完成 |
| `stopAction` | `POST /api/v1/motion/stop` | 幂等停止、冲突错误转用户文案；可选executionId区分停止目标，旧响应不能取消新执行，正式接口需明确其语义 |
| `getIntimacy` / `completeTask` | intimacy / interactions | 等级、每日上限和升级事件由服务端判定 |
| `recordInteraction` | `POST /api/v1/pets/{pet_id}/interactions` | 固定事件枚举和稳定 `request_id`；正式触摸由设备产生 |
| `getGrowth` | `GET /api/v1/pets/{pet_id}/growth` | 六维顺序、数值范围、事件原因与时间 |
| `getDialogStatus` / 控制 | dialog status/wake/interrupt/text | 状态枚举、1 秒增量轮询、打断语义 |
| `listConversations` | dialog-conversations | 游标或 revision，禁止循环全量刷新 |
| `getConversation` | 会话详情 | 消息正序、等待回答占位、可选文本 |
| `updatePet` | settings | 名称 1–20 字、音量 0–100、幂等写入 |
| `updateFollowUpSeconds` | dialog config | 5–120 秒、步长 5 |
| `updateSettings` | pet settings + dialog config | 聚合保存名称、音量和连续聊天；访问密钥不持久化 |
| `submitFeedback` | feedback | 2–500 字、最多 3 图、上传凭证流程 |
| `unbindPet` | unbind | 成长随宠物保留、历史归属和二次确认 |
| `clearMemory` | `POST /api/v1/pets/{pet_id}/memory/clear` | 清除两层记忆但保留完整对话；对话忙时返回冲突 |
| `resetProductData` | `POST /api/v1/admin/reset-product-data` | 仅内部体验环境开放，确认字符串和恢复编号保持一致 |
| `getMijiaStatus` / `getMijiaSpec` | Mijia status/spec | 当前只显示准备状态，不虚构平台型号和实例编号 |
| `prepareMijia` / `removeMijia` | Mijia prepare/remove | 使用稳定 `request_id`，解绑时撤销旧网关标识 |

## 统一模型差异

- 页面消费扁平的 UI 模型；`ApiPetService` 负责拆除后端响应包裹、补齐空值并转换枚举。
- `batteryPercent` 无数据必须为 `null`，不能映射成 `0`。
- `batteryState`、`chargerOnline`、`connectionState` 必须分别映射，不能合并成一个字符串。
- 后端时间统一转换为 ISO 8601；页面再通过 `utils/format.ts` 转换为用户时间。
- HTTP 状态码、后端 `details` 和原始设备错误不得进入页面；Service 转换为可理解的业务错误。
- 所有写请求携带客户端生成的 `request_id`，超时重试复用原 ID。

## 上线前配置

- 微信公众平台配置 HTTPS 合法请求域名、上传域名和下载域名。
- 云端验证微信身份和宠物归属，不能信任客户端传入的用户 ID。
- 密钥只保存在云端密钥管理中；小程序包内不得出现设备 IP、ProductSecret、Bot ID 或 License。
- 增加超时、取消、重试、弱网退避和统一日志追踪，但日志不得包含对话隐私或密钥。
