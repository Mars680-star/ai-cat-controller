# AI Cat Controller

SpaceMIT K1 AI 猫的独立控制仓库。当前提供 FastAPI 产品体验 Mock、浏览器端
小程序流程替身、完整 Mock 适配器和保守的 Local K1 对话控制；默认配置不会
控制真实硬件。

## 更新记录

### 2026-08-02

修改人：Mars

- 接入 K1 标准 Linux `power_supply` 电源接口，读取 `cw-bat` 电量、状态、
  电池存在性和电压，以及 `ip2317-charger` 充电器在线状态。
- `/api/v1/device/status` 增加经过范围校验的真实电池字段；接口缺失或数据非法
  时返回明确的不可用状态，不使用 Mock 数值兜底。
- 浏览器宠物主页在 `local_k1` 模式下每 5 秒显示真机电量、充电状态和电压，
  Mock 模式保持原有可调试数据。
- K1 实测 API 与 sysfs 同步，自动测试更新为 `74 passed`。

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
- 7 个安全预设动作、异步结果、重复请求幂等和离线拒绝。
- SQLite 持久化，进程重启后恢复性格、亲密度和历史。
- API Key、动作串行、停止取消、超时和退出清理。
- Local K1 固定 systemd 服务状态查询、对话唤醒/打断和实时阶段显示。
- Local K1 真实电量、充电状态、电池电压和充电器在线检测。
- 火山引擎 K1 补丁、唤醒词入口和硬件应用源码。

Local K1 的真实电机动作仍返回 `501`。对话 API 只允许向
`volc-conv-ai.service` 发送固定的 `SIGUSR1`/`SIGUSR2`；产品 Mock 的对话
仍是本地模板，逻辑音色 ID 未映射到火山引擎真实音色。

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
```

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
  `SIGUSR1`/`SIGUSR2`。
- 不执行 `systemctl start/stop/restart`，也不接受请求传入任意信号或服务名。
- 不执行真实电机、DDS 或 ROS2 操作。
- 首次开放真机动作前，必须确认独立停止接口并把设备放在安全空旷区域。
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
