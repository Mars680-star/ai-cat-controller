# AI Cat Controller 开发交接

> 最后更新：2026-08-11
>
> 维护人：Mars
>
> 用途：新电脑或新的 Codex 会话开始工作时，先读取本文件，再读取 `README.md`。

## 1. 仓库与连续开发规则

- GitHub 私有仓库：`https://github.com/Mars680-star/ai-cat-controller`
- 权威分支：`main`。成长人格 V1 当前开发分支为
  `feat/growth-personality-v1`，完成真机验收后再合并。
- Python 要求：`>=3.10`。
- 新会话第一步必须执行：

```bash
git status --short --branch
git log -5 --oneline --decorate
git pull --ff-only
```

- 不要覆盖未提交的用户修改，不要提交密钥、设备凭据、数据库、模型、厂商完整 SDK
  或板端构建产物。
- K1 的局域网地址由 DHCP 分配，序列号 `7c2b63fd4a128` 在 2026-08-11 实测地址为
  `192.168.1.112`；每次连接前仍应在板端执行 `hostname -I` 重新确认。

## 2. 新电脑直接运行

桌面端和普通 Linux 电脑不需要 K1、火山引擎账号或厂商 SDK，即可运行完整产品
Mock：

```bash
gh auth login
gh repo clone Mars680-star/ai-cat-controller
cd ai-cat-controller
./scripts/bootstrap_dev.sh
./scripts/run_mock.sh
```

仓库为 Private，新电脑必须先用有访问权限的 GitHub 账号登录。没有 GitHub CLI 时，
也可以在配置好 Git 凭据后使用 HTTPS 地址克隆。

打开：

- `http://127.0.0.1:8000/control`：浏览器产品体验页面
- `http://127.0.0.1:8000/docs`：OpenAPI 文档
- `http://127.0.0.1:8000/health`：健康检查

`bootstrap_dev.sh` 会创建 `.venv`、安装开发依赖、编译 Python 文件并执行完整
pytest。`run_mock.sh` 强制使用 Mock 驱动，不会操作 K1、systemd 或电机。

如需手机在同一局域网访问：

```bash
AI_CAT_API_HOST=0.0.0.0 ./scripts/run_mock.sh
```

只应在受信任局域网使用该方式，不要把开发服务直接暴露到公网。

## 3. 项目结构

```text
src/ai_cat_controller/       FastAPI、REST API、网页、业务服务和硬件适配器
tests/                       Mock、Local K1、安全边界和源码约束测试
assets/local-speech/         15 条性格主动 WAV + 5 条共享触摸 WAV
native/dialog/               当前对话主程序的可读源码快照
native/wake-word/            “小安小安”本地唤醒入口
native/ai-toy-app/           K1 固定安全电机命令应用层
integrations/volcengine-k1/  上游版本锁、补丁、K1 overlay 和 systemd 单元
deploy/systemd/              FastAPI 服务模板
docs/                        架构、API、真机部署和验证记录
tools/                       已鉴权 K1 上重新生成本地语音素材的工具
```

主要调用链：

```text
浏览器/未来微信小程序 -> FastAPI /api/v1 -> 业务服务
                                      -> SQLite 产品状态
                                      -> MockAdapter 或 LocalK1Adapter
LocalK1Adapter -> 固定 systemd 信号/固定电机命令/板端状态文件和 sysfs
```

## 4. 当前已完成能力

- FastAPI 和浏览器产品 Mock：登录绑定、宠物主页、性格盲盒、培养、动作、对话
  历史、会话详情和设置。
- 设置页可在显式开启调试开关后，一键备份并格式化全部产品体验数据，供重新拍摄
  登录、绑定和性格盲盒流程；火山鉴权、License、SDK 与系统配置不在清理范围内。
- 5 种持久化性格：元气探险家、温柔陪伴者、傲娇小明星、好奇小博士、沉稳守护者。
  每种性格已绑定独立提示词、动作白名单和本地主动短语；所有 TTS 统一使用火山
  控制台当前音色，运行时不发送 `TTSConfig`。
- 5 个亲密度等级、每日正向增长上限 `50`、称呼/动作解锁和 SQLite 持久化。
- 长期成长人格 V1：6 个固定点属性、8 个配置化标签、对话/触摸/任务/每日见面
  成长事件、重复与话题衰减、按宠物隔离的 SQLite 原因日志，以及现有
  `personality-runtime.json` / `session.update` Prompt 集成。培养页可查看倾向、
  标签、行为画像和最近成长；Mock 可快速模拟，Local K1 默认禁止 Debug 写入。
  总开关 `AI_CAT_ENABLE_GROWTH_PERSONALITY_V1` 默认关闭；关闭期间不写成长、不修改
  Prompt，重新开启也不会追溯处理关闭期间已导入的真机对话。当前 K1 测试设备已
  显式开启，软件层全量回归为 `219 passed`。
- K1 发布前可运行 `scripts/k1_backup_release.sh`，回滚前可先用
  `scripts/k1_rollback_release.sh BACKUP --check` 校验；五部位实体触摸使用
  `scripts/k1_verify_touch_mapping.sh` 验收。完整流程见
  `docs/growth-v1-deployment.md`。
- `7c2b63fd4a128` 的成长 V1 启用前恢复点为
  `/root/ai-cat-backups/before-growth-v1-20260811T101234Z-5724`。用户明确跳过本轮
  其余硬件测试后，板端已开启成长总开关并保持 Debug 关闭；任务和两个真实文字
  问答成长、幂等与历史导入通过。头部、背部和左右爪触摸已由现场确认正常，左右爪
  继续使用双触防误触；鼻部 `GPIO113` 在厂商日志和独立 GPIO 监听中均无信号。
- 自动动作/本地播音曾造成 6 条背部传感器回灌成长。运行时现会在受控动作、结束后
  1 秒噪声收敛窗或 `/run/ai-cat/local-speech-active` 存在时忽略原始触摸日志；
  自动行为回归未再污染成长。
  清理前备份为 `/root/ai-cat-backups/before-growth-v1-20260811T103223Z-7833`。
- 原生火山对话必须收到 `session.created` 才发布 ready，避免首个文字问题被欢迎语
  抢占。K1 已重新编译部署并用“一加一等于几”验证首轮回答正确。
- 真机电量、充电器在线、充电状态和电压读取；语音可通过 Function Calling 查询。
- 网页音量直接控制 K1 PulseAudio 默认输出，页面同步实际音量；FastAPI systemd
  进程必须使用 `Group=pulse-access`（只设附加组会被当前板端拒绝）。
- 头部、背部、左右脚和鼻部实体触摸从 `toy_main` 日志增量导入亲密度，复用
  request ID 幂等、每日次数和总增长上限；头部/背部映射安全点头，鼻部/左右脚
  映射安全摇头，对话忙、电机忙及 3 秒冷却内跳过动作；完成任务由网页按钮确认。
- 5 个共享本地触摸 WAV 使用控制台当前音色预生成；触摸动作结束后按部位播放，
  不启动火山云端，并通过 `/run/ai-cat/local-speech-active` 暂停本地唤醒。
- 触摸接线按设备序列号选择：`7c2b63fd4a128` 使用 identity 映射；旧设备
  `7c2b63fd4a138` 使用 `nose <-> head`、`back <-> left_foot` 的
  `legacy_swapped` 映射，右爪不变。业务元数据保留 `hardware_sensor`、修正后的
  `sensor` 和 `sensor_mapping`。
- 触摸语音按实体部位独立执行 3 秒防刷并使用串行播放锁；它与电机 3 秒冷却解耦，
  因此电机因冷却跳过时，新的部位仍可播放对应本地短语。
- 实体左右爪在映射后分别做防误触确认：默认同一只爪 3 秒内连续触摸 2 次才导入
  一次业务事件；头部、鼻部和背部保持单次触发。相关环境变量为
  `AI_CAT_PAW_TOUCH_CONFIRMATION_COUNT` 和
  `AI_CAT_PAW_TOUCH_CONFIRMATION_WINDOW_SECONDS`。
- 真机语音字幕实时导入、对话历史同步、按会话查询和网页文字提问。
- 本地“小安小安”唤醒常驻；命中后按需启动火山云端，云端就绪后自动聆听，
  空闲 90 秒正常断开。网页文字提问也按需启动云端。
- 固定摇头、点头、停止接口；摇头已收敛为低速 `右 -> 左 -> 中`。尾部接口已
  实现但默认关闭，等待硬件维修验收。
- 新设备 `7c2b63fd4a128` 的原厂顺滑动作参数已记录，并通过
  `AI_CAT_MOTION_PROFILE=k1_vendor_smooth` 显式启用速度 3、100 ms 停留配置；
  旧设备默认继续使用 `legacy_safe`。部署前阅读 `docs/k1-motion-profiles.md`。
- 安全自主行为服务：云端离线时按性格执行已验收头部动作，每 3 分钟最多播放
  一条对应的本地缓存短语，不消耗火山语音时长。
- K1 的 `toy_motor.service` 必须保持
  `AI_CAT_AUTONOMY_LOCAL_SPEECH_ENABLED=true`、`Group=pulse-access` 并依赖
  `volc-pulseaudio.service`；进程启动时会校验 `paplay` 与全部 15 条性格 WAV，
  任一资源缺失都应使服务明确失败，不能改回只执行动作的静默降级。
- 旧 DDS 电机执行入口已替换；触摸动作只经过 FastAPI `MotionService`，不恢复
  原厂 DDS 电机消费者，尾部仍保持禁用。
- 双眼可显示 `932 x 466` GIF/PNG，但现用 `toy_ui 1.1.10` 的 DDS IDL 未入库，
  当前只完成调查，不应使用仓库内旧版 UI 源码覆盖板端程序。详见
  `docs/eye-display-investigation.md`。

更细的历史和修复记录见 `README.md`，接口契约见 `docs/api.md`。

## 5. K1 当前运行模型

最近一次验收时的预期服务状态：

| 服务 | 预期状态 | 说明 |
| --- | --- | --- |
| `ai-cat-controller.service` | active/enabled | FastAPI 真机控制服务 |
| `volc-pulseaudio.service` | active/enabled | PulseAudio 与回声消除路由 |
| `volc-k1-wake-word.service` | active/enabled | 本地唤醒，常驻但不连接火山 |
| `volc-conv-ai.service` | inactive/disabled | 正常状态；唤醒或文字提问时按需启动 |
| `toy_motor.service` | active/enabled | 本项目安全自主动作，不是原厂 DDS 执行器 |
| `toy_voice.service` | inactive/disabled | 旧语音服务，不得与新对话进程并行 |

板端常用检查：

```bash
systemctl --no-pager --full status \
  ai-cat-controller.service \
  volc-pulseaudio.service \
  volc-k1-wake-word.service \
  volc-conv-ai.service \
  toy_motor.service

curl -sS http://127.0.0.1:8000/health
journalctl -u volc-k1-wake-word.service -u volc-conv-ai.service -n 200 --no-pager
```

Local K1 部署要求 API Key，并从板端 `/etc/ai-cat-controller.env` 或等价的
systemd `EnvironmentFile` 注入。具体步骤见 `docs/k1-fastapi-deployment.md` 和
`integrations/volcengine-k1/README.md`。

## 6. Git 中有意不包含的内容

以下内容受许可证、体积或安全限制，不能靠 GitHub 单独恢复：

- 火山引擎完整 ConversationalAI Embedded Kit；
- ProductSecret、AccessToken、DeviceSecret、API Key 和设备鉴权缓存；
- K1 的 ONNX/SenseVoice/Ten-VAD 模型、SpaceMIT EP 和厂商共享库；
- 板端 `/var/lib/ai-cat-controller/` 运行数据、SQLite 数据库、日志和备份；
- 真实 `conv_ai_config.json`、构建目录和生成的可执行文件。

厂商 SDK 可使用锁定提交和本仓库补丁重建：

```bash
./integrations/volcengine-k1/scripts/prepare_sdk.sh \
  "$HOME/ConversationalAI-Embedded-Kit-2.0"
```

之后必须由有权限的维护者在本地填写产品配置并安装板端依赖。不要向 Codex、日志、
Issue 或提交内容写入真实密钥。若密钥曾公开展示，应立即在火山控制台轮换。

因此，本仓库的“可直接运行”定义是：全新电脑克隆后可完整运行和测试 Mock；恢复
K1 真实语音和电机还需要物理板、厂商依赖及独立保存的部署凭据。

## 7. 关键安全约束

- HTTP 和 Function Calling 只能选择固定动作，不能传入 GPIO、角度、速度、命令、
  文件路径、服务名或 Shell 参数。
- Local K1 必须启用 API Key；正式远程访问还需要 HTTPS 和受控网关。
- `AI_CAT_ENABLE_TAIL_MOTION=false` 必须保持，直到尾部硬件完成
  `docs/tail-motion-validation.md` 的全部验收。
- 不恢复原厂 `ExecStart=/usr/bin/toy_control`，避免两套进程同时控制电机。
- `volc-conv-ai.service` 使用 `Restart=on-failure` 且不开机启用；正常空闲退出后
  不应被 systemd 自动拉起。
- 本地主动短语必须从仓库内 15 个 WAV 白名单选择，不允许 HTTP 指定任意音频路径。
- 触摸短语必须从仓库内 5 个共享 `touch-*.wav` 白名单选择；生产播放开关为
  `AI_CAT_ENABLE_TOUCH_SPEECH`，不得回退到任意路径或云端自由文本。
- `AI_CAT_ENABLE_PRODUCT_DATA_RESET` 默认关闭；临时开启时仍必须通过 API Key、
  有效体验会话和固定确认词校验，语音会话进行中不得执行。备份只保存在数据库
  同级的受限目录，HTTP 响应不得返回板端绝对路径。

## 8. 尚未完成或未完全验收

优先级从高到低：

1. 检修 `7c2b63fd4a128` 鼻部传感器或排线，使 `GPIO113` 能产生边沿并补做鼻部
   验收。背部和左右爪已由用户现场确认正常；当前成长 V1 已开启。
2. 修复尾部硬件后完成低速直连、停止、重复调用和超时验收，再决定是否开放尾部。
3. 取得 `toy_ui 1.1.10` 对应 `ToyCommand` IDL，开发只接受预设图片 ID 的双眼
   显示接口并完成真机资源/优先级验收。
4. 把 K1 外部模型、共享库和凭据恢复流程做成不含秘密的板端部署检查清单。
5. 继续评估 K1 约 1 GiB 内存下的常驻唤醒占用、zram/swap 和长时间稳定性。
6. 接入正式微信小程序、用户鉴权、设备绑定网关、HTTPS 和云端多用户数据隔离。
7. 控制台更换音色后重新采集并抽听 20 个本地 WAV；完成 RTC、视频和厂商 SDK
   其余未覆盖 API 的测试。
8. 继续积累足够真实互动以跨越属性倾向或标签阈值，验证标签变化刷新 runtime，
   并由下一次 `session.update` 体现稳定行为差异；真实问答成长链路本轮已经通过。

已知边界：当前并未测试完厂商 SDK 的全部 API；已重点验证设备鉴权、WebSocket
实时会话、ASR/LLM/TTS、Function Calling、音频播放、唤醒、文字提问、电量查询、
性格更新和头部动作。RTC、视频、尾部真机、所有异常网络场景及长期压力测试仍未完成。

## 9. 新 Codex 会话接手清单

1. 读取本文件、`README.md`、`docs/architecture.md` 和与任务相关的专项文档。
2. 检查 `git status`、当前分支、最近提交和 GitHub `main`，确认没有未同步修改。
3. 运行 `./scripts/bootstrap_dev.sh`；已有环境时至少运行 `.venv/bin/pytest -q`。
4. 只在用户明确准备好真机测试时连接 K1；先重新确认 IP 和服务状态。
5. 修改前备份板端配置和数据库；代码修改走 Git 提交，部署秘密留在板端。
6. 对共享行为增加测试；电机、systemd、鉴权和文件写入改动必须覆盖拒绝路径。
7. 完成后更新 `README.md` 的日期记录；若架构、服务状态或待办变化，同时更新本文件。
8. 推送到 GitHub，并从默认分支检查新电脑克隆路径仍然可运行。
