# AI Cat Controller

SpaceMIT K1 AI 猫的独立控制仓库。当前提供 FastAPI 产品体验 Mock、浏览器端
小程序流程替身、完整 Mock 适配器和受限的 Local K1 真机控制；未完成硬件验收
的尾部动作默认关闭。

## 更新记录

### 2026-08-04

修改人：Mars

- 将 5 种持久化性格映射到火山引擎真实音色和独立系统提示词；当前宠物绑定、
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
  `ResourceId=volc.service_type.10029`，已验证“撒娇学妹”音色能完成 TTS 播放并
  返回 `response.done`；其余 4 个音色仍需逐一人工听感验收。
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
  暂停收音，结束后恢复。15 个 WAV 由对应火山音色一次性数字缓存并保存到私有
  仓库；自主头部动作完成 2.2 秒后再播放，避免电机噪声覆盖语音。
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

## 当前能力

- `/control` 手机和电脑浏览器六模块产品体验页面。
- `/api/v1` 稳定 REST API，可供后续微信小程序复用。
- Mock 登录、设备绑定、性格盲盒、主页、培养、历史和设置。
- 5 种持久化性格、5 个亲密度等级、每日增长上限和解锁规则。
- Local K1 将当前性格的真实火山音色、提示词和动作规则动态应用到现有会话。
- 7 个安全预设动作、异步结果、重复请求幂等和离线拒绝。
- SQLite 持久化，进程重启后恢复性格、亲密度和历史。
- 真机最终字幕近实时同步、按会话汇总及单会话详情查询。
- API Key、动作串行、停止取消、超时和退出清理。
- Local K1 固定 systemd 服务状态查询、对话唤醒/打断和实时阶段显示。
- Local K1 网页文字提问、真机语音回答和统一会话历史。
- Local K1 本地唤醒常驻、云端对话按需启动及空闲 90 秒自动断开。
- Local K1 网页配置免唤醒追问时间，设备重启后保持设置。
- Local K1 真实电量、充电状态、电池电压和充电器在线检测，以及语音查询。
- Local K1 固定摇头、点头和停止动作，以及默认关闭的维修后摇尾接口。
- 火山引擎 K1 补丁、唤醒词入口和硬件应用源码。

Local K1 只执行固定的 `head_lr 1`、`head_ud 2`、`motor stop` 命令。摇尾只在
维修并完成低速验收后设置 `AI_CAT_ENABLE_TAIL_MOTION=true` 才开放固定的
`tail_lr 1`；默认返回 `501`。对话 API 只允许向 `volc-conv-ai.service` 发送
固定的 `SIGHUP`/`SIGUSR1`/`SIGUSR2`，并只允许按需启动这一个固定服务。产品
Mock 的回答仍是本地模板，不会消耗火山服务；Local K1 使用同一性格定义中的
真实火山音色 ID 和提示词。

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
export AI_CAT_AUTONOMY_MIN_INTERVAL_SECONDS=180
export AI_CAT_AUTONOMY_MAX_INTERVAL_SECONDS=180
export AI_CAT_AUTONOMY_PHRASE_PROBABILITY=1.0
export AI_CAT_AUTONOMY_CLOUD_SPEECH_ENABLED=false
export AI_CAT_AUTONOMY_LOCAL_SPEECH_ENABLED=true
export AI_CAT_AUTONOMY_LOCAL_SPEECH_ASSET_ROOT=/opt/ai-cat-controller/assets/local-speech
export AI_CAT_AUTONOMY_LOCAL_SPEECH_MOTION_SETTLE_SECONDS=2.2
```

首次部署应确认私有仓库内的 15 个 WAV 已同步：

```bash
find /opt/ai-cat-controller/assets/local-speech -type f -name '*.wav' | wc -l
# 预期：15
```

只有修改短语或音色时，才在已完成火山鉴权的 K1 上以管理员身份运行
`tools/capture_cloud_phrase_assets.py` 重新缓存；工具结束后会恢复原性格并关闭
云端服务。

不要将真实 API Key、火山 ProductSecret 或设备鉴权缓存提交到 GitHub。

产品 Mock 的默认数据库为 `.data/ai-cat-mock.db`。可修改：

```bash
export AI_CAT_DATA_PATH=/var/lib/ai-cat-controller/product-mock.db
export AI_CAT_INTIMACY_DAILY_CAP=20
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
- 当前触摸事件只保留检测及非电机反馈，不会执行头部或尾部动作。
- 尾部动作必须在硬件修复、低速直连和停止验收全部通过后才允许开启配置。
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
