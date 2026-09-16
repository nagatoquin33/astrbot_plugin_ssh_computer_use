"""LLM 工具公共基类与辅助。"""

from __future__ import annotations

import struct
from typing import Any

from pydantic import Field
from pydantic.dataclasses import dataclass

from astrbot.api import FunctionTool, logger

from ..core.errors import AgentError, SSHConnectionError

try:  # 消息链与图片组件（用于把截图发送到聊天）
    from astrbot.api import MessageChain
except ImportError:  # pragma: no cover
    from astrbot.core.message.message_event_result import MessageChain

from astrbot.api.message_components import Image


def _schema(props: dict, required: list[str] | None = None) -> dict:
    return {"type": "object", "properties": props, "required": required or []}


def png_size(data: bytes) -> tuple[int, int]:
    """从 PNG 文件头读取宽高。"""
    if len(data) > 24 and data[:8] == b"\x89PNG\r\n\x1a\n":
        w, h = struct.unpack(">II", data[16:24])
        return int(w), int(h)
    return 0, 0


@dataclass
class _SshToolBase(FunctionTool[Any]):
    """公共基类：持有插件引用。"""

    plugin: Any = None

    def _resolve(self, context):
        event = context.context.event
        target = self.plugin.pool.resolve(event.unified_msg_origin)
        return event, target

    def _perm(self, context) -> str | None:
        event = context.context.event
        return self.plugin.check_perm(event)

    @property
    def _max_chars(self) -> int:
        return int(self.plugin.config.get("max_output_chars", 4000))


def _safe_call_wrapper(tool: FunctionTool):
    """包装工具 call：异常转为友好文本返回给 LLM，避免中断整个 agent 回合。"""
    orig = tool.call

    async def call(context, **kwargs):
        try:
            return await orig(context, **kwargs)
        except (SSHConnectionError, AgentError) as e:
            return f"操作失败：{e}"
        except Exception as e:
            logger.error(f"[ssh_computer_use] 工具 {tool.name} 异常：{e}", exc_info=True)
            return f"工具 {tool.name} 执行异常：{e}"

    tool.call = call
    return tool
