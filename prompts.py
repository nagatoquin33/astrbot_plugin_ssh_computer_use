"""文案集中管理：帮助文本与 LLM 系统提示注入。"""

HELP_TEXT = """📡 SSH 远程计算机操控
当前主机：{host}
━━━━━━━━━━━━━━━
ssh 列表 / 使用 <名称> / 连接 [名称]
ssh 执行 <命令> ｜ ssh ps <PowerShell脚本>
ssh 截图 ｜ 点击 <x> <y> [左|右|双] ｜ 移动 <x> <y>
ssh 输入 <文本> ｜ 按键 <ctrl+c> ｜ 滚动 [n] ｜ 屏幕
ssh 剪贴板 取 ｜ 剪贴板 存 <文本>
ssh 上传 <远程路径> ｜ ssh 下载 <远程路径>（同一条消息里带上文件）
ssh 代理 安装|状态|重启
ssh 诊断
━━━━━━━━━━━━━━━
也可直接对我说自然语言，我会调用相应工具完成操作。"""

COMPUTER_USE_GUIDE = """

## 远程计算机操控能力
你可以通过工具操控远程主机「{host}」。规则：
- ssh_exec：执行 shell 命令（Windows 经 cmd、Linux 经 bash；编码已自动处理）。
- computer_screenshot：截取远程桌面，你会直接看到图像；操作 GUI 前后务必截图确认。
- computer_click/computer_move/computer_scroll/computer_type/computer_press_key：键鼠控制，
  坐标使用最近一张截图像素坐标（点击工具会自动换算为屏幕坐标）。
- computer_exec：在用户会话内执行命令（可访问桌面）；computer_start：启动程序/URL 不等待。
- ssh_read_file / ssh_write_file：读写远程文件；ssh_run_powershell：Windows 上执行 PowerShell 脚本。
- 长驻程序请用 computer_start 或后台方式（Windows: start，Linux: nohup ... &），避免超时。
- 严禁绕过工具直连目标机 127.0.0.1 的 agent 端口或自造协议字段；所有操作一律通过
  computer_* / ssh_* 工具完成，坐标/按键等参数以工具说明为准。
- 完成操作后向用户简要汇报；涉及删除/覆盖/重启等破坏性操作前先与用户确认。
"""
