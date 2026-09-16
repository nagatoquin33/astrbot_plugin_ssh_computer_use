"""SSH 管理类工具：主机列表/切换/命令执行/PowerShell/文件读写。"""

from __future__ import annotations

from pydantic import Field
from pydantic.dataclasses import dataclass

from .base import _SshToolBase, _schema


@dataclass
class SshListHostsTool(_SshToolBase):
    name: str = "ssh_list_hosts"
    description: str = (
        "列出所有已配置的 SSH 远程主机（名称、系统类型、地址、认证方式）及当前使用的主机。"
    )
    parameters: dict = Field(default_factory=lambda: _schema({}))

    async def call(self, context, **kwargs) -> str:
        if err := self._perm(context):
            return err
        pool = self.plugin.pool
        if not pool.profile_names:
            return "尚未配置任何主机，请在插件配置 hosts_text 中添加。"
        current = None
        try:
            event = context.context.event
            current = pool.resolve(event.unified_msg_origin).profile.name
        except Exception:
            pass
        lines = []
        for n in pool.profile_names:
            p = pool.get_profile(n)
            mark = " [当前]" if n == current else ""
            lines.append(
                f"- {n}{mark}：{p.os_type}，{p.username or '(SSH默认用户)'}@{p.host}:{p.port}，"
                + ("密钥认证" if p.auth == "key" else "密码认证")
                + ("" if p.has_secret else "（免密/默认凭据）")
            )
        return "\n".join(lines)


@dataclass
class SshSwitchHostTool(_SshToolBase):
    name: str = "ssh_switch_host"
    description: str = "切换当前会话使用的远程主机。后续 ssh_exec / computer_* 工具都将作用于该主机。"
    parameters: dict = Field(
        default_factory=lambda: _schema(
            {"name": {"type": "string", "description": "主机名称（见 ssh_list_hosts）"}},
            ["name"],
        )
    )

    async def call(self, context, name: str = "", **kwargs) -> str:
        if err := self._perm(context):
            return err
        event = context.context.event
        self.plugin.pool.set_selection(event.unified_msg_origin, name.strip())
        p = self.plugin.pool.get_profile(name.strip())
        return f"已切换到主机 {p.name}（{p.os_type}，{p.host}）。"


@dataclass
class SshExecTool(_SshToolBase):
    name: str = "ssh_exec"
    description: str = (
        "在当前远程主机上执行一条 shell 命令并返回输出。"
        "Windows 主机经由 cmd 执行（编码已自动处理，不要使用 bash 语法）；"
        "Linux 主机经由 bash 执行。适合系统管理、查看日志、安装软件等非 GUI 操作。"
    )
    parameters: dict = Field(
        default_factory=lambda: _schema(
            {"command": {"type": "string", "description": "要执行的命令"}}, ["command"]
        )
    )

    async def call(self, context, command: str = "", **kwargs) -> str:
        if err := self._perm(context):
            return err
        _, target = self._resolve(context)
        if blocked := self.plugin.pool.check_blocked(command):
            return f"命令被黑名单规则拦截：{blocked}"
        res = await target.exec(command)
        return res.format(self._max_chars)


@dataclass
class SshRunPowerShellTool(_SshToolBase):
    name: str = "ssh_run_powershell"
    description: str = (
        "在当前远程 Windows 主机上执行一段 PowerShell 脚本（可多行）。"
        "脚本会被写入临时 .ps1 文件执行，无需担心引号转义。仅 Windows 主机可用。"
    )
    parameters: dict = Field(
        default_factory=lambda: _schema(
            {"script": {"type": "string", "description": "PowerShell 脚本内容（可多行）"}},
            ["script"],
        )
    )

    async def call(self, context, script: str = "", **kwargs) -> str:
        if err := self._perm(context):
            return err
        _, target = self._resolve(context)
        if target.os_type != "windows":
            return "当前主机不是 Windows，无法执行 PowerShell。"
        if blocked := self.plugin.pool.check_blocked(script):
            return f"脚本被黑名单规则拦截：{blocked}"
        res = await target.run_powershell(script)
        return res.format(self._max_chars)


@dataclass
class SshReadFileTool(_SshToolBase):
    name: str = "ssh_read_file"
    description: str = "通过 SFTP 读取远程主机上的文本文件内容（UTF-8，超出上限会截断）。"
    parameters: dict = Field(
        default_factory=lambda: _schema(
            {
                "path": {"type": "string", "description": "远程文件路径"},
                "max_chars": {"type": "number", "description": "最多返回的字符数，默认 8000"},
            },
            ["path"],
        )
    )

    async def call(self, context, path: str = "", max_chars: int = 8000, **kwargs) -> str:
        if err := self._perm(context):
            return err
        _, target = self._resolve(context)
        data = await target.read_file(path.strip(), max_bytes=max(1, int(max_chars)) * 4)
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            text = data.decode("utf-8", errors="replace")
        if len(text) > int(max_chars):
            text = text[: int(max_chars)] + "\n...[已截断，文件更大]"
        return text or "(文件为空)"


@dataclass
class SshWriteFileTool(_SshToolBase):
    name: str = "ssh_write_file"
    description: str = "通过 SFTP 把文本内容写入远程主机的文件（UTF-8，覆盖写入）。"
    parameters: dict = Field(
        default_factory=lambda: _schema(
            {
                "path": {"type": "string", "description": "远程文件路径"},
                "content": {"type": "string", "description": "要写入的文本内容"},
            },
            ["path", "content"],
        )
    )

    async def call(self, context, path: str = "", content: str = "", **kwargs) -> str:
        if err := self._perm(context):
            return err
        _, target = self._resolve(context)
        await target.write_file(path.strip(), content.encode("utf-8"))
        return f"已写入 {path}（{len(content)} 字符）"
