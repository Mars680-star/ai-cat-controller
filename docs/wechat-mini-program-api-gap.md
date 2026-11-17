# 微信小程序第二阶段接口差异清单

> 更新日期：2026-10-21（下方保留最初的接口映射）  
> 输入：第一阶段 `PetService`、当前 `/api/v1` 产品语义与正式链路安全边界

2026-10-21最新：主页、培养、性格三页已新增本地网关真实数据与受确认的培养互动；
七种固定动作接口也已完成（当前可用5项用户实测正常，另2项仍锁定）。默认预览
PetService继续独立，真实模式走共享微信身份与受限网关，不把下文历史Mock状态
视为当前全部缺口。对话纯读取及独立文字/聆听/打断控制代码均已补齐，控制默认
关闭；普通设置和设备端反馈也已补独立真实代码。用户要求先备份/本地验证，
部署与实机验收延后。危险操作、米家、真实绑定配网及手机HTTPS仍未迁移。
详见[对话控制](wechat-mini-program-dialog-control.md)与[普通设置](wechat-mini-program-preferences.md)。
见[对话只读边界](wechat-mini-program-local-gateway.md#8-2026-10-21-上线-goal对话纯读取)、
[三页接入](wechat-mini-program-live-product-data.md)与[动作说明](wechat-mini-program-motion-control.md)。

2026-10-20产品页整合：运动页已直接用原产品排版调用真实受限接口，移除独立
“使用真实设备”分区；增加当前账号/设备最近20条真实网关意图的只读合同。
仅点头/摇头/停止，其他五业务主入口仍演示；不扩大到尾部、自动动作或生产手机。
见[运动说明](wechat-mini-program-motion-control.md)。以下为各次历史阶段边界。

2026-10-20第四阶段：本地8787已增加仅点头/摇头/停止的受限动作合同，运动页
共享微信身份，独立持久化请求编号；当前实机执行开关关闭，待现场试动确认。
8765仍只读，其他五主入口仍Mock；这不代表正式云端控制已经接入。
见[动作说明](wechat-mini-program-motion-control.md)。下方第三阶段记录是此前边界。

2026-10-20第三阶段：主页和连接页可显式切到真实只读状态，使用共享内存微信会话，
切页重新核权；其他六主入口仍Mock。尚未迁移真实亲密度、成长、历史和任何写操作，
不改变下表的生产缺口。见[主页/连接说明](wechat-mini-program-live-home-connection.md)。

2026-10-20补充：真实微信登录已由开发者工具验证。本机管理端已显式登记当前
设备到该账号，增加完整设备身份pin和授权后的只读状态页；不改K1产品绑定/数据，
不等于正式扫码配网或云端设备通道。默认PetService仍Mock，生产缺口仍适用。
见[第二阶段说明](wechat-mini-program-device-registration.md)。

2026-10-19后续：独立本地登录/权限网关、内存会话client及“网关接入检查”已实现，
但用户尚无服务器/域名，AppSecret待配置，真实微信登录、可信绑定与云端通道尚未
验收。当前只完成本地可测试基础，不改变下表的生产接入缺口，默认PetService仍Mock。
详见[本地网关说明](wechat-mini-program-local-gateway.md)。

2026-10-19补充：已新增电脑开发者工具专用的独立只读联调页，电脑回环代理持有
K1密钥，仅过滤三个固定状态GET，不触发控制或创建业务Session。默认八入口与
ApiPetService的正式接入状态不变，不视为下表的微信云网关/登录/HTTPS已完成。
使用步骤和已验证/待人工验证边界见
[`UI-PREVIEW-GUIDE.md`第7节](../miniprogram/docs/UI-PREVIEW-GUIDE.md#7-电脑开发者工具ai猫只读联调)。

第一阶段页面不直接依赖 FastAPI 响应。第二阶段应新增云端业务服务，由
`ApiPetService` 把云端模型映射成小程序 UI 模型；不能让小程序直连设备局域网地址，
也不能把设备密钥、火山配置或当前浏览器体验鉴权搬进客户端。

## 1. 必须先补的云端能力

| 能力 | 当前状态 | 第二阶段要求 |
|---|---|---|
| 微信登录 | 只有 `auth/mock-login` 和临时 `X-Mock-Session` | 使用 `wx.login` code 换取短期业务 Session，服务端绑定微信主体并支持续期/注销。 |
| 用户与宠物授权 | 当前服务按体验会话查询绑定 | 每个读写接口按业务 Session 校验宠物所有权；解绑后旧 Session 不得读取宠物新主人的数据。 |
| 设备网关 | 当前 Local K1 适合受信任局域网 | 云端到设备使用受控长连接或命令通道；小程序不保存 K1 IP、API Key 或设备凭据。 |
| HTTPS 与域名 | 当前开发服务可使用 HTTP | 配置微信合法请求域名、HTTPS、证书轮换、限流、审计和重放保护。 |
| 真实绑定/配网 | 当前绑定只记录编号、名称和网络名 | 定义二维码签名、设备所有权证明、配网协议、超时恢复和重复绑定冲突语义。 |
| 增量同步 | 会话有 revision，其他页面主要整页查询 | 为主页、动作、成长和设备状态提供轻量 revision/更新时间，避免高频下载全部数据。 |

## 2. `PetService` 到当前接口的映射

| `PetService` | 当前参考接口 | 需要的适配或后端改动 |
|---|---|---|
| `login` / `getSession` | `POST /auth/mock-login` | 替换为正式微信 Session；页面不接触响应包裹、Header 或 token 刷新细节。 |
| `getBoundPet` | `GET /pets` | 云端只返回当前用户有权访问的宠物；映射脱敏编号和头像资源 URL。 |
| `bindPet` | `POST /pets/bind` | 当前请求没有客户端 `request_id`，需增加幂等绑定；真实网络接入不能仅信任客户端网络名。 |
| `getDashboard` | `GET /pets/{pet_id}/dashboard` | 将宠物、亲密度、今日陪伴和最近动态聚合为 `DashboardView`；电量仍需与设备状态合并。 |
| `getDeviceDetail` | `GET /device/status` | 当前返回进程、平台等工程字段；云端必须过滤，只暴露脱敏编号、连接、电池、充电和最近在线。 |
| `reconnectDevice` | 暂无产品接口 | 新增幂等的设备重连/唤醒意图接口；第一阶段只能模拟，不能映射成任意服务操作。 |
| `getActions` | `GET /pets/{pet_id}/actions` | 将服务端解锁和硬件能力映射为 `available/locked/unavailable/running`，客户端不自行推断安全门。 |
| `executeAction` | `POST /pets/{pet_id}/actions/{action_id}/execute` | 已接受 `request_id`；保持只提交动作 ID，不能回退到旧动作接口的强度和时长字段。 |
| `stopAction` | `POST /motion/stop` | 当前是设备级接口；云端需按宠物授权并接受稳定 `request_id`，重复停止返回同一业务结果。 |
| `getActionHistory` | `GET /pets/{pet_id}/actions/executions` | 增加游标或 revision，移动端默认只取最近 20 条。 |
| `getIntimacy` | `GET /pets/{pet_id}/intimacy` | 映射等级区间、每日增长、当前解锁和下一等级预告；升级事实必须由服务端返回。 |
| `completeTask` | `POST /pets/{pet_id}/interactions` | 固定发送 `event_type=completed_task` 和 `request_id`；正式任务证明机制落地前保留风险标识。 |
| `getGrowth` | `GET /pets/{pet_id}/growth` | 普通用户需要六维精确值、五段倾向、active 标签、行为画像和脱敏原因；不暴露 Debug 证据字段。 |
| `getDialogStatus` | `GET /dialog/status` | 当前状态为设备级；云端按宠物路由并映射为六种 UI phase，保留 sequence/revision 防止旧状态覆盖。 |
| `startListening` | `POST /dialog/wake` | 已有 `request_id`；云端要区分离线、启动中、忙碌和已进入聆听。 |
| `interruptDialog` | `POST /dialog/interrupt` | 已有 `request_id`；重复打断应幂等，页面只显示用户可理解的结果。 |
| `sendText` | `POST /dialog/text` | 已接受 `content + request_id`；成功只代表设备收到，最终交付状态从会话增量同步。 |
| `listConversations` | `GET /pets/{pet_id}/dialog-conversations` | 当前使用 `limit` 和 revision；`ApiPetService` 可先适配，长期应提供稳定游标防止翻页重复/漏项。 |
| `getConversation` | `GET /pets/{pet_id}/dialog-conversations/{id}` | 消息顺序已经匹配；云端继续按用户隔离历史并保留等待回答状态。 |
| `getProfileSettings` | 宠物设置 + `GET /dialog/config` | 聚合名称、音量和连续聊天时间；不把声卡或输出设备信息返回客户端。 |
| `updatePet` | `PATCH /pets/{pet_id}/settings` | 当前请求没有 `request_id`，需增加幂等写入或版本条件；音量仍只接受 `0..100`。 |
| `updateFollowUpSeconds` | `PATCH /dialog/config` | 当前请求没有 `request_id`，需增加幂等键并按宠物/用户授权，范围保持 `5..120`、步长 5。 |
| `submitFeedback` | `POST /pets/{pet_id}/feedback` | 当前没有 `request_id`，字段名为 `content`；云端需映射 `description`，增加幂等和最多 3 图的受控上传。 |
| `unbindPet` | `POST /pets/{pet_id}/unbind` | 增加稳定 `request_id`、再认证/确认策略和审计；保证宠物成长保留但旧用户历史不泄露。 |

## 3. 通用合同要求

- `ApiPetService` 统一解开 `{success,message,data}`，页面不能解析 HTTP 状态码或
  `details`；错误统一映射为 `offline/conflict/validation/not_found/send_failed/unavailable`。
- 所有写操作接受客户端生成的稳定 `request_id`。网络超时重试必须复用原 ID，服务端
  保存足够长的幂等结果，不能只在单进程内去重。
- 时间字段统一 ISO 8601 并带时区；列表翻页使用游标，不能依赖会变化的数组下标。
- 电量不可用使用 `null + unavailable`，不能用 `0%` 代替；`charger_online` 与
  `battery_state=charging` 分开保存。
- 页面只接收动作预设和用户文案。GPIO、角度、速度、进程、服务名、设备路径、内部
  错误和鉴权材料都必须在云端/设备边界内消化。
- 解绑、反馈、设置、动作、任务和对话控制记录用户、宠物、请求 ID、结果与时间，日志
  不记录对话全文之外的密钥或设备凭据。

## 4. 上线 goal 推进清单（2026-10-21）

目标是完整产品上线，不以本地8787可访问或自动测试通过代替发布。按照可本地验证
的功能逐阶段接入；生产设施、真实设备写入和最终发布授权分别核实。当前运行仍
是电脑开发者工具/回环网关，不自动公开监听，也不购买服务器或提交微信审核。

| 工作包 | 当前边界 | 上线门槛 |
| --- | --- | --- |
| 登录、设备授权、状态 | 本机真实微信链路已验证；显式管理端授权 | 正式可信绑定/解绑、撤权隔离、多设备路由、会话到期恢复 |
| 主页、培养、性格 | 真实数据和受确认互动已接入；原预览保留 | GUI/弱网/长时间与真实培养验收，未知结果不自动补发 |
| 七种固定动作 | 5种可用动作用户实测正常，2种仍自然锁定；总开关/无尾源码已修复，本地测试通过 | 锁定不绕过；新安全门配套设备部署/原生构建与异常场景实机验收 |
| 对话和记录 | 专用纯读及独立文字/聆听/打断代码已补，持久去重与未知回执保护；控制默认关闭，用户延后部署 | 新接口配套部署、真实云端/扬声器/打断及弱网实机验收；不以Mock通过代替 |
| 设置与反馈 | 普通设置/设备端文字反馈本地代码已补；双默认关闭、持久分步回执，尚未部署 | 实际音量与存储值验收、部分/未知结果和弱网真机测试；不冒充人工客服或云反馈 |
| 记忆与危险操作 | 隐私基础、跨进程租约、唤醒词/直接调用及嵌套旧票据保护已补本地源码；小程序危险接口仍未接 | 旧二进制/全参与者活核验、故障恢复、全库范围/生产授权、独立确认及撤权后回执；[数据范围](product-data-lifecycle-safety.md)、[进程协议](process-maintenance-protocol.md) |
| 米家 | 原网页只有接入前协议，未完成平台账号与产品SPEC接入 | 取得正式产品模型/协议与平台权限，不编造MIoT实例编号 |
| 运行与生产通道 | 本机临时user服务，关闭WSL后不能视为在线 | 常驻运维、受控设备通道、HTTPS/合法域名、备份回滚和告警 |
| 版本与质量 | 脏工作树保留，无提交/推送；Python全量2002通过、小程序回归通过；离线Python源码候选包已实际构建/核验/暂存 | [候选包](offline-release-candidate.md)不是固件/运行证明；依赖锁定、原生构建、受控启动与SDK/真机/生产验收仍待完成 |
| 微信发布 | 尚无本轮账号资质/服务器/域名确认 | 管理后台当前要求、隐私声明、体验版手机验收、审核及发布授权 |

正式网络配置以微信[网络能力文档](https://developers.weixin.qq.com/miniprogram/dev/framework/ability/network.html)
为准；2026-10-21读取官方页面确认服务器域名需HTTPS、不能使用localhost或普通
IP地址。当前两个回环代理不能直接当作上线域名，小程序不携带AppSecret或设备密钥。

本阶段不自动签到、提交文字、发声、执行动作、清记忆或重置产品数据。需要新部署
设备服务或实际业务写验收时，先明确目标及恢复点；软件测试使用临时SQLite/Mock。

## 5. 推荐接入顺序

1. 微信 Session、宠物所有权和 HTTPS；
2. 只读主页、设备、亲密度、成长、动作和历史；
3. 幂等动作、任务、设置、反馈和解绑；
4. 对话状态、文字发送、打断和会话增量同步；
5. 真实扫码绑定、设备网关、弱网恢复和审计告警。
