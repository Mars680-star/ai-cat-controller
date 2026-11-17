# 离线 Python 源码候选包

> 本地发布准备，不是部署命令。不会安装依赖、执行包内代码、修改设备数据、
> 切换运行版本或重启服务。当前仍没有完成正式产品上线。

## 它与现有备份的区别

`k1_backup_release.sh`与`k1_rollback_release.sh`是设备私有恢复工具，会涉及数据库、
环境文件、运行目录和服务；不能把其归档当作公开发行包，也不能用回滚脚本接收
未经审核的离线包。本轮没有执行或修改这两个脚本。

新工具单独构建确定性的Python源码候选包。固定、经过审查的清单包含当前应用
全部Python模块、网页资源、构建元数据、许可及21个固定本地语音资产。清单成员
缺失或出现未审查的运行时文件会拒绝；新增模块须显式更新发布策略，不静默漏包。

不打包Git、虚拟环境、数据库、环境配置、AppSecret、网页授权、运行日志、缓存、
原始录音、微信桌面配置或外部SDK。候选包不是完整固件；不包含Python依赖、
RISC-V原生二进制、声学模型或设备校准文件。只读源码也不代表实际运行进程已经
加载这个版本，更不能据此宣告全参与者就绪。

## 生成、核验与暂存

在已信任、已安装项目的开发环境使用模块入口；不要运行收到包内的校验器来证明
那个包可信。下方`-I`隔离当前目录与PYTHONPATH的模块注入；不隔离已安装环境本身。
不要对AI猫运行以下命令来代替正式部署。
输出文件和暂存目录必须是新目标，父目录由维护者事先准备；暂存父目录须由当前
用户所有、权限0700。不覆盖既有路径，也不自动新建宽权限父目录。

```bash
.venv/bin/python -I -m ai_cat_controller.release_bundle build \
  --source /home/kfc/ai-cat-controller \
  --output /absolute/private-output/runtime-source.tar
```

生成方应将SHA-256保存在独立受信发布记录中。接收方必须从该独立渠道取得期望值，
不能把包内manifest自报值，或与归档一起被替换的校验文件，当作来源认证。

```bash
.venv/bin/python -I -m ai_cat_controller.release_bundle inspect \
  --bundle /absolute/private-output/runtime-source.tar \
  --sha256 VERIFIED_64_CHARACTER_SHA256

.venv/bin/python -I -m ai_cat_controller.release_bundle stage \
  --bundle /absolute/private-output/runtime-source.tar \
  --sha256 VERIFIED_64_CHARACTER_SHA256 \
  --destination /absolute/private-output/new-candidate
```

inspect不运行包内Python。stage先完成全部校验，在私有随机hidden目录构建完整只读
树，再以Linux不覆盖重命名原子发布到新目标；不使用通用`extractall`、不覆盖现有
目录、不更新`current`链接、不写任何运行配置或数据库。libc或文件系统不支持
`RENAME_NOREPLACE`时拒绝，不降级为可能覆盖目标的rename；语义见
[Linux rename手册](https://man7.org/linux/man-pages/man2/rename.2.html)。
仅用于本机Linux文件系统和受信私有父目录；不据此认证网络文件系统、同UID恶意
并行篡改或管理员操作。目标平台和文件系统仍需独立验收。
内容和权限全部完成后，先在hidden树写入并同步`STAGED.json`的`status=staged`与摘要，
再原子发布新名称。hidden树不是可用目的目录；缺失、空或损坏的完成记录也不完整。
记录只表示源码暂存，不是发布/运行/就绪证明。失败时不会把已有目录当作待清理
目标；错误可能留下本轮hidden目录或已发布但清空完成记录的新目录，均不能当作
合格候选版本，不能原地重试覆盖。掉电持久性仍须在目标文件系统验收。

## 固定格式与验证边界

采用有界的未压缩USTAR，不接受压缩变体、PAX/GNU扩展、链接、设备条目、额外或
重复成员、非规范路径及穿越。先读取有界稳定字节副本并验证外部SHA，再核manifest
固定schema、完整文件集合、每个大小和内容哈希，避免核验与后续读取之间换包。
构建时不执行待打包源码，拒绝跟随符号链接及读取FIFO等特殊文件。
维护者仍需冻结构建源树：逐文件稳定读取不是跨多个文件的一致性文件系统快照，
候选包记录的是实际纳入包中的字节，不冒称干净Git提交或未发生并发编辑。

manifest明确标注`python-source-candidate`、原生未构建和依赖未随包提供。
secret检查只能降低误带已知敏感材料的风险，不是完整秘密审计；维护者仍需审查
候选内容和来源。固定清单、哈希、文件只读模式都不能防止有权限的管理员替换代码。

## 后续上线门槛

还需要受信发布来源/签名策略、目标平台依赖锁定与离线依赖制品、SDK/RISC-V构建
和独立二进制来源证明、版本化受控启动与实际加载身份、全参与者活握手、正式
部署/回滚审批及真实设备验收。没有这些条件时，不能仅凭inspect或stage成功启用
危险操作，更不能将本地候选包标为整个产品已经上线。

进程协议与验收要求见[维护协议](process-maintenance-protocol.md)。

后续已增加[固定角色的内存 Python 导入检查](verified-python-import.md)，将外部
SHA 校验后的同一快照交给项目模块 loader；它不调用服务入口，不等于真实受控
启动、依赖/资源运行身份或全参与者就绪。新增 `release_runtime.py` 已显式列入
候选策略；下列 110 项统计仅指上一阶段被冻结的旧候选包，不能当作新包的清单。

## 上一阶段本地验证（2026-10-21）

84项发布工具单测与5项独立真实CLI回归通过。覆盖完整110成员清单、21个固定WAV、
未执行候选代码、独立外SHA、恶意USTAR/JSON、链接/FIFO/限额、父目录与命名inode
替换、并发publisher只有一个胜者、目标碰撞不覆盖、原子API不可用拒绝，以及失败
清空本轮seal。CLI还验证了隔离模式不导入恶意当前目录或PYTHONPATH中的包。

本轮实际构建、inspect及原子stage均成功，110个文件、13,549,799字节payload；用
独立Node校验器逐文件核对归档清单SHA、现有源码及暂存内容一致，全部文件0444、
目录0555。该证据不代表目标设备文件系统/断电/实际加载版本通过验收。

最终Python全量**2002 passed**，66.30秒，1条已有httpx弃用警告；小程序全套回归
通过。代码和候选包输入在全量前后校验一致。没有安装依赖、部署、运行候选服务、
重启现有服务或执行真实数据/硬件操作，也没有改桌面工程和登录数据。

源码前快照、阶段源码、实际候选包与只读树、红绿/全量报告和SHA记录位于
`/home/kfc/ai-cat-miniprogram-backups/20261021-release-foundation-dIcvqDIq`。
具体范围见该目录VALIDATION.md；源码候选包与窄阶段快照均不是完整可部署固件。
