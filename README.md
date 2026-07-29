# AI Cat Controller

SpaceMIT K1 AI 猫的独立控制仓库。当前版本保存已经验证的硬件控制入口、
火山引擎对话适配补丁、K1 systemd 配置和验证记录；后续将在此仓库实现
FastAPI 服务和手机/电脑浏览器控制页面。

## 当前状态

- K1 头部、尾部和上下电机命令入口已保留。
- 火山引擎低负载 WebSocket 对话修改已通过补丁和 overlay 保存。
- “小安小安”唤醒入口及 systemd 服务已保存。
- FastAPI 控制层尚未实现，目标架构见 `docs/architecture.md`。
- 厂商完整 SDK、模型、预编译库、构建产物和真实密钥均不在本仓库中。

## 目录

```text
native/ai-toy-app/                 K1 硬件命令应用层
native/dialog/                     可直接阅读的对话主程序
native/wake-word/                  本地唤醒词入口
integrations/volcengine-k1/        上游锁定、补丁、overlay 和恢复脚本
docs/                              架构、依赖、部署和验证记录
src/ai_cat_controller/             后续 FastAPI Python 包
tests/                             后续单元和集成测试
```

## 恢复火山引擎 K1 工程

本仓库不会分发完整火山 SDK。执行以下命令会克隆锁定的上游版本、应用补丁并
复制 K1 新增文件：

```bash
./integrations/volcengine-k1/scripts/prepare_sdk.sh \
  "$HOME/ConversationalAI-Embedded-Kit-2.0"
```

然后按照生成工程中的
`examples/low_load_solution/linux_k1/README.md` 配置和构建。

## 安全原则

- 不提供任意 Shell 命令 API。
- 硬件动作只能映射到固定可执行文件和参数。
- 电机动作必须串行、限时并校验返回码。
- API 使用密钥认证，默认只建议在受信任局域网开放。
- 不提交 `.env`、`conv_ai_config.json`、设备鉴权缓存或云端密钥。

## 上游与许可证

本项目采用 Apache-2.0 许可证。厂商来源和适用许可证见 `NOTICE` 与
`docs/vendor-dependencies.md`。
