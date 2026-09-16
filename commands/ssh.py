"""`ssh` 命令的解析与分发（与 AstrBot 装饰器解耦，便于测试）。"""

from __future__ import annotations

from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import logger

from ..core import SSHConnectionError
from ..prompts import HELP_TEXT

_ALIASES = {
    "列表": "list", "ls": "list", "list": "list", "hosts": "list",
    "使用": "use", "切换": "use", "use": "use", "switch": "use",
    "连接": "conn", "conn": "conn", "connect": "conn", "info": "conn",
    "执行": "run", "run": "run", "exec": "run", "sh": "run", "cmd": "run",
    "ps": "ps", "powershell": "ps",
    "截图": "shot", "shot": "shot", "screenshot": "shot",
    "点击": "click", "click": "click",
    "移动": "move", "move": "move",
    "输入": "type", "type": "type",
    "按键": "key", "key": "key",
    "滚动": "scroll", "scroll": "scroll",
    "屏幕": "screen", "screen": "screen",
    "剪贴板": "clip", "clipboard": "clip", "clip": "clip",
    "上传": "up", "up": "up", "upload": "up",
    "下载": "down", "down": "down", "download": "down",
    "代理": "agent", "agent": "agent",
    "诊断": "diag", "diag": "diag", "doctor": "diag",
}


def parse_command(message_str: str) -> tuple[str, str]:
    """从消息文本解析出 (子命令, 参数)。兼容 message_str 带或不带命令名两种情况。"""
    text = (message_str or "").strip()
    low = text.lower()
    for prefix in ("ssh", "远程"):
        if low.startswith(prefix):
            text = text[len(prefix):].strip()
            break
    parts = text.split(None, 1)
    sub = (parts[0].lower() if parts else "").strip()
    arg = parts[1].strip() if len(parts) > 1 else ""
    return sub, arg


def current_host_name(plugin, umo: str) -> str:
    try:
        return plugin.pool.resolve(umo).profile.name
    except Exception:
        return "（未配置）"


def format_host_list(plugin, umo: str) -> str:
    if not plugin.pool.profile_names:
        return "尚未配置主机：请在管理面板插件配置中填写 hosts_text。"
    current = plugin.pool.get_selection(umo) or (plugin.config.get("default_host") or "")
    lines = ["📡 已配置主机："]
    for n in plugin.pool.profile_names:
        p = plugin.pool.get_profile(n)
        mark = " ← 当前" if n == current else ""
        cred = ("密钥认证" if p.auth == "key" else "密码认证") + (
            "" if p.has_secret else "（免密/默认凭据）"
        )
        lines.append(
            f"- {n}{mark}：{p.os_type} ｜ {p.username or '(SSH默认用户)'}@{p.host}:{p.port} ｜ {cred}"
        )
    lines.append("切换：ssh 使用 <名称>")
    return "\n".join(lines)


async def _extract_file(event) -> tuple[str | None, object | None]:
    """从消息中提取文件/图片组件并落地为本地路径。"""
    comp = None
    for c in event.message_obj.message or []:
        if isinstance(c, (Comp.File, Comp.Image)):
            comp = c
            break
    if comp is None:
        return None, None
    try:
        if isinstance(comp, Comp.File):
            return await comp.get_file(), comp
        return await comp.convert_to_file_path(), comp
    except Exception as e:
        logger.warning(f"[ssh_computer_use] 提取消息文件失败：{e}")
        return None, comp


def _run_diag(plugin) -> str:
    """收集图片链路诊断信息：AstrBot 版本、工具图片缓存、模型模态配置。"""
    lines = ["🔬 图片链路诊断"]

    # 1. AstrBot 版本
    try:
        import astrbot as _ab

        lines.append(f"· AstrBot 版本：{_ab.__version__}")
    except Exception as e:
        lines.append(f"· AstrBot 版本：获取失败（{e}）")

    # 2. 工具图片缓存机制是否存在（较新版本才有）
    cache_dir = None
    try:
        from astrbot.core.agent.tool_image_cache import tool_image_cache

        lines.append("· 工具图片缓存机制：存在（tool_image_cache）")
        cache_dir = tool_image_cache._cache_dir
    except Exception:
        lines.append(
            "· 工具图片缓存机制：不存在 → 当前 AstrBot 版本过旧，"
            "工具返回的截图无法进入模型输入，请升级 AstrBot"
        )

    # 3. 最近被框架缓存的工具图片（判断上次截图是否真的进了链路）
    if cache_dir:
        try:
            import time as _time

            files = sorted(
                (Path(cache_dir).glob("*")),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:5]
            if files:
                now = _time.time()
                lines.append("· 最近缓存的工具图片（截图后应有新文件）：")
                for p in files:
                    age = int(now - p.stat().st_mtime)
                    lines.append(f"    - {p.name}（{age} 秒前，{p.stat().st_size} 字节）")
            else:
                lines.append(
                    "· 最近缓存的工具图片：缓存目录为空 → "
                    "截图从未被框架缓存（版本过旧或缓存写入失败）"
                )
        except Exception as e:
            lines.append(f"· 缓存目录读取失败：{e}")

    # 4. 各文本模型的模态配置（modalities 不含 image 时工具图片会被丢弃）
    try:
        providers = plugin.context.get_all_providers()
        if not providers:
            lines.append("· 文本模型：未配置")
        for p in providers:
            try:
                m = p.meta()
                mods = (p.provider_config or {}).get("modalities", None)
                mod_str = "未配置（默认支持图片）" if not mods else "、".join(mods)
                warn = (
                    ""
                    if (not mods or "image" in mods)
                    else "  ⚠️ 未包含 image，工具截图不会进入该模型的输入！"
                )
                lines.append(f"· 文本模型：{m.id}（{m.type}，{m.model}）模态：{mod_str}{warn}")
            except Exception as e:
                lines.append(f"· 文本模型信息读取失败：{e}")
    except Exception as e:
        lines.append(f"· 模型列表获取失败：{e}")

    lines.append("→ 若以上有 ⚠️ 或「不存在/为空」，按提示处理后重试截图即可。")
    return "\n".join(lines)


async def run_ssh_command(plugin, event):
    """执行 `ssh` 命令，逐条 yield 结果（异常由调用方统一兜底）。"""
    umo = event.unified_msg_origin
    sub, arg = parse_command(event.message_str)
    action = _ALIASES.get(sub)

    if not action:
        yield event.plain_result(HELP_TEXT.format(host=current_host_name(plugin, umo)))
        return

    if action == "list":
        yield event.plain_result(format_host_list(plugin, umo))

    elif action == "use":
        if not arg:
            names = "、".join(plugin.pool.profile_names) or "（无）"
            yield event.plain_result(f"用法：ssh 使用 <名称>。可选：{names}")
            return
        name = arg.split()[0]
        plugin.pool.set_selection(umo, name)
        p = plugin.pool.get_profile(name)
        yield event.plain_result(
            f"✅ 已切换到 {p.name}（{p.os_type}，{p.username or '(SSH默认用户)'}@{p.host}:{p.port}）"
        )

    elif action == "conn":
        target = plugin.pool.resolve(umo, arg.split()[0] if arg else None)
        yield event.plain_result(f"⏳ 正在连接 {target.profile.name} ...")
        info = await target.sysinfo()
        plugin.pool.set_selection(umo, target.profile.name)
        yield event.plain_result(f"✅ {target.profile.name} 连接成功：\n{info}")

    elif action == "run":
        if not arg:
            yield event.plain_result("用法：ssh 执行 <命令>")
            return
        if blocked := plugin.pool.check_blocked(arg):
            yield event.plain_result(f"⛔ 命令被黑名单规则拦截：{blocked}")
            return
        target = plugin.pool.resolve(umo)
        res = await target.exec(arg)
        yield event.plain_result(res.format(int(plugin.config.get("max_output_chars", 4000))))

    elif action == "ps":
        if not arg:
            yield event.plain_result("用法：ssh ps <PowerShell 脚本>（可多行）")
            return
        target = plugin.pool.resolve(umo)
        if target.os_type != "windows":
            yield event.plain_result("当前主机不是 Windows。")
            return
        res = await target.run_powershell(arg)
        yield event.plain_result(res.format(int(plugin.config.get("max_output_chars", 4000))))

    elif action == "shot":
        yield event.plain_result("⏳ 正在截取屏幕 ...")
        target = plugin.pool.resolve(umo)
        shot = await target.screenshot(
            max_width=int(plugin.config.get("screenshot_max_width", 1280)),
            fmt=str(plugin.config.get("screenshot_format", "jpeg")),
            quality=int(plugin.config.get("screenshot_quality", 80)),
        )
        path = plugin.save_screenshot(target.profile.name, shot.data)
        yield event.chain_result([Comp.Image.fromFileSystem(str(path))])

    elif action == "click":
        toks = arg.split()
        if len(toks) < 2:
            yield event.plain_result("用法：ssh 点击 <x> <y> [左|右|中|双]")
            return
        x, y = int(float(toks[0])), int(float(toks[1]))
        button, double = "left", False
        if len(toks) > 2:
            t = toks[2].lower()
            if t in ("右", "right", "r"):
                button = "right"
            elif t in ("中", "middle", "m"):
                button = "middle"
            elif t in ("双", "double", "d"):
                double = True
        target = plugin.pool.resolve(umo)
        yield event.plain_result(await target.gui_click(x, y, button, double))

    elif action == "move":
        toks = arg.split()
        if len(toks) < 2:
            yield event.plain_result("用法：ssh 移动 <x> <y>")
            return
        target = plugin.pool.resolve(umo)
        yield event.plain_result(
            await target.gui_move(int(float(toks[0])), int(float(toks[1])))
        )

    elif action == "type":
        if not arg:
            yield event.plain_result("用法：ssh 输入 <文本>")
            return
        target = plugin.pool.resolve(umo)
        yield event.plain_result(await target.gui_type(arg))

    elif action == "key":
        if not arg:
            yield event.plain_result("用法：ssh 按键 <组合>，如 ctrl+c、win、alt+tab")
            return
        target = plugin.pool.resolve(umo)
        yield event.plain_result(await target.gui_key(arg))

    elif action == "scroll":
        target = plugin.pool.resolve(umo)
        amount = int(float(arg)) if arg else 3
        yield event.plain_result(await target.gui_scroll(amount))

    elif action == "screen":
        target = plugin.pool.resolve(umo)
        if target.os_type == "windows":
            info = await target.gui_info()
            s, p = info.get("screen", {}), info.get("primary", {})
            yield event.plain_result(
                f"🖥 {info.get('machine')} / {info.get('user')}，Agent v{info.get('version')}\n"
                f"虚拟屏幕：{s.get('w')}x{s.get('h')} @ ({s.get('x')},{s.get('y')})\n"
                f"主屏：{p.get('w')}x{p.get('h')}"
            )
        else:
            env, info = await target.gui_env()
            yield event.plain_result(
                f"🖥 会话类型：{info.get('SESSION_TYPE', '?')}，DISPLAY={info.get('DISPLAY', '?')}\n"
                f"可用工具：{info.get('BACKENDS', '无') or '无'}"
            )

    elif action == "clip":
        target = plugin.pool.resolve(umo)
        toks = arg.split(None, 1)
        if toks and toks[0] in ("存", "set", "s"):
            yield event.plain_result(await target.clipboard_set(toks[1] if len(toks) > 1 else ""))
        else:
            content = await target.clipboard_get()
            limit = int(plugin.config.get("max_output_chars", 4000))
            yield event.plain_result("📋 " + (content[:limit] or "(剪贴板为空)"))

    elif action == "up":
        local_path, comp = await _extract_file(event)
        if local_path is None:
            yield event.plain_result(
                "未找到文件：请在同一条消息中发送文件并附文字 `ssh 上传 <远程路径>`"
            )
            return
        target = plugin.pool.resolve(umo)
        remote = arg.strip()
        fname = Path(local_path).name
        if not remote:
            if target.os_type == "linux":
                remote = f"~/{fname}"
            else:
                home = await target._get_home()
                remote = target._win_path(home) + "\\" + fname
        await target.upload(local_path, remote)
        yield event.plain_result(f"✅ 已上传 {fname} → {remote}")

    elif action == "down":
        if not arg:
            yield event.plain_result("用法：ssh 下载 <远程路径>")
            return
        target = plugin.pool.resolve(umo)
        if not await target.exists(arg):
            yield event.plain_result(f"❌ 远端文件不存在：{arg}")
            return
        local = plugin.down_dir / Path(arg).name
        await target.download(arg, str(local))
        yield event.chain_result([Comp.File(name=Path(arg).name, file=str(local))])

    elif action == "agent":
        target = plugin.pool.resolve(umo)
        if target.os_type != "windows":
            yield event.plain_result("GUI Agent 仅用于 Windows 主机；Linux 请直接用命令操作。")
            return
        op = (arg.split()[0].lower() if arg else "状态")
        if op in ("安装", "install", "i", "部署", "deploy"):
            yield event.plain_result("⏳ 正在部署 GUI Agent（上传脚本 + 创建计划任务）...")
            yield event.plain_result(f"✅ {await target.agent_deploy()}")
        elif op in ("重启", "restart", "r"):
            yield event.plain_result(f"✅ {await target.agent_restart()}")
        else:
            yield event.plain_result(await target.agent_status())

    elif action == "diag":
        yield event.plain_result(_run_diag(plugin))
