"""LLM 函数工具包（dataclass 模式，AstrBot v4.5.7+）。"""

from .base import _safe_call_wrapper
from .computer_tools import (
    ComputerClickTool,
    ComputerClipboardTool,
    ComputerExecTool,
    ComputerInfoTool,
    ComputerMoveTool,
    ComputerPressKeyTool,
    ComputerScreenshotTool,
    ComputerScrollTool,
    ComputerStartTool,
    ComputerTypeTool,
)
from .ssh_tools import (
    SshExecTool,
    SshListHostsTool,
    SshReadFileTool,
    SshRunPowerShellTool,
    SshSwitchHostTool,
    SshWriteFileTool,
)

TOOL_CLASSES = (
    SshListHostsTool,
    SshSwitchHostTool,
    SshExecTool,
    SshRunPowerShellTool,
    SshReadFileTool,
    SshWriteFileTool,
    ComputerScreenshotTool,
    ComputerClickTool,
    ComputerMoveTool,
    ComputerScrollTool,
    ComputerTypeTool,
    ComputerPressKeyTool,
    ComputerExecTool,
    ComputerStartTool,
    ComputerInfoTool,
    ComputerClipboardTool,
)


def build_tools(plugin) -> list:
    """构建全部工具实例（plugin 为插件实例）。"""
    return [_safe_call_wrapper(cls(plugin=plugin)) for cls in TOOL_CLASSES]


__all__ = ["build_tools"]
