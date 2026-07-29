# SpaceMIT K1 SDK 修改与验证摘要

日期：2026-07-14

环境：SpaceMIT K1（riscv64）、Bianbu 2.2.1、ES8326/ES7243 音频设备

## 结论

- `ConversationalAI-Embedded-Kit-2.0` 的低负载 WebSocket 主链路已在 K1 跑通：设备鉴权、ASR、LLM、TTS、播放、唤醒和开机自启动可用。
- SDK **没有全部测试完毕**。RTC、视频、完整 API、异常网络、长稳和全部 Function Calling 场景仍未覆盖。
- 原 `starburst_aidialog_linux_SPACEMIT_K1_LINUX_llm_sdk_v101_2026_02_03` 只完成本地初始化和音频验证；其云端注册返回“设备已激活”且板上没有旧鉴权文件，因此未完成云端功能验证。

## 修改文件

| 文件或目录 | 主要内容 |
|---|---|
| `examples/low_load_solution/macos/volc_conv_ai_demo.c` | 修复录放音和多线程缓冲；增加无终端运行、`SIGUSR1` 唤醒、6 秒收音上限、断线退出重启、`shake_head` Function Calling。 |
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

## 增加的能力

- K1 原生 riscv64 编译和系统 mbedTLS 适配。
- PulseAudio 双向音频、播放小块化和环形缓冲互斥保护。
- 设备首次注册后缓存凭据，重启直接复用。
- 无键盘守护进程模式，收到 `SIGUSR1` 开始一轮对话。
- “小安小安”本地唤醒，兼容实测近音结果；对话时暂停唤醒采集，避免 K1 双录音冲突，20 秒后恢复。
- 背景人声下最大收音 6 秒，避免云端 VAD 长时间等待。
- 云端断开后退出，由 systemd 自动重建会话。
- `shake_head` 工具调用 `/usr/bin/ai-toy_app motor head_lr 2` 并回传执行结果。
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
| 两次冷启动后的服务、网络和模型自动恢复 | 通过 |
| 电机本地命令 | 通过 |
| `shake_head` 云端 Function Calling | 部分验证：处理代码和本地电机已验证，仍需保留一次云端调用日志作为完整证据 |

当前服务状态：三个新服务均为 `enabled/active`，原 `toy_voice.service` 为 `disabled/inactive`。

## 公开 API 覆盖

| API | 状态 | 验证内容 |
|---|---|---|
| `volc_create` | 已测试 | 配置解析、首次注册、缓存鉴权和 engine 创建成功。 |
| `volc_start` | 已测试 | WebSocket 启动、连接和 `session.created` 成功。 |
| `volc_send_audio_data` | 已测试 | PCM 上行、提交、ASR/LLM/TTS 完整链路成功。 |
| `volc_interrupt` | 已测试 | 唤醒期间打断回答并开始下一轮，日志确认调用成功。 |
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
- 除 `shake_head` 外的其他硬件 Function Calling。

## 安全注意

不要提交板端 `build/conv_ai_config.json` 或鉴权缓存。此前展示过的火山引擎 AccessToken/ProductSecret 应在控制台轮换。
