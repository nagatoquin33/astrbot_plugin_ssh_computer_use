"""astrbot_plugin_ssh_computer_use - 插件入口。

本文件只保留 AstrBot 要求的 Star 类、装饰器注册与生命周期管理；
具体逻辑分层如下：

- core/     连接管理、平台抽象、Windows GUI Agent 客户端
- tools/    LLM 函数工具（dataclass 模式）
- commands/ 聊天命令解析与分发
- prompts.py 文案（帮助文本 / LLM 系统提示注入）
- agent/    部署到 Windows 目标机的 win_agent.ps1
"""

from pathlib import Path

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star, StarTools, register

from .commands import run_ssh_command
from .core import AgentError, ConnectionPool, SSHConnectionError
from .prompts import COMPUTER_USE_GUIDE
from .tools import build_tools

PLUGIN_NAME = "astrbot_plugin_ssh_computer_use"


@register(
    PLUGIN_NAME,
    "nagatoquin33",
    "基于 SSH 的全平台远程计算机操控：命令/文件/截图/键鼠，Windows 与 Linux 通用",
    "v0.2.2",
    "https://github.com/nagatoquin33/astrbot_plugin_ssh_computer_use",
)
class SshComputerUsePlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        # 直接引用配置对象（AstrBot 原地更新），WebUI 改配置后无需重载即可生效
        self.pool = ConnectionPool(config)
        self.shot_dir: Path = Path(".")
        self.down_dir: Path = Path(".")

    # ---------------- 生命周期 ----------------

    async def initialize(self):
        import asyncssh

        data_dir = StarTools.get_data_dir(PLUGIN_NAME)
        self.shot_dir = data_dir / "screenshots"
        self.down_dir = data_dir / "downloads"
        self.shot_dir.mkdir(parents=True, exist_ok=True)
        self.down_dir.mkdir(parents=True, exist_ok=True)
        try:
            has_sftp_api = hasattr(asyncssh.SSHClientConnection, "start_sftp_client") or hasattr(
                asyncssh.SSHClientConnection, "start_sftp"
            )
            logger.info(f"[{PLUGIN_NAME}] asyncssh {asyncssh.__version__}")
            if not has_sftp_api:
                logger.error(
                    f"[{PLUGIN_NAME}] 当前 asyncssh（{asyncssh.__version__}）不支持 SFTP 客户端 API，"
                    "文件传输功能不可用。请安装 asyncssh>=2.14 后重启 AstrBot"
                )
        except (AttributeError, ValueError):
            pass
        if self.config.get("enable_llm_tools", True):
            self.context.add_llm_tools(*build_tools(self))
            logger.info(f"[{PLUGIN_NAME}] LLM 工具已注册，主机：{self.pool.profile_names or '无'}")

    async def terminate(self):
        await self.pool.close_all()
        logger.info(f"[{PLUGIN_NAME}] 已卸载，SSH 连接全部关闭")

    # ---------------- 公共辅助 ----------------

    def check_perm(self, event: AstrMessageEvent) -> str | None:
        """返回拒绝原因；None 表示允许。"""
        if not self.config.get("admin_only", True):
            return None
        uid = str(event.get_sender_id() or "")
        allowed = {str(u) for u in (self.config.get("allowed_users") or [])}
        if event.is_admin() or uid in allowed:
            return None
        return "⛔ 仅管理员或白名单用户可使用远程计算机操控功能。"

    def save_screenshot(self, host_name: str, data: bytes) -> Path:
        import time

        ext = "png" if str(self.config.get("screenshot_format", "jpeg")) == "png" else "jpg"
        path = self.shot_dir / (
            f"{host_name}_{int(time.time())}_{int(time.time()*1000)%1000:03d}.{ext}"
        )
        path.write_bytes(data)
        return path

    # ---------------- LLM 请求钩子 ----------------

    @filter.on_llm_request()
    async def inject_computer_use_guide(self, event: AstrMessageEvent, req):
        if not self.config.get("computer_use_prompt", True):
            return
        if not self.config.get("enable_llm_tools", True):
            return
        try:
            target = self.pool.resolve(event.unified_msg_origin)
        except Exception:
            return
        try:
            req.system_prompt = (req.system_prompt or "") + COMPUTER_USE_GUIDE.format(
                host=target.profile.name
            )
        except Exception as e:
            logger.warning(f"[{PLUGIN_NAME}] 注入系统提示失败：{e}")

    # ---------------- 聊天命令 ----------------

    @filter.command("ssh", alias=["远程"], priority=5)
    async def ssh_command(self, event: AstrMessageEvent):
        """SSH 远程计算机操控主命令。"""
        if err := self.check_perm(event):
            yield event.plain_result(err)
            return
        try:
            async for r in run_ssh_command(self, event):
                yield r
        except (SSHConnectionError, AgentError) as e:
            yield event.plain_result(f"❌ {e}")
        except Exception as e:
            logger.error(f"[{PLUGIN_NAME}] 命令执行异常：{e}", exc_info=True)
            yield event.plain_result(f"❌ 执行出错：{e}")
