# 已验证 Python 字节的受控导入

> 本地发布基础：只执行固定角色的模块导入检查，不启动服务，不代表产品上线。
> 不连接 K1、读取真实凭据、调用模型/电机/云服务或改变现有登录。

## 为什么不能只校验归档

[离线候选包](offline-release-candidate.md)的外部 SHA 和只读暂存证明收到的源码字节，
却不能证明 Python 后来实际导入了它们。旧安装、当前目录、PYTHONPATH、缓存和
导入路径回退都可能让进程执行另一版代码；单独哈希 Python 解释器也不能解决。

新 `release_runtime.py` 是独立的受信工具，使用候选包同一份已验证内存快照编译
项目模块。安装内存 finder 前拒绝已经加载的 `ai_cat_controller` 命名空间；
该命名空间内不存在的模块直接拒绝，不回退到磁盘源码或 `.pyc`。校验后的归档或
暂存 Python 文件被换掉，也不会成为这次导入使用的项目字节。

## 使用边界

只在已信任的开发环境使用，工具必须来自独立受信安装位置，不能运行收到包中的
`release_runtime.py` 来证明那个包可信。相邻 `release_bundle.py` 同属信任根；
它以源码稳定读取后编译，不让旁边的缓存替代校验器源码。

直接入口要求 `-I -S`：隔离当前目录、PYTHONPATH、用户站点，且不执行 `site`、
`.pth` 和 `sitecustomize`。依赖目录须由操作者显式提供；这不证明依赖自身可信。
`VerifiedRuntime` 的 API 也只用于一次性的新进程：安装会清理 `AI_CAT_*` 并更改
导入环境，关闭后保留项目命名空间围栏，不恢复成可回退磁盘的状态。不能嵌入
当前正在运行的控制器、网关或开发者工具进程。
解释器、标准库、原生加载器及显式依赖仍属受信环境。`LD_PRELOAD` 等影响发生在
脚本执行之前，必须由受信父进程/后续部署配置保证启动环境干净，不能靠脚本内
清环境声称防御任意原生注入。

`--resource-root` 指向已暂存候选的 `payload`，不是 `src` 或包目录。启动时核对固定
网页资源和 WAV 与候选快照一致；模块的 `__file__` 仍指向该资源树，保证原有
StaticFiles/Jinja 路径可用。之后直接按路径读取的模板、静态文件和语音资产并非
内存不可变资源，不能把 Python 导入身份扩大成所有资源的运行期身份。

示意命令（不是设备部署指令，SHA 必须来自独立受信记录）：

```bash
/trusted/venv/bin/python -I -S /trusted/tools/release_runtime.py check \
  --bundle /absolute/private-output/runtime-source.tar \
  --sha256 VERIFIED_64_CHARACTER_SHA256 \
  --resource-root /absolute/private-output/new-candidate/payload \
  --dependency-root /trusted/venv/lib/python3.12/site-packages \
  --role controller
```

固定角色为 `controller`、`gateway`、`autonomy`；分别导入应用、网关应用和自主模块，
不接受任意模块名/表达式，不调用 `main`、`uvicorn.run` 或自主工作循环。
检查先清除 ambient `AI_CAT_*` 设置，不继承真实设备模式、云凭据或协调目录；
导入成功也不代表服务生命周期、账本、授权、云端或运动已经通过验收。

成功摘要明确为 `verified-python-import`，包含制品/manifest SHA 和实际执行模块的
源码 SHA；`dependencies_verified`、`resources_immutable`、`production_ready` 均为
`false`。这不是 HTTP 健康/ready 接口、活进程握手或维护授权票据。

## 尚未完成

真正的版本化服务启动、显式私有配置交接、依赖锁与目标平台制品、原生二进制
来源、进程身份绑定、全参与者活握手和未知写者隔离仍待完成。受信项目代码不是
任意 Python 沙箱；同 UID 恶意修改、管理员替换工具或依赖、动态导入其它命名空间
不在本机制的安全承诺内。危险接口保持禁用，不因此部署或更改当前服务。

验收合同见[跨进程维护协议](process-maintenance-protocol.md)。

## 本阶段验证（2026-10-21）

- 既有发布工具基线 89 项通过；最终直接目标 181 项通过，相关原生目标 302 项通过
  （有重叠，不相加当作全量）。内存导入 34 单测与 17 独立集成测试通过。
- 独立 `-I -S` 子进程覆盖真实执行标记、精确模块哈希集合、旧源码/有效 pyc、
  未知模块的依赖树及 stage 回退、预加载命名空间、环境/`.pth`、换包、资源及
  stage 完成记录异常。执行对象被删除/替换/置 None 后身份永久失效；关闭后仍
  保留围栏。调试源码也固定为验证字节，支持 BOM/换行，不从可变文件补 traceback。
- 对本仓库完整候选实际执行三个角色导入；另在独立子进程进入真实 Mock 应用
  生命周期，所有可写 Path 都改到临时目录，健康、首页、CSS 返回 200。没有调用
  `uvicorn.run`、真实网关、硬件、语音模型或云服务。
- 新实际候选为 111 文件、13,564,806 字节 payload，外部 SHA 为
  `7f0abc3a6babb126cbca1ce27248aecce9ba167b21f9c34bde3227e90d63ec37`。
  三角色分别实际导入 61/22/10 个项目模块；独立 Node 校验器将每项源码 SHA 与
  manifest 对比，并在全量后复核归档、stage 和当前源码一致，文件 0444、目录 0555。
- 最终 Python 全量 **2055 passed**，87.11 秒，1 条已有 httpx/Starlette 弃用警告；
  小程序 `npm test` 全套通过，无 skip/xfail。8 个直接代码/测试/配置输入全量前后
  SHA 一致，全部 111 项候选输入也独立逐项复核一致。

本轮语音初始化退出的两个新回归先失败后通过：模型或首次 VAD 初始化阻塞期间
收到 SIGTERM，退出仍须持原 SH 到模型析构实际完成。旧正向用例的非阻塞 admission
竞争改为真实清理握手，保留原业务断言，不改变协议规则、唤醒词或音频参数。
这是宿主 inert SDK 验证，不是 SDK/RISC-V 构建或真机验收。

窄源码前备份、阶段源码、原始测试报告/红绿证据、完整 Python 候选、只读 stage、
独立校验脚本/结果和 SHA 位于
`/home/kfc/ai-cat-miniprogram-backups/20261021-controlled-runtime-ZrwlOt4j`。
范围见其中 `VALIDATION.md`。它不是设备数据备份或完整可部署固件，不递归 chmod
已暂存的只读树。本轮未读取凭据、访问 K1、部署/重启、改桌面工程/登录或提交 Git。
