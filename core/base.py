"""SshTarget 基类：连接管理、SFTP 通用操作、失败重连与自动重试。"""

from __future__ import annotations

import asyncio
from abc import abstractmethod

import asyncssh

from .errors import SSHConnectionError
from .models import CommandResult, HostProfile, Screenshot, decode_bytes


class SshTarget:
    """一台远程主机。子类实现 exec / sysinfo 及平台相关 GUI 操作。"""

    os_type = "linux"

    def __init__(self, profile: HostProfile, cfg: dict):
        self.profile = profile
        self.cfg = cfg
        self._conn: asyncssh.SSHClientConnection | None = None
        self._sftp: asyncssh.SFTPClient | None = None
        self._lock = asyncio.Lock()

    # ---------------- 连接管理 ----------------

    @property
    def connect_timeout(self) -> float:
        return float(self.cfg.get("connect_timeout", 15))

    async def _new_conn(self) -> asyncssh.SSHClientConnection:
        p = self.profile
        kwargs: dict = {
            "host": p.host,
            "port": p.port,
            "known_hosts": None,  # 局域网/虚拟局域网场景，跳过 host key 校验
            "encoding": None,  # 返回 bytes，自行按 utf-8/gbk 解码
        }
        # 用户名可空：交由 SSH 默认行为（当前用户 / ~/.ssh/config）
        if p.username:
            kwargs["username"] = p.username
        if p.auth == "password":
            # asyncssh 会同时尝试 password 与 keyboard-interactive（部分 NAS 需要）
            kwargs["password"] = p.password or None
            kwargs["preferred_auth"] = ("password", "keyboard-interactive", "publickey")
        else:
            kwargs["preferred_auth"] = ("publickey",)
            client_keys: list = []
            if p.key_content:
                try:
                    client_keys.append(asyncssh.import_private_key(p.key_content))
                except asyncssh.KeyImportError as e:
                    raise SSHConnectionError(
                        f"主机 {p.name} 的私钥内容无法解析（{e}），"
                        "请检查是否为完整的 PEM 私钥（以 -----BEGIN ... 开头）"
                    ) from None
            elif p.key_path:
                client_keys.append(os_path_expand(p.key_path))
            if client_keys:
                kwargs["client_keys"] = client_keys
            # 私钥内容与路径均为空 → 使用 ~/.ssh 默认密钥与 ssh-agent（免密场景）
        try:
            return await asyncio.wait_for(asyncssh.connect(**kwargs), self.connect_timeout)
        except asyncio.TimeoutError:
            raise SSHConnectionError(
                f"连接 {p.name}({p.host}:{p.port}) 超时（{self.connect_timeout}s），"
                "请检查地址/网络（EasyTier/WireGuard 是否连通）"
            )
        except asyncssh.PermissionDenied:
            hint = "密码" if p.auth == "password" else "公钥是否已加入目标机 authorized_keys（Windows 管理员账户请放入 administrators_authorized_keys）"
            raise SSHConnectionError(f"认证被拒绝（{p.name}）：请检查{hint}") from None
        except (OSError, asyncssh.Error) as e:
            raise SSHConnectionError(f"连接 {p.name}({p.host}:{p.port}) 失败：{e}") from e

    async def _get_conn(self) -> asyncssh.SSHClientConnection:
        async with self._lock:
            if self._conn is None:
                self._conn = await self._new_conn()
            return self._conn

    async def _get_sftp(self) -> asyncssh.SFTPClient:
        async with self._lock:
            if self._conn is None:
                self._conn = await self._new_conn()
            if self._sftp is None:
                starter = getattr(self._conn, "start_sftp", None)
                if starter is None:
                    raise SSHConnectionError(
                        f"bot 环境的 asyncssh 版本过旧"
                        f"（{getattr(asyncssh, '__version__', '未知')}，需 >=2.14），"
                        "缺少 SFTP 支持。请在 bot 所在主机的 AstrBot Python 环境中执行 "
                        "pip install -U asyncssh 后重启 AstrBot"
                    )
                self._sftp = await starter()
            return self._sftp

    async def _reset(self):
        async with self._lock:
            if self._sftp is not None:
                try:
                    self._sftp.exit()
                except Exception:
                    pass
                self._sftp = None
            if self._conn is not None:
                try:
                    self._conn.close()
                except Exception:
                    pass
                self._conn = None

    async def _with_retry(self, fn):
        """执行远程操作；连接失效时自动重连并重试一次。"""
        await self._get_conn()
        try:
            return await fn(self._conn)
        except (asyncssh.Error, OSError) as e:
            from astrbot.api import logger

            logger.warning(f"[ssh_computer_use] {self.profile.name} 连接异常，重连重试：{e}")
            await self._reset()
            await self._get_conn()
            return await fn(self._conn)

    async def close(self):
        await self._reset()

    # ---------------- 通用操作 ----------------

    @abstractmethod
    async def exec(self, command: str, timeout: int | None = None) -> CommandResult:
        raise NotImplementedError

    async def run_checked(self, command: str, timeout: int | None = None) -> CommandResult:
        """执行并失败时抛出异常（带输出）。"""
        res = await self.exec(command, timeout)
        if not res.ok:
            raise SSHConnectionError(f"命令失败：{res.format(1500)}")
        return res

    async def read_file(self, remote_path: str, max_bytes: int = 0) -> bytes:
        sftp = await self._get_sftp()

        async def _read():
            async with sftp.open(remote_path, "rb") as f:
                data = await f.read()
                if max_bytes and len(data) > max_bytes:
                    return data[:max_bytes]
                return bytes(data)

        try:
            return await _read()
        except (asyncssh.Error, OSError):
            await self._reset()
            sftp = await self._get_sftp()
            return await _read()

    async def write_file(self, remote_path: str, data: bytes):
        sftp = await self._get_sftp()

        async def _write():
            async with sftp.open(remote_path, "wb") as f:
                await f.write(data)

        try:
            await _write()
        except (asyncssh.Error, OSError):
            await self._reset()
            sftp = await self._get_sftp()
            await _write()

    async def exists(self, remote_path: str) -> bool:
        sftp = await self._get_sftp()
        try:
            await sftp.stat(remote_path)
            return True
        except asyncssh.SFTPNoSuchFile:
            return False
        except (asyncssh.Error, OSError):
            await self._reset()
            sftp = await self._get_sftp()
            try:
                await sftp.stat(remote_path)
                return True
            except Exception:
                return False

    async def download(self, remote_path: str, local_path: str):
        sftp = await self._get_sftp()

        async def _get():
            await sftp.get(remote_path, local_path)

        try:
            await _get()
        except (asyncssh.Error, OSError):
            await self._reset()
            sftp = await self._get_sftp()
            await _get()

    async def upload(self, local_path: str, remote_path: str):
        sftp = await self._get_sftp()

        async def _put():
            await sftp.put(local_path, remote_path)

        try:
            await _put()
        except (asyncssh.Error, OSError):
            await self._reset()
            sftp = await self._get_sftp()
            await _put()

    # ---------------- 平台通用接口（子类实现） ----------------

    async def screenshot(
        self, max_width: int = 0, fmt: str = "jpeg", quality: int = 80
    ) -> Screenshot:
        raise SSHConnectionError(f"{self.os_type} 暂不支持截图")

    def map_coords(self, x: int, y: int, space: str = "image") -> tuple[int, int]:
        return int(x), int(y)

    async def gui_click(self, x: int, y: int, button: str = "left", double: bool = False,
                        space: str = "image") -> str:
        raise SSHConnectionError(f"{self.os_type} 暂不支持键鼠控制")

    async def gui_move(self, x: int, y: int, space: str = "image") -> str:
        raise SSHConnectionError(f"{self.os_type} 暂不支持键鼠控制")

    async def gui_scroll(self, amount: int) -> str:
        raise SSHConnectionError(f"{self.os_type} 暂不支持键鼠控制")

    async def gui_type(self, text: str) -> str:
        raise SSHConnectionError(f"{self.os_type} 暂不支持键鼠控制")

    async def gui_key(self, keys: str) -> str:
        raise SSHConnectionError(f"{self.os_type} 暂不支持键鼠控制")

    async def gui_exec(self, command: str, timeout: int = 30) -> CommandResult:
        return await self.exec(command, timeout)

    async def gui_start(self, command: str) -> str:
        raise SSHConnectionError(f"{self.os_type} 暂不支持会话内启动程序")

    async def clipboard_get(self) -> str:
        raise SSHConnectionError(f"{self.os_type} 暂不支持剪贴板")

    async def clipboard_set(self, text: str) -> str:
        raise SSHConnectionError(f"{self.os_type} 暂不支持剪贴板")

    @abstractmethod
    async def sysinfo(self) -> str:
        raise NotImplementedError


def os_path_expand(path: str) -> str:
    """展开私钥路径中的 ~。"""
    import os

    return os.path.expanduser(path)


__all__ = ["SshTarget"]
