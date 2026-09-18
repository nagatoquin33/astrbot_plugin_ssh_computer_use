---
name: ssh-computer-use
description: 使用 computer_* 工具远程操控 Windows/Linux 桌面（GUI 自动化）时查阅：标准操作循环、坐标规则、输入技巧、长驻程序、破坏性操作确认与常见故障排查。
---

# 远程桌面 GUI 操控指南（ssh_computer_use）

## 标准操作循环

1. `computer_screenshot` 截图（图像直接进入你的视觉输入）
2. 在截图上定位目标元素，取其**图像像素坐标**
3. `computer_click` / `computer_type` / `computer_press_key` 执行操作
4. 再次 `computer_screenshot` 确认结果，不符则调整重试

## 坐标规则

- 截图结果中包含「图像尺寸」与「屏幕尺寸/虚拟屏原点」
- `computer_click` / `computer_move` 接受**最近一张截图的图像像素坐标**，工具会自动换算为屏幕坐标
- 同一界面内连续操作，坐标持续有效；窗口移动、最小化、分辨率变化后**必须重新截图**

## 输入技巧

- 中文与特殊字符用 `computer_type`（支持中文，回车用 `\n` 表示）
- 组合键用 `computer_press_key`：`ctrl+c`、`alt+tab`、`win+d`、`f5` 等
- 滚轮用 `computer_scroll`：正数向上、负数向下，单位为齿格
- 点击输入框获得焦点后再输入文本

## 窗口管理（Windows）

- `computer_window(action="list")` 列出所有可见窗口（标题、进程、pid）
- `computer_window(action="focus", query="chrome")` 把窗口置前并还原（切换窗口首选，比点击任务栏可靠）
- `computer_window(action="minimize"/"maximize"/"restore", query=...)`
- `computer_window(action="close", query=...)` 关闭窗口——**先与用户确认**
- query 是标题或进程名的子串；操作前先 list 确认窗口存在

## 长驻与后台程序

- 启动 GUI 程序、URL、文档：`computer_start`（不等待退出）
- 命令行长驻进程用后台方式：Windows `start /b ...`，Linux `nohup ... &`
- 直接前台执行长驻程序会拖到超时，且输出对任务无意义

## 安全

- 删除、覆盖、重启等破坏性操作：先向用户确认，再执行
- 完成后向用户简要汇报做了什么、结果如何

## 故障排查

| 症状 | 原因与处理 |
|------|-----------|
| 截图黑屏/失败 | 目标机锁屏或无人登录（Windows Agent 依赖交互会话）；解锁后重试 |
| 键鼠无反应 | `ssh 代理 状态` 确认 Agent 存活，必要时 `ssh 代理 重启` |
| Agent 端口冲突 | 改插件配置 `agent_port` 后重新 `ssh 代理 安装` |
| 看不到截图图像 | 插件需 v0.2.10+ 且 AstrBot 较新版本；发 `ssh 诊断` 检查链路 |
