# K1 双眼自定义显示调查

调查日期：2026-08-10  
调查人：Mars

## 结论

K1 头部屏幕能够显示自定义 GIF 或 PNG。当前 `toy_ui.service` 使用
`/usr/bin/toy_ui`（`ai-toy 1.1.10`），通过 CycloneDDS 接收 `ToyCommand_Msg`
中的 `CUSTOM_MEDIA` 指令，再从固定资源目录加载图片。暂时不应替换该程序或直接
开放任意文件路径，因为仓库中没有与板端 1.1.10 完全一致的 DDS IDL 和发布端源码。

## 已确认信息

- 屏幕逻辑分辨率为 `932 x 466`。
- QML 使用同一张完整图片生成左右两个圆形眼睛区域。
- 已有资源位于 `/usr/local/share/sourcefiles/elephant/gif/`，包括多种双眼 GIF
  和低电量 PNG。
- 板端日志确认 `CUSTOM_MEDIA` 可切换固定 GIF，例如
  `04_dual_eye_shake.gif`。
- 本地旧版 UI 源码能说明渲染方式，但不包含板端现用的 DDS 接口，不能直接覆盖
  `/usr/bin/toy_ui`。

## 推荐实现

1. 从厂商取得 `ai-toy 1.1.10` 对应源码或准确的 `ToyCommand` IDL。
2. 新增只接受预设图片 ID 的显示适配器，不允许 HTTP 传入文件系统路径。
3. 将审核后的 `932 x 466` GIF/PNG 安装到独立只读资源目录。
4. 通过 DDS 发布固定 `CUSTOM_MEDIA` 指令，并增加恢复默认眼睛、超时和重复调用测试。
5. 在真机上检查左右眼裁切、帧率、内存占用以及与低电量/动作动画的优先级。

在取得精确 DDS 契约之前，本阶段只保留调查结果，不修改正在运行的双眼显示服务。
