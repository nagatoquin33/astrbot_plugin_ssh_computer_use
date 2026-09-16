"""计算机操控（GUI）类工具：截图 / 键鼠 / 会话内执行 / 剪贴板 / 信息。"""

from __future__ import annotations

import base64

import mcp.types as mcp_types
from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api import logger

from .base import MessageChain, Image, _SshToolBase, _schema, png_size


@dataclass
class ComputerScreenshotTool(_SshToolBase):
    name: str = "computer_screenshot"
    description: str = (
        "截取当前远程主机的屏幕画面。返回的图像坐标可直接用于 computer_click / computer_move（自动换算为屏幕坐标）。"
        "操作 GUI 前后都应截图确认状态。"
    )
    parameters: dict = Field(default_factory=lambda: _schema({}))

    async def call(self, context, **kwargs):
        if err := self._perm(context):
            return err
        event, target = self._resolve(context)
        cfg = self.plugin.config
        shot = await target.screenshot(
            max_width=int(cfg.get("screenshot_max_width", 1280)),
            fmt=str(cfg.get("screenshot_format", "jpeg")),
            quality=int(cfg.get("screenshot_quality", 80)),
        )
        path = self.plugin.save_screenshot(target.profile.name, shot.data)
        if target.os_type == "windows":
            coord_note = (
                f"图像 {shot.width}x{shot.height}，屏幕 {shot.screen_w}x{shot.screen_h}"
                f"（虚拟屏原点 {shot.screen_x},{shot.screen_y}）。图像坐标可直接用于点击（自动换算）。"
            )
        else:
            w, h = png_size(shot.data)
            shot.width, shot.height = w, h
            coord_note = f"图像 {w}x{h}（未缩放），坐标即屏幕坐标。"

        if cfg.get("send_screenshot_to_chat", True):
            try:
                await event.send(MessageChain(chain=[Image.fromFileSystem(str(path))]))
            except Exception as e:
                logger.warning(f"[ssh_computer_use] 截图发送到聊天失败：{e}")

        mime = "image/png" if str(cfg.get("screenshot_format", "jpeg")) == "png" else "image/jpeg"
        return mcp_types.CallToolResult(
            content=[
                mcp_types.TextContent(
                    type="text",
                    text=f"{target.profile.name} 截图已保存：{path.name}。{coord_note}",
                ),
                mcp_types.ImageContent(
                    type="image",
                    data=base64.b64encode(shot.data).decode("ascii"),
                    mimeType=mime,
                ),
            ]
        )


@dataclass
class ComputerClickTool(_SshToolBase):
    name: str = "computer_click"
    description: str = "在远程主机的屏幕上单击/双击鼠标。x/y 使用最近一次 computer_screenshot 图像中的像素坐标。"
    parameters: dict = Field(
        default_factory=lambda: _schema(
            {
                "x": {"type": "number", "description": "目标 X 坐标（截图像素）"},
                "y": {"type": "number", "description": "目标 Y 坐标（截图像素）"},
                "button": {"type": "string", "description": "left / middle / right，默认 left"},
                "double": {"type": "boolean", "description": "是否双击，默认 false"},
            },
            ["x", "y"],
        )
    )

    async def call(self, context, x: float = 0, y: float = 0,
                   button: str = "left", double: bool = False, **kwargs) -> str:
        if err := self._perm(context):
            return err
        _, target = self._resolve(context)
        return await target.gui_click(int(x), int(y), button or "left", bool(double))


@dataclass
class ComputerMoveTool(_SshToolBase):
    name: str = "computer_move"
    description: str = "把远程主机的鼠标移动到指定坐标（截图像素坐标），不点击。"
    parameters: dict = Field(
        default_factory=lambda: _schema(
            {
                "x": {"type": "number", "description": "目标 X 坐标（截图像素）"},
                "y": {"type": "number", "description": "目标 Y 坐标（截图像素）"},
            },
            ["x", "y"],
        )
    )

    async def call(self, context, x: float = 0, y: float = 0, **kwargs) -> str:
        if err := self._perm(context):
            return err
        _, target = self._resolve(context)
        return await target.gui_move(int(x), int(y))


@dataclass
class ComputerScrollTool(_SshToolBase):
    name: str = "computer_scroll"
    description: str = "在远程主机上滚动鼠标滚轮。amount 为滚轮齿数，正数向上、负数向下。"
    parameters: dict = Field(
        default_factory=lambda: _schema(
            {"amount": {"type": "number", "description": "滚轮齿数，正上负下，如 3 或 -3"}},
            ["amount"],
        )
    )

    async def call(self, context, amount: int = 0, **kwargs) -> str:
        if err := self._perm(context):
            return err
        _, target = self._resolve(context)
        return await target.gui_scroll(int(amount))


@dataclass
class ComputerTypeTool(_SshToolBase):
    name: str = "computer_type"
    description: str = "在远程主机上输入文本（相当于键盘逐字输入，支持中文；回车用 \\n 表示）。输入前请先点击目标输入框。"
    parameters: dict = Field(
        default_factory=lambda: _schema(
            {"text": {"type": "string", "description": "要输入的文本"}}, ["text"]
        )
    )

    async def call(self, context, text: str = "", **kwargs) -> str:
        if err := self._perm(context):
            return err
        _, target = self._resolve(context)
        return await target.gui_type(text)


@dataclass
class ComputerPressKeyTool(_SshToolBase):
    name: str = "computer_press_key"
    description: str = (
        "在远程主机上按下组合键，如 ctrl+c、win、alt+tab、enter、f5、win+d。"
        "支持修饰键 ctrl/alt/shift/win 和 enter/esc/tab/up/down/left/right/f1-f12 等。"
    )
    parameters: dict = Field(
        default_factory=lambda: _schema(
            {"keys": {"type": "string", "description": "按键组合，如 ctrl+shift+esc"}}, ["keys"]
        )
    )

    async def call(self, context, keys: str = "", **kwargs) -> str:
        if err := self._perm(context):
            return err
        _, target = self._resolve(context)
        return await target.gui_key(keys.strip())


@dataclass
class ComputerExecTool(_SshToolBase):
    name: str = "computer_exec"
    description: str = (
        "在远程主机的交互式用户会话内执行命令（与 ssh_exec 的区别：可访问桌面、启动 GUI 程序）。"
        "Windows 走 cmd（如 tasklist | findstr chrome）；Linux 走 bash。"
    )
    parameters: dict = Field(
        default_factory=lambda: _schema(
            {
                "command": {"type": "string", "description": "要执行的命令"},
                "timeout_sec": {"type": "number", "description": "超时秒数，默认 30"},
            },
            ["command"],
        )
    )

    async def call(self, context, command: str = "", timeout_sec: int = 30, **kwargs) -> str:
        if err := self._perm(context):
            return err
        _, target = self._resolve(context)
        if blocked := self.plugin.pool.check_blocked(command):
            return f"命令被黑名单规则拦截：{blocked}"
        res = await target.gui_exec(command, int(timeout_sec) or 30)
        return res.format(self._max_chars)


@dataclass
class ComputerStartTool(_SshToolBase):
    name: str = "computer_start"
    description: str = "在远程主机上启动一个程序或打开一个文件（不等待其退出），如 notepad、calc、https://example.com。"
    parameters: dict = Field(
        default_factory=lambda: _schema(
            {"command": {"type": "string", "description": "程序/文件/URL"}}, ["command"]
        )
    )

    async def call(self, context, command: str = "", **kwargs) -> str:
        if err := self._perm(context):
            return err
        _, target = self._resolve(context)
        return await target.gui_start(command.strip())


@dataclass
class ComputerInfoTool(_SshToolBase):
    name: str = "computer_info"
    description: str = "获取远程主机屏幕信息（分辨率、虚拟屏边界、GUI Agent 状态等）。"
    parameters: dict = Field(default_factory=lambda: _schema({}))

    async def call(self, context, **kwargs) -> str:
        if err := self._perm(context):
            return err
        _, target = self._resolve(context)
        if target.os_type == "windows":
            info = await target.gui_info()
            s = info.get("screen", {})
            p = info.get("primary", {})
            return (
                f"主机 {info.get('machine')}，用户 {info.get('user')}，Agent v{info.get('version')}。"
                f"虚拟屏幕：{s.get('w')}x{s.get('h')} @ ({s.get('x')},{s.get('y')})；"
                f"主屏：{p.get('w')}x{p.get('h')}。"
            )
        res = await target.exec(
            "xdpyinfo 2>/dev/null | grep dimensions || xrandr 2>/dev/null | grep ' connected' || true",
            15,
        )
        return res.format(1000) or "未能获取屏幕信息（可能未安装 x11-utils/xrandr）"


@dataclass
class ComputerClipboardTool(_SshToolBase):
    name: str = "computer_clipboard"
    description: str = "读取或写入远程主机的剪贴板。action 为 get 或 set；set 时需要 text。"
    parameters: dict = Field(
        default_factory=lambda: _schema(
            {
                "action": {"type": "string", "description": "get / set"},
                "text": {"type": "string", "description": "action 为 set 时要写入的文本"},
            },
            ["action"],
        )
    )

    async def call(self, context, action: str = "get", text: str = "", **kwargs) -> str:
        if err := self._perm(context):
            return err
        _, target = self._resolve(context)
        if (action or "get").lower() == "set":
            return await target.clipboard_set(text)
        content = await target.clipboard_get()
        return content[: self._max_chars]
