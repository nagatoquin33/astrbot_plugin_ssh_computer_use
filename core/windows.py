"""Windows 目标机：临时 .bat / .ps1 文件执行，规避转义与编码问题。

GUI 操作（截图/键鼠/剪贴板等）委托给同目录 agent_client.py 中的
WinAgentClient（JSON over SSH 端口转发）。
"""

from __future__ import annotations

import asyncio
import re
import uuid

from .agent_client import WinAgentClient
from .base import SshTarget
from .errors import SSHConnectionError
from .models import CommandResult, decode_bytes


class WindowsTarget(SshTarget):
    os_type = "windows"

    def __init__(self, profile, cfg: dict):
        super().__init__(profile, cfg)
        self._home: str | None = None
        self._tmp_ready = False
        self._tmp_unix: str = ""
        self._tmp_win: str = ""
        self.agent = WinAgentClient(self)

    # ---------------- 路径与目录 ----------------

    async def _get_home(self) -> str:
        if self._home:
            return self._home
        sftp = await self._get_sftp()
        # SFTP REALPATH 解析家目录；normalize 是 paramiko 的 API，asyncssh 里叫 realpath
        canon = getattr(sftp, "realpath", None) or getattr(sftp, "normalize", None)
        if canon is None:
            raise SSHConnectionError("当前 asyncssh 的 SFTPClient 不支持路径解析 API，请升级 asyncssh")
        home = await canon(".")
        home = home.replace("\\", "/").rstrip("/")
        self._home = home
        return home

    @staticmethod
    def _win_path(unix_style: str) -> str:
        """SFTP normalize 返回 /C:/Users/xx 形式；转为 cmd 可用的 C:\\Users\\xx。"""
        p = unix_style
        m = re.match(r"^/([A-Za-z]):/(.*)$", p)
        if m:
            p = f"{m.group(1).upper()}:/{m.group(2)}"
        elif p.startswith("/"):
            p = p.lstrip("/")
        return p.replace("/", "\\")

    async def ensure_tmp_dir(self) -> tuple[str, str]:
        """确保远端 .sshbot/tmp 目录存在（纯 SFTP，避免 exec 递归依赖）。
        返回 (unix 风格路径, cmd 风格路径)。"""
        if self._tmp_ready:
            return self._tmp_unix, self._tmp_win
        sftp = await self._get_sftp()
        home = await self._get_home()
        base = home + "/.sshbot"
        tmpd = base + "/tmp"
        for d in (base, tmpd):
            try:
                await sftp.mkdir(d)
            except Exception:
                pass  # 已存在等情况
        try:
            await sftp.stat(tmpd)
        except Exception as e:
            raise SSHConnectionError(f"无法创建远端目录 {tmpd}：{e}") from e
        self._tmp_unix, self._tmp_win = tmpd, self._win_path(tmpd)
        self._tmp_ready = True
        return self._tmp_unix, self._tmp_win

    # ---------------- 命令执行 ----------------

    async def exec(self, command: str, timeout: int | None = None) -> CommandResult:
        """Windows 命令执行：写入临时 .bat（chcp 65001）后运行，规避转义与编码问题。"""
        timeout = timeout or int(self.cfg.get("exec_timeout", 60))
        unix_dir, win_dir = await self.ensure_tmp_dir()
        token = uuid.uuid4().hex[:12]
        bat_unix = f"{unix_dir}/c_{token}.bat"
        bat_win = f"{win_dir}\\c_{token}.bat"  # win_dir 已是 <HOME>\.sshbot\tmp

        bat = "@echo off\r\nchcp 65001 >nul\r\n" + command + "\r\nexit /b %errorlevel%\r\n"
        await self.write_file(bat_unix, bat.encode("utf-8"))

        async def _run(conn):
            return await conn.run(f'cmd /d /s /c "{bat_win}"')

        try:
            result = await self._with_retry(_run)
        except asyncio.TimeoutError:
            return CommandResult(stdout="", stderr="", exit_code=-1, timed_out=True)
        finally:
            try:
                sftp = await self._get_sftp()
                await sftp.remove(bat_unix)
            except Exception:
                pass

        return CommandResult(
            stdout=decode_bytes(result.stdout or b""),
            stderr=decode_bytes(result.stderr or b""),
            exit_code=result.exit_status if result.exit_status is not None else -1,
        )

    async def run_powershell(self, script: str, timeout: int | None = None) -> CommandResult:
        """执行 PowerShell 脚本：写入 UTF-8 BOM 的 .ps1 再运行，无转义地狱。"""
        timeout = timeout or int(self.cfg.get("exec_timeout", 60))
        unix_dir, win_dir = await self.ensure_tmp_dir()
        token = uuid.uuid4().hex[:12]
        ps_unix = f"{unix_dir}/p_{token}.ps1"
        ps_win = f"{win_dir}\\p_{token}.ps1"

        header = (
            "$ErrorActionPreference = 'Continue'\r\n"
            "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}\r\n"
        )
        # UTF-8 BOM：Windows PowerShell 5.1 必需，否则中文会乱码
        content = b"\xef\xbb\xbf" + (header + script).encode("utf-8")
        await self.write_file(ps_unix, content)

        async def _run(conn):
            return await asyncio.wait_for(
                conn.run(f'powershell -NoProfile -ExecutionPolicy Bypass -File "{ps_win}"'),
                timeout + 10,
            )

        try:
            result = await self._with_retry(_run)
        finally:
            try:
                sftp = await self._get_sftp()
                await sftp.remove(ps_unix)
            except Exception:
                pass

        return CommandResult(
            stdout=decode_bytes(result.stdout or b""),
            stderr=decode_bytes(result.stderr or b""),
            exit_code=result.exit_status if result.exit_status is not None else -1,
        )

    async def sysinfo(self) -> str:
        res = await self.exec("ver & echo PC=%COMPUTERNAME% USER=%USERNAME%", 20)
        return res.format(1500)

    # ---------------- GUI 操作（委托 Agent 客户端） ----------------

    @property
    def agent_port(self) -> int:
        return self.agent.port

    async def screenshot(self, max_width: int = 0, fmt: str = "jpeg", quality: int = 80):
        return await self.agent.screenshot(max_width, fmt, quality)

    def map_coords(self, x: int, y: int, space: str = "image"):
        return self.agent.map_coords(x, y, space)

    async def gui_click(self, x: int, y: int, button: str = "left", double: bool = False,
                        space: str = "image") -> str:
        return await self.agent.click(x, y, button, double, space)

    async def gui_move(self, x: int, y: int, space: str = "image") -> str:
        return await self.agent.move(x, y, space)

    async def gui_scroll(self, amount: int) -> str:
        return await self.agent.scroll(amount)

    async def gui_type(self, text: str) -> str:
        return await self.agent.type_text(text)

    async def gui_key(self, keys: str) -> str:
        return await self.agent.press_key(keys)

    async def gui_exec(self, command: str, timeout: int = 30) -> CommandResult:
        return await self.agent.exec(command, timeout)

    async def gui_start(self, command: str) -> str:
        return await self.agent.start(command)

    async def gui_info(self) -> dict:
        return await self.agent.info()

    async def agent_deploy(self) -> str:
        return await self.agent.deploy()

    async def agent_status(self) -> str:
        return await self.agent.status()

    async def agent_restart(self) -> str:
        return await self.agent.restart()

    async def clipboard_get(self) -> str:
        return await self.agent.clipboard_get()

    async def clipboard_set(self, text: str) -> str:
        return await self.agent.clipboard_set(text)

    async def close(self):
        await self.agent.close()
        await super().close()
