# SpaceMIT K1 SDK 修改与验证摘要

首次验证：2026-07-14

连续对话与 FastAPI 联调更新：2026-07-30

真机头部动作与尾部接口更新：2026-08-02

环境：SpaceMIT K1（riscv64）、Bianbu 2.2.1、ES8326/ES7243 音频设备

## 结论

- `ConversationalAI-Embedded-Kit-2.0` 的低负载 WebSocket 主链路已在 K1 跑通：设备鉴权、ASR、LLM、TTS、播放、唤醒和开机自启动可用。
- SDK **没有全部测试完毕**。RTC、视频、完整 API、异常网络、长稳和全部 Function Calling 场景仍未覆盖。
- 原 `starburst_aidialog_linux_SPACEMIT_K1_LINUX_llm_sdk_v101_2026_02_03` 只完成本地初始化和音频验证；其云端注册返回“设备已激活”且板上没有旧鉴权文件，因此未完成云端功能验证。

## 修改文件

| 文件或目录 | 主要内容 |
|---|---|
| `examples/low_load_solution/macos/volc_conv_ai_demo.c` | 修复录放音和多线程缓冲；增加连续上行、播放排空后的追问窗口、`SIGUSR1`/`SIGUSR2`、原子状态、断线恢复，以及固定参数的摇头、点头和受开关保护的摇尾 Function Calling。 |
| `examples/low_load_solution/linux_k1/CMakeLists.txt` | 新增 K1/riscv64 WebSocket 构建入口，禁用 x86 RTC，链接 PulseAudio 和系统 TLS 库。 |
| `examples/low_load_solution/linux_k1/configs/conv_ai_config.example.json` | 增加脱敏的 K1 配置模板；实际密钥只保存在板端构建目录。 |
| `examples/low_load_solution/linux_k1/README.md` | 增加构建、运行、Function Calling 和 systemd 使用说明。 |
| `examples/low_load_solution/linux_k1/systemd/*.service` | 新增 PulseAudio、云端对话、唤醒词三个开机服务。 |
| `examples/low_load_solution/linux_k1/wake_word/` | 新增 SenseVoice + Ten-VAD 本地唤醒程序、SpaceMIT EP 支持和低内存构建配置。 |
| `volc_conv_ai/src/volc_conv_ai.c` | 增加设备凭据安全缓存和复用，避免每次启动重复注册；修正 64 位日志格式。 |
| `volc_conv_ai/cmake/linux.cmake` | 支持系统 mbedTLS/zlib，补充低负载 WS 源文件和 Linux 输出目录。 |
| `volc_conv_ai/osal/src/linux/volc_osal.c` | 修正 Linux 平台标识和 pthread 退出参数。 |
| `volc_conv_ai/src/transports/low_load/third_party/websocket/websocket.{c,h}` | 兼容 Linux pthread 入口和不同版本 mbedTLS SHA-1 API。 |

板端另外生成或安装：

- `/root/.volc_conv_ai_<product>_<device>.json`：权限 `0600` 的设备鉴权缓存。
- `/etc/systemd/system/volc-pulseaudio.service`
- `/etc/systemd/system/volc-conv-ai.service`
- `/etc/systemd/system/volc-k1-wake-word.service`
- `/opt/ai-cat-controller`：独立 FastAPI 测试服务。
- `/etc/ai-cat-controller.env`：权限 `0600` 的 Local K1/API Key 配置。
- `/etc/systemd/system/ai-cat-controller.service`

## 增加的能力

- K1 原生 riscv64 编译和系统 mbedTLS 适配。
- PulseAudio 双向音频、播放小块化和环形缓冲互斥保护。
- 设备首次注册后缓存凭据，重启直接复用。
- 无键盘守护进程模式：`SIGUSR1` 开始/继续对话，`SIGUSR2` 打断并结束。
- 首次唤醒后连续上传麦克风；回答播放完成并响起短提示音后，30 秒内可直接追问。
- “小安小安”本地唤醒；连续会话期间通过
  `/run/ai-cat/dialog-session-active` 暂停唤醒采集，会话结束立即恢复。
- `/run/ai-cat/dialog-status.json` 输出
  `ready/listening/thinking/answering/followup` 等实时阶段。
- FastAPI `/api/v1/dialog/*` 和浏览器页面可查看状态、开始聆听和打断。
- 网页/API 可将免唤醒追问窗口设置为 5 到 120 秒，重启后保持并逐轮生效。
- 云端断开后退出，由 systemd 自动重建会话。
- `shake_head` 工具固定调用低速 `/usr/bin/ai-toy_app motor head_lr 1`；左右范围
  为 `30°`，轨迹为 `右 -> 左 -> 中`，不再执行六段大幅往返。
- `nod_head` 工具调用 `/usr/bin/ai-toy_app motor head_ud 2`；头部动作与
  FastAPI 共用原生跨进程锁和停止机制。
- `wag_tail` 软件路径固定调用低速 `/usr/bin/ai-toy_app motor tail_lr 1`，但
  默认由 `AI_CAT_ENABLE_TAIL_MOTION=false` 拒绝，等待硬件维修后验收。
- `get_battery_status` 工具实时读取电量、充电状态、电压和充电器在线状态。
- 开机等待网络、DNS、PulseAudio 就绪后再连接云端。

## 已测试

| 项目 | 结果 |
|---|---|
| K1 麦克风录音和扬声器播放 | 通过 |
| riscv64 编译、链接和动态库检查 | 通过 |
| 首次设备注册、凭据保存和重启加载 | 通过 |
| TLS/WebSocket、`session.created` 和云端 ready | 通过 |
| ASR -> LLM -> TTS -> 扬声器完整语音链路 | 通过 |
| 多次唤醒及近音容错 | 通过 |
| 唤醒后释放第二路录音、自动恢复监听 | 通过 |
| 背景讲话下收音提交和回复延迟 | 通过；实测约 2 秒开始下发回答 |
| 免唤醒连续多轮 | 通过；合成音频完成至少 3 轮状态流转 |
| 思考/回答期间持续上行 | 通过；日志持续出现约 255 kbps 上行 |
| 云端语音打断 | 通过；`response.done` 返回 `status_details=interrupt` |
| FastAPI 状态、唤醒和打断 | 通过；K1 本机和局域网 HTTP 均验证 |
| 真人连续追问和真人插话体验 | 待最终现场主观确认 |
| 两次冷启动后的服务、网络和模型自动恢复 | 通过 |
| FastAPI 摇头与点头 | 通过；用户现场确认真实动作成功 |
| 低速 30 度三段摇头 | 通过；约 3.8 秒完成，用户确认无抽搐且幅度合适 |
| FastAPI 中途停止 | 通过；运行中动作停止并回到空闲状态 |
| 并发动作拒绝 | 通过；第二个并发动作返回 `409` |
| 尾部软件接口与默认禁用 | 通过；关闭时 API 返回 `501`，状态为 `disabled`，未驱动硬件 |
| 尾部真实机械动作 | 未测试；当前硬件故障，修复后按专项文档验收 |
| `shake_head` 云端 Function Calling | 部分验证：处理代码和本地电机已验证，仍需保留一次云端调用日志作为完整证据 |
| `get_battery_status` Function Calling | 部分验证：K1 编译、sysfs 读取及处理代码已验证，待控制台配置工具后完成真人语音验证 |

当前服务状态：四个服务
`volc-pulseaudio`、`volc-conv-ai`、`volc-k1-wake-word`、
`ai-cat-controller` 均为 `enabled/active`，原 `toy_voice.service`
为 `disabled/inactive`。

## 公开 API 覆盖

| API | 状态 | 验证内容 |
|---|---|---|
| `volc_create` | 已测试 | 配置解析、首次注册、缓存鉴权和 engine 创建成功。 |
| `volc_start` | 已测试 | WebSocket 启动、连接和 `session.created` 成功。 |
| `volc_send_audio_data` | 已测试 | PCM 上行、提交、ASR/LLM/TTS 完整链路成功。 |
| `volc_interrupt` | 已测试 | 固定 `SIGUSR2` 入口和云端 `status_details=interrupt` 均有日志证据。 |
| `volc_send_message` | 部分测试 | 消息接收正常，Function Calling 输出代码已接入；缺少完整云端工具调用证据。 |
| `volc_stop` | 未专项测试 | 代码中有入口，未验证各种状态下停止及再次启动。 |
| `volc_destroy` | 未专项测试 | 未验证正常、重复及空句柄销毁。 |
| `volc_update` | 未测试 | 未验证会话参数动态更新。 |
| `volc_send_video_data` | 未测试 | 当前没有启用视频链路。 |
| `volc_send_text_to_agent` | 未测试 | 当前 demo 没有调用。 |
| `volc_get_version` | 未专项测试 | SDK 可运行，但未单独校验该接口返回值。 |
| `volc_err_2_str` | 未测试 | 未逐项校验错误码文本。 |

回调方面，`on_volc_event`、`on_volc_conversation_status`、`on_volc_audio_data` 和 `on_volc_message_data` 已实际触发；`on_volc_video_data` 未测试。

## 尚未测试

- RTC 模式（仓库附带库不是 riscv64，因此当前明确禁用）。
- 视频上传、视觉模型和 RTC 高质量传输。
- SDK 全部 API：`update`、完整 `stop/start/destroy` 组合、所有消息类型和并发场景。
- 断网、DNS 故障、服务器限流、配额耗尽、密钥过期等异常恢复。
- 24 小时以上长稳、内存泄漏、压力和高并发测试。
- 不同麦克风、远场、强噪声和大量人员环境下的唤醒率与误唤醒率。
- 真人在回答期间插话、30 秒追问窗口和网页状态提示的最终体验验收。
- 天气、电量等 Function Calling 的完整云端调用日志留档。
- 修复后尾部的 GPIO、方向、限位、堵转、温升、停止和重复动作验收。

旧 `toy_motor.service` 已设为 `disabled/inactive`。日志确认其上游
`toy_main.service` 会周期性发布 DDS 自主动作，若与本项目原生程序同时运行会
绕过电机锁并造成双控制；需要恢复厂商电机服务时，应先停用本项目的真机动作入口。

## 2026-08-02 电机回退点

- Git 提交 `b8f7a56`：固定头部动作和停止接口检查点。
- K1 `/usr/bin/ai-toy_app.pre-head-motion`
- K1 `/usr/bin/ai-toy_app.pre-head-map-fix`
- K1 `/usr/bin/ai-toy_app.pre-tail-interface`
- K1 `/usr/bin/ai-toy_app.pre-shake-smoothing-20260802-202510`
- K1 `examples/low_load_solution/linux_k1/build/volc_conv_ai_k1_demo.pre-shake-smoothing-20260802-202510`

## 2026-07-30 回退点

- `/root/ai-cat-backups/voice-dialog-before-continuous-20260730-1908`
- `/root/ai-cat-backups/voice-dialog-before-pacing-fix-20260730-1914`

FastAPI 测试地址为 `http://192.168.1.112:8000/control`。API Key 仅保存在
K1 的 `/etc/ai-cat-controller.env`，未提交到仓库。

## 安全注意

不要提交板端 `build/conv_ai_config.json` 或鉴权缓存。此前展示过的火山引擎 AccessToken/ProductSecret 应在控制台轮换。
