# AI Cat Controller

SpaceMIT K1 AI 猫的独立控制仓库。第一阶段提供 FastAPI、浏览器控制页面、
完整 Mock 适配器和只读 Local K1 框架；默认配置不会控制真实硬件。

## 当前能力

- `/control` 手机和电脑浏览器控制页面。
- `/api/v1` 稳定 REST API，可供后续微信小程序复用。
- Mock 摇头、点头、摇尾、停止、对话唤醒和打断。
- API Key、动作串行、停止取消、超时和退出清理。
- Local K1 固定 systemd 服务只读状态查询。
- 火山引擎 K1 补丁、唤醒词入口和硬件应用源码。

Local K1 的真实动作和对话控制在第一阶段返回 `501`，不会调用电机或发送信号。

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

## 安全边界

- HTTP 请求不能指定可执行文件、服务名或 Shell 参数。
- Python 代码不使用 `os.system`、`shell=True` 或 Shell 字符串拼接。
- Local K1 第一阶段只允许固定服务的 `is-active` 和 `is-enabled`。
- 不执行 `systemctl start/stop/restart/kill`。
- 不执行真实电机、DDS 或 ROS2 操作。
- 首次开放真机动作前，必须确认独立停止接口并把设备放在安全空旷区域。
- 正式远程控制需要 HTTPS、鉴权和受控中转，不能直接暴露公网端口。

## 火山引擎 SDK

本仓库不分发完整厂商 SDK。恢复方法见
`integrations/volcengine-k1/README.md`。

许可证和厂商来源见 `LICENSE`、`NOTICE` 和
`docs/vendor-dependencies.md`。
