"""核心层异常类型。"""


class SSHConnectionError(Exception):
    """SSH 连接、认证或命令执行失败。"""


class AgentError(Exception):
    """Windows GUI Agent 部署或调用失败。"""
