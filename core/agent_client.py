"""Windows GUI Agent 客户端。

Agent（agent/win_agent.ps1）部署在目标机的交互式用户会话中，监听 127.0.0.1，
本客户端经 SSH 端口转发与其通信：每个 TCP 连接发送一行 UTF-8 JSON 请求，
回一行 JSON 应答。坐标在"截图像素空间"与"屏幕空间"之间自动换算。
"""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path

import asyncssh

from astrbot.api import logger

from .errors import AgentError
from .models import CommandResult, Screenshot

AGENT_TASK_NAME = "AstrBotSshAgent"
PLUGIN_ROOT = Path(__file__).resolve().parent.parent
WIN_AGENT_PS1 = PLUGIN_ROOT / "agent" / "win_agent.ps1"

# 截图 base64 单行可达数 MB；asyncio readline 默认 64KB 行限制会抛
# "Separator is not found, and chunk exceed the limit"，必须放大
AGENT_LINE_LIMIT = 64 * 1024 * 1024


class WinAgentClient:
    """绑定到一台 WindowsTarget 的 Agent 客户端。"""

    def __init__(self, target):
        self._t = target
        self._listener: asyncssh.SSHListener | None = None
        self._lock = asyncio.Lock()
        self._req_id = 0
        self._last_shot: dict | None = None

    # ---------------- 端口转发与请求 ----------------

    @property
    def port(self) -> int:
        return self._t.profile.agent_port or int(self._t.cfg.get("agent_port", 7622))

    def _close_listener(self):
        if self._listener is not None:
            try:
                self._listener.close()
                wait_closed = getattr(self._listener, "wait_closed", None)
                if wait_closed:
                    asyncio.ensure_future(wait_closed())
            except Exception:
                pass
            self._listener = None

    async def call(self, op: str, timeout: float = 60, **params) -> dict:
        """发送一条 JSON 请求并返回 data 字段。"""
        async with self._lock:
            conn = await self._t._get_conn()
            if self._listener is None:
                try:
                    self._listener = await conn.forward_local_port(
                        "127.0.0.1", 0, "127.0.0.1", self.port
                    )
                except (asyncssh.Error, OSError) as e:
                    raise AgentError(f"建立到 Agent 的端口转发失败：{e}")
            local_port = self._listener.get_port()

            self._req_id += 1
            req_id = self._req_id
            payload = json.dumps({"id": req_id, "op": op, **params}, ensure_ascii=False)

            reader = writer = None
            try:
                reader, writer = await asyncio.wait_for(
                    asyncio.open_connection("127.0.0.1", local_port, limit=AGENT_LINE_LIMIT), 6
                )
                writer.write((payload + "\n").encode("utf-8"))
                await writer.drain()
                line = await asyncio.wait_for(reader.readline(), timeout)
                if not line:
                    raise AgentError("Agent 无响应（连接被关闭）")
                resp = json.loads(line.decode("utf-8"))
            except asyncio.TimeoutError:
                raise AgentError(f"Agent 响应超时（op={op}, {timeout}s）")
            except (OSError, ConnectionError) as e:
                self._close_listener()  # 转发通道可能失效，下次重建
                raise AgentError(
                    f"无法连接 GUI Agent（{e}）。请在目标机执行：ssh 代理 安装 "
                    f"（或确认 Agent 已启动且监听 {self.port}）"
                )
            finally:
                if writer is not None:
                    try:
                        writer.close()
                    except Exception:
                        pass

            if not resp.get("ok"):
                raise AgentError(f"Agent 返回错误（op={op}）：{resp.get('err', '未知')}")
            return resp.get("data") or {}

    async def close(self):
        self._close_listener()

    # ---------------- 截图与坐标换算 ----------------

    async def screenshot(
        self, max_width: int = 0, fmt: str = "jpeg", quality: int = 80
    ) -> Screenshot:
        d = await self.call(
            "screenshot", timeout=60, max_width=max_width, format=fmt, quality=quality
        )
        self._last_shot = {
            "img_w": int(d.get("w", 1)),
            "img_h": int(d.get("h", 1)),
            "sx": int(d.get("sx", 0)),
            "sy": int(d.get("sy", 0)),
            "sw": int(d.get("sw", d.get("w", 1))),
            "sh": int(d.get("sh", d.get("h", 1))),
        }
        return Screenshot(
            data=base64.b64decode(d["b64"]),
            width=self._last_shot["img_w"],
            height=self._last_shot["img_h"],
            screen_x=self._last_shot["sx"],
            screen_y=self._last_shot["sy"],
            screen_w=self._last_shot["sw"],
            screen_h=self._last_shot["sh"],
        )

    def map_coords(self, x: int, y: int, space: str = "image") -> tuple[int, int]:
        """把图像空间坐标换算为屏幕空间坐标。"""
        ls = self._last_shot
        if space != "image" or not ls:
            return int(x), int(y)
        try:
            rx = ls["sx"] + round(float(x) * ls["sw"] / ls["img_w"])
            ry = ls["sy"] + round(float(y) * ls["sh"] / ls["img_h"])
            return int(rx), int(ry)
        except (ZeroDivisionError, KeyError, ValueError):
            return int(x), int(y)

    # ---------------- GUI 操作 ----------------

    async def click(self, x: int, y: int, button: str = "left", double: bool = False,
                    space: str = "image") -> str:
        rx, ry = self.map_coords(x, y, space)
        await self.call("click", timeout=30, x=rx, y=ry, button=button, double=double)
        return f"已在屏幕坐标 ({rx}, {ry}) 完成{'双击' if double else '单击'}（{button} 键）"

    async def move(self, x: int, y: int, space: str = "image") -> str:
        rx, ry = self.map_coords(x, y, space)
        await self.call("move", timeout=30, x=rx, y=ry)
        return f"鼠标已移动到屏幕坐标 ({rx}, {ry})"

    async def scroll(self, amount: int) -> str:
        await self.call("scroll", timeout=30, amount=amount)
        return f"已滚动 {amount} 齿（正向上/负向下）"

    async def type_text(self, text: str) -> str:
        await self.call("type", timeout=60, text=text)
        return f"已输入 {len(text)} 个字符"

    async def press_key(self, keys: str) -> str:
        await self.call("key", timeout=30, keys=keys)
        return f"已按下 {keys}"

    async def exec(self, command: str, timeout: int = 30) -> CommandResult:
        d = await self.call(
            "exec", timeout=timeout + 30, command=command, timeout_sec=timeout
        )
        return CommandResult(
            stdout=str(d.get("stdout", "")),
            stderr=str(d.get("stderr", "")),
            exit_code=int(d.get("code", -1)),
            timed_out=bool(d.get("timed_out", False)),
        )

    async def start(self, command: str) -> str:
        await self.call("start", timeout=30, command=command)
        return f"已启动：{command}"

    async def info(self) -> dict:
        return await self.call("info", timeout=15)

    async def clipboard_get(self) -> str:
        d = await self.call("clipboard_get", timeout=15)
        return str(d.get("text", ""))

    async def clipboard_set(self, text: str) -> str:
        await self.call("clipboard_set", timeout=15, text=text)
        return "已写入剪贴板"

    # ---------------- 部署 / 状态 / 重启 ----------------

    async def deploy(self) -> str:
        """上传 agent.ps1 并通过计划任务在交互式会话中启动。"""
        if not WIN_AGENT_PS1.exists():
            raise AgentError("插件缺少 agent/win_agent.ps1")
        unix_dir, win_dir = await self._t.ensure_tmp_dir()
        script = WIN_AGENT_PS1.read_text(encoding="utf-8")
        agent_unix = unix_dir.rsplit("/tmp", 1)[0] + "/agent.ps1"  # <HOME>/.sshbot/agent.ps1
        await self._t.write_file(agent_unix, script.encode("utf-8"))

        port = self.port
        # win_dir 形如 <HOME>\.sshbot\tmp；agent.ps1 位于其上一级 <HOME>\.sshbot
        agent_ps1 = win_dir.rsplit("\\tmp", 1)[0] + "\\agent.ps1"
        tr_value = (
            "powershell.exe -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden "
            f'-File \\"{agent_ps1}\\" -Port {port}'
        )
        bat_lines = (
            f'schtasks /Create /F /TN {AGENT_TASK_NAME} /SC ONLOGON /IT /RL LIMITED /TR "{tr_value}"\r\n'
            f"if errorlevel 1 schtasks /Create /F /TN {AGENT_TASK_NAME} /SC DAILY /ST 23:59 /IT /RL LIMITED /TR \"{tr_value}\"\r\n"
            f"schtasks /End /TN {AGENT_TASK_NAME}\r\n"
            f"schtasks /Run /TN {AGENT_TASK_NAME}\r\n"
            "if errorlevel 1 echo RUN_FAIL\r\n"
            "if not errorlevel 1 echo DEPLOY_OK\r\n"
        )
        res = await self._t.exec(bat_lines, 60)
        if "DEPLOY_OK" not in res.stdout:
            raise AgentError(f"部署失败：{res.format(1200)}")

        last_err = ""
        for _ in range(10):
            await asyncio.sleep(1.5)
            try:
                info = await self.info()
                screen = info.get("screen", {})
                return (
                    f"Agent 已上线：{info.get('machine', '?')} / 用户 {info.get('user', '?')}，"
                    f"屏幕 {screen.get('w', '?')}x{screen.get('h', '?')}，端口 {self.port}"
                )
            except Exception as e:
                last_err = str(e)
        raise AgentError(
            f"Agent 已部署但未在 15 秒内上线：{last_err}\n"
            "可能原因：目标机当前无已登录的桌面会话（锁屏/未登录时计划任务 /IT 不会运行）"
        )

    async def status(self) -> str:
        try:
            info = await self.info()
            screen = info.get("screen", {})
            return (
                f"Agent 运行中：v{info.get('version', '?')}，"
                f"{info.get('machine', '?')} / {info.get('user', '?')}，"
                f"屏幕 {screen.get('w', '?')}x{screen.get('h', '?')}，端口 {self.port}"
            )
        except Exception as e:
            res = await self._t.exec(f"schtasks /Query /TN {AGENT_TASK_NAME}", 20)
            if res.ok:
                return f"Agent 未响应（{e}），但计划任务存在。可尝试：ssh 代理 重启"
            return f"Agent 未运行，且未找到计划任务（{e}）。请执行：ssh 代理 安装"

    async def restart(self) -> str:
        res = await self._t.exec(
            f"schtasks /End /TN {AGENT_TASK_NAME} & schtasks /Run /TN {AGENT_TASK_NAME}", 30
        )
        if not res.ok:
            raise AgentError(
                f"重启失败：{res.format(800)}（任务可能不存在，请先：ssh 代理 安装）"
            )
        for _ in range(8):
            await asyncio.sleep(1.5)
            try:
                await self.info()
                return "Agent 已重启"
            except Exception:
                continue
        raise AgentError("重启命令已发送但 Agent 未上线（锁屏/未登录时会失败）")
