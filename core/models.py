"""数据模型与主机配置解析。

主机清单（hosts_text）每行一台：
    名称 | windows|linux | 用户名 | key|password | 地址 | 端口 | 密码或私钥路径 [ | Agent端口 ]

无密码/免密兼容：
- key 认证：最后一列留空 → 使用 bot 主机 ~/.ssh/ 默认密钥与 ssh-agent；
- password 认证：最后一列留空 → 以空密码尝试（个别设备允许）；
- 用户名留空 → 交由 SSH 默认行为（当前用户 / ~/.ssh/config）。
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

from astrbot.api import logger


@dataclass
class HostProfile:
    name: str
    os_type: str  # "windows" | "linux"
    username: str  # 可为空 → SSH 默认行为
    auth: str  # "key" | "password"
    host: str
    port: int = 22
    secret: str = ""  # password 认证：密码（可空）；key 认证：私钥路径（可空）
    agent_port: int = 0  # 0 表示使用全局默认端口

    @property
    def has_secret(self) -> bool:
        return bool(self.secret)


@dataclass
class CommandResult:
    stdout: str = ""
    stderr: str = ""
    exit_code: int = -1
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def format(self, max_chars: int = 4000) -> str:
        parts = []
        if self.stdout.strip():
            parts.append(self.stdout.strip())
        if self.stderr.strip():
            parts.append("[stderr] " + self.stderr.strip())
        if self.timed_out:
            parts.append("[命令超时被中断]")
        elif self.exit_code != 0:
            parts.append(f"[exit code: {self.exit_code}]")
        text = "\n".join(parts) if parts else "(无输出)"
        if len(text) > max_chars:
            text = text[:max_chars] + f"\n...[输出过长，已截断，共 {len(text)} 字符]"
        return text


@dataclass
class Screenshot:
    data: bytes
    width: int = 0
    height: int = 0
    screen_x: int = 0
    screen_y: int = 0
    screen_w: int = 0
    screen_h: int = 0


def parse_hosts_text(text: str) -> dict[str, HostProfile]:
    """解析多行主机配置，容错处理：跳过空行/注释/字段不足的行。"""
    profiles: dict[str, HostProfile] = {}
    for lineno, raw in enumerate((text or "").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 5:
            logger.warning(f"[ssh_computer_use] 主机配置第 {lineno} 行字段不足，已跳过：{line}")
            continue
        name = parts[0]
        os_type = parts[1].lower()
        username = parts[2]
        auth = parts[3].lower()
        host = parts[4]
        if os_type not in ("windows", "linux"):
            logger.warning(f"[ssh_computer_use] 第 {lineno} 行系统类型应为 windows/linux：{line}")
            continue
        if auth not in ("key", "password"):
            logger.warning(f"[ssh_computer_use] 第 {lineno} 行认证方式应为 key/password：{line}")
            continue
        if not name or not host:
            logger.warning(f"[ssh_computer_use] 第 {lineno} 行名称/地址不能为空：{line}")
            continue
        try:
            port = int(parts[5]) if len(parts) > 5 and parts[5] else 22
        except ValueError:
            port = 22
        secret = parts[6] if len(parts) > 6 else ""
        agent_port = 0
        if len(parts) > 7 and parts[7]:
            try:
                agent_port = int(parts[7])
            except ValueError:
                agent_port = 0
        profiles[name] = HostProfile(
            name=name,
            os_type=os_type,
            username=username,
            auth=auth,
            host=host,
            port=port,
            secret=secret,
            agent_port=agent_port,
        )
    return profiles


def decode_bytes(data: bytes) -> str:
    """按 utf-8 → gbk → 替换模式依次解码（Windows 中文环境兜底）。"""
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("gbk")
        except UnicodeDecodeError:
            return data.decode("utf-8", errors="replace")


def png_size(data: bytes) -> tuple[int, int]:
    """从 PNG 文件头读取宽高。"""
    if len(data) > 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    return 0, 0
