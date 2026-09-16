"""astrbot_plugin_ssh_computer_use - 核心包。

分层：
- errors.py       异常类型
- models.py       数据模型与主机配置解析
- base.py         SshTarget 基类（连接管理 / SFTP / 重试 / 解码）
- agent_client.py Windows GUI Agent 客户端（JSON over SSH 端口转发）
- windows.py      Windows 目标机（.bat/.ps1 文件执行）
- linux.py        Linux 目标机（bash 执行 / GUI 尽力而为）
- pool.py         连接池与主机选择
"""

from .errors import AgentError, SSHConnectionError
from .models import CommandResult, HostProfile, Screenshot, parse_hosts_text
from .pool import ConnectionPool, build_target

__all__ = [
    "AgentError",
    "SSHConnectionError",
    "CommandResult",
    "HostProfile",
    "Screenshot",
    "parse_hosts_text",
    "ConnectionPool",
    "build_target",
]
