# 系统交互流程

## 当前产品 Mock

```text
电脑/手机浏览器
   │  HTTP JSON，X-Mock-Session，可选 X-API-Key
   ▼
FastAPI 产品 API
   ├── SQLite：用户、设备、宠物、性格、亲密度、历史、动作记录
   ├── ProductMockService：绑定、盲盒、成长、对话模板、设置
   └── MotionService：串行、超时、冷却、停止、结果跟踪
          │
          ▼
      MockAiCatAdapter
```

浏览器每 5 秒刷新一次主页状态。关键业务数据写入
`AI_CAT_DATA_PATH` 指定的 SQLite 文件，服务重启后恢复；Mock 登录会话只保存在
进程内，服务重启后浏览器使用原登录码重新获取会话。

Mock 对话回复仍由本地模板生成，不调用火山引擎，也不播放声音；其中的
`voice_id=volcengine_console` 是统一音色来源标记。Local K1 模式下，FastAPI 会
把当前宠物的提示词和动作规则同步给已运行的火山会话，并由原生进程执行最终动作
白名单检查；TTS 发音人只在火山引擎控制台设置。

## 目标生产链路

```text
微信小程序 ── HTTPS REST / WSS ── 云端业务服务 ── 云数据库
                                      │
                                      │ 设备主动建立的 TLS 长连接
                                      ▼
                                  K1 设备代理
                                      ├── 安全动作执行器
                                      └── 火山对话 SDK ── WSS ── 火山 AI 网关
```

- 小程序到云端：HTTPS REST 处理登录、绑定、查询和设置；WSS 或受控消息通道推送
  设备状态、动作结果和亲密度变化。
- K1 到云端：设备主动建立 TLS 长连接，避免把板端端口暴露到公网。通信协议可在
  MQTT over TLS 与 WSS 中二选一；首版优先 WSS，以复用现有 WebSocket 经验。
- K1 到火山引擎：继续使用已验证的 ConversationalAI WebSocket SDK。Function
  Calling 只能映射到本地固定动作编号，不能接收任意电机参数或 Shell 命令。
- 云端存储：生产环境把 SQLite 替换为事务数据库，并为事件、对话和动作结果设置
  设备、用户和宠物联合索引。

## 标识与隔离

| 标识 | 当前 Mock | 生产要求 |
|---|---|---|
| `user_id` | 根据 Mock 登录码稳定生成，前缀 `usr_` | 微信 `openid/unionid` 映射后的内部 ID |
| `device_id` | 根据设备序列号稳定生成，前缀 `dev_` | 服务端登记的硬件身份，不能由客户端声明归属 |
| `pet_id` | 与设备实例稳定绑定，前缀 `pet_` | 一只实体宠物一个实例，解绑不改变 |
| `session_token` | 随机生成，仅进程内有效 | 短期访问令牌和可撤销刷新令牌 |
| `request_id` | 客户端生成，宠物范围内幂等 | 全链路透传并设置唯一约束 |
| `conversation_id` | 每轮 Mock 对话生成 | 火山会话 ID 与内部会话 ID建立映射 |

宠物性格和亲密度跟随 `pet_id`。对话、互动事件和动作记录同时保存
`pet_id + user_id`；设备转移给新用户后，新用户不能读取旧用户的对话记录。

## 数据一致性

- 绑定和亲密度变更使用 SQLite 事务。
- 同一 `pet_id + request_id` 的互动和动作只处理一次。
- 正向亲密度按 UTC 自然日计算次数与总增长上限，扣减后不低于 0。
- 动作先写 `pending`，调度成功后写 `running`，最终写
  `completed/cancelled/failed/timed_out`。
- 设备离线时动作接口返回 `503`；动作未解锁或执行冲突返回 `409`。
- 生产环境需要增加设备上报序列号、服务端事件版本号和断线重放队列。
