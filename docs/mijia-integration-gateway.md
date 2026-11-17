# 米家接入准备与网关契约

## 当前阶段

当前完成的是米家接入前的网页与服务端边界，不代表设备已经加入米家：

- 网页增加“米家连接”页，可准备或清除接入资料，并查看计划开放的状态、操作和通知。
- SQLite 保存当前用户、宠物和隔离网关设备标识的准备状态。
- 网关提供固定的设备快照、属性读取、音量写入和动作调用接口。
- `platform_linked` 与 `credentials_configured` 当前固定为 `false`。
- 不保存米家账号、访问令牌、刷新令牌、应用密钥或回调地址。
- `miot_model`、`siid`、`piid`、`aiid`、`eiid` 当前均为 `null`，等待正式产品
  SPEC 确认后映射，不能用猜测值占位。

网页入口为 `http://127.0.0.1:8000/control`，登录并绑定设备后选择“米家”。

## 调用链

```text
浏览器登录会话
    │
    ├── 查询能力和准备状态
    └── 准备/清除接入资料
             │
             ▼
       MijiaGatewayService ── SQLite
             ▲
             │ 固定属性和动作，不接受原始硬件参数
       未来米家连接器
```

正式连接器应部署在受控云端，通过这里的网关契约访问业务能力。不要把 K1 或本地
开发服务直接暴露到公网。

## 接入准备接口

这些接口供当前网页使用，除全局 `X-API-Key` 配置外，还要求
`X-Mock-Session`，并校验宠物属于当前用户。

| Method | Path | 说明 |
|---|---|---|
| `GET` | `/api/v1/integrations/mijia/pets/{pet_id}/status` | 查询准备状态。 |
| `GET` | `/api/v1/integrations/mijia/pets/{pet_id}/spec` | 查询固定语义能力草案。 |
| `POST` | `/api/v1/integrations/mijia/pets/{pet_id}/prepare` | 生成或恢复准备状态。 |
| `POST` | `/api/v1/integrations/mijia/pets/{pet_id}/remove` | 撤销当前网关标识。 |

准备和清除请求只接受：

```json
{
  "request_id": "mijia-prepare-20260812-001"
}
```

同一准备请求重放返回 `duplicate=true`；已准备时使用新的请求号不会生成第二个网关
标识。清除后再次准备会生成新标识，旧标识立即失效。解绑宠物也会撤销当前标识，
避免设备转移后旧连接器继续读取或控制。

## 网关接口

网关接口不使用浏览器会话头，生产环境必须配置全局 `X-API-Key`，并在其外层增加
HTTPS、连接器身份认证、密钥轮换、限流和审计。当前本地环境若未配置全局密钥，
仅适用于回环地址或受信任局域网开发。

| Method | Path | 说明 |
|---|---|---|
| `GET` | `/api/v1/gateway/mijia/v1/devices/{gateway_device_id}/snapshot` | 获取用户态设备快照。 |
| `POST` | `/api/v1/gateway/mijia/v1/devices/{gateway_device_id}/properties/read` | 批量读取白名单属性。 |
| `POST` | `/api/v1/gateway/mijia/v1/devices/{gateway_device_id}/properties/write` | 写入音量。 |
| `POST` | `/api/v1/gateway/mijia/v1/devices/{gateway_device_id}/actions/{action_id}` | 调用固定动作，返回 `202`。 |

快照只返回名称、在线、电量、充电、音量、亲密度、互动状态、动作状态和固定动作
标识。不返回序列号、主机名、操作系统、Python 版本、服务名或适配器模式。

属性读取示例：

```json
{
  "properties": ["online", "battery_level", "volume", "dialog_state"]
}
```

允许读取：

- `online`
- `battery_level`
- `charging`
- `volume`
- `intimacy_level`
- `intimacy_points`
- `dialog_state`
- `motion_state`

当前唯一可写属性为 `volume`：

```json
{
  "property_id": "volume",
  "value": 45,
  "request_id": "mijia-volume-20260812-001"
}
```

允许调用：

- `head_nod`
- `head_shake`
- `tail_wag`
- `stop_motion`
- `start_listening`
- `interrupt_dialog`

动作请求只接受 `request_id`，不能传入角度、速度、时长、GPIO、命令、文件路径、
音频路径、服务名或输出设备。尾部动作仍受现有硬件安全门和亲密度解锁规则限制。
音量固定写入已有受控输出，不接受任意 sink 名称。

网关写入和动作以 `gateway_device_id + request_id` 持久化结果。相同请求和相同载荷
重放返回 `duplicate=true`；同一请求号被用于不同操作或不同值时返回 `409`。

## SPEC 语义草案

当前 `mijia-preparation-v1` 先固定业务语义，再等待平台分配实例编号：

| 类型 | 数量 | 当前状态 |
|---|---:|---|
| 服务 | 1 | `ai_cat_companion`，未映射 `siid`。 |
| 属性 | 8 | 已有读写白名单，未映射 `siid/piid`。 |
| 动作 | 6 | 已有固定业务映射，未映射 `siid/aiid`。 |
| 事件 | 3 | 仅声明动作完成、等级变化、设备异常，尚未上报，未映射 `siid/eiid`。 |

米家官方 SPEC 测试会根据产品实例中的属性和动作生成测试项，并对实际上报与产品
SPEC 做对比；未使用的 SPEC 项应删除。因此本阶段不提前虚构实例编号或声明未实现
能力：

- [SPEC 协议规范](https://autotest.iot.mi.com/acsLanding/%E6%B5%8B%E8%AF%95%E5%B7%A5%E5%85%B7/SPEC%E5%8D%8F%E8%AE%AE%E8%A7%84%E8%8C%83/)
- [SPEC 测试工具帮助](https://autotest.iot.mi.com/acsLanding/%E6%B5%8B%E8%AF%95%E5%B7%A5%E5%85%B7/SPEC%E6%B5%8B%E8%AF%95%E5%B7%A5%E5%85%B7/%E5%B8%AE%E5%8A%A9%E6%96%87%E6%A1%A3.html)
- [SPEC 失败原因分析](https://autotest.iot.mi.com/acsLanding/%E6%B5%8B%E8%AF%95%E5%B7%A5%E5%85%B7/SPEC%E6%B5%8B%E8%AF%95%E5%B7%A5%E5%85%B7/%E5%A4%B1%E8%B4%A5%E5%8E%9F%E5%9B%A0%E5%88%86%E6%9E%90.html)

## 正式接入待办

1. 注册并审核米家开放平台主体、产品品类和网络接入方式。
2. 在正式产品实例中删减未实现项，确认 `miot_model` 以及全部实例编号、数据类型、
   范围、读写和通知约束。
3. 实现云端连接器、平台要求的认证授权、签名校验、令牌加密存储和轮换；凭据不落
   K1 仓库或浏览器。
4. 增加属性变化队列、事件上报、失败重试、乱序/重复处理、限流和审计记录；只上报
   实际变化的属性。
5. 用平台开发者账号和在线设备跑 SPEC、控制、异常码、离线恢复及认证测试。
6. 完成隐私说明、账号解绑、设备转移和凭据吊销验收后，才把网页状态改为正式连接。

真实平台密钥、产品数据或 K1 硬件参数变更需要单独确认；本阶段没有修改这些内容。
