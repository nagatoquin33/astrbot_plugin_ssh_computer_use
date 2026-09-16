"""连接池：主机配置刷新、目标构建与会话级主机选择。"""

from __future__ import annotations

import re

from astrbot.api import logger

from .base import SshTarget
from .errors import SSHConnectionError
from .linux import LinuxTarget
from .models import HostProfile, parse_hosts_config
from .windows import WindowsTarget


def build_target(profile: HostProfile, cfg: dict) -> SshTarget:
    if profile.os_type == "windows":
        return WindowsTarget(profile, cfg)
    return LinuxTarget(profile, cfg)


class ConnectionPool:
    """管理所有主机配置与连接，并记录各会话选择的主机。"""

    def __init__(self, cfg: dict):
        self.cfg = cfg
        self._profiles: dict[str, HostProfile] = {}
        self._targets: dict[str, SshTarget] = {}
        self._selection: dict[str, str] = {}  # unified_msg_origin -> host name
        self.reload()

    def reload(self):
        self._profiles = parse_hosts_config(self.cfg)
        if not self._profiles:
            logger.warning("[ssh_computer_use] 尚未配置任何 SSH 主机")

    @property
    def profile_names(self) -> list[str]:
        return list(self._profiles.keys())

    def get_profile(self, name: str) -> HostProfile:
        if name not in self._profiles:
            known = "、".join(self.profile_names) or "（无）"
            raise SSHConnectionError(f"未找到主机 '{name}'。已配置的主机：{known}")
        return self._profiles[name]

    def get_target(self, name: str) -> SshTarget:
        if name not in self._targets:
            self._targets[name] = build_target(self.get_profile(name), self.cfg)
        return self._targets[name]

    def resolve(self, umo: str | None, name: str | None = None) -> SshTarget:
        """解析目标主机：显式指定 > 会话选择 > 默认主机。
        每次解析前刷新主机清单，WebUI 改配置无需重载即可生效。"""
        self.reload()
        if name:
            chosen = name
        elif umo and self._selection.get(umo):
            chosen = self._selection[umo]
        else:
            chosen = (self.cfg.get("default_host") or "").strip() or (
                self.profile_names[0] if self.profile_names else ""
            )
        if not chosen:
            raise SSHConnectionError("没有可用主机：请先在插件配置中填写 hosts_text")
        return self.get_target(chosen)

    def set_selection(self, umo: str, name: str):
        self.reload()
        self.get_profile(name)  # 校验存在
        self._selection[umo] = name

    def get_selection(self, umo: str | None) -> str | None:
        if not umo:
            return None
        return self._selection.get(umo)

    def check_blocked(self, command: str) -> str | None:
        """返回命中的黑名单正则，未命中返回 None。"""
        for pattern in self.cfg.get("blocked_patterns") or []:
            try:
                if re.search(pattern, command):
                    return pattern
            except re.error:
                continue
        return None

    async def close_all(self):
        for t in self._targets.values():
            try:
                await t.close()
            except Exception:
                pass
        self._targets.clear()
