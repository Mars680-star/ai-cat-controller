# 成长人格 V1 部署与回滚

## 安全边界

首次部署必须保持：

```dotenv
AI_CAT_ENABLE_GROWTH_PERSONALITY_V1=false
AI_CAT_ENABLE_DEBUG_GROWTH=false
AI_CAT_DEBUG_UNLOCK_ALL_ACTIONS=false
AI_CAT_DEBUG_UNLIMITED_TOUCH_INTIMACY=false
```

总开关关闭时保留成长数据的只读查询，但不记录成长事件，也不把成长指令写入
火山运行时 Prompt。数据库新增表是增量创建，不会清空用户、宠物、性格、亲密度
或对话历史。

## 部署前备份

在 K1 上使用 `root` 执行：

```bash
cd /opt/ai-cat-controller
AI_CAT_PROJECT_DIR=/opt/ai-cat-controller ./scripts/k1_backup_release.sh
```

命令会短暂停止 FastAPI，保存以下内容后恢复原服务状态：

- SQLite 一致性副本；
- 项目代码，不包含 `.venv`、`.git` 和运行数据库；
- `/etc/ai-cat-controller.env`；
- FastAPI systemd 单元；
- `/var/lib/ai-cat-controller` 运行时文件；
- 原服务 active/enabled 状态和 Git 版本信息。

备份目录权限为 `0700`，文件带 SHA-256 清单和 `.complete` 标记。部署前先验证：

```bash
./scripts/k1_rollback_release.sh \
  /root/ai-cat-backups/before-growth-v1-YYYYMMDDTHHMMSSZ-PID \
  --check
```

## 首次启动

1. 整包部署当前 Git 版本，不要只复制单个 Python 模块。
2. 确认总开关和三个 Debug 开关均为 `false`。
3. 重启 `ai-cat-controller.service`。
4. 检查 `/health`、浏览器控制页、语音历史和基础动作。
5. 检查成长接口返回 `enabled=false`，新互动的 `growth` 为 `null`。
6. 执行五部位触摸验收。

```bash
cd /opt/ai-cat-controller
./scripts/k1_verify_touch_mapping.sh
```

头部、鼻部和背部触摸一次；左右爪分别需要在 3 秒内触摸两次。脚本同时核对厂商
原始标签、逻辑部位、动作和固定短语。任何一项不一致都不能开启成长。

## 开启成长

触摸和基础回归通过后修改：

```dotenv
AI_CAT_ENABLE_GROWTH_PERSONALITY_V1=true
```

然后重启 FastAPI。首次验收只生成少量真实问题、触摸、每日见面和任务事件，确认
来源 ID 幂等、属性方向正确、Prompt 中只增加成长指令且火山音色没有变化。

## 2026-08-11 现场记录

设备：`7c2b63fd4a128`

部署恢复点：

```text
/root/ai-cat-backups/before-growth-v1-20260811T093252Z-3748
```

该恢复点的 SHA-256 清单、SQLite `integrity_check` 和回滚脚本 `--check` 均已通过。
板端已部署总开关与触摸防抖版本，成长总开关和全部 Debug 开关保持关闭。

| 部位 | 结果 | 现场证据 |
| --- | --- | --- |
| 头部 | 通过 | 4 条连续厂商记录经 1.5 秒防抖后只导入 1 个业务事件；点头和固定短语正确。 |
| 鼻部 | 硬件待检 | `toy_main` 已注册 `GPIO113`，但短触、3 秒长按及独立 60 秒 GPIO 监听均没有边沿或 `NOSE_*` 事件。 |
| 背部 | 跳过 | 用户决定本轮不继续硬件测试，未形成验收结论。 |
| 左爪 | 跳过 | 用户决定本轮不继续硬件测试，双触确认尚未现场验收。 |
| 右爪 | 跳过 | 用户决定本轮不继续硬件测试，双触确认尚未现场验收。 |

软件没有把鼻部事件映射成其他部位，也没有过滤原始 `GPIO113` 边沿；当前缺失发生
在厂商触摸输入层。修复传感器或排线后，应重新执行五部位脚本。未完成剩余触摸
验收前，生产环境继续保持 `AI_CAT_ENABLE_GROWTH_PERSONALITY_V1=false`。

## 回滚

先仅验证备份，再执行需要固定确认词的回滚：

```bash
./scripts/k1_rollback_release.sh BACKUP_DIRECTORY --check
./scripts/k1_rollback_release.sh BACKUP_DIRECTORY
# 输入 ROLLBACK
```

回滚前的现状会再次保存到 `/root/ai-cat-backups/before-rollback-*`。脚本恢复代码、
数据库、环境、运行时数据、systemd 单元和原服务状态；不删除被替换的数据。
