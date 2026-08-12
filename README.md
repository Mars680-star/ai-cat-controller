# AI Cat Controller

SpaceMIT K1 AI 猫的独立控制仓库。当前提供 FastAPI 产品体验 Mock、浏览器端
小程序流程替身、完整 Mock 适配器和受限的 Local K1 真机控制；未完成硬件验收
的尾部动作默认关闭。

新电脑或新的 Codex 会话请先读取 [`CODEX_HANDOFF.md`](CODEX_HANDOFF.md)。从
GitHub 克隆后可用以下命令完成开发环境安装、全量测试和 Mock 启动：

```bash
./scripts/bootstrap_dev.sh
./scripts/run_mock.sh
```

## 更新记录

### 2026-08-12

修改人：Mars

- 新增亲密度升级庆祝：每日见面、有效对话、实体触摸或完成任务使亲密度跨级时，
  自动等待语音会话和当前电机动作结束，再执行固定 `celebration_combo`；动作完成后
  本地播放“太棒啦，我们的羁绊又更深了一步！”。重复请求不会重复庆祝，触摸升级
  时庆祝动作优先于普通触摸反馈，网页手动调用仍遵守原等级解锁规则。功能由默认
  关闭的 `AI_CAT_ENABLE_LEVEL_UP_CELEBRATION` 控制，本地语音不启动火山云端；新增
  1 个控制台当前音色的共享 WAV。采集工具会先避开云端启动欢迎语，缓存语音固定
  走实体扬声器，避免云端关闭时虚拟回声消除输出静默；现场复听文案正确，完整
  自动化测试为 `235 passed`。部署恢复点为
  `/root/ai-cat-backups/before-growth-v1-20260812T050410Z-3417`。
- 修复每日见面未增加亲密度：用户当天首次登录已有宠物或完成新绑定时自动登记
  `daily_check_in +3`，手动按钮保留为失败重试入口，完成后显示“今日已见面”。服务端
  使用“宠物 + K1 本地日期”生成幂等键，并按本地自然日统计次数和每日增长上限，
  避免 UTC 跨日误判、重复点击加分以及重复生成成长事件。K1 实测亲密度从 `73`
  增至 `76`，第二个不同请求号未重复加分；部署恢复点为
  `/root/ai-cat-backups/before-daily-meeting-fix-20260812T045028Z`，自动化测试为
  `230 passed`。
- 成长人格页的六维倾向不再只显示分段进度：好奇、共情、求知、活力、淘气和自律
  均显示实际 `0–100` 分值，保留两位小数并与倾向等级同时呈现。普通用户接口固定
  返回分值，内部事件增量、标签证据和成长加速入口仍受原有调试权限保护。
- AI 播放回答期间由单次轻动作升级为持续的双节奏动作：头部使用已验收的低幅度
  侧头/点头，每 `7–10` 秒尝试一次；尾部从回答约 2 秒后开始，每 `3–4.5` 秒尝试
  一次。动作忙碌时直接跳过而不排队，回答结束、进入聆听或状态失效时立即清空本轮
  调度，避免在用户说话时或回答结束后补动作。尾部节奏有独立开关，且仍要求设备的
  `AI_CAT_ENABLE_TAIL_MOTION=true` 安全门已开放。K1 长回答现场验收中头部执行 4 次、
  尾部执行 9 次，回答结束后未继续动作，体验符合预期。部署恢复点为
  `/root/ai-cat-backups/before-repeating-dialog-motion-20260812T042426Z`，自动化测试为
  `229 passed`。

### 2026-08-11

修改人：Mars

- 为高频维护入口补充文件头说明，覆盖应用装配、环境配置、性格和亲密度规则、动作
  安全边界、触摸与成长服务、网页结构、本地唤醒、实时对话和原生电机程序。注释明确
  源码职责、联动文件、K1 部署位置及不可绕过的命令白名单和动作互斥约束，不改变
  现有运行行为。
- 为长期成长人格 V1 增加默认关闭的总开关
  `AI_CAT_ENABLE_GROWTH_PERSONALITY_V1`。关闭时不生成成长事件、不授予标签、不运行
  Debug 养成，也不把成长指令写入火山 Prompt；已有成长数据保持只读，基础性格、
  亲密度、语音、动作和对话历史继续工作。培养页同步显示开关状态。
- 增加 K1 发布备份和回滚工具：备份 SQLite、代码、环境、运行时数据、systemd
  单元及服务状态，使用 SHA-256 和完整标记拒绝不完整备份；回滚前还会保存当前
  状态，支持 `--check` 只校验模式。完整流程见
  `docs/growth-v1-deployment.md`。
- 增加五部位触摸真机验收脚本，按设备序列号核对厂商原始标签与逻辑部位；头、鼻、
  背单次触发，左右爪按 3 秒内两次触发，同时要求人工确认动作和固定短语。
- 真机验收发现厂商触摸服务会为持续接触连续上报多条记录，新增按实体部位的
  `1.5` 秒尾随防抖；连续上报会延长静默窗口，只生成一次亲密度和成长事件，动作
  与语音原有冷却保持独立。
- 成长 V1 发布前已在设备 `7c2b63fd4a128` 创建并校验完整恢复点
  `/root/ai-cat-backups/before-growth-v1-20260811T101234Z-5724`。用户明确跳过本轮
  剩余实体触摸测试后，板端已设置
  `AI_CAT_ENABLE_GROWTH_PERSONALITY_V1=true`，全部 Debug 开关保持关闭。鼻部仍属于
  未完成硬件验收，不因启用软件功能而标记为通过。
- 实体头部触摸已通过现场验收：一次连续触摸产生的 4 条厂商原始记录经防抖后只
  导入 1 个业务事件，并正确点头和播放“摸摸头，好舒服呀。”。鼻部 `GPIO113`
  经厂商日志和独立 60 秒 GPIO 边沿监听均无输入，暂记为硬件传感器/排线待检。
  背部和左右爪随后由用户现场确认触摸输入与功能正常；左右爪继续保留 3 秒内两次
  触摸的防误触规则。
- 成长 V1 真机软件验收已覆盖完成任务、真实火山文字问答、来源 ID 幂等和运行时
  更新。修复原生对话在 `session.created` 前发送首个请求导致欢迎语抢占的问题，
  首次文字提问现在会等待云端会话真正创建后再发送。
- 自动动作及本地播音会造成背部传感器回灌。现已在受控电机运行、动作结束后的
  1 秒噪声收敛窗和本地语音标记存在期间丢弃触摸输入；两轮自动“点头 + 本地短句”
  回归均未新增触摸成长。已精确清理此前 6 条确认的误触数据，清理前备份为
  `/root/ai-cat-backups/before-growth-v1-20260811T103223Z-7833`，校验和及数据库
  完整性均通过。
- 修复发布备份停止 FastAPI 后没有恢复被连带停止的 `toy_motor.service`；备份脚本
  现在恢复备份前全部 active 服务。自动化测试更新为 `219 passed`，同时覆盖开关
  关闭兼容性、首次云会话门控、本地播音/电机误触屏蔽和备份服务恢复。
- 重新设计浏览器端产品界面：培养页聚焦亲密度、今日互动和等级解锁，动作记录与
  互动记录默认折叠；长期性格成长拆分为独立导航页，集中展示成长标签、六维倾向、
  行为画像和最近成长。连接、主页、运动、对话与设置同步统一为白色与暖黄色主视觉，
  手机端 7 个入口无需横向滑动即可全部显示。
- 页面可见文案已统一改为面向用户的表达，不再显示“真机、Mock、调试、SDK、
  License”等工程术语；自动化测试增加禁用词保护。桌面 `1440x1000` 与手机
  `390x844` 已通过真实 Chromium 登录、绑定、互动、记录展开和页面切换检查，整页
  无横向溢出。本次 K1 发布恢复点为
  `/root/ai-cat-backups/before-growth-v1-20260811T135600Z-11784`。
- 修复设置页音量滑条被 5 秒设备刷新覆盖的问题：首次加载并行读取保存资料和设备
  实际音量，拖动后保持“待保存”状态，保存成功前禁止旧请求回写，保存后再恢复实时
  同步。Chromium 已验证设备持续上报 `100%` 时，将滑条拖到 `60%` 并等待超过一个
  刷新周期仍保持 `60%`。发布恢复点为
  `/root/ai-cat-backups/before-growth-v1-20260811T140329Z-12446`。
- 性格页“最近成长”改为默认折叠，摘要实时显示记录数量；手机端 Chromium 已验证
  默认收起、数量更新及展开后的完整记录。动作记录、亲密度互动和最近成长现在使用
  一致的折叠交互。
- 本地唤醒服务新增云端无关的命名打断词：对话状态仅在允许打断时监听“小安停下”，
  并兼容“小安别说了、小安安静、小安暂停”，命中后复用固定 `SIGUSR2`，不会把
  打断词提交给模型。普通收音和连续追问阶段继续释放第二路录音，通用词“停”不会
  触发，降低扬声器回声误停风险。K1 已连续完成两轮实体打断测试，长回答均可重复
  停止；受本地录音窗口和离线识别耗时影响，当前从说完口令到停止约 3 秒。旧二进制
  恢复点为 `/root/ai-cat-backups/before-wake-interrupt-20260811T2211`。
- AI 回答期间新增低幅度轻侧头或轻点头，每次回答最多执行一次；聆听、思考、连续
  追问、触摸播报和电机忙碌期间不触发。两种固定轨迹均已在 K1 现场确认自然，动作
  继续经过既有互斥锁、冷却和固定命令白名单，网页参数不能转换为原始电机参数。
  实际语音回答联动已确认按回答状态各执行一次，部署恢复点为
  `/root/ai-cat-backups/before-conversation-motion-20260811T145519Z`。自动化测试为
  `227 passed`。

### 2026-08-10

修改人：Mars

- 新增长期成长人格 V1：保留出生性格与亲密度的独立含义，在真实完整对话、实体
  触摸、完成任务和每日见面之后生成幂等 `GrowthEvent`，累计好奇、共情、求知、
  活力、淘气、自律 6 个长期属性。成长速度受初始性格偏置、参与度、话题新颖度和
  重复衰减共同控制，任一成长子系统异常不会中断原有对话或触摸链路。
- 增加小小探索者、探索家、小学者、学霸猫、贴心伙伴、小调皮、自律搭子、嘴硬
  心软 8 个配置化成长标签。高级标签保留并替换低级标签历史；行为画像只在属性
  跨层级或标签变化时改变 revision，并合入现有 `personality-runtime.json` 和
  火山 `session.update` Prompt，不新增第二套运行时。
- SQLite 新增 `growth_attributes`、`growth_events`、`growth_tags`，成长跟随
  `pet_id`，设备换主后保留人格结果但不向新主人暴露旧主人的成长事件详情。培养页
  新增成长倾向、标签、最近成长和行为画像；Mock 提供受控快速养成，Local K1 默认
  禁止 Debug 写入。此次只完成软件层和 Mock 自动验证，尚未部署或执行真机验收。
- 成长人格实现完成后执行全量编译与测试，结果为 `201 passed`；新增测试覆盖成长
  偏置、重复衰减、属性上限、非法数据原子性、标签证据与升级、设备换主隐私、
  双宠物隔离、完整真机字幕轮次接入、runtime Prompt 和 Debug 权限边界。
- 网页音量设置接入 K1 系统 PulseAudio：只允许对固定
  `@DEFAULT_SINK@` 设置 `0..100` 音量，`0` 自动静音、非零自动取消静音；真机
  设置成功后才写入 SQLite，网页每 5 秒同步实际输出音量。
- Local K1 启动实体触摸监控，从现有 `toy_main` 日志末尾增量识别头部、背部、
  左右脚和鼻部的长短触摸。事件通过设备序列号关联已绑定宠物，按 `+1`、每日最多
  8 次及总增长上限计入亲密度，并以稳定请求 ID 防止重复计分。
- 亲密度每日正向总增长上限由 `20` 调整为 `50`；触摸、任务、有效对话等单项
  每日次数限制保持不变。
- 培养页在打开时每 5 秒同步亲密度；网页“完成任务”按钮继续按 `+5`、每日最多
  2 次计分。实体触摸动作已接入统一安全调度器：头部/背部触发点头，鼻部/左右脚
  触发摇头；对话期间、电机忙或 3 秒触摸冷却内只计亲密度，不启动新动作。
- 增加 5 条统一音色的共享本地触摸短语：动作完成后按头部、背部、鼻部、左脚和
  右脚播放固定短句。播放不启动火山云端，使用本地语音标记
  暂停唤醒收音，并与自主短语互斥，避免回声自唤醒或两段语音重叠。
- 触摸接线改为按设备序列号选择：当前设备 `7c2b63fd4a128` 使用厂商正常标签；
  旧设备 `7c2b63fd4a138` 才交换 `nose`/`head` 与 `back`/`left_foot`，右爪不变。
  事件同时保留 `hardware_sensor` 和 `sensor_mapping`，便于后续检修接线。
- 触摸语音使用按部位独立的 3 秒防刷间隔，并与电机冷却解耦：同一部位连续触摸
  不会重复播报，但头部后紧接鼻部等不同部位仍会串行播放正确短语；电机处于冷却
  时只跳过动作，不再同时丢失语音反馈。
- 为防止左右爪误触，实体左爪和右爪分别要求在 3 秒内连续触摸 2 次，才生成一次
  业务触摸事件并增加亲密度、执行动作和播放短语；头部、鼻部和背部仍保持单次
  触发。次数和窗口可通过 `AI_CAT_PAW_TOUCH_CONFIRMATION_COUNT`、
  `AI_CAT_PAW_TOUCH_CONFIRMATION_WINDOW_SECONDS` 调整。
- 记录第二台 K1（序列号 `7c2b63fd4a128`）原厂顺滑动作参数：头部左右、上下和
  尾部均使用归一化目标 `180 -> 0 -> 90`、速度级别 `3`、极限位停留 `100 ms`；
  新增显式 `k1_vendor_smooth` 配置。默认 `legacy_safe` 不变，避免影响旧设备。
  GPIO、电机编号、有效摆幅和其他原厂动作见 `docs/k1-motion-profiles.md`。
- FastAPI systemd 模板改用 `pulse-access` 主组和固定 PulseAudio socket；新增
  音量命令白名单、状态解析、失败处理、触摸日志增量/半行处理及部署约束测试。
- 完成双眼显示能力调查：板端 `toy_ui 1.1.10` 可通过 DDS `CUSTOM_MEDIA` 显示
  `932 x 466` GIF/PNG，但仓库缺少对应版本的 `ToyCommand` IDL。本阶段不替换
  现有 UI，后续取得准确 DDS 契约后再开放预设图片接口。详见
  `docs/eye-display-investigation.md`。
- K1 真机验证网页 API 将实际音量从 `100%` 改为 `37%` 后恢复 `100%`，状态读取、
  静音状态和 SQLite 持久值一致；实体右脚、鼻部和背部触摸共 4 次均被导入，
  亲密度由 `32` 增加到 `36`。部署前备份位于
  `/root/ai-cat-backups/before-volume-touch-20260810-1315/`。
- 本地编译检查通过，自动测试更新为 `177 passed`。
- 第二台 K1（序列号 `7c2b63fd4a128`）已完成 FastAPI 控制服务、固定安全电机
  应用和 `k1_vendor_smooth` 动作配置部署；头部左右、头部上下和尾部原厂轨迹已
  逐项执行验证。火山原生对话程序已在该板完成 RISC-V 编译，PulseAudio 与对话
  systemd 单元已安装但保持停用，待设备 License 和本地唤醒资源就绪后再启用，
  当前不会与原厂语音服务争用声卡。
- 新设备尾部验收通过后恢复网页“开心摇尾”动作：全部 5 种性格均在亲密度 Lv.1
  解锁，Local K1 仍必须显式开启尾部硬件开关，并只执行固定安全轨迹；语音主动
  调用摇尾继续遵守各性格的 Function Calling 白名单。
- 修复产品动作参数只展示但未真正传到 K1 的问题：`k1_vendor_smooth` 新增
  `proud_pose`、`quiet_companion`、`greeting_combo` 和 `celebration_combo` 四个
  固定板端预设，FastAPI 只能通过命令白名单选择预设名。骄傲转身现为侧向保持、
  摇尾一次、缓慢回中，已通过现场验收；安静陪伴改为速度 1 的小幅慢点头并通过
  现场验收。见面问候和升级庆祝按描述组合动作，二者代码、RISC-V 编译和接口检查
  通过，仍待逐项现场动作验收。旧设备 `legacy_safe` 自动回退原固定动作，不使用
  新轨迹。
- 新增 `AI_CAT_DEBUG_UNLOCK_ALL_ACTIONS` 调试开关，第二台 K1 已临时启用，可在网页
  测试全部 7 个固定动作。该开关仅跳过性格和亲密度解锁条件，不绕过硬件能力门、
  命令白名单、动作互斥、冷却、超时或停止保护；默认关闭，不得用于正式产品环境。
- 新增 `AI_CAT_DEBUG_UNLIMITED_TOUCH_INTIMACY` 调试开关，第二台 K1 已临时启用：
  `touch` 事件不受每日 8 次和每日总增长上限限制，方便连续拍摄培养功能。重复事件
  幂等、触摸动作冷却及任务/对话等其他培养限制保持不变；正式环境默认关闭。
- 设置页新增“一键格式化体验数据”：操作前自动生成 SQLite 与运行时文件备份，
  随后清除体验账号、设备绑定、性格、亲密度、动作、对话和反馈数据，并使旧网页
  会话立即失效。火山鉴权缓存、License、厂商 SDK、模型和 systemd 配置均不删除；
  功能受 API Key、体验会话、固定确认词及 `AI_CAT_ENABLE_PRODUCT_DATA_RESET` 开关
  共同保护，默认关闭。备份保存在产品数据库同级的 `backups/` 目录。
- 取消 5 种性格各自覆盖 TTS 音色：性格继续控制提示词、称呼、主动短语文案和
  动作规则，`session.update` 只同步 `LLMConfig.SystemMessages`，不再发送
  `TTSConfig`。云端回答、文字播报和重新采集的本地 WAV 统一使用火山引擎控制台
  当前音色；运行时的 `voice_type=volcengine_console` 只是来源标记，不是实际
  发音人 ID。
- 已在 K1 上以控制台当前音色重新生成 15 条性格主动短语和 5 条共享触摸短语，
  共 20 个本地 WAV。控制台以后更换音色时，需要重新运行
  `tools/capture_cloud_phrase_assets.py --kind all` 才能同步更新离线语音。
- 恢复真机对话历史：火山智能体必须开启“字幕显示”，运行时性格更新持续携带
  `SubtitleConfig(DisableRTSSubtitle=false, SubtitleMode=1)`，防止性格切换覆盖字幕
  回调。FastAPI 现在启动时即按设备序列号和当前绑定关系幂等导入
  `dialog-events.jsonl`，不再要求浏览器历史页保持打开；文字和真实语音问答均已在
  新 K1 上验证为同一会话内的用户、助手双向记录。

### 2026-08-07

修改人：Mars

- 新增根目录 `CODEX_HANDOFF.md`，集中记录仓库入口、架构、当前真机服务模型、
  已完成功能、外部厂商依赖、安全边界、未验收范围和下一阶段优先级，供新电脑及
  新 Codex 会话直接接续开发。
- 新增 `scripts/bootstrap_dev.sh` 和 `scripts/run_mock.sh`：全新克隆后可创建 Python
  环境、安装依赖、执行全量测试，并以不会操作 K1 硬件的 Mock 模式启动服务。
- 本地复验 `136 passed`，并确认 `/health`、`/control` 和 `/docs` 均正常返回。

### 2026-08-04

修改人：Mars

- 初始实现将 5 种持久化性格映射到独立火山音色和系统提示词；当前宠物绑定、
  改名、亲密度升级及服务启动时会生成固定的
  `personality-runtime.json`，原生对话进程通过 `session.update` 在空闲阶段应用。
- 语音 Function Calling 增加独立的性格动作触发表和板端白名单，网页动作库继续
  按性格与亲密度解锁；安全自主行为改为读取同一运行时性格的动作权重和主动短语。
  尾部动作还需同时满足亲密度 1 级、性格允许和硬件开关开启。
- 增加 `/api/v1/personality/runtime` 同步状态接口；原生状态上报当前
  `personality_id`、配置 revision 和 `voice_type`。K1 已编译部署，当前
  “元气探险家”revision 与原生状态一致，火山网关返回 `session.updated`。
- 原硬件产品的语音 License 用尽后，新建硬件产品已通过 ProductKey、
  ProductSecret、实例 ID 和 Bot ID 在同一 K1 上完成动态注册；新的设备密钥已按
  `0600` 权限独立缓存，旧产品密钥保留用于回滚，配置和凭据备份不进入 Git。
- 新产品首次测试暴露出动态性格音色只更新 `voice_type`、未携带 TTS 服务信息的
  问题，网关返回 `code=1005004: resource ID is mismatched with speaker related
  resource`。性格运行时现固定携带 `Provider=volcano_bidirection` 和
  `ResourceId=volc.service_type.10029`，并验证“撒娇学妹”音色能完成 TTS 播放。
  该动态音色覆盖方案已于 2026-08-10 移除，当前统一使用控制台音色。
- 为降低语音时长消耗，云端对话进程改为按需启动：
  本地“小安小安”唤醒服务常驻，匹配后才启动固定的
  `volc-conv-ai.service`，等待云端与人格配置就绪后自动进入聆听。
- 网页文字提问和显式播报同样会按需启动云端；FastAPI 只允许执行固定的
  `systemctl --no-block start volc-conv-ai.service`，并在原生状态进入 `ready`
  后才发送 `SIGHUP` 或 `SIGUSR1`，避免启动阶段丢请求。
- 连续对话及免唤醒追问结束后，云端再空闲 90 秒会正常断开；语音单元改为
  `Restart=on-failure` 且不再开机启用，异常断线最多快速重试 3 次。本地唤醒、
  FastAPI、PulseAudio 和安全自主动作继续常驻。
- 安全自主头部动作可在云端离线时继续执行；后台随机云端短语默认关闭，避免
  自主行为重新产生云端会话。5 种性格各有 3 句固定本地 WAV，K1 每 3 分钟最多
  随机播放 1 句；播放只使用 PulseAudio，不启动火山服务。播放期间本地唤醒自动
  暂停收音，结束后恢复。这批 15 个 WAV 最初按性格音色缓存；2026-08-10 已按
  控制台统一音色重新生成。自主头部动作完成 2.2 秒后再播放，避免电机噪声覆盖
  语音。
- K1 上已验证 RISC-V 对话与唤醒程序编译、网页文字按需启动、云端错误最多重试
  3 次，以及离线自主头部动作不启动云端。新产品鉴权、模型文字回复、动态 TTS
  音色和完整回答结束事件均已通过；实测回答完成后云端按 90 秒空闲策略正常退出，
  本地唤醒服务保持运行。
- 自动测试更新为 `136 passed`。产品重绑回滚包位于
  `/root/ai-cat-backups/before-product-rebind-6a715196-20260804/rebind-backup.tar.gz`；
  TTS 修复前的服务文件和运行时配置位于
  `/root/ai-cat-backups/before-tts-resource-fix-20260804/`。

### 2026-08-03

修改人：Mars

- 修复 K1 长时间空闲后网页和 SSH 暂时不可达的问题：确认 FastAPI 服务与
  `0.0.0.0:8000` 监听正常，将当前 NetworkManager 连接的 Wi-Fi 省电设置为
  `disable`，并立即关闭 `wlan0` power save。
- 电脑直连验证 `/health`、`/control` 及页面静态资源均返回 `200`；K1 地址仍为
  `192.168.1.112`。关闭 Wi-Fi 省电会略微增加待机功耗。
- 对话历史改为按会话展示：网页左侧显示会话摘要，右侧显示按时间排列的用户与
  AI 消息详情；会话页每秒同步一次真机最终字幕，并标记“等待回答”或“已完成”。
- 增加会话汇总、单会话详情和同步版本 API；真机字幕仍按事件 ID 幂等导入，旧版
  重复会话编号会按真实问答轮次自动拆分，原生程序重启后的新编号包含启动时间。
- 新增部分问答实时补全、重复原生编号拆分、会话详情和页面结构测试；完整自动
  测试结果为 `102 passed`。
- K1 部署将当前宠物的占位设备序列号迁移为板端真实序列号，保留原宠物 ID、
  性格、亲密度和 Mock 记录；迁移前 SQLite 已保存一致性备份，历史真机字幕随后
  成功导入。
- 增加网页文字提问：FastAPI 校验后将问题原子写入固定请求文件，仅通过白名单
  `SIGHUP` 通知原生进程；原生进程以 `input_text` 送入同一个火山引擎会话，
  回答继续由真机扬声器播放并实时进入会话历史。对话繁忙时拒绝重复提交。
- K1 实测文字问题“一加一等于多少”无需唤醒即完成云端回答，用户文本与回答
  “一加一等于二”进入同一会话，随后正常开放免唤醒追问窗口。
- 将原 `toy_motor.service` 服务入口替换为本项目安全自主行为进程并恢复开机启动：
  默认开机等待 45 秒，之后每 60–120 秒只从已验收的点头、摇头中随机选择；
  尾部硬件仍保持禁用。
- 自主动作以一定概率同时调用火山 `input_tts` 直接播报固定短语，不经过大模型；
  仅在对话状态为 `ready` 时执行，唤醒、聆听、思考、回答和追问期间均跳过，
  云端使用低优先级避免与用户交互竞争。
- 原厂 `/usr/bin/toy_control` 仍不启用；自主动作统一经过 FastAPI 调度和
  `/run/ai-cat/motor.lock`，避免恢复此前的双电机控制源与摇头抽动问题。
- K1 实测单次点头/摇头、短语 `input_tts`、播放队列排空、状态恢复和常驻服务
  首轮调度均正常；`toy_motor.service` 为 `active/enabled`，完整自动测试为
  `114 passed`。

### 2026-08-02

修改人：Mars

- 接入 K1 标准 Linux `power_supply` 电源接口，读取 `cw-bat` 电量、状态、
  电池存在性和电压，以及 `ip2317-charger` 充电器在线状态。
- `/api/v1/device/status` 增加经过范围校验的真实电池字段；接口缺失或数据非法
  时返回明确的不可用状态，不使用 Mock 数值兜底。
- 浏览器宠物主页和设备连接页在 `local_k1` 模式下每 5 秒显示真机电量、
  充电状态和电压；连接页隐藏模拟滑块与模拟重连，Mock 模式仍保留调试控件。
- 充电器拔插状态作为接电主判据，并禁用状态请求缓存，避免电量计状态更新
  滞后导致“正在充电”和“充电器未连接”同时出现。
- 原生对话增加 `get_battery_status` Function Calling，用户可直接询问当前
  电量、充电状态和电压，回答数据在调用时从 K1 sysfs 读取。
- 网页基础设置增加 `5..120` 秒免唤醒追问时间；设置原子持久化，原生对话
  在每次开启追问窗口时动态读取，无需重启服务。
- 对话改为端侧 VAD 单次提交，云端确认 `commit` 后才发送
  `response.create`；回答请求后 8 秒仍无有效用户转写则发送
  `response.cancel`，避免环境声造成长时间假思考；每次新唤醒前仍清除云端
  音频缓存。
- 保存低负载 WebSocket 传输层最小补丁，并为语音 systemd 服务启用运行时
  状态目录跨自动重启保留，网页重连期间不再短暂显示“状态不可用”。
- 增加 Local K1 固定摇头、点头和停止接口；所有动作共用跨进程锁，停止或超时
  会先请求进程正常退出，使原生程序将电机切回 `MOTOR_MODE_IDLE`。
- 增加维修后使用的低速摇尾接口；网页、REST API 和语音 Function Calling
  共用 `AI_CAT_ENABLE_TAIL_MOTION` 开关，默认关闭且不接受任意电机参数。
- 修复摇头抽动：左右范围由 `60°` 收敛为厂商现用的 `30°`，动作改为低速
  `右 -> 左 -> 中` 三段轨迹；停用会自主发布 DDS 动作的旧
  `toy_motor.service`，避免两套进程同时控制同一电机；修复后真机验收幅度合适、
  动作平顺。
- 鼻部触摸检测、情绪计算、屏幕和音效服务仍保持运行；由于旧
  `toy_motor.service` 已关闭，当前触摸事件不会触发电机动作，待后续接入统一的
  安全电机调度器后恢复触摸动作反馈。
- K1 实测 API 与 sysfs 同步，摇头、点头和中途停止通过真机验收；自动测试更新为
  `96 passed`。尾部机械动作尚未测试。

### 2026-07-30

修改人：Mars

- 完成 FastAPI 第一阶段控制服务及浏览器端产品体验 Mock，覆盖登录绑定、
  性格盲盒、宠物主页、动作控制、亲密度培养、对话历史和基础设置。
- 增加 5 种持久化宠物性格、5 个亲密度等级、每日增长上限、解锁规则和
  盲盒重置调试能力。
- 保存火山引擎 K1 SDK 的可重建补丁和 overlay，不上传完整厂商 SDK、模型、
  ProductSecret 或设备鉴权缓存。
- 接入本地“小安小安”唤醒词、systemd 开机自启动、PulseAudio WebRTC AEC
  和 100% AEC 麦克风输入增益。
- 修复首次提问漏收音、回答卡顿、扬声器回声自打断、播放尾块残留、云端
  `response.create`、回答结束后追问无法识别等问题。
- 首次唤醒后提供 30 秒提问窗口；回答音频完全播放后响起短提示音，再开放
  30 秒免唤醒追问窗口，并清理云端及本地录音缓冲。
- 增加浏览器/API 固定信号打断、实时对话状态、断线重启保护和超时恢复。
  播放期间的纯语音唤醒打断暂未开放，以避免扬声器回声误唤醒。
- 接入 `shake_head` 和实时天气 Function Calling，增加电机互斥、重复动作
  冷却、工具执行期间收音保护和明确的失败返回。
- 增加原生对话事件持久化及 FastAPI 幂等导入逻辑，为对话历史页面提供真实
  设备数据入口。
- 完成 K1 真人唤醒、首轮问答、连续追问、音频播放和服务重启验证；自动测试
  结果为 `71 passed`。

## 已实现功能

### 浏览器与后端

- 提供 FastAPI `/api/v1` REST API、Swagger `/docs`、健康检查 `/health` 和适合
  手机/电脑浏览器使用的 `/control` 页面，可作为微信小程序完成前的交互替身。
- 页面覆盖体验账号登录、设备绑定与解绑、网络/在线状态、宠物主页、运动控制、
  亲密度培养、长期成长人格、对话历史、会话详情、音量与追问时间设置、异常反馈。
- 支持完整 Mock 驱动和 Local K1 真机驱动；Mock 模式可模拟电量、充电、断线和
  重连，不会执行 systemd、声卡或电机命令。
- SQLite 持久化用户、设备、宠物、性格、亲密度、动作执行、对话和设置，服务或
  设备重启后可恢复；长期成长属性、标签及原因事件也按宠物隔离持久化，重复来源
  ID 不会重复成长。

### 性格与培养

- 首次绑定通过性格盲盒持久化分配 5 种性格之一：元气探险家、温柔陪伴者、
  傲娇小明星、好奇小博士、沉稳守护者；普通解绑后保留设备实例的性格和成长数据。
- 每种性格包含独立描述、系统提示词、语言风格、亲密度称呼、
  动作白名单、Function Calling 规则、本地主动短语和禁止内容。
- 所有性格共用火山引擎控制台当前 TTS 音色，应用不再按性格覆盖发音人。
- 提供 5 个亲密度等级和升级进度；每日正向总增长上限为 `50`。每日见面、有效
  对话、实体触摸和完成任务按独立次数规则计分，重复事件不会重复增加。
- 亲密度解锁称呼、语气、头部动作、尾部/组合动作和主动陪伴规则；网页培养页
  每 5 秒同步当前值、等级、已解锁内容和下一等级内容。
- 长期成长人格不会替换初始性格或亲密度。6 个属性只形成自然语言行为倾向，8 个
  标签需要“属性阈值 + 真实事件累计”共同满足；最终行为画像遵守“初始性格决定
  怎么表达、成长人格决定更倾向做什么、亲密度决定关系有多近”。

### 语音与对话

- 本地“小安小安”唤醒服务常驻且不连接火山；唤醒后按需启动云端实时对话，
  云端就绪后自动聆听，回答播放结束后开放可配置的免唤醒追问窗口。
- 支持 ASR、LLM、TTS、实时对话状态、短提示音、网页打断、超时恢复和回答期间
  的声学回声保护；云端空闲 90 秒后正常断开，降低 License 语音时长消耗。
- 网页可直接输入文字提问，回答由真机扬声器播放并进入同一对话历史；也提供
  固定短文本 TTS 播报入口，忙碌时拒绝竞争请求。
- 真机最终字幕近实时、幂等导入 SQLite，页面按真实问答轮次显示会话摘要、用户
  与 AI 消息详情以及等待回答/已完成状态。
- 当前持久化性格会在空闲阶段通过 `session.update` 同步真实提示词和动作规则；
  音色完全由火山引擎控制台管理。已实现电量查询、天气及头部/尾部动作
  Function Calling。

### 真机状态与交互

- 从 K1 Linux `power_supply` 读取真实电量、电池状态、电压和充电器在线状态；
  网页每 5 秒同步，也可通过语音直接询问电量。
- 网页读取并设置 PulseAudio 实际输出音量，范围固定为 `0..100`；零音量自动
  静音，非零自动取消静音，硬件设置成功后才写入数据库。
- 从原厂 `toy_main` 日志增量识别头部、背部、鼻部及左右脚触摸，修正样机接线
  标签后增加亲密度，并触发统一安全调度器中的点头或摇头。
- 触摸动作完成后按部位播放共享的本地固定语句；5 个触摸 WAV 使用控制台当前
  音色预生成，
  播放不启动火山云端，并暂停本地唤醒收音以避免自唤醒。
- 安全自主服务在云端离线时按性格随机执行已验收头部动作，每 3 分钟最多播放
  1 条对应性格的本地短语；5 种性格共 15 个主动短语 WAV，不消耗云端时长。
- 修复板端旧 `toy_motor.service` 将离线主动短语覆盖为关闭的问题：K1 单元和环境
  示例统一启用本地短语、使用 `pulse-access` 并依赖 PulseAudio 服务；自主进程
  启动时校验 `paplay` 和全部 15 条性格 WAV，配置或资源异常将明确启动失败，不再
  静默退化为“只执行动作、不播放声音”。

### 动作与安全

- 产品动作库包含 7 个带编号、性格和亲密度约束的安全预设：摇头、点头、摇尾、
  骄傲姿态、安静陪伴、见面问候组合和升级庆祝组合。
- Local K1 已接入固定摇头、点头、停止和低速摇尾接口；动作异步执行、全局串行，
  支持重复请求幂等、冷却、超时、停止取消和进程退出清理。
- HTTP、语音 Function Calling 和自主动作只能选择固定预设，不能传入 GPIO、角度、
  速度、Shell 命令、服务名或音频路径。原厂 DDS 电机执行入口保持停用，避免两套
  进程同时控制电机。
- 提供 `legacy_safe` 与第二台 K1 使用的 `k1_vendor_smooth` 两套显式动作配置；
  后者的头部左右、上下及尾部原厂轨迹已真机验证。尾部 API 虽已实现，但生产开关
  默认关闭，必须在对应设备完成机械维修和安全验收后才能开放。
- `k1_vendor_smooth` 的复杂动作使用固定命名预设，不再把动作目录中的差异参数
  丢弃后复用同一个电机轨迹；骄傲转身和安静陪伴已真机验收，其余两个组合预设
  继续保持待验收标记，不能仅凭自动测试认定机械表现正确。

### 工程与部署

- 仓库包含 FastAPI systemd 模板、K1 部署文档、火山 SDK 锁定版本与最小 overlay、
  可读的对话/唤醒/电机应用源码，以及新电脑一键安装和 Mock 启动脚本。
- API Key、固定命令白名单、systemd 服务白名单、动作跨进程锁和敏感文件忽略规则
  已实现；ProductSecret、DeviceSecret、设备缓存、厂商完整 SDK 和模型不进入 Git。
- 已调查双眼自定义显示：当前 `toy_ui 1.1.10` 具备显示 `932 x 466` GIF/PNG 的
  底层能力，但缺少匹配的 DDS IDL，因此尚未开放图片 API，也不会替换板端 UI。

## 真机部署状态

| 设备 | 当前状态 |
| --- | --- |
| `7c2b63fd4a138` | 已完成语音、网页控制、状态读取、触摸、亲密度、头部动作和本地短语验证。 |
| `7c2b63fd4a128` | 已部署控制服务与电机应用，`k1_vendor_smooth` 头部和尾部轨迹已验证；原生火山对话已编译，云端语音等待可用 License 和本地唤醒资源后启用。 |

当前没有宣称测试完厂商 SDK 全部 API。已验证范围集中在设备鉴权、WebSocket
实时会话、ASR/LLM/TTS、Function Calling、音频播放、本地唤醒、文字提问、电量
查询、性格更新、字幕同步和头部动作；RTC、视频、双眼自定义显示、第二台设备云端
语音、尾部生产开放、全部异常网络场景及长期压力测试仍未完成。

Local K1 只执行所选硬件配置中的固定命令：`legacy_safe` 使用 `head_lr 1`、
`head_ud 2`，新设备验收配置 `k1_vendor_smooth` 使用对应的速度 `3`；两者均只
允许固定 `motor stop`。摇尾还必须显式设置 `AI_CAT_ENABLE_TAIL_MOTION=true`，
否则返回 `501`。对话 API 只允许向 `volc-conv-ai.service` 发送
固定的 `SIGHUP`/`SIGUSR1`/`SIGUSR2`，并只允许按需启动这一个固定服务。产品
Mock 的回答仍是本地模板，不会消耗火山服务；Local K1 使用性格定义中的提示词，
TTS 音色由火山引擎控制台统一决定。

## 目录

```text
src/ai_cat_controller/             FastAPI、网页、服务和适配器
tests/                             Mock 自动测试和安全边界测试
deploy/systemd/                    FastAPI systemd 模板
native/ai-toy-app/                 K1 硬件命令应用层
native/dialog/                     对话主程序快照
native/wake-word/                  本地唤醒词入口
integrations/volcengine-k1/        上游锁定、补丁和 K1 overlay
docs/                              API、架构、部署和验证记录
```

## 开发运行

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"

export AI_CAT_HARDWARE_DRIVER=mock
uvicorn ai_cat_controller.main:app \
  --host 127.0.0.1 \
  --port 8000 \
  --reload
```

访问：

- http://127.0.0.1:8000/control
- http://127.0.0.1:8000/docs
- http://127.0.0.1:8000/health

也可以使用：

```bash
ai-cat-server
```

## 手机局域网测试

只在受信任局域网中显式监听所有网卡：

```bash
export AI_CAT_HARDWARE_DRIVER=mock
export AI_CAT_API_HOST=0.0.0.0
uvicorn ai_cat_controller.main:app --host 0.0.0.0 --port 8000
```

通过 `hostname -I` 查询电脑地址，然后访问：

```text
http://电脑局域网IP:8000/control
```

## 自动测试

```bash
python -m compileall src tests
pytest -q
```

测试默认使用 Mock。Local K1 测试注入假的命令执行器，不会操作 systemd 或硬件。

## 配置

配置项见 `.env.example`。程序不会自动读取 `.env`，部署时应由终端或 systemd
`EnvironmentFile` 注入变量。

`local_k1` 模式强制要求：

```bash
export AI_CAT_HARDWARE_DRIVER=local_k1
export AI_CAT_API_KEY_ENABLED=true
export AI_CAT_API_KEY='替换为随机密钥'
export AI_CAT_ENABLE_TAIL_MOTION=false
export AI_CAT_DEBUG_UNLOCK_ALL_ACTIONS=false
export AI_CAT_DEBUG_UNLIMITED_TOUCH_INTIMACY=false
export AI_CAT_ENABLE_PRODUCT_DATA_RESET=false
export AI_CAT_ENABLE_LEVEL_UP_CELEBRATION=false
export AI_CAT_AUTONOMY_MIN_INTERVAL_SECONDS=180
export AI_CAT_AUTONOMY_MAX_INTERVAL_SECONDS=180
export AI_CAT_AUTONOMY_PHRASE_PROBABILITY=1.0
export AI_CAT_AUTONOMY_CLOUD_SPEECH_ENABLED=false
export AI_CAT_AUTONOMY_LOCAL_SPEECH_ENABLED=true
export AI_CAT_AUTONOMY_LOCAL_SPEECH_ASSET_ROOT=/opt/ai-cat-controller/assets/local-speech
export AI_CAT_AUTONOMY_LOCAL_SPEECH_MOTION_SETTLE_SECONDS=2.2
export AI_CAT_CONVERSATION_MOTION_ENABLED=true
export AI_CAT_CONVERSATION_MOTION_PROBABILITY=1.0
export AI_CAT_CONVERSATION_MOTION_DELAY_SECONDS=1.0
export AI_CAT_CONVERSATION_POLL_INTERVAL_SECONDS=0.5
export AI_CAT_CONVERSATION_HEAD_MIN_INTERVAL_SECONDS=7.0
export AI_CAT_CONVERSATION_HEAD_MAX_INTERVAL_SECONDS=10.0
export AI_CAT_CONVERSATION_TAIL_ENABLED=true
export AI_CAT_CONVERSATION_TAIL_DELAY_SECONDS=2.0
export AI_CAT_CONVERSATION_TAIL_MIN_INTERVAL_SECONDS=3.0
export AI_CAT_CONVERSATION_TAIL_MAX_INTERVAL_SECONDS=4.5
```

首次部署应确认私有仓库内的 21 个 WAV 已同步：

```bash
find /opt/ai-cat-controller/assets/local-speech -type f -name '*.wav' | wc -l
# 预期：21（15 个性格主动短语 + 5 个共享触摸短语 + 1 个升级提示语）
```

只有修改短语或在火山引擎控制台更换音色时，才在已完成火山鉴权的 K1 上以管理员
身份运行
`sg pulse-access -c 'tools/capture_cloud_phrase_assets.py --kind all'` 重新缓存；工具不会
覆盖控制台音色，结束后会恢复原性格运行时并关闭云端服务。

不要将真实 API Key、火山 ProductSecret 或设备鉴权缓存提交到 GitHub。

产品 Mock 的默认数据库为 `.data/ai-cat-mock.db`。可修改：

```bash
export AI_CAT_DATA_PATH=/var/lib/ai-cat-controller/product-mock.db
export AI_CAT_INTIMACY_DAILY_CAP=50
```

## 安全边界

- HTTP 请求不能指定可执行文件、服务名或 Shell 参数。
- Python 代码不使用 `os.system`、`shell=True` 或 Shell 字符串拼接。
- Local K1 只允许固定服务的 `is-active`、`is-enabled`，以及对话服务的
  `SIGHUP`/`SIGUSR1`/`SIGUSR2`。
- 仅允许无 Shell 地执行固定的
  `systemctl --no-block start volc-conv-ai.service`；HTTP 请求不能改变命令、
  服务名或启动参数。
- 文字问题和云端主动短语只能写入固定的 `dialog-text-request.json`；本地主动
  短语只能从 5 种性格的预生成 WAV 白名单中选择；问题限制为 500 个字符、
  云端主动短语限制为 100 个字符；
  HTTP 请求不能指定文件路径、信号、服务或厂商鉴权信息。
- 不执行任意 `systemctl stop/restart`，也不接受请求传入任意信号或服务名。
- 真机电机只接受审核过的固定动作，不接受 HTTP 或 Function Calling 传入 GPIO、
  方向、角度、速度、持续时间或 Shell 参数。
- K1 上的 `toy_motor.service` 必须使用本仓库提供的安全单元，禁止恢复原厂
  `ExecStart=/usr/bin/toy_control`，否则 DDS 动作会绕过锁并形成双控制源。
- 实体触摸只允许触发固定的 `head_nod`/`head_shake`，并受对话状态、动作互斥和
  3 秒冷却限制；不恢复原厂 DDS 电机消费者，也不触发未验收的尾部动作。
- 尾部动作必须在硬件修复、低速直连和停止验收全部通过后才允许开启配置。
- `AI_CAT_DEBUG_UNLOCK_ALL_ACTIONS` 只能用于现场动作验收，正式环境必须关闭；它只
  跳过性格和亲密度解锁条件，不能绕过硬件与调度安全保护。
- `AI_CAT_DEBUG_UNLIMITED_TOUCH_INTIMACY` 只能用于培养流程调试，正式环境必须关闭；
  同一触摸事件的 `request_id` 幂等保护始终保留。
- 一键格式化会清除全部产品体验数据，只能在明确需要重新拍摄绑定和性格盲盒流程
  时临时开启 `AI_CAT_ENABLE_PRODUCT_DATA_RESET=true`；操作仍要求 API Key、有效体验
  会话和固定确认词，语音会话进行中会拒绝执行。备份目录权限为 `0700`，数据库和
  运行时文件权限为 `0600`，HTTP 响应只返回备份编号。
- 不执行 DDS 或 ROS2 操作。
- 正式远程控制需要 HTTPS、鉴权和受控中转，不能直接暴露公网端口。

## 火山引擎 SDK

本仓库不分发完整厂商 SDK。恢复方法见
`integrations/volcengine-k1/README.md`。

许可证和厂商来源见 `LICENSE`、`NOTICE` 和
`docs/vendor-dependencies.md`。

产品交互边界、性格规则和当前验证范围见：

- `docs/system-interaction-flow.md`
- `docs/personality-and-growth.md`
- `docs/product-mock-validation.md`
- `docs/voice-dialog-testing.md`
